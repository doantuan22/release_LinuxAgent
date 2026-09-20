"""Session persistence: lưu/đọc lại hội thoại giữa các lần chạy agent (Giai đoạn 4).

distro_at_creation lấy từ system_profile.scan_system() tại đúng thời điểm tạo
session — không đọc lại lúc load_session(), để mở lại session cũ vẫn hiểu đúng
ngữ cảnh máy lúc đó nếu người dùng đổi máy/distro giữa các lần dùng.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent.core.redaction import redact_audit_data, sanitize_for_persistence
from agent.core.tool_observation import parse_tool_payload, replace_tool_payload
from agent.llm.base import LLMProvider, Message, Role, ToolCall
from agent.memory.schemas import MessageRecord, SESSION_SCHEMA_VERSION, SessionRecord, check_schema_version
from agent.system.profile import scan_system

_FALLBACK_TITLE_LENGTH = 40


def _connect(db_path: str | Path) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        fd = os.open(db_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
    return sqlite3.connect(db_path)


def init_db(db_path: str | Path) -> None:
    """Tạo bảng nếu chưa có. LƯU Ý: `CREATE TABLE IF NOT EXISTS` không đổi cấu
    trúc bảng đã tồn tại — một session.db cũ từ trước Phase 4 (schema_version=1,
    thiếu cột updated_at/distro_version_at_creation) vẫn giữ nguyên cấu trúc cũ,
    nhưng check_schema_version() bên dưới sẽ raise ngay vì dữ liệu cũ mang
    schema_version=1 trong khi code hiện tại cần SESSION_SCHEMA_VERSION=2 — đúng
    chính sách "raise rõ ràng, không tự động migrate ngầm" đã thống nhất. Xử lý:
    xóa file DB cũ rồi chạy lại (dự án chưa có người dùng thật)."""
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                distro_at_creation TEXT NOT NULL,
                distro_version_at_creation TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                role TEXT NOT NULL,
                content TEXT,
                tool_calls_json TEXT,
                created_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            )
            """
        )
        conn.commit()
        check_schema_version(conn, "sessions", SESSION_SCHEMA_VERSION)
        check_schema_version(conn, "messages", SESSION_SCHEMA_VERSION)
    finally:
        conn.close()


def create_session(db_path: str | Path, *, title: str = "Cuộc trò chuyện mới") -> SessionRecord:
    init_db(db_path)

    profile = scan_system()
    now = datetime.now(timezone.utc).isoformat()
    record = SessionRecord(
        id=str(uuid.uuid4()),
        title=sanitize_for_persistence(title),
        distro_at_creation=profile.distro_id,
        distro_version_at_creation=profile.distro_version,
        created_at=now,
        updated_at=now,
    )

    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO sessions (id, title, distro_at_creation, distro_version_at_creation, "
            "created_at, updated_at, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.title,
                record.distro_at_creation,
                record.distro_version_at_creation,
                record.created_at,
                record.updated_at,
                record.schema_version,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return record


def _tool_calls_json_for(message: Message) -> str | None:
    if message.role == Role.ASSISTANT and message.tool_calls:
        return json.dumps(
            [sanitize_for_persistence(redact_audit_data({"id": tc.id, "name": tc.name, "arguments": tc.arguments}))
             for tc in message.tool_calls],
            ensure_ascii=False,
        )
    if message.role == Role.TOOL:
        return json.dumps(
            sanitize_for_persistence({"tool_call_id": message.tool_call_id, "name": message.name}),
            ensure_ascii=False,
        )
    return None


def _content_for_storage(message: Message) -> str | None:
    if message.content is None:
        return None
    if message.role != Role.TOOL:
        return sanitize_for_persistence(message.content)
    payload = parse_tool_payload(message.content)
    if payload is None:
        return "[REDACTED TOOL OUTPUT]"
    # Tool observations may contain credentials without a recognizable key name.
    # Keep success/failure context on resume, not raw output captured from host.
    if "data" in payload:
        payload["data"] = "[REDACTED]"
    if payload.get("error"):
        payload["error"] = "Tool failed."
    redacted_payload = sanitize_for_persistence(redact_audit_data(payload))
    return replace_tool_payload(message.content, redacted_payload)


def append_message(db_path: str | Path, session_id: str, message: Message) -> MessageRecord:
    record = MessageRecord(
        id=str(uuid.uuid4()),
        session_id=session_id,
        role=message.role.value,
        content=_content_for_storage(message),
        tool_calls_json=_tool_calls_json_for(message),
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, tool_calls_json, created_at, schema_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.session_id,
                record.role,
                record.content,
                record.tool_calls_json,
                record.created_at,
                record.schema_version,
            ),
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (record.created_at, session_id),
        )
        conn.commit()
    finally:
        conn.close()

    return record


