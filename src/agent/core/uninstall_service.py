"""Gỡ hoàn toàn Linux Agent: xóa dữ liệu người dùng rồi gỡ package.

Đây là USER COMMAND trực tiếp (CLI `agent uninstall` và nút "Xóa ứng dụng" ở GUI
Settings), KHÔNG PHẢI tool của agent: module này tuyệt đối không import
`agent.tools.registry` và không đăng ký gì vào registry, nên LLM/agent loop không
có đường nào gọi tới. Không đi qua ConfirmationRequest/confirm_policies — bước xác
nhận (gõ đúng CONFIRMATION_PHRASE) thuộc về command layer, và không có tham số/
biến môi trường nào bỏ qua được nó.

Không phụ thuộc CLI hay Qt để CLI và GUI dùng chung. Thư mục luôn lấy qua
`agent.paths` (đúng các hàm bootstrap dùng, tôn trọng XDG_*), không tự resolve lại.
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent import paths

PACKAGE_NAME = "linux-agent"
CONFIRMATION_PHRASE = "XOA VINH VIEN"

Installer = Literal["pipx", "pip", "unknown"]

# Lệnh gỡ thủ công để in ra khi không xác định được cách cài (không đoán một lệnh).
MANUAL_REMOVAL_COMMANDS: tuple[tuple[str, str], ...] = (
    ("pipx", f"pipx uninstall {PACKAGE_NAME}"),
    ("pip", f"pip uninstall -y {PACKAGE_NAME}"),
)


@dataclass(frozen=True)
class UninstallTarget:
    path: Path
    description: str
    exists: bool


@dataclass(frozen=True)
class UninstallPlan:
    targets: tuple[UninstallTarget, ...]

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(target.path for target in self.targets)


@dataclass(frozen=True)
class WipeFailure:
    path: Path
    reason: str


@dataclass(frozen=True)
class WipeResult:
    removed: tuple[Path, ...] = ()
    absent: tuple[Path, ...] = ()
    failed: tuple[WipeFailure, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failed


def _app_directories() -> tuple[tuple[Path, str], ...]:
    """4 thư mục app, đúng thứ tự và cùng hàm với bootstrap; bỏ trùng lặp."""
    candidates = (
        (paths.config_dir(), "Cấu hình: providers.json và .env (API key)"),
        (paths.data_dir(), "Dữ liệu: lịch sử session, memory, cơ sở tri thức RAG"),
        (paths.state_dir(), "Trạng thái: audit log, đánh dấu onboarding"),
        (paths.cache_dir(), "Cache"),
    )
    seen: set[Path] = set()
    unique: list[tuple[Path, str]] = []
    for path, description in candidates:
        if path not in seen:
            seen.add(path)
            unique.append((path, description))
    return tuple(unique)


def plan_uninstall() -> UninstallPlan:
    """Danh sách path sẽ bị xóa để hiển thị trước khi xác nhận. Không xóa gì."""
    return UninstallPlan(
        targets=tuple(
            UninstallTarget(path=path, description=description, exists=path.exists() or path.is_symlink())
            for path, description in _app_directories()
        )
    )


def _refusal_reason(path: Path) -> str | None:
    """Chặn các mục tiêu có thể gây hại nặng nếu cấu hình đường dẫn bị sai."""
    if not path.is_absolute():
        return "đường dẫn không tuyệt đối"
    if path == Path(path.anchor):
        return "là thư mục gốc của hệ thống tệp"
    home = Path.home()
    if path == home or path in home.parents:
        return "là (hoặc chứa) thư mục home của người dùng"
    if path.is_symlink():
        return "là symlink — không theo/không xóa để tránh xóa nhầm đích"
    return None


def _describe_error(exc: BaseException) -> str:
    if isinstance(exc, OSError):
        detail = exc.strerror or str(exc)
        location = f" ({exc.filename})" if exc.filename else ""
        return f"{type(exc).__name__}: {detail}{location}"
    return f"{type(exc).__name__}: {exc}"


def wipe_user_data() -> WipeResult:
    """Xóa từng thư mục app. Lỗi ở một thư mục KHÔNG dừng các thư mục còn lại; mọi
    thư mục đều được báo cáo rõ là đã xóa / vốn không tồn tại / lỗi kèm lý do."""
    removed: list[Path] = []
    absent: list[Path] = []
    failed: list[WipeFailure] = []

    for path, _description in _app_directories():
        refusal = _refusal_reason(path)
        if refusal is not None:
            failed.append(WipeFailure(path=path, reason=f"từ chối xóa: {refusal}"))
            continue
        if not path.exists():
            absent.append(path)
            continue
        try:
            shutil.rmtree(path)
        except Exception as exc:  # noqa: BLE001 - phải cô lập lỗi theo từng thư mục
            failed.append(WipeFailure(path=path, reason=_describe_error(exc)))
        else:
            removed.append(path)

    return WipeResult(removed=tuple(removed), absent=tuple(absent), failed=tuple(failed))


def _is_pipx_venv_path(path: Path) -> bool:
    """`.../pipx/venvs/<PACKAGE_NAME>/...` (cả ~/.local/pipx và ~/.local/share/pipx),
    hoặc dưới `$PIPX_HOME/venvs/<PACKAGE_NAME>`."""
    parts = path.parts
    for index in range(len(parts) - 2):
        if parts[index] == "pipx" and parts[index + 1] == "venvs" and parts[index + 2] == PACKAGE_NAME:
            return True
    pipx_home = os.environ.get("PIPX_HOME")
    if pipx_home:
        return path.is_relative_to(Path(pipx_home) / "venvs" / PACKAGE_NAME)
    return False


def _package_installed() -> bool:
    try:
        importlib.metadata.version(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def _is_externally_managed_system_python() -> bool:
    """PEP 668: `pip uninstall` trên Python hệ thống được distro quản lý sẽ bị từ chối
    (chạy nền nên người dùng không thấy lỗi) — chỉ áp dụng ngoài virtualenv."""
    if sys.prefix != sys.base_prefix:
        return False
    return (Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED").exists()


def detect_installer() -> Installer:
    """pipx / pip / unknown. Chỉ trả "pip" khi có bằng chứng `pip uninstall` sẽ thật sự
    gỡ được package này; nếu không chắc chắn thì "unknown" (không đoán lệnh gỡ)."""
    prefix = Path(sys.prefix) if sys.prefix else None
    executable = Path(sys.executable) if sys.executable else None
    if prefix is None and executable is None:
        return "unknown"
    if any(candidate is not None and _is_pipx_venv_path(candidate) for candidate in (prefix, executable)):
        return "pipx"
    if prefix is not None and (prefix / "pipx_metadata.json").exists():
        return "unknown"  # venv do pipx quản lý nhưng bố cục không nhận ra được
    if not _package_installed():
        return "unknown"  # chạy từ source tree: `pip uninstall` sẽ không gỡ gì
    if _is_externally_managed_system_python():
        return "unknown"
    return "pip"


def spawn_package_removal(installer: Installer) -> None:
    """Chạy lệnh gỡ package ở tiến trình tách rời (start_new_session) để không bị kill
    khi process hiện tại thoát ngay sau đó. "unknown": không chạy gì. Có thể raise
    OSError (vd không có pipx) — caller phải xử lý và in hướng dẫn thủ công."""
    if installer == "pipx":
        command = ["pipx", "uninstall", PACKAGE_NAME]
    elif installer == "pip":
        command = [sys.executable, "-m", "pip", "uninstall", "-y", PACKAGE_NAME]
    else:
        return
    subprocess.Popen(
        command,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
