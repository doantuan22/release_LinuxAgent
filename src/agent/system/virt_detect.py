"""Phát hiện ảo hóa/container.

Kết hợp tối thiểu 3 tín hiệu độc lập — không tin một nguồn duy nhất, vì mỗi
tín hiệu riêng lẻ có thể sai (ví dụ /.dockerenv không tồn tại trong container
rootless, systemd-detect-virt không có sẵn trên vài base image tối giản).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def _read_proc_version() -> str | None:
    try:
        return Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return None


def _check_dockerenv() -> bool:
    return Path("/.dockerenv").exists()


def _run_systemd_detect_virt() -> str | None:
    try:
        result = subprocess.run(
            ["systemd-detect-virt"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    output = result.stdout.strip()
    return output or None


def detect_virtualization() -> dict[str, Any]:
    signals: list[str] = []
    virt_type = "none"

    proc_version = _read_proc_version()
    if proc_version and ("microsoft" in proc_version or "wsl" in proc_version):
        signals.append("proc_version")
        virt_type = "wsl"

    if _check_dockerenv():
        signals.append("dockerenv")
        virt_type = "docker"

    systemd_result = _run_systemd_detect_virt()
    if systemd_result and systemd_result != "none":
        signals.append("systemd-detect-virt")
        virt_type = systemd_result

    return {
        "is_virtual": bool(signals),
        "type": virt_type,
        "signals": signals,
    }
