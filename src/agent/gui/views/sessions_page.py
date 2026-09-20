"""Sessions page thật (docs/ui_ux_spec.md mục 4.3, gui_implementation_plan.md
Phase 6) — bảng session (`SessionTable`) + panel xem lại nội dung của session
đang chọn, nút Resume mỗi dòng phát `resume_requested(session_id)` với ID đầy
đủ để `MainWindow` chuyển sang Chat page.

Page chỉ gọi `SessionsController`, không tự import tầng service/store hay
kiểu dữ liệu Message/Role của lớp provider — controller mới là nơi chạm
backend (ranh giới presentation/orchestration của gui_implementation_plan.md).
KHÔNG bao giờ tạo session mới ở đây: mở page này chỉ đọc/list (bất biến ở
docstring của hàm tạo session mới trong tầng service) — không có nút "New"
nào ở Phase 6, và `SessionsController` không có method nào gọi tới hàm đó.

Nội dung history hiển thị trong `QPlainTextEdit` (readOnly) — widget này
luôn coi input là plain text thuần, không có API nào để nó diễn giải HTML,
nên dữ liệu untrusted (title/nội dung message do người dùng/LLM tạo) không
thể bị "thực thi" như markup dù không tự escape thủ công.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent.gui import theme
from agent.gui.controllers.sessions_controller import SessionsController
from agent.gui.widgets.session_table import SessionTable

_EMPTY_STATUS = "Chưa có session nào."
_ERROR_STATUS = "Session database không đọc được hoặc schema không tương thích."
_TRUNCATED_STATUS = "Hiển thị {limit} session gần nhất."
_DETAIL_PLACEHOLDER = "Chọn một session để xem lại nội dung."


def _card(title: str) -> tuple[QFrame, QVBoxLayout, QLabel]:
    card = QFrame()
    card.setStyleSheet(
        f"QFrame {{ background-color: {theme.COLORS['bg_card']}; "
        f"border: {theme.LAYOUT['border_width']}px solid {theme.COLORS['border']}; "
        f"border-radius: {theme.LAYOUT['radius']}px; }}"
    )
    layout = QVBoxLayout(card)
    margin = theme.LAYOUT["content_padding"]
    layout.setContentsMargins(margin, margin, margin, margin)

    header = QHBoxLayout()
    title_label = QLabel(title)
    title_label.setFont(theme.general_font("card_title"))
    title_label.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
    status_label = QLabel("")
    status_label.setFont(theme.general_font("label"))
    header.addWidget(title_label)
    header.addStretch(1)
    header.addWidget(status_label)
    layout.addLayout(header)

    return card, layout, status_label


class SessionsPage(QWidget):
    resume_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, db_path: Path | None = None) -> None:
        super().__init__(parent)
        self._controller = SessionsController(db_path, self)

        self._outer_layout = QVBoxLayout(self)
        margin = theme.LAYOUT["content_padding"]
        self._outer_layout.setContentsMargins(margin, margin, margin, margin)
        self._outer_layout.setSpacing(theme.LAYOUT["card_gap"])
        outer = self._outer_layout

        list_card, list_layout, self._status_label = _card("Sessions")
        self._table = SessionTable()
        list_layout.addWidget(self._table)

        detail_card, detail_layout, self._detail_status_label = _card("History")
        self._detail_view = QPlainTextEdit()
        self._detail_view.setReadOnly(True)
        self._detail_view.setFont(theme.fixed_font())
        self._detail_view.setPlainText(_DETAIL_PLACEHOLDER)
        detail_layout.addWidget(self._detail_view)

        self._header_layouts = (list_layout, detail_layout)
        outer.addWidget(list_card, 2)
        outer.addWidget(detail_card, 1)

        self._table.resume_requested.connect(self.resume_requested.emit)
        self._table.session_selected.connect(self._controller.load_detail)
        self._controller.sessions_loaded.connect(self._on_sessions_loaded)
        self._controller.sessions_truncated.connect(self._on_sessions_truncated)
        self._controller.sessions_failed.connect(self._on_sessions_failed)
        self._controller.detail_loaded.connect(self._on_detail_loaded)
        self._controller.detail_failed.connect(self._on_detail_failed)

        self._controller.refresh()

    def status_text(self) -> str:
        return self._status_label.text()

    def detail_text(self) -> str:
        return self._detail_view.toPlainText()

    def session_count(self) -> int:
        return self._table.rowCount()

    def table(self) -> SessionTable:
        """Public accessor cho test — tương tác trực tiếp với bảng (chọn
        dòng, bấm Resume) mà không cần biết cấu trúc widget nội bộ."""
        return self._table

    def refresh(self) -> None:
        self._controller.refresh()

    def is_busy(self) -> bool:
        return self._controller.is_busy()

    def controller(self) -> SessionsController:
        """Public accessor để `MainWindow.closeEvent()` chờ đúng trạng thái
        busy của page này (cùng idiom `AuditPage.controller()`)."""
        return self._controller

    def shutdown(self) -> None:
        """`MainWindow.closeEvent()` gọi qua duck-typing; chỉ drain nhanh."""
        self._controller.shutdown()

    def apply_responsive(self, *, compact: bool, window_width: int, short_height: bool) -> None:
        """ui_ux_spec.md mục 8.1/8.3."""
        margin = theme.LAYOUT["content_padding_compact"] if compact else theme.LAYOUT["content_padding"]
        gap = theme.LAYOUT["card_gap_compact"] if compact else theme.LAYOUT["card_gap"]
        self._outer_layout.setContentsMargins(margin, margin, margin, margin)
        self._outer_layout.setSpacing(gap)
        for layout in self._header_layouts:
            layout.setSpacing(theme.LAYOUT["header_spacing_short" if short_height else "header_spacing"])
        self._table.set_compact(compact)

    def _on_sessions_loaded(self, records: list) -> None:
        self._table.set_sessions(records)
        self._status_label.setText(_EMPTY_STATUS if not records else "")
        self._status_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")

    def _on_sessions_truncated(self, limit: int) -> None:
        # Phát sau sessions_loaded (đã đặt status về rỗng) nên ghi đè ở đây là đúng.
        self._status_label.setText(_TRUNCATED_STATUS.format(limit=limit))
        self._status_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")

    def _on_sessions_failed(self, _message: str) -> None:
        self._table.set_sessions([])
        self._status_label.setStyleSheet(f"color: {theme.COLORS['danger']};")
        self._status_label.setText(_ERROR_STATUS)

    def _on_detail_loaded(self, text: str) -> None:
        self._detail_status_label.setText("")
        self._detail_view.setPlainText(text)

    def _on_detail_failed(self, _message: str) -> None:
        self._detail_status_label.setStyleSheet(f"color: {theme.COLORS['danger']};")
        self._detail_status_label.setText(_ERROR_STATUS)
        self._detail_view.setPlainText(_DETAIL_PLACEHOLDER)
