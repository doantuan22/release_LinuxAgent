"""Adapter apt/apt-get thật — Tier A (Ubuntu/Debian).

install() chuẩn bị sudo qua PrivilegeManager, rồi chỉ chạy lệnh đã được bảo đảm
không hỏi password. Root chạy apt-get trực tiếp.
DEBIAN_FRONTEND=noninteractive chỉ tắt hộp thoại cấu hình gói của dpkg, không
liên quan tới việc xác thực sudo.
"""

from __future__ import annotations

import os
import subprocess

from agent.system.package_managers.base import PackageManagerAdapter
from agent.system.privilege import ExecutionPlan, PrivilegeError
from agent.tools.schemas import ToolResult

_INSTALL_TIMEOUT_SECONDS = 300
_SEARCH_TIMEOUT_SECONDS = 30


class AptAdapter(PackageManagerAdapter):
    def preflight(self, package_name: str, *, call_id: str) -> ExecutionPlan:
        if not package_name or not package_name.strip():
            raise ValueError("Thiếu tên gói cần cài.")
        return self._preflight_install_command(
            ["apt-get", "install", "-y", package_name], call_id=call_id
        )

    def execute(self, plan: ExecutionPlan) -> ToolResult:
        package_name = plan.base_argv[-1]
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        try:
            result = subprocess.run(
                list(plan.argv),
                capture_output=True,
                text=True,
                timeout=_INSTALL_TIMEOUT_SECONDS,
                env=env,
            )
        except FileNotFoundError:
            return ToolResult(ok=False, error="Không tìm thấy apt-get trên hệ thống này.")
        except subprocess.TimeoutExpired as e:
            return ToolResult(ok=False, error=f"Cài đặt '{package_name}' hết thời gian chờ: {e}")

        # Once subprocess received plan.argv, every error is terminal. sudo's
        # exit status/stderr cannot reliably tell an expired ticket apart from
        # a child command that already made partial changes; retrying either
        # could run a system-changing action twice.
        if result.returncode != 0:
            return ToolResult(
                ok=False,
                error=f"apt-get install '{package_name}' thất bại (mã {result.returncode}).",
                data={"stdout": result.stdout.strip(), "stderr": result.stderr.strip()},
            )
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
                ["apt-cache", "search", query],
                capture_output=True,
                text=True,
                timeout=_SEARCH_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            return ToolResult(ok=False, error="Không tìm thấy apt-cache trên hệ thống này.")
        except subprocess.TimeoutExpired as e:
            return ToolResult(ok=False, error=f"Tìm kiếm '{query}' hết thời gian chờ: {e}")

        return ToolResult(ok=True, data={"query": query, "output": result.stdout.strip()})
