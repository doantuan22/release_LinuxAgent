"""Controller QObject sống ở UI thread, sở hữu QThread + worker cho một page,
nối signal worker -> signal page-facing đã chuẩn hoá (loading/success/error).
Page chỉ gọi controller, không tự tạo QThread hay import service/core.
"""

from __future__ import annotations
