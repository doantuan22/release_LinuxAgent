"""OnboardingController (gui_implementation_plan.md Phase 18) — orchestrates
first-run onboarding: Welcome -> System scan -> Choose provider -> Safety.

Every side effect this controller triggers is an EXISTING call already used
elsewhere, reused as-is:

- System scan (step 2) runs `SystemScanWorker` on its own `QThread`, wired
  exactly like `SystemController.refresh()` (Phase 4) — same worker class,
  same signal/thread lifecycle, so Rescan-equivalent behaviour (including the
  redaction `SystemScanWorker` already applies) is identical to the System
  page.
- Provider test connection (step 3) runs `ProviderTestWorker` on its own
  `QThread`, wired exactly like `SettingsController.test_connection()` (Phase
  10) — same worker class, same bounded 10s network call via
  `config_service.test_connection()`.
- Saving + activating the chosen provider calls `config_service.
  add_provider_from_preset()` then `config_service.set_active_provider()`
  directly — the same two functions `SettingsController` calls, and the
  module `config_service` names Onboarding as an intended direct caller of
  (see its module docstring: "cho commands/config.py ... và GUI Settings/
  Onboarding"). These two calls are synchronous local JSON/.env read-write
  (no network), so — same as `SettingsController.save_new_provider()`/
  `set_active()` — no `QThread` is needed for them.

No new business logic is introduced here: this controller only sequences
existing calls for the onboarding flow and owns the two bounded QThreads
onboarding needs (scan, test connection).

Completion marker: `paths.onboarding_marker_file()` is written ONLY by
`mark_complete()`. The view calls it exactly once, after the user clicks
"Got it, let's go" on the last step — never on back, error, or app close.
Per the resolved scope decision for this phase, there is no per-step resume
state: an onboarding left incomplete always restarts at Welcome on the next
launch (see `onboarding_view.py`).
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from agent import paths
from agent.core.provider_presets import ProviderPreset
from agent.gui.workers.provider_worker import ProviderTestWorker
from agent.gui.workers.system_worker import SystemScanWorker
from agent.services import config_service

# Same bound as SystemScanWorker's own systemd-detect-virt timeout (Phase 4:
# `SystemController._SHUTDOWN_WAIT_MS`) and ProviderTestWorker's network call
# bound (Phase 10: `SettingsController._SHUTDOWN_WAIT_MS`) — the exact same
# worker classes, so the exact same shutdown bounds apply here.
_SCAN_SHUTDOWN_WAIT_MS = 6000
_TEST_SHUTDOWN_WAIT_MS = 12000


class OnboardingController(QObject):
    scan_loading_changed = Signal(bool)
    scan_finished = Signal(list)  # list[tuple[str, str]], same shape as SystemController.profile_loaded
    scan_failed = Signal(str)

    test_loading_changed = Signal(bool)
    test_finished = Signal(object)  # config_service.ConnectionTestResult

    provider_saved = Signal(str)
    provider_save_failed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scan_thread: QThread | None = None
        self._scan_worker: SystemScanWorker | None = None
        self._test_thread: QThread | None = None
        self._test_worker: ProviderTestWorker | None = None
        self._retired: list[tuple[QThread, QObject]] = []

    # --- completion marker ---

    @staticmethod
    def is_complete() -> bool:
        return paths.onboarding_marker_file().exists()

    @staticmethod
    def mark_complete() -> None:
        paths.onboarding_marker_file().touch()

    # --- system scan (step 2) ---

    def is_scan_busy(self) -> bool:
        return self._scan_thread is not None and self._scan_thread.isRunning()

    def start_system_scan(self) -> None:
        """Ignore a request while one is already running instead of queueing
        or running a second worker concurrently — same guard as
        `SystemController.refresh()`."""
        if self.is_scan_busy():
            return

        thread = QThread(self)
        worker = SystemScanWorker(force_rescan=False)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)

        self._scan_thread = thread
        self._scan_worker = worker
        self.scan_loading_changed.emit(True)
        thread.start()

    def _on_scan_finished(self, rows: list[tuple[str, str]]) -> None:
        self._retire_scan()
        self.scan_loading_changed.emit(False)
        self.scan_finished.emit(rows)

    def _on_scan_failed(self, message: str) -> None:
        self._retire_scan()
        self.scan_loading_changed.emit(False)
        self.scan_failed.emit(message)

    # --- provider test connection (step 3) ---

    def is_test_busy(self) -> bool:
        return self._test_thread is not None and self._test_thread.isRunning()

    def test_connection(
        self, provider_type: str, *, model: str, api_key: str | None, base_url: str | None
    ) -> None:
        if self.is_test_busy():
            return

        thread = QThread(self)
        worker = ProviderTestWorker(provider_type, model=model, api_key=api_key, base_url=base_url)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_test_finished)
        worker.failed.connect(self._on_test_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)

        self._test_thread = thread
        self._test_worker = worker
        self.test_loading_changed.emit(True)
        thread.start()

    def _on_test_finished(self, result: object) -> None:
        self._retire_test()
        self.test_loading_changed.emit(False)
        self.test_finished.emit(result)

    def _on_test_failed(self, _status: str) -> None:
        self._retire_test()
        self.test_loading_changed.emit(False)
        # Same unmodeled-failure fallback as SettingsController._on_test_failed.
        self.test_finished.emit(
            config_service.ConnectionTestResult(status="unknown_failure", message="Provider request failed.")
        )

    # --- save + activate chosen provider (step 3 Continue) ---

    def save_provider(
        self, preset: ProviderPreset, *, api_key: str | None, model: str, base_url: str | None
    ) -> None:
        """Persist the chosen provider AND make it active in one step — unlike
        Settings' "Add provider" (which deliberately never changes the active
        provider, see `config_service.add_provider_from_preset()`), the whole
        point of the onboarding provider step is to establish the primary
        provider, so this also calls `set_active_provider()` right after.
        Both calls are the exact functions Settings already uses; this only
        sequences them for onboarding."""
        try:
            name = config_service.add_provider_from_preset(preset, api_key=api_key, model=model, base_url=base_url)
            config_service.set_active_provider(name)
        except Exception:
            self.provider_save_failed.emit()
            return
        self.provider_saved.emit(name)

    def _retire_scan(self) -> None:
        if self._scan_thread is not None and self._scan_worker is not None:
            self._retired.append((self._scan_thread, self._scan_worker))
        self._scan_thread = None
        self._scan_worker = None

    def _retire_test(self) -> None:
        if self._test_thread is not None and self._test_worker is not None:
            self._retired.append((self._test_thread, self._test_worker))
        self._test_thread = None
        self._test_worker = None

    def _on_thread_finished(self) -> None:
        finished = self.sender()
        self._retired = [(thread, worker) for thread, worker in self._retired if thread is not finished]

    # --- lifecycle ---

    def is_busy(self) -> bool:
        return self.is_scan_busy() or self.is_test_busy()

    def shutdown(self) -> None:
        """Fast, bounded drain — only ever called by `OnboardingView.
        closeEvent()` on the NOT-busy path (`is_busy()` already false), so
        both `wait()` calls below return immediately; this just flushes any
        already-queued `deleteLater()` cleanup from a worker that JUST
        finished. It is NOT how a genuinely in-flight worker is waited out
        anymore — measured directly, `QThread.wait(timeout_ms)` blocks the
        calling thread's event loop for the real duration (not merely a
        bound before something async happens), and if the worker somehow
        doesn't finish within it, destroying the window afterward hits Qt's
        fatal "QThread: Destroyed while thread is still running" abort. That
        is exactly the failure `OnboardingView.closeEvent()` now avoids by
        never calling this while `is_busy()` is true — it ignores the close
        and retries once the worker's own *_loading_changed(False) signal
        confirms it is actually done, the same cooperative shape as
        `MainWindow.closeEvent()` (Phase 17)."""
        threads_and_timeouts = [
            (self._scan_thread, _SCAN_SHUTDOWN_WAIT_MS),
            (self._test_thread, _TEST_SHUTDOWN_WAIT_MS),
        ]
        threads_and_timeouts.extend(
            (thread, _TEST_SHUTDOWN_WAIT_MS) for thread, _worker in self._retired
        )
        for thread, timeout_ms in threads_and_timeouts:
            if thread is not None:
                try:
                    thread.quit()
                    thread.wait(timeout_ms)
                except RuntimeError:
                    pass
        QCoreApplication.processEvents()
