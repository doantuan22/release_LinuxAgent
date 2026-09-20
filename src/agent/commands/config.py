"""agent config show/set-provider/add-provider (CLI.md mục 12, 13, 14)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.prompt import Prompt

from agent.core.provider_presets import PROVIDER_PRESETS
from agent.errors import redact_cli_text
from agent.services import config_service

console = Console()

config_app = typer.Typer(help="Xem/đổi cấu hình provider LLM.")


@config_app.command("show")
def show_command() -> None:
    """Hiển thị provider configuration hiện tại (mục 12). Không bao giờ in giá
    trị API key thật, kể cả khi debug."""
    try:
        summary = config_service.show_config()
    except Exception:
        console.print("[red]Không thể đọc cấu hình provider.[/]")
        raise typer.Exit(code=3)

    console.print("[bold]Provider Configuration[/]")
    console.print(f"Active Provider   {redact_cli_text(summary.active_provider)}")
    console.print(f"Model             {redact_cli_text(summary.model)}")
    console.print(f"Base URL          {redact_cli_text(summary.base_url or '-')}")
    console.print(f"API Key           {summary.api_key_status}")
    console.print(f"Fallback          {redact_cli_text(summary.fallback or '-')}")


@config_app.command("set-provider")
def set_provider_command(
    provider: str = typer.Argument(..., help="Tên provider (key trong providers.json)"),
) -> None:
    """Đổi provider đang active (mục 13)."""
    try:
        old_name, new_name = config_service.set_active_provider(provider)
    except ValueError:
        console.print("[red]Provider không tồn tại trong cấu hình.[/]")
        raise typer.Exit(code=3)
    except Exception:
        console.print("[red]Không thể đọc/ghi cấu hình provider.[/]")
        raise typer.Exit(code=3)

    console.print("Active provider changed:")
    console.print(f"{redact_cli_text(old_name)} → {redact_cli_text(new_name)}")


@config_app.command("add-provider")
def add_provider_command() -> None:
    """Wizard cấu hình provider mới (mục 14)."""
    console.print("Select provider:")
    for index, preset in enumerate(PROVIDER_PRESETS, start=1):
        console.print(f"{index}. {preset.display_name}")

    choice = Prompt.ask("Choice", choices=[str(i) for i in range(1, len(PROVIDER_PRESETS) + 1)])
    preset = PROVIDER_PRESETS[int(choice) - 1]

    api_key: str | None = None
    if preset.requires_api_key:
        api_key = Prompt.ask("API key", password=True)

    base_url = preset.default_base_url
    if preset.ask_base_url:
        base_url = Prompt.ask("Base URL")

    model = Prompt.ask("Model", default=preset.default_model)

    try:
        config_service.add_provider_from_preset(preset, api_key=api_key, model=model, base_url=base_url)
    except Exception:
        console.print("[red]Không thể ghi cấu hình provider.[/]")
        raise typer.Exit(code=3)

    console.print()
    console.print("[green]Provider configured successfully.[/]")
