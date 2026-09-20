"""Dataclass mô tả các bảng lưu trữ persistent của agent (Giai đoạn 4: session +
user memory).

schema_version có mặt trên MỌI bảng ngay từ đầu (nguyên tắc an toàn #7), dù MVP
chưa cần dùng tới — cho phép phát hiện lệch phiên bản rõ ràng và raise lỗi thay vì
tự động migrate ngầm khi cấu trúc bảng đổi ở giai đoạn sau.

Schema được thiết kế để Giai đoạn 5 (RAG) chỉ cần thêm cột embedding vào bảng
memory + tầng Reciprocal Rank Fusion, không cần đổi cấu trúc 3 bảng này.

SCHEMA_VERSION và SESSION_SCHEMA_VERSION tách riêng (Phase 4 — CLI): `sessions`/
`messages` (session_store.py) đổi cấu trúc (thêm updated_at/distro_version_at_creation)
nên cần version riêng; `memory` (user_memory.py) và `usage_log` (core/cost_tracker.py)
chưa đổi gì nên vẫn dùng SCHEMA_VERSION cũ — dùng chung một hằng số cho cả 3 bảng
không liên quan sẽ khiến bump version của session vô tình làm memory.db/usage_log
bị coi là lệch schema dù cấu trúc của chúng không đổi.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

SCHEMA_VERSION = 1
SESSION_SCHEMA_VERSION = 2


@dataclass
class SessionRecord:
    id: str
    title: str
    distro_at_creation: str
    distro_version_at_creation: str
    created_at: str
    updated_at: str
    schema_version: int = SESSION_SCHEMA_VERSION


@dataclass
class MessageRecord:
    id: str
    session_id: str
    role: str
    content: str | None
    tool_calls_json: str | None
    created_at: str
    schema_version: int = SESSION_SCHEMA_VERSION


@dataclass
class MemoryEntry:
    id: str
    kind: Literal["episodic", "preference"]
    content: str
    created_at: str
    schema_version: int = SCHEMA_VERSION


def check_schema_version(
    conn: sqlite3.Connection, table: str, expected_version: int = SCHEMA_VERSION
) -> None:
    """Raise rõ ràng nếu bảng có dữ liệu ở schema_version khác `expected_version`.

    KHÔNG tự động migrate ngầm — nguyên tắc an toàn: lệch schema phải được xử lý
    tường minh bởi người vận hành. Dự án chưa có người dùng thật nên cách xử lý
    đúng khi gặp lỗi này là xóa file DB cũ chứa bảng đó rồi chạy lại để tạo mới
    (không tự ý migrate ngầm dữ liệu cũ), trừ khi thật sự cần giữ dữ liệu thì viết
    migration rõ ràng riêng.
    """
    rows = conn.execute(f"SELECT DISTINCT schema_version FROM {table}").fetchall()
    unexpected = sorted({row[0] for row in rows} - {expected_version})
    if unexpected:
        raise RuntimeError(
            f"Bảng '{table}' chứa dữ liệu ở schema_version {unexpected}, khác với "
            f"phiên bản code hiện tại (schema_version={expected_version}). Không tự "
            f"động migrate ngầm — hãy xóa file DB cũ chứa bảng '{table}' rồi chạy lại "
            f"để tạo mới, hoặc viết migration rõ ràng nếu cần giữ dữ liệu cũ."
        )
