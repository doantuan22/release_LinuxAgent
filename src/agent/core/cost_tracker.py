"""Theo dõi chi phí LLM tích lũy theo ngày/session (Giai đoạn 6).

Dùng số token THẬT từ LLMResponse.usage (đã có sẵn từ Giai đoạn 1) — KHÔNG dùng
ước lượng len//4 của agent/memory/compaction.py, ước lượng đó chỉ để quyết định
lúc nào cần nén history, không đủ chính xác để tính tiền.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date as date_cls
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agent.memory.schemas import check_schema_version

SCHEMA_VERSION = 1


def _connect(db_path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def init_cost_db(db_path: str | Path) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_log (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                date TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                cost_usd REAL,
                created_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            )
            """
        )
        conn.commit()
        check_schema_version(conn, "usage_log")
    finally:
        conn.close()


def _compute_cost(
    prompt_tokens: int, completion_tokens: int, price_config: dict[str, Any] | None
) -> float | None:
    if not price_config:
        return None

    input_price = price_config.get("input_price_per_1m")
    output_price = price_config.get("output_price_per_1m")
    if input_price is None and output_price is None:
        return None

    cost = 0.0
    if input_price is not None:
        cost += prompt_tokens / 1_000_000 * input_price
    if output_price is not None:
        cost += completion_tokens / 1_000_000 * output_price
    return cost


def record_usage(
    db_path: str | Path,
    session_id: str | None,
    provider_name: str,
    model: str,
    usage: dict[str, Any],
    price_config: dict[str, Any] | None = None,
) -> None:
    init_cost_db(db_path)

    prompt_tokens = usage.get("prompt_tokens") or 0
    completion_tokens = usage.get("completion_tokens") or 0
    cost_usd = _compute_cost(prompt_tokens, completion_tokens, price_config)

    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO usage_log "
            "(id, session_id, date, provider, model, prompt_tokens, completion_tokens, "
            "cost_usd, created_at, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                session_id,
                date_cls.today().isoformat(),
                provider_name,
                model,
                prompt_tokens,
                completion_tokens,
                cost_usd,
                datetime.now(timezone.utc).isoformat(),
                SCHEMA_VERSION,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_daily_total(db_path: str | Path, date: str | None = None) -> dict[str, float]:
    target_date = date or date_cls.today().isoformat()
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0), "
            "COALESCE(SUM(cost_usd), 0) FROM usage_log WHERE date = ?",
            (target_date,),
        ).fetchone()
    finally:
        conn.close()
    return {"tokens": row[0], "cost_usd": row[1]}


def get_session_total(db_path: str | Path, session_id: str) -> dict[str, float]:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0), "
            "COALESCE(SUM(cost_usd), 0) FROM usage_log WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    return {"tokens": row[0], "cost_usd": row[1]}


def get_daily_summary(
    db_path: str | Path, date: str | None = None
) -> dict[str, int | float]:
    """Tổng hợp usage theo ngày cho CLI mà không đổi contract hàm total cũ."""
    init_cost_db(db_path)
    target_date = date or date_cls.today().isoformat()
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(prompt_tokens), 0), "
            "COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(cost_usd), 0) "
            "FROM usage_log WHERE date = ?",
            (target_date,),
        ).fetchone()
    finally:
        conn.close()
    return {
        "requests": int(row[0]),
        "prompt_tokens": int(row[1]),
        "completion_tokens": int(row[2]),
        "cost_usd": float(row[3]),
    }


def get_session_summary(
    db_path: str | Path, session_id: str
) -> dict[str, int | float]:
    """Tổng hợp usage của đúng một session; không có record thì trả toàn số 0."""
    init_cost_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(prompt_tokens), 0), "
            "COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(cost_usd), 0) "
            "FROM usage_log WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    return {
        "requests": int(row[0]),
        "prompt_tokens": int(row[1]),
        "completion_tokens": int(row[2]),
        "cost_usd": float(row[3]),
    }


def check_budget(
    soft_limit_usd: float, hard_limit_usd: float, current_total: float
) -> Literal["ok", "soft", "hard"]:
    if current_total >= hard_limit_usd:
        return "hard"
    if current_total >= soft_limit_usd:
        return "soft"
    return "ok"
