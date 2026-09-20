"""Audit page thật (docs/ui_ux_spec.md mục 4.5, gui_implementation_plan.md
Phase 8) — filter tab All/Tier 1/Tier 2/Denied/Failed + bảng đúng 5 cột
Time/Tier/Tool/Result/Duration (`AuditTable`), nút Refresh (không có live
follow liên tục — refresh thủ công đủ cho v1, đúng "Không làm trong phase
này").

Page chỉ gọi `AuditController`, không tự import tầng service hay đọc/parse
JSONL — controller/worker mới là nơi chạm backend (ranh giới presentation/
orchestration của gui_implementation_plan.md). 5 filter lọc trên field đã
chuẩn hoá (`tier`/`result`) mà tầng service đã tính sẵn (Phase 7), không tự
suy diễn lại phân loại denied/failed ở đây.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from agent.gui import theme
from agent.gui.controllers.audit_controller import AuditController
from agent.gui.resources import icon_manager
from agent.gui.widgets.audit_table import AuditTable
from agent.gui.widgets.buttons import SecondaryButton

# (filter key, nhãn hiển thị) — đúng thứ tự ui_ux_spec.md mục 4.5.
_FILTERS: tuple[tuple[str, str], ...] = (
    ("all", "All"),
    ("tier1", "Tier 1"),
    ("tier2", "Tier 2"),
    ("denied", "Denied"),
    ("failed", "Failed"),
)
_DEFAULT_FILTER = _FILTERS[0][0]

# Bounded tail — không đọc toàn bộ file audit không giới hạn.
_DEFAULT_TAIL_LINES = 200

_LOADING_STATUS = "⟳ Loading..."
_MISSING_STATUS = "Audit log chưa tồn tại."
_EMPTY_STATUS = "Audit log đang rỗng."
_NO_MATCH_STATUS = "Không có entry nào khớp bộ lọc."
_ERROR_STATUS = "Không thể đọc audit log."


def _matches_filter(record: Any, filter_key: str) -> bool:
    """Lọc trên `record.tier`/`record.result` — đã được `audit_service`
    chuẩn hoá/phân loại sẵn (Phase 7), không tự đoán lại ở đây."""
    if filter_key == "tier1":
        return record.tier == "tier_1_readonly"
    if filter_key == "tier2":
        return record.tier == "tier_2_action"
    if filter_key == "denied":
        return record.result == "denied"
    if filter_key == "failed":
        return record.result == "error"
    return True  # "all"


class AuditPage(QWidget):
    def __init__(self, parent: QWidget | None = None, *, log_path: Path | None = None) -> None:
        super().__init__(parent)
        self._controller = AuditController(log_path, self)
        self._all_records: list[Any] = []
        self._malformed_count = 0
        self._active_filter = _DEFAULT_FILTER

        self._outer_layout = QVBoxLayout(self)
        margin = theme.LAYOUT["content_padding"]
        self._outer_layout.setContentsMargins(margin, margin, margin, margin)
        outer = self._outer_layout

        self._card = QFrame()
        card = self._card
        card.setStyleSheet(
            f"QFrame {{ background-color: {theme.COLORS['bg_card']}; "
            f"border: {theme.LAYOUT['border_width']}px solid {theme.COLORS['border']}; "
            f"border-radius: {theme.LAYOUT['radius']}px; }}"
        )
        self._card_layout = QVBoxLayout(card)
        card_layout = self._card_layout
        card_layout.setContentsMargins(margin, margin, margin, margin)

        header = QHBoxLayout()
        title = QLabel("Audit")
        title.setFont(theme.general_font("card_title"))
        title.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
        self._status_label = QLabel("")
        self._status_label.setFont(theme.general_font("label"))
        self._refresh_button = SecondaryButton("Refresh", icon=icon_manager.icon("refresh-cw"))
        self._refresh_button.clicked.connect(self._on_refresh_clicked)
        header.addWidget(title)
        header.addWidget(self._status_label)
        header.addStretch(1)
        header.addWidget(self._refresh_button)

        filter_row = QHBoxLayout()
        self._filter_buttons: dict[str, QPushButton] = {}
        radius = theme.LAYOUT["radius"]
        for key, label in _FILTERS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setFlat(True)
            button.setFont(theme.general_font("nav_item"))
            button.setStyleSheet(
                "QPushButton { border: 2px solid transparent; background: transparent; "
                f"color: {theme.COLORS['text_secondary']}; border-radius: {radius}px; "
                "padding: 6px 14px; margin-right: 4px; }"
                "QPushButton:checked { background-color: "
                f"{theme.COLORS['accent_bg']}; color: {theme.COLORS['accent']}; }}"
                f"QPushButton:focus {{ border: 2px solid {theme.COLORS['accent']}; }}"
            )
            button.clicked.connect(lambda _checked=False, k=key: self._on_filter_clicked(k))
            self._filter_buttons[key] = button
            filter_row.addWidget(button)
        filter_row.addStretch(1)
        self._filter_buttons[_DEFAULT_FILTER].setChecked(True)

        # ui_ux_spec.md mục 8.4: "Filter ... nếu không đủ chiều rộng thì trở
        # thành horizontal-scroll tab bar, không xuống dòng thành hai hàng."
        # QHBoxLayout tự nó không bao giờ wrap xuống 2 hàng; bọc trong
        # QScrollArea để phần overflow scroll ngang thay vì bị cắt/đẩy widget
        # cha rộng ra ngoài viewport.
        filter_container = QWidget()
        filter_container.setLayout(filter_row)
        self._filter_scroll = QScrollArea()
        self._filter_scroll.setWidget(filter_container)
        self._filter_scroll.setWidgetResizable(True)
        self._filter_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._filter_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._filter_scroll.setFixedHeight(filter_container.sizeHint().height())

        self._table = AuditTable()

        card_layout.addLayout(header)
        card_layout.addWidget(self._filter_scroll)
        card_layout.addWidget(self._table)

        outer.addWidget(card)

        self._controller.loading_changed.connect(self._on_loading_changed)
        self._controller.tail_loaded.connect(self._on_tail_loaded)
        self._controller.tail_failed.connect(self._on_tail_failed)

        self._controller.refresh(count=_DEFAULT_TAIL_LINES)

    def status_text(self) -> str:
        return self._status_label.text()

    def table(self) -> AuditTable:
        """Public accessor cho test — tương tác trực tiếp với bảng (đọc
        cell) mà không cần biết cấu trúc widget nội bộ."""
        return self._table

    def filter_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _label in _FILTERS)

    def filter_button(self, key: str) -> QPushButton:
        return self._filter_buttons[key]

    def active_filter(self) -> str:
        return self._active_filter

    def refresh_button(self) -> SecondaryButton:
        return self._refresh_button

    def filter_scroll_area(self) -> QScrollArea:
        """Public accessor cho test — kiểm scrollbar policy/khả năng scroll
        ngang của hàng filter ở compact (mục 8.4)."""
        return self._filter_scroll

    def apply_responsive(self, *, compact: bool, window_width: int, short_height: bool) -> None:
        """ui_ux_spec.md mục 8.1/8.4."""
        page_margin = theme.LAYOUT["content_padding_compact"] if compact else theme.LAYOUT["content_padding"]
        self._outer_layout.setContentsMargins(page_margin, page_margin, page_margin, page_margin)
        self._card_layout.setContentsMargins(page_margin, page_margin, page_margin, page_margin)
        self._card_layout.setSpacing(theme.LAYOUT["header_spacing_short" if short_height else "header_spacing"])
        self._table.set_compact(compact)

    def is_busy(self) -> bool:
        return self._controller.is_busy()

    def controller(self) -> AuditController:
        """Public accessor so `MainWindow.closeEvent()` can wait on this
        page's own busy state cooperatively (same idiom as `ChatPage.
        chat_controller()`/`SystemPage.controller()`/`SettingsPage.
        controller()`) instead of only ever knowing about it via
        `shutdown()`'s blocking wait."""
        return self._controller

    def shutdown(self) -> None:
        """`MainWindow.closeEvent()` tự gọi qua duck-typing (Phase 4) — chỉ
        còn là drain nhanh, không busy: `MainWindow.closeEvent()` không bao
        giờ gọi hàm này khi `is_busy()` còn true nữa (xem `AuditController.
        shutdown()` — lý do là bug đã đo được: blocking `wait()` ở đây từng
        khiến `MainWindow.close()` đứng hình đúng `_SHUTDOWN_WAIT_MS` [đo
        được 6.001s] rồi phá worker đang chạy dở)."""
        self._controller.shutdown()

    def _on_refresh_clicked(self) -> None:
        self._controller.refresh(count=_DEFAULT_TAIL_LINES)

    def _on_filter_clicked(self, key: str) -> None:
        self._active_filter = key
        self._apply_filter()

    def _on_loading_changed(self, loading: bool) -> None:
        self._refresh_button.setEnabled(not loading)
        if loading:
            self._status_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
            self._status_label.setText(_LOADING_STATUS)

    def _on_tail_loaded(self, result: Any) -> None:
        if result.status == "missing":
            self._set_records_and_status([], 0, _MISSING_STATUS, "text_secondary")
            return
        if result.status == "read_error":
            self._set_records_and_status([], 0, _ERROR_STATUS, "danger")
            return

        self._all_records = result.entries
        self._malformed_count = result.malformed_count
        self._apply_filter()

    def _on_tail_failed(self, _message: str) -> None:
        # Không render `_message` (chuỗi exception thô) — chỉ trạng thái chung.
        self._set_records_and_status([], 0, _ERROR_STATUS, "danger")

    def _set_records_and_status(self, records: list[Any], malformed: int, status: str, color_token: str) -> None:
        self._all_records = records
        self._malformed_count = malformed
        self._table.set_records(records)
        self._status_label.setStyleSheet(f"color: {theme.COLORS[color_token]};")
        self._status_label.setText(status)

    def _apply_filter(self) -> None:
        filtered = [record for record in self._all_records if _matches_filter(record, self._active_filter)]
        self._table.set_records(filtered)

        if self._malformed_count:
            self._status_label.setStyleSheet(f"color: {theme.COLORS['warning']};")
            self._status_label.setText(f"Skipped {self._malformed_count} malformed audit line(s).")
        elif not self._all_records:
            self._status_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
            self._status_label.setText(_EMPTY_STATUS)
        elif not filtered:
            self._status_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
            self._status_label.setText(_NO_MATCH_STATUS)
        else:
            self._status_label.setText("")
