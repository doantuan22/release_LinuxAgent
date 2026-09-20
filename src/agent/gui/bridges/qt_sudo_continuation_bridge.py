"""Thread-safe Retry/Dismiss bridge for sudo authentication continuation.

The synchronous callback is invoked by the AgentWorker thread.  It emits only
a small safe DTO and waits on a condition predicate; the future UI thread will
resolve the request asynchronously and never blocks its Qt event loop.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from agent.core.executor import SudoAuthenticationRequired, SudoContinuationDecision


@dataclass(frozen=True)
class SudoAuthenticationPrompt:
    """Safe presentation payload; no args, argv, stderr, or credentials."""

    request_id: str
    call_id: str
    tool_name: str
    category: str
    still_unavailable: bool


@dataclass
class _PendingDecision:
    resolved: bool = False
    decision: SudoContinuationDecision = SudoContinuationDecision.DISMISS


class QtSudoContinuationBridge(QObject):
    """Adapt worker-side sudo continuation to queued Qt presentation work."""

    authentication_requested = Signal(object)  # SudoAuthenticationPrompt only

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._condition = threading.Condition()
        self._pending: dict[str, _PendingDecision] = {}
        self._closed = False

    def wait_for_resolution(self, request: SudoAuthenticationRequired) -> SudoContinuationDecision:
        """Wait only in the caller's worker thread for Retry or Dismiss."""
        request_id = uuid4().hex
        with self._condition:
            if self._closed:
                return SudoContinuationDecision.DISMISS
            pending = _PendingDecision()
            self._pending[request_id] = pending

        self.authentication_requested.emit(
            SudoAuthenticationPrompt(
                request_id=request_id,
                call_id=request.call_id,
                tool_name=request.tool_name,
                category=request.category,
                still_unavailable=request.still_unavailable,
            )
        )
        with self._condition:
            while not pending.resolved:
                self._condition.wait()
            return pending.decision

    def retry(self, request_id: str) -> bool:
        return self._resolve(request_id, SudoContinuationDecision.RETRY)

    def dismiss(self, request_id: str) -> bool:
        return self._resolve(request_id, SudoContinuationDecision.DISMISS)

    def cancel(self, request_id: str) -> bool:
        return self.dismiss(request_id)

    def _resolve(self, request_id: str, decision: SudoContinuationDecision) -> bool:
        """Resolve exactly once; stale/duplicate UI responses are harmless."""
        with self._condition:
            pending = self._pending.pop(request_id, None)
            if pending is None or pending.resolved:
                return False
            pending.decision = decision
            pending.resolved = True
            self._condition.notify_all()
            return True

    def deny_all(self) -> None:
        """Close fail-closed and wake every worker before thread cleanup."""
        with self._condition:
            self._closed = True
            pending = tuple(self._pending.values())
            self._pending.clear()
            for decision in pending:
                decision.decision = SudoContinuationDecision.DISMISS
                decision.resolved = True
            self._condition.notify_all()

    def reopen(self) -> None:
        """Undo a prior ``deny_all()`` so the NEXT turn can wait for Retry/Dismiss
        again (Phase 17: Stop cancels only the current turn, not the app). Never
        called at real app shutdown; ``deny_all()`` there stays permanent for
        that bridge instance."""
        with self._condition:
            self._closed = False

    def is_pending(self, request_id: str) -> bool:
        with self._condition:
            return request_id in self._pending
