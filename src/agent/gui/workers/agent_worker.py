"""AgentWorker for GUI Phase 13.

The worker owns no widgets and runs only ``AgentService``'s GUI entry point in
its QThread. Signals retain Phase 12 event types, with sanitized presentation
copies for free-text fields. They never expose raw history, tool arguments,
provider responses, or exception strings; domain objects remain untouched.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from agent.core.cancellation import CancellationToken
from agent.core.executor import ConfirmCallback, SudoContinuationCallback, ToolEvent, ToolEventKind
from agent.core.loop import (
    OfflineFallbackUsed,
    ProviderFailure,
    ProviderFallbackUsed,
    TurnFinished,
    TurnStarted,
)
from agent.core.redaction import sanitize_for_persistence
from agent.core.network_fallback import ProviderErrorCategory, categorize_provider_error
from agent.errors import AgentExecutionError, ProviderConfigError
from agent.llm.base import LLMProviderError
from agent.services import agent_service
from agent.services.provider_retry import ProviderRetry


class AgentWorker(QObject):
    """One-shot worker preserving Phase 12 types and non-text event semantics.

    ``finished`` is deliberately a lifecycle signal without a new payload.
    ``response_ready`` carries a sanitized ``TurnFinished`` presentation copy
    with the original outcome; ``finished`` only lets the controller quit and
    clean up the QThread after ``run()`` returns.
    """

    started = Signal(object)  # TurnStarted
    tool_started = Signal(object)  # ToolEvent(kind=STARTED)
    tool_finished = Signal(object)  # ToolEvent(kind=FINISHED)
    provider_state = Signal(object)  # ProviderFailure | ProviderFallbackUsed | OfflineFallbackUsed
    response_ready = Signal(object)  # TurnFinished
    error = Signal(object)  # ProviderErrorCategory, never an exception/message
    configuration_failed = Signal()
    finished = Signal()

    def __init__(
        self,
        user_message: str,
        *,
        session_id: str,
        db_path: Path,
        confirm_callback: ConfirmCallback,
        sudo_continuation_callback: SudoContinuationCallback | None = None,
        cancellation_token: CancellationToken | None = None,
        provider_retry: ProviderRetry | None = None,
        retry_only: bool = False,
        soft_limit_usd: float | None = None,
        hard_limit_usd: float | None = None,
    ) -> None:
        super().__init__()
        self._user_message = user_message
        self._session_id = session_id
        self._db_path = db_path
        self._confirm_callback = confirm_callback
        self._sudo_continuation_callback = sudo_continuation_callback
        self._cancellation_token = cancellation_token
        self._provider_retry = provider_retry
        self._retry_only = retry_only
        self._soft_limit_usd = soft_limit_usd
        self._hard_limit_usd = hard_limit_usd
        self._finished_emitted = False

    @Slot()
    def run(self) -> None:
        """Invoke the service boundary once; never call loop/executor/LLM here."""
        try:
            if self._retry_only:
                if self._provider_retry is not None:
                    self._provider_retry.run(on_event=self._forward_event, cancellation_token=self._cancellation_token)
                return
            agent_service.run_chat_turn_with_events(
                self._user_message,
                session_id=self._session_id,
                db_path=self._db_path,
                confirm_callback=self._confirm_callback,
                sudo_continuation_callback=self._sudo_continuation_callback,
                on_event=self._forward_event,
                cancellation_token=self._cancellation_token,
                provider_retry=self._provider_retry,
                soft_limit_usd=self._soft_limit_usd,
                hard_limit_usd=self._hard_limit_usd,
            )
        except ProviderConfigError:
            self.configuration_failed.emit()
        except LLMProviderError as exc:
            self.error.emit(categorize_provider_error(exc))
        except AgentExecutionError:
            self.error.emit(ProviderErrorCategory.GENERIC_FAILURE)
        except Exception:
            # The service should already normalize operational errors. This
            # final boundary never forwards a raw exception, which could contain
            # credentials or provider output.
            self.error.emit(ProviderErrorCategory.GENERIC_FAILURE)
        finally:
            self._emit_finished()

    def _forward_event(self, event: object) -> None:
        """Copy free-text presentation fields before crossing Qt signals."""
        if isinstance(event, TurnStarted):
            self.started.emit(event)
        elif isinstance(event, ToolEvent):
            if event.kind == ToolEventKind.STARTED:
                self.tool_started.emit(event)
            elif event.kind == ToolEventKind.FINISHED:
                self.tool_finished.emit(event)
        elif isinstance(event, ProviderFallbackUsed):
            # This name is presentation only; never replace the domain event
            # or the provider identity used for fallback/cost accounting.
            self.provider_state.emit(ProviderFallbackUsed(
                primary_error=event.primary_error,
                fallback_provider_name=sanitize_for_persistence(event.fallback_provider_name),
            ))
        elif isinstance(event, (ProviderFailure, OfflineFallbackUsed)):
            self.provider_state.emit(event)
        elif isinstance(event, TurnFinished):
            # Persistence sanitizes its own copy, not this live response. Keep
            # provider history untouched and redact before crossing Qt signals.
            self.response_ready.emit(TurnFinished(
                outcome=event.outcome,
                assistant_text=sanitize_for_persistence(event.assistant_text),
            ))

    def _emit_finished(self) -> None:
        if self._finished_emitted:
            return
        self._finished_emitted = True
        self.finished.emit()
