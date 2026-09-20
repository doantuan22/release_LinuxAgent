"""Chat page (docs/ui_ux_spec.md mục 4.2) — mặc định khi mở app.

Phase 3 chỉ có placeholder tiêu đề/mô tả. Phase 11 dựng shell thật đúng bố
cục: `ConversationView` co giãn ở trên (empty state hoặc danh sách message),
`MessageComposer` cố định ở đáy (mục 8.2: "Input luôn cố định ở đáy content
area") — layout dùng stretch factor (1 cho conversation, 0 cho composer) nên
composer không bao giờ bị đẩy khuất dù resize cửa sổ.

Phase 14 nối Composer vào `ChatController`: tạo session đúng lúc gửi tin đầu,
load history qua service, và hiển thị Tier 1 theo ``ToolEvent.call_id``. Từ
Phase 15, Tier 2 được chuyển qua modal xác nhận không-blocking do MainWindow
điều phối; ChatPage vẫn chỉ render conversation.

`open_session()` (Phase 6, gui_implementation_plan.md "Resume → Chat session
selection") là điểm nối tối thiểu để Sessions page Resume có nơi truyền đúng
session ID đầy đủ sang Chat — controller load history qua service và giữ ID
đó cho turn kế tiếp."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from agent.gui import theme
from agent.gui.controllers.chat_controller import ChatController
from agent.gui.widgets.conversation_view import ConversationView
from agent.gui.widgets.message_composer import MessageComposer
from agent.gui.widgets.sudo_auth_card import SudoAuthCard


class ChatPage(QWidget):
    open_settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None, *, session_db_path=None) -> None:
        super().__init__(parent)
        self._pending_session_id: str | None = None
        self._confirmation_call_ids: dict[str, str] = {}
        self._retry_button = None

        self._outer_layout = QVBoxLayout(self)
        margin = theme.LAYOUT["content_padding"]
        self._outer_layout.setContentsMargins(margin, margin, margin, margin)
        self._outer_layout.setSpacing(12)
        layout = self._outer_layout

        self._conversation = ConversationView()
        self._composer = MessageComposer()
        self._chat_controller = ChatController(self, db_path=session_db_path)
        self._chat_controller.ready_changed.connect(self._composer.set_send_enabled)
        self._composer.send_requested.connect(self._on_send_requested)
        self._composer.stop_requested.connect(self._on_stop_requested)
        self._chat_controller.tool_started.connect(self._on_tool_started)
        self._chat_controller.tool_finished.connect(self._on_tool_finished)
        self._chat_controller.confirmation_requested.connect(self._on_confirmation_requested)
        self._chat_controller.confirmation_resolved.connect(self._on_confirmation_resolved)
        self._chat_controller.sudo_authentication_requested.connect(self._on_sudo_authentication_requested)
        self._chat_controller.provider_state.connect(self._on_provider_state)
        self._chat_controller.response_ready.connect(self._on_response_ready)
        self._chat_controller.error.connect(self._on_error)
        self._chat_controller.configuration_failed.connect(self._on_configuration_failed)
        self._chat_controller.retry_available_changed.connect(self._set_retry_available)
        self._chat_controller.finished.connect(self._on_turn_finished)
        self._chat_controller.history_loaded.connect(self._on_history_loaded)
        self._chat_controller.history_failed.connect(self._on_history_failed)
        self._chat_controller.activate()

        layout.addWidget(self._conversation, 1)
        layout.addWidget(self._composer, 0)

    def open_session(self, session_id: str) -> None:
        self._pending_session_id = session_id
        self._chat_controller.open_session(session_id)

    def pending_session_id(self) -> str | None:
        return self._pending_session_id

    def conversation(self) -> ConversationView:
        """Public accessor cho test — render sample conversation trực tiếp
        (dữ liệu tĩnh dựng sẵn trong test), chưa qua luồng gửi thật."""
        return self._conversation

    def composer(self) -> MessageComposer:
        """Public accessor cho test — đọc/kiểm tra input và nút Send."""
        return self._composer

    def chat_controller(self) -> ChatController:
        """Accessor cho Phase 13 test; Phase 14 sở hữu interactive send wiring."""
        return self._chat_controller

    def apply_responsive(self, *, compact: bool, window_width: int, short_height: bool) -> None:
        """ui_ux_spec.md mục 8.1/8.2/8.7 — `MainWindow.resizeEvent()` gọi
        (duck-typed) mỗi lần đổi kích thước. Composer không có margin riêng
        (mục 8.2: "compact chỉ giảm margin hai bên") — margin đó chính là
        content padding của page, giảm ở đây là đủ, không cần sửa
        `MessageComposer`. Sudo card (mục 8.7) là widget con nằm sâu trong
        `ConversationView`/`ToolStatus`, không có accessor riêng cho từng
        card đang hiển thị nên cập nhật qua `findChildren`."""
        margin = theme.LAYOUT["content_padding_compact"] if compact else theme.LAYOUT["content_padding"]
        self._outer_layout.setContentsMargins(margin, margin, margin, margin)
        self._conversation.set_compact(compact)
        self._conversation.set_short_height(short_height)
        for card in self.findChildren(SudoAuthCard):
            card.apply_responsive_width(window_width)

    def new_chat(self) -> None:
        """Reset presentation/session selection without creating an empty session."""
        if self._chat_controller.is_busy():
            return
        self._pending_session_id = None
        self._chat_controller.new_chat()
        self._retry_button = None
        self._conversation.clear()
        self._composer.clear()
        self._composer.set_send_enabled(self._chat_controller.is_ready())

    def _on_send_requested(self, user_message: str) -> None:
        # The controller creates a DB session only here (on an actual non-empty
        # send), never while this page is constructed or opened.
        if not self._chat_controller.send_message(user_message):
            return
        self._conversation.add_user_message(user_message)
        self._composer.clear()
        # Disable immediately, before the queued TurnStarted event returns, so a
        # double click/Enter cannot create a concurrent second turn.
        self._composer.set_send_enabled(False)
        self._composer.set_busy(True)

    def _on_stop_requested(self) -> None:
        # Cooperative only: denies pending confirmation/sudo waiters and
        # cancels the turn's token. Never kills the worker or an in-flight
        # tool call; the loop reacts at its next checkpoint (core/loop.py).
        self._chat_controller.request_stop()

    def _on_history_loaded(self, messages: list[object]) -> None:
        self._retry_button = None
        self._conversation.clear()
        for message in messages:
            display_message = self._chat_controller.history_message_for_display(message)
            if display_message is None:
                continue
            role, content = display_message
            if role == "user":
                self._conversation.add_user_message(content)
            else:
                self._conversation.add_assistant_message(content)

    def _on_history_failed(self) -> None:
        self._conversation.add_status_message("Could not load session history.", variant="error")

    def _on_tool_started(self, event: object) -> None:
        tool = self._chat_controller.tier1_tool_started(event)
        if tool is not None:
            call_id, tool_name = tool
            self._conversation.start_tool_status(call_id, tool_name)
            return
        tier2 = self._chat_controller.tier2_tool_started(event)
        if tier2 is None:
            return
        call_id, tool_name = tier2
        self._conversation.start_tool_status(
            call_id, tool_name, tier_label="Tier 2", tier_variant="tier2", initial_status="Running",
        )
        self._conversation.set_tool_phase(call_id, "Running")
        self._conversation.remove_sudo_auth_card(call_id)

    def _on_tool_finished(self, event: object) -> None:
        tool = self._chat_controller.tier1_tool_finished(event)
        if tool is not None:
            call_id, state = tool
            self._conversation.finish_tool_status(call_id, state)
            return
        tier2 = self._chat_controller.tier2_tool_finished(event)
        if tier2 is not None:
            call_id, state = tier2
            self._conversation.finish_tool_status(call_id, state)

    def _on_confirmation_requested(self, prompt: object) -> None:
        call_id = getattr(prompt, "call_id", None)
        tool_name = getattr(prompt, "tool_name", None)
        request_id = getattr(prompt, "request_id", None)
        if not all(isinstance(value, str) and value for value in (call_id, tool_name, request_id)):
            return
        self._confirmation_call_ids[request_id] = call_id
        self._conversation.start_tool_status(
            call_id, tool_name, tier_label="Tier 2", tier_variant="tier2", initial_status="Waiting for confirmation",
        )

    def _on_confirmation_resolved(self, request_id: str, allowed: bool) -> None:
        call_id = self._confirmation_call_ids.pop(request_id, None)
        if call_id is None:
            return
        if allowed:
            self._conversation.set_tool_phase(call_id, "Approved", color="success")
        else:
            # The worker will shortly forward the authoritative DENIED event;
            # render only presentation text here so this widget never imports
            # core types directly.
            self._conversation.set_tool_phase(call_id, "Denied", color="danger")

    def _on_sudo_authentication_requested(self, prompt: object) -> None:
        call_id = getattr(prompt, "call_id", None)
        tool_name = getattr(prompt, "tool_name", None)
        request_id = getattr(prompt, "request_id", None)
        still_unavailable = bool(getattr(prompt, "still_unavailable", False))
        if not all(isinstance(value, str) and value for value in (call_id, tool_name, request_id)):
            return
        self._conversation.start_tool_status(
            call_id, tool_name, tier_label="Tier 2", tier_variant="tier2", initial_status="Waiting for sudo authentication",
        )
        self._conversation.set_tool_phase(call_id, "Waiting for sudo authentication", color="warning")
        status = self._conversation.tool_status(call_id)
        if status is None:
            return
        card = status.sudo_card()
        if card is None:
            card = SudoAuthCard(call_id)
            card.retry_requested.connect(lambda: self._retry_sudo_card(card))
            card.dismiss_requested.connect(lambda: self._dismiss_sudo_card(card))
            self._conversation.set_sudo_auth_card(call_id, card)
        card.bind_request(request_id, still_unavailable=still_unavailable)

    def _retry_sudo_card(self, card: SudoAuthCard) -> None:
        if card.request_id:
            self._chat_controller.retry_sudo_authentication(card.request_id)

    def _dismiss_sudo_card(self, card: SudoAuthCard) -> None:
        if card.request_id:
            self._chat_controller.dismiss_sudo_authentication(card.request_id)

    def _on_provider_state(self, event: object) -> None:
        # These concrete Phase 12 types are deliberately distinct: offline is
        # not treated as a successful provider fallback.
        if self._chat_controller.is_provider_fallback_used(event):
            provider_name = self._chat_controller.fallback_provider_name(event) or "fallback provider"
            self._conversation.add_status_message(
                f"Primary provider unavailable · using {provider_name}", variant="warning"
            )
        else:
            category = self._chat_controller.provider_failure_category(event)
            if category is not None:
                self._add_provider_error(category)
                return
            offline_reason = self._chat_controller.offline_fallback_reason(event)
            if offline_reason is None:
                return
            reason = (
                "Provider unavailable · using local Linux documentation."
                if offline_reason == "provider_failure"
                else "Using local Linux documentation because the cost limit was reached."
            )
            self._conversation.add_status_message(reason, variant="warning")

    def _on_response_ready(self, event: object) -> None:
        # Cancelled (ui_ux_spec.md mục 10: muted text, not a system failure) is
        # deliberately never persisted as a fake completed answer — assistant_text
        # is always None for this outcome, so it is checked before that branch.
        if self._chat_controller.turn_outcome_is_cancelled(event):
            self._conversation.add_status_message("Cancelled", variant="muted")
            return
        # The offline answer is separate from the error/status card above.
        assistant_text = self._chat_controller.turn_assistant_text(event)
        if assistant_text:
            self._conversation.add_assistant_message(assistant_text)

    def _on_error(self, category: object) -> None:
        self._add_provider_error(category)

    def _on_turn_finished(self) -> None:
        self._composer.set_send_enabled(self._chat_controller.is_ready())
        self._composer.set_busy(False)

    def _set_retry_available(self, available: bool) -> None:
        if self._retry_button is not None:
            self._retry_button.setEnabled(available)

    def _retry_provider(self) -> None:
        if self._chat_controller.retry_provider():
            self._composer.set_send_enabled(False)
            self._composer.set_busy(True)

    def _on_configuration_failed(self) -> None:
        self._set_retry_available(False)
        self._retry_button = None
        button = self._conversation.add_provider_error(
            "Provider configuration unavailable", "Open Settings to configure a supported provider.",
            action="Open Settings",
        )
        button.clicked.connect(self.open_settings_requested.emit)

    def _add_provider_error(self, category: object) -> None:
        self._set_retry_available(False)
        self._retry_button = None
        title, detail = self._chat_controller.provider_error_copy(category)
        warning, action = self._chat_controller.provider_error_presentation(category)
        button = self._conversation.add_provider_error(
            title, detail, warning=warning, action=action,
        )
        if action == "Open Settings":
            button.clicked.connect(self.open_settings_requested.emit)
        else:
            self._retry_button = button
            button.setEnabled(False)  # enabled only once the worker seals its snapshot
            button.clicked.connect(self._retry_provider)

    def shutdown(self) -> None:
        """MainWindow.closeEvent() gọi method này qua duck typing đã có."""
        self._chat_controller.shutdown()
