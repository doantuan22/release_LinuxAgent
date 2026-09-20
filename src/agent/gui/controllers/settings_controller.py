"""SettingsController (gui_implementation_plan.md Phase 10) — gọi
config_service. List/set-active/add/update chạy đồng bộ (đọc/ghi file JSON
cục bộ, cùng lý do Phase 6 Sessions không cần QThread); Test connection chạy
QThread thật qua `ProviderTestWorker` (gọi mạng thật, bounded 10s — cùng lý
do Phase 4 System/Phase 8 Audit cần worker).

`shutdown()` theo đúng duck-typing mà `MainWindow.closeEvent()` đã dựng từ
Phase 4 — Settings page chỉ cần có `shutdown()`, không cần sửa `MainWindow`.
"""

from __future__ import annotations

from dataclasses import fields
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from agent.core.provider_presets import ProviderPreset
from agent.core.redaction import sanitize_for_persistence
from agent.gui.workers.provider_worker import ProviderTestWorker
from agent.services import config_service

# systemd-detect-virt/network call thật của test_connection() bounded ở 10s
# (Phase 9) — chờ khi widget bị huỷ phải bao trọn mốc đó, cộng biên an toàn.
_SHUTDOWN_WAIT_MS = 12000


def _require_safe_text(value: str) -> None:
    """Reject unsafe display data, never rewrite an operational identifier."""
    if not isinstance(value, str) or sanitize_for_persistence(value) != value:
        raise ValueError("unsafe_display_data")
    if "://" in value:
        parsed = urlsplit(value)
        decoded = unquote(value)
        if parsed.username is not None or sanitize_for_persistence(decoded) != decoded:
            raise ValueError("unsafe_display_data")


def _require_safe_summary(summary: config_service.ConfigSummary | config_service.ProviderSummary) -> None:
    # These same fields populate editable forms and action callbacks. Redacting
    # them in place would silently change the configuration on a later save.
    for field in fields(summary):
        value = getattr(summary, field.name)
        if value is not None and not isinstance(value, bool):
            _require_safe_text(value)


class SettingsController(QObject):
    providers_loaded = Signal(object)  # (ConfigSummary, list[ProviderSummary])
    providers_failed = Signal(str)
    save_succeeded = Signal(str)
    save_failed = Signal(str)
    test_loading_changed = Signal(bool)
    test_finished = Signal(object)  # ConnectionTestResult

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: ProviderTestWorker | None = None
        self._retired: list[tuple[QThread, QObject]] = []

    # --- đồng bộ: đọc/ghi providers.json + .env cục bộ ---

    def refresh(self) -> None:
        try:
            active = config_service.show_config()
            providers = config_service.list_providers()
            for summary in (active, *providers):
                _require_safe_summary(summary)
        except Exception:
            self.providers_failed.emit("read_error")
            return
        self.providers_loaded.emit((active, providers))

    def set_active(self, name: str) -> None:
        try:
            _require_safe_text(name)
            config_service.set_active_provider(name)
        except Exception:
            self.save_failed.emit("set_active_failed")
            return
        self.save_succeeded.emit(name)
        self.refresh()

    def save_new_provider(
        self, preset: ProviderPreset, *, api_key: str | None, model: str, base_url: str | None
    ) -> None:
        try:
            _require_safe_text(preset.key)
            name = config_service.add_provider_from_preset(
                preset, api_key=api_key, model=model, base_url=base_url
            )
            _require_safe_text(name)
        except Exception:
            self.save_failed.emit("save_failed")
            return
        self.save_succeeded.emit(name)
        self.refresh()

    def save_provider_edits(
        self, name: str, *, model: str | None, base_url: str | None, api_key: str | None
    ) -> None:
        try:
            _require_safe_text(name)
            config_service.update_provider_fields(name, model=model, base_url=base_url, api_key=api_key)
        except Exception:
            self.save_failed.emit("save_failed")
            return
        self.save_succeeded.emit(name)
        self.refresh()

    # --- bất đồng bộ: test connection (mạng thật, QThread) ---

    def is_test_busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def shutdown(self, timeout_ms: int = _SHUTDOWN_WAIT_MS) -> None:
        """Fast, bounded drain — `MainWindow.closeEvent()` only ever calls
        this on the NOT-busy path (`is_test_busy()` already false).

        Measured directly (not assumed): closing `MainWindow` while Test
        connection was genuinely still running blocked `MainWindow.close()`
        for exactly `_SHUTDOWN_WAIT_MS` (12.006s measured), and afterward the
        stale worker thread's own `finished.emit(result)` hit `RuntimeError:
        Signal source has been deleted` once it finally returned — the
        `ProviderTestWorker` QObject had already been destroyed while it was
        still running. `MainWindow.closeEvent()` now avoids this by never
        calling `shutdown()` while `SettingsPage.is_test_busy()` is true — it
        ignores the close and retries once `test_loading_changed(False)`
        (exposed via `SettingsPage.controller()`) confirms the test is
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

        self._thread = thread
        self._worker = worker
        self.test_loading_changed.emit(True)
        thread.start()

    def _on_test_finished(self, result: object) -> None:
        self._retire_current()
        self.test_loading_changed.emit(False)
        self.test_finished.emit(result)

    def _on_test_failed(self, _status: str) -> None:
        self._retire_current()
        self.test_loading_changed.emit(False)
        # Không dùng lại message của config_service ở đây (tránh phải import
        # bảng private) — trạng thái "unknown_failure" đã đủ để UI hiển thị
        # copy trung lập giống mọi lỗi provider không xác định khác.
        self.test_finished.emit(config_service.ConnectionTestResult(
            status="unknown_failure", message="Provider request failed.",
        ))

    def _retire_current(self) -> None:
        if self._thread is not None and self._worker is not None:
            self._retired.append((self._thread, self._worker))
        self._thread = None
        self._worker = None

    def _on_thread_finished(self) -> None:
        finished = self.sender()
        self._retired = [(thread, worker) for thread, worker in self._retired if thread is not finished]
