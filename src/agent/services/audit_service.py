"""Application service đọc audit JSONL (`core/audit.py` chỉ ghi) — dùng
chung cho `commands/audit.py` (CLI) và GUI (Phase 8 Audit page).

Chỉ expose field đã được phép: `timestamp`/`tier`/`tool_name`/`event`/
`result`/`duration_ms`. TUYỆT ĐỐI không trả `args`/`args_digest`/`error`
nguyên văn ra ngoài service — kể cả khi đọc `error` nội bộ để suy ra
`result` cho entry cũ thiếu `event` (fallback tương thích ngược), giá trị đó
không bao giờ được gắn vào `AuditRecord` trả ra ngoài.

Không import Typer/Rich/Qt — CLI giữ nguyên toàn bộ phần trình bày (Rich
table, định dạng ms/nhãn Tier) và follow/polling/Ctrl+C; chỉ phần đọc/parse/
chuẩn hoá dữ liệu chuyển vào đây để CLI và GUI dùng chung, không lệch nhau.
"""

from __future__ import annotations

import json
import math
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from agent.core.redaction import redact_known_secrets
from agent.tools.schemas import Tier

_SAFE_TOOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KNOWN_TIERS = {tier.value for tier in Tier}
_INVALID_TOOL_NAME = "<invalid>"


@dataclass
class AuditRecord:
    """Một dòng audit đã được chuẩn hoá/kiểm tra an toàn để hiển thị."""

    timestamp: str | None
    tier: str | None
    tool_name: str
    event: str | None
    result: str
    duration_ms: float | None


@dataclass
class AuditTailResult:
    status: str  # "ok" | "missing" | "read_error"
    entries: list[AuditRecord] = field(default_factory=list)
    malformed_count: int = 0


def _parse_line(line: str) -> dict[str, Any] | None:
    try:
        entry = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    return entry if isinstance(entry, dict) else None


def _normalize_timestamp(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _normalize_tier(value: Any) -> str | None:
    return value if isinstance(value, str) and value in _KNOWN_TIERS else None


def _normalize_event(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _normalize_tool_name(value: Any) -> str:
    if isinstance(value, str) and _SAFE_TOOL_NAME.fullmatch(value):
        # Regex đã chặn hầu hết định dạng secret thường gặp (chứa '-', '.'...),
        # nhưng vẫn redact phòng khi giá trị bí mật đang cấu hình trùng ngẫu
        # nhiên với một chuỗi định danh hợp lệ — cùng mức phòng thủ CLI cũ
        # (`redact_cli_text` áp lên tool_name đã validate).
        return redact_known_secrets(value)
    return _INVALID_TOOL_NAME


def _normalize_duration(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric < 0:
        return None
    return numeric


def _classify_result(entry: dict[str, Any]) -> str:
    """Suy ra trạng thái hiển thị từ `event`/`ok`/`error` — `error` chỉ đọc
    nội bộ ở đây (fallback cho entry cũ thiếu `event`, phân biệt denied với
    error khác qua nội dung câu báo lỗi cố định do `core.audit` ghi), KHÔNG
    bao giờ được trả ra ngoài qua `AuditRecord`."""
    event = entry.get("event")
    if event == "started":
        return "started"
    if event == "denied":
        return "denied"

    ok = entry.get("ok")
    if ok is True:
        return "success"
    if ok is False:
        error = entry.get("error")
        if isinstance(error, str) and ("bị từ chối" in error.lower() or "denied" in error.lower()):
            return "denied"
        return "error"
    return "unknown"


def _to_record(entry: dict[str, Any]) -> AuditRecord:
    return AuditRecord(
        timestamp=_normalize_timestamp(entry.get("timestamp")),
        tier=_normalize_tier(entry.get("tier")),
        tool_name=_normalize_tool_name(entry.get("tool_name")),
        event=_normalize_event(entry.get("event")),
        result=_classify_result(entry),
        duration_ms=_normalize_duration(entry.get("duration_ms")),
    )


def parse_entry(line: str) -> AuditRecord | None:
    """Parse một dòng JSONL đơn lẻ; `None` nếu dòng malformed. Dùng cho
    follow mode (CLI tự đọc từng dòng mới xuất hiện qua handle riêng)."""
    entry = _parse_line(line)
    return _to_record(entry) if entry is not None else None


def read_bounded_tail(handle: TextIO, count: int) -> tuple[list[AuditRecord], int]:
    """Đọc `handle` (đã mở sẵn) tới hết, giữ lại tối đa `count` entry HỢP LỆ
    cuối cùng — dòng malformed không chiếm chỗ trong cửa sổ `count`, chỉ
    được đếm riêng. Nhận handle thay vì tự mở file để CLI có thể giữ handle
    sống tiếp tục dùng cho follow mode (không mở lại file, không lệch vị trí
    đọc)."""
    window: deque[AuditRecord] = deque(maxlen=count)
    malformed = 0
    for line in handle:
        entry = _parse_line(line)
        if entry is None:
            malformed += 1
        else:
            window.append(_to_record(entry))
    return list(window), malformed


def tail_file(log_path: Path, count: int) -> AuditTailResult:
    """Entry point tự quản lý vòng đời file, an toàn cho caller không cần
    giữ handle sống (GUI Phase 8 không có follow; CLI dùng cho phần tail ban
    đầu). File mất hoặc đọc lỗi trả `status` rõ ràng, không raise."""
    if not log_path.is_file():
        return AuditTailResult(status="missing")
    try:
        with log_path.open("r", encoding="utf-8") as handle:
            entries, malformed = read_bounded_tail(handle, count)
    except (OSError, UnicodeError):
        return AuditTailResult(status="read_error")
    return AuditTailResult(status="ok", entries=entries, malformed_count=malformed)
