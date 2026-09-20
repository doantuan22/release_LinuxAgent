"""Settings page thật (docs/ui_ux_spec.md mục 4.6/9.4, gui_implementation_plan.md
Phase 10) — card "Active provider" (Provider/Model/Base URL/API Key trạng
thái/Fallback), bảng "Providers" đã cấu hình (Set active/Edit mỗi dòng), và
form Add/Edit provider (`ProviderForm`, dùng lại được cho Onboarding Phase 18)
kèm Test connection.

Page chỉ gọi `SettingsController`, không tự import tầng service/factory hay
kiểu dữ liệu provider — controller/worker mới là nơi chạm backend (ranh giới
presentation/orchestration của gui_implementation_plan.md). KHÔNG có đường
nào để giá trị API key thật xuất hiện trên UI: chỉ hiển thị field trạng thái
(`api_key_status`, "configured"/"missing"/"không cần") từ dữ liệu đọc được,
chưa từng có field key thật trong đó; field key trên form CHỈ được clear sau
khi lưu thành công (mục 9.5), không bao giờ đọc lại giá trị đã lưu để hiện
lại lên form.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent.gui import theme
from agent.gui.controllers.settings_controller import SettingsController
from agent.gui.controllers.uninstall_controller import UninstallController
from agent.gui.widgets.buttons import DangerButton, PrimaryButton, SecondaryButton
from agent.gui.widgets.uninstall_dialog import UninstallConfirmationDialog
from agent.gui.widgets.provider_form import ProviderForm

_ACTIVE_FIELDS: tuple[str, ...] = ("Provider", "Model", "Base URL", "API Key", "Fallback")
_TABLE_COLUMNS: tuple[str, ...] = ("Name", "Type", "Model", "API Key", "", "")
_SET_ACTIVE_COLUMN = 4
_EDIT_COLUMN = 5

_LOADING_TEST_TEXT = "Testing..."
_TEST_BUTTON_TEXT = "Test connection"
_SAVE_BUTTON_TEXT = "Save"
_ADD_TITLE = "Add provider"
_PROVIDERS_READ_ERROR = "Could not read provider configuration."

_DANGER_TITLE = "Vùng nguy hiểm"
_DANGER_DESCRIPTION = (
    "Xóa VĨNH VIỄN toàn bộ dữ liệu của Linux Agent (cấu hình, API key, lịch sử session, "
    "cơ sở tri thức, audit log, cache) rồi gỡ cài đặt ứng dụng. Không thể hoàn tác."
)
_UNINSTALL_BUTTON_TEXT = "Xóa ứng dụng"
_CLOSE_APP_TEXT = "Đóng ứng dụng"
_UNINSTALL_BUSY_TEXT = "Đang có tác vụ chạy — hãy chờ hoặc dừng tác vụ đó trước khi xóa ứng dụng."
_UNINSTALL_WORKING_TEXT = "Đang xóa dữ liệu..."
_UNINSTALL_REMOVING_TEXT = "Đã xóa dữ liệu, đang gỡ ứng dụng..."
_UNINSTALL_UNEXPECTED_TEXT = "Không thể hoàn tất việc xóa do lỗi không mong đợi. Ứng dụng KHÔNG bị gỡ."
# Thời gian giữ màn hình cuối trước khi gỡ package + đóng GUI.
_FINAL_SCREEN_DELAY_MS = 1500


def _card(title: str) -> tuple[QFrame, QVBoxLayout, QLabel, QLabel]:
    card = QFrame()
    card.setStyleSheet(
        f"QFrame {{ background-color: {theme.COLORS['bg_card']}; "
        f"border: {theme.LAYOUT['border_width']}px solid {theme.COLORS['border']}; "
        f"border-radius: {theme.LAYOUT['radius']}px; }}"
    )
    layout = QVBoxLayout(card)
    margin = theme.LAYOUT["content_padding"]
    layout.setContentsMargins(margin, margin, margin, margin)

    header = QHBoxLayout()
    title_label = QLabel(title)
    title_label.setFont(theme.general_font("card_title"))
    title_label.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
    status_label = QLabel("")
    status_label.setFont(theme.general_font("label"))
    header.addWidget(title_label)
    header.addStretch(1)
    header.addWidget(status_label)
    layout.addLayout(header)

    return card, layout, title_label, status_label


def _active_field_block(field: str) -> tuple[QWidget, QLabel]:
    """ui_ux_spec.md mục 8.6 — mỗi field là 1 khối Label-trên-Value; khối này
    không đổi hình dạng giữa hai breakpoint, chỉ container cha đổi hướng sắp
    xếp (`QBoxLayout.setDirection`)."""
    block = QWidget()
    layout = QVBoxLayout(block)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    label = QLabel(field)
    label.setFont(theme.general_font("label"))
    label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
    value = QLabel("-")
    value.setFont(theme.general_font("body"))
    value.setWordWrap(True)
    layout.addWidget(label)
    layout.addWidget(value)
    return block, value


class SettingsPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = SettingsController(self)
        self._editing_name: str | None = None
        self._uninstall_controller = UninstallController(self)
        self._uninstall_dialog: UninstallConfirmationDialog | None = None
        self._awaiting_close = False
        self._pending_installer: str | None = None
        self._final_timer = QTimer(self)
        self._final_timer.setSingleShot(True)
        self._final_timer.timeout.connect(self._finish_uninstall)

        self._outer_layout = QVBoxLayout(self)
        margin = theme.LAYOUT["content_padding"]
        self._outer_layout.setContentsMargins(margin, margin, margin, margin)
        self._outer_layout.setSpacing(theme.LAYOUT["card_gap"])

        active_card, active_layout, _, _ = _card("Active provider")
        # ui_ux_spec.md mục 8.6: Normal — các field trên 1 hàng ngang; Compact
        # — xếp dọc từng field. `QBoxLayout.setDirection()` đổi hướng ngay
        # trên cùng layout/widget con, không rebuild lại field ở
        # `apply_responsive()`.
        self._active_fields_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._active_fields_layout.setContentsMargins(0, 0, 0, 0)
        self._active_fields_layout.setSpacing(24)
        self._active_value_labels: dict[str, QLabel] = {}
        for field in _ACTIVE_FIELDS:
            block, value_label = _active_field_block(field)
            self._active_value_labels[field] = value_label
            self._active_fields_layout.addWidget(block)
        active_layout.addLayout(self._active_fields_layout)

        providers_card, providers_layout, _, self._providers_status_label = _card("Providers")
        self._providers_table = QTableWidget(0, len(_TABLE_COLUMNS))
        self._providers_table.setHorizontalHeaderLabels(_TABLE_COLUMNS)
        self._providers_table.verticalHeader().setVisible(False)
        self._providers_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._providers_table.setFont(theme.general_font("body"))
        providers_layout.addWidget(self._providers_table)

        form_card, form_layout, self._form_title_label, _ = _card(_ADD_TITLE)
        new_provider_row = QHBoxLayout()
        new_provider_row.addStretch(1)
        self._new_provider_button = SecondaryButton("New provider")
        self._new_provider_button.clicked.connect(self._on_new_provider_clicked)
        new_provider_row.addWidget(self._new_provider_button)
        form_layout.addLayout(new_provider_row)

        self._form = ProviderForm()
        form_layout.addWidget(self._form)

        action_row = QHBoxLayout()
        self._test_button = SecondaryButton(_TEST_BUTTON_TEXT)
        self._test_button.clicked.connect(self._on_test_clicked)
        self._save_button = PrimaryButton(_SAVE_BUTTON_TEXT)
        self._save_button.clicked.connect(self._on_save_clicked)
        action_row.addWidget(self._test_button)
        action_row.addStretch(1)
        action_row.addWidget(self._save_button)
        form_layout.addLayout(action_row)

        self._form_status_label = QLabel("")
        self._form_status_label.setFont(theme.general_font("label"))
        self._form_status_label.setWordWrap(True)
        form_layout.addWidget(self._form_status_label)

        danger_card, danger_layout, danger_title, _ = _card(_DANGER_TITLE)
        danger_card.setStyleSheet(
            f"QFrame {{ background-color: {theme.COLORS['bg_card']}; "
            f"border: {theme.LAYOUT['border_width']}px solid {theme.COLORS['danger']}; "
            f"border-radius: {theme.LAYOUT['radius']}px; }}"
        )
        danger_title.setStyleSheet(f"color: {theme.COLORS['danger']};")
        danger_description = QLabel(_DANGER_DESCRIPTION)
        danger_description.setFont(theme.general_font("body"))
        danger_description.setWordWrap(True)
        danger_layout.addWidget(danger_description)
        danger_row = QHBoxLayout()
        danger_row.addStretch(1)
        self._uninstall_button = DangerButton(_UNINSTALL_BUTTON_TEXT)
        self._uninstall_button.clicked.connect(self._on_uninstall_clicked)
        danger_row.addWidget(self._uninstall_button)
        danger_layout.addLayout(danger_row)
        self._uninstall_status_label = QLabel("")
        self._uninstall_status_label.setFont(theme.general_font("label"))
        self._uninstall_status_label.setWordWrap(True)
        self._uninstall_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self._uninstall_status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        danger_layout.addWidget(self._uninstall_status_label)

        # ui_ux_spec.md mục 8.8: "content area được phép scroll" — 3 card xếp
        # dọc + form Save/Test có thể cao hơn viewport ở 560px; bọc trong
        # QScrollArea để Save/Test luôn tới được bằng cuộn thay vì bị đẩy
        # khỏi màn hình không cách nào bấm tới.
        self._header_layouts = (active_layout, providers_layout, form_layout, danger_layout)
        self._cards_container = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(theme.LAYOUT["card_gap"])
        self._cards_layout.addWidget(active_card)
        self._cards_layout.addWidget(providers_card)
        self._cards_layout.addWidget(form_card)
        self._cards_layout.addWidget(danger_card)
        self._cards_layout.addStretch(1)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidget(self._cards_container)
        self._outer_layout.addWidget(scroll_area, 1)

        self._controller.providers_loaded.connect(self._on_providers_loaded)
        self._controller.providers_failed.connect(self._on_providers_failed)
        self._controller.save_succeeded.connect(self._on_save_succeeded)
        self._controller.save_failed.connect(self._on_save_failed)
        self._controller.test_loading_changed.connect(self._on_test_loading_changed)
        self._controller.test_finished.connect(self._on_test_finished)
        self._uninstall_controller.finished.connect(self._on_uninstall_finished)
        self._uninstall_controller.failed.connect(self._on_uninstall_failed)

        self._controller.refresh()

    # --- public accessors cho test/controller ---

    def active_field_text(self, field: str) -> str:
        return self._active_value_labels[field].text()

    def providers_table(self) -> QTableWidget:
        return self._providers_table

    def providers_status_text(self) -> str:
        return self._providers_status_label.text()

    def form(self) -> ProviderForm:
        return self._form

    def form_title_text(self) -> str:
        return self._form_title_label.text()

    def test_button(self) -> SecondaryButton:
        return self._test_button

    def save_button(self) -> PrimaryButton:
        return self._save_button

    def status_text(self) -> str:
        return self._form_status_label.text()

    def editing_name(self) -> str | None:
        return self._editing_name

    def uninstall_button(self) -> DangerButton:
        return self._uninstall_button

    def uninstall_status_text(self) -> str:
        return self._uninstall_status_label.text()

    def uninstall_dialog(self) -> UninstallConfirmationDialog | None:
        return self._uninstall_dialog

    def uninstall_controller(self) -> UninstallController:
        """Public accessor để `MainWindow.closeEvent()` chờ đúng trạng thái busy."""
        return self._uninstall_controller

    def is_uninstall_busy(self) -> bool:
        return self._uninstall_controller.is_busy()

    def is_test_busy(self) -> bool:
        return self._controller.is_test_busy()

    def active_fields_direction(self) -> QBoxLayout.Direction:
        """Public accessor cho test — hướng sắp xếp hiện tại của khối Active
        provider (mục 8.6)."""
        return self._active_fields_layout.direction()

    def apply_responsive(self, *, compact: bool, window_width: int, short_height: bool) -> None:
        """ui_ux_spec.md mục 8.1/8.6."""
        margin = theme.LAYOUT["content_padding_compact"] if compact else theme.LAYOUT["content_padding"]
        gap = theme.LAYOUT["card_gap_compact"] if compact else theme.LAYOUT["card_gap"]
        self._outer_layout.setContentsMargins(margin, margin, margin, margin)
        self._cards_layout.setSpacing(gap)
        for layout in self._header_layouts:
            layout.setSpacing(theme.LAYOUT["header_spacing_short" if short_height else "header_spacing"])
        self._active_fields_layout.setDirection(
            QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        )

    def controller(self) -> SettingsController:
        """Public accessor so `MainWindow.closeEvent()` can wait on this
        page's own busy state cooperatively (same idiom as `ChatPage.
        chat_controller()`) instead of only ever knowing about it via
        `shutdown()`'s blocking wait."""
        return self._controller

    def shutdown(self) -> None:
        """`MainWindow.closeEvent()` tự gọi qua duck-typing (Phase 4/8) —
        chỉ còn là drain nhanh, không busy: `MainWindow.closeEvent()` không
        bao giờ gọi hàm này khi `is_test_busy()` còn true nữa (xem
        `SettingsController.shutdown()` — lý do là bug đã đo được: blocking
        `wait()` ở đây từng khiến `MainWindow.close()` đứng hình đúng
        `_TEST_SHUTDOWN_WAIT_MS` rồi phá worker đang chạy dở)."""
        self._controller.shutdown()
        self._uninstall_controller.shutdown()

    # --- Vùng nguy hiểm: xóa ứng dụng (dùng chung uninstall_service với CLI) ---

    def _show_uninstall_status(self, text: str, *, error: bool = False) -> None:
        color = theme.COLORS["danger"] if error else theme.COLORS["text_secondary"]
        self._uninstall_status_label.setStyleSheet(f"color: {color};")
        self._uninstall_status_label.setText(text)

    def _on_uninstall_clicked(self) -> None:
        if self._awaiting_close:
            self._quit_application()
            return
        # Không xóa dữ liệu dưới chân một lượt chat/tác vụ đang chạy (vd tool Tier 2 giữa chừng).
        is_busy = getattr(self.window(), "is_busy", None)
        if callable(is_busy) and is_busy():
            self._show_uninstall_status(_UNINSTALL_BUSY_TEXT, error=True)
            return
        dialog = UninstallConfirmationDialog(self._uninstall_controller.plan(), self)
        dialog.confirmed.connect(self._on_uninstall_confirmed)
        self._uninstall_dialog = dialog
        dialog.show()

    def _on_uninstall_confirmed(self) -> None:
        self._uninstall_button.setEnabled(False)
        self._show_uninstall_status(_UNINSTALL_WORKING_TEXT)
        self._uninstall_controller.start()

    def _on_uninstall_failed(self, _status: str) -> None:
        self._uninstall_button.setEnabled(True)
        self._show_uninstall_status(_UNINSTALL_UNEXPECTED_TEXT, error=True)

    def _on_uninstall_finished(self, outcome: object) -> None:
        result = outcome.result  # type: ignore[attr-defined]
        installer = outcome.installer  # type: ignore[attr-defined]
        if not result.ok:
            lines = ["Xóa dữ liệu CHƯA hoàn tất — ứng dụng KHÔNG bị gỡ và sẽ không tự đóng:"]
            lines += [f"✗ {failure.path}: {failure.reason}" for failure in result.failed]
            lines += [f"✓ đã xóa {path}" for path in result.removed]
            lines.append("Hãy xử lý các lỗi trên rồi thử lại (thư mục đã xóa sẽ được bỏ qua).")
            self._uninstall_button.setEnabled(True)
            self._show_uninstall_status("\n".join(lines), error=True)
            return
        if installer in (None, "unknown"):
            self._show_manual_removal("Đã xóa toàn bộ dữ liệu. Không xác định được cách bạn đã cài đặt nên chưa gỡ package.")
            return
        self._pending_installer = installer
        self._show_uninstall_status(_UNINSTALL_REMOVING_TEXT)
        self._final_timer.start(_FINAL_SCREEN_DELAY_MS)

    def _show_manual_removal(self, headline: str) -> None:
        lines = [headline, "Hãy tự chạy MỘT trong các lệnh sau, tùy cách bạn đã cài Linux Agent:"]
        lines += [f"  ({label})  {command}" for label, command in self._uninstall_controller.manual_commands()]
        self._show_uninstall_status("\n".join(lines))
        # Dữ liệu đã xóa: chỉ còn cho phép đóng ứng dụng, để người dùng đọc kịp hướng dẫn.
        self._awaiting_close = True
        self._uninstall_button.setText(_CLOSE_APP_TEXT)
        self._uninstall_button.setEnabled(True)

    def _finish_uninstall(self) -> None:
        installer = self._pending_installer
        if installer is None:
            return
        error = self._uninstall_controller.spawn_removal(installer)  # type: ignore[arg-type]
        if error is not None:
            self._show_manual_removal(f"Đã xóa dữ liệu nhưng KHÔNG khởi chạy được lệnh gỡ package ({error}).")
            return
        self._quit_application()

    def _quit_application(self) -> None:
        window = self.window()
        if window is not self:
            window.close()
        QApplication.quit()

    # --- new/edit mode ---

    def _on_new_provider_clicked(self) -> None:
        self._editing_name = None
        self._form.set_add_mode()
        self._set_form_title(_ADD_TITLE)
        self._form_status_label.setText("")

    def _enter_edit_mode(self, name: str, provider_type: str, model: str, base_url: str | None, requires_key: bool) -> None:
        self._editing_name = name
        self._form.set_edit_mode(
            provider_type=provider_type, model=model, base_url=base_url, requires_api_key=requires_key
        )
        self._set_form_title(f"Edit {name}")
        self._form_status_label.setText("")

    def _set_form_title(self, text: str) -> None:
        self._form_title_label.setText(text)

    # --- actions ---

    def _on_test_clicked(self) -> None:
        preset = self._form.current_preset()
        self._controller.test_connection(
            preset.provider_type,
            model=self._form.current_model(),
            api_key=self._form.current_api_key(),
            base_url=self._form.current_base_url(),
        )

    def _on_save_clicked(self) -> None:
        if self._editing_name is None:
            preset = self._form.current_preset()
            self._controller.save_new_provider(
                preset,
                api_key=self._form.current_api_key(),
                model=self._form.current_model(),
                base_url=self._form.current_base_url(),
            )
        else:
            self._controller.save_provider_edits(
                self._editing_name,
                model=self._form.current_model(),
                base_url=self._form.current_base_url(),
                api_key=self._form.current_api_key(),
            )

    # --- controller callbacks ---

    def _on_providers_loaded(self, payload: object) -> None:
        active, providers = payload
        self._providers_status_label.setText("")
        for field, value in (
            ("Provider", active.active_provider),
            ("Model", active.model),
            ("Base URL", active.base_url or "-"),
            ("API Key", active.api_key_status),
            ("Fallback", active.fallback or "-"),
        ):
            self._active_value_labels[field].setText(value)

        self._providers_table.setRowCount(len(providers))
        for row, summary in enumerate(providers):
            self._providers_table.setItem(row, 0, QTableWidgetItem(summary.name))
            self._providers_table.setItem(row, 1, QTableWidgetItem(summary.provider_type))
            self._providers_table.setItem(row, 2, QTableWidgetItem(summary.model))
            self._providers_table.setItem(row, 3, QTableWidgetItem(summary.api_key_status))

            if summary.is_active:
                active_label = QLabel("Active")
                active_label.setStyleSheet(f"color: {theme.COLORS['accent']};")
                self._providers_table.setCellWidget(row, _SET_ACTIVE_COLUMN, active_label)
            else:
                set_active_button = SecondaryButton("Set active")
                set_active_button.clicked.connect(lambda _c=False, n=summary.name: self._controller.set_active(n))
                self._providers_table.setCellWidget(row, _SET_ACTIVE_COLUMN, set_active_button)

            edit_button = SecondaryButton("Edit")
            edit_button.clicked.connect(
                lambda _c=False, s=summary: self._enter_edit_mode(
                    s.name, s.provider_type, s.model, s.base_url, s.api_key_status != "không cần"
                )
            )
            self._providers_table.setCellWidget(row, _EDIT_COLUMN, edit_button)

    def _on_providers_failed(self, _status: str) -> None:
        self._providers_status_label.setStyleSheet(f"color: {theme.COLORS['danger']};")
        self._providers_status_label.setText(_PROVIDERS_READ_ERROR)

    def _on_save_succeeded(self, _name: str) -> None:
        self._form.clear_api_key()
        self._form_status_label.setStyleSheet(f"color: {theme.COLORS['success']};")
        self._form_status_label.setText("Provider saved.")

    def _on_save_failed(self, _status: str) -> None:
        # Không clear API key khi lưu thất bại — người dùng cần sửa lại.
        self._form_status_label.setStyleSheet(f"color: {theme.COLORS['danger']};")
        self._form_status_label.setText("Could not save provider configuration.")

    def _on_test_loading_changed(self, loading: bool) -> None:
        self._test_button.setEnabled(not loading)
        self._test_button.setText(_LOADING_TEST_TEXT if loading else _TEST_BUTTON_TEXT)
        if loading:
            self._form_status_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
            self._form_status_label.setText(_LOADING_TEST_TEXT)

    def _on_test_finished(self, result: object) -> None:
        if result.status == "success":
            self._form_status_label.setStyleSheet(f"color: {theme.COLORS['success']};")
            self._form_status_label.setText(f"✓ {result.message}")
        else:
            # Không clear API key ở đây (mục 9.5) — chỉ hiển thị lý do ngắn
            # đã được config_service chuẩn hoá, không phải exception thô.
            self._form_status_label.setStyleSheet(f"color: {theme.COLORS['danger']};")
            self._form_status_label.setText(f"✕ {result.message}")
