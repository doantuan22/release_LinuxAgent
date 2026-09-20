from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Iterator, Mapping
from typing import Any

class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str,Any]


@dataclass
class Message:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class TokenUsage(Mapping[str, int]):
    """Token counts shared by every adapter and the cost tracker.

    Mapping access preserves the existing cost-tracker/CLI contract.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        for value in (self.prompt_tokens, self.completion_tokens, self.total_tokens):
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError("Token usage must contain non-negative integers")
        if self.total_tokens is None:
            object.__setattr__(self, "total_tokens", self.prompt_tokens + self.completion_tokens)

    def __getitem__(self, key: str) -> int:
        if key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            return getattr(self, key)
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(("prompt_tokens", "completion_tokens", "total_tokens"))

    def __len__(self) -> int:
        return 3

    def __bool__(self) -> bool:
        return bool(self.prompt_tokens or self.completion_tokens or self.total_tokens)


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: str
    usage: TokenUsage = field(default_factory=TokenUsage)

    def __post_init__(self) -> None:
        if not isinstance(self.usage, TokenUsage):
            self.usage = TokenUsage(
                prompt_tokens=self.usage.get("prompt_tokens", 0),
                completion_tokens=self.usage.get("completion_tokens", 0),
                total_tokens=self.usage.get("total_tokens"),
            )

class ProviderError(Exception):
    """Base class for provider failures; messages must never include secrets."""


class ProviderAuthenticationError(ProviderError):
    pass


class ProviderNetworkError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


# Existing callers and fakes still use this name. All new errors are subclasses.
LLMProviderError = ProviderError

class LLMProvider(ABC):
    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        raise NotImplementedError

    @property
    @abstractmethod
    def model_name(self) -> str:
        raise NotImplementedError
