"""Danh sách đường dẫn cấm — áp dụng cho CẢ đọc lẫn ghi.

Đây là whitelist/blacklist đường dẫn TĨNH, không suy luận từ nội dung lệnh lúc
runtime (nguyên tắc an toàn #1). Đây KHÔNG phải cơ chế "chặn theo pattern nguy
hiểm" bù cho việc thiếu tier — Tier 3 (xóa đệ quy, sửa /etc/passwd/sudoers, tắt
firewall/SELinux...) vẫn không có tool tương ứng nào tồn tại (nguyên tắc #2).
Danh sách này chỉ giới hạn thêm phạm vi path mà các tool Tier 1/Tier 2 hợp lệ
được phép đọc/ghi.
"""

from __future__ import annotations

import os
import re
import stat
from contextlib import contextmanager
from collections.abc import Iterator

_FORBIDDEN_EXACT_FILES: frozenset[str] = frozenset(
    {
        "/etc/passwd",
        "/etc/shadow",
        "/etc/sudoers",
    }
)

# Thư mục cấm toàn bộ nội dung bên trong (cả chính thư mục lẫn mọi file con).
_FORBIDDEN_DIR_PREFIXES: tuple[str, ...] = (
    "/etc/sudoers.d",
    "/boot",
    "/sys",
)

# Thiết bị block thô (toàn bộ ổ đĩa), KHÔNG khớp phân vùng có số (/dev/sda1, /dev/nvme0n1p1).
_RAW_BLOCK_DEVICE_RE = re.compile(r"^/dev/(sd[a-z]+|nvme\d+n\d+)$")


def is_path_forbidden(path: str) -> bool:
    normalized = os.path.normpath(path)
    # Kiểm tra cả tên người gọi đưa vào lẫn đích symlink thực tế. Các tool mở
    # file bằng open_regular_file_no_symlinks() để chặn cả race sau bước này.
    return _matches_forbidden(normalized) or _matches_forbidden(os.path.realpath(normalized))


def _matches_forbidden(normalized: str) -> bool:

    if normalized in _FORBIDDEN_EXACT_FILES:
        return True

    for prefix in _FORBIDDEN_DIR_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix + os.sep):
            return True

    if _RAW_BLOCK_DEVICE_RE.match(normalized):
        return True

    return False


@contextmanager
def open_regular_file_no_symlinks(path: str, flags: int = os.O_RDONLY) -> Iterator[tuple[int, int, str]]:
    """Mở file thường qua từng directory fd; không dereference symlink ở bất kỳ cấp nào.

    Trả (file_fd, parent_fd, basename) để caller có thể backup trong cùng thư mục
    mà không mở lại path đã được kiểm tra (tránh TOCTOU).
    """
    normalized = os.path.normpath(path)
    if not os.path.isabs(normalized) or normalized == "/":
        raise ValueError("Đường dẫn file phải là tuyệt đối.")
    parts = normalized.split("/")[1:]
    parent_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    file_fd = None
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        file_fd = os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise ValueError("Chỉ chấp nhận file thường.")
        yield file_fd, parent_fd, parts[-1]
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)
