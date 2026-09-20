"""3 tool Tier 2 (hành động thật) đầu tiên của agent.

Xác nhận trước khi chạy là trách nhiệm của ToolExecutor (core/executor.py), KHÔNG
phải của tool — tool ở đây chỉ thực thi khi đã được executor cho phép chạy tới.
Cố tình KHÔNG có tool nào cho xóa đệ quy tùy ý, thao tác phân vùng, sửa
/etc/passwd|sudoers, tắt firewall/SELinux, hay nạp kernel module (Tier 3 không
tồn tại tool tương ứng — nguyên tắc an toàn #2).
"""

from __future__ import annotations

import os
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from agent.core.forbidden_paths import is_path_forbidden, open_regular_file_no_symlinks
from agent.system.package_managers.apt import AptAdapter
from agent.system.package_managers.base import PackageManagerAdapter
from agent.system.package_managers.dnf import DnfAdapter
from agent.system.package_managers.pacman import PacmanAdapter
from agent.system.package_managers.zypper import ZypperAdapter
from agent.system.profile import UNSUPPORTED_PACKAGE_MANAGER, SystemProfile, scan_system
from agent.system.privilege import ExecutionPlan, PrivilegeError, PrivilegeManager
from agent.tools.registry import tool
from agent.tools.schemas import Tier, ToolResult

# Điểm duy nhất ánh xạ package_manager -> adapter thật. install_package() không
# hardcode apt — nó tra bảng này theo package manager phát hiện được từ máy
# (Tier A: apt/dnf/pacman, Tier B: zypper).
_ADAPTERS: dict[str, PackageManagerAdapter] = {
    "apt": AptAdapter(),
    "dnf": DnfAdapter(),
    "pacman": PacmanAdapter(),
    "zypper": ZypperAdapter(),
}
_ADAPTERS_BY_EXECUTABLE: dict[str, PackageManagerAdapter] = {
    "apt-get": _ADAPTERS["apt"],
    "dnf": _ADAPTERS["dnf"],
    "pacman": _ADAPTERS["pacman"],
    "zypper": _ADAPTERS["zypper"],
}


def _validate_absolute_path(path: str) -> str | None:
    if not path or not Path(path).is_absolute():
        return f"Đường dẫn phải là tuyệt đối, nhận được: '{path}'"
    return None


# Allowlist (không phải blacklist): chữ/số, rồi '.', '-' (không ở đầu), '+', ':'
# (vd multi-arch "libc6:amd64"). Không bắt đầu bằng '-' để package manager không
# diễn giải tên gói thành option/flag của chính nó (vd "--allow-downgrades").
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+:-]*$")


def _validate_package_name(name: str) -> str | None:
    if not _PACKAGE_NAME_RE.fullmatch(name):
        return (
            f"Tên gói '{name}' không hợp lệ — không được bắt đầu bằng '-', chứa khoảng "
            "trắng hoặc ký tự đặc biệt; chỉ cho phép chữ, số và các ký tự '.', '-', '+', ':'."
        )
    return None


def _editable_config_roots() -> tuple[Path, ...]:
    # Chỉ các họ cấu hình hệ thống được tool hỗ trợ; không cho phép file tùy ý
    # trong home, /dev hay toàn bộ /etc. Tests thay roots bằng tmp_path.
    return (Path("/etc/nginx"), Path("/etc/apache2"), Path("/etc/systemd/system"))


def _is_editable_config_path(path: str) -> bool:
    candidate = Path(os.path.normpath(path))
    return candidate.suffix in {".conf", ".service", ".socket", ".timer"} and any(
        candidate.is_relative_to(root) for root in _editable_config_roots()
    )


# Thư mục unit chuẩn của systemd (system manager) — liệt kê đủ, không chỉ
# /etc/systemd/system, để chặn theo LOẠI NỘI DUNG (unit file tự thực thi
# ExecStart= bằng root sau daemon-reload+restart) chứ không phải theo 1 path cụ
# thể. edit_config_file ghi đè toàn bộ nội dung không kiểm tra ngữ nghĩa, nên
# cho phép sửa unit file ở đây tương đương root code execution/persistence —
# đúng bản chất Tier 3, trong khi registry không đăng ký tool Tier 3 nào.
_SYSTEMD_UNIT_DIRECTORIES: tuple[Path, ...] = (
    Path("/etc/systemd/system"),
    Path("/run/systemd/system"),
    Path("/usr/lib/systemd/system"),
    Path("/lib/systemd/system"),
    Path("/usr/local/lib/systemd/system"),
)
_SYSTEMD_UNIT_SUFFIXES = {".service", ".socket", ".timer", ".path", ".mount"}


