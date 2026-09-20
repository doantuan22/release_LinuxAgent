"""MessageComposer (docs/ui_ux_spec.md mục 4.2/8.2, gui_implementation_plan.md
Phase 11) — ô input + nút Send, luôn đứng cố định ở đáy Chat page (`ChatPage`
lắp ở dưới `ConversationView` với stretch factor 0).

Send khởi tạo **disabled**: chưa có `AgentWorker` thật đứng sau để nhận tin
nhắn (Phase 12/14 mới nối agent loop qua QThread) — bật lại bằng
`set_send_enabled()` là việc của phase đó, không tự bật ở đây dù người dùng
đã nhập chữ.

Không có icon paperclip/attachment ở phase này — spec mục 8.2 vẽ ô input có
`[attachment]` trong layout tổng quát, nhưng plan "Công việc kỹ thuật" của
Phase 11 nói rõ: không hứa attachment nếu chưa có attachment API thật, icon
đó chỉ xuất hiện khi có chức năng thật đứng sau. Ưu tiên plan vì đây là
quyết định kỹ thuật cụ thể cho phase, còn spec mô tả layout đích cuối cùng.

`QLineEdit` không có API diễn giải rich text — nội dung người dùng gõ vào
luôn là chữ thuần, không cần xử lý an toàn thêm.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QWidget
from PySide6.QtCore import Signal

from agent.gui import theme
from agent.gui.resources import icon_manager
from agent.gui.widgets.buttons import PrimaryButton, SecondaryButton

_PLACEHOLDER_TEXT = "Type a message..."


class MessageComposer(QWidget):
    send_requested = Signal(str)
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._input = QLineEdit()
        self._input.setPlaceholderText(_PLACEHOLDER_TEXT)
        self._input.setFont(theme.general_font("input"))
        self._input.returnPressed.connect(self._trigger_send)

        self._send_button = PrimaryButton("Send", icon=icon_manager.icon("send", color="on_accent"))
        self._send_button.setEnabled(False)
        self._send_button.clicked.connect(self._trigger_send)

        # Phase 17 (gui_implementation_plan.md): Stop is a distinct control,
        # never a bypass — it cooperatively cancels the current turn (see
        # ChatController.request_stop()), never kills the worker/tool. Hidden
        # while idle; ChatPage toggles it opposite Send via `set_busy()`.
        self._stop_button = SecondaryButton("Stop", icon=icon_manager.icon("x"))
        self._stop_button.setVisible(False)
        self._stop_button.clicked.connect(self.stop_requested)

        layout.addWidget(self._input, 1)
        layout.addWidget(self._send_button, 0)
        layout.addWidget(self._stop_button, 0)

    def input_field(self) -> QLineEdit:
        """Public accessor cho test — đọc/gõ nội dung ô input."""
        return self._input

    def send_button(self) -> PrimaryButton:
        """Public accessor cho test — kiểm tra trạng thái enabled/disabled."""
        return self._send_button

    def stop_button(self) -> SecondaryButton:
        """Public accessor cho test — kiểm tra visibility/click của nút Stop."""
        return self._stop_button

    def set_send_enabled(self, enabled: bool) -> None:
        self._send_button.setEnabled(enabled)

    def is_send_enabled(self) -> bool:
        return self._send_button.isEnabled()

    def set_busy(self, busy: bool) -> None:
        """Toggle Stop visible/enabled opposite Send while a turn is running."""
        self._stop_button.setVisible(busy)
        self._stop_button.setEnabled(busy)

    def text(self) -> str:
        return self._input.text()

    def clear(self) -> None:
        self._input.clear()

    def _trigger_send(self) -> None:
        if not self._send_button.isEnabled():
            return
        text = self._input.text().strip()
        if not text:
            return
        self.send_requested.emit(text)
