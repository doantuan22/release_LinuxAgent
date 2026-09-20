"""Narrow sudo preparation for commands already authorized as Tier 2 actions.

This module never reads a password. Only ``sudo -v`` may prompt, directly on an
interactive terminal; every real command is executed with ``sudo -n``.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from collections.abc import Sequence

from agent.core.tty_detection import has_interactive_tty

_CHECK_TIMEOUT_SECONDS = 10
_AUTH_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class ExecutionPlan:
    """One preflight-approved privileged invocation.

    ``tuple`` deliberately represents argv rather than a shell string (and is
    stricter than a mutable ``list[str]``).  It is passed directly to
    ``subprocess.run`` as an argv sequence.  A plan belongs to exactly one
    tool-call ID and must be discarded by its caller after that call ends.
    """

    call_id: str
    base_argv: tuple[str, ...]
    argv: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.call_id:
            raise ValueError("ExecutionPlan requires a call_id")
        if not self.base_argv or not self.argv:
            raise ValueError("ExecutionPlan argv must not be empty")


class PrivilegeError(Exception):
    """A privilege precondition failed before the real command ran."""


class SudoUnavailable(PrivilegeError):
    pass


class PrivilegeAuthenticationRequired(PrivilegeError):
    """Authentication is needed before an exact, not-yet-invoked plan can run."""

    def __init__(self, message: str, *, plan: ExecutionPlan | None = None) -> None:
        super().__init__(message)
        self.plan = plan


class PrivilegeAuthenticationFailed(PrivilegeError):
    pass


class PrivilegeAuthenticationCancelled(PrivilegeError):
    pass


class PrivilegeCheckFailed(PrivilegeError):
    pass


class PrivilegeManager:
    @property
    def is_root(self) -> bool:
        return os.geteuid() == 0

    @property
    def sudo_available(self) -> bool:
        return shutil.which("sudo") is not None

    @property
    def interactive(self) -> bool:
        return has_interactive_tty()

    def ticket_valid(self) -> bool:
        """Also succeeds for sudo rules that allow ``true`` without a password."""
        if not self.sudo_available:
            return False
        try:
            result = subprocess.run(
                ["sudo", "-n", "true"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=_CHECK_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise SudoUnavailable("Không tìm thấy sudo trên hệ thống này.") from exc
        except subprocess.TimeoutExpired as exc:
            raise PrivilegeCheckFailed("Kiểm tra sudo ticket hết thời gian chờ.") from exc
        except OSError as exc:
            raise PrivilegeCheckFailed("Không kiểm tra được sudo ticket.") from exc
        return result.returncode == 0

    def _command_listed_without_auth(self, command: Sequence[str]) -> bool:
        """Allow a command-scoped NOPASSWD rule even if ``sudo true`` is denied.

        Listing is only a preflight hint. The final ``sudo -n`` still enforces
        the actual command policy and cannot ask for a password.
        """
        try:
            result = subprocess.run(
                ["sudo", "-n", "-l", "--", *command],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=_CHECK_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise SudoUnavailable("Không tìm thấy sudo trên hệ thống này.") from exc
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise PrivilegeCheckFailed("Không kiểm tra được quyền sudo cho lệnh.") from exc
        return result.returncode == 0

    def can_run_noninteractive(self, command: Sequence[str] | None = None) -> bool:
        if self.is_root:
            return True
        try:
            return self.ticket_valid() or (command is not None and self._command_listed_without_auth(command))
        except PrivilegeError:
            return False

    def prepare_command_noninteractive(self, command: Sequence[str]) -> list[str]:
        """Preflight one exact command without ever invoking ``sudo -v``.

        This is the GUI-safe predicate used before a resumable Tier 2 action:
        it checks both the ticket and command-scoped NOPASSWD policy, but does
        not execute the command or ask for a password.
        """
        if not command:
            raise ValueError("Privileged command must not be empty")
        if self.is_root:
            return list(command)
        if not self.sudo_available:
            raise SudoUnavailable("Không tìm thấy sudo trên hệ thống này.")
        if self.ticket_valid() or self._command_listed_without_auth(command):
            return ["sudo", "-n", *command]
        raise PrivilegeAuthenticationRequired("Cần xác thực sudo trên terminal tương tác.")

    def preflight(self, command: Sequence[str], *, call_id: str) -> ExecutionPlan:
        """Validate one exact argv and return its immutable execution plan.

        This has no action side effect: it only performs the non-interactive
        sudo ticket / command-policy checks in ``prepare_command_noninteractive``.
        A future GUI retry must call ``revalidate`` on this same plan, rather
        than reconstructing a command from the original tool arguments.
        """
        base_argv = tuple(command)
        # Construct the immutable command before checking sudo, so an
        # authentication-required continuation can retain this exact argv
        # without reconstructing it from raw tool arguments after Retry.
        planned_argv = base_argv if self.is_root else ("sudo", "-n", *base_argv)
        plan = ExecutionPlan(call_id=call_id, base_argv=base_argv, argv=planned_argv)
        try:
            checked_argv = tuple(self.prepare_command_noninteractive(base_argv))
        except PrivilegeAuthenticationRequired as exc:
            raise PrivilegeAuthenticationRequired(str(exc), plan=plan) from exc
        if checked_argv != plan.argv:
            raise PrivilegeCheckFailed("Quyền sudo thay đổi trong khi preflight lệnh.")
        return plan

    def revalidate(self, plan: ExecutionPlan) -> ExecutionPlan:
        """Re-check sudo policy for an existing plan without rebuilding argv."""
        checked_argv = tuple(self.prepare_command_noninteractive(plan.base_argv))
        if checked_argv != plan.argv:
            raise PrivilegeCheckFailed("Quyền sudo thay đổi sau khi preflight lệnh.")
        return plan

    def prepare_command(self, command: Sequence[str]) -> list[str]:
        """Return a command that cannot prompt for sudo authentication.

        The caller remains responsible for Tier 2 confirmation, auditing, and
        actually running its narrow command. No command is executed here apart
        from the sudo ticket check and, on a TTY, ``sudo -v``.
        """
        try:
            return self.prepare_command_noninteractive(command)
        except PrivilegeAuthenticationRequired:
            pass
        if not self.interactive:
            raise PrivilegeAuthenticationRequired("Cần xác thực sudo trên terminal tương tác.")

        try:
            # Inherit the terminal. sudo itself reads the password; the agent
            # neither supplies stdin nor captures/records authentication output.
            result = subprocess.run(["sudo", "-v"], timeout=_AUTH_TIMEOUT_SECONDS)
        except KeyboardInterrupt as exc:
            raise PrivilegeAuthenticationCancelled("Đã hủy xác thực sudo.") from exc
        except FileNotFoundError as exc:
            raise SudoUnavailable("Không tìm thấy sudo trên hệ thống này.") from exc
        except subprocess.TimeoutExpired as exc:
            raise PrivilegeAuthenticationFailed("Xác thực sudo hết thời gian chờ.") from exc

        if result.returncode in (130, -signal.SIGINT):
            raise PrivilegeAuthenticationCancelled("Đã hủy xác thực sudo.")
        if result.returncode != 0:
            raise PrivilegeAuthenticationFailed("Xác thực sudo thất bại hoặc người dùng không có quyền sudo.")
        return ["sudo", "-n", *command]
