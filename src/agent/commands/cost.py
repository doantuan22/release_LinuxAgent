"""``agent cost`` — xem usage/cost đã được core ghi (CLI.md mục 21/22)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from agent import paths
from agent.core import cost_tracker
from agent.errors import redact_cli_text
from agent.services import session_service

console = Console()
cost_app = typer.Typer(help="Xem usage token và chi phí LLM.")


def _format_cost(value: int | float) -> str:
    # Giữ tối đa 8 chữ số thập phân cho usage rất nhỏ, tối thiểu 3 như CLI.md.
    whole, fraction = f"{float(value):,.8f}".split(".")
    fraction = fraction.rstrip("0").ljust(3, "0")
    return f"${whole}.{fraction}"


def _render_summary(title: str, summary: dict[str, int | float]) -> None:
    table = Table(title=title, title_justify="left", show_header=False, box=None)
    table.add_column(style="bold")
    table.add_column(justify="right")
    table.add_row("Requests", f"{int(summary['requests']):,}")
    table.add_row("Input Tokens", f"{int(summary['prompt_tokens']):,}")
    table.add_row("Output Tokens", f"{int(summary['completion_tokens']):,}")
    table.add_row("Estimated Cost", _format_cost(summary["cost_usd"]))
    console.print(table)


@cost_app.command("today")
def today_command() -> None:
    """Hiển thị usage của ngày hiện tại."""
    try:
        summary = cost_tracker.get_daily_summary(paths.memory_db())
    except Exception:
        console.print("[red]Không thể đọc dữ liệu usage.[/]")
        raise typer.Exit(code=1)
    _render_summary("Usage Today", summary)


@cost_app.command("session")
def session_command(
    session_id: str = typer.Argument(..., help="Session id (rút gọn 8 ký tự hoặc đầy đủ)"),
) -> None:
    """Hiển thị usage của một session tồn tại."""
    try:
        record = session_service.find_session_by_id_prefix(paths.sessions_db(), session_id)
    except session_service.AmbiguousSessionIdError:
        console.print("[red]Session ID không duy nhất; hãy nhập thêm ký tự.[/]")
        raise typer.Exit(code=1)
    except Exception:
        console.print("[red]Không thể đọc session database.[/]")
        raise typer.Exit(code=1)
    if record is None:
        console.print(f"[red]Không tìm thấy session '{redact_cli_text(session_id)}'.[/]")
        raise typer.Exit(code=1)

    try:
        summary = cost_tracker.get_session_summary(paths.memory_db(), record.id)
    except Exception:
        console.print("[red]Không thể đọc dữ liệu usage.[/]")
        raise typer.Exit(code=1)
    _render_summary(f"Session {record.id[:8]}", summary)
