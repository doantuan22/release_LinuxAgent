"""IconManager (docs/ui_ux_spec.md mục 6.2) — widget chỉ gọi
`icon("settings")`/`icon("alert-triangle", color="warning")`, không tự đọc
SVG hay tự chọn màu ngoài design system ở từng nơi.

SVG nguồn: Lucide (`lucide-static`, ISC) — xem `resources/icons/NOTICE.md` và
`resources/icons/LICENSE`. Tô màu bằng cách thay `currentColor` trong SVG gốc
bằng mã hex của token màu trước khi render, vì QSvgRenderer không tự resolve
`currentColor` như trình duyệt.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from agent.gui import theme

_RESOURCE_PACKAGE = "agent.gui.resources"
_ICON_SUBDIR = "icons"

# Kích thước theo ngữ cảnh — ui_ux_spec.md mục 6.2.
NAV_ICON_SIZE = 20
BUTTON_ICON_SIZE = 18
INLINE_ICON_SIZE = 16
EMPTY_STATE_ICON_SIZE = 72


def _icons_root():
    return resources.files(_RESOURCE_PACKAGE).joinpath(_ICON_SUBDIR)


def available_icons() -> tuple[str, ...]:
    """Tên icon (không có .svg) thật sự có trong bundle — dùng cho test/gallery."""
    return tuple(
        sorted(
            entry.name.removesuffix(".svg")
            for entry in _icons_root().iterdir()
            if entry.name.endswith(".svg")
        )
    )


@lru_cache(maxsize=None)
def _raw_svg(name: str) -> bytes:
    resource = _icons_root().joinpath(f"{name}.svg")
    if not resource.is_file():
        raise FileNotFoundError(f"Icon không tồn tại trong bundle: {name}")
    return resource.read_bytes()


@lru_cache(maxsize=None)
def _colored_svg(name: str, color_hex: str) -> bytes:
    return _raw_svg(name).decode("utf-8").replace("currentColor", color_hex).encode("utf-8")


def icon(name: str, color: str = "text_secondary", size: int = BUTTON_ICON_SIZE) -> QIcon:
    """QIcon đã tô màu theo token `theme.COLORS` (mặc định --text-secondary).

    `color` là TÊN token (vd "accent", "warning", "danger", "success",
    "text_secondary"), không phải mã hex tự do, để icon luôn nằm trong bảng
    màu design system thay vì mỗi widget tự phối màu riêng.
    """
    try:
        color_hex = theme.COLORS[color]
    except KeyError as exc:
        raise ValueError(f"Unknown theme color token: {color}") from exc

    svg_bytes = _colored_svg(name, color_hex)
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return QIcon(pixmap)
