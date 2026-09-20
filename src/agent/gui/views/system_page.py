"""System page thật (docs/ui_ux_spec.md mục 4.4, gui_implementation_plan.md
Phase 4) — card "System profile" + nút Rescan góc trên phải, bảng 2 cột
nhãn/giá trị đúng thứ tự OS/Version/Architecture/Kernel/Package Manager/
Environment/WSL/Docker/Virtual Machine.

Page chỉ gọi `SystemController`, không tự tạo QThread hay import module quét
trực tiếp — controller/worker mới là nơi chạm `system.profile` (ranh giới
presentation/orchestration của gui_implementation_plan.md). Trạng thái tải/lỗi
theo quy ước chung mục 10 (`ui_ux_spec.md`): không disable toàn bộ cửa sổ khi
chỉ page này đang loading, Cancelled/Error không coi ngang hàng crash.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from agent.gui import theme
from agent.gui.controllers.system_controller import SystemController
from agent.gui.resources import icon_manager
from agent.gui.widgets.buttons import SecondaryButton

# Đúng thứ tự bảng ở ui_ux_spec.md mục 4.4 — cũng là thứ tự
# `to_display_summary()` trả về (worker gọi qua controller) nên không cần map lại.
_FIELD_ORDER: tuple[str, ...] = (
    "OS",
    "Version",
    "Architecture",
    "Kernel",
    "Package Manager",
    "Environment",
    "WSL",
    "Docker",
    "Virtual Machine",
)

_PLACEHOLDER_VALUE = "—"
# Ví dụ nguyên văn ở ui_ux_spec.md mục 10: "System profile   ⟳ Rescanning...".
_LOADING_STATUS = "⟳ Rescanning..."
_ERROR_STATUS = "⚠ Could not scan system"


class SystemPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = SystemController(self)

        self._outer_layout = QVBoxLayout(self)
        margin = theme.LAYOUT["content_padding"]
        self._outer_layout.setContentsMargins(margin, margin, margin, margin)
        outer = self._outer_layout

        self._card = QFrame()
        card = self._card
        card.setStyleSheet(
            f"QFrame {{ background-color: {theme.COLORS['bg_card']}; "
            f"border: {theme.LAYOUT['border_width']}px solid {theme.COLORS['border']}; "
            f"border-radius: {theme.LAYOUT['radius']}px; }}"
        )
        self._card_layout = QVBoxLayout(card)
        card_layout = self._card_layout
        card_layout.setContentsMargins(margin, margin, margin, margin)

        header = QHBoxLayout()
        title = QLabel("System profile")
        title.setFont(theme.general_font("card_title"))
        title.setStyleSheet(f"color: {theme.COLORS['text_primary']};")

        self._status_label = QLabel("")
        self._status_label.setFont(theme.general_font("label"))

        self._rescan_button = SecondaryButton("Rescan", icon=icon_manager.icon("refresh-cw"))
        self._rescan_button.clicked.connect(self._on_rescan_clicked)

        header.addWidget(title)
        header.addWidget(self._status_label)
        header.addStretch(1)
        header.addWidget(self._rescan_button)

        table = QGridLayout()
        table.setContentsMargins(0, 0, 0, 0)
        table.setHorizontalSpacing(24)
        table.setVerticalSpacing(6)
        table.setColumnStretch(1, 1)
        self._value_labels: dict[str, QLabel] = {}
        for row, field_label in enumerate(_FIELD_ORDER):
            label = QLabel(field_label)
            label.setFont(theme.general_font("label"))
            label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")

            value = QLabel(_PLACEHOLDER_VALUE)
            value.setFont(theme.general_font("body"))
            value.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
            value.setWordWrap(True)

            self._value_labels[field_label] = value
            table.addWidget(label, row, 0)
            table.addWidget(value, row, 1)

        card_layout.addLayout(header)
        card_layout.addLayout(table)

        # ui_ux_spec.md mục 8.8: "content area được phép scroll" — bọc card
        # trong QScrollArea để "Rescan không bao giờ bị đẩy khỏi màn hình"
        # (mục 8.5) giữ đúng ở 560px dù value word-wrap dài làm card cao hơn
        # viewport; header/Rescan luôn ở đầu card nên vẫn thấy ngay khi mở
        # trang, cuộn xuống mới cần cho các dòng value phía dưới.
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidget(card)

        outer.addWidget(scroll_area, 1)

        # Nối signal xong mới gọi refresh() lần đầu — refresh() phát
        # loading_changed đồng bộ (controller/page cùng UI thread), nên widget
        # phải tồn tại trước khi handler chạy.
        self._controller.loading_changed.connect(self._on_loading_changed)
        self._controller.profile_loaded.connect(self._on_profile_loaded)
        self._controller.profile_failed.connect(self._on_profile_failed)
        self._controller.refresh(force_rescan=False)

    def rescan_button(self) -> SecondaryButton:
        """Public accessor cho test — không đụng `_rescan_button` trực tiếp
        (cùng quy ước với `Sidebar.button_for`)."""
        return self._rescan_button

    def value_text(self, field_label: str) -> str:
        """Public accessor cho test đọc giá trị 1 dòng theo nhãn trong `_FIELD_ORDER`."""
        return self._value_labels[field_label].text()

    def status_text(self) -> str:
        return self._status_label.text()

    def is_busy(self) -> bool:
        return self._controller.is_busy()

    def apply_responsive(self, *, compact: bool, window_width: int, short_height: bool) -> None:
        """ui_ux_spec.md mục 8.1/8.5 — 2 cột Label/Value giữ nguyên ở mọi
        breakpoint (mục 8.5), chỉ đổi content padding."""
        margin = theme.LAYOUT["content_padding_compact"] if compact else theme.LAYOUT["content_padding"]
        self._outer_layout.setContentsMargins(margin, margin, margin, margin)
        self._card_layout.setContentsMargins(margin, margin, margin, margin)
        self._card_layout.setSpacing(theme.LAYOUT["header_spacing_short" if short_height else "header_spacing"])

    def controller(self) -> SystemController:
        """Public accessor so `MainWindow.closeEvent()` can wait on this
        page's own busy state cooperatively (same idiom as `ChatPage.
        chat_controller()`) instead of only ever knowing about it via
        `shutdown()`'s blocking wait."""
        return self._controller

    def shutdown(self) -> None:
        """Gọi từ `MainWindow.closeEvent` — chỉ còn là drain nhanh, không
        busy: `MainWindow.closeEvent()` không bao giờ gọi hàm này khi
        `is_busy()` còn true nữa (xem `SystemController.shutdown()` — lý do
        là bug đã đo được: blocking `wait()` ở đây từng khiến
        `MainWindow.close()` đứng hình đúng `_SCAN_SHUTDOWN_WAIT_MS` rồi phá
        worker đang chạy dở, thay vì tránh được nó như docstring cũ tưởng)."""
        self._controller.shutdown()

    def _on_rescan_clicked(self) -> None:
        self._controller.refresh(force_rescan=True)

    def _on_loading_changed(self, loading: bool) -> None:
        self._rescan_button.setEnabled(not loading)
        if loading:
            self._status_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
            self._status_label.setText(_LOADING_STATUS)

    def _on_profile_loaded(self, rows: list[tuple[str, str]]) -> None:
        self._status_label.setText("")
        for field_label, value in rows:
            target = self._value_labels.get(field_label)
            if target is not None:
                target.setText(value)

    def _on_profile_failed(self, message: str) -> None:
        # Không render `message` (chuỗi exception thô) ra UI — chỉ trạng thái
        # chung, giữ nguyên bảng giá trị lần quét thành công gần nhất (nếu có).
        self._last_error = message
        self._status_label.setStyleSheet(f"color: {theme.COLORS['danger']};")
        self._status_label.setText(_ERROR_STATUS)
