"""Developer command tìm trực tiếp trong RAG index, giữ RRF score."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import typer
from rich.console import Console
from rich.text import Text

from agent import paths
from agent.errors import redact_cli_text
from agent.memory.schemas import check_schema_version
from agent.rag import index
from agent.rag.schemas import DocumentRecord

console = Console()
rag_app = typer.Typer(help="Tìm trực tiếp trong RAG index để debug.")


def _check_database(db_path: Path) -> None:
    """Phát hiện DB hỏng/schema thiếu mà index.search có thể coi là không match."""
    with sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True) as conn:
        conn.execute(
            "SELECT id, title, summary, source_url, license, distro_id, "
            "content_hash, schema_version FROM documents LIMIT 0"
        )
        conn.execute("SELECT title, summary, doc_id FROM documents_fts LIMIT 0")
        check_schema_version(conn, "documents")


@rag_app.command("search")
def search_command(query: str = typer.Argument(..., help="Từ khóa tìm trong RAG index")) -> None:
    """Tìm default/user DB trực tiếp; dedup theo doc ID và giữ RRF score."""
    if not query.strip():
        console.print("[red]Query không được rỗng.[/]")
        raise typer.Exit(code=1)

    db_paths = (("default", paths.rag_default_db()), ("user", paths.rag_user_db()))
    found_database = False
    ranked: list[tuple[DocumentRecord, float, str]] = []
    seen_ids: set[str] = set()

    for source, db_path in db_paths:
        if not db_path.exists():
            continue
        found_database = True
        try:
            _check_database(db_path)
            hits = index.search(db_path, query)
        except Exception:
            console.print(f"[red]Không thể tìm RAG: database {source} lỗi hoặc schema không hợp lệ.[/]")
            raise typer.Exit(code=1)

        # Giống search_linux_docs: default trước, bỏ duplicate doc ID; debug CLI
        # giữ nguyên RRF score mà LLM tool cố ý loại bỏ.
        for document, score in hits:
            if document.id in seen_ids:
                continue
            seen_ids.add(document.id)
            ranked.append((document, score, source))

    if not found_database:
        console.print("Chưa có RAG database.")
        return
    if not ranked:
        console.print("Không tìm thấy tài liệu phù hợp.")
        return

    ranked.sort(key=lambda hit: hit[1], reverse=True)
    console.print(f"{len(ranked)} RAG result(s)")
    for rank, (document, score, source) in enumerate(ranked, start=1):
        heading = Text()
        heading.append(f"{rank}. ")
        heading.append(redact_cli_text(document.title), style="bold")
        heading.append(f"  RRF Score: {score:.8f}  [{source}]")
        console.print(heading)
        console.print(Text(redact_cli_text(document.summary)))
        console.print(Text(redact_cli_text(document.source_url)))
