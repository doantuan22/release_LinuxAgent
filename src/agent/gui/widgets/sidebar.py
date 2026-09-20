"""Sidebar điều hướng 5 mục (docs/ui_ux_spec.md mục 3, responsive mục 8.1).

Sidebar chỉ phát `navigate_requested(nav_id)` khi người dùng chọn mục khác —
không tự biết page nào đang hiển thị. `MainWindow` là nơi quyết định qua
`QStackedWidget` (Luồng kiến trúc trong gui_implementation_plan.md Phase 3:
"Sidebar signal → MainWindow controller → stacked page").

Phase 19: `set_compact()` chuyển đổi giữa Normal (220px, icon+label) và
Compact (68px, chỉ icon + tooltip tên mục khi hover) — `MainWindow` gọi hàm
này từ `resizeEvent`, sidebar không tự đọc kích thước cửa sổ.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from agent.core.redaction import sanitize_for_persistence
from agent.gui import theme
from agent.gui.resources import icon_manager

# (nav_id, label, icon_name) — đúng thứ tự 5 mục ở ui_ux_spec.md mục 3, Chat mặc định.
NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("chat", "Chat", "message-circle"),
    ("sessions", "Sessions", "history"),
    ("system", "System", "monitor"),
    ("audit", "Audit", "file-text"),
    ("settings", "Settings", "settings"),
)

DEFAULT_NAV_ID = NAV_ITEMS[0][0]

_STATUS_PLACEHOLDER_TEXT = "View profile in System"


class Sidebar(QWidget):
    navigate_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._compact = False
        self.setStyleSheet(f"background-color: {theme.COLORS['bg_sidebar']};")

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._header_layout, self._status_dot_label, self._status_text_label = self._build_status_header()
        header_widget = QWidget()
        header_widget.setLayout(self._header_layout)
        self._layout.addWidget(header_widget)

        self._icon_names: dict[str, str] = {}
        self._nav_labels: dict[str, str] = {}
        self._buttons: dict[str, QPushButton] = {}
        for nav_id, label, icon_name in NAV_ITEMS:
            self._icon_names[nav_id] = icon_name
            self._nav_labels[nav_id] = label
            button = self._build_nav_button(nav_id, label)
            self._buttons[nav_id] = button
            self._layout.addWidget(button)
        self._layout.addStretch(1)

        self._buttons[DEFAULT_NAV_ID].setChecked(True)
        self._refresh_icon(DEFAULT_NAV_ID, active=True)
        self._apply_width()

    def button_for(self, nav_id: str) -> QPushButton:
        """Public accessor cho test/controller — không đụng `_buttons` trực tiếp."""
        return self._buttons[nav_id]

    def active_nav_id(self) -> str:
        for nav_id, button in self._buttons.items():
            if button.isChecked():
                return nav_id
        raise RuntimeError("Không có mục sidebar nào active — không nên xảy ra (autoExclusive)")

    def is_compact(self) -> bool:
        return self._compact

    def status_dot_label(self) -> QLabel:
        """Public accessor cho test — kiểm tooltip/text ở chế độ compact."""
        return self._status_dot_label

    def status_text_label(self) -> QLabel:
        return self._status_text_label

    def set_system_summary(self, rows: list) -> None:
        """Reuse the System page's completed scan, never launch another scan."""
        values = dict(rows)
        text = (f"{values.get('OS', 'Unknown')} {values.get('Version', '')} · "
                f"{values.get('Package Manager', 'unsupported')}") if rows else _STATUS_PLACEHOLDER_TEXT
        text = sanitize_for_persistence(text)
        self._status_text_label.setText(text)
        self._status_dot_label.setToolTip(escape(text) if self._compact else "")

    def set_compact(self, compact: bool) -> None:
        """ui_ux_spec.md mục 8.1: Compact (800-1023px) chuyển sidebar còn
        68px, chỉ icon (tên mục qua tooltip), header chỉ còn status dot +
        tooltip chứa nội dung đầy đủ. Idempotent — gọi lại với cùng giá trị
        là no-op, không rebuild widget."""
        if compact == self._compact:
            return
        self._compact = compact
        self._apply_width()

        for nav_id, button in self._buttons.items():
            label = self._nav_labels[nav_id]
            button.setText("" if compact else label)
            button.setToolTip(label if compact else "")
            button.setStyleSheet(self._nav_button_stylesheet(compact=compact))

        self._status_text_label.setVisible(not compact)
        self._status_dot_label.setToolTip(escape(self._status_text_label.text()) if compact else "")

        margin = theme.LAYOUT["content_padding_compact"] if compact else theme.LAYOUT["content_padding"]
        self._header_layout.setContentsMargins(margin, margin, margin, margin // 2)

    def _build_status_header(self) -> tuple[QHBoxLayout, QLabel, QLabel]:
        header_layout = QHBoxLayout()
        margin = theme.LAYOUT["content_padding"]
        header_layout.setContentsMargins(margin, margin, margin, margin // 2)
        header_layout.setSpacing(6)

        dot_label = QLabel("●")
        dot_label.setFont(theme.general_font("label"))
        dot_label.setStyleSheet(f"color: {theme.COLORS['text_muted']};")

        # Phase 3 chưa nối scan_system() thật (đó là Phase 4) — dùng chấm màu
        # trung tính, không khẳng định "ok" khi chưa thật sự kiểm tra được gì.
        text_label = QLabel(_STATUS_PLACEHOLDER_TEXT)
        text_label.setTextFormat(Qt.TextFormat.PlainText)
        text_label.setWordWrap(True)
        text_label.setFont(theme.general_font("label"))
        text_label.setStyleSheet(f"color: {theme.COLORS['text_muted']};")

        header_layout.addWidget(dot_label)
        header_layout.addWidget(text_label, 1)
        return header_layout, dot_label, text_label

    def _build_nav_button(self, nav_id: str, label: str) -> QPushButton:
        button = QPushButton(label)
        button.setAccessibleName(label)
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setFlat(True)
        button.setFont(theme.general_font("nav_item"))
        button.setIconSize(QSize(icon_manager.NAV_ICON_SIZE, icon_manager.NAV_ICON_SIZE))
        button.setStyleSheet(self._nav_button_stylesheet(compact=False))
        button.toggled.connect(lambda checked, nid=nav_id: self._refresh_icon(nid, active=checked))
        button.clicked.connect(lambda checked=False, nid=nav_id: self.navigate_requested.emit(nid))
        return button

    def _nav_button_stylesheet(self, *, compact: bool) -> str:
        radius = theme.LAYOUT["radius"]
        align = "center" if compact else "left"
        padding = "8px 0px" if compact else "8px 16px"
        return (
            f"QPushButton {{ text-align: {align}; border: 2px solid transparent; background: transparent; "
            f"color: {theme.COLORS['text_secondary']}; border-radius: {radius}px; "
            f"padding: {padding}; margin: 2px 8px; }}"
            "QPushButton:checked { background-color: "
            f"{theme.COLORS['accent_bg']}; color: {theme.COLORS['accent']}; }}"
            f"QPushButton:focus {{ border: 2px solid {theme.COLORS['accent']}; }}"
        )

    def _apply_width(self) -> None:
        width = theme.LAYOUT["sidebar_width_compact"] if self._compact else theme.LAYOUT["sidebar_width"]
        self.setFixedWidth(width)

    def _refresh_icon(self, nav_id: str, *, active: bool) -> None:
        color = "accent" if active else "text_secondary"
        icon_name = self._icon_names[nav_id]
        self._buttons[nav_id].setIcon(
            icon_manager.icon(icon_name, color=color, size=icon_manager.NAV_ICON_SIZE)
        )
