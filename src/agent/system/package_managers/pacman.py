"""Adapter pacman thật — Tier A (Arch Linux và dẫn xuất).

install() gọi `pacman -S --noconfirm` qua PrivilegeManager; root chạy trực tiếp,
user thường chỉ thực thi lệnh thật với `sudo -n`.
"""

from __future__ import annotations

import subprocess

from agent.system.package_managers.base import PackageManagerAdapter
from agent.system.privilege import ExecutionPlan, PrivilegeError
from agent.tools.schemas import ToolResult

_INSTALL_TIMEOUT_SECONDS = 300
_SEARCH_TIMEOUT_SECONDS = 30


class PacmanAdapter(PackageManagerAdapter):
    def preflight(self, package_name: str, *, call_id: str) -> ExecutionPlan:
        if not package_name or not package_name.strip():
            raise ValueError("Thiếu tên gói cần cài.")
        return self._preflight_install_command(
            ["pacman", "-S", "--noconfirm", package_name], call_id=call_id
        )

    def execute(self, plan: ExecutionPlan) -> ToolResult:
        package_name = plan.base_argv[-1]
        try:
            result = subprocess.run(list(plan.argv), capture_output=True, text=True, timeout=_INSTALL_TIMEOUT_SECONDS)
        except FileNotFoundError:
            return ToolResult(ok=False, error="Không tìm thấy pacman trên hệ thống này.")
        except subprocess.TimeoutExpired as e:
            return ToolResult(ok=False, error=f"Cài đặt '{package_name}' hết thời gian chờ: {e}")
        # A process was invoked, so a nonzero result remains terminal: sudo
        # diagnostics cannot prove the child did not already change the host.
        if result.returncode != 0:
            return ToolResult(ok=False, error=f"pacman -S '{package_name}' thất bại (mã {result.returncode}).", data={"stdout": result.stdout.strip(), "stderr": result.stderr.strip()})
        return ToolResult(ok=True, data={"package_name": package_name, "stdout": result.stdout.strip()})

    def install(self, package_name: str) -> ToolResult:
        if not package_name or not package_name.strip():
            return ToolResult(ok=False, error="Thiếu tên gói cần cài.")

        try:
            plan = self.preflight(package_name, call_id=self._direct_call_id())
        except PrivilegeError as exc:
            return ToolResult(ok=False, error=str(exc))
        return self.execute(plan)

    def search(self, query: str) -> ToolResult:
        if not query or not query.strip():
            return ToolResult(ok=False, error="Thiếu từ khóa tìm kiếm.")

        try:
            result = subprocess.run(
                ["pacman", "-Ss", query], capture_output=True, text=True, timeout=_SEARCH_TIMEOUT_SECONDS
            )
        except FileNotFoundError:
            return ToolResult(ok=False, error="Không tìm thấy pacman trên hệ thống này.")
        except subprocess.TimeoutExpired as e:
            return ToolResult(ok=False, error=f"Tìm kiếm '{query}' hết thời gian chờ: {e}")

        return ToolResult(ok=True, data={"query": query, "output": result.stdout.strip()})
