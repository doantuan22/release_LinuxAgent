"""Thread-safe Tier 2 confirmation bridge for the GUI.

``request_confirmation()`` is called synchronously by ``ToolExecutor`` in an
AgentWorker thread.  It emits only a redacted presentation DTO, then waits on a
condition predicate.  The Qt UI thread resolves the exact request ID later; it
never enters a nested dialog event loop or blocks waiting for this worker.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from agent.core.confirmation import ConfirmationRequest
from agent.core.redaction import redact_audit_data, sanitize_for_persistence


@dataclass(frozen=True)
class ConfirmationPrompt:
    """Safe, UI-only copy of a Tier 2 confirmation request.

    ``request_id`` is an opaque correlation ID.  The original
    ``ConfirmationRequest`` (including its raw arguments) is deliberately not
    stored in this object or emitted over a Qt signal.
    """

    request_id: str
    call_id: str | None
    tool_name: str
    tier: str
    arguments: dict[str, Any]
    description: str
    risk: str | None


@dataclass
class _PendingDecision:
    resolved: bool = False
    allowed: bool = False


def _safe_text(value: object) -> str:
    """Render arbitrary input only after the shared secret redaction policy."""
    return str(sanitize_for_persistence(redact_audit_data(str(value))))


def _safe_value(value: object) -> Any:
    """Produce a JSON-like, redacted copy suitable for a read-only modal."""
    value = sanitize_for_persistence(redact_audit_data(value))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, dict):
        return {_safe_text(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    # Tool arguments are JSON values in normal operation.  Do not stringify an
    # unexpected custom object: its repr could itself contain a secret.
    return "[UNSUPPORTED]"


class QtConfirmationBridge(QObject):
    """Adapt the synchronous core callback to queued, non-blocking Qt UI work."""

    confirmation_requested = Signal(object)  # ConfirmationPrompt, never ConfirmationRequest

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._condition = threading.Condition()
        self._pending: dict[str, _PendingDecision] = {}
        self._closed = False

    def request_confirmation(self, request: ConfirmationRequest) -> bool:
        """Block only the calling worker until its own request is resolved.

        The ``while`` predicate handles a resolution arriving before ``wait()``,
        spurious wakeups, and ``deny_all()``.  A bridge closed for application
        shutdown denies immediately, so no later worker can become stranded.
        """
        request_id = uuid4().hex
        with self._condition:
            if self._closed:
                return False
            pending = _PendingDecision()
            self._pending[request_id] = pending

        prompt = ConfirmationPrompt(
            request_id=request_id,
            # call_id is the Phase 12 opaque correlation token; unlike tool
            # arguments it is not user-rendered and must remain exact so the
            # subsequent sudo prompt updates the same tool item.
            call_id=request.call_id if isinstance(request.call_id, str) else None,
            tool_name=_safe_text(request.tool_name),
            tier=_safe_text(request.tier.value),
            arguments=_safe_value(request.arguments),
            description=_safe_text(request.description),
            risk=_safe_text(request.risk) if request.risk is not None else None,
        )
        self.confirmation_requested.emit(prompt)

        with self._condition:
            while not pending.resolved:
                self._condition.wait()
            return pending.allowed

    def resolve(self, request_id: str, allowed: bool) -> bool:
        """Resolve one pending request exactly once; stale replies are ignored."""
        with self._condition:
            pending = self._pending.pop(request_id, None)
            if pending is None or pending.resolved:
                return False
            pending.allowed = bool(allowed)
            pending.resolved = True
            self._condition.notify_all()
            return True

    def cancel(self, request_id: str) -> bool:
        """Dialog dismissal is semantically an explicit deny for that request."""
        return self.resolve(request_id, False)

    def deny_all(self) -> None:
        """Permanently close the bridge and wake every waiting worker as denied."""
        with self._condition:
            self._closed = True
            pending = tuple(self._pending.values())
            self._pending.clear()
            for decision in pending:
                decision.allowed = False
                decision.resolved = True
            self._condition.notify_all()

    def reopen(self) -> None:
        """Undo a prior ``deny_all()`` so the NEXT turn can request confirmation
        again (Phase 17: Stop cancels only the current turn, not the app —
        without this, one Stop click would silently auto-deny every Tier 2
        action for the rest of the session). Never called at real app
        shutdown; ``deny_all()`` there stays permanent for that bridge
        instance."""
        with self._condition:
            self._closed = False

    def is_pending(self, request_id: str) -> bool:
        """UI guard for a queued prompt which arrived after shutdown/resolve."""
        with self._condition:
            return request_id in self._pending
