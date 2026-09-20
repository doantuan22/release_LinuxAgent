"""MainWindow (gui_implementation_plan.md Phase 3).

Phase 1: cửa sổ trống. Phase 2: gallery tạm làm central widget (dời sang
`views/gallery.py` ở phase này — xem docstring file đó). Phase 3: Sidebar cố
định 220px điều hướng 5 page thật (placeholder nội dung, chưa gắn backend) —
"Trạng thái GUI sau phase": "Có app 5 mục với nội dung placeholder".

Luồng kiến trúc: Sidebar signal (`navigate_requested`) → MainWindow (điều
khiển `QStackedWidget`) → page tương ứng hiện ra. Chỉ một MainWindow.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QWidget

from agent.gui import theme
from agent.gui.views.audit_page import AuditPage
from agent.gui.views.chat_page import ChatPage
from agent.gui.views.sessions_page import SessionsPage
from agent.gui.views.settings_page import SettingsPage
from agent.gui.views.system_page import SystemPage
from agent.gui.widgets.confirmation_dialog import ConfirmationDialog
from agent.gui.widgets.sidebar import DEFAULT_NAV_ID, Sidebar

# Đúng thứ tự 5 mục ở ui_ux_spec.md mục 3 — phải khớp NAV_ITEMS trong sidebar.py.
_PAGE_FACTORIES: tuple[tuple[str, type], ...] = (
    ("chat", ChatPage),
    ("sessions", SessionsPage),
    ("system", SystemPage),
    ("audit", AuditPage),
    ("settings", SettingsPage),
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Linux Agent")
        self.setMinimumSize(theme.LAYOUT["min_window_width"], theme.LAYOUT["min_window_height"])

        self.sidebar = Sidebar()
        self.stack = QStackedWidget()
        self._pages: dict[str, QWidget] = {}
        for nav_id, page_cls in _PAGE_FACTORIES:
            page = page_cls()
            self._pages[nav_id] = page
            self.stack.addWidget(page)

        self.sidebar.navigate_requested.connect(self._navigate)
        self._pages["chat"].open_settings_requested.connect(self.sidebar.button_for("settings").click)
        self._pages["system"].controller().profile_loaded.connect(self.sidebar.set_system_summary)
        self._pages["system"].controller().profile_failed.connect(lambda _: self.sidebar.set_system_summary([]))
        self._pages["sessions"].resume_requested.connect(self._handle_resume_requested)
        self._confirmation_dialogs: dict[str, ConfirmationDialog] = {}
        self._is_closing = False
        self._close_pending = False
        chat_controller = self._pages["chat"].chat_controller()
        chat_controller.confirmation_requested.connect(
            self._show_confirmation_dialog, Qt.ConnectionType.QueuedConnection
        )
        # Phase 17: Stop denies the bridge future directly, which never goes
        # through _resolve_confirmation() — without this, a dialog left over
        # from a Stop-cancelled turn stays tracked/visible and can swallow a
        # click meant for the next turn's dialog.
        chat_controller.stop_requested.connect(
            self._close_pending_confirmation_dialogs, Qt.ConnectionType.QueuedConnection
        )

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self._navigate(DEFAULT_NAV_ID)
        # Áp breakpoint ngay tại construction — không chờ resizeEvent đầu
        # tiên, vì test/caller có thể đọc trạng thái compact/normal trước khi
        # `show()` từng chạy một vòng event loop.
        self._apply_responsive_layout()

    def _navigate(self, nav_id: str) -> None:
        self.stack.setCurrentWidget(self._pages[nav_id])

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def _apply_responsive_layout(self) -> None:
        """ui_ux_spec.md mục 8.1-8.8 — một điểm duy nhất quyết định breakpoint
        rồi phát cho sidebar + từng page qua `apply_responsive()` (duck-typed,
        cùng quy ước với `shutdown()`); page nào không cần responsive không
        bắt buộc có method này. Mở modal đang hiển thị cũng được cập nhật lại
        width theo đúng công thức mục 8.7 khi cửa sổ đổi kích thước."""
        width = self.width()
        height = self.height()
        compact = theme.is_compact_width(width)
        short_height = theme.is_short_height(height)

        self.sidebar.set_compact(compact)
        for page in self._pages.values():
            apply_responsive = getattr(page, "apply_responsive", None)
            if callable(apply_responsive):
                apply_responsive(compact=compact, window_width=width, short_height=short_height)
        for dialog in self._confirmation_dialogs.values():
            dialog.apply_responsive_width(width)

    def _handle_resume_requested(self, session_id: str) -> None:
        # Resume → Chat session selection (Phase 6): Chat page nhận đúng ID
        # đầy đủ trước, rồi chuyển page qua đúng đường click sidebar thật
        # (giữ nguyên hành vi active indicator/icon) — Chat page thật (Phase
        # 14) mới xử lý ID này (load history/gửi tin nhắn dưới session đó).
        self._pages["chat"].open_session(session_id)
        self.sidebar.button_for("chat").click()

    def _show_confirmation_dialog(self, prompt: object) -> None:
        """Show an asynchronous app modal only while its request is pending."""
        chat_controller = self._pages["chat"].chat_controller()
        request_id = getattr(prompt, "request_id", "")
        if self._is_closing or not isinstance(request_id, str) or not chat_controller.is_confirmation_pending(request_id):
            return
        if request_id in self._confirmation_dialogs:
            return
        dialog = ConfirmationDialog(prompt, self)
        dialog.apply_responsive_width(self.width())
        self._confirmation_dialogs[request_id] = dialog
        dialog.decision_requested.connect(self._resolve_confirmation)
        dialog.show()  # Deliberately never QDialog.exec().

    def _resolve_confirmation(self, request_id: str, allowed: bool) -> None:
        self._pages["chat"].chat_controller().resolve_confirmation(request_id, allowed)
        dialog = self._confirmation_dialogs.pop(request_id, None)
        if dialog is not None:
            dialog.deleteLater()

    def _close_pending_confirmation_dialogs(self) -> None:
        """Close and forget every tracked dialog (Stop or app close).

        ``ConfirmationDialog.closeEvent()`` denies through its own one-shot
        ``decision_requested`` — same path as the Deny button — so this also
        correctly resolves the bridge future for whichever dialog is still
        showing. A dialog close can emit a duplicate deny; bridge request IDs
        are one-shot, so a redundant ``deny_all()`` elsewhere is harmless.
        """
        for dialog in tuple(self._confirmation_dialogs.values()):
            dialog.close()
        self._confirmation_dialogs.clear()

    def _is_any_worker_busy(self) -> bool:
        """Chat's agent loop is not the only worker that can be in flight
        when the window is closed: System page auto-starts a scan on
        construction, Settings' Test connection runs on its own QThread, and
        Audit page auto-starts a tail read on construction too — all three
        discovered (measured, not assumed) to block `MainWindow.close()` for
        their full shutdown bound and risk destroying a still-running
        QThread if closed while busy, the exact failure Phase 17 already
        fixed for chat. Sessions page also auto-starts its list load on
        construction (its SQLite + redaction moved off the UI thread), so
        it is covered the same way."""
        chat_controller = self._pages["chat"].chat_controller()
        return (
            chat_controller.is_busy()
            or self._pages["system"].is_busy()
            or self._pages["settings"].is_test_busy()
            or self._pages["audit"].is_busy()
            or self._pages["sessions"].is_busy()
            or self._pages["settings"].is_uninstall_busy()
        )

    def is_busy(self) -> bool:
        """Public: còn worker nào (chat/System/Settings/Audit/Sessions/uninstall) đang chạy
        không. Settings' "Xóa ứng dụng" hỏi hàm này trước khi cho xóa dữ liệu."""
        return self._is_any_worker_busy()

    def closeEvent(self, event: QCloseEvent) -> None:
        # Page nào sở hữu worker thread thật (System từ Phase 4) phải chặn
        # tới khi worker kết thúc trước khi widget bị huỷ — QThread bị huỷ
        # lúc còn chạy là Qt fatal assertion, không phải warning bỏ qua
        # được. Page thuần placeholder không có `shutdown()`, bỏ qua.
        self._is_closing = True
        chat_controller = self._pages["chat"].chat_controller()
        system_page = self._pages["system"]
        settings_page = self._pages["settings"]
        audit_page = self._pages["audit"]
        sessions_page = self._pages["sessions"]
        self._close_pending_confirmation_dialogs()

        if self._is_any_worker_busy():
            # Phase 17 (chat) + follow-up (System/Settings/Audit): never
            # QThread.wait() here — that would block the UI thread. Deny
            # pending confirmation/sudo waiters and cancel chat's turn token
            # if chat is the one busy (cooperative; an already-invoked tool
            # call still runs to completion) — System/Settings/Audit's
            # workers aren't cancellable, so for them this only ever WAITS,
            # never stops anything. Ignore this close and wait for whichever
            # worker(s) are actually busy to report done via their own
            # signal, then retry. A second close click while already
            # waiting is silently ignored.
            event.ignore()
            if self._close_pending:
                return
            self._close_pending = True
            self.statusBar().showMessage("Closing… waiting for the current turn to finish safely.")
            if chat_controller.is_busy():
                chat_controller.finished.connect(self._retry_close, Qt.ConnectionType.QueuedConnection)
                chat_controller.request_stop()
            if system_page.is_busy():
                system_page.controller().loading_changed.connect(
                    self._retry_close, Qt.ConnectionType.QueuedConnection
                )
            if settings_page.is_test_busy():
                settings_page.controller().test_loading_changed.connect(
                    self._retry_close, Qt.ConnectionType.QueuedConnection
                )
            if audit_page.is_busy():
                audit_page.controller().loading_changed.connect(
                    self._retry_close, Qt.ConnectionType.QueuedConnection
                )
            if sessions_page.is_busy():
                sessions_page.controller().loading_changed.connect(
                    self._retry_close, Qt.ConnectionType.QueuedConnection
                )
            if settings_page.is_uninstall_busy():
                settings_page.uninstall_controller().loading_changed.connect(
                    self._retry_close, Qt.ConnectionType.QueuedConnection
                )
            return

        self.statusBar().clearMessage()
        chat_controller.deny_all_confirmations()
        chat_controller.deny_all_sudo_authentication()
        for page in self._pages.values():
            shutdown = getattr(page, "shutdown", None)
            if callable(shutdown):
                shutdown()
        super().closeEvent(event)

    def _retry_close(self, *_args: object) -> None:
        """A worker that blocked the earlier close attempt has now finished
        naturally (never killed) — retry the close it deferred. Re-checks
        the aggregate busy state (not just "a signal fired") because chat/
        System/Settings can finish at different times; only actually closes
        once none of them are still busy."""
        if self._is_any_worker_busy():
            return
        self.close()
