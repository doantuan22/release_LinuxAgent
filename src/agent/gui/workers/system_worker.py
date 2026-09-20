"""SystemScanWorker (gui_implementation_plan.md Phase 4) — QObject chạy trong
một QThread riêng, gọi `system.profile.scan_system()`/`to_display_summary()`
và phát lại kết quả đã redact qua signal. UI thread chỉ nhận và render
(Thread contract ở đầu gui_implementation_plan.md: worker không tự tạo/sửa
widget, chỉ phát dữ liệu render-an-toàn).

`scan_system()` dùng `lru_cache(maxsize=1)` vì toàn bộ trường của
`SystemProfile` là tĩnh/bán tĩnh trong vòng đời tiến trình (xem docstring
`agent/system/profile.py`) — nhưng vì vậy Rescan phải tự
`scan_system.cache_clear()` trước khi gọi lại, nếu không sẽ luôn trả kết quả
lần quét đầu tiên trong cùng process (mục 0 dòng 4 của
gui_implementation_plan.md). Dữ liệu profile không phải bí mật, nhưng vẫn
redact như `commands/profile.py` đã làm để nhất quán quy tắc "không CLI/GUI
rendering path nào lộ secret".
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from agent.core.redaction import redact_known_secrets
from agent.system.profile import scan_system, to_display_summary


class SystemScanWorker(QObject):
    """Dùng một lần: `SystemController` tạo worker + thread mới cho mỗi lần
    refresh thay vì tái sử dụng, để không phải reset state giữa các lần chạy."""

    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, *, force_rescan: bool) -> None:
        super().__init__()
        self._force_rescan = force_rescan

    def run(self) -> None:
        try:
            if self._force_rescan:
                scan_system.cache_clear()
            profile = scan_system()
            rows = [(label, redact_known_secrets(value)) for label, value in to_display_summary(profile)]
        except Exception:  # quan sát lỗi quét, không để exception thoát khỏi worker thread
            # Phase 20: không phát str(exc) qua signal — text exception có thể
            # chứa dữ liệu môi trường/secret; UI chỉ cần status cố định.
            self.failed.emit("scan_failed")
            return
        self.finished.emit(rows)
