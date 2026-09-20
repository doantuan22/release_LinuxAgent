"""SessionTable (docs/ui_ux_spec.md mục 4.3, gui_implementation_plan.md
Phase 6) — bảng ID/Title/Created/Updated/System + nút Resume mỗi dòng. Widget
chỉ nhận `SessionRecord` đã có sẵn qua `set_sessions()`, không tự đọc DB —
`SessionsPage`/`SessionsController` mới là nơi gọi `session_service`.

ID hiển thị rút gọn 8 ký tự (giống `agent sessions list`) nhưng ID đầy đủ
luôn được lưu ở `Qt.ItemDataRole.UserRole` của cell ID — `resume_requested`/
`session_selected` luôn phát ID đầy đủ, không phải phần rút gọn hiển thị, để
tránh trùng khi hai session tình cờ cùng 8 ký tự đầu (CLI phải tự xử lý
ambiguous vì người dùng gõ tay id rút gọn; GUI có sẵn ID đầy đủ trong record
nên không cần đoán/resolve lại).

`title`/`system` là dữ liệu untrusted (do người dùng/LLM đặt), nhưng
`QTableWidgetItem` luôn hiển thị text thuần qua item delegate mặc định của
Qt — không có đường nào để nội dung bị diễn giải thành rich text/HTML.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from agent.gui import theme
from agent.gui.widgets.buttons import SecondaryButton
from agent.memory.schemas import SessionRecord

_COLUMN_LABELS = ("ID", "Title", "Created", "Updated", "System", "")
_ID_COLUMN = 0
_TITLE_COLUMN = 1
_CREATED_COLUMN = 2
_RESUME_COLUMN = 5
# ui_ux_spec.md mục 8.3 — Compact ẩn ID và Created, còn lại Title/Updated/
# System/Resume. Dữ liệu vẫn còn (không xóa cell) — chỉ ẩn cột, ID/Created
# vẫn xem được qua session detail (không mất dữ liệu, chỉ ẩn hiển thị).
_COMPACT_HIDDEN_COLUMNS = (_ID_COLUMN, _CREATED_COLUMN)


def _format_timestamp(value: str) -> str:
    """Cùng định dạng với `commands/sessions.py::_format_timestamp` — chuyển
    UTC (naive coi như UTC) sang giờ địa phương, không đoán format khác nếu
    parse lỗi."""
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    except ValueError:
        return value


class SessionTable(QTableWidget):
    resume_requested = Signal(str)
    session_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(_COLUMN_LABELS), parent)
        self._compact = False
        self.setHorizontalHeaderLabels(_COLUMN_LABELS)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setWordWrap(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(_TITLE_COLUMN, QHeaderView.ResizeMode.Stretch)
        self.setFont(theme.general_font("body"))
        self.currentCellChanged.connect(self._on_current_cell_changed)

    def set_compact(self, compact: bool) -> None:
        """ui_ux_spec.md mục 8.3 — ẩn cột ID/Created ở Compact, không ép chữ
        xuống dòng; nếu vẫn không đủ rộng, policy ScrollBarAsNeeded đã khai
        báo trong constructor cho phép cuộn ngang."""
        if compact == self._compact:
            return
        self._compact = compact
        for column in _COMPACT_HIDDEN_COLUMNS:
            self.setColumnHidden(column, compact)

    def is_compact(self) -> bool:
        return self._compact

    def set_sessions(self, records: list[SessionRecord]) -> None:
        self.clearSelection()
        self.setRowCount(len(records))
        for row, record in enumerate(records):
            id_item = QTableWidgetItem(record.id[:8])
            id_item.setData(Qt.ItemDataRole.UserRole, record.id)
            self.setItem(row, _ID_COLUMN, id_item)
            self.setItem(row, _TITLE_COLUMN, QTableWidgetItem(record.title))
            self.setItem(row, 2, QTableWidgetItem(_format_timestamp(record.created_at)))
            self.setItem(row, 3, QTableWidgetItem(_format_timestamp(record.updated_at)))
            self.setItem(
                row, 4, QTableWidgetItem(f"{record.distro_at_creation} {record.distro_version_at_creation}")
            )

            resume_button = SecondaryButton("Resume")
            resume_button.clicked.connect(lambda _checked=False, sid=record.id: self.resume_requested.emit(sid))
            self.setCellWidget(row, _RESUME_COLUMN, resume_button)

    def full_session_id(self, row: int) -> str | None:
        """Public accessor cho test — ID đầy đủ của dòng `row` (không phải
        chuỗi 8 ký tự hiển thị ở cell ID)."""
        item = self.item(row, _ID_COLUMN)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def resume_button(self, row: int) -> SecondaryButton:
        widget = self.cellWidget(row, _RESUME_COLUMN)
        assert isinstance(widget, SecondaryButton)
        return widget

    def _on_current_cell_changed(self, current_row: int, *_rest: int) -> None:
        if current_row < 0:
            return
        session_id = self.full_session_id(current_row)
        if session_id is not None:
            self.session_selected.emit(session_id)
