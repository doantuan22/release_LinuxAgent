"""Audit log: ghi JSON Lines cho MỌI lời gọi tool, kể cả bị từ chối.

Không phải bảng SQLite, nhưng vẫn giữ trường schema_version ở mỗi dòng để nhất
quán với nguyên tắc an toàn #7 và dễ nâng cấp định dạng sau này.
"""

from __future__ import annotations

import json
import hmac
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent import paths
from agent.tools.schemas import Tier, ToolResult

SCHEMA_VERSION = 2
DEFAULT_AUDIT_LOG_PATH = paths.audit_log_path()
_AUDIT_HMAC_KEY = os.urandom(32)


def log_tool_call(
    tool_name: str,
    args: dict[str, Any],
    result: ToolResult | None,
    tier: Tier | None,
    *,
    log_path: Path = DEFAULT_AUDIT_LOG_PATH,
    duration_ms: float | None = None,
    event: str = "completed",
) -> None:
    entry = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_name": tool_name if tier is not None else "<unknown_tool>",
        "tier": tier.value if tier is not None else None,
        # Tool arguments may be arbitrary text, including secrets not known to
        # env/.env. HMAC permits correlating intent/result within this process
        # without leaving a reversible unsalted hash of low-entropy secrets.
        "args": "[REDACTED]",
        "args_digest": hmac.new(
            _AUDIT_HMAC_KEY,
            json.dumps(args, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"),
            "sha256",
        ).hexdigest(),
        "event": event,
        "ok": result.ok if result is not None else None,
        # Raw tool/OS errors are untrusted and may contain unregistered secrets.
        "error": ("Operation denied." if event == "denied" else "Tool failed.")
        if result is not None and result.error else None,
        "duration_ms": duration_ms,
    }

    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        if event == "started":
            f.flush()
            os.fsync(f.fileno())
