"""Workers cho SessionsController — QObject dùng một lần, chạy trong QThread riêng
(cùng khuôn mẫu `AuditTailWorker`/`SystemScanWorker`) để SQLite + redaction không
chạy trên UI thread.

Đo thật (không đoán): SQL chỉ tốn ~10 ms cho 5000 session, nhưng redact từng
record tốn ~3.6 s và format+redact một history ~10 MB tốn ~1 s — phần CPU này
mới là thứ làm đứng UI, nên cả hai nằm trong worker.

Worker chỉ phát dữ liệu đã render-an-toàn và mã lỗi cố định ("sessions_failed"/
"detail_failed") — không bao giờ phát text của exception, có thể chứa secret.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from agent.core.redaction import sanitize_for_persistence
from agent.llm.base import Message, Role
from agent.services import session_service

_EMPTY_HISTORY_TEXT = "(Chưa có nội dung.)"

# GUI là nơi DUY NHẤT giới hạn danh sách; CLI `agent sessions list` gọi service không
# truyền limit nên vẫn liệt kê đầy đủ.
SESSION_LIST_LIMIT = 500


def format_history(messages: list[Message]) -> str:
    lines: list[str] = []
    for message in messages:
        if message.role == Role.SYSTEM:
            continue
        if message.role == Role.TOOL:
            lines.append(f"TOOL {message.name or ''}".rstrip())
            lines.append("")
            continue
        if not message.content:
            continue
        lines.append(message.role.value.upper())
        lines.append(message.content)
        lines.append("")

    text = "\n".join(lines).rstrip("\n")
    return text or _EMPTY_HISTORY_TEXT


class SessionListWorker(QObject):
    finished = Signal(object)  # (list[SessionRecord], truncated: bool)
    failed = Signal(str)

    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self._db_path = db_path

    def run(self) -> None:
        try:
            # Xin thêm 1 dòng trong cùng một truy vấn: nhận được LIMIT+1 dòng nghĩa là còn
            # session cũ hơn bị cắt (không cần COUNT riêng, không lệch snapshot).
            records = session_service.list_all_sessions(self._db_path, SESSION_LIST_LIMIT + 1)
            truncated = len(records) > SESSION_LIST_LIMIT
            records = records[:SESSION_LIST_LIMIT]
            # Presentation copies only: retain exact IDs for selection/resume.
            records = [replace(
                record,
                title=sanitize_for_persistence(record.title),
                distro_at_creation=sanitize_for_persistence(record.distro_at_creation),
                distro_version_at_creation=sanitize_for_persistence(record.distro_version_at_creation),
                created_at=sanitize_for_persistence(record.created_at),
                updated_at=sanitize_for_persistence(record.updated_at),
            ) for record in records]
        except Exception:
            # Contain malformed persisted data at the Qt boundary; never emit
            # exception text, which may include secrets or database contents.
            self.failed.emit("sessions_failed")
            return
        self.finished.emit((records, truncated))


class SessionDetailWorker(QObject):
    finished = Signal(str)  # text đã redact
    failed = Signal(str)

    def __init__(self, db_path: Path, session_id: str) -> None:
        super().__init__()
        self._db_path = db_path
        self._session_id = session_id

    def run(self) -> None:
        try:
            messages = session_service.load_session_messages(self._db_path, self._session_id)
            # Legacy data and newly configured secrets require read-time
            # redaction; never rewrite the service objects or stored history.
            text = sanitize_for_persistence(format_history(messages))
        except Exception:
            self.failed.emit("detail_failed")
            return
        self.finished.emit(text)
