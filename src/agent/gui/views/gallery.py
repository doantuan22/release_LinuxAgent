"""Design system gallery (Phase 2) — dời ra khỏi `main_window.py` ở Phase 3
vì `MainWindow` không còn dùng gallery làm central widget mặc định nữa (thay
bằng Sidebar + 5 page thật, xem `main_window.py`). Gallery vẫn còn nguyên vẹn
để xem lại token màu/typography/badge/icon/button khi cần — Phase 3 không xoá
tính năng Phase 2, chỉ đổi chỗ MainWindow hiển thị gì làm central widget.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from agent.gui import theme
from agent.gui.resources import icon_manager
from agent.gui.widgets.badge import Badge
from agent.gui.widgets.buttons import PrimaryButton, SecondaryButton


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setFont(theme.general_font("page_title"))
    return label


def _color_swatch(token_name: str, hex_value: str) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    swatch = QFrame()
    swatch.setFixedSize(28, 28)
    swatch.setStyleSheet(
        f"background-color: {hex_value}; border: {theme.LAYOUT['border_width']}px "
        f"solid {theme.COLORS['border']}; border-radius: {theme.LAYOUT['radius']}px;"
    )
    label = QLabel(f"{token_name}  {hex_value}")
    label.setFont(theme.general_font("body"))
    layout.addWidget(swatch)
    layout.addWidget(label)
    layout.addStretch(1)
    return row


def _build_color_section() -> QWidget:
    section = QWidget()
    layout = QGridLayout(section)
    for index, (name, hex_value) in enumerate(theme.COLORS.items()):
        layout.addWidget(_color_swatch(name, hex_value), index // 2, index % 2)
    return section


def _build_typography_section() -> QWidget:
    section = QWidget()
    layout = QVBoxLayout(section)
    for role, (size_px, weight) in theme.TYPOGRAPHY.items():
        label = QLabel(f"{role} — {size_px}px / Qt weight {int(weight)}")
        label.setFont(theme.general_font(role))
        layout.addWidget(label)
    mono = QLabel("install_package --name htop")
    mono.setFont(theme.fixed_font())
    layout.addWidget(mono)
    return section


def _build_badge_section() -> QWidget:
    section = QWidget()
    layout = QHBoxLayout(section)
    layout.addWidget(Badge("Tier 1", "tier1"))
    layout.addWidget(Badge("Tier 2", "tier2"))
    layout.addWidget(Badge("Allowed", "success"))
    layout.addWidget(Badge("Denied", "danger"))
    layout.addStretch(1)
    return section


def _build_icon_section() -> QWidget:
    section = QWidget()
    layout = QGridLayout(section)
    columns = 4
    for index, name in enumerate(icon_manager.available_icons()):
        cell = QWidget()
        cell_layout = QHBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        icon_label = QLabel()
        size = icon_manager.NAV_ICON_SIZE
        icon_label.setPixmap(icon_manager.icon(name, size=size).pixmap(size, size))
        text_label = QLabel(name)
        text_label.setFont(theme.general_font("label"))
        cell_layout.addWidget(icon_label)
        cell_layout.addWidget(text_label)
        layout.addWidget(cell, index // columns, index % columns)
    return section


def _build_button_section() -> QWidget:
    section = QWidget()
    layout = QHBoxLayout(section)
    layout.addWidget(SecondaryButton("Deny"))
    layout.addWidget(PrimaryButton("Allow once"))
    layout.addStretch(1)
    return section


class DesignSystemGallery(QWidget):
    """Review token màu/typography/badge/icon/button — không phải page thật."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        margin = theme.LAYOUT["content_padding"]
        layout.setContentsMargins(margin, margin, margin, margin)

        for title, widget in (
            ("Colors", _build_color_section()),
            ("Typography", _build_typography_section()),
            ("Badges", _build_badge_section()),
            ("Icons", _build_icon_section()),
            ("Buttons", _build_button_section()),
        ):
            layout.addWidget(_section_title(title))
            layout.addWidget(widget)
        layout.addStretch(1)
