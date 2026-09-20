"""Bootstrap lần đầu chạy (CLI.md mục 15): tạo khung thư mục XDG, copy template
providers.json + default.db nếu chưa có, tạo .env với permission 600, nạp .env
vào biến môi trường.

Idempotent: gọi nhiều lần không ghi đè file người dùng đã có/đã chỉnh sửa — chỉ
copy template khi đích CHƯA tồn tại.

Nguồn template là package resources, không phụ thuộc source checkout.
"""

from __future__ import annotations

import stat

from dotenv import load_dotenv

from agent import paths
from agent.core.resource_loader import copy_resource

_DOTENV_PERMISSIONS = stat.S_IRUSR | stat.S_IWUSR  # 600


def _ensure_dirs() -> None:
    for directory in (
        paths.config_dir(), paths.data_dir(), paths.data_dir() / "rag",
        paths.state_dir(), paths.state_dir() / "logs", paths.cache_dir(),
    ):
        if directory.is_symlink():
            raise RuntimeError(f"XDG app directory must not be a symlink: {directory}")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        if stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise RuntimeError(f"Cannot set private permissions on XDG app directory: {directory}")


def _ensure_providers_json() -> None:
    copy_resource("providers.json", paths.providers_file())


def _ensure_default_db() -> None:
    copy_resource("default.db", paths.rag_default_db())


def _ensure_dotenv() -> None:
    target = paths.dotenv_file()
    if not target.exists():
        target.touch()
    target.chmod(_DOTENV_PERMISSIONS)


def ensure_bootstrapped() -> None:
    _ensure_dirs()
    _ensure_providers_json()
    _ensure_default_db()
    _ensure_dotenv()
    load_dotenv(dotenv_path=paths.dotenv_file(), override=True)
