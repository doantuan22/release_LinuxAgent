"""Application service bọc memory/session_store cho commands/sessions.py,
commands/chat.py (--resume/--resume-last/tạo session mới) và GUI (Phase 6
Sessions page, Phase 14 Chat thật) — cả hai chỉ nói chuyện với service này,
không tự import `memory.session_store` (Luồng kiến trúc gui_implementation_plan.md
Phase 5: "GUI/CLI → session_service → session_store(SQLite XDG)").
"""

from __future__ import annotations

from pathlib import Path

from agent.llm.base import Message
from agent.memory.schemas import SessionRecord
from agent.memory.session_store import create_session, get_last_session_id, list_sessions, load_session


class AmbiguousSessionIdError(ValueError):
    """ID rút gọn khớp nhiều session; người dùng cần nhập thêm ký tự."""


def list_all_sessions(db_path: Path, limit: int | None = None) -> list[SessionRecord]:
    return list_sessions(db_path, limit)


def create_new_session(db_path: Path) -> SessionRecord:
    """Tạo một session mới thật (ghi record vào DB ngay) qua `session_store`
    hiện có — không đổi schema/migration.

    Bất biến bắt buộc cho mọi caller (CLI `agent chat` không --resume, và sau
    này GUI Phase 6/14): chỉ gọi hàm này đúng lúc người dùng thật sự bắt đầu
    một cuộc hội thoại mới (CLI: không có session nào để resume; GUI: bấm
    "New chat" hoặc gửi tin nhắn đầu tiên). KHÔNG gọi hàm này chỉ vì một
    page/window được mở — mở Sessions page hay Chat page không tự tạo session
    rỗng nào. Vi phạm bất biến này sẽ để lại session trống vô nghĩa trong
    danh sách session của người dùng.
    """
    return create_session(db_path)


def find_session_by_id_prefix(db_path: Path, id_prefix: str) -> SessionRecord | None:
    """Tìm đúng 1 session có id bắt đầu bằng `id_prefix` — CLI.md mục 4.2/9 chỉ
    hiển thị và yêu cầu người dùng gõ id rút gọn 8 ký tự, không phải UUID đầy đủ.
    Trả về None nếu không khớp; raise AmbiguousSessionIdError nếu khớp nhiều."""
    if not id_prefix or not id_prefix.strip():
        return None
    matches = [s for s in list_sessions(db_path) if s.id.startswith(id_prefix)]
    if len(matches) > 1:
        raise AmbiguousSessionIdError("Session ID không duy nhất; hãy nhập thêm ký tự.")
    return matches[0] if matches else None


def get_last_session(db_path: Path) -> str | None:
    return get_last_session_id(db_path)


def load_session_messages(db_path: Path, session_id: str) -> list[Message]:
    return load_session(db_path, session_id)
