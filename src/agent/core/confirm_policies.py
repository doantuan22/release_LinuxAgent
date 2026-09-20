"""Chính sách xác nhận Tier 2 (CLI.md mục 6), tách khỏi ToolExecutor thành các hàm
độc lập, đặt tên rõ ràng để mỗi command (chat/ask/GUI sau này) chọn đúng policy khi
khởi tạo ToolExecutor.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from agent.core.confirmation import ConfirmationRequest
from agent.core.redaction import redact_audit_data, redact_known_secrets
from agent.core.tty_detection import has_interactive_tty

# Console riêng cho panel xác nhận (không dùng chung instance với spinner của
# commands/chat.py) — CLI.md mục 7 yêu cầu hiện đủ tool/tier/arguments/description
# trước khi hỏi [y/N], không chỉ một dòng input() trần.
_console = Console()


def _render_confirmation_panel(request: ConfirmationRequest) -> None:
    lines = [f"Tool: {redact_known_secrets(request.tool_name)}", f"Tier: {request.tier.value}", ""]
    for key, value in redact_audit_data(request.arguments).items():
        lines.append(f"{key}: {value}")
    lines.append("")
    lines.append(redact_known_secrets(request.description or "This action will modify your system."))
    _console.print(
        Panel(
            "\n".join(lines),
            title="System change requires confirmation",
            border_style="yellow",
        )
    )


def interactive_confirm(request: ConfirmationRequest) -> bool:
    """Hiện panel tool/tier/arguments/description (CLI.md mục 7) rồi hỏi qua
    input() nếu có TTY; tự động từ chối nếu không có TTY.

    Đây là hành vi mặc định của ToolExecutor — logic từ chối non-interactive y hệt
    đã viết ở Giai đoạn 2, chỉ tách ra thành hàm có tên rõ ràng để tái sử dụng.
    """
    if not has_interactive_tty():
        return False

    _render_confirmation_panel(request)
    try:
        answer = input("Proceed? [y/N]: ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def always_deny_confirm(request: ConfirmationRequest) -> bool:
    """Luôn từ chối Tier 2, bất kể có TTY hay không. Dùng cho agent ask."""
    return False
