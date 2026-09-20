"""AuditController (gui_implementation_plan.md Phase 8) — sở hữu QThread +
`AuditTailWorker`, một lần refresh tại một thời điểm — cùng khuôn mẫu
`SystemController` (Phase 4), vì `audit_service.tail_file()` đọc cả file
JSONL (có thể lớn) nên chạy nền thay vì đồng bộ như `SessionsController`
(Phase 6, chỉ 1 câu SQL nhỏ).

`shutdown()` theo đúng duck-typing mà `MainWindow.closeEvent()` đã dựng ở
Phase 4: page nào có method `shutdown()` thì được gọi trước khi đóng, không
cần sửa gì thêm ở `MainWindow`.

Follow-up sau lượt vá System/Settings: `MainWindow.closeEvent()` giờ đưa
Audit vào `_is_any_worker_busy()` qua `AuditPage.controller()` (cùng idiom
`SystemController`/`SettingsController`) — `shutdown()` dưới đây không còn
là đường chờ chính khi worker đang chạy dở nữa, xem docstring của nó.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from agent import paths
from agent.gui.workers.audit_worker import AuditTailWorker

_SHUTDOWN_WAIT_MS = 6000


class AuditController(QObject):
    loading_changed = Signal(bool)
    tail_loaded = Signal(object)  # AuditTailResult
    tail_failed = Signal(str)

    def __init__(self, log_path: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._log_path = log_path if log_path is not None else paths.audit_log_path()
        self._thread: QThread | None = None
        self._worker: AuditTailWorker | None = None
        self._retired: list[tuple[QThread, QObject]] = []

    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def shutdown(self, timeout_ms: int = _SHUTDOWN_WAIT_MS) -> None:
        """Fast, bounded drain — `MainWindow.closeEvent()` only ever calls
        this on the NOT-busy path (`AuditPage.is_busy()` already false).

        Measured directly (not assumed, same method used for
        `SystemController`/`SettingsController`): closing `MainWindow` while
        Audit's tail read was genuinely still running (AuditPage
        auto-triggers `refresh()` on construction, same as System's scan)
        blocked `MainWindow.close()` for exactly `_SHUTDOWN_WAIT_MS` (6.001s
        measured), with the same "QThread: Destroyed while thread is still
        running" abort risk afterward if the worker still hadn't finished.
        `MainWindow.closeEvent()` now avoids this by never calling
        `shutdown()` while `AuditPage.is_busy()` is true — it ignores the
        close and retries once `loading_changed(False)` (exposed via
        `AuditPage.controller()`) confirms the tail read is actually done,
        the same cooperative shape as `MainWindow.closeEvent()`'s chat/
        System/Settings handling (Phase 17 + follow-up)."""
        threads = [thread for thread, _worker in self._retired]
        if self._thread is not None:
            threads.append(self._thread)
        for thread in threads:
            try:
                thread.quit()
                thread.wait(timeout_ms)
            except RuntimeError:
                pass
        QCoreApplication.processEvents()

    def refresh(self, *, count: int) -> None:
        if self.is_busy():
            return

        thread = QThread(self)
        worker = AuditTailWorker(self._log_path, count)
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
        self.loading_changed.emit(True)
        thread.start()

    def _on_finished(self, result: object) -> None:
        self._retire_current()
        self.loading_changed.emit(False)
        self.tail_loaded.emit(result)

    def _on_failed(self, message: str) -> None:
        self._retire_current()
        self.loading_changed.emit(False)
        self.tail_failed.emit(message)

    def _retire_current(self) -> None:
        if self._thread is not None and self._worker is not None:
            self._retired.append((self._thread, self._worker))
        self._thread = None
        self._worker = None

    def _on_thread_finished(self) -> None:
        finished = self.sender()
        self._retired = [(thread, worker) for thread, worker in self._retired if thread is not finished]
