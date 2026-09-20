"""ProviderTestWorker (gui_implementation_plan.md Phase 10) — QObject chạy
trong QThread riêng, gọi config_service.test_connection() (Phase 9: gọi mạng
thật, bounded 10s, thiết kế để không tự raise) và phát lại nguyên vẹn
`ConnectionTestResult` — DTO đã an toàn (không field nào chứa API key) — qua
signal.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from agent.services import config_service


class ProviderTestWorker(QObject):
    """Dùng một lần: `SettingsController` tạo worker + thread mới cho mỗi
    lần Test connection, giống `SystemScanWorker`/`AuditTailWorker`."""

    finished = Signal(object)  # ConnectionTestResult
    failed = Signal(str)

    def __init__(
        self, provider_type: str, *, model: str, api_key: str | None, base_url: str | None
    ) -> None:
        super().__init__()
        self._provider_type = provider_type
        self._model = model
        self._api_key = api_key
        self._base_url = base_url

    def run(self) -> None:
        try:
            result = config_service.test_connection(
                self._provider_type,
                model=self._model,
                api_key=self._api_key,
                base_url=self._base_url,
            )
        except Exception:
            # test_connection() được thiết kế để không tự raise (Phase 9) —
            # đây chỉ là lưới an toàn cuối. Cố tình KHÔNG dùng str(exc): nếu
            # lỗi xảy ra ngay trong lúc build/gọi provider, message exception
            # có thể vô tình nhắc tới giá trị đang giữ trong worker này
            # (self._api_key) — không có cách nào đảm bảo an toàn 100% với
            # exception thô, nên luôn dùng status cố định, không có nội dung.
            self.failed.emit("unexpected_error")
            return
        self.finished.emit(result)
