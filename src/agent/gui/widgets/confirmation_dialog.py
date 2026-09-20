"""Non-blocking GUI modal for one sanitized Tier 2 confirmation request."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent.gui import theme
from agent.gui.bridges.qt_confirmation_bridge import ConfirmationPrompt
from agent.gui.resources import icon_manager
from agent.gui.widgets.badge import Badge
from agent.gui.widgets.buttons import PrimaryButton, SecondaryButton


def _plain_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class ConfirmationDialog(QDialog):
    """Application-modal presentation with exactly Deny and Allow once actions.

    The dialog uses ``show()`` from its owner, never ``exec()``. Closing the
    title-bar window or pressing Escape follows the same one-shot deny path as
    the explicit Deny button.
    """

    decision_requested = Signal(str, bool)

    def __init__(self, prompt: ConfirmationPrompt, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._prompt = prompt
        self._resolved = False
        self.setWindowTitle("System change requires confirmation")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        heading = QHBoxLayout()
        warning_icon = QLabel()
        icon_size = icon_manager.BUTTON_ICON_SIZE
        warning_icon.setPixmap(
            icon_manager.icon("alert-triangle", color="warning", size=icon_size).pixmap(icon_size, icon_size)
        )
        title = _plain_label("System change requires confirmation")
        title.setFont(theme.general_font("card_title"))
        title.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
        heading.addWidget(warning_icon)
        heading.addWidget(title, 1)
        layout.addLayout(heading)

        details = QFormLayout()
        details.setSpacing(8)
        tool_name = _plain_label(prompt.tool_name)
        tool_name.setFont(theme.general_font("body"))
        details.addRow("Tool", tool_name)
        details.addRow("Tier", Badge("Tier 2", "tier2"))

        arguments = QPlainTextEdit()
        arguments.setReadOnly(True)
        arguments.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        arguments.setPlainText(json.dumps(prompt.arguments, ensure_ascii=False, indent=2, default=str))
        arguments.setFixedHeight(104)
        arguments.setFont(theme.fixed_font())
        details.addRow("Arguments", arguments)
        layout.addLayout(details)

        description = _plain_label(prompt.description or "This action will modify your system.")
        description.setFont(theme.general_font("body"))
        description.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
        layout.addWidget(description)

        risk = _plain_label(f"Risk: {prompt.risk or 'This action will modify your system.'}")
        risk.setFont(theme.general_font("label"))
        risk.setStyleSheet(f"color: {theme.COLORS['warning']};")
        layout.addWidget(risk)

        self._actions_layout = QHBoxLayout()
        self._actions_layout.addStretch(1)
        self._deny_button = SecondaryButton("Deny")
        self._allow_button = PrimaryButton("Allow once")
        self._deny_button.setAutoDefault(False)
        self._deny_button.setDefault(True)
        self._allow_button.setAutoDefault(False)
        self._allow_button.setDefault(False)
        self._deny_button.clicked.connect(self.deny)
        self._allow_button.clicked.connect(self.allow_once)
        self._actions_layout.addWidget(self._deny_button)
        self._actions_layout.addWidget(self._allow_button)
        layout.addLayout(self._actions_layout)
        self._deny_button.setFocus(Qt.FocusReason.OtherFocusReason)
        QWidget.setTabOrder(self._deny_button, self._allow_button)

        self.apply_responsive_width(theme.LAYOUT["min_window_width"])

    def request_id(self) -> str:
        return self._prompt.request_id

    def deny_button(self) -> SecondaryButton:
        return self._deny_button

    def allow_button(self) -> PrimaryButton:
        return self._allow_button

    def actions_direction(self) -> QBoxLayout.Direction:
        """Public accessor cho test — hướng layout hiện tại của hàng nút."""
        return self._actions_layout.direction()

    def apply_responsive_width(self, window_width: int) -> None:
        """ui_ux_spec.md mục 8.7 — cùng công thức `min(520, window_width -
        32)` với `SudoAuthCard`, gọi lại mỗi khi `MainWindow` resize (dialog
        là top-level riêng, không tự nhận resizeEvent của MainWindow).
        KHÔNG thêm action nào mới khi đảo hướng dọc — chỉ 2 nút Deny/Allow
        once y hệt hướng ngang."""
        target = theme.modal_width_for(window_width)
        self.setFixedWidth(target)
        # Đo trực tiếp từ 2 nút, KHÔNG dùng `self._actions_layout.sizeHint()`
        # — sizeHint() của layout phụ thuộc hướng HIỆN TẠI (chồng dọc thì
        # width chỉ còn bằng nút rộng nhất), nên dùng nó làm điều kiện tự
        # phản hồi ngược sẽ dao động qua lại giữa 2 hướng tuỳ lần gọi trước
        # đó, thay vì luôn xét "nếu xếp ngang thì có vừa không".
        horizontal_needed = (
            self._deny_button.sizeHint().width()
            + self._allow_button.sizeHint().width()
            + self._actions_layout.spacing()
        )
        fits_horizontal = target >= horizontal_needed
        self._actions_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if fits_horizontal else QBoxLayout.Direction.TopToBottom
        )

    def deny(self) -> None:
        self._finish(False)

    def allow_once(self) -> None:
        self._finish(True)

    def reject(self) -> None:
        self._finish(False)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._finish(False)
        event.accept()

    def _finish(self, allowed: bool) -> None:
        if self._resolved:
            return
        self._resolved = True
        self.decision_requested.emit(self._prompt.request_id, allowed)
        self.hide()
