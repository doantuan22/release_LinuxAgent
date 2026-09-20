"""agent chat — REPL tương tác (CLI.md mục 4.1, 4.2, 4.3, 23, 24).

Ctrl+C khi đang nhập hoặc khi agent đang xử lý: hủy lượt hiện tại, KHÔNG kill
tiến trình. Ctrl+D (EOF): lưu session, thoát sạch, không traceback (mục 23).

--resume <id>: mở lại session cũ theo id (rút gọn 8 ký tự như hiển thị ở
`agent sessions list`, hoặc UUID đầy đủ). --resume-last: mở session được cập
nhật gần nhất theo `updated_at` (mục 4.3), không phải theo `created_at`. Không
có session nào phù hợp -> báo lỗi rõ ràng, không crash, không tự tạo session mới
thay thế (tránh người dùng tưởng nhầm là đã resume đúng).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import typer
from rich.console import Console

from agent import paths
from agent.core.confirm_policies import interactive_confirm
from agent.errors import AgentExecutionError, redact_cli_text
from agent.memory.schemas import SessionRecord
from agent.services import session_service
from agent.services.agent_service import (
    last_assistant_text,
    load_configured_budget_limits,
    load_configured_provider_with_fallback,
    run_chat_turn,
)
from agent.system.profile import scan_system, to_banner_line

console = Console()

_HELP_TEXT = """Các lệnh nội bộ:
  /help      Hiện trợ giúp này
  /session   In session id hiện tại
  /profile   Xem system profile (chưa khả dụng, xem ở Phase 5)
  /cost      Xem chi phí phiên (chưa khả dụng, xem ở Phase 7)
  /clear     Xóa màn hình
  /exit      Thoát"""


def chat(
    resume: str = typer.Option(None, "--resume", help="Mở lại session cũ theo id (rút gọn hoặc đầy đủ)"),
    resume_last: bool = typer.Option(False, "--resume-last", help="Mở session được cập nhật gần nhất"),
) -> None:
    """Chạy Linux Agent ở chế độ tương tác REPL."""
    if resume is not None and resume_last:
        console.print("[red]Chỉ dùng một trong hai: --resume hoặc --resume-last.[/]")
        raise typer.Exit(code=1)
    if resume is not None and not resume.strip():
        console.print("[red]Session ID không hợp lệ: không được để trống.[/]")
        raise typer.Exit(code=1)

    db_path = paths.sessions_db()
    # Với --resume, kiểm tra session trước provider để lỗi ID trả đúng code 1,
    # và không tạo session mới nếu provider chưa cấu hình.
    try:
        session = _resolve_session(db_path, resume=resume, resume_last=resume_last) if resume is not None or resume_last else None
    except session_service.AmbiguousSessionIdError:
        console.print("[red]Session ID không duy nhất; hãy nhập thêm ký tự.[/]")
        raise typer.Exit(code=1)
    except (sqlite3.DatabaseError, OSError, RuntimeError):
        console.print("[red]Session database không đọc được hoặc schema không tương thích.[/]")
        raise typer.Exit(code=1)
    provider, fallback_provider = load_configured_provider_with_fallback()
    budget_limits = load_configured_budget_limits()
    if session is None:
        try:
            session = session_service.create_new_session(db_path)
        except (sqlite3.DatabaseError, OSError, RuntimeError):
            console.print("[red]Session database không đọc được hoặc schema không tương thích.[/]")
            raise typer.Exit(code=1)

    profile = scan_system()

    console.print("[bold]Linux Agent[/]")
    console.print(redact_cli_text(to_banner_line(profile)))
    console.print(f"Session: {session.id[:8]}" + ("  (đã mở lại)" if resume is not None or resume_last else ""))
    console.print("Type /help for commands · Ctrl+D to exit\n")

    while True:
        try:
            user_input = console.input("[bold cyan]you>[/] ")
        except KeyboardInterrupt:
            console.print()
            continue
        except EOFError:
            console.print("\nSession saved.")
            console.print("Goodbye.")
            raise typer.Exit(code=0)

        stripped = user_input.strip()
        if not stripped:
            continue

        if stripped.startswith("/"):
            _handle_internal_command(stripped, session_id=session.id)
            continue

        try:
            with console.status("[cyan]Thinking...[/]", spinner="dots"):
                history = run_chat_turn(
                    provider,
                    stripped,
                    fallback_provider=fallback_provider,
                    session_id=session.id,
                    db_path=db_path,
                    confirm_callback=interactive_confirm,
                    soft_limit_usd=budget_limits.soft_limit_usd,
                    hard_limit_usd=budget_limits.hard_limit_usd,
                )
        except KeyboardInterrupt:
            console.print("[yellow]Request interrupted.[/]")
            continue
        except AgentExecutionError:
            console.print("[red]Agent execution failed for this request.[/]")
            continue

        answer = last_assistant_text(history)
        if answer:
            console.print(f"[bold green]agent>[/] {redact_cli_text(answer)}")


def _resolve_session(db_path: Path, *, resume: str | None, resume_last: bool) -> SessionRecord:
    if resume_last:
        last_id = session_service.get_last_session(db_path)
        if last_id is None:
            console.print("[red]Chưa có session nào để mở lại (--resume-last).[/]")
            raise typer.Exit(code=1)
        session = session_service.find_session_by_id_prefix(db_path, last_id)
        if session is None:
            console.print("[red]Không thể mở lại session gần nhất — dữ liệu không nhất quán.[/]")
            raise typer.Exit(code=1)
        return session

    if resume is not None:
        session = session_service.find_session_by_id_prefix(db_path, resume)
        if session is None:
            console.print(f"[red]Không tìm thấy session '{redact_cli_text(resume)}'.[/]")
            raise typer.Exit(code=1)
        return session

    return session_service.create_new_session(db_path)


def _handle_internal_command(command: str, *, session_id: str) -> None:
    if command in ("/exit", "/quit"):
        console.print("Session saved.")
        console.print("Goodbye.")
        raise typer.Exit(code=0)
    if command == "/help":
        console.print(_HELP_TEXT)
        return
    if command == "/session":
        console.print(session_id)
        return
    if command == "/profile":
        console.print("[yellow]/profile chưa khả dụng, xem ở Phase 5.[/]")
        return
    if command == "/cost":
        console.print("[yellow]/cost chưa khả dụng, xem ở Phase 7.[/]")
        return
    if command == "/clear":
        console.clear()
        return
    console.print(f"[red]Lệnh không hợp lệ: {command}. Gõ /help để xem danh sách lệnh.[/]")
