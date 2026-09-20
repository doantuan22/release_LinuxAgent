"""``agent doctor`` — report diagnostic môi trường (CLI.md mục 17)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.text import Text

from agent.errors import redact_cli_text
from agent.services import doctor_service

console = Console()

_STATUS_DISPLAY = {
    "ok": ("✓", "green"),
    "warning": ("!", "yellow"),
    "error": ("✗", "red"),
}


def doctor() -> None:
    """Kiểm tra môi trường. Issue được report nhưng không làm command exit khác 0."""
    try:
        results = doctor_service.run_all_checks()
    except Exception:
        # Không đưa exception ra output: lỗi cấp service cũng có thể mang secret.
        console.print("[red]Linux Agent Doctor could not run.[/]")
        raise typer.Exit(code=1)

    console.print("[bold]Linux Agent Doctor[/]")
    for result in results:
        symbol, style = _STATUS_DISPLAY.get(result.status, ("✗", "red"))
        line = Text()
        line.append(f"[{symbol}] ", style=style)
        line.append(redact_cli_text(result.name), style="bold")
        line.append(f": {redact_cli_text(result.message)}")
        console.print(line)

    issue_count = sum(result.status != "ok" for result in results)
    console.print()
    console.print(f"{issue_count} issues found.")
