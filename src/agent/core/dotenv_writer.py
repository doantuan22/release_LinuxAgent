"""Đọc/ghi `.env` tại thư mục config XDG (CLI.md mục 16).

Dùng lại `agent.paths` (Phase 1) để lấy đường dẫn — không tự viết lại logic
resolve XDG. File phải có permission 600 ngay khi tạo/ghi, và kiểm tra lại
permission THẬT sau khi ghi bằng os.stat() thay vì chỉ giả định chmod() đã
thành công (có filesystem/mount không hỗ trợ đổi permission, chmod() khi đó
không raise nhưng cũng không đổi được gì).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from agent import paths

_REQUIRED_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0o600


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _verify_permissions(path: Path) -> None:
    actual_mode = stat.S_IMODE(path.stat().st_mode)
    if actual_mode != _REQUIRED_MODE:
        raise RuntimeError(
            f"Không thể đặt permission 600 cho '{path}' (thực tế: {oct(actual_mode)}). "
            f"Kiểm tra quyền thư mục cha hoặc filesystem có hỗ trợ chmod không — "
            f"file chứa API key nên KHÔNG được để lộ quyền đọc/ghi rộng hơn 600."
        )


def set_key(key: str, value: str, *, dotenv_path: Path | None = None) -> Path:
    """Ghi/cập nhật 1 biến vào .env (giữ nguyên các biến khác đã có). Trả về
    đường dẫn file đã ghi."""
    target = dotenv_path or paths.dotenv_file()
    target.parent.mkdir(parents=True, exist_ok=True)

    values = _parse_env_file(target)
    values[key] = value
    content = "".join(f"{k}={v}\n" for k, v in values.items())

    # os.open(..., mode=0o600) chỉ áp dụng permission này lúc TẠO MỚI file (umask
    # vẫn có thể can thiệp) — file đã tồn tại từ trước giữ nguyên permission cũ
    # bất kể mode truyền vào đây, nên vẫn cần chmod() + verify tường minh bên dưới
    # cho cả 2 trường hợp (file mới lẫn file đã có).
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _REQUIRED_MODE)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)

    target.chmod(_REQUIRED_MODE)
    _verify_permissions(target)

    return target


def get_key(key: str, *, dotenv_path: Path | None = None) -> str | None:
    target = dotenv_path or paths.dotenv_file()
    value = _parse_env_file(target).get(key)
    return value or None


def has_key(key: str, *, dotenv_path: Path | None = None) -> bool:
    return get_key(key, dotenv_path=dotenv_path) is not None
