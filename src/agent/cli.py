"""Entry point CLI (CLI.md mục 2, 26): chỉ đăng ký command, không chứa business
logic — mọi logic thật nằm ở services/, được commands/ gọi lại.
"""

from __future__ import annotations

import typer
from rich.console import Console

from agent import bootstrap
from agent.commands.ask import ask
from agent.commands.audit import audit_app
from agent.commands.chat import chat
from agent.commands.config import config_app
from agent.commands.cost import cost_app
from agent.commands.doctor import doctor
from agent.commands.memory import memory_app
from agent.commands.profile import profile
from agent.commands.rag import rag_app
from agent.commands.sessions import sessions_app
from agent.commands.uninstall import uninstall
from agent.errors import AgentExecutionError, ProviderConfigError
from agent.llm.base import LLMProviderError

app = typer.Typer(help="Linux Agent — trợ lý AI hỗ trợ Linux có khả năng hành động thật trên máy.")
app.command()(chat)
app.command()(ask)
app.command()(profile)
app.command()(doctor)
app.command()(uninstall)
app.add_typer(sessions_app, name="sessions")
app.add_typer(config_app, name="config")
app.add_typer(audit_app, name="audit")
app.add_typer(cost_app, name="cost")
app.add_typer(memory_app, name="memory")
app.add_typer(rag_app, name="rag")


@app.callback()
def _bootstrap_for_command(ctx: typer.Context) -> None:
    """Doctor và debug search chỉ đọc dữ liệu hiện có, không bootstrap/seed. `uninstall`
    cũng không được bootstrap: nó sẽ tạo lại chính các thư mục sắp bị xóa."""
    if ctx.invoked_subcommand not in {"doctor", "memory", "rag", "uninstall"}:
        bootstrap.ensure_bootstrapped()


def main() -> int:
    try:
        app()
    except ProviderConfigError:
        Console(stderr=True).print("[red]Provider configuration error. Check agent config show.[/]")
        return 3
    except (AgentExecutionError, LLMProviderError):
        Console(stderr=True).print("[red]Agent execution failed.[/]")
        return 4
    except KeyboardInterrupt:
        # Lưới an toàn cuối cùng (mục 23) — vòng lặp REPL của `chat` đã tự bắt
        # Ctrl+C ở từng bước, đây chỉ cho các điểm còn lại (bootstrap, `ask` khi
        # đang chờ mạng) để không có traceback nào lọt ra ngoài.
        Console(stderr=True).print("\n[yellow]Interrupted.[/]")
        return 130
    except Exception:
        # Safety net: exception bất ngờ có thể chứa API key/HTTP body.
        Console(stderr=True).print("[red]Unexpected CLI error.[/]")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
