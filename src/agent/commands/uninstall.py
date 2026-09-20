"""agent uninstall — xóa sạch dữ liệu rồi gỡ Linux Agent.

USER COMMAND trực tiếp (như `agent config`/`agent doctor`), KHÔNG phải tool của
agent: không có tool "uninstall" nào trong registry, không đi qua Tier 2/
ConfirmationRequest. Xác nhận bằng cách gõ CHÍNH XÁC `CONFIRMATION_PHRASE`; không
có flag/biến môi trường nào bỏ qua bước này (kể cả --yes/--force — cố ý không có).

Exit code: 0 xong (kể cả khi chỉ xóa dữ liệu và in hướng dẫn gỡ thủ công), 1 người
dùng hủy/không xác nhận (không xóa gì), 4 xóa dữ liệu chưa xong 100% hoặc không
khởi chạy được lệnh gỡ package.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.text import Text

from agent.core import uninstall_service
from agent.errors import redact_cli_text

console = Console()

_EXIT_CANCELLED = 1
_EXIT_FAILED = 4


def _print_manual_instructions() -> None:
    console.print("Hãy tự chạy MỘT trong các lệnh sau, tùy cách bạn đã cài Linux Agent:")
    for label, command in uninstall_service.MANUAL_REMOVAL_COMMANDS:
        console.print(Text(f"  ({label})  {command}"))


def _print_plan(plan: uninstall_service.UninstallPlan, installer: str) -> None:
    console.print("[bold red]Sẽ bị XÓA VĨNH VIỄN (không thể hoàn tác):[/]")
    for target in plan.targets:
        state = "tồn tại" if target.exists else "không tồn tại, bỏ qua"
        # soft_wrap: không bao giờ cắt "…" hay tách path giữa chừng — phải thấy đủ và
        # copy được nguyên path sắp bị xóa (terminal tự xuống dòng nếu quá rộng).
        console.print(Text(f"  • {target.path}  [{state}]"), soft_wrap=True)
        console.print(Text(f"      {target.description}", style="dim"), soft_wrap=True)
    if installer == "unknown":
        console.print("Package sẽ KHÔNG được tự gỡ (không xác định được cách cài); bạn sẽ được hướng dẫn gỡ thủ công.")
    else:
        console.print(f"Sau đó package {uninstall_service.PACKAGE_NAME} sẽ được gỡ bằng: {installer}.")


def uninstall() -> None:
    """Xóa VĨNH VIỄN cấu hình, API key, session, dữ liệu, audit log, cache rồi gỡ package."""
    plan = uninstall_service.plan_uninstall()
    _print_plan(plan, uninstall_service.detect_installer())

    console.print(f'Để xác nhận, gõ chính xác "{uninstall_service.CONFIRMATION_PHRASE}" (mọi nội dung khác sẽ hủy):')
    try:
        answer = input("> ")
    except (EOFError, KeyboardInterrupt):
        answer = None
    if answer != uninstall_service.CONFIRMATION_PHRASE:
        console.print("Đã hủy: không có gì bị xóa.")
        raise typer.Exit(code=_EXIT_CANCELLED)

    result = uninstall_service.wipe_user_data()
    if not result.ok:
        console.print("[red]Xóa dữ liệu CHƯA hoàn tất — package KHÔNG bị gỡ.[/]")
        for failure in result.failed:
            console.print(Text(f"  ✗ {failure.path}: {redact_cli_text(failure.reason)}", style="red"))
        for path in result.removed:
            console.print(Text(f"  ✓ đã xóa {path}"))
        console.print("Hãy xử lý các lỗi trên rồi chạy lại `agent uninstall` (các thư mục đã xóa sẽ được bỏ qua).")
        raise typer.Exit(code=_EXIT_FAILED)

    installer = uninstall_service.detect_installer()
    if installer == "unknown":
        console.print("Đã xóa toàn bộ dữ liệu Linux Agent.")
        console.print("Không xác định được cách bạn đã cài đặt nên package chưa được gỡ.")
        _print_manual_instructions()
        raise typer.Exit(code=0)

    try:
        uninstall_service.spawn_package_removal(installer)
    except OSError as exc:
        console.print("Đã xóa toàn bộ dữ liệu Linux Agent, nhưng KHÔNG khởi chạy được lệnh gỡ package.")
        console.print(Text(f"  Lý do: {redact_cli_text(str(exc))}", style="red"))
        _print_manual_instructions()
        raise typer.Exit(code=_EXIT_FAILED)

    console.print("Đã xóa toàn bộ dữ liệu Linux Agent. Đang gỡ package ở nền — thoát ngay.")
    raise typer.Exit(code=0)
