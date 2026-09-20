"""AuditTable (docs/ui_ux_spec.md mục 4.5, gui_implementation_plan.md
Phase 8) — đúng 5 cột Time/Tier/Tool/Result/Duration. Tier/Result hiển thị
`Badge` theo đúng quy ước màu mục 2.5 (Tier 1 = accent, Tier 2 = warning,
Success = success, Denied/Failed = danger) — không tự chọn màu khác.

Widget chỉ nhận `AuditRecord` đã chuẩn hoá qua `set_records()` (Phase 7:
`audit_service` chỉ expose timestamp/tier/tool_name/event/result/
duration_ms), không tự đọc/parse JSONL hay hiển thị field nào khác ngoài 5
cột này dù `AuditRecord` còn field `event`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from agent.gui import theme
from agent.gui.widgets.badge import Badge
from agent.services.audit_service import AuditRecord

_COLUMN_LABELS = ("Time", "Tier", "Tool", "Result", "Duration")
_TIME_COLUMN = 0
_TIER_COLUMN = 1
_TOOL_COLUMN = 2
_RESULT_COLUMN = 3
_DURATION_COLUMN = 4

# (nhãn badge, variant trong theme.BADGES) — đúng quy ước ui_ux_spec.md mục 2.5.
_TIER_BADGE: dict[str, tuple[str, str]] = {
    "tier_1_readonly": ("Tier 1", "tier1"),
    "tier_2_action": ("Tier 2", "tier2"),
}
_RESULT_BADGE: dict[str, tuple[str, str]] = {
    "success": ("Success", "success"),
    "denied": ("Denied", "danger"),
    "error": ("Failed", "danger"),
}


def _format_timestamp(value: str | None) -> str:
    if value is None:
        return "-"
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone().strftime("%H:%M:%S %Z")
    except ValueError:
        return "-"


def _format_duration(value: float | None) -> str:
    if value is None:
        return "-"
    if value.is_integer():
        return f"{int(value)}ms"
    return f"{value:.1f}ms"


def _plain_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFont(theme.general_font("body"))
    return item


class AuditTable(QTableWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(_COLUMN_LABELS), parent)
        self._compact = False
        self.setHorizontalHeaderLabels(_COLUMN_LABELS)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(_TOOL_COLUMN, QHeaderView.ResizeMode.Stretch)
        self.setFont(theme.general_font("body"))

    def set_compact(self, compact: bool) -> None:
        """ui_ux_spec.md mục 8.4 — Compact ẩn Duration trước tiên (còn Time/
        Tier/Tool/Result), không ép chữ xuống dòng."""
        if compact == self._compact:
            return
        self._compact = compact
        self.setColumnHidden(_DURATION_COLUMN, compact)

    def is_compact(self) -> bool:
        return self._compact

    def set_records(self, records: list[AuditRecord]) -> None:
        self.setRowCount(len(records))
        for row, record in enumerate(records):
            self.setItem(row, _TIME_COLUMN, _plain_item(_format_timestamp(record.timestamp)))
            self._set_tier_cell(row, record.tier)
            self.setItem(row, _TOOL_COLUMN, _plain_item(record.tool_name))
            self._set_result_cell(row, record.result)
            self.setItem(row, _DURATION_COLUMN, _plain_item(_format_duration(record.duration_ms)))

    def _set_tier_cell(self, row: int, tier: str | None) -> None:
        badge_spec = _TIER_BADGE.get(tier) if tier is not None else None
        if badge_spec is None:
            self.setItem(row, _TIER_COLUMN, _plain_item("-"))
            return
        label, variant = badge_spec
        self.setCellWidget(row, _TIER_COLUMN, Badge(label, variant))

    def _set_result_cell(self, row: int, result: str) -> None:
        badge_spec = _RESULT_BADGE.get(result)
        if badge_spec is None:
            # "started"/"unknown" chưa có variant badge riêng trong mục 2.5
            # — hiển thị text thuần thay vì tự chọn màu ngoài bảng token.
            self.setItem(row, _RESULT_COLUMN, _plain_item(result.capitalize()))
            return
        label, variant = badge_spec
        self.setCellWidget(row, _RESULT_COLUMN, Badge(label, variant))
