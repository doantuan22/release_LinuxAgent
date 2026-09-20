"""One inline, mutable status row for a single safe tool call (Phase 14)."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from agent.core.executor import ToolOutcomeState
from agent.gui import theme
from agent.gui.resources import icon_manager
from agent.gui.widgets.badge import Badge
from agent.gui.widgets.sudo_auth_card import SudoAuthCard


class ToolStatus(QWidget):
    """Status bound to the immutable ``ToolEvent.call_id`` from Phase 12."""

    def __init__(
        self, call_id: str, tool_name: str, parent: QWidget | None = None, *,
        tier_label: str = "Tier 1", tier_variant: str = "tier1", initial_status: str = "Running",
    ) -> None:
        super().__init__(parent)
        self.call_id = call_id
        self.tool_name = tool_name
        self._state: ToolOutcomeState | None = None

        icon_label = QLabel()
        icon_size = icon_manager.INLINE_ICON_SIZE
        icon_label.setPixmap(icon_manager.icon("terminal", size=icon_size).pixmap(icon_size, icon_size))

        name_label = QLabel(tool_name)
        name_label.setFont(theme.general_font("label"))
        name_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")

        self._status_label = QLabel(initial_status)
        self._status_label.setFont(theme.general_font("label"))
        self._status_label.setStyleSheet(f"color: {theme.COLORS['text_muted']};")

        header = QHBoxLayout()
        header.setSpacing(6)
        header.addWidget(icon_label)
        header.addWidget(name_label)
        header.addWidget(Badge(tier_label, tier_variant))
        header.addWidget(self._status_label)
        header.addStretch(1)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._layout.addLayout(header)
        self._sudo_card: SudoAuthCard | None = None

    def state(self) -> ToolOutcomeState | None:
        return self._state

    def status_text(self) -> str:
        return self._status_label.text()

    def set_outcome(self, state: ToolOutcomeState) -> None:
        self._state = state
        text, color = {
            ToolOutcomeState.OK: ("Completed", "success"),
            ToolOutcomeState.DENIED: ("Denied", "danger"),
            ToolOutcomeState.ERROR: ("Failed", "danger"),
            ToolOutcomeState.AUTHENTICATION_REQUIRED: ("Waiting for sudo authentication", "warning"),
        }[state]
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {theme.COLORS[color]};")

    def set_phase(self, text: str, *, color: str = "text_muted") -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {theme.COLORS[color]};")

    def set_sudo_card(self, card: SudoAuthCard) -> None:
        if self._sudo_card is not None:
            self._layout.removeWidget(self._sudo_card)
            self._sudo_card.deleteLater()
        self._sudo_card = card
        self._layout.addWidget(card)

    def remove_sudo_card(self) -> None:
        if self._sudo_card is None:
            return
        self._layout.removeWidget(self._sudo_card)
        self._sudo_card.deleteLater()
        self._sudo_card = None

    def sudo_card(self) -> SudoAuthCard | None:
        return self._sudo_card
