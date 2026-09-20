"""SessionsController (gui_implementation_plan.md Phase 6) — gọi
`session_service` (list/load), không tự đọc SQLite và **không bao giờ** gọi
`create_new_session` (bất biến ghi ở docstring của hàm đó: chỉ tạo session
lúc New chat/gửi tin nhắn đầu tiên — mở Sessions page chỉ đọc).

Phase 6 ban đầu gọi đồng bộ trên UI thread vì tưởng chỉ là một câu SQL nhỏ. Đo
thật cho thấy SQL rẻ nhưng redaction từng record/history mới là phần CPU nặng
(~3.6 s cho 5000 session, ~1 s cho một history ~10 MB) — nên nay `refresh()`/
`load_detail()` chạy trong QThread + worker, cùng khuôn mẫu `AuditController`.
Signal contract của Phase 6 giữ nguyên nên `SessionsPage` chỉ cần thêm hook busy.

Một thread tại một thời điểm. Khác `AuditController` (bỏ qua nếu đang bận): ở đây
"click dòng nào cuối cùng thì hiện history dòng đó" là hành vi đã có khi đồng bộ,
nên request đến lúc đang bận được nhớ lại (mới nhất thắng) thay vì bị bỏ, và kết
quả history cũ bị bỏ nếu đã có request history mới hơn đang chờ.

Format lịch sử hội thoại thành text thuần ở worker (không phải ở page) —
cùng nguyên tắc Phase 4: worker/controller phát dữ liệu đã render-an-toàn,
page chỉ nhận và hiển thị, không tự chạm `agent.llm`/`agent.memory`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from agent import paths
from agent.gui.workers.sessions_worker import SESSION_LIST_LIMIT, SessionDetailWorker, SessionListWorker

_SHUTDOWN_WAIT_MS = 6000

_LIST = "list"
_DETAIL = "detail"


class SessionsController(QObject):
    loading_changed = Signal(bool)
    sessions_loaded = Signal(list)
    sessions_truncated = Signal(int)  # số session gần nhất đang hiển thị; chỉ phát khi có session bị cắt
    sessions_failed = Signal(str)
    detail_loaded = Signal(str)
    detail_failed = Signal(str)

    def __init__(self, db_path: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._db_path = db_path if db_path is not None else paths.sessions_db()
        self._thread: QThread | None = None
        self._worker: SessionListWorker | SessionDetailWorker | None = None
        self._running_kind: str | None = None
        # Thread+worker đã trả kết quả nhưng QThread chưa thoát hẳn: worker (không có parent)
        # chỉ được Python sở hữu, nên bỏ tham chiếu sớm sẽ huỷ nó từ UI thread trong khi thread
        # còn đang ở cuối emit() -> use-after-free. Giữ tới khi QThread.finished.
        self._retired: list[tuple[QThread, QObject]] = []
        self._loading = False
        self._pending_refresh = False
        self._pending_detail: str | None = None

    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def shutdown(self, timeout_ms: int = _SHUTDOWN_WAIT_MS) -> None:
        """Bounded drain, cùng ý nghĩa `AuditController.shutdown()`:
        `MainWindow.closeEvent()` chỉ gọi khi page không busy."""
        self._pending_refresh = False
        self._pending_detail = None
        threads = [thread for thread, _worker in self._retired]
        if self._thread is not None:
            threads.append(self._thread)
        for thread in threads:
            try:
                thread.quit()
                thread.wait(timeout_ms)
            except RuntimeError:  # C++ QThread đã bị deleteLater — coi như đã thoát
                pass
        QCoreApplication.processEvents()

    def refresh(self) -> None:
        if self._thread is not None:
            self._pending_refresh = True
            return
        self._start(_LIST, SessionListWorker(self._db_path))

    def load_detail(self, session_id: str) -> None:
        if self._thread is not None:
            self._pending_detail = session_id  # mới nhất thắng
            return
        self._start(_DETAIL, SessionDetailWorker(self._db_path, session_id))

    def _start(self, kind: str, worker: SessionListWorker | SessionDetailWorker) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)

        self._thread = thread
        self._worker = worker
        self._running_kind = kind
        if not self._loading:
            self._loading = True
            self.loading_changed.emit(True)
        thread.start()

    def _finish_current(self) -> str | None:
        kind = self._running_kind
        if self._thread is not None and self._worker is not None:
            self._retired.append((self._thread, self._worker))
        self._thread = None
        self._worker = None
        self._running_kind = None
        return kind

    def _on_thread_finished(self) -> None:
        finished = self.sender()
        self._retired = [(t, w) for t, w in self._retired if t is not finished]

    def _on_finished(self, result: object) -> None:
        kind = self._finish_current()
        if kind == _LIST:
            records, truncated = result  # type: ignore[misc]
            self.sessions_loaded.emit(records)
            if truncated:
                self.sessions_truncated.emit(SESSION_LIST_LIMIT)
        elif self._pending_detail is None:
            self.detail_loaded.emit(result)  # type: ignore[arg-type]
        self._run_pending_or_idle()

    def _on_failed(self, message: str) -> None:
        kind = self._finish_current()
        if kind == _LIST:
            self.sessions_failed.emit(message)
        elif self._pending_detail is None:
            self.detail_failed.emit(message)
        self._run_pending_or_idle()

    def _run_pending_or_idle(self) -> None:
        # Slot của loaded/failed có thể đã tự bắt đầu request mới (vd đổi
        # selection) — khi đó không được chạy thêm một thread nữa.
        if self._thread is not None:
            return
        if self._pending_detail is not None:
            session_id, self._pending_detail = self._pending_detail, None
            self._start(_DETAIL, SessionDetailWorker(self._db_path, session_id))
        elif self._pending_refresh:
            self._pending_refresh = False
            self._start(_LIST, SessionListWorker(self._db_path))
        else:
            self._loading = False
            self.loading_changed.emit(False)
