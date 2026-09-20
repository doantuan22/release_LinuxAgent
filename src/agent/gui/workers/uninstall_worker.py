"""UninstallWorker — QObject dùng một lần, chạy trong QThread riêng (cùng khuôn mẫu
`ProviderTestWorker`/`AuditTailWorker`) để `rmtree` không chặn UI thread.

Chỉ xóa dữ liệu + nhận diện cách cài; KHÔNG gỡ package ở đây: việc gỡ package chỉ được
làm sau khi UI đã hiển thị màn hình cuối (xem `UninstallController.spawn_removal`),
vì gỡ package trong lúc GUI còn đang chạy có thể làm mất file resource sắp được đọc.

Chỉ phát DTO an toàn (path + lý do lỗi hệ thống tệp) và mã lỗi cố định — không phát
text của exception bất ngờ.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from agent.core import uninstall_service


@dataclass(frozen=True)
class UninstallOutcome:
    result: uninstall_service.WipeResult
    installer: uninstall_service.Installer | None  # None khi xóa chưa xong 100%


class UninstallWorker(QObject):
    finished = Signal(object)  # UninstallOutcome
    failed = Signal(str)

    def run(self) -> None:
        try:
            result = uninstall_service.wipe_user_data()
            # Không nhận diện/gỡ package khi dữ liệu chưa xóa xong 100%.
            installer = uninstall_service.detect_installer() if result.ok else None
        except Exception:
            self.failed.emit("unexpected_error")
            return
        self.finished.emit(UninstallOutcome(result=result, installer=installer))
