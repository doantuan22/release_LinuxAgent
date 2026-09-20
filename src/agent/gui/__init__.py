"""GUI PySide6 (docs/gui_implementation_plan.md) — presentation/orchestration
layer trên cùng core/services đã có cho CLI. Không được import từ agent.cli
hay bất kỳ đường chạy CLI nào, để `agent --help` không cần cài PySide6.
"""

from __future__ import annotations