def load_session(db_path: str | Path, session_id: str) -> list[Message]:
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT role, content, tool_calls_json FROM messages WHERE session_id = ? ORDER BY rowid",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    messages: list[Message] = []
    for row in rows:
        role = Role(row["role"])
        raw_meta = json.loads(row["tool_calls_json"]) if row["tool_calls_json"] else None

        if role == Role.ASSISTANT and raw_meta:
            tool_calls = [ToolCall(id=c["id"], name=c["name"], arguments=c["arguments"]) for c in raw_meta]
            messages.append(Message(role=role, content=row["content"], tool_calls=tool_calls))
        elif role == Role.TOOL and raw_meta:
            messages.append(
                Message(
                    role=role,
                    content=row["content"],
                    tool_call_id=raw_meta.get("tool_call_id"),
                    name=raw_meta.get("name"),
                )
            )
        else:
            messages.append(Message(role=role, content=row["content"]))

    return messages


def _row_to_session_record(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        id=row["id"],
        title=row["title"],
        distro_at_creation=row["distro_at_creation"],
        distro_version_at_creation=row["distro_version_at_creation"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        schema_version=row["schema_version"],
    )


def list_sessions(db_path: str | Path, limit: int | None = None) -> list[SessionRecord]:
    """Liệt kê session, mới cập nhật nhất lên đầu (CLI.md mục 4.3: "resume-last"
    xác định bằng updated_at, không phải created_at). `limit=None` (mặc định) là
    toàn bộ — CLI dựa vào đó; chỉ GUI chủ động truyền giới hạn."""
    if limit is not None and limit < 1:
        # SQLite coi LIMIT âm là "không giới hạn" — chặn để giá trị sai không âm thầm vô hiệu giới hạn.
        raise ValueError("limit phải là số nguyên dương hoặc None.")
    init_db(db_path)

    query = (
        "SELECT id, title, distro_at_creation, distro_version_at_creation, "
        "created_at, updated_at, schema_version FROM sessions ORDER BY updated_at DESC"
    )
    params: tuple[int, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)

    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    return [_row_to_session_record(row) for row in rows]


def get_last_session_id(db_path: str | Path) -> str | None:
    """Id của session được cập nhật gần nhất, dùng cho `agent chat --resume-last`.
    Trả về None nếu chưa có session nào — command gọi vào tự báo lỗi rõ ràng,
    không crash."""
    sessions = list_sessions(db_path)
    return sessions[0].id if sessions else None


def generate_title(provider: LLMProvider, first_user_message: str) -> str:
    try:
        response = provider.chat(
            [
                Message(
                    role=Role.SYSTEM,
                    content=(
                        "Tóm tắt yêu cầu sau thành một tiêu đề ngắn gọn (dưới 8 từ), "
                        "tiếng Việt, không dùng dấu ngoặc kép."
                    ),
                ),
                Message(role=Role.USER, content=first_user_message),
            ],
            max_tokens=32,
        )
        title = (response.content or "").strip()
        if title:
            return title
    except Exception:
        pass

    fallback = first_user_message.strip()[:_FALLBACK_TITLE_LENGTH]
    return fallback or "Cuộc trò chuyện mới"
