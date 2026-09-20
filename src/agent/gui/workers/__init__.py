"""Worker QObject chạy trong QThread riêng (gui_implementation_plan.md — "Thread
contract cho mọi phase bất đồng bộ"). Mỗi worker chỉ gọi service/core và phát
signal dữ liệu đã render-an-toàn; không tự tạo/sửa widget UI.
"""

from __future__ import annotations
