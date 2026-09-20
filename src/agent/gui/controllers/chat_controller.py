"""UI-thread owner for GUI chat turns, sessions, and Tier 2 confirmation."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from PySide6.QtCore import QCoreApplication, QObject, QThread, Qt, Signal

from agent import paths
from agent.core.cancellation import CancellationToken
from agent.core.executor import ToolEvent, ToolEventKind, ToolOutcomeState
from agent.core.loop import OfflineFallbackUsed, ProviderFailure, ProviderFallbackUsed, TurnFinished, TurnOutcome
from agent.core.network_fallback import ProviderErrorCategory
from agent.core.redaction import sanitize_for_persistence
from agent.errors import ProviderConfigError
from agent.gui.bridges.qt_confirmation_bridge import QtConfirmationBridge
from agent.gui.bridges.qt_sudo_continuation_bridge import QtSudoContinuationBridge
from agent.gui.workers.agent_worker import AgentWorker
from agent.llm.base import Message, Role
from agent.services import agent_service, session_service
from agent.services.provider_retry import ProviderRetry
from agent.tools.schemas import Tier

_SHUTDOWN_WAIT_MS = 12000

_PROVIDER_ERROR_COPY: dict[ProviderErrorCategory, tuple[str, str]] = {
    ProviderErrorCategory.AUTHENTICATION: (
        "Provider authentication failed",
        "Check the provider configuration and API key.",
    ),
    ProviderErrorCategory.NETWORK: (
        "Provider network unavailable",
        "The provider could not be reached. Try again when the network is available.",
    ),
    ProviderErrorCategory.RATE_LIMIT: (
        "Provider rate limit reached",
        "Try again later.",
    ),
    ProviderErrorCategory.TIMEOUT: (
        "Provider request timed out",
        "The provider did not respond in time. Try again.",
    ),
    ProviderErrorCategory.MALFORMED_RESPONSE: (
        "Provider returned an invalid response",
        "Try again or check the provider configuration.",
    ),
    ProviderErrorCategory.GENERIC_FAILURE: (
        "Provider request failed",
        "Try again or check the provider configuration.",
    ),
}


class ChatController(QObject):
    """Own one AgentWorker/QThread at a time; all page-facing signals are queued."""

    ready_changed = Signal(bool)
    started = Signal(object)
    tool_started = Signal(object)
    tool_finished = Signal(object)
    provider_state = Signal(object)
    response_ready = Signal(object)
    error = Signal(object)
    configuration_failed = Signal()
    retry_available_changed = Signal(bool)
    finished = Signal()
    history_loaded = Signal(list)
    history_failed = Signal()
    session_created = Signal(str)
    confirmation_requested = Signal(object)  # safe ConfirmationPrompt
    confirmation_resolved = Signal(str, bool)
    sudo_authentication_requested = Signal(object)  # safe SudoAuthenticationPrompt
    stop_requested = Signal()

    def __init__(self, parent: QObject | None = None, *, db_path: Path | None = None) -> None:
        super().__init__(parent)
        self._db_path = db_path if db_path is not None else paths.sessions_db()
        self._session_id: str | None = None
        self._thread: QThread | None = None
        self._worker: AgentWorker | None = None
        self._retired: list[tuple[QThread, QObject]] = []
        self._cancellation_token: CancellationToken | None = None
        self._ready = False
        self._provider_retry: ProviderRetry | None = None
        self._confirmation_bridge = QtConfirmationBridge(self)
        self._confirmation_bridge.confirmation_requested.connect(
            self.confirmation_requested, Qt.ConnectionType.QueuedConnection
        )
        self._sudo_continuation_bridge = QtSudoContinuationBridge(self)
        self._sudo_continuation_bridge.authentication_requested.connect(
            self.sudo_authentication_requested, Qt.ConnectionType.QueuedConnection
        )

    def activate(self) -> None:
        """Mark the controller ready after its owning page has connected signals."""
        if self._ready:
            return
        self._ready = True
        self.ready_changed.emit(True)

    def is_ready(self) -> bool:
        return self._ready

    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def current_session_id(self) -> str | None:
        return self._session_id

    def resolve_confirmation(self, request_id: str, allowed: bool) -> bool:
        """Resolve one UI answer; late and duplicate answers are no-ops."""
        resolved = self._confirmation_bridge.resolve(request_id, allowed)
        if resolved:
            self.confirmation_resolved.emit(request_id, allowed)
        return resolved

    def cancel_confirmation(self, request_id: str) -> bool:
        return self.resolve_confirmation(request_id, False)

    def is_confirmation_pending(self, request_id: str) -> bool:
        return self._confirmation_bridge.is_pending(request_id)

    def deny_all_confirmations(self) -> None:
        """Fail closed before a worker may be waited on: app shutdown, or a
        Phase 17 Stop request. ``run_turn()`` reopens the bridge for the next
        turn (see ``QtConfirmationBridge.reopen()``), so a mid-turn Stop never
        permanently blocks future Tier 2 confirmations."""
        self._confirmation_bridge.deny_all()

    def retry_sudo_authentication(self, request_id: str) -> bool:
        return self._sudo_continuation_bridge.retry(request_id)

    def dismiss_sudo_authentication(self, request_id: str) -> bool:
        return self._sudo_continuation_bridge.dismiss(request_id)

    def is_sudo_authentication_pending(self, request_id: str) -> bool:
        return self._sudo_continuation_bridge.is_pending(request_id)

    def deny_all_sudo_authentication(self) -> None:
        """Wake every worker blocked for Retry/Dismiss: app shutdown, or a
        Phase 17 Stop request. Reopened per turn the same way as the
        confirmation bridge (see ``deny_all_confirmations()``)."""
        self._sudo_continuation_bridge.deny_all()

    def request_stop(self) -> None:
        """Cooperative, non-blocking cancel for the CURRENT turn (Phase 17).

        Safe to call even when idle: denying pending bridge waiters is a
        no-op with nothing pending, and there is no active token to cancel.
        Never kills the worker thread or an in-flight tool call — the loop
        only reacts at its next checkpoint (core/loop.py); a tool that has
        already invoked its subprocess always runs to completion.

        Emits ``stop_requested`` first: MainWindow owns the visible
        ConfirmationDialog widget for a pending Tier 2 request (this
        controller only owns the bridge's future), and must close/forget it
        here the same way it already does in ``closeEvent()`` — otherwise a
        stale dialog from the cancelled turn stays on screen and can swallow
        a click meant for the next turn's dialog.
        """
        self.invalidate_provider_retry()
        self.stop_requested.emit()
        self.deny_all_confirmations()
        self.deny_all_sudo_authentication()
        if self._cancellation_token is not None:
            self._cancellation_token.cancel()

    # Worker signals retain Phase 12 types, with sanitized text copies. These
    # small presentation queries keep core types out of ChatPage, which must
    # remain a widget-only layer.
    @staticmethod
    def tier1_tool_started(event: object) -> tuple[str, str] | None:
        if (
            isinstance(event, ToolEvent)
            and event.kind == ToolEventKind.STARTED
            and event.tier == Tier.TIER_1_READONLY
            and event.call_id
        ):
            return event.call_id, event.tool_name
        return None

    @staticmethod
    def tier1_tool_finished(event: object) -> tuple[str, ToolOutcomeState] | None:
        if (
            isinstance(event, ToolEvent)
            and event.kind == ToolEventKind.FINISHED
            and event.tier == Tier.TIER_1_READONLY
            and event.call_id
            and event.state is not None
        ):
            return event.call_id, event.state
        return None

    @staticmethod
    def tier2_tool_started(event: object) -> tuple[str, str] | None:
        if (
            isinstance(event, ToolEvent)
            and event.kind == ToolEventKind.STARTED
            and event.tier == Tier.TIER_2_ACTION
            and event.call_id
        ):
            return event.call_id, event.tool_name
        return None

    @staticmethod
    def tier2_tool_finished(event: object) -> tuple[str, ToolOutcomeState] | None:
        if (
            isinstance(event, ToolEvent)
            and event.kind == ToolEventKind.FINISHED
            and event.tier == Tier.TIER_2_ACTION
            and event.call_id
            and event.state is not None
        ):
            return event.call_id, event.state
        return None

    @staticmethod
    def is_provider_fallback_used(event: object) -> bool:
        return isinstance(event, ProviderFallbackUsed)

    @staticmethod
    def fallback_provider_name(event: object) -> str | None:
        return event.fallback_provider_name if isinstance(event, ProviderFallbackUsed) else None

    @staticmethod
    def provider_failure_category(event: object) -> ProviderErrorCategory | None:
        return event.primary_error if isinstance(event, ProviderFailure) else None

    @staticmethod
    def offline_fallback_reason(event: object) -> str | None:
        return event.reason if isinstance(event, OfflineFallbackUsed) else None

    @staticmethod
    def turn_assistant_text(event: object) -> str | None:
        return event.assistant_text if isinstance(event, TurnFinished) else None

    @staticmethod
    def turn_outcome_is_cancelled(event: object) -> bool:
        """Cancelled (mục 10 ui_ux_spec.md) is muted text, not an error card —
        ChatPage must tell it apart from a normal answer without importing
        TurnOutcome directly."""
        return isinstance(event, TurnFinished) and event.outcome == TurnOutcome.CANCELLED

    @staticmethod
    def history_message_for_display(message: object) -> tuple[str, str] | None:
        if not isinstance(message, Message) or not message.content:
            return None
        if message.role == Role.USER:
            return "user", message.content
        if message.role == Role.ASSISTANT:
            return "assistant", message.content
        return None

    @staticmethod
    def provider_error_copy(category: object) -> tuple[str, str]:
        if not isinstance(category, ProviderErrorCategory):
            category = ProviderErrorCategory.GENERIC_FAILURE
        return _PROVIDER_ERROR_COPY.get(category, _PROVIDER_ERROR_COPY[ProviderErrorCategory.GENERIC_FAILURE])

    @staticmethod
    def provider_error_presentation(category: object) -> tuple[bool, str]:
        """Return warning severity and action without exposing core enums to views."""
        warning = category in (
            ProviderErrorCategory.TIMEOUT,
            ProviderErrorCategory.NETWORK,
            ProviderErrorCategory.RATE_LIMIT,
        )
        action = "Open Settings" if category == ProviderErrorCategory.AUTHENTICATION else "Retry"
        return warning, action

    def new_chat(self) -> None:
        """Clear the selected session without creating an empty DB record."""
        if self.is_busy():
            return
        self.invalidate_provider_retry()
        self._session_id = None

    def open_session(self, session_id: str) -> bool:
        """Load a selected session through SessionService, never from a widget."""
        if self.is_busy() or not session_id:
            return False
        self.invalidate_provider_retry()
        try:
            messages = session_service.load_session_messages(self._db_path, session_id)
            # ChatPage consumes only user/assistant role and content. Do not
            # forward hidden system/tool payloads or share mutable history.
            presentation = [
                Message(role=message.role, content=sanitize_for_persistence(message.content))
                for message in messages
                if message.role in (Role.USER, Role.ASSISTANT)
            ]
        except Exception:
            # Malformed role/JSON/metadata must not escape a Qt resume slot.
            self.history_failed.emit()
            return False
        self._session_id = session_id
        self.history_loaded.emit(presentation)
        return True

    def send_message(self, user_message: str) -> bool:
        """Create a session only at the first actual send, then start one turn."""
        if not self._ready or self.is_busy() or not user_message.strip():
            return False
        if self._session_id is None:
            try:
                self._session_id = session_service.create_new_session(self._db_path).id
            except (sqlite3.DatabaseError, OSError, RuntimeError):
                self.error.emit(ProviderErrorCategory.GENERIC_FAILURE)
                return False
            self.session_created.emit(self._session_id)
        return self.run_turn(user_message, session_id=self._session_id, db_path=self._db_path)

    def run_turn(self, user_message: str, *, session_id: str, db_path: Path) -> bool:
        """Start one real service turn with the per-controller confirmation bridge."""
        if not self._ready or self.is_busy():
            return False

        self.invalidate_provider_retry()
        self._provider_retry = ProviderRetry(session_id=session_id, db_path=db_path, cost_db_path=paths.memory_db())
        self._session_id = session_id
        self._db_path = db_path
        return self._start_worker(user_message, retry_only=False)

    def invalidate_provider_retry(self) -> None:
        if self._provider_retry is not None:
            self._provider_retry.invalidate()
        self._provider_retry = None
        self.retry_available_changed.emit(False)

    def retry_provider(self) -> bool:
        if not self._ready or self.is_busy() or self._provider_retry is None or not self._provider_retry.available():
            return False
        self.retry_available_changed.emit(False)
        return self._start_worker("", retry_only=True)

    def _start_worker(self, user_message: str, *, retry_only: bool) -> bool:
        try:
            budget_limits = agent_service.load_configured_budget_limits()
        except ProviderConfigError:
            self.configuration_failed.emit()
            return False

        # Undo any deny_all() left by a PRIOR turn's Stop/close (Phase 17) —
        # without this, one Stop click would silently auto-deny Tier 2/sudo
        # for every turn after it.
        self._confirmation_bridge.reopen()
        self._sudo_continuation_bridge.reopen()
        self._cancellation_token = CancellationToken()

        thread = QThread(self)
        worker = AgentWorker(
            user_message,
            session_id=self._session_id,
            db_path=self._db_path,
            confirm_callback=self._confirmation_bridge.request_confirmation,
            sudo_continuation_callback=self._sudo_continuation_bridge.wait_for_resolution,
            cancellation_token=self._cancellation_token,
            provider_retry=self._provider_retry,
            retry_only=retry_only,
            soft_limit_usd=budget_limits.soft_limit_usd,
            hard_limit_usd=budget_limits.hard_limit_usd,
        )
        worker.moveToThread(thread)

        queued = Qt.ConnectionType.QueuedConnection
        thread.started.connect(worker.run, queued)
        worker.started.connect(self.started, queued)
        worker.tool_started.connect(self.tool_started, queued)
        worker.tool_finished.connect(self.tool_finished, queued)
        worker.provider_state.connect(self.provider_state, queued)
        worker.response_ready.connect(self.response_ready, queued)
        worker.error.connect(self.error, queued)
        worker.configuration_failed.connect(self.configuration_failed, queued)
        worker.finished.connect(self._on_worker_finished, queued)
        worker.finished.connect(thread.quit, queued)
        worker.finished.connect(worker.deleteLater, queued)
        thread.finished.connect(self._on_thread_finished, queued)
        thread.finished.connect(thread.deleteLater, queued)

        self._thread = thread
        self._worker = worker
        thread.start()
        return True

    def shutdown(self, timeout_ms: int = _SHUTDOWN_WAIT_MS) -> None:
        """Cooperatively stop, then drain active/retired QThreads before teardown.

        MainWindow only calls this after ``is_busy()`` is false, so normal UI
        close waits only for the tiny retirement window after worker ``finished``.
        The bounded active-thread wait is defensive for direct cleanup callers.
        """
        self.request_stop()
        threads = [thread for thread, _worker in self._retired]
        if self._thread is not None:
            threads.append(self._thread)
        for thread in threads:
            try:
                thread.quit()
                thread.wait(timeout_ms)
            except RuntimeError:
                pass
        QCoreApplication.processEvents()

    def _on_worker_finished(self) -> None:
        # This slot runs in the UI thread through the explicit queued connection.
        if self._thread is not None and self._worker is not None:
            self._retired.append((self._thread, self._worker))
        self._thread = None
        self._worker = None
        self._cancellation_token = None
        self.retry_available_changed.emit(self._provider_retry is not None and self._provider_retry.available())
        self.finished.emit()

    def _on_thread_finished(self) -> None:
        finished = self.sender()
        self._retired = [(thread, worker) for thread, worker in self._retired if thread is not finished]
