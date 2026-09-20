"""ConfirmationRequest: dữ liệu đầy đủ truyền cho confirm_callback khi ToolExecutor
gặp một Tier 2 tool cần xác nhận (CLI.md mục 7).

Tách riêng khỏi confirm_policies.py để executor.py chỉ phụ thuộc vào kiểu dữ liệu
này, không phụ thuộc ngược vào các policy cụ thể (interactive_confirm/
always_deny_confirm) — CLI/GUI sau này dùng cùng dataclass để render xác nhận mà
không cần đọc lại registry ở nơi khác.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.tools.schemas import Tier


@dataclass
class ConfirmationRequest:
    tool_name: str
    tier: Tier
    arguments: dict[str, Any]
    description: str
    risk: str | None = None
    # Optional for CLI/backward-compatible direct callers. The agent loop
    # supplies this opaque, already-safe correlation ID for GUI binding.
    call_id: str | None = None
