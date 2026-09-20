"""Network fallback: thử provider chính, rồi thử đúng 1 lần provider dự phòng nếu
có, KHÔNG retry vô hạn (Giai đoạn 6).

Không tự viết adapter provider mới — "fallback" ở đây chỉ là một provider khác đã
cấu hình trong providers.json (ví dụ Ollama chạy qua OpenAICompatibleProvider với
base_url cục bộ), theo đúng nguyên tắc Ports & Adapters (#4): core/ ở đây chỉ nói
chuyện qua interface LLMProvider, không biết provider cụ thể là gì.

`chat_with_fallback()` giữ nguyên chữ ký/hành vi cũ cho mọi caller hiện có (CLI qua
agent_service) — nay chỉ là wrapper mỏng gọi `chat_with_fallback_detailed()` rồi trả
`.response`, không đổi quan sát được từ bên ngoài.

`chat_with_fallback_detailed()` (Phase 12, gui_implementation_plan.md — event/outcome
contract cho GUI) trả thêm provenance đã sanitize: provider nào THẬT SỰ trả lời
("primary"/"fallback"/None nếu cả hai đều lỗi) và category lỗi đã chuẩn hoá của từng
provider — không bao giờ kèm exception message/traceback gốc (có thể chứa secret hoặc
raw SDK response). `core/loop.py` dùng bản chi tiết này để: (1) phát event phân biệt
rõ "provider lỗi" khỏi "loop chuyển sang RAG offline" — không được gộp hai việc này
làm một; (2) gắn đúng tên/model provider nào thật sự trả lời khi ghi nhận chi phí,
không tiếp tục ghi nhãn provider chính nếu fallback mới là bên trả lời.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from agent.core.redaction import redact_known_secrets
from agent.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMResponse,
    Message,
    ProviderAuthenticationError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ToolSchema,
)

logger = logging.getLogger(__name__)


class ProviderErrorCategory(str, Enum):
    """6 category chuẩn cho lỗi provider trong MỘT lượt chat (docs/ui_ux_spec.md mục
    9.2) — KHÔNG bao gồm "configuration": lỗi cấu hình/nạp provider xảy ra trước khi
    có provider nào để gọi `.chat()`, đã có `agent.errors.ProviderConfigError` xử lý
    riêng ở boundary service (gui_implementation_plan.md mục 0 dòng 8: không tạo
    `ProviderConfigurationError` mới vì lớp hiện có đã đủ dùng)."""

    AUTHENTICATION = "authentication"
    NETWORK = "network"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    MALFORMED_RESPONSE = "malformed_response"
    GENERIC_FAILURE = "generic_failure"


def categorize_provider_error(exc: LLMProviderError) -> ProviderErrorCategory:
    """Map exception typed sẵn ở `llm/base.py` sang category trung lập — không đọc
    `str(exc)` (có thể chứa secret/raw SDK message), chỉ dựa vào type."""
    if isinstance(exc, ProviderAuthenticationError):
        return ProviderErrorCategory.AUTHENTICATION
    if isinstance(exc, ProviderNetworkError):
        return ProviderErrorCategory.NETWORK
    if isinstance(exc, ProviderRateLimitError):
        return ProviderErrorCategory.RATE_LIMIT
    if isinstance(exc, ProviderTimeoutError):
        return ProviderErrorCategory.TIMEOUT
    if isinstance(exc, ProviderResponseError):
        return ProviderErrorCategory.MALFORMED_RESPONSE
    return ProviderErrorCategory.GENERIC_FAILURE


@dataclass(frozen=True)
class FallbackOutcome:
    """Kết quả chi tiết của một lượt `chat_with_fallback_detailed()`.

    `provider_used` là provenance THẬT — None khi cả hai provider (hoặc chỉ primary,
    nếu không có fallback cấu hình) đều lỗi. `fallback_attempted` phân biệt "không có
    fallback cấu hình" (False) với "có fallback nhưng cũng lỗi" (True, kèm
    `fallback_error`) — hai tình huống khác nhau dù cùng kết quả `response=None`.
    """

    response: LLMResponse | None
    provider_used: Literal["primary", "fallback"] | None
    primary_error: ProviderErrorCategory | None = None
    fallback_attempted: bool = False
    fallback_error: ProviderErrorCategory | None = None


def chat_with_fallback_detailed(
    primary: LLMProvider,
    fallback: LLMProvider | None,
    messages: list[Message],
    tools: list[ToolSchema] | None = None,
    **kwargs,
) -> FallbackOutcome:
    try:
        response = primary.chat(messages, tools, **kwargs)
        return FallbackOutcome(response=response, provider_used="primary")
    except LLMProviderError as primary_exc:
        primary_category = categorize_provider_error(primary_exc)

        if fallback is None:
            logger.warning(
                f"Provider chính '{redact_known_secrets(primary.model_name)}' lỗi và không có "
                f"provider dự phòng nào cấu hình — trả về None."
            )
            return FallbackOutcome(response=None, provider_used=None, primary_error=primary_category)

        logger.warning(
            f"Provider chính '{redact_known_secrets(primary.model_name)}' lỗi — chuyển sang "
            f"provider dự phòng '{redact_known_secrets(fallback.model_name)}'."
        )
        try:
            response = fallback.chat(messages, tools, **kwargs)
            return FallbackOutcome(
                response=response,
                provider_used="fallback",
                primary_error=primary_category,
                fallback_attempted=True,
            )
        except LLMProviderError as fallback_exc:
            logger.warning(
                f"Provider dự phòng '{redact_known_secrets(fallback.model_name)}' cũng lỗi — "
                f"cả 2 provider đều thất bại, trả về None."
            )
            return FallbackOutcome(
                response=None,
                provider_used=None,
                primary_error=primary_category,
                fallback_attempted=True,
                fallback_error=categorize_provider_error(fallback_exc),
            )


def chat_with_fallback(
    primary: LLMProvider,
    fallback: LLMProvider | None,
    messages: list[Message],
    tools: list[ToolSchema] | None = None,
    **kwargs,
) -> LLMResponse | None:
    return chat_with_fallback_detailed(primary, fallback, messages, tools, **kwargs).response
