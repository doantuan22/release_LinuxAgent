"""Application service gộp provider_presets/dotenv_writer/provider_config_writer
(core/) cho commands/config.py (CLI.md mục 12-14) và GUI Settings/Onboarding
(Phase 9-10/18 gui_implementation_plan.md).

"Configured" (có đủ field/API key trong env, xem `api_key_status`) và
"connected" (đã thật sự gọi provider thành công, xem `test_connection()`) là
hai khái niệm TÁCH BIỆT — không dataclass nào ở đây trộn hai trạng thái đó
vào cùng 1 field. `ConfigSummary`/`ProviderSummary` chỉ phản ánh trạng thái
cấu hình tĩnh; `test_connection()` luôn gọi thật lại provider mỗi lần, không
cache/suy diễn từ config summary.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent.core import dotenv_writer, provider_config_writer
from agent.core.provider_presets import ProviderPreset
from agent.llm import factory as llm_factory
from agent.llm.base import (
    Message,
    ProviderAuthenticationError,
    ProviderError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    Role,
)

# Bounded — "Test connection" gọi provider thật, không được treo vô hạn nếu
# provider không phản hồi (mục bắt buộc Phase 9).
_TEST_CONNECTION_TIMEOUT_SECONDS = 10.0
_TEST_CONNECTION_PROMPT = "Say OK."
_TEST_CONNECTION_MAX_TOKENS = 8

# Message cố định theo status — KHÔNG BAO GIỜ lấy từ str(exception)/response
# SDK, dù message của adapter hiện tại đã an toàn (mục ui_ux_spec.md 9.2:
# không hiển thị raw SDK request/response). Cố định ở đây để an toàn không
# phụ thuộc adapter tương lai có đổi nội dung message hay không.
_STATUS_MESSAGES: dict[str, str] = {
    "success": "Connection successful.",
    "auth_failed": "Authentication failed. Check the API key and try again.",
    "config_invalid": "Provider configuration is missing or invalid.",
    "timeout": "Provider request timed out.",
    "network_unavailable": "Cannot reach the provider.",
    "rate_limited": "Provider rate limit exceeded.",
    "malformed_response": "Provider returned an invalid response.",
    "unknown_failure": "Provider request failed.",
}


@dataclass
class ConfigSummary:
    active_provider: str
    model: str
    base_url: str | None
    api_key_status: str
    """"configured" | "missing" | "không cần" — KHÔNG BAO GIỜ chứa giá trị key
    thật (mục 12/DoD 10)."""
    fallback: str | None


@dataclass
class ProviderSummary:
    """Một dòng trong danh sách "provider đã cấu hình" (ui_ux_spec.md mục
    4.6) — cùng field như `ConfigSummary` nhưng cho MỘT provider bất kỳ
    (không chỉ provider active), kèm `name`/`is_active` để UI biết đây là
    provider nào và có đang active hay không."""

    name: str
    provider_type: str
    model: str
    base_url: str | None
    api_key_status: str
    fallback: str | None
    is_active: bool


@dataclass
class ConnectionTestResult:
    """Kết quả "Test connection" — status trung lập theo đúng tập ở
    ui_ux_spec.md mục 9.2 (7 loại lỗi) + "success"; `message` là câu cố định
    an toàn theo status, không phải chuỗi exception/response SDK thô."""

    status: str
    message: str


@dataclass(frozen=True)
class BudgetLimits:
    """Giới hạn chi phí USD; ``None`` giữ hành vi không giới hạn hiện có."""

    soft_limit_usd: float | None
    hard_limit_usd: float | None


def get_budget_limits() -> BudgetLimits:
    """Đọc budget dùng chung cho CLI/GUI từ ``providers.json``."""
    config = provider_config_writer.read_config()
    cost_config = config.get("cost") or {}
    soft_limit = cost_config.get("soft_limit_usd")
    hard_limit = cost_config.get("hard_limit_usd")
    return BudgetLimits(
        soft_limit_usd=float(soft_limit) if soft_limit is not None else None,
        hard_limit_usd=float(hard_limit) if hard_limit is not None else None,
    )


def _resolve_api_key_status(provider_config: dict) -> str:
    api_key_env = provider_config.get("api_key_env")
    if api_key_env is None:
        return "không cần"
    # Kiểm tra os.environ (trạng thái thật lúc CHẠY) thay vì chỉ đọc file
    # .env XDG (dotenv_writer.has_key) — llm/factory.py cũng đọc key qua
    # os.environ.get(), và giá trị đó có thể đến từ bootstrap nạp file .env
    # XDG hoặc từ biến môi trường shell, chứ không chỉ từ việc đọc trực tiếp
    # file .env XDG. llm/factory.py tuyệt đối không tự tìm/nạp .env theo CWD.
    return "configured" if os.environ.get(api_key_env) else "missing"


def show_config() -> ConfigSummary:
    config = provider_config_writer.read_config()
    active_name = config["active"]
    active_config = config["providers"][active_name]

    return ConfigSummary(
        active_provider=active_name,
        model=active_config.get("model", ""),
        base_url=active_config.get("base_url"),
        api_key_status=_resolve_api_key_status(active_config),
        fallback=active_config.get("fallback"),
    )


def list_providers() -> list[ProviderSummary]:
    """Liệt kê MỌI provider đã cấu hình (không chỉ active) — GUI Settings
    (Phase 10, mục 4.6: "list provider đã cấu hình") dùng để hiển thị danh
    sách, không phải chỉ 1 provider như `show_config()`."""
    config = provider_config_writer.read_config()
    active_name = config["active"]
    return [
        ProviderSummary(
            name=name,
            provider_type=provider_config.get("type", ""),
            model=provider_config.get("model", ""),
            base_url=provider_config.get("base_url"),
            api_key_status=_resolve_api_key_status(provider_config),
            fallback=provider_config.get("fallback"),
            is_active=(name == active_name),
        )
        for name, provider_config in config["providers"].items()
    ]


def set_active_provider(name: str) -> tuple[str, str]:
    """Đổi active provider, trả về (tên cũ, tên mới). Raise ValueError nếu `name`
    không tồn tại trong providers.json — command tự map sang exit code 3 (mục 29),
    KHÔNG đổi gì trong file khi tên không hợp lệ."""
    config = provider_config_writer.read_config()
    if name not in config["providers"]:
        available = ", ".join(sorted(config["providers"])) or "(chưa có provider nào)"
        raise ValueError(f"Provider '{name}' không tồn tại. Các provider hiện có: {available}.")

    old_name = config["active"]
    config["active"] = name
    provider_config_writer.write_config(config)
    return old_name, name


def add_provider_from_preset(
    preset: ProviderPreset,
    *,
    api_key: str | None,
    model: str,
    base_url: str | None,
) -> str:
    """Ghi 1 provider mới vào providers.json (và API key vào .env nếu có) theo
    preset đã chọn + input người dùng. Trả về tên provider (key trong
    providers.json) vừa ghi. Không tự đổi active provider; factory chọn adapter
    lúc cấu hình được sử dụng."""
    config = provider_config_writer.read_config()

    entry: dict = dict(config["providers"].get(preset.key, {}))
    entry.update({
        "type": preset.provider_type,
        "model": model,
        "base_url": base_url or preset.default_base_url,
    })
    entry.setdefault("input_price_per_1m", None)
    entry.setdefault("output_price_per_1m", None)
    if preset.requires_api_key and preset.api_key_env:
        entry["api_key_env"] = preset.api_key_env
        if api_key:
            dotenv_writer.set_key(preset.api_key_env, api_key)
            # dotenv_writer.set_key() chỉ ghi file .env XDG, KHÔNG tự nạp lại
            # vào os.environ của tiến trình đang chạy (chỉ xảy ra ở lần
            # bootstrap kế tiếp) — llm/factory.py
            # đọc key qua os.environ.get() nên nếu không đồng bộ ngay đây,
            # test_connection()/load_provider() trong CÙNG process GUI/CLI sẽ
            # thấy giá trị cũ (hoặc thiếu) ngay sau khi vừa lưu (gap đã biết,
            # Phase 9 gui_implementation_plan.md).
            os.environ[preset.api_key_env] = api_key
    else:
        entry.pop("api_key_env", None)

    config["providers"][preset.key] = entry
    provider_config_writer.write_config(config)
    return preset.key


def update_provider_fields(
    name: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> None:
    """Sửa field của 1 provider ĐÃ CÓ (Settings edit form, Phase 10) — không
    tạo mới, không đổi active provider. `model`/`base_url` bằng `None` nghĩa
    là giữ nguyên giá trị cũ (không phải yêu cầu xoá field). `api_key` (nếu
    có) chỉ ghi vào .env qua `dotenv_writer.set_key` + đồng bộ `os.environ`
    ngay (cùng gap đã xử lý ở `add_provider_from_preset`) — không bao giờ
    lưu giá trị key vào providers.json. Raise ValueError nếu `name` không
    tồn tại, hoặc nếu truyền `api_key` cho provider không cần key."""
    config = provider_config_writer.read_config()
    if name not in config["providers"]:
        available = ", ".join(sorted(config["providers"])) or "(chưa có provider nào)"
        raise ValueError(f"Provider '{name}' không tồn tại. Các provider hiện có: {available}.")

    entry = config["providers"][name]
    if model is not None:
        entry["model"] = model
    if base_url is not None:
        entry["base_url"] = base_url

    if api_key is not None:
        api_key_env = entry.get("api_key_env")
        if api_key_env is None:
            raise ValueError(f"Provider '{name}' không cần API key.")
        dotenv_writer.set_key(api_key_env, api_key)
        os.environ[api_key_env] = api_key

    provider_config_writer.write_config(config)


def test_connection(
    provider_type: str,
    *,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> ConnectionTestResult:
    """Gọi THẬT tới provider (không mock) bằng 1 prompt vô hại, không tool,
    bounded bằng timeout ngắn (`_TEST_CONNECTION_TIMEOUT_SECONDS`) — dùng
    chung cho Settings (Phase 10) và Onboarding (Phase 18), kể cả khi config
    chưa được lưu vào providers.json (form đang nhập). Không bao giờ trả lại
    `api_key` — dù thành công hay thất bại, field đó chỉ dùng để gọi rồi bỏ.

    Không tự raise: mọi lỗi (bao gồm provider_type/model không hợp lệ, hay
    exception không lường trước) đều map về một `status` trong
    `_STATUS_MESSAGES`, không để exception hay response SDK thô lộ ra ngoài.
    """
    if not model or not model.strip():
        return _make_connection_result("config_invalid")

    try:
        provider = llm_factory.build_provider_from_fields(
            provider_type,
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=_TEST_CONNECTION_TIMEOUT_SECONDS,
        )
    except Exception:
        # Bất kỳ lỗi lúc DỰNG provider (provider_type chưa đăng ký, field
        # sai kiểu...) đều xảy ra TRƯỚC khi có cơ hội gọi mạng — luôn là lỗi
        # cấu hình cục bộ, không phải auth/network/timeout thật.
        return _make_connection_result("config_invalid")

    try:
        provider.chat(
            [Message(role=Role.USER, content=_TEST_CONNECTION_PROMPT)],
            max_tokens=_TEST_CONNECTION_MAX_TOKENS,
        )
    except ProviderAuthenticationError:
        return _make_connection_result("auth_failed")
    except ProviderTimeoutError:
        return _make_connection_result("timeout")
    except ProviderNetworkError:
        return _make_connection_result("network_unavailable")
    except ProviderRateLimitError:
        return _make_connection_result("rate_limited")
    except ProviderResponseError:
        return _make_connection_result("malformed_response")
    except ProviderError:
        return _make_connection_result("unknown_failure")
    except Exception:
        return _make_connection_result("unknown_failure")

    return _make_connection_result("success")


def _make_connection_result(status: str) -> ConnectionTestResult:
    return ConnectionTestResult(status=status, message=_STATUS_MESSAGES[status])
