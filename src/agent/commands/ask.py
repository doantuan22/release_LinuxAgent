"""agent ask — hỏi một câu rồi thoát (CLI.md mục 5).

Chính sách bảo mật: Tier 1 được phép chạy, Tier 2 LUÔN bị từ chối — dùng
always_deny_confirm (Phase 2), không phụ thuộc sys.stdin.isatty() như agent chat,
nên `agent ask "cài htop"` chạy trực tiếp trong terminal có TTY thật vẫn không
bao giờ thực thi Tier 2, và không bao giờ treo chờ input().
"""

from __future__ import annotations

import sys

import typer
from rich.console import Console

from agent.core.confirm_policies import always_deny_confirm
from agent.core.tool_observation import parse_tool_payload
from agent.errors import redact_cli_text
from agent.llm.base import Message, Role
from agent.services.agent_service import (
    last_assistant_text,
    load_configured_budget_limits,
    load_configured_provider_with_fallback,
    run_ask,
)

console = Console()

_TIER2_DENIED_MARKER = "(Tier 2) bị từ chối"


def ask(
    prompt: str = typer.Argument(..., help='Câu hỏi cần trả lời, hoặc "-" để đọc từ stdin'),
) -> None:
    """Hỏi một câu rồi thoát — phù hợp cho shell script/CI/debug."""
    text = sys.stdin.read() if prompt == "-" else prompt
    text = text.strip()
    if not text:
        console.print("[red]Thiếu nội dung câu hỏi.[/]")
        raise typer.Exit(code=1)

    provider, fallback_provider = load_configured_provider_with_fallback()
    budget_limits = load_configured_budget_limits()
    history = run_ask(
        provider,
        text,
        fallback_provider=fallback_provider,
        confirm_callback=always_deny_confirm,
        soft_limit_usd=budget_limits.soft_limit_usd,
        hard_limit_usd=budget_limits.hard_limit_usd,
    )

    if _has_tier2_denial(history):
        console.print("Operation denied: Tier 2 actions are not allowed in agent ask.")
        raise typer.Exit(code=5)

    answer = last_assistant_text(history)
    console.print(redact_cli_text(answer or ""))


def _has_tier2_denial(history: list[Message]) -> bool:
    for message in history:
        if message.role != Role.TOOL or not message.content:
            continue
        payload = parse_tool_payload(message.content)
        if payload is None:
            continue
        if payload.get("ok") is False and _TIER2_DENIED_MARKER in (payload.get("error") or ""):
            return True
    return False