def _is_systemd_unit_path(path: str) -> bool:
    candidate = Path(os.path.normpath(path))
    if candidate.suffix not in _SYSTEMD_UNIT_SUFFIXES:
        return False
    return any(candidate.is_relative_to(root) for root in _SYSTEMD_UNIT_DIRECTORIES)


# Thư mục drop-in "<tên-unit>.<loại>.d/" (vd "nginx.service.d/") — systemd tự
# merge MỌI file bên trong (bất kể đuôi, .conf chỉ là convention chứ không bắt
# buộc) vào unit gốc lúc daemon-reload. Ghi vào đây override được ExecStart=/
# User=... y hệt sửa thẳng unit file, nên chặn theo TÊN THƯ MỤC CHA, không theo
# đuôi file — dùng lại đúng _SYSTEMD_UNIT_SUFFIXES/_SYSTEMD_UNIT_DIRECTORIES ở
# trên, không định nghĩa danh sách loại unit/thư mục thứ hai.
_SYSTEMD_DROPIN_DIR_RE = re.compile(
    r"^.+\.(" + "|".join(re.escape(suffix.lstrip(".")) for suffix in _SYSTEMD_UNIT_SUFFIXES) + r")\.d$"
)


def _is_systemd_dropin_path(path: str) -> bool:
    candidate = Path(os.path.normpath(path))
    parent = candidate.parent
    if not _SYSTEMD_DROPIN_DIR_RE.match(parent.name):
        return False
    return parent.parent in _SYSTEMD_UNIT_DIRECTORIES


def _write_all(fd: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("Short write to file")
        remaining = remaining[written:]


def _direct_call_id() -> str:
    """Compatibility ID until the loop supplies its established call_id."""
    return f"direct-{uuid4().hex}"


def _package_adapter(
    name: str, system_profile: SystemProfile | None,
) -> tuple[PackageManagerAdapter | None, ToolResult | None]:
    if not name or not name.strip():
        return None, ToolResult(ok=False, error="Thiếu tên gói cần cài.")
    invalid_reason = _validate_package_name(name)
    if invalid_reason is not None:
        return None, ToolResult(ok=False, error=invalid_reason)
    profile = system_profile if system_profile is not None else scan_system()
    manager_id = profile.package_manager
    if manager_id == UNSUPPORTED_PACKAGE_MANAGER:
        return None, ToolResult(
            ok=False,
            error=(
                "Không phát hiện được package manager quen thuộc (apt/dnf/pacman/zypper) "
                "trên máy này — có thể là NixOS, Silverblue/Kinoite, hoặc distro immutable "
                "khác, không hỗ trợ mô hình cài đặt gói kiểu này."
            ),
        )
    adapter = _ADAPTERS.get(manager_id)
    if adapter is None:
        return None, ToolResult(ok=False, error=f"Chưa hỗ trợ cài đặt cho package manager '{manager_id}'.")
    return adapter, None


def preflight_install_package(
    name: str, system_profile: SystemProfile | None = None, *, call_id: str,
) -> ExecutionPlan:
    """Build the package argv once; retry will revalidate this same plan."""
    adapter, error = _package_adapter(name, system_profile)
    if error is not None:
        raise ValueError(error.error)
    assert adapter is not None
    return adapter.preflight(name, call_id=call_id)


def preflight_restart_service(name: str, *, call_id: str) -> ExecutionPlan:
    if not name or not name.strip():
        raise ValueError("Thiếu tên service.")
    return PrivilegeManager().preflight(["systemctl", "restart", name], call_id=call_id)


def preflight_edit_config_file(path: str, new_content: str, *, call_id: str) -> ExecutionPlan:
    """Validate the target and build the exact narrow helper argv."""
    _ = new_content  # Content is delivered on stdin only at execution time.
    error = _validate_absolute_path(path)
    if error:
        raise ValueError(error)
    if is_path_forbidden(path):
        raise ValueError(f"Đường dẫn '{path}' nằm trong danh sách cấm truy cập.")
    if _is_systemd_unit_path(path):
        raise ValueError(
            f"Không được sửa unit file systemd '{path}' qua tool này — ExecStart=/User=... "
            "trong unit file có thể chạy tùy ý bằng root sau daemon-reload, tương đương Tier 3."
        )
    if _is_systemd_dropin_path(path):
        raise ValueError(
            f"Không được sửa file trong drop-in directory unit systemd '{path}' — mọi file trong "
            "thư mục *.service.d/*.socket.d/*.timer.d/*.path.d/*.mount.d/ đều bị systemd tự merge "
            "vào unit gốc lúc daemon-reload, override ExecStart=/User=... tương đương Tier 3 y hệt "
            "sửa thẳng unit file."
        )
    if not _is_editable_config_path(path):
        raise ValueError("File nằm ngoài phạm vi cấu hình được phép sửa.")
    target = Path(os.path.normpath(path))
    if not target.exists():
        raise ValueError(
            f"File '{path}' không tồn tại — tool này chỉ sửa file cấu hình đã có, không tạo file mới."
        )
    return PrivilegeManager().preflight(
        [sys.executable, "-m", "agent.system.config_edit_helper", str(target)], call_id=call_id
    )


@tool(
    name="install_package",
    description=(
        "Cài một gói phần mềm bằng package manager của máy hiện tại. "
        "LUÔN gọi search_package trước để xác nhận đúng tên gói giữa các distro. "
        "Đây là hành động Tier 2, cần người dùng xác nhận trước khi chạy."
    ),
    tier=Tier.TIER_2_ACTION,
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Tên gói cần cài, đúng như search_package trả về"},
        },
        "required": ["name"],
    },
    preflight=preflight_install_package,
)
def install_package(
    name: str,
    system_profile: SystemProfile | None = None,
    *,
    execution_plan: ExecutionPlan | None = None,
    call_id: str | None = None,
) -> ToolResult:
    if execution_plan is not None:
        # Resume uses the adapter that created the plan, never a newly scanned
        # package-manager choice. The exact argv is therefore the one checked
        # during preflight, even if system discovery later changes.
        adapter = _ADAPTERS_BY_EXECUTABLE.get(execution_plan.base_argv[0])
        if adapter is None:
            return ToolResult(ok=False, error="ExecutionPlan cài gói không hợp lệ.")
    else:
        adapter, error = _package_adapter(name, system_profile)
        if error is not None:
            return error
        assert adapter is not None
    try:
        plan = execution_plan or adapter.preflight(name, call_id=call_id or _direct_call_id())
    except PrivilegeError as exc:
        return ToolResult(ok=False, error=str(exc))
    return adapter.execute(plan)


