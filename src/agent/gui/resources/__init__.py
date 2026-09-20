"""Resource package cho GUI (hiện chỉ SVG icon — xem `icon_manager.py`).

Không dùng lại `agent.core.resource_loader`: module đó là allowlist cố định
cho resource backend copy-once ra thư mục XDG (providers.json, default.db...).
Icon GUI chỉ đọc trực tiếp từ package lúc render (không copy ra đĩa người
dùng), nên có access pattern khác và không thuộc allowlist đó.
"""

from __future__ import annotations
