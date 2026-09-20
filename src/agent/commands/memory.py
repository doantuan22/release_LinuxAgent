"""Developer command đọc user memory trực tiếp, không qua LLM tool."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from agent import paths
from agent.errors import redact_cli_text
from agent.memory import user_memory
from agent.memory.schemas import check_schema_version

console = Console()
memory_app = typer.Typer(help="Tìm kiếm user memory để debug.")


def _check_database(db_path: Path) -> bool:
    """Trả False khi DB chỉ có usage_log; validate schema nếu đã có memory."""
    with sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True) as conn:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory'"
        ).fetchone() is None:
            return False
        conn.execute(
            "SELECT id, kind, content, created_at, schema_version FROM memory LIMIT 0"
        )
        check_schema_version(conn, "memory")
        if user_memory._is_fts5_available():
            conn.execute("SELECT content, entry_id FROM memory_fts LIMIT 0")
    return True


@memory_app.command("search")
def search_command(query: str = typer.Argument(..., help="Từ khóa tìm trong user memory")) -> None:
    """Tìm memory đã lưu; thứ tự kết quả là rank FTS5/LIKE của core."""
    if not query.strip():
        console.print("[red]Query không được rỗng.[/]")
        raise typer.Exit(code=1)

    db_path = paths.memory_db()
    if not db_path.exists():
        console.print("Memory database chưa tồn tại.")
        return

    try:
        if not _check_database(db_path):
            console.print("Không tìm thấy memory phù hợp.")
            return
        results = user_memory.search_memory(db_path, query)
    except Exception:
        console.print("[red]Không thể tìm memory: memory database lỗi hoặc schema không hợp lệ.[/]")
        raise typer.Exit(code=1)

    if not results:
        console.print("Không tìm thấy memory phù hợp.")
        return

    # search_memory() chỉ trả MemoryEntry, không có numeric score; rank là thứ tự API.
    table = Table(title=f"{len(results)} memory result(s)")
    table.add_column("Rank", justify="right")
    table.add_column("Kind")
    table.add_column("Content")
    for rank, entry in enumerate(results, start=1):
        table.add_row(str(rank), Text(entry.kind), Text(redact_cli_text(entry.content)))
    console.print(table)
