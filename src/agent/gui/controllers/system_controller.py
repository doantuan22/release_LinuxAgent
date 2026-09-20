"""SystemController (gui_implementation_plan.md Phase 4) — sở hữu QThread +
`SystemScanWorker`, một lần refresh tại một thời điểm. `SystemPage` chỉ gọi
`refresh()`/nối signal, không tự tạo QThread hay import module quét (Luồng
kiến trúc: "SystemPage → SystemController → QThread worker → system.profile
→ queued result → SystemPage").
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from agent.gui.workers.system_worker import SystemScanWorker

# systemd-detect-virt (agent/system/virt_detect.py) tự timeout ở 5s — chờ tới
# khi widget bị huỷ phải bao trọn mốc đó, cộng biên an toàn.
_SHUTDOWN_WAIT_MS = 6000


class SystemController(QObject):
    loading_changed = Signal(bool)
    profile_loaded = Signal(list)
    profile_failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: SystemScanWorker | None = None
        self._retired: list[tuple[QThread, QObject]] = []

    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def shutdown(self, timeout_ms: int = _SHUTDOWN_WAIT_MS) -> None:
        """Fast, bounded drain — `MainWindow.closeEvent()` only ever calls
        this on the NOT-busy path (`is_busy()` already false), so `wait()`
        returns immediately; it just flushes any already-queued
        `deleteLater()` cleanup from a worker that JUST finished.

        It is NOT how a genuinely in-flight scan is waited out anymore.
        Measured directly (not assumed): `QThread.wait(timeout_ms)` blocks
        the CALLING thread's event loop for the real duration — with System
        page's scan auto-started on construction, closing `MainWindow`
        while it was still running blocked `MainWindow.close()` for exactly
        `_SHUTDOWN_WAIT_MS` (6.003s measured) before it even checked whether
        the worker was done, and destroying the widget tree afterward risks
        Qt's fatal "QThread: Destroyed while thread is still running" abort
        if the worker still hadn't finished. `MainWindow.closeEvent()` now
        avoids both by never calling this while `SystemPage.is_busy()` is
        true — it ignores the close and retries once `loading_changed(False)`
        (exposed via `SystemPage.controller()`) confirms the scan is
        actually done, the same cooperative shape as `MainWindow.
        closeEvent()`'s own chat-turn handling (Phase 17)."""
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

    def refresh(self, *, force_rescan: bool = False) -> None:
        """Bỏ qua yêu cầu mới nếu một lần quét đang chạy dở, thay vì xếp hàng
        hoặc chạy chồng nhiều worker cùng lúc trên cùng page."""
        if self.is_busy():
            return

        thread = QThread(self)
        worker = SystemScanWorker(force_rescan=force_rescan)
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

    def _on_finished(self, rows: list[tuple[str, str]]) -> None:
        self._retire_current()
        self.loading_changed.emit(False)
        self.profile_loaded.emit(rows)

    def _on_failed(self, message: str) -> None:
        self._retire_current()
        self.loading_changed.emit(False)
        self.profile_failed.emit(message)

    def _retire_current(self) -> None:
        if self._thread is not None and self._worker is not None:
            self._retired.append((self._thread, self._worker))
        self._thread = None
        self._worker = None

    def _on_thread_finished(self) -> None:
        finished = self.sender()
        self._retired = [(thread, worker) for thread, worker in self._retired if thread is not finished]
