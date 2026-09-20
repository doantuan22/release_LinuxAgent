"""Button widget theo layout token mục 2.3 (bo góc 8px) — ví dụ dùng thực tế
là "Deny"/"Allow once" của Tier 2 confirmation modal (mục 4.7), lắp hành vi
thật ở Phase 15. Phase 2 chỉ dựng widget theo token, chưa gắn callback.
"""

from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QPushButton, QWidget

from agent.gui import theme


class PrimaryButton(QPushButton):
    """Nút hành động chính — nền `--accent` đặc, ví dụ "Allow once"."""

    def __init__(self, text: str, icon: QIcon | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        if icon is not None:
            self.setIcon(icon)
        self.setFont(theme.general_font("nav_item"))
        radius = theme.LAYOUT["radius"]
        self.setStyleSheet(
            f"QPushButton {{ background-color: {theme.COLORS['accent']}; "
            f"color: {theme.COLORS['on_accent']}; border: 2px solid transparent; "
            f"border-radius: {radius}px; padding: 6px 16px; }}"
            f"QPushButton:focus {{ border: 2px solid {theme.COLORS['text_primary']}; }}"
        )


class SecondaryButton(QPushButton):
    """Nút viền, nền trong suốt — ví dụ "Deny"."""

    def __init__(self, text: str, icon: QIcon | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        if icon is not None:
            self.setIcon(icon)
        self.setFont(theme.general_font("nav_item"))
        radius = theme.LAYOUT["radius"]
        border_width = theme.LAYOUT["border_width"]
        self.setStyleSheet(
            f"QPushButton {{ background-color: transparent; "
            f"color: {theme.COLORS['text_primary']}; "
            f"border: {border_width}px solid {theme.COLORS['border']}; "
            f"border-radius: {radius}px; padding: 6px 16px; }}"
            f"QPushButton:focus {{ border-color: {theme.COLORS['accent']}; "
            f"background-color: {theme.COLORS['accent_bg']}; }}"
        )


class DangerButton(QPushButton):
    """Nút hành động phá hủy/không thể hoàn tác — nền `--danger` đặc, chỉ dùng trong
    "Vùng nguy hiểm" (vd "Xóa ứng dụng")."""

    def __init__(self, text: str, icon: QIcon | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        if icon is not None:
            self.setIcon(icon)
        self.setFont(theme.general_font("nav_item"))
        radius = theme.LAYOUT["radius"]
        self.setStyleSheet(
            f"QPushButton {{ background-color: {theme.COLORS['danger']}; "
            f"color: {theme.COLORS['on_accent']}; border: 2px solid transparent; "
            f"border-radius: {radius}px; padding: 6px 16px; }}"
            f"QPushButton:focus {{ border: 2px solid {theme.COLORS['text_primary']}; }}"
            f"QPushButton:disabled {{ background-color: {theme.COLORS['danger_bg']}; "
            f"color: {theme.COLORS['text_secondary']}; }}"
        )
