"""Inline sudo authentication card; it never reads or requests a password."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from agent.gui import theme
from agent.gui.resources import icon_manager
from agent.gui.widgets.buttons import PrimaryButton, SecondaryButton

_SUDO_COMMAND = "sudo -v"
_UNAVAILABLE_COPY = "sudo authentication is still unavailable. Run sudo -v in a terminal and try again."


class SudoAuthCard(QFrame):
    """Presentation-only card bound to one pending Tier 2 call ID."""

    retry_requested = Signal()
    dismiss_requested = Signal()

    def __init__(self, call_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.call_id = call_id
        self.request_id: str | None = None
        self.setStyleSheet(
            f"QFrame {{ background-color: {theme.COLORS['warning_bg']}; "
            f"border: {theme.LAYOUT['border_width']}px solid {theme.COLORS['warning']}; "
            f"border-radius: {theme.LAYOUT['radius']}px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        icon_size = icon_manager.INLINE_ICON_SIZE
        icon = QLabel()
        icon.setPixmap(icon_manager.icon("alert-triangle", color="warning", size=icon_size).pixmap(icon_size, icon_size))
        title = QLabel("Administrator authentication required")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setFont(theme.general_font("label"))
        title.setStyleSheet(f"color: {theme.COLORS['warning']}; border: none;")
        title_row.addWidget(icon)
        title_row.addWidget(title)
        title_row.addStretch(1)
        layout.addLayout(title_row)

        description = QLabel(
            "This action requires sudo access. Linux Agent never asks for or stores your password. "
            "Authenticate from a terminal: run sudo -v, then return here and select Retry."
        )
        description.setTextFormat(Qt.TextFormat.PlainText)
        description.setWordWrap(True)
        description.setFont(theme.general_font("body"))
        description.setStyleSheet(f"color: {theme.COLORS['text_primary']}; border: none;")
        layout.addWidget(description)

        command_row = QHBoxLayout()
        command = QLabel(_SUDO_COMMAND)
        command.setTextFormat(Qt.TextFormat.PlainText)
        command.setFont(theme.fixed_font())
        command.setStyleSheet(f"color: {theme.COLORS['text_primary']}; border: none;")
        self._copy_button = SecondaryButton("Copy")
        self._copy_button.clicked.connect(self._copy_command)
        command_row.addWidget(command)
        command_row.addStretch(1)
        command_row.addWidget(self._copy_button)
        layout.addLayout(command_row)

        self._unavailable_label = QLabel(_UNAVAILABLE_COPY)
        self._unavailable_label.setTextFormat(Qt.TextFormat.PlainText)
        self._unavailable_label.setWordWrap(True)
        self._unavailable_label.setFont(theme.general_font("label"))
        self._unavailable_label.setStyleSheet(f"color: {theme.COLORS['warning']}; border: none;")
        self._unavailable_label.hide()
        layout.addWidget(self._unavailable_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._dismiss_button = SecondaryButton("Dismiss")
        self._retry_button = PrimaryButton("Retry")
        self._dismiss_button.clicked.connect(self.dismiss_requested)
        self._retry_button.clicked.connect(self.retry_requested)
        buttons.addWidget(self._dismiss_button)
        buttons.addWidget(self._retry_button)
        layout.addLayout(buttons)

        self.apply_responsive_width(theme.LAYOUT["min_window_width"])

    def apply_responsive_width(self, window_width: int) -> None:
        """ui_ux_spec.md mục 8.7 — cùng công thức `min(520, window_width -
        32)` với `ConfirmationDialog`, không có rule riêng cho sudo card dù
        đây là card inline trong luồng chat (mục 7.2), không phải modal riêng
        — cap chiều rộng tối đa thay vì `setFixedWidth` vì card nằm trong
        layout của `ToolStatus`/`ConversationView`, không phải cửa sổ tự do."""
        self.setMaximumWidth(theme.modal_width_for(window_width))

    def _copy_command(self) -> None:
        QApplication.clipboard().setText(_SUDO_COMMAND)

    def set_still_unavailable(self, value: bool = True) -> None:
        self._unavailable_label.setVisible(value)

    def bind_request(self, request_id: str, *, still_unavailable: bool) -> None:
        """Update the one UI card when core re-prompts after a failed Retry."""
        self.request_id = request_id
        self.set_still_unavailable(still_unavailable)

    def unavailable_text(self) -> str:
        return self._unavailable_label.text() if self._unavailable_label.isVisible() else ""

    def copy_button(self) -> SecondaryButton:
        return self._copy_button

    def dismiss_button(self) -> SecondaryButton:
        return self._dismiss_button

    def retry_button(self) -> PrimaryButton:
        return self._retry_button
