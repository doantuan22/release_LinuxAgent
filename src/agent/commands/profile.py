"""agent profile — hiển thị system profile mà agent phát hiện (CLI.md mục 11)."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.text import Text

from agent.errors import redact_cli_text
from agent.system.profile import scan_system, to_display_summary

console = Console()


def profile() -> None:
    """Hiển thị system profile mà agent phát hiện."""
    # scan_system() dùng lru_cache(maxsize=1) từ Giai đoạn 1 — gọi lại trong
    # cùng tiến trình KHÔNG quét lại /etc/os-release, PATH, systemd-detect-virt
    # (nguyên tắc an toàn #5: chỉ dữ liệu tĩnh/bán tĩnh mới được cache, và
    # SystemProfile hoàn toàn thuộc loại đó).
    system_profile = scan_system()

    table = Table(show_header=False, box=None, title="System Profile", title_justify="left")
    table.add_column(style="bold")
    table.add_column()
    for label, value in to_display_summary(system_profile):
        table.add_row(label, Text(redact_cli_text(value)))

    console.print(table)
