"""Factory dựng LLMProvider từ cấu hình được truyền hoặc bản mặc định đóng gói."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent.core.resource_loader import get_resource

from .base import LLMProvider
from .anthropic_provider import AnthropicProvider
from .openai_compatible import OpenAICompatibleProvider

_PROVIDER_REGISTRY: dict[str, type[LLMProvider]] = {
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
}

# Các trường thuộc về factory/loop (Giai đoạn 6: fallback, theo dõi chi phí) — KHÔNG
# phải tham số khởi tạo của provider cụ thể, phải pop ra trước khi truyền **provider_config.
_NON_CONSTRUCTOR_FIELDS = ("fallback", "input_price_per_1m", "output_price_per_1m")


def _load_config(config_path: str | Path | None) -> dict:
    if config_path is None:
        with get_resource("providers.json") as source:
            return json.loads(source.read_text(encoding="utf-8"))
    return json.loads(Path(config_path).read_text(encoding="utf-8"))


def _build_provider(config: dict, name: str, config_path: str | Path) -> LLMProvider:
    try:
        provider_config = dict(config["providers"][name])
    except KeyError as e:
        raise ValueError(f"Không tìm thấy provider '{name}' trong '{config_path}'.") from e

    provider_type = provider_config.pop("type")
    provider_cls = _PROVIDER_REGISTRY.get(provider_type)
    if provider_cls is None:
        raise ValueError(
            f"Provider type '{provider_type}' chưa được đăng ký. "
            f"Các loại hiện có: {list(_PROVIDER_REGISTRY)}"
        )

    for field in _NON_CONSTRUCTOR_FIELDS:
        provider_config.pop(field, None)

    api_key_env = provider_config.pop("api_key_env", None)
    if api_key_env:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ValueError(f"Thiếu biến môi trường '{api_key_env}' cho provider '{name}'.")
        provider_config["api_key"] = api_key

    return provider_cls(**provider_config)


def build_provider_from_fields(
    provider_type: str,
    *,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
) -> LLMProvider:
    """Dựng `LLMProvider` trực tiếp từ field đã có, KHÔNG qua `providers.json`
    — dùng cho test connection (Phase 9 gui_implementation_plan.md) khi config
    có thể chưa được lưu (form Settings/Onboarding). Dùng lại đúng
    `_PROVIDER_REGISTRY` mà `_build_provider()` (đọc từ file) cũng dùng, để
    type->class chỉ có một nguồn sự thật duy nhất — không thêm nhánh
    if provider == ... nào ở service.

    Raise `ValueError` nếu `provider_type` chưa đăng ký; caller
    (`config_service.test_connection`) tự map sang status "config_invalid",
    không để lộ ra ngoài dạng thô.
    """
    provider_cls = _PROVIDER_REGISTRY.get(provider_type)
    if provider_cls is None:
        raise ValueError(
            f"Provider type '{provider_type}' chưa được đăng ký. "
            f"Các loại hiện có: {list(_PROVIDER_REGISTRY)}"
        )

    kwargs: dict[str, Any] = {"model": model}
    if base_url is not None:
        kwargs["base_url"] = base_url
    if api_key is not None:
        kwargs["api_key"] = api_key
    if timeout is not None:
        kwargs["timeout"] = timeout
    return provider_cls(**kwargs)


def load_provider(config_path: str | Path | None = None) -> LLMProvider:
    config = _load_config(config_path)
    return _build_provider(config, config["active"], config_path or "packaged providers.json")


def load_provider_with_fallback(
    config_path: str | Path | None = None,
) -> tuple[LLMProvider, LLMProvider | None]:
    """Trả (provider chính, provider dự phòng | None) theo trường "fallback" của
    provider active trong config. Không có "fallback" hoặc rỗng -> None (Giai đoạn 6:
    core/network_fallback.py sẽ không thử fallback nếu None)."""
    config = _load_config(config_path)
    active_name = config["active"]
    source = config_path or "packaged providers.json"
    primary = _build_provider(config, active_name, source)

    fallback_name = config["providers"].get(active_name, {}).get("fallback")
    fallback = _build_provider(config, fallback_name, source) if fallback_name else None

    return primary, fallback


def load_judge_provider(config_path: str | Path | None = None) -> LLMProvider:
    """Provider dùng để chấm điểm (Giai đoạn 7 — evals/llm_judge.py). Đọc trường
    top-level "judge_provider" (tên 1 provider khác trong cùng file, thường model
    rẻ/nhanh hơn) — không có thì fallback dùng luôn provider active."""
    config = _load_config(config_path)
    judge_name = config.get("judge_provider") or config["active"]
    return _build_provider(config, judge_name, config_path or "packaged providers.json")


def get_price_config(
    config_path: str | Path | None = None, provider_name: str | None = None
) -> dict[str, float | None]:
    """Trả {"input_price_per_1m": ..., "output_price_per_1m": ...} cho provider (mặc
    định là provider active). Giá trị None nếu chưa cấu hình -> cost_tracker không
    tính ra $, chỉ đếm token."""
    config = _load_config(config_path)
    name = provider_name or config["active"]
    provider_config = config["providers"].get(name, {})
    return {
        "input_price_per_1m": provider_config.get("input_price_per_1m"),
        "output_price_per_1m": provider_config.get("output_price_per_1m"),
    }
