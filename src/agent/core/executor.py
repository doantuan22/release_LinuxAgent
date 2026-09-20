"""ToolExecutor: tra registry, kiểm tra path cấm, xác nhận Tier 2, gọi tool, bắt
mọi exception, luôn ghi audit log.

Đây là lớp bảo vệ cuối cùng theo nguyên tắc "mọi exception/timeout từ tool phải
bọc thành ToolResult(ok=False, ...)" — không bao giờ để exception thoát ra khỏi
vòng lặp agent (core/loop.py).

Chính sách xác nhận Tier 2 (CLI.md mục 6/7): confirm_callback nhận nguyên vẹn một
ConfirmationRequest (đủ dữ liệu tool_name/tier/arguments/description để render
CLI/GUI mà không phải đọc lại registry ở nơi khác) thay vì (tool_name, args) rời
rạc. Nếu không cấu hình confirm_callback riêng, mặc định dùng interactive_confirm
(core/confirm_policies.py) — hỏi qua input() khi có tty thật, LUÔN từ chối khi
chạy non-interactive (script/cron không có ai xác nhận được thì phải từ chối).
Executor xác nhận và ghi intent trước khi gọi tool. Tool cần quyền cao dùng
PrivilegeManager: sudo tự đọc password trên terminal khi cần; executor không
đọc hoặc lưu password.

`on_event` (Phase 12, gui_implementation_plan.md — opt-in, mặc định None nên không
đổi hành vi caller hiện có) phát `ToolEvent` STARTED/FINISHED quanh vòng đời một tool
call, kèm đúng `call_id` do loop.py truyền vào để GUI khớp đúng tool call nào trong
một lượt có nhiều tool call song song. Payload CHỈ gồm ID/tier/state đã sanitize —
không bao giờ kèm `args`/`result.data`/`result.error` (có thể chứa nội dung nhạy cảm
hoặc output hệ thống dài) — đúng "safe state" trong plan, không phải toàn bộ kết quả.
"""

from __future__ import annotations

import inspect
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from agent.core import audit
from agent.core.confirm_policies import interactive_confirm
from agent.core.confirmation import ConfirmationRequest
from agent.core.forbidden_paths import is_path_forbidden
from agent.system.privilege import ExecutionPlan, PrivilegeAuthenticationRequired, PrivilegeError, PrivilegeManager
from agent.tools.registry import RegisteredTool, get_tool
from agent.tools.schemas import Tier, ToolResult

ConfirmCallback = Callable[[ConfirmationRequest], bool]

logger = logging.getLogger(__name__)


class ToolEventKind(str, Enum):
    STARTED = "tool_started"
    FINISHED = "tool_finished"


class ToolOutcomeState(str, Enum):
    OK = "ok"
    DENIED = "denied"
    ERROR = "error"
    AUTHENTICATION_REQUIRED = "authentication_required"


@dataclass(frozen=True)
class ToolEvent:
    kind: ToolEventKind
    call_id: str | None
    tool_name: str
    tier: Tier | None
    state: ToolOutcomeState | None = None  # None khi kind=STARTED


ToolEventSink = Callable[[ToolEvent], None]


class SudoContinuationDecision(str, Enum):
    RETRY = "retry"
    DISMISS = "dismiss"


@dataclass(frozen=True)
class SudoAuthenticationRequired:
    """Safe, structured state sent to a registered continuation consumer."""

    call_id: str
    tool_name: str
    category: str = "authentication_required"
    still_unavailable: bool = False


@dataclass(frozen=True)
class PendingToolAction:
    """In-process-only action retained while GUI authentication is pending."""

    call_id: str
    registered_tool: RegisteredTool
    arguments: dict[str, Any]
    plan: ExecutionPlan


SudoContinuationCallback = Callable[[SudoAuthenticationRequired], SudoContinuationDecision]


