"""Quét thông tin máy: distro, kiến trúc CPU, package manager, ảo hóa.

Phân loại dữ liệu (nguyên tắc an toàn #5 — không bao giờ cache dữ liệu động):
- TĨNH trong vòng đời tiến trình: distro_id, distro_name, distro_version, architecture
  — máy không đổi distro hay kiến trúc CPU giữa lúc agent chạy.
- BÁN TĨNH, an toàn để cache trong tiến trình: package_manager, virtualization
  — về lý thuyết có thể đổi (cài thêm package manager khác, đổi lớp ảo hóa lồng
  nhau) nhưng gần như không xảy ra trong một phiên chạy của agent.
- ĐỘNG, KHÔNG được quét/cache ở đây: dung lượng đĩa, RAM, tiến trình, trạng thái
  service — nằm ở tools/tier1_readonly.py, luôn đọc lại mỗi lần gọi tool.

scan_system() dùng lru_cache(maxsize=1) đúng vì toàn bộ trường của SystemProfile
đều tĩnh/bán tĩnh theo phân loại trên.
"""

from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent.system.virt_detect import detect_virtualization

# Dò package manager qua binary có sẵn trong PATH — không hardcode theo tên distro,
# để tự động tương thích với các distro dẫn xuất mà không cần liệt kê tên riêng.
_PACKAGE_MANAGER_BINARIES: tuple[tuple[str, str], ...] = (
    ("apt-get", "apt"),
    ("dnf", "dnf"),
    ("pacman", "pacman"),
    ("zypper", "zypper"),
)

# Không tìm thấy binary nào ở trên -> distro immutable/không dùng package manager
# truyền thống (NixOS, Silverblue/Kinoite...). Trả rõ ràng "unsupported", không
# đoán bừa hay fallback sang lệnh sai (nguyên tắc: không tuyên bố hỗ trợ mọi Linux).
UNSUPPORTED_PACKAGE_MANAGER = "unsupported"


@dataclass
class SystemProfile:
    distro_id: str
    distro_name: str
    distro_version: str
    architecture: str
    package_manager: str
    virtualization: dict[str, Any] = field(default_factory=dict)
    # Thêm ở Phase 5 (CLI `agent profile`, mục 11) — tĩnh trong vòng đời tiến
    # trình như distro_id/architecture, đặt cuối + có default để không phá vỡ
    # các chỗ đang dựng SystemProfile(...) mà chưa truyền trường này.
    kernel_version: str = ""


# Giá trị mặc định khi dựng SystemProfile từ dict không đầy đủ (Giai đoạn 7 — eval
# framework chỉ cần set vài trường liên quan cho mỗi case, không phải điền hết).
_PROFILE_DEFAULTS: dict[str, Any] = {
    "distro_id": "unknown",
    "distro_name": "Unknown",
    "distro_version": "unknown",
    "architecture": "x86_64",
    "package_manager": UNSUPPORTED_PACKAGE_MANAGER,
    "virtualization": {},
    "kernel_version": "unknown",
}


def build_profile_from_dict(data: dict[str, Any]) -> SystemProfile:
    """Dựng SystemProfile từ dict thiếu trường (vd mock_system_profile trong eval
    case YAML) — điền mặc định hợp lý cho các trường không được set."""
    merged = {**_PROFILE_DEFAULTS, **data}
    return SystemProfile(**{key: merged[key] for key in _PROFILE_DEFAULTS})


def _read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    data: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        data[key] = value.strip().strip('"')
    return data


def detect_package_manager() -> str:
    for binary, manager_id in _PACKAGE_MANAGER_BINARIES:
        if shutil.which(binary):
            return manager_id
    return UNSUPPORTED_PACKAGE_MANAGER


@lru_cache(maxsize=1)
def scan_system() -> SystemProfile:
    os_release = _read_os_release()
    return SystemProfile(
        distro_id=os_release.get("ID", "unknown"),
        distro_name=os_release.get("NAME", "Unknown"),
        distro_version=os_release.get("VERSION_ID", "unknown"),
        architecture=platform.machine(),
        package_manager=detect_package_manager(),
        virtualization=detect_virtualization(),
        kernel_version=platform.release(),
    )


def to_prompt_context(profile: SystemProfile | None = None) -> str:
    profile = profile or scan_system()
    virt = profile.virtualization or {}
    virt_desc = virt.get("type", "none") if virt.get("is_virtual") else "không ảo hóa"

    text = (
        f"Máy đang chạy: {profile.distro_name} {profile.distro_version} "
        f"({profile.distro_id}), kiến trúc {profile.architecture}, "
        f"package manager: {profile.package_manager}, "
        f"môi trường: {virt_desc}."
    )

    if profile.package_manager == UNSUPPORTED_PACKAGE_MANAGER:
        text += (
            " Cảnh báo: không phát hiện được package manager quen thuộc "
            "(apt/dnf/pacman/zypper) — có thể là NixOS, Silverblue/Kinoite hoặc distro "
            "immutable khác; các tool cài đặt gói sẽ báo lỗi rõ ràng thay vì đoán lệnh."
        )

    return text


def _environment_label(virtualization: dict[str, Any]) -> str:
    if not virtualization.get("is_virtual"):
        return "Bare Metal"
    virt_type = virtualization.get("type") or "unknown"
    if virt_type in ("wsl", "kvm"):
        return virt_type.upper()
    return virt_type.replace("_", " ").title()


def to_banner_line(profile: SystemProfile | None = None) -> str:
    """Dòng tóm tắt ngắn gọn cho banner mở đầu của `agent chat` (CLI.md mục 4.1)
    — KHÁC to_prompt_context() (cho LLM đọc) và to_display_summary() (bảng đầy
    đủ cho `agent profile`, mục 11)."""
    profile = profile or scan_system()
    environment = _environment_label(profile.virtualization or {})
    return (
        f"{profile.distro_name} {profile.distro_version} · {profile.architecture} · "
        f"{profile.package_manager} · {environment}"
    )


def to_display_summary(profile: SystemProfile | None = None) -> list[tuple[str, str]]:
    """Dữ liệu phẳng (label, value) cho bảng "System Profile" của `agent profile`
    (CLI.md mục 11) — KHÁC to_prompt_context() (cho LLM đọc, mô tả dạng câu văn)
    và to_banner_line() (1 dòng tóm tắt cho banner `agent chat`). Không tự
    scan_system() lại nếu đã có profile truyền vào — nguyên tắc an toàn #5, chỉ
    scan_system() (đã tự cache qua lru_cache) mới có quyền quét máy thật."""
    profile = profile or scan_system()
    virt = profile.virtualization or {}
    virt_type = virt.get("type") or "none"
    is_virtual = bool(virt.get("is_virtual"))

    def _yes_no(value: bool) -> str:
        return "Yes" if value else "No"

    return [
        ("OS", profile.distro_name),
        ("Version", profile.distro_version),
        ("Architecture", profile.architecture),
        ("Kernel", profile.kernel_version),
        ("Package Manager", profile.package_manager),
        ("Environment", _environment_label(virt)),
        ("WSL", _yes_no(virt_type == "wsl")),
        ("Docker", _yes_no(virt_type == "docker")),
        ("Virtual Machine", _yes_no(is_virtual and virt_type not in ("wsl", "docker"))),
    ]
