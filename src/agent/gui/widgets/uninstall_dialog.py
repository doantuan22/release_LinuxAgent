"""Dialog xác nhận gỡ ứng dụng: chỉ cho xác nhận khi người dùng gõ CHÍNH XÁC
`CONFIRMATION_PHRASE`.

Dialog riêng cho lệnh người dùng "Xóa ứng dụng" — không dùng ConfirmationRequest/
ConfirmationDialog của Tier 2 (đó là cơ chế của tool executor). Cùng quy ước với
ConfirmationDialog: non-blocking (owner gọi `show()`, không bao giờ `exec()`); đóng
cửa sổ/Escape = hủy; nút mặc định là "Hủy". Không có đường nào để bỏ qua việc gõ.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from agent.core.uninstall_service import CONFIRMATION_PHRASE, UninstallPlan
from agent.gui import theme
from agent.gui.widgets.buttons import DangerButton, SecondaryButton


def _plain_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class UninstallConfirmationDialog(QDialog):
    confirmed = Signal()
    cancelled = Signal()

    def __init__(self, plan: UninstallPlan, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._resolved = False
        self.setWindowTitle("Xóa Linux Agent")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = _plain_label("Xóa VĨNH VIỄN Linux Agent và toàn bộ dữ liệu?")
        title.setFont(theme.general_font("card_title"))
        title.setStyleSheet(f"color: {theme.COLORS['danger']};")
        layout.addWidget(title)

        warning = _plain_label("Các thư mục sau sẽ bị xóa và KHÔNG thể hoàn tác, sau đó ứng dụng được gỡ cài đặt:")
        warning.setFont(theme.general_font("body"))
        layout.addWidget(warning)

        paths_view = QPlainTextEdit()
        paths_view.setReadOnly(True)
        paths_view.setFont(theme.fixed_font())
        paths_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        paths_view.setPlainText(
            "\n".join(f"{target.path}{'' if target.exists else '  (không tồn tại, bỏ qua)'}" for target in plan.targets)
        )
        paths_view.setFixedHeight(110)
        layout.addWidget(paths_view)

        prompt = _plain_label(f'Gõ chính xác "{CONFIRMATION_PHRASE}" để xác nhận:')
        prompt.setFont(theme.general_font("label"))
        layout.addWidget(prompt)

        self._input = QLineEdit()
        self._input.setPlaceholderText(CONFIRMATION_PHRASE)
        self._input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._input)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._cancel_button = SecondaryButton("Hủy")
        self._confirm_button = DangerButton("Xóa vĩnh viễn")
        self._cancel_button.setAutoDefault(False)
        self._cancel_button.setDefault(True)
        self._confirm_button.setAutoDefault(False)
        self._confirm_button.setDefault(False)
        self._confirm_button.setEnabled(False)
        self._cancel_button.clicked.connect(self.reject)
        self._confirm_button.clicked.connect(self._on_confirm_clicked)
        actions.addWidget(self._cancel_button)
        actions.addWidget(self._confirm_button)
        layout.addLayout(actions)

        self.setMinimumWidth(520)
        self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def input_field(self) -> QLineEdit:
        return self._input

    def confirm_button(self) -> DangerButton:
        return self._confirm_button

    def cancel_button(self) -> SecondaryButton:
        return self._cancel_button

    def _on_text_changed(self, text: str) -> None:
        # So khớp CHÍNH XÁC (phân biệt hoa/thường, không cắt khoảng trắng).
        self._confirm_button.setEnabled(text == CONFIRMATION_PHRASE)

    def _on_confirm_clicked(self) -> None:
        # Kiểm tra lại tại thời điểm submit, không tin riêng trạng thái enabled của nút.
        if self._input.text() != CONFIRMATION_PHRASE:
            return
        self._finish(True)

    def reject(self) -> None:
        self._finish(False)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._finish(False)
        event.accept()

    def _finish(self, confirmed: bool) -> None:
        if self._resolved:
            return
        self._resolved = True
        (self.confirmed if confirmed else self.cancelled).emit()
        self.hide()
