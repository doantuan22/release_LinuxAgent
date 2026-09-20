"""Lỗi ứng dụng dùng chung ở boundary service → CLI."""

from __future__ import annotations

from agent.core.redaction import redact_known_secrets as redact_cli_text


class ProviderConfigError(Exception):
    """Provider/config runtime không hợp lệ hoặc không đọc được."""


class AgentExecutionError(Exception):
    """Agent loop không hoàn tất do lỗi thực thi, không phải policy denial."""
