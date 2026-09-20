"""Kiểu dữ liệu chung cho tool: tier cố định lúc khai báo và kết quả thực thi.

Tier gắn cố định ở đây, không bao giờ suy luận từ nội dung lệnh lúc runtime
(nguyên tắc an toàn #1). Tier 3 (phá hủy, không thể hoàn tác) cố tình không có
enum tương ứng — không tồn tại tool nào ở tier đó (nguyên tắc an toàn #2).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Tier(str, Enum):
    TIER_1_READONLY = "tier_1_readonly"
    TIER_2_ACTION = "tier_2_action"


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None