@tool(
    name="restart_service",
    description=(
        "Khởi động lại một systemd service (systemctl restart). Khi cần quyền cao, "
        "sudo xác thực trực tiếp trên terminal sau khi người dùng xác nhận. "
        "Đây là hành động Tier 2, cần người dùng xác nhận trước khi chạy."
    ),
    tier=Tier.TIER_2_ACTION,
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Tên service cần restart"},
        },
        "required": ["name"],
    },
    preflight=preflight_restart_service,
)
def restart_service(
    name: str, *, execution_plan: ExecutionPlan | None = None, call_id: str | None = None,
) -> ToolResult:
    if not name or not name.strip():
        return ToolResult(ok=False, error="Thiếu tên service.")

    try:
        plan = execution_plan or preflight_restart_service(name, call_id=call_id or _direct_call_id())
        result = subprocess.run(
            list(plan.argv), capture_output=True, text=True, timeout=30
        )
    except PrivilegeError as exc:
        return ToolResult(ok=False, error=str(exc))
    except FileNotFoundError:
        return ToolResult(ok=False, error="Không tìm thấy systemctl (hệ thống này có thể không dùng systemd).")
    except subprocess.TimeoutExpired as e:
        return ToolResult(ok=False, error=f"Restart '{name}' hết thời gian chờ: {e}")

    # ``plan.argv`` has been invoked. Do not parse sudo diagnostics here:
    # an indistinguishable child process may have made a partial side effect,
    # so treating every post-invocation error as terminal is safer than retry.
    if result.returncode != 0:
        return ToolResult(
            ok=False,
            error=f"Không restart được '{name}' (có thể thiếu quyền hoặc service không tồn tại): {result.stderr.strip()}",
        )

    return ToolResult(ok=True, data={"service_name": name, "message": f"Đã restart '{name}'."})


