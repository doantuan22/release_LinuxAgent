"""7 tool Tier 1 (chỉ đọc) đầu tiên của agent.

Mọi exception được bọc thành ToolResult(ok=False, error=...) ngay tại tool —
không để lọt ra ngoài (ToolExecutor ở core/executor.py là lớp bảo vệ thứ hai).
Tham số path chỉ chấp nhận đường dẫn tuyệt đối. Dùng bộ lệnh hiện đại
(systemctl/journalctl), không dùng ifconfig/netstat.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agent import paths
from agent.core.forbidden_paths import is_path_forbidden, open_regular_file_no_symlinks
from agent.core.redaction import redact_known_secrets
from agent.rag.index import search as rag_search
from agent.system.profile import SystemProfile, scan_system
from agent.tools.registry import tool
from agent.tools.schemas import Tier, ToolResult

logger = logging.getLogger(__name__)

# Cả default.db đã copy từ package_data và user.db đều nằm trong XDG data.
_DEFAULT_DOCS_DB = paths.rag_default_db()
_USER_DOCS_DB = paths.rag_user_db()


def _validate_absolute_path(path: str) -> str | None:
    if not path or not Path(path).is_absolute():
        return f"Đường dẫn phải là tuyệt đối, nhận được: '{path}'"
    return None


@tool(
    name="get_system_info",
    description="Lấy thông tin distro, kiến trúc CPU, package manager và trạng thái ảo hóa của máy.",
    tier=Tier.TIER_1_READONLY,
    parameters={"type": "object", "properties": {}, "required": []},
)
def get_system_info(system_profile: SystemProfile | None = None) -> ToolResult:
    try:
        profile = system_profile if system_profile is not None else scan_system()
    except Exception as e:
        return ToolResult(ok=False, error=f"Không quét được thông tin hệ thống: {e}")

    return ToolResult(
        ok=True,
        data={
            "distro_id": profile.distro_id,
            "distro_name": profile.distro_name,
            "distro_version": profile.distro_version,
            "architecture": profile.architecture,
            "package_manager": profile.package_manager,
            "virtualization": profile.virtualization,
        },
    )


@tool(
    name="check_disk_usage",
    description="Kiểm tra dung lượng đĩa (tổng/đã dùng/còn trống) tại một đường dẫn tuyệt đối.",
    tier=Tier.TIER_1_READONLY,
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Đường dẫn tuyệt đối cần kiểm tra, mặc định '/'"},
        },
        "required": [],
    },
)
def check_disk_usage(path: str = "/") -> ToolResult:
    error = _validate_absolute_path(path)
    if error:
        return ToolResult(ok=False, error=error)

    try:
        usage = shutil.disk_usage(path)
    except OSError as e:
        return ToolResult(ok=False, error=f"Không kiểm tra được dung lượng '{path}': {e}")

    gb = 1024**3
    percent_used = round(usage.used / usage.total * 100, 1) if usage.total else 0.0
    return ToolResult(
        ok=True,
        data={
            "path": path,
            "total_gb": round(usage.total / gb, 2),
            "used_gb": round(usage.used / gb, 2),
            "free_gb": round(usage.free / gb, 2),
            "percent_used": percent_used,
        },
    )


@tool(
    name="check_memory_usage",
    description="Kiểm tra dung lượng RAM tổng/đã dùng/khả dụng qua /proc/meminfo.",
    tier=Tier.TIER_1_READONLY,
    parameters={"type": "object", "properties": {}, "required": []},
)
def check_memory_usage(meminfo_path: Path = Path("/proc/meminfo")) -> ToolResult:
    try:
        text = meminfo_path.read_text(encoding="utf-8")
    except OSError as e:
        return ToolResult(ok=False, error=f"Không đọc được {meminfo_path}: {e}")

    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        digits = "".join(ch for ch in rest if ch.isdigit())
        if digits:
            values[key] = int(digits)

    total_kb = values.get("MemTotal", 0)
    available_kb = values.get("MemAvailable", 0)
    used_kb = max(total_kb - available_kb, 0)
    percent_used = round(used_kb / total_kb * 100, 1) if total_kb else 0.0

    return ToolResult(
        ok=True,
        data={
            "total_gb": round(total_kb / 1024 / 1024, 2),
            "available_gb": round(available_kb / 1024 / 1024, 2),
            "used_gb": round(used_kb / 1024 / 1024, 2),
            "percent_used": percent_used,
        },
    )


def _read_process_info(proc_dir: Path) -> dict[str, Any] | None:
    try:
        comm = proc_dir.joinpath("comm").read_text(encoding="utf-8").strip()
        status_text = proc_dir.joinpath("status").read_text(encoding="utf-8")
    except OSError:
        return None

    state = "unknown"
    vm_rss_kb = 0
    for line in status_text.splitlines():
        if line.startswith("State:"):
            state = line.split(":", 1)[1].strip()
        elif line.startswith("VmRSS:"):
            digits = "".join(ch for ch in line.split(":", 1)[1] if ch.isdigit())
            vm_rss_kb = int(digits) if digits else 0

    return {"pid": int(proc_dir.name), "name": comm, "state": state, "rss_mb": round(vm_rss_kb / 1024, 1)}


@tool(
    name="list_processes",
    description="Liệt kê các tiến trình đang chạy, sắp xếp theo RAM sử dụng giảm dần.",
    tier=Tier.TIER_1_READONLY,
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Số tiến trình tối đa trả về, mặc định 20"},
        },
        "required": [],
    },
)
def list_processes(limit: int = 20, proc_root: Path = Path("/proc")) -> ToolResult:
    try:
        pid_dirs = [entry for entry in proc_root.iterdir() if entry.name.isdigit()]
    except OSError as e:
        return ToolResult(ok=False, error=f"Không đọc được {proc_root}: {e}")

    processes = [info for entry in pid_dirs if (info := _read_process_info(entry)) is not None]
    processes.sort(key=lambda p: p["rss_mb"], reverse=True)

    return ToolResult(ok=True, data={"processes": processes[:limit], "total_count": len(processes)})


@tool(
    name="check_service_status",
    description="Kiểm tra trạng thái active/enabled của một systemd service và log gần nhất qua journalctl.",
    tier=Tier.TIER_1_READONLY,
    parameters={
        "type": "object",
        "properties": {
            "service_name": {"type": "string", "description": "Tên service, ví dụ 'nginx' hoặc 'nginx.service'"},
        },
        "required": ["service_name"],
    },
)
def check_service_status(service_name: str) -> ToolResult:
    if not service_name or not service_name.strip():
        return ToolResult(ok=False, error="Thiếu tên service.")

    try:
        active = subprocess.run(
            ["systemctl", "is-active", service_name], capture_output=True, text=True, timeout=10
        )
        enabled = subprocess.run(
            ["systemctl", "is-enabled", service_name], capture_output=True, text=True, timeout=10
        )
    except FileNotFoundError:
        return ToolResult(ok=False, error="Không tìm thấy systemctl (hệ thống này có thể không dùng systemd).")
    except subprocess.TimeoutExpired as e:
        return ToolResult(ok=False, error=f"systemctl không phản hồi kịp thời: {e}")

    recent_logs: list[str] = []
    try:
        journal = subprocess.run(
            ["journalctl", "-u", service_name, "-n", "10", "--no-pager", "--output=short"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        recent_logs = [line for line in journal.stdout.splitlines() if line.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return ToolResult(
        ok=True,
        data={
            "service_name": service_name,
            "active": active.stdout.strip(),
            "enabled": enabled.stdout.strip(),
            "recent_logs": recent_logs,
        },
    )


def _allowed_log_path(path: str) -> bool:
    candidate = Path(os.path.normpath(path))
    allowed_roots = (Path("/var/log"), paths.state_dir() / "logs")
    valid_name = candidate.name.endswith(".log") or candidate.name in {"syslog", "messages", "dmesg"}
    return valid_name and any(candidate.is_relative_to(root) for root in allowed_roots)


@tool(
    name="read_log",
    description="Đọc N dòng cuối của file log thường trong /var/log hoặc XDG state/logs (tối đa 2 MiB).",
    tier=Tier.TIER_1_READONLY,
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Đường dẫn tuyệt đối tới file log"},
            "lines": {"type": "integer", "description": "Số dòng cuối cần đọc, mặc định 200"},
        },
        "required": ["path"],
    },
)
def read_log(path: str, lines: int = 200) -> ToolResult:
    error = _validate_absolute_path(path)
    if error:
        return ToolResult(ok=False, error=error)

    normalized = os.path.normpath(path)
    if is_path_forbidden(normalized) or not _allowed_log_path(normalized):
        return ToolResult(ok=False, error="Chỉ được đọc file log trong thư mục log được phép.")

    try:
        with open_regular_file_no_symlinks(normalized) as (fd, _, __):
            if os.fstat(fd).st_nlink != 1:
                return ToolResult(ok=False, error="Không đọc file log có hard link.")
            # Đọc có giới hạn để một log rất lớn không làm cạn RAM của agent.
            with os.fdopen(os.dup(fd), "rb") as handle:
                raw = handle.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                return ToolResult(ok=False, error="File log vượt quá giới hạn 2 MiB.")
            text = raw.decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ToolResult(ok=False, error="Không đọc được file log thường hoặc path chứa symlink.")

    all_lines = [redact_known_secrets(line) for line in text.splitlines()]
    tail = all_lines[-lines:] if lines > 0 else []
    return ToolResult(ok=True, data={"path": path, "lines": tail, "total_lines": len(all_lines)})


_SEARCH_COMMANDS: dict[str, list[str]] = {
    "apt": ["apt-cache", "search"],
    "dnf": ["dnf", "search"],
    "pacman": ["pacman", "-Ss"],
    "zypper": ["zypper", "--non-interactive", "search"],
}


def _extract_apt_package_names(output: str) -> set[str]:
    # "pkgname - mô tả" mỗi dòng.
    return {name for line in output.splitlines() if (name := line.split(" - ", 1)[0].strip())}


def _extract_dnf_package_names(output: str) -> set[str]:
    # "pkgname.arch : mô tả" cho mỗi kết quả; bỏ qua dòng header "==== ... Matched: ... ====".
    names: set[str] = set()
    for line in output.splitlines():
        match = re.match(r"^(\S+)\s*:\s", line.strip())
        if not match:
            continue
        candidate = match.group(1)
        names.add(candidate)
        if "." in candidate:
            names.add(candidate.rsplit(".", 1)[0])
    return names


def _extract_pacman_package_names(output: str) -> set[str]:
    # "repo/pkgname version" ở đầu dòng; dòng mô tả được thụt lề nên bỏ qua.
    names: set[str] = set()
    for line in output.splitlines():
        if not line or line[0].isspace():
            continue
        match = re.match(r"^\S+/(\S+)\s", line)
        if match:
            names.add(match.group(1))
    return names


def _extract_zypper_package_names(output: str) -> set[str]:
    # Bảng cột "S | Name | Summary | Type"; bỏ qua header và dòng kẻ phân cách.
    names: set[str] = set()
    for line in output.splitlines():
        if "|" not in line:
            continue
        columns = [c.strip() for c in line.split("|")]
        if len(columns) < 2 or columns[1] in ("", "Name"):
            continue
        names.add(columns[1])
    return names


_PACKAGE_NAME_EXTRACTORS = {
    "apt": _extract_apt_package_names,
    "dnf": _extract_dnf_package_names,
    "pacman": _extract_pacman_package_names,
    "zypper": _extract_zypper_package_names,
}


@tool(
    name="search_package",
    description="Tìm gói theo từ khóa bằng package manager của máy hiện tại. Luôn gọi trước install_package.",
    tier=Tier.TIER_1_READONLY,
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Từ khóa tìm gói"},
        },
        "required": ["query"],
    },
)
def search_package(query: str, system_profile: SystemProfile | None = None) -> ToolResult:
    if not query or not query.strip():
        return ToolResult(ok=False, error="Thiếu từ khóa tìm kiếm.")

    profile = system_profile if system_profile is not None else scan_system()
    manager = profile.package_manager
    if manager is None or manager not in _SEARCH_COMMANDS:
        return ToolResult(ok=False, error=f"Không hỗ trợ tìm gói cho package manager '{manager}'.")

    command = [*_SEARCH_COMMANDS[manager], query]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return ToolResult(ok=False, error=f"Không tìm thấy binary cho package manager '{manager}'.")
    except subprocess.TimeoutExpired as e:
        return ToolResult(ok=False, error=f"Lệnh tìm gói hết thời gian chờ: {e}")

    matched_names = sorted(_PACKAGE_NAME_EXTRACTORS[manager](result.stdout))
    return ToolResult(
        ok=True,
        data={
            "package_manager": manager,
            "query": query,
            "output": result.stdout.strip(),
            "matched_names": matched_names,
        },
    )


@tool(
    name="search_linux_docs",
    description=(
        "Tìm tài liệu Linux liên quan (tóm tắt man page/Ubuntu docs/Arch Wiki đã chọn lọc) theo "
        "từ khóa hoặc câu hỏi, trả về title + tóm tắt + link nguồn cho từng kết quả. Dùng khi cần "
        "trích dẫn nguồn thay vì tự suy đoán câu trả lời. Nếu không có kết quả nào, nói rõ với "
        "người dùng là chưa có tài liệu liên quan, không bịa nguồn."
    ),
    tier=Tier.TIER_1_READONLY,
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Từ khóa hoặc câu hỏi cần tìm tài liệu liên quan"},
        },
        "required": ["query"],
    },
)
def search_linux_docs(query: str, system_profile: SystemProfile | None = None) -> ToolResult:
    if not query or not query.strip():
        return ToolResult(ok=False, error="Thiếu từ khóa tìm kiếm.")

    try:
        profile = system_profile if system_profile is not None else scan_system()
        distro_id = profile.distro_id
    except Exception:
        distro_id = None

    results: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for db_path in (_DEFAULT_DOCS_DB, _USER_DOCS_DB):
        if not db_path.exists():
            continue
        try:
            hits = rag_search(db_path, query, distro_id=distro_id, limit=5)
        except Exception as e:
            logger.warning(f"search_linux_docs: lỗi khi tìm trong '{db_path}': {e}")
            continue

        for doc, _score in hits:
            if doc.id in seen_ids:
                continue
            seen_ids.add(doc.id)
            results.append(
                {"title": doc.title, "summary": doc.summary, "source_url": doc.source_url}
            )

    return ToolResult(ok=True, data={"query": query, "results": results})
