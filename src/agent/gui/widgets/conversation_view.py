"""ConversationView (docs/ui_ux_spec.md mục 4.2, gui_implementation_plan.md
Phase 11) — khung hội thoại co giãn: empty state (icon mờ giữa khung + text
gợi ý) khi chưa có message nào, hoặc danh sách message cuộn được khi đã có.
Composer đứng ngoài widget này (`ChatPage` lắp cố định ở đáy, xem
`widgets/message_composer.py`) — `ConversationView` chỉ chịu trách nhiệm
phần co giãn phía trên.

Không dùng widget rich-text nào để hiển thị nội dung message: user bubble/
assistant text/tool status đều dựng qua `_plain_label()` — `QLabel` với
`setTextFormat(Qt.TextFormat.PlainText)` ép buộc. API này luôn coi input là
chữ thuần dù nội dung giống HTML/markup, cùng nguyên lý an toàn với
`QPlainTextEdit` ở Sessions page (Phase 6) — không cần tự escape thủ công và
không có đường nào để nội dung untrusted (user/LLM) bị diễn giải thành
rich text.

Theo mục 4.2: bong bóng nền chỉ dùng cho message của **user** (căn phải);
phản hồi **assistant** là text thuần căn trái, không có bong bóng/nền — hai
nhánh `add_user_message()`/`add_assistant_message()` cố tình khác nhau, đây
không phải thiếu sót đối xứng.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from agent.gui import theme
from agent.gui.resources import icon_manager
from agent.gui.widgets.badge import Badge
from agent.gui.widgets.tool_status import ToolStatus
from agent.core.executor import ToolOutcomeState
from agent.core.redaction import sanitize_for_persistence

_EMPTY_STATE_TEXT = "Ask about your Linux system"
_USER_BUBBLE_MAX_WIDTH = 480

# ui_ux_spec.md mục 8.2 — Normal 88-96px icon/18-20px text, Compact 64px/
# 16-18px (điểm giữa mỗi khoảng, không phải số tùy ý).
_EMPTY_ICON_SIZE_NORMAL = 92
_EMPTY_ICON_SIZE_COMPACT = 64
_EMPTY_TEXT_SIZE_NORMAL = 19
_EMPTY_TEXT_SIZE_COMPACT = 17

_EMPTY_PAGE_INDEX = 0
_MESSAGES_PAGE_INDEX = 1


def _plain_label(text: str) -> QLabel:
    label = QLabel(sanitize_for_persistence(text))
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class ConversationView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._compact = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        self._stack.addWidget(self._build_empty_state())
        self._stack.addWidget(self._build_scroll_area())

        self._message_count = 0
        self._tool_status_by_call_id: dict[str, ToolStatus] = {}

    def _build_empty_state(self) -> QWidget:
        page = QWidget()
        self._empty_layout = QVBoxLayout(page)
        self._empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_layout.setSpacing(12)

        self._empty_icon_label = QLabel()
        self._empty_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._empty_text_label = QLabel(_EMPTY_STATE_TEXT)
        self._empty_text_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
        self._empty_text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._empty_layout.addWidget(self._empty_icon_label)
        self._empty_layout.addWidget(self._empty_text_label)
        self._apply_empty_state_size()
        return page

    def _apply_empty_state_size(self) -> None:
        icon_size = _EMPTY_ICON_SIZE_COMPACT if self._compact else _EMPTY_ICON_SIZE_NORMAL
        text_size = _EMPTY_TEXT_SIZE_COMPACT if self._compact else _EMPTY_TEXT_SIZE_NORMAL
        self._empty_icon_label.setPixmap(
            icon_manager.icon("message-circle", color="text_muted", size=icon_size).pixmap(icon_size, icon_size)
        )
        font = theme.general_font("body")
        font.setPixelSize(text_size)
        self._empty_text_label.setFont(font)

    def set_compact(self, compact: bool) -> None:
        """ui_ux_spec.md mục 8.2 — kích thước icon/text empty state theo
        breakpoint chiều rộng."""
        if compact == self._compact:
            return
        self._compact = compact
        self._apply_empty_state_size()

    def set_short_height(self, short_height: bool) -> None:
        """ui_ux_spec.md mục 8.8: "Nếu chiều cao nhỏ, empty state dịch lên
        trên để không va vào message input" — thiên lệch canh giữa lên trên
        thay vì `AlignCenter` tuyệt đối khi cửa sổ thấp (560-699px)."""
        alignment = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        if not short_height:
            alignment = Qt.AlignmentFlag.AlignCenter
        self._empty_layout.setAlignment(alignment)

    def _build_scroll_area(self) -> QScrollArea:
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self._messages_container = QWidget()
        self._messages_layout = QVBoxLayout(self._messages_container)
        self._messages_layout.setContentsMargins(0, 0, 0, 0)
        self._messages_layout.setSpacing(12)
        self._messages_layout.addStretch(1)

        scroll_area.setWidget(self._messages_container)
        return scroll_area

    def is_empty(self) -> bool:
        return self._message_count == 0

    def message_count(self) -> int:
        return self._message_count

    def clear(self) -> None:
        while self._messages_layout.count() > 1:  # giữ lại stretch cuối cùng
            item = self._messages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._message_count = 0
        self._tool_status_by_call_id.clear()
        self._stack.setCurrentIndex(_EMPTY_PAGE_INDEX)

    def add_user_message(self, text: str) -> None:
        bubble = QFrame()
        bubble.setMaximumWidth(_USER_BUBBLE_MAX_WIDTH)
        bubble.setStyleSheet(
            f"QFrame {{ background-color: {theme.COLORS['accent_bg']}; "
            f"border-radius: {theme.LAYOUT['radius']}px; }}"
        )
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 8, 12, 8)
        label = _plain_label(text)
        label.setFont(theme.general_font("body"))
        label.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
        bubble_layout.addWidget(label)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(bubble)
        self._insert_row(row)

    def add_assistant_message(self, text: str) -> None:
        # Không bong bóng/nền — chỉ text thuần căn trái (mục 4.2).
        label = _plain_label(text)
        label.setFont(theme.general_font("body"))
        label.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
        self._insert_widget(label)

    def add_tool_status(self, tool_name: str, *, tier_label: str = "Tier 1", variant: str = "tier1") -> None:
        """Dòng trạng thái tool call inline trong luồng chat (mục 4.2: icon
        terminal + tên tool + badge tier) — không phải modal, không chặn
        thao tác."""
        icon_label = QLabel()
        icon_size = icon_manager.INLINE_ICON_SIZE
        icon_label.setPixmap(icon_manager.icon("terminal", size=icon_size).pixmap(icon_size, icon_size))

        name_label = _plain_label(tool_name)
        name_label.setFont(theme.general_font("label"))
        name_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(icon_label)
        row.addWidget(name_label)
        row.addWidget(Badge(tier_label, variant))
        row.addStretch(1)
        self._insert_row(row)

    def start_tool_status(
        self, call_id: str, tool_name: str, *, tier_label: str = "Tier 1", tier_variant: str = "tier1",
        initial_status: str = "Running",
    ) -> None:
        """Insert exactly one mutable status item for this call ID."""
        if call_id in self._tool_status_by_call_id:
            return
        status = ToolStatus(
            call_id, tool_name, tier_label=tier_label, tier_variant=tier_variant, initial_status=initial_status,
        )
        self._tool_status_by_call_id[call_id] = status
        self._insert_widget(status)

    def finish_tool_status(self, call_id: str, state: ToolOutcomeState) -> None:
        """Mutate the row created by STARTED; never append a duplicate row."""
        status = self._tool_status_by_call_id.get(call_id)
        if status is not None:
            if state != ToolOutcomeState.AUTHENTICATION_REQUIRED:
                status.remove_sudo_card()
            status.set_outcome(state)

    def set_tool_phase(self, call_id: str, text: str, *, color: str = "text_muted") -> None:
        status = self._tool_status_by_call_id.get(call_id)
        if status is not None:
            status.set_phase(text, color=color)

    def set_sudo_auth_card(self, call_id: str, card: "SudoAuthCard") -> None:
        status = self._tool_status_by_call_id.get(call_id)
        if status is not None:
            status.set_sudo_card(card)

    def remove_sudo_auth_card(self, call_id: str) -> None:
        status = self._tool_status_by_call_id.get(call_id)
        if status is not None:
            status.remove_sudo_card()

    def tool_status_count(self) -> int:
        return len(self._tool_status_by_call_id)

    def tool_status(self, call_id: str) -> ToolStatus | None:
        return self._tool_status_by_call_id.get(call_id)

    def add_status_message(self, text: str, *, variant: str = "info") -> None:
        """Small plain-text provider/offline status; never a raw SDK error."""
        label = _plain_label(text)
        label.setFont(theme.general_font("label"))
        colors = {
            "info": "text_secondary",
            "warning": "warning",
            "error": "danger",
            # Phase 17 (gui_implementation_plan.md): "Cancelled" per ui_ux_spec.md
            # mục 10 — muted text, deliberately NOT the "error" variant above.
            "muted": "text_muted",
        }
        label.setStyleSheet(f"color: {theme.COLORS[colors[variant]]};")
        self._insert_widget(label)

    def add_provider_error(self, title: str, detail: str, *, warning: bool = False,
                           action: str = "Retry") -> QPushButton:
        """Sanitized card with explicit severity and a caller-owned action."""
        color = "warning" if warning else "danger"
        card = QFrame()
        card.setObjectName("providerErrorCard")
        card.setProperty("severity", color)
        card.setStyleSheet(
            f"QFrame {{ background-color: {theme.COLORS[color + '_bg']}; "
            f"border: {theme.LAYOUT['border_width']}px solid {theme.COLORS[color]}; "
            f"border-radius: {theme.LAYOUT['radius']}px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        title_label = _plain_label(title)
        title_label.setFont(theme.general_font("label"))
        title_label.setStyleSheet(f"color: {theme.COLORS[color]};")
        detail_label = _plain_label(detail)
        detail_label.setFont(theme.general_font("body"))
        detail_label.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
        layout.addWidget(title_label)
        layout.addWidget(detail_label)
        button = QPushButton(action)
        button.setObjectName("providerRetry" if action == "Retry" else "providerOpenSettings")
        button.setAccessibleName(action)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignRight)
        self._insert_widget(card)
        return button

    def _insert_row(self, row: QHBoxLayout) -> None:
        container = QWidget()
        container.setLayout(row)
        self._insert_widget(container)

    def _insert_widget(self, widget: QWidget) -> None:
        # Chèn trước stretch cuối cùng (luôn nằm ở index cuối của layout) để
        # message mới luôn nối tiếp phía trên, không đẩy stretch ra sai vị trí.
        insert_index = self._messages_layout.count() - 1
        self._messages_layout.insertWidget(insert_index, widget)
        self._message_count += 1
        self._stack.setCurrentIndex(_MESSAGES_PAGE_INDEX)
