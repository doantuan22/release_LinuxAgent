"""Danh sách preset cố định cho `agent config add-provider` (CLI.md mục 14).

Chỉ là dữ liệu tĩnh — KHÔNG instantiate provider hay import adapter cụ thể.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    """Tên dùng làm key trong providers.json (vd "nvidia_nim")."""
    display_name: str
    """Tên hiển thị trong wizard (vd "NVIDIA NIM")."""
    provider_type: str
    """Giá trị field "type" ghi vào providers.json; factory chọn adapter."""
    default_base_url: str | None
    """None nghĩa là adapter dùng endpoint mặc định hoặc wizard cần hỏi URL."""
    default_model: str
    requires_api_key: bool
    api_key_env: str | None
    """Tên biến môi trường sẽ ghi vào providers.json/.env — None nếu
    requires_api_key=False (vd Ollama chạy local, dùng "not-needed")."""
    ask_base_url: bool = False
    """Chỉ preset endpoint tùy ý cần hỏi URL trong wizard."""


NVIDIA_NIM = ProviderPreset(
    key="nvidia_nim",
    display_name="NVIDIA NIM",
    provider_type="openai_compatible",
    default_base_url="https://integrate.api.nvidia.com/v1",
    default_model="nvidia/nemotron-3-ultra-550b-a55b",
    requires_api_key=True,
    api_key_env="NVIDIA_API_KEY",
)

OPENAI_COMPATIBLE = ProviderPreset(
    key="openai_compatible",
    display_name="OpenAI-compatible",
    provider_type="openai_compatible",
    default_base_url=None,
    default_model="gpt-4o-mini",
    requires_api_key=True,
    api_key_env="OPENAI_API_KEY",
    ask_base_url=True,
)

OLLAMA = ProviderPreset(
    key="ollama_local",
    display_name="Ollama",
    provider_type="openai_compatible",
    default_base_url="http://localhost:11434/v1",
    default_model="llama3.1",
    requires_api_key=False,
    api_key_env=None,
)

ANTHROPIC = ProviderPreset(
    key="anthropic",
    display_name="Anthropic",
    provider_type="anthropic",
    default_base_url=None,
    default_model="claude-sonnet-5",
    requires_api_key=True,
    api_key_env="ANTHROPIC_API_KEY",
)

PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (NVIDIA_NIM, OPENAI_COMPATIBLE, OLLAMA, ANTHROPIC)
