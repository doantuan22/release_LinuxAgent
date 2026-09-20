"""Đọc/ghi `providers.json` tại thư mục config XDG (CLI.md mục 12-15).

Không sửa trực tiếp `repo/config/providers.json` sau khi package được cài (mục
15) — chỉ đọc/ghi bản ở `agent.paths.providers_file()`. Nếu file XDG chưa tồn
tại (ví dụ gọi module này trước khi `bootstrap.ensure_bootstrapped()` kịp chạy,
hoặc dùng độc lập ngoài luồng CLI), copy bản mẫu package_data.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from agent import paths
from agent.core.resource_loader import copy_resource


def _ensure_providers_file_exists() -> Path:
    target = paths.providers_file()
    if target.exists():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    copy_resource("providers.json", target)
    return target


_BUDGET_FIELDS = ("soft_limit_usd", "hard_limit_usd")


def _validate_config(config: dict) -> None:
    cost_config = config.get("cost", {})
    if cost_config is None:
        cost_config = {}
    if not isinstance(cost_config, dict):
        raise ValueError("Trường 'cost' phải là một object.")

    for field in _BUDGET_FIELDS:
        value = cost_config.get(field)
        if value is None:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"Trường 'cost.{field}' phải là số dương hoặc null.")

    # run_agent_loop chỉ áp ngân sách khi có đủ cả hai ngưỡng; cấu hình một nửa
    # sẽ bị bỏ qua âm thầm nên phải bị từ chối ngay lúc load.
    if (cost_config.get("soft_limit_usd") is None) != (cost_config.get("hard_limit_usd") is None):
        raise ValueError("'cost.soft_limit_usd' và 'cost.hard_limit_usd' phải được đặt cùng nhau hoặc cùng null.")


def read_config() -> dict:
    target = _ensure_providers_file_exists()
    config = json.loads(target.read_text(encoding="utf-8"))
    _validate_config(config)
    return config


def write_config(config: dict) -> None:
    target = _ensure_providers_file_exists()
    target.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
