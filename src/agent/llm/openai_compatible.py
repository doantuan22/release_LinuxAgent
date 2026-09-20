"""Adapter cho mọi provider tuân theo chuẩn OpenAI /v1/chat/completions."""

from __future__ import annotations

import json
from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

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


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        *,
        model: str,
        base_url: str | None = None,
        api_key: str = "not-needed",
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

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
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[self._to_openai_message(m) for m in messages],
                tools=[self._to_openai_tool(t) for t in tools] if tools else None,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except APITimeoutError as e:
            raise ProviderTimeoutError("Provider request timed out") from e
        except AuthenticationError as e:
            raise ProviderAuthenticationError("Provider authentication failed") from e
        except RateLimitError as e:
            raise ProviderRateLimitError("Provider rate limit exceeded") from e
        except APIConnectionError as e:
            raise ProviderNetworkError("Cannot connect to provider") from e
        except APIResponseValidationError as e:
            raise ProviderResponseError("Provider returned an invalid response") from e
        except APIStatusError as e:
            error_type = _status_error_type(e.status_code)
            raise error_type(f"Provider request failed (HTTP {e.status_code})") from e
        except APIError as e:
            raise ProviderError("Provider request failed") from e

        return self._to_llm_response(response)

    @staticmethod
    def _to_openai_message(message: Message) -> dict[str, Any]:
        out: dict[str, Any] = {"role": message.role.value}

        if message.role == Role.TOOL:
            out["tool_call_id"] = message.tool_call_id
            out["content"] = message.content or ""
            return out

        if message.content is not None:
            out["content"] = message.content

        if message.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in message.tool_calls
            ]

        return out

    @staticmethod
    def _to_openai_tool(tool: ToolSchema) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    @staticmethod
    def _to_llm_response(response: Any) -> LLMResponse:
        try:
            choice = response.choices[0]
            raw_message = choice.message
            tool_calls: list[ToolCall] = []
            for tc in raw_message.tool_calls or []:
                arguments = json.loads(tc.function.arguments)
                if not isinstance(arguments, dict) or not tc.id or not tc.function.name:
                    raise ValueError("invalid tool call")
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=arguments))

            usage = TokenUsage()
            if response.usage is not None:
                usage = TokenUsage(
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    total_tokens=response.usage.total_tokens,
                )
            if choice.finish_reason not in {"stop", "tool_calls", "length", "content_filter"}:
                raise ValueError("invalid finish reason")
            if choice.finish_reason == "tool_calls" and not tool_calls:
                raise ValueError("tool calls and finish reason disagree")
            if tool_calls and choice.finish_reason not in {"tool_calls", "stop"}:
                raise ValueError("incomplete tool calls")
            if raw_message.content is None and not tool_calls:
                raise ValueError("empty response")
            return LLMResponse(
                content=raw_message.content,
                tool_calls=tool_calls,
                # Some OpenAI-compatible local servers report "stop" for a
                # tool call; the neutral contract follows the actual calls.
                finish_reason="tool_calls" if tool_calls else choice.finish_reason,
                usage=usage,
            )
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            raise ProviderResponseError("Provider returned a malformed response") from exc


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
