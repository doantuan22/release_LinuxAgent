"""Interface chung cho package manager (Ports & Adapters — nguyên tắc an toàn #4).

Tool ở tools/tier2_actions.py chỉ nói chuyện qua interface này, không hardcode
theo một package manager cụ thể — adapter thật (apt/dnf/pacman/zypper) được
chọn dựa trên package manager phát hiện được từ system_profile.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import uuid4

from agent.system.privilege import ExecutionPlan, PrivilegeManager
from agent.tools.schemas import ToolResult


class PackageManagerAdapter(ABC):
    @staticmethod
    def _preflight_install_command(command: list[str], *, call_id: str) -> ExecutionPlan:
        """Build the only execution command for both normal run and retry."""
        return PrivilegeManager().preflight(command, call_id=call_id)

    @staticmethod
    def _direct_call_id() -> str:
        """Compatibility ID for direct adapter callers outside the loop contract.

        Production tool calls will receive their loop ``call_id`` through the
        preflight callback.  This uncached ID keeps the legacy public adapter
        API safe until that continuation wiring is added.
        """
        return f"direct-{uuid4().hex}"

    @abstractmethod
    def preflight(self, package_name: str, *, call_id: str) -> ExecutionPlan:
        raise NotImplementedError

    @abstractmethod
    def execute(self, plan: ExecutionPlan) -> ToolResult:
        raise NotImplementedError

    @abstractmethod
    def install(self, package_name: str) -> ToolResult:
        raise NotImplementedError

    @abstractmethod
    def search(self, query: str) -> ToolResult:
        raise NotImplementedError
