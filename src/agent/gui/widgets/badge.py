"""Badge widget (docs/ui_ux_spec.md mục 2.5) — Tier 1/Tier 2/Allowed-Success/
Denied-Failed. Dùng token màu qua `theme.BADGES`, không hardcode màu ở từng
page (Audit/Chat page dùng lại y hệt widget này ở phase sau).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from agent.gui import theme


class Badge(QLabel):
    """Nhãn bo góc nhỏ. `variant` phải là một trong `theme.BADGES`."""

    def __init__(self, text: str, variant: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        try:
            bg_token, fg_token = theme.BADGES[variant]
        except KeyError as exc:
            raise ValueError(f"Unknown badge variant: {variant}") from exc

        self.setFont(theme.general_font("badge"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        radius = theme.LAYOUT["radius"]
        self.setStyleSheet(
            f"QLabel {{ background-color: {theme.COLORS[bg_token]}; "
            f"color: {theme.COLORS[fg_token]}; border-radius: {radius}px; "
            f"padding: 2px 8px; }}"
        )
