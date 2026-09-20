"""Anthropic Messages API adapter for the provider-neutral conversation contract."""

from __future__ import annotations

from typing import Any

from anthropic import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    Anthropic,
    AuthenticationError,
    RateLimitError,
)

from agent.core.tool_observation import parse_tool_payload

from .base import (
    LLMProvider,
    LLMResponse,
    Message,
    ProviderAuthenticationError,
    ProviderError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    Role,
    TokenUsage,
    ToolCall,
    ToolSchema,
)


_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "refusal": "refusal",
    "pause_turn": "pause",
}


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._client = Anthropic(api_key=api_key, base_url=base_url, timeout=timeout)
        # Tool-use continuations with adaptive thinking must replay the exact
        # assistant blocks. Keep them inside this adapter, never in Message or
        # persisted session data.
        self._pending_tool_turn: tuple[tuple[str, ...], list[dict[str, Any]]] | None = None

    @property
    def model_name(self) -> str:
        return self._model

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        system, conversation = self._to_anthropic_messages(messages)
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": conversation,
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = [self._to_anthropic_tool(tool) for tool in tools]
        # The loop's default 0.0 is not accepted by current Claude models.
        # Omission uses the model default; explicit non-default values pass through.
        if temperature != 0.0:
            request["temperature"] = temperature

        try:
            response = self._client.messages.create(**request)
        except APITimeoutError as exc:
            raise ProviderTimeoutError("Anthropic request timed out") from exc
        except AuthenticationError as exc:
            raise ProviderAuthenticationError("Anthropic authentication failed") from exc
        except RateLimitError as exc:
            raise ProviderRateLimitError("Anthropic rate limit exceeded") from exc
        except APIConnectionError as exc:
            raise ProviderNetworkError("Cannot connect to Anthropic") from exc
        except APIResponseValidationError as exc:
            raise ProviderResponseError("Anthropic returned an invalid response") from exc
        except APIStatusError as exc:
            error_type = _status_error_type(exc.status_code)
            raise error_type(f"Anthropic request failed (HTTP {exc.status_code})") from exc
        except APIError as exc:
            raise ProviderError("Anthropic request failed") from exc

        return self._to_llm_response(response)

    @staticmethod
    def _to_anthropic_tool(tool: ToolSchema) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
        }

    def _to_anthropic_messages(self, messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        conversation: list[dict[str, Any]] = []
        for message in messages:
            if message.role == Role.SYSTEM:
                if message.content:
                    system_parts.append(message.content)
            elif message.role == Role.TOOL:
                if not message.tool_call_id:
                    raise ProviderResponseError("Tool result has no call ID")
                content = message.content or ""
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": content,
                }
                result = parse_tool_payload(content)
                if isinstance(result, dict) and result.get("ok") is False:
                    block["is_error"] = True
                if conversation and conversation[-1]["role"] == "user" and isinstance(conversation[-1]["content"], list) and all(
                    item.get("type") == "tool_result" for item in conversation[-1]["content"]
                ):
                    conversation[-1]["content"].append(block)
                else:
                    conversation.append({"role": "user", "content": [block]})
            elif message.role == Role.ASSISTANT:
                call_ids = tuple(call.id for call in message.tool_calls)
                if self._pending_tool_turn and call_ids and call_ids == self._pending_tool_turn[0]:
                    # SDK blocks include signed thinking data that cannot be
                    # reconstructed from the neutral Message model.
                    blocks = self._pending_tool_turn[1]
                else:
                    blocks = []
                    if message.content is not None:
                        blocks.append({"type": "text", "text": message.content})
                    blocks.extend(
                        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                        for call in message.tool_calls
                    )
                conversation.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif message.role == Role.USER:
                conversation.append({"role": "user", "content": message.content or ""})
            else:
                raise ProviderResponseError("Unsupported conversation role")
        return "\n\n".join(system_parts), conversation

    def _to_llm_response(self, response: Any) -> LLMResponse:
        self._pending_tool_turn = None
        try:
            blocks = response.content
            if not isinstance(blocks, list):
                raise ValueError("content is not a list")
            text_parts: list[str] = []
            calls: list[ToolCall] = []
            for block in blocks:
                if block.type == "text":
                    if not isinstance(block.text, str):
                        raise ValueError("invalid text block")
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    if not isinstance(block.id, str) or not block.id or not isinstance(block.name, str) or not block.name or not isinstance(block.input, dict):
                        raise ValueError("invalid tool use block")
                    calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
            stop_reason = _STOP_REASONS[response.stop_reason]
            if (stop_reason == "tool_calls") != bool(calls):
                raise ValueError("tool calls and stop reason disagree")
            usage = TokenUsage(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
            )
            if not text_parts and not calls:
                raise ValueError("empty response")
            if calls:
                self._pending_tool_turn = (
                    tuple(call.id for call in calls),
                    [
                        block.model_dump(exclude_none=True)
                        if hasattr(block, "model_dump") else vars(block).copy()
                        for block in blocks
                    ],
                )
            return LLMResponse(
                content="\n".join(text_parts) if text_parts else None,
                tool_calls=calls,
                finish_reason=stop_reason,
                usage=usage,
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ProviderResponseError("Anthropic returned a malformed response") from exc


def _status_error_type(status_code: int) -> type[ProviderError]:
    if status_code in (401, 403):
        return ProviderAuthenticationError
    if status_code == 429:
        return ProviderRateLimitError
    if status_code in (408, 504):
        return ProviderTimeoutError
    if status_code >= 500:
        return ProviderNetworkError
    return ProviderResponseError
