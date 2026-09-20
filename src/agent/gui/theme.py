"""Design system tokens (docs/ui_ux_spec.md mục 2, 6.1) — nguồn duy nhất cho
màu/typography/layout GUI. Widget phải đọc token ở đây thay vì tự hardcode
mã màu/kích thước rải rác, để đổi palette hay thêm Dark Mode sau này chỉ sửa
một chỗ (gui_implementation_plan.md Phase 2).

Chỉ light theme trong Phase 2 (xem "Không làm trong phase này" của phase).
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase

# Bảng màu — ui_ux_spec.md mục 2.1, tên token bỏ dấu "--" và đổi "-" thành "_".
COLORS: dict[str, str] = {
    "bg_app": "#F5F5F3",
    "bg_sidebar": "#FFFFFF",
    "bg_card": "#FFFFFF",
    "border": "#E2E1DD",
    "text_primary": "#1A1A18",
    "text_secondary": "#6B6A64",
    "text_muted": "#9B9A93",
    "accent": "#2F6FE4",
    "accent_bg": "#EAF1FD",
    "warning": "#B76E00",
    "warning_bg": "#FDF1DC",
    "danger": "#C23B3B",
    "danger_bg": "#FBEAEA",
    "success": "#1E8E5A",
    "success_bg": "#E8F5EE",
    "on_accent": "#FFFFFF",
}

# Layout tokens — ui_ux_spec.md mục 2.3 (normal) và mục 8.1-8.8 (responsive).
LAYOUT: dict[str, int] = {
    "sidebar_width": 220,
    "sidebar_width_compact": 68,
    "content_padding": 24,
    "content_padding_compact": 16,
    "card_gap": 16,
    "card_gap_compact": 12,
    "header_spacing": 6,
    "header_spacing_short": 3,
    "onboarding_header_spacing": 16,
    "onboarding_header_spacing_short": 8,
    "radius": 8,
    "border_width": 1,
    # ui_ux_spec.md mục 8.1: "Cửa sổ không nhỏ hơn 800×560" — đây là sàn cứng
    # cho cả MainWindow và OnboardingView (Phase 19), không còn 1024×700 cũ.
    "min_window_width": 800,
    "min_window_height": 560,
    # width < compact_breakpoint => Compact (800-1023); >= => Normal (>=1024).
    "compact_breakpoint": 1024,
    # height < short_height_breakpoint => vùng 560-699 của mục 8.8 (page
    # header giảm khoảng cách dưới, content được phép scroll).
    "short_height_breakpoint": 700,
    # ui_ux_spec.md mục 8.7 — cùng công thức cho cả ConfirmationDialog (Phase
    # 15) và SudoAuthCard (Phase 16), không có rule riêng cho sudo card.
    "modal_max_width": 520,
    "modal_width_margin": 32,
}


def is_compact_width(width: int) -> bool:
    """True nếu `width` nằm trong vùng Compact (800-1023px) của mục 8.1."""
    return width < LAYOUT["compact_breakpoint"]


def is_short_height(height: int) -> bool:
    """True nếu `height` nằm trong vùng 560-699px của mục 8.8."""
    return height < LAYOUT["short_height_breakpoint"]


def modal_width_for(window_width: int) -> int:
    """Công thức width modal/sudo card chung — mục 8.7: `min(520,
    window_width - 32)`. Dùng chung cho `ConfirmationDialog` và
    `SudoAuthCard`, không có công thức riêng cho sudo card."""
    return min(LAYOUT["modal_max_width"], window_width - LAYOUT["modal_width_margin"])

# Typography — ui_ux_spec.md mục 2.2, tinh chỉnh ở mục 6.1. Weight Qt chỉ xấp
# xỉ weight CSS nêu trong spec (Qt Normal=50≈400, Medium=57≈500,
# DemiBold=63≈600 — không có scale 1:1 giữa hai hệ).
TYPOGRAPHY: dict[str, tuple[int, QFont.Weight]] = {
    "page_title": (18, QFont.Weight.DemiBold),
    "card_title": (17, QFont.Weight.DemiBold),
    "nav_item": (14, QFont.Weight.Medium),
    "body": (14, QFont.Weight.Normal),
    "input": (14, QFont.Weight.Normal),
    "label": (12, QFont.Weight.Medium),
    "badge": (12, QFont.Weight.Medium),
}

# Quy ước badge — ui_ux_spec.md mục 2.5: variant -> (token nền, token chữ).
BADGES: dict[str, tuple[str, str]] = {
    "tier1": ("accent_bg", "accent"),
    "tier2": ("warning_bg", "warning"),
    "success": ("success_bg", "success"),
    "danger": ("danger_bg", "danger"),
}


def general_font(role: str = "body") -> QFont:
    """System UI font (mục 6.1) — không hard-code Segoe UI/-apple-system.

    Nguồn là `QFontDatabase.systemFont(GeneralFont)` của desktop environment
    hiện tại; `role` chỉ chỉnh size/weight theo bảng typography, không đổi
    font family.
    """
    try:
        size_px, weight = TYPOGRAPHY[role]
    except KeyError as exc:
        raise ValueError(f"Unknown typography role: {role}") from exc
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPixelSize(size_px)
    font.setWeight(weight)
    return font


def fixed_font() -> QFont:
    """System monospace cho command/tool argument (mục 6.1) — không giả định
    Consolas/Cascadia Code đã được cài."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPixelSize(TYPOGRAPHY["body"][0])
    return font
