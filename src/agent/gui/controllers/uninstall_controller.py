"""UninstallController — sở hữu QThread + `UninstallWorker` (một lần tại một thời điểm),
cùng khuôn mẫu `AuditController`/`SettingsController`, kể cả vòng đời an toàn:
thread + worker được giữ tới `QThread.finished` (worker không có parent nên chỉ Python
sở hữu C++ object — bỏ tham chiếu sớm sẽ huỷ nó từ UI thread khi worker thread còn ở
cuối `emit()`).

Chỉ gọi `uninstall_service` (dùng chung với CLI); không đăng ký gì vào tool registry.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from agent.core import uninstall_service
from agent.gui.workers.uninstall_worker import UninstallWorker

_SHUTDOWN_WAIT_MS = 6000


class UninstallController(QObject):
    loading_changed = Signal(bool)
    finished = Signal(object)  # UninstallOutcome
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: UninstallWorker | None = None
        self._retired: list[tuple[QThread, QObject]] = []

    def plan(self) -> uninstall_service.UninstallPlan:
        return uninstall_service.plan_uninstall()

    def manual_commands(self) -> tuple[tuple[str, str], ...]:
        return uninstall_service.MANUAL_REMOVAL_COMMANDS

    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def shutdown(self, timeout_ms: int = _SHUTDOWN_WAIT_MS) -> None:
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

    def start(self) -> None:
        if self._thread is not None:
            return

        thread = QThread(self)
        worker = UninstallWorker()
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

    def spawn_removal(self, installer: uninstall_service.Installer) -> str | None:
        """Chạy lệnh gỡ package (tiến trình tách rời). Trả None nếu ổn, hoặc mô tả lỗi."""
        try:
            uninstall_service.spawn_package_removal(installer)
        except OSError as exc:
            return f"{type(exc).__name__}: {exc.strerror or exc}"
        return None

    def _retire_current(self) -> None:
        if self._thread is not None and self._worker is not None:
            self._retired.append((self._thread, self._worker))
        self._thread = None
        self._worker = None

    def _on_finished(self, outcome: object) -> None:
        self._retire_current()
        self.loading_changed.emit(False)
        self.finished.emit(outcome)

    def _on_failed(self, status: str) -> None:
        self._retire_current()
        self.loading_changed.emit(False)
        self.failed.emit(status)

    def _on_thread_finished(self) -> None:
        finished = self.sender()
        self._retired = [(thread, worker) for thread, worker in self._retired if thread is not finished]
