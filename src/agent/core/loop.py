"""Vòng lặp ReAct: gọi LLM kèm tool schema, thực thi tool_calls, lặp tới khi có
câu trả lời cuối hoặc chạm max_iters.

Khi executor từ chối một tool_call (Tier 2 chưa xác nhận, hoặc path nằm trong
danh sách cấm), executor.execute() vẫn trả về ToolResult(ok=False, error=<lý do>)
như mọi lỗi tool khác — vòng lặp đẩy y nguyên kết quả đó vào history dạng
Message(role=TOOL, content=<json chứa lý do từ chối>), để model đọc được lý do
và phản hồi đúng cho người dùng thay vì gọi lại tool đó vô hạn. max_iters vẫn là
giới hạn cứng cuối cùng.

session_id (tùy chọn, Giai đoạn 4): nếu có, lịch sử được load lại từ SQLite lúc
bắt đầu và mỗi message mới (user/assistant/tool) được ghi ngay xuống DB — không
chỉ giữ trong bộ nhớ tiến trình. compact_if_needed() chạy trước mỗi lượt gọi
provider để tránh history phình vô hạn trong session dài.

Giai đoạn 6 — vận hành bền vững, tất cả đều tùy chọn (không cấu hình = hành vi
như cũ):
- Budget: cost_db_path + soft_limit_usd + hard_limit_usd. Chạm "hard" -> bỏ qua
  LLM, trả lời từ RAG cục bộ qua offline_answer. Chạm "soft" -> chỉ log cảnh báo.
- Network fallback: fallback_provider. provider.chat() trực tiếp được thay bằng
  network_fallback.chat_with_fallback() — cả 2 provider lỗi -> None -> cũng rơi
  về offline_answer, không crash, không treo.
- Loop detection: loop_detector (mặc định tự tạo). Cùng (tool, args) lặp liên
  tiếp -> dừng sớm, không chạy tới max_iters.
- Cost tracking: record_usage() sau mỗi lần gọi provider thành công, dùng đúng
  số token thật trong response.usage (KHÔNG dùng ước lượng của memory/compaction.py).

Giai đoạn 7 — system_profile (dict | None): dependency injection cho eval framework.
None -> hành vi y hệt trước đây (tool tự gọi scan_system() thật). Có giá trị -> dựng
SystemProfile từ dict (điền mặc định cho trường thiếu) rồi tiêm vào ToolExecutor mặc
định, để get_system_info/search_package/search_linux_docs/install_package dùng đúng
profile giả thay vì quét máy thật — cho phép eval mô phỏng nhiều distro khác nhau mà
không cần chạy trên container thật của từng distro. Chỉ áp dụng khi KHÔNG truyền
executor riêng — executor tự cung cấp thì tự chịu trách nhiệm profile của mình.

Phase 12 (gui_implementation_plan.md) — `on_event` opt-in, mặc định None nên KHÔNG
đổi hành vi/return value cho CLI hiện có (run_chat_turn()/run_ask() không truyền
tham số này). Khi có, phát `AgentEvent` (turn started/finished, tool started/finished
qua ToolExecutor, provider failure/fallback-used, offline outcome) để GUI dựng trạng
thái Chat mà không cần đọc core trực tiếp. Event KHÔNG bao giờ mang raw tool args,
provider response nguyên văn hay exception/traceback — chỉ ID/tier/category/outcome
đã sanitize.

Dùng `chat_with_fallback_detailed()` (không phải `chat_with_fallback()` mỏng) để biết
CHÍNH XÁC provider nào đã trả lời (primary/fallback) — bắt buộc để: (a) phát đúng
event provider-failure/fallback-used, không đoán từ text/log; (b) gắn nhãn provider
đúng khi ghi nhận chi phí — nếu fallback trả lời, `record_usage()` dùng tên/model của
fallback (qua `fallback_provider_name`/`fallback_price_config`), không tiếp tục ghi
nhãn provider chính sai như hành vi cũ. `fallback_provider_name`/`fallback_price_config`
mặc định None -> không đổi hành vi cost tracking cũ khi không có fallback.

Phase 17 (gui_implementation_plan.md) — `cancellation_token` opt-in, mặc định None nên
KHÔNG đổi hành vi CLI/test hiện có. Khi có, checkpoint đọc `is_cancelled()` ở 5 điểm cố
định: đầu mỗi iteration mới, trước và ngay sau khi có response từ provider, trước và
ngay sau mỗi tool_call riêng lẻ trong một batch. Một tool đã thực sự gọi
`executor.execute()` (subprocess/side effect đã invoke) luôn được chạy hết — token chỉ
chặn ở checkpoint TIẾP THEO, không bao giờ ngắt tool đang chạy. Turn bị hủy kết thúc qua
`TurnFinished(outcome=TurnOutcome.CANCELLED, assistant_text=None)`, KHÔNG ghi thêm
message giả vào history/session (giống MAX_ITERS, khác LOOP_STOPPED cố ý có message giải
thích) — response vừa nhận từ provider bị hủy trước khi `_append` cũng bị bỏ hẳn, không
persist. Đây là lý do "cancelled" tách biệt "denied": một tool call đang chờ Tier 2/sudo
xác nhận bị đóng qua `deny_all()` vẫn được audit đúng là "denied" (lý do: không xác
nhận) — token cancellation chỉ dừng phần TIẾP THEO của turn, đọc ở checkpoint sau khi
tool đó (đã bị denied) trả về, không tự gán "denied" thành "cancelled" hay ngược lại.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Union

from agent.core.cancellation import CancellationToken
from agent.core.cost_tracker import check_budget, get_daily_total, get_session_total, record_usage
from agent.core.executor import ToolEvent, ToolExecutor
from agent.core.loop_detection import LoopDetector
from agent.core.network_fallback import ProviderErrorCategory, chat_with_fallback_detailed
from agent.core.offline_answer import format_offline_answer
from agent.core.tool_observation import ensure_tool_observation, wrap_tool_result
from agent.llm.base import LLMProvider, Message, Role
from agent.memory.compaction import compact_if_needed
from agent.memory.session_store import append_message, load_session
from agent.rag.schemas import DocumentRecord
from agent.system.profile import build_profile_from_dict
from agent.tools.registry import to_tool_schemas

logger = logging.getLogger(__name__)


class TurnOutcome(str, Enum):
    ANSWERED = "answered"
    OFFLINE = "offline"
    LOOP_STOPPED = "loop_stopped"
    MAX_ITERS = "max_iters"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TurnStarted:
    pass


@dataclass(frozen=True)
class TurnFinished:
    outcome: TurnOutcome
    assistant_text: str | None


@dataclass(frozen=True)
class ProviderFailure:
    """Primary lỗi (và fallback cũng lỗi hoặc không có fallback cấu hình) — luôn đi
    kèm một `OfflineFallbackUsed` + `TurnFinished(outcome=OFFLINE, ...)` riêng ngay
    sau đó, KHÔNG tự nó là "offline thành công"."""

    primary_error: ProviderErrorCategory
    fallback_attempted: bool
    fallback_error: ProviderErrorCategory | None = None


@dataclass(frozen=True)
class ProviderFallbackUsed:
    """Fallback THẬT SỰ trả lời — provenance đã xác nhận qua
    `chat_with_fallback_detailed()`, không suy đoán."""

    primary_error: ProviderErrorCategory
    fallback_provider_name: str | None


@dataclass(frozen=True)
class OfflineFallbackUsed:
    reason: str  # "provider_failure" | "budget_hard_limit"


AgentEvent = Union[
    TurnStarted, TurnFinished, ProviderFailure, ProviderFallbackUsed, OfflineFallbackUsed, ToolEvent
]
EventSink = Callable[[AgentEvent], None]


def _emit(on_event: EventSink | None, event: AgentEvent) -> None:
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception:
        logger.exception("on_event callback ném exception — bỏ qua, không ảnh hưởng tới agent loop.")


def run_agent_loop(
    provider: LLMProvider,
    user_message: str,
    *,
    system_prompt: str | None = None,
    max_iters: int = 10,
    executor: ToolExecutor | None = None,
    session_id: str | None = None,
    db_path: str | Path | None = None,
    max_history_tokens: int = 8000,
    fallback_provider: LLMProvider | None = None,
    provider_name: str = "primary",
    cost_db_path: str | Path | None = None,
    price_config: dict | None = None,
    fallback_provider_name: str | None = None,
    fallback_price_config: dict | None = None,
    soft_limit_usd: float | None = None,
    hard_limit_usd: float | None = None,
    loop_detector: LoopDetector | None = None,
    system_profile: dict | None = None,
    on_event: EventSink | None = None,
    cancellation_token: CancellationToken | None = None,
) -> list[Message]:
    if session_id is not None and db_path is None:
        raise ValueError("session_id yêu cầu db_path để load/lưu lịch sử.")

    if executor is None:
        injected_profile = build_profile_from_dict(system_profile) if system_profile is not None else None
        executor = ToolExecutor(system_profile=injected_profile, on_event=on_event)
    loop_detector = loop_detector or LoopDetector()
    tools = to_tool_schemas()
    budget_configured = cost_db_path is not None and soft_limit_usd is not None and hard_limit_usd is not None

    history: list[Message] = load_session(db_path, session_id) if session_id is not None else []
    for historical_message in history:
        if historical_message.role == Role.TOOL:
            # Old persisted sessions predate the trust envelope. Normalize only
            # the in-memory provider context; stored history remains untouched.
            historical_message.content = ensure_tool_observation(historical_message.content)

    def _append(message: Message) -> None:
        history.append(message)
        if session_id is not None:
            append_message(db_path, session_id, message)

    def _offline_fallback(reason: str) -> list[Message]:
        _emit(on_event, OfflineFallbackUsed(reason=reason))
        rag_result = executor.execute("search_linux_docs", {"query": user_message})
        raw_results = rag_result.data.get("results", []) if rag_result.ok and rag_result.data else []
        documents = [
            DocumentRecord(
                id=r["title"], title=r["title"], summary=r["summary"], source_url=r["source_url"],
                license="", distro_id=None, content_hash="",
            )
            for r in raw_results
        ]
        offline_text = format_offline_answer(user_message, documents)
        _append(Message(role=Role.ASSISTANT, content=offline_text))
        _emit(on_event, TurnFinished(outcome=TurnOutcome.OFFLINE, assistant_text=offline_text))
        return history

    if system_prompt:
        current_system_message = Message(role=Role.SYSTEM, content=system_prompt)
        if not history:
            _append(current_system_message)
        elif history[0].role == Role.SYSTEM:
            # A resumed session may contain an older prompt. Keep the effective
            # policy current without duplicating a persisted system message.
            history[0] = current_system_message
        else:
            # Legacy sessions without a system row still receive the current
            # policy in effective context on every run.
            history.insert(0, current_system_message)

    def _cancelled() -> bool:
        return cancellation_token is not None and cancellation_token.is_cancelled()

    def _cancel_turn() -> list[Message]:
        _emit(on_event, TurnFinished(outcome=TurnOutcome.CANCELLED, assistant_text=None))
        return history

    _append(Message(role=Role.USER, content=user_message))
    _emit(on_event, TurnStarted())

    for _ in range(max_iters):
        # Checkpoint: trước mỗi iteration mới (bao gồm trước provider call đầu
        # tiên) — không có tool nào đang chạy ở đây nên dừng luôn là an toàn.
        if _cancelled():
            return _cancel_turn()

        if budget_configured:
            if session_id is not None:
                current_total = get_session_total(cost_db_path, session_id)["cost_usd"]
            else:
                current_total = get_daily_total(cost_db_path)["cost_usd"]

            budget_status = check_budget(soft_limit_usd, hard_limit_usd, current_total)
            if budget_status == "hard":
                logger.warning(
                    f"Ngân sách chạm ngưỡng hard (đã dùng ${current_total:.4f}, hard=${hard_limit_usd}) "
                    f"— bỏ qua lời gọi LLM, chuyển sang chế độ ngoại tuyến."
                )
                return _offline_fallback(reason="budget_hard_limit")
            if budget_status == "soft":
                logger.warning(
                    f"Ngân sách chạm ngưỡng soft (đã dùng ${current_total:.4f}, soft=${soft_limit_usd}) "
                    f"— vẫn gọi LLM bình thường."
                )

        history = compact_if_needed(provider, history, max_tokens=max_history_tokens)

        outcome = chat_with_fallback_detailed(provider, fallback_provider, history, tools)
        response = outcome.response
        if response is None:
            logger.warning("Cả provider chính lẫn dự phòng đều lỗi (hoặc không có dự phòng) — chuyển sang chế độ ngoại tuyến.")
            # Hai việc tách biệt, không được gộp: (1) provider lỗi (category đã
            # sanitize) — (2) loop chuyển sang RAG offline (_offline_fallback tự phát
            # OfflineFallbackUsed + TurnFinished của riêng nó).
            _emit(
                on_event,
                ProviderFailure(
                    primary_error=outcome.primary_error,
                    fallback_attempted=outcome.fallback_attempted,
                    fallback_error=outcome.fallback_error,
                ),
            )
            return _offline_fallback(reason="provider_failure")

        if outcome.provider_used == "fallback":
            _emit(
                on_event,
                ProviderFallbackUsed(primary_error=outcome.primary_error, fallback_provider_name=fallback_provider_name),
            )

        if cost_db_path is not None and response.usage:
            # Gắn đúng provider THẬT SỰ trả lời (đã xác nhận qua provenance của
            # chat_with_fallback_detailed, không đoán) — nếu fallback trả lời, ghi
            # nhãn/model/giá của fallback, không tiếp tục ghi nhãn provider chính sai.
            if outcome.provider_used == "fallback" and fallback_provider is not None:
                record_usage(
                    cost_db_path,
                    session_id,
                    fallback_provider_name or "fallback",
                    fallback_provider.model_name,
                    response.usage,
                    fallback_price_config,
                )
            else:
                record_usage(cost_db_path, session_id, provider_name, provider.model_name, response.usage, price_config)

        # Checkpoint: response đã nhận (cost, nếu có, đã ghi nhận vì request
        # thật đã tốn phí) nhưng CHƯA ghi vào history/session — một turn bị
        # hủy không bao giờ để lại một câu trả lời "hoàn chỉnh giả".
        if _cancelled():
            return _cancel_turn()

        _append(Message(role=Role.ASSISTANT, content=response.content, tool_calls=response.tool_calls))

        if not response.tool_calls:
            _emit(on_event, TurnFinished(outcome=TurnOutcome.ANSWERED, assistant_text=response.content))
            return history

        looping = False
        cancelled_mid_batch = False
        for tool_call in response.tool_calls:
            # Checkpoint: trước tool call — tool này chưa hề gọi executor.execute()
            # (chưa có side effect nào bắt đầu), nên bỏ qua an toàn tại đây.
            if _cancelled():
                cancelled_mid_batch = True
                break

            result = executor.execute(tool_call.name, tool_call.arguments, call_id=tool_call.id)
            _append(
                Message(
                    role=Role.TOOL,
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    content=wrap_tool_result(result),
                )
            )

            # Checkpoint: ngay sau tool call — tool vừa chạy XONG (kết quả thật
            # đã được ghi ở trên dù bị hủy ngay sau đó); token chỉ chặn tool
            # TIẾP THEO trong cùng batch, không bao giờ ngắt cái vừa chạy.
            if _cancelled():
                cancelled_mid_batch = True
                break

            loop_detector.record(tool_call.name, tool_call.arguments)
            if loop_detector.is_looping():
                looping = True
                break

        if cancelled_mid_batch:
            return _cancel_turn()

        if looping:
            logger.warning(f"Phát hiện agent lặp lại cùng hành động '{tool_call.name}' — dừng sớm.")
            stop_text = "Agent đang lặp lại cùng một hành động, dừng lại để tránh lãng phí."
            _append(Message(role=Role.ASSISTANT, content=stop_text))
            _emit(on_event, TurnFinished(outcome=TurnOutcome.LOOP_STOPPED, assistant_text=stop_text))
            return history

    _emit(on_event, TurnFinished(outcome=TurnOutcome.MAX_ITERS, assistant_text=None))
    return history
