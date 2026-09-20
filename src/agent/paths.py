"""Điểm resolve đường dẫn XDG duy nhất cho toàn bộ dữ liệu ghi ra đĩa.

Dùng platformdirs để tôn trọng XDG_CONFIG_HOME/XDG_DATA_HOME/XDG_STATE_HOME/
XDG_CACHE_HOME nếu người dùng đã set (CLI.md mục 15), thay vì tự viết logic
fallback bằng tay. Mọi module khác (audit, session store, rag, memory, provider
config) phải import các hàm ở đây thay vì tự hardcode đường dẫn theo Path.home().
"""

from __future__ import annotations

from pathlib import Path

import platformdirs

_APP_NAME = "linux-agent"


def config_dir() -> Path:
    return Path(platformdirs.user_config_dir(_APP_NAME))


def data_dir() -> Path:
    return Path(platformdirs.user_data_dir(_APP_NAME))


def state_dir() -> Path:
    return Path(platformdirs.user_state_dir(_APP_NAME))


def cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir(_APP_NAME))


def config_file() -> Path:
    return config_dir() / "config.json"


def providers_file() -> Path:
    return config_dir() / "providers.json"


def dotenv_file() -> Path:
    return config_dir() / ".env"


def sessions_db() -> Path:
    return data_dir() / "sessions.db"


def memory_db() -> Path:
    return data_dir() / "memory.db"


def rag_default_db() -> Path:
    return data_dir() / "rag" / "default.db"


def rag_user_db() -> Path:
    return data_dir() / "rag" / "user.db"


def audit_log_path() -> Path:
    return state_dir() / "audit.log"


def onboarding_marker_file() -> Path:
    """Existence-only sentinel (GUI Phase 18): written by `OnboardingController.
    mark_complete()` after the user finishes the first-run wizard. Never holds
    a secret or per-step progress — an incomplete onboarding always restarts
    at Welcome on the next launch (no resume state)."""
    return state_dir() / "onboarding_complete"
