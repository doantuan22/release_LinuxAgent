"""``agent audit tail`` — đọc audit JSONL ở XDG state (CLI.md mục 18).

Phase 7 (gui_implementation_plan.md): đọc/parse/chuẩn hoá dữ liệu chuyển hết
sang `services/audit_service.py` (dùng chung với GUI Phase 8) — CLI chỉ còn
giữ phần trình bày Rich (table, định dạng giờ địa phương/ms/nhãn Tier) và
toàn bộ follow/polling/Ctrl+C (không thuộc service, chỉ CLI cần).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

import typer
from rich.console import Console
from rich.table import Table

from agent import paths
from agent.services import audit_service
from agent.services.audit_service import AuditRecord

console = Console()
audit_app = typer.Typer(help="Xem audit log của các tool execution.")

_DEFAULT_LINES = 20
_POLL_INTERVAL_SECONDS = 0.5
_TIER_LABELS = {
    "tier_1_readonly": "Tier 1",
    "tier_2_action": "Tier 2",
}


def _format_timestamp(value: str | None) -> str:
    if value is None:
        return "-"
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone().strftime("%H:%M:%S %Z")
    except ValueError:
        return "-"


def _format_tier(value: str | None) -> str:
    return _TIER_LABELS.get(value, "-") if value is not None else "-"


def _format_duration(value: float | None) -> str:
    if value is None:
        return "-"
    if value.is_integer():
        return f"{int(value)}ms"
    return f"{value:.1f}ms"


def _safe_row(record: AuditRecord) -> tuple[str, str, str, str, str]:
    """`record` đã qua `audit_service` chuẩn hoá/redact — hàm này chỉ format
    hiển thị, không tự đọc/validate field thô nào nữa."""
    return (
        _format_timestamp(record.timestamp),
        _format_tier(record.tier),
        record.tool_name,
        record.result,
        _format_duration(record.duration_ms),
    )


def _render_entries(entries: list[AuditRecord], *, show_header: bool = True) -> None:
    if not entries:
        return
    table = Table(box=None, show_header=show_header, pad_edge=False)
    table.add_column("Time")
    table.add_column("Tier")
    table.add_column("Tool")
    table.add_column("Result")
    table.add_column("Duration", justify="right")
    for record in entries:
        table.add_row(*_safe_row(record))
    console.print(table)


def _follow(handle: TextIO) -> None:
    """Đọc từ vị trí hiện tại (EOF sau initial tail), mỗi dòng đúng một lần."""
    while True:
        line = handle.readline()
        if not line:
            time.sleep(_POLL_INTERVAL_SECONDS)
            continue
        record = audit_service.parse_entry(line)
        if record is None:
            console.print("[yellow]Skipped malformed audit line.[/]")
            continue
        _render_entries([record], show_header=False)


@audit_app.command("tail")
def tail_command(
    lines: int = typer.Option(_DEFAULT_LINES, "-n", min=1, help="Số entry cuối cần hiển thị."),
    follow: bool = typer.Option(False, "-f", help="Theo dõi entry mới bằng polling."),
) -> None:
    """Hiển thị các audit entry gần nhất; malformed line được bỏ qua an toàn."""
    log_path: Path = paths.audit_log_path()
    if not log_path.is_file():
        console.print("Audit log chưa tồn tại.")
        return

    # Một handle DUY NHẤT xuyên suốt tail + follow (đúng hành vi trước Phase
    # 7) — nếu tail tự mở/đóng handle riêng rồi follow mở lại, khoảng hở
    # giữa hai lần mở có thể làm mất im lặng entry ghi đúng lúc đó. Vì vậy
    # phần tail gọi thẳng `audit_service.read_bounded_tail(handle, ...)`
    # (nhận handle sống) thay vì `audit_service.tail_file()` (tự mở/đóng,
    # chỉ hợp cho caller không cần giữ handle tiếp theo — ví dụ GUI Phase 8
    # không có follow, vẫn dùng `tail_file()`/status missing/read_error).
    try:
        with log_path.open("r", encoding="utf-8") as handle:
            entries, malformed = audit_service.read_bounded_tail(handle, lines)

            if entries:
                _render_entries(entries)
            elif malformed == 0:
                console.print("Audit log đang rỗng.")

            if malformed:
                console.print(f"[yellow]Skipped {malformed} malformed audit line(s).[/]")

            if not follow:
                return

            console.print("[dim]Following audit log. Press Ctrl+C to stop.[/]")
            try:
                _follow(handle)
            except KeyboardInterrupt:
                console.print("\nStopped following audit log.")
    except (OSError, UnicodeError):
        console.print("[red]Không thể đọc audit log.[/]")
        raise typer.Exit(code=1)