@tool(
    name="edit_config_file",
    description=(
        "Ghi đè file .conf thường trong /etc/nginx hoặc /etc/apache2 tại đường dẫn tuyệt đối "
        "(KHÔNG áp dụng cho unit file systemd .service/.socket/.timer/.path/.mount, kể cả file "
        "trong thư mục drop-in *.service.d/... của chúng — đều bị từ chối); "
        "tự động backup file gốc (path.bak.<timestamp>) trước khi ghi. "
        "Nếu file cần quyền cao, sudo xác thực trên terminal rồi chạy helper giới hạn. "
        "Đây là hành động Tier 2, cần người dùng xác nhận trước khi chạy."
    ),
    tier=Tier.TIER_2_ACTION,
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Đường dẫn tuyệt đối tới file cấu hình đã tồn tại"},
            "new_content": {"type": "string", "description": "Nội dung mới sẽ ghi đè toàn bộ file"},
        },
        "required": ["path", "new_content"],
    },
    preflight=preflight_edit_config_file,
)
def edit_config_file(
    path: str,
    new_content: str,
    *,
    execution_plan: ExecutionPlan | None = None,
    call_id: str | None = None,
) -> ToolResult:
    error = _validate_absolute_path(path)
    if error:
        return ToolResult(ok=False, error=error)

    if is_path_forbidden(path):
        return ToolResult(ok=False, error=f"Đường dẫn '{path}' nằm trong danh sách cấm truy cập.")

    if _is_systemd_unit_path(path):
        return ToolResult(
            ok=False,
            error=(
                f"Không được sửa unit file systemd '{path}' qua tool này — ExecStart=/User=... "
                "trong unit file có thể chạy tùy ý bằng root sau daemon-reload, tương đương Tier 3."
            ),
        )

    if _is_systemd_dropin_path(path):
        return ToolResult(
            ok=False,
            error=(
                f"Không được sửa file trong drop-in directory unit systemd '{path}' — mọi file trong "
                "thư mục *.service.d/*.socket.d/*.timer.d/*.path.d/*.mount.d/ đều bị systemd tự merge "
                "vào unit gốc lúc daemon-reload, override ExecStart=/User=... tương đương Tier 3 y hệt "
                "sửa thẳng unit file."
            ),
        )

    if not _is_editable_config_path(path):
        return ToolResult(ok=False, error="File nằm ngoài phạm vi cấu hình được phép sửa.")

    target = Path(os.path.normpath(path))
    if not target.exists():
        return ToolResult(
            ok=False,
            error=f"File '{path}' không tồn tại — tool này chỉ sửa file cấu hình đã có, không tạo file mới.",
        )

    if os.geteuid() != 0 and not (
        os.access(target, os.W_OK) and os.access(target.parent, os.W_OK)
    ):
        # Only this narrow helper is elevated. It revalidates the path and uses
        # the same no-symlink, backup-before-write implementation below.
        try:
            plan = execution_plan or preflight_edit_config_file(
                str(target), new_content, call_id=call_id or _direct_call_id()
            )
            result = subprocess.run(
                list(plan.argv), input=new_content, capture_output=True, text=True, timeout=60
            )
        except PrivilegeError as exc:
            return ToolResult(ok=False, error=str(exc))
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return ToolResult(ok=False, error="Không chạy được helper sửa file cấu hình.")
        # The helper received plan.argv, so errors now are terminal. sudo does
        # not reliably distinguish a rejected ticket from a helper that already
        # changed a file; retrying could therefore repeat a partial edit.
        if result.returncode != 0:
            return ToolResult(ok=False, error="Helper sửa file cấu hình thất bại.")
        try:
            response = json.loads(result.stdout)
            if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
                raise ValueError("invalid helper result")
            return ToolResult(ok=response["ok"], data=response.get("data"), error=response.get("error"))
        except (TypeError, ValueError):
            return ToolResult(ok=False, error="Helper sửa file cấu hình trả kết quả không hợp lệ.")

    return _edit_config_file_local(target, new_content)


def _edit_config_file_local(target: Path, new_content: str) -> ToolResult:
    """Filesystem operation shared by direct execution and the root helper."""
    path = str(target)
    try:
        with open_regular_file_no_symlinks(str(target), os.O_RDWR) as (fd, parent_fd, name):
            if os.fstat(fd).st_nlink != 1:
                return ToolResult(ok=False, error="Không sửa file có hard link.")
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            backup_name = f"{name}.bak.{timestamp}"
            backup_path = target.with_name(backup_name)
            backup_fd = os.open(
                backup_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=parent_fd,
            )
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                while chunk := os.read(fd, 65536):
                    _write_all(backup_fd, chunk)
                os.fsync(backup_fd)
            except OSError:
                os.close(backup_fd)
                os.unlink(backup_name, dir_fd=parent_fd)
                return ToolResult(ok=False, error="Không backup được file trước khi ghi.")
            else:
                os.close(backup_fd)

            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            _write_all(fd, new_content.encode("utf-8"))
            os.fsync(fd)
    except (OSError, ValueError):
        return ToolResult(ok=False, error="Không sửa được file cấu hình thường hoặc path chứa symlink.")

    return ToolResult(ok=True, data={"path": path, "backup_path": str(backup_path)})
