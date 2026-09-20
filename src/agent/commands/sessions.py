"""agent sessions list/show (CLI.md mục 9, 10)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from agent import paths
from agent.errors import redact_cli_text
from agent.llm.base import Role
from agent.services import session_service

console = Console()

sessions_app = typer.Typer(help="Xem lại các session hội thoại trước đây.")


def _format_timestamp(value: str) -> str:
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    except ValueError:
        return value


@sessions_app.command("list")
def list_sessions_command() -> None:
    """Liệt kê các session trước đây, mới cập nhật nhất lên đầu (mục 9)."""
    try:
        sessions = session_service.list_all_sessions(paths.sessions_db())
    except (sqlite3.DatabaseError, OSError, RuntimeError):
        console.print("[red]Session database không đọc được hoặc schema không tương thích.[/]")
        raise typer.Exit(code=1)
    if not sessions:
        console.print("Chưa có session nào.")
        return

    if console.width < 90:
        # Bảng 5 cột quá rộng cho PTY/terminal hẹp; giữ đủ metadata theo DoD.
        for record in sessions:
            title = redact_cli_text(record.title)
            available = max(8, console.width - 13)
            if len(title) > available:
                title = title[: available - 1] + "…"
            console.print(f"{record.id[:8]} · {title}")
            console.print(f"  Created: {_format_timestamp(record.created_at)}")
            console.print(f"  Updated: {_format_timestamp(record.updated_at)}")
            console.print(f"  System: {redact_cli_text(record.distro_at_creation)} {redact_cli_text(record.distro_version_at_creation)}")
        return

    table = Table()
    table.add_column("ID")
    table.add_column("Title")
    table.add_column("Created")
    table.add_column("Updated")
    table.add_column("System")

    for record in sessions:
        table.add_row(
            record.id[:8],
            Text(redact_cli_text(record.title)),
            _format_timestamp(record.created_at),
            _format_timestamp(record.updated_at),
            f"{record.distro_at_creation} {record.distro_version_at_creation}",
        )

    console.print(table)


@sessions_app.command("show")
def show_session_command(
    session_id: str = typer.Argument(..., help="Session id (rút gọn 8 ký tự hoặc đầy đủ)"),
) -> None:
    """Hiển thị lại toàn bộ conversation của một session, đúng thứ tự (mục 10)."""
    db_path = paths.sessions_db()
    try:
        record = session_service.find_session_by_id_prefix(db_path, session_id)
    except session_service.AmbiguousSessionIdError:
        console.print("[red]Session ID không duy nhất; hãy nhập thêm ký tự.[/]")
        raise typer.Exit(code=1)
    except (sqlite3.DatabaseError, OSError, RuntimeError):
        console.print("[red]Session database không đọc được hoặc schema không tương thích.[/]")
        raise typer.Exit(code=1)
    if record is None:
        console.print(f"[red]Không tìm thấy session '{redact_cli_text(session_id)}'.[/]")
        raise typer.Exit(code=1)

    console.print(f"Session: {record.id[:8]}")
    console.print(redact_cli_text(f"{record.distro_at_creation} {record.distro_version_at_creation}"))
    console.print(f"Created: {_format_timestamp(record.created_at)}")
    console.print()

    try:
        messages = session_service.load_session_messages(db_path, record.id)
    except (sqlite3.DatabaseError, OSError, RuntimeError):
        console.print("[red]Session database không đọc được hoặc schema không tương thích.[/]")
        raise typer.Exit(code=1)

    for message in messages:
        if message.role == Role.SYSTEM:
            continue
        if message.role == Role.TOOL:
            console.print(f"[bold]TOOL[/] {redact_cli_text(message.name or '')}")
            console.print()
            continue
        if not message.content:
            continue
        console.print(f"[bold]{message.role.value.upper()}[/]")
        console.print(Text(redact_cli_text(message.content)))
        console.print()