class ToolExecutor:
    def __init__(
        self,
        confirm_callback: ConfirmCallback | None = None,
        system_profile: Any | None = None,
        on_event: ToolEventSink | None = None,
        sudo_continuation_callback: SudoContinuationCallback | None = None,
    ) -> None:
        self._confirm_callback = confirm_callback or interactive_confirm
        # Dependency injection cho eval framework (Giai đoạn 7): khi có, được truyền
        # vào các tool nhận tham số `system_profile` thay vì để tool tự gọi
        # scan_system() thật — KHÔNG đổi hành vi production (None -> tool tự quét
        # như cũ). Audit chỉ giữ digest của args LLM, không lưu raw value.
        self._system_profile = system_profile
        self._on_event = on_event
        # CLI leaves this unset: failed preflight remains an immediate result,
        # never a pending action nobody can wake.
        self._sudo_continuation_callback = sudo_continuation_callback
        self._pending_actions: dict[str, PendingToolAction] = {}
        # Session-scoped (per-executor-instance, không persist qua session khác):
        # tên gói search_package đã THỰC SỰ trả về trong phiên này, để cưỡng chế
        # search-before-install bằng state thật thay vì chỉ mô tả trong tool schema.
        self._searched_package_names: set[str] = set()

    def _emit(self, kind: ToolEventKind, tool_name: str, tier: Tier | None, call_id: str | None, state: ToolOutcomeState | None = None) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(ToolEvent(kind=kind, call_id=call_id, tool_name=tool_name, tier=tier, state=state))
        except Exception:
            # Sink là code trình bày (GUI callback) — lỗi ở đó không được phép làm
            # hỏng lượt thực thi tool thật đang chạy.
            logger.exception("on_event callback ném exception — bỏ qua, không ảnh hưởng tới việc chạy tool.")

    def _preflight(self, registered: RegisteredTool, args: dict[str, Any], call_id: str) -> ExecutionPlan:
        assert registered.preflight is not None
        preflight_args = dict(args)
        if self._system_profile is not None and "system_profile" in inspect.signature(registered.preflight).parameters:
            preflight_args["system_profile"] = self._system_profile
        plan = registered.preflight(**preflight_args, call_id=call_id)
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("Tier 2 preflight phải trả về ExecutionPlan.")
        return plan

    def _finish_pending(
        self,
        pending: PendingToolAction,
        result: ToolResult,
        *,
        state: ToolOutcomeState,
        event: str = "completed",
    ) -> ToolResult:
        """Write exactly one terminal audit record after pending is resolved."""
        try:
            audit.log_tool_call(
                pending.registered_tool.name,
                pending.arguments,
                result,
                pending.registered_tool.tier,
                event=event,
            )
        except OSError:
            self._emit(ToolEventKind.FINISHED, pending.registered_tool.name, pending.registered_tool.tier, pending.call_id, ToolOutcomeState.ERROR)
            return ToolResult(ok=False, error="Audit log không ghi được; hành động không chạy.")
        self._emit(ToolEventKind.FINISHED, pending.registered_tool.name, pending.registered_tool.tier, pending.call_id, state)
        return result

    def _resolve_sudo_continuation(
        self, pending: PendingToolAction,
    ) -> tuple[ExecutionPlan | None, ToolResult | None]:
        """Wait for Retry/Dismiss without audit drafts or a second confirmation."""
        callback = self._sudo_continuation_callback
        assert callback is not None
        still_unavailable = False
        self._pending_actions[pending.call_id] = pending
        try:
            while True:
                self._emit(
                    ToolEventKind.FINISHED,
                    pending.registered_tool.name,
                    pending.registered_tool.tier,
                    pending.call_id,
                    ToolOutcomeState.AUTHENTICATION_REQUIRED,
                )
                decision = callback(
                    SudoAuthenticationRequired(
                        call_id=pending.call_id,
                        tool_name=pending.registered_tool.name,
                        still_unavailable=still_unavailable,
                    )
                )
                if decision != SudoContinuationDecision.RETRY:
                    result = ToolResult(ok=False, error="Xác thực sudo đã bị hủy trước khi chạy hành động.")
                    return None, self._finish_pending(
                        pending, result, state=ToolOutcomeState.DENIED, event="denied"
                    )
                try:
                    # This makes fresh sudo -n checks while keeping the exact
                    # immutable argv that failed initial preflight.
                    return PrivilegeManager().revalidate(pending.plan), None
                except PrivilegeAuthenticationRequired:
                    still_unavailable = True
                except PrivilegeError as exc:
                    result = ToolResult(ok=False, error=str(exc))
                    return None, self._finish_pending(pending, result, state=ToolOutcomeState.ERROR)
        finally:
            self._pending_actions.pop(pending.call_id, None)

    def has_pending_action(self, call_id: str) -> bool:
        """Read-only test/bridge query; this state is never persisted."""
        return call_id in self._pending_actions

    def execute(self, tool_name: str, args: dict[str, Any], *, call_id: str | None = None) -> ToolResult:
        registered = get_tool(tool_name)
        if registered is None:
            result = ToolResult(ok=False, error=f"Tool '{tool_name}' không tồn tại.")
            try:
                audit.log_tool_call(tool_name, args, result, None)
            except OSError:
                self._emit(ToolEventKind.FINISHED, tool_name, None, call_id, ToolOutcomeState.ERROR)
                return ToolResult(ok=False, error="Audit log không ghi được.")
            self._emit(ToolEventKind.FINISHED, tool_name, None, call_id, ToolOutcomeState.ERROR)
            return result

        # Áp dụng cho cả đọc lẫn ghi — kiểm tra TRƯỚC cả bước xác nhận Tier 2, vì một
        # đường dẫn bị cấm thì không có xác nhận nào biến nó thành hợp lệ được.
        path_arg = args.get("path")
        if isinstance(path_arg, str) and is_path_forbidden(path_arg):
            result = ToolResult(
                ok=False, error=f"Đường dẫn '{path_arg}' nằm trong danh sách cấm truy cập."
            )
            try:
                audit.log_tool_call(tool_name, args, result, registered.tier)
            except OSError:
                self._emit(ToolEventKind.FINISHED, tool_name, registered.tier, call_id, ToolOutcomeState.ERROR)
                return ToolResult(ok=False, error="Audit log không ghi được.")
            self._emit(ToolEventKind.FINISHED, tool_name, registered.tier, call_id, ToolOutcomeState.ERROR)
            return result

        # Cưỡng chế "luôn search_package trước install_package" bằng state thật
        # (không chỉ mô tả trong tool schema): tên gói phải nằm trong tập tên mà
        # search_package đã THỰC SỰ trả về ở cùng phiên này, không phải chỉ cần đã
        # gọi search_package với query bất kỳ.
        if tool_name == "install_package":
            name_arg = args.get("name")
            if isinstance(name_arg, str) and name_arg.strip() and name_arg not in self._searched_package_names:
                result = ToolResult(
                    ok=False,
                    error=(
                        f"Chưa search_package cho tên gói '{name_arg}' trong phiên làm việc này — "
                        "gọi search_package trước rồi install_package đúng tên gói tìm được."
                    ),
                )
                try:
                    audit.log_tool_call(tool_name, args, result, registered.tier)
                except OSError:
                    self._emit(ToolEventKind.FINISHED, tool_name, registered.tier, call_id, ToolOutcomeState.ERROR)
                    return ToolResult(ok=False, error="Audit log không ghi được.")
                self._emit(ToolEventKind.FINISHED, tool_name, registered.tier, call_id, ToolOutcomeState.ERROR)
                return result

        execution_plan: ExecutionPlan | None = None
        resolved_from_sudo_pending = False
        if registered.tier == Tier.TIER_2_ACTION:
            request = ConfirmationRequest(
                tool_name=tool_name,
                tier=registered.tier,
                arguments=args,
                description=registered.description,
                risk=None,
                call_id=call_id,
            )
            if not self._confirm_callback(request):
                result = ToolResult(
                    ok=False, error=f"Tool '{tool_name}' (Tier 2) bị từ chối: chưa có xác nhận."
                )
                try:
                    audit.log_tool_call(tool_name, args, result, registered.tier, event="denied")
                except OSError:
                    self._emit(ToolEventKind.FINISHED, tool_name, registered.tier, call_id, ToolOutcomeState.ERROR)
                    return ToolResult(ok=False, error="Audit log không ghi được; hành động không chạy.")
                self._emit(ToolEventKind.FINISHED, tool_name, registered.tier, call_id, ToolOutcomeState.DENIED)
                return result

            # Only the GUI continuation consumer may wait for sudo. CLI leaves
            # this callback unset, retaining its immediate legacy ToolResult.
            if (
                self._sudo_continuation_callback is not None
                and registered.preflight is not None
                and call_id is not None
            ):
                try:
                    execution_plan = self._preflight(registered, args, call_id)
                except PrivilegeAuthenticationRequired as exc:
                    if exc.plan is None:
                        # Never rebuild a privileged command from raw arguments
                        # after authentication has been requested.
                        result = ToolResult(ok=False, error=str(exc))
                        try:
                            audit.log_tool_call(tool_name, args, result, registered.tier)
                        except OSError:
                            self._emit(ToolEventKind.FINISHED, tool_name, registered.tier, call_id, ToolOutcomeState.ERROR)
                            return ToolResult(ok=False, error="Audit log không ghi được; hành động không chạy.")
                        self._emit(ToolEventKind.FINISHED, tool_name, registered.tier, call_id, ToolOutcomeState.ERROR)
                        return result
                    pending = PendingToolAction(call_id, registered, dict(args), exc.plan)
                    execution_plan, terminal_result = self._resolve_sudo_continuation(pending)
                    if terminal_result is not None:
                        return terminal_result
                    resolved_from_sudo_pending = True
                except (PrivilegeError, ValueError) as exc:
                    # Only authentication-required can become a continuation.
                    # Other preflight failures have no runnable plan and must
                    # terminate before the tool function could rebuild argv.
                    result = ToolResult(ok=False, error=str(exc))
                    try:
                        audit.log_tool_call(tool_name, args, result, registered.tier)
                    except OSError:
                        self._emit(ToolEventKind.FINISHED, tool_name, registered.tier, call_id, ToolOutcomeState.ERROR)
                        return ToolResult(ok=False, error="Audit log không ghi được; hành động không chạy.")
                    self._emit(ToolEventKind.FINISHED, tool_name, registered.tier, call_id, ToolOutcomeState.ERROR)
                    return result

            # A continued call has one terminal audit record only (the pending
            # branch intentionally wrote no draft). Normal non-pending Tier 2
            # calls retain the established fail-closed intent record.
            if not resolved_from_sudo_pending:
                try:
                    audit.log_tool_call(tool_name, args, None, registered.tier, event="started")
                except OSError:
                    self._emit(ToolEventKind.FINISHED, tool_name, registered.tier, call_id, ToolOutcomeState.ERROR)
                    return ToolResult(ok=False, error="Audit log không ghi được; hành động không chạy.")
            self._emit(ToolEventKind.STARTED, tool_name, registered.tier, call_id)
        else:
            self._emit(ToolEventKind.STARTED, tool_name, registered.tier, call_id)

        call_args = dict(args)
        if self._system_profile is not None and "system_profile" in inspect.signature(registered.func).parameters:
            call_args["system_profile"] = self._system_profile
        if execution_plan is not None and "execution_plan" in inspect.signature(registered.func).parameters:
            call_args["execution_plan"] = execution_plan
        if execution_plan is not None and "call_id" in inspect.signature(registered.func).parameters:
            call_args["call_id"] = execution_plan.call_id

        start = time.monotonic()
        try:
            result = registered.func(**call_args)
        except Exception as e:
            result = ToolResult(ok=False, error=f"Lỗi khi chạy tool '{tool_name}': {e}")
        duration_ms = (time.monotonic() - start) * 1000

        if tool_name == "search_package" and result.ok and isinstance(result.data, dict):
            matched = result.data.get("matched_names")
            if isinstance(matched, (list, set, tuple)):
                self._searched_package_names.update(str(name) for name in matched)

        try:
            audit.log_tool_call(tool_name, args, result, registered.tier, duration_ms=duration_ms)
        except OSError:
            self._emit(ToolEventKind.FINISHED, tool_name, registered.tier, call_id, ToolOutcomeState.ERROR)
            if registered.tier == Tier.TIER_2_ACTION:
                # Không đổi ok=False sau side effect: caller không được nghĩ rằng
                # hành động chưa chạy và tự retry. Intent vẫn có trong audit.
                result.error = "Đã chạy tool nhưng không ghi được audit result; không tự chạy lại."
                return result
            return ToolResult(ok=False, error="Audit log không ghi được.")

        self._emit(
            ToolEventKind.FINISHED,
            tool_name,
            registered.tier,
            call_id,
            ToolOutcomeState.OK if result.ok else ToolOutcomeState.ERROR,
        )
        return result
