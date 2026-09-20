"""User memory: episodic + preference, tìm kiếm bằng FTS5 (Giai đoạn 4).

RAG thật (Giai đoạn 5) chưa tồn tại nên dùng module sqlite3 chuẩn của Python với
FTS5 làm cơ chế tìm kiếm tạm thời — schema (bảng memory) giữ nguyên để Giai đoạn 5
chỉ cần thêm cột embedding + tầng Reciprocal Rank Fusion lên trên, không đổi cấu
trúc bảng đã có.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agent.memory.schemas import MemoryEntry, check_schema_version

logger = logging.getLogger(__name__)

# Chuỗi liền sau "=", dài hơn 20 ký tự, không khoảng trắng — dấu hiệu giống
# secret/API key (vd "OPENAI_API_KEY=sk-..."). Redact trước khi lưu, không bao
# giờ lưu nguyên văn secret vào memory (nguyên tắc an toàn: dữ liệu tool/nội dung
# người dùng là quan sát, không phải nơi để giữ credential).
_SECRET_LIKE_RE = re.compile(r"=(\S{21,})")

# FTS5 có ngữ pháp truy vấn riêng (?, -, ", :, ...) — câu hỏi tự nhiên đưa thẳng vào
# MATCH có thể gây syntax error. Chỉ giữ token chữ/số (Unicode-aware) trước khi MATCH,
# nối bằng OR (không phải khoảng trắng = AND mặc định của FTS5, quá chặt cho câu hỏi
# nhiều từ) để giữ recall, rank vẫn tính đúng qua bm25()/rank.
_FTS5_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _sanitize_fts5_query(query: str) -> str:
    return " OR ".join(_FTS5_TOKEN_RE.findall(query))


_fts5_available: bool | None = None


def _is_fts5_available() -> bool:
    global _fts5_available
    if _fts5_available is not None:
        return _fts5_available

    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        finally:
            conn.close()
        _fts5_available = True
    except sqlite3.OperationalError:
        _fts5_available = False
        logger.warning(
            "SQLite build hiện tại không hỗ trợ FTS5 — search_memory() fallback sang "
            "LIKE query, độ chính xác tìm kiếm sẽ kém hơn full-text search thật."
        )

    return _fts5_available


def _redact_secrets(content: str) -> str:
    if not _SECRET_LIKE_RE.search(content):
        return content

    logger.warning("add_memory(): phát hiện chuỗi giống secret/API key trong nội dung, đã redact trước khi lưu.")
    return _SECRET_LIKE_RE.sub("=[REDACTED]", content)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def init_memory_db(db_path: str | Path) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            )
            """
        )
        conn.commit()
        check_schema_version(conn, "memory")

        if _is_fts5_available():
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(content, entry_id UNINDEXED)"
            )
            conn.commit()
    finally:
        conn.close()


def add_memory(db_path: str | Path, kind: Literal["episodic", "preference"], content: str) -> MemoryEntry:
    safe_content = _redact_secrets(content)
    entry = MemoryEntry(
        id=str(uuid.uuid4()),
        kind=kind,
        content=safe_content,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO memory (id, kind, content, created_at, schema_version) VALUES (?, ?, ?, ?, ?)",
            (entry.id, entry.kind, entry.content, entry.created_at, entry.schema_version),
        )
        if _is_fts5_available():
            conn.execute(
                "INSERT INTO memory_fts (content, entry_id) VALUES (?, ?)", (entry.content, entry.id)
            )
        conn.commit()
    finally:
        conn.close()

    return entry


def search_memory(
    db_path: str | Path,
    query: str,
    *,
    kind: Literal["episodic", "preference"] | None = None,
    limit: int = 10,
) -> list[MemoryEntry]:
    if _is_fts5_available() and not _sanitize_fts5_query(query):
        return []

    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if _is_fts5_available():
            sql = (
                "SELECT m.id, m.kind, m.content, m.created_at, m.schema_version "
                "FROM memory_fts f JOIN memory m ON m.id = f.entry_id "
                "WHERE f.content MATCH ?"
            )
            params: list[Any] = [_sanitize_fts5_query(query)]
            if kind is not None:
                sql += " AND m.kind = ?"
                params.append(kind)
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)
        else:
            sql = "SELECT id, kind, content, created_at, schema_version FROM memory WHERE content LIKE ?"
            params = [f"%{query}%"]
            if kind is not None:
                sql += " AND kind = ?"
                params.append(kind)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning(f"Truy vấn tìm memory không hợp lệ ('{query}'): {e} — trả về không có kết quả.")
            return []
    finally:
        conn.close()

    return [
        MemoryEntry(
            id=row["id"],
            kind=row["kind"],
            content=row["content"],
            created_at=row["created_at"],
            schema_version=row["schema_version"],
        )
        for row in rows
    ]
