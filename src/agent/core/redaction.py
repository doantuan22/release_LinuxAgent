"""Che credential đã biết trước khi ghi audit/log hoặc hiển thị dữ liệu CLI."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from dotenv import dotenv_values

from agent import paths

_SECRET_LABEL = (
    r"\b(?:[A-Za-z0-9]+[_-])*(?:api[_-]?key|token|secret|password|credential|authorization)"
    r"\b['\"]?\s*[:=]\s*"
)
_QUOTED_ASSIGNED_SECRET = re.compile(rf"(?i)({_SECRET_LABEL})(['\"])(.*?)\2")
_SCHEMED_ASSIGNED_SECRET = re.compile(rf"(?i)({_SECRET_LABEL})(?:Bearer|Basic|Token)\s+([^\s'\",;]+)")
_ASSIGNED_SECRET = re.compile(rf"(?i)({_SECRET_LABEL}['\"]?)([^\s'\",;]+)")
_BEARER_SECRET = re.compile(r"(?i)(\bBearer\s+)([^\s'\",;]+)")
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?P<kind>(?:[A-Z0-9]+ )?PRIVATE KEY)-----.*?"
    r"(?:-----END (?P=kind)-----|\Z)",
    re.IGNORECASE | re.DOTALL,
)
# Chỉ nhận các prefix/độ dài đặc trưng, không đoán chuỗi entropy cao bất kỳ.
_RECOGNIZABLE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:"
    r"sk-[A-Za-z0-9_-]{20,}"
    r"|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}"
    r"|gh[pousr]_[A-Za-z0-9_]{24,}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{20,}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r")(?![A-Za-z0-9_-])"
)


def _is_secret_name(name: str) -> bool:
    upper = name.upper()
    return upper in {"API_KEY", "TOKEN", "SECRET", "PASSWORD", "AUTHORIZATION"} or upper.endswith(
        ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_CREDENTIAL", "_AUTHORIZATION")
    )


def _configured_key_names() -> set[str]:
    """Nhận cả api_key_env đặt tên tùy ý trong providers.json XDG."""
    try:
        config_path = paths.providers_file()
        if not config_path.is_file():
            return set()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        providers = config.get("providers", {})
        if not isinstance(providers, dict):
            return set()
        names: set[str] = set()
        for provider in providers.values():
            if isinstance(provider, dict):
                name = provider.get("api_key_env")
                if isinstance(name, str):
                    names.add(name)
        return names
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        return set()


def redact_known_secrets(value: str) -> str:
    """Che giá trị từ env/.env XDG; debug command không cần bootstrap để dùng."""
    configured_names = _configured_key_names()
    secrets = {
        secret for name, secret in os.environ.items()
        if (_is_secret_name(name) or name in configured_names) and len(secret) >= 4
    }
    try:
        dotenv_path = paths.dotenv_file()
        if dotenv_path.is_file():
            secrets.update(
                secret for name, secret in dotenv_values(dotenv_path).items()
                if (_is_secret_name(name) or name in configured_names)
                and secret is not None and len(secret) >= 4
            )
    except (OSError, UnicodeError, ValueError):
        pass

    rendered = value
    for secret in sorted(secrets, key=len, reverse=True):
        rendered = rendered.replace(secret, "[REDACTED]")
    rendered = _QUOTED_ASSIGNED_SECRET.sub(
        lambda match: match.group(1) + match.group(2) + "[REDACTED]" + match.group(2), rendered
    )
    rendered = _SCHEMED_ASSIGNED_SECRET.sub(lambda match: match.group(1) + "[REDACTED]", rendered)
    rendered = _BEARER_SECRET.sub(lambda match: match.group(1) + "[REDACTED]", rendered)
    rendered = _ASSIGNED_SECRET.sub(lambda match: match.group(1) + "[REDACTED]", rendered)
    return rendered


def sanitize_for_persistence(value: Any) -> Any:
    """Che credential nhận diện chắc chắn trong dữ liệu sắp lưu session.

    Giữ nguyên văn bản không nhãn/không khớp format; không cố suy đoán secret
    bất kỳ trong lời nhắn tự do. Cấu trúc dict/list được giữ để ghi JSON.
    """
    if isinstance(value, str):
        rendered = _PRIVATE_KEY_BLOCK.sub("[REDACTED]", value)
        rendered = redact_known_secrets(rendered)
        return _RECOGNIZABLE_TOKEN.sub("[REDACTED]", rendered)
    if isinstance(value, dict):
        return {sanitize_for_persistence(str(key)): sanitize_for_persistence(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_for_persistence(item) for item in value]
    return value


def redact_audit_data(value: Any) -> Any:
    """Giữ shape JSON audit, mask field nhạy cảm và chuỗi chứa credential."""
    if isinstance(value, dict):
        configured_names = _configured_key_names()
        return {
            redact_known_secrets(str(key)): (
                "[REDACTED]" if _is_secret_name(str(key)) or str(key) in configured_names
                or str(key).lower() in {"new_content", "query"}
                else redact_audit_data(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_audit_data(item) for item in value]
    if isinstance(value, str):
        return redact_known_secrets(value)
    return value
