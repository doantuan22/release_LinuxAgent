"""AuditTailWorker (gui_implementation_plan.md Phase 8) — QObject chạy trong
QThread riêng, gọi `audit_service.tail_file()` (đã tự quản vòng đời file, trả
`AuditTailResult` an toàn — status/entries/malformed_count, không bao giờ
raise) và phát lại nguyên vẹn qua signal. `AuditTailResult`/`AuditRecord` đã
chỉ chứa field được phép hiển thị (Phase 7) nên an toàn để đi thẳng qua
signal tới UI thread, không cần xử lý thêm ở đây.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from agent.services import audit_service


class AuditTailWorker(QObject):
    """Dùng một lần: `AuditController` tạo worker + thread mới cho mỗi lần
    refresh, giống `SystemScanWorker` (Phase 4)."""

    finished = Signal(object)  # AuditTailResult
    failed = Signal(str)

    def __init__(self, log_path: Path, count: int) -> None:
        super().__init__()
        self._log_path = log_path
        self._count = count

    def run(self) -> None:
        try:
            result = audit_service.tail_file(self._log_path, self._count)
        except Exception:  # tail_file() không tự raise, nhưng vẫn phòng thủ như Phase 4
            # Phase 20: status cố định thay vì str(exc) — exception text có thể
            # chứa đường dẫn/nội dung file audit, không phát qua signal tới UI.
            self.failed.emit("audit_failed")
            return
        self.finished.emit(result)
