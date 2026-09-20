"""Ephemeral text-generation retry; deliberately has no executor or Qt boundary.

Only failed generation calls with a tool-schema argument are captured (not
compaction). Snapshots never enter signals or persistence. Three explicit retries
are allowed per failed turn, each one provider request with no tool capability.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from agent.core.cancellation import CancellationToken
from agent.core.cost_tracker import check_budget, get_session_summary, record_usage
from agent.core.loop import EventSink, TurnFinished, TurnOutcome, TurnStarted
from agent.llm.base import LLMProvider, LLMResponse, Message, ProviderResponseError, Role, ToolSchema
from agent.memory.session_store import append_message, load_session


@dataclass(repr=False)
class _Request:
    provider: LLMProvider
    messages: list[Message]
    max_tokens: int
    temperature: float
    name: str
    prices: dict | None


class ProviderRetry:
    MAX_ATTEMPTS = 3

    def __init__(self, *, session_id: str, db_path: Path, cost_db_path: Path) -> None:
        self._session_id = session_id
        self._db_path = db_path
        self._cost_db_path = cost_db_path
        self._lock = RLock()
        self._valid = True
        self._busy = False
        self._attempts = 0
        self._request: _Request | None = None
        self._expected: list[Message] | None = None
        self._soft_limit: float | None = None
        self._hard_limit: float | None = None

    def set_budget(self, soft_limit: float | None, hard_limit: float | None) -> None:
        self._soft_limit, self._hard_limit = soft_limit, hard_limit

    def invalidate(self) -> None:
        with self._lock:
            self._valid = False
            self._request = None
            self._expected = None

    def available(self) -> bool:
        with self._lock:
            return (self._valid and not self._busy and self._attempts < self.MAX_ATTEMPTS
                    and self._request is not None and self._expected is not None)

    def seal(self) -> None:
        """Bind the failed request to persisted state after offline handling."""
        with self._lock:
            if self._valid and self._request is not None:
                self._expected = load_session(self._db_path, self._session_id)

    def wrap(self, provider: LLMProvider, name: str, prices: dict | None) -> LLMProvider:
        return _RecordingProvider(provider, self, name, prices)

    def _capture(self, request: _Request | None) -> None:
        with self._lock:
            # The card describes the primary failure. A failed fallback must
            # not silently replace that provider; a successful fallback clears it.
            if self._valid and (request is None or self._request is None):
                self._request = request

    def run(self, *, on_event: EventSink | None, cancellation_token: CancellationToken | None) -> None:
        with self._lock:
            if not self.available():
                return
            request, expected = self._request, self._expected
            self._busy = True
            self._attempts += 1
        assert request is not None

        def emit(event):
            if on_event is not None:
                try:
                    on_event(event)
                except Exception:
                    pass  # presentation callbacks cannot replay a paid request

        def cancelled():
            with self._lock:
                return not self._valid or (cancellation_token is not None and cancellation_token.is_cancelled())

        try:
            if load_session(self._db_path, self._session_id) != expected:
                self.invalidate()
                return
            emit(TurnStarted())
            if cancelled():
                emit(TurnFinished(TurnOutcome.CANCELLED, None))
                return
            if self._soft_limit is not None and self._hard_limit is not None:
                total = get_session_summary(self._cost_db_path, self._session_id)["cost_usd"]
                if check_budget(self._soft_limit, self._hard_limit, total) == "hard":
                    self.invalidate()
                    emit(TurnFinished(TurnOutcome.ANSWERED, "Provider retry skipped: cost limit reached."))
                    return
            if cancelled():
                emit(TurnFinished(TurnOutcome.CANCELLED, None))
                return
            # No loop, fallback, compaction, RAG or executor. The exact failed
            # messages include completed tool results, not the offline answer.
            response = request.provider.chat(deepcopy(request.messages), tools=None,
                                             max_tokens=request.max_tokens, temperature=request.temperature)
            if response.usage:
                record_usage(self._cost_db_path, self._session_id, request.name,
                             request.provider.model_name, response.usage, request.prices)
            if cancelled():
                emit(TurnFinished(TurnOutcome.CANCELLED, None))
                return
            if response.tool_calls or not isinstance(response.content, str) or not response.content.strip():
                raise ProviderResponseError("Text-only retry returned an invalid response.")
            with self._lock:
                if cancelled():
                    emit(TurnFinished(TurnOutcome.CANCELLED, None))
                    return
                if load_session(self._db_path, self._session_id) != expected:
                    self.invalidate()
                    return
                append_message(self._db_path, self._session_id, Message(role=Role.ASSISTANT, content=response.content))
                self.invalidate()
            emit(TurnFinished(TurnOutcome.ANSWERED, response.content))
        finally:
            with self._lock:
                self._busy = False
                if cancelled():
                    self.invalidate()


class _RecordingProvider(LLMProvider):
    def __init__(self, provider: LLMProvider, retry: ProviderRetry, name: str, prices: dict | None) -> None:
        self._provider, self._retry, self._name, self._prices = provider, retry, name, prices

    @property
    def model_name(self) -> str:
        return self._provider.model_name

    def chat(self, messages: list[Message], tools: list[ToolSchema] | None = None, *,
             max_tokens: int = 4096, temperature: float = 0.0) -> LLMResponse:
        if tools is None:  # compaction is not a retryable generation request
            return self._provider.chat(messages, tools, max_tokens=max_tokens, temperature=temperature)
        snapshot = _Request(self._provider, deepcopy(messages), max_tokens, temperature, self._name, self._prices)
        try:
            response = self._provider.chat(messages, tools, max_tokens=max_tokens, temperature=temperature)
        except Exception:
            self._retry._capture(snapshot)
            raise
        self._retry._capture(None)
        return response
