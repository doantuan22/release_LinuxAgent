"""Các diagnostic check độc lập cho ``agent doctor`` (CLI.md mục 17).

Doctor chỉ quan sát trạng thái (ngoại trừ probe tạo/xóa file tạm để kiểm tra
quyền ghi). Mỗi hàm được cô lập bằng ``_isolated_check``: lỗi bất ngờ trở thành
``DoctorResult(status="error")`` ngắn gọn và không đưa nội dung exception ra
output, tránh traceback cũng như nguy cơ lộ secret.
"""

from __future__ import annotations

import importlib
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, fields
from functools import wraps
from pathlib import Path
from typing import Callable, Literal, TypeVar

from agent import paths
from agent.core import dotenv_writer, provider_config_writer
from agent.memory import schemas as memory_schemas
from agent.services import config_service
from agent.system import profile as system_profile

DoctorStatus = Literal["ok", "warning", "error"]


@dataclass(frozen=True)
class DoctorResult:
    name: str
    status: DoctorStatus
    message: str


_Check = TypeVar("_Check", bound=Callable[[], DoctorResult])
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _unexpected_result(name: str, exc: Exception) -> DoctorResult:
    # Không dùng str(exc): exception từ config/provider có thể chứa credential.
    return DoctorResult(name, "error", f"check failed ({type(exc).__name__})")


def _isolated_check(name: str) -> Callable[[_Check], _Check]:
    def decorator(check: _Check) -> _Check:
        @wraps(check)
        def wrapped() -> DoctorResult:
            try:
                return check()
            except Exception as exc:
                return _unexpected_result(name, exc)

        return wrapped  # type: ignore[return-value]

    return decorator


def _read_only_connection(db_path: Path):
    sqlite3 = importlib.import_module("sqlite3")
    return sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)


def _probe_directory_writable(directory: Path) -> bool:
    directory.mkdir(parents=True, exist_ok=True)
    probe_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".doctor-", dir=directory, delete=False) as probe:
            probe.write(b"doctor")
            probe.flush()
            probe_path = Path(probe.name)
        probe_path.unlink()
        probe_path = None
        return True
    except OSError:
        return False
    finally:
        if probe_path is not None:
            try:
                probe_path.unlink()
            except OSError:
                pass


@_isolated_check("Python version")
def check_python() -> DoctorResult:
    version = sys.version_info
    display = f"{version.major}.{version.minor}.{version.micro}"
    if version < (3, 11):
        return DoctorResult("Python version", "error", f"{display}; requires >= 3.11")
    return DoctorResult("Python version", "ok", display)


@_isolated_check("SQLite")
def check_sqlite() -> DoctorResult:
    sqlite3 = importlib.import_module("sqlite3")
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return DoctorResult("SQLite", "ok", "available")


@_isolated_check("SQLite FTS5")
def check_fts5() -> DoctorResult:
    sqlite3 = importlib.import_module("sqlite3")
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE _doctor_fts5_probe USING fts5(content)")
        conn.execute("DROP TABLE _doctor_fts5_probe")
    except sqlite3.OperationalError:
        return DoctorResult("SQLite FTS5", "error", "not available")
    finally:
        conn.close()
    return DoctorResult("SQLite FTS5", "ok", "enabled")


@_isolated_check("System profile")
def check_system_profile() -> DoctorResult:
    profile = system_profile.scan_system()
    summary = system_profile.to_display_summary(profile)
    if not summary or not profile.distro_name or not profile.architecture:
        return DoctorResult("System profile", "error", "empty or incomplete")
    return DoctorResult("System profile", "ok", "available")


@_isolated_check("Package manager")
def check_package_manager() -> DoctorResult:
    manager = system_profile.detect_package_manager()
    if manager == system_profile.UNSUPPORTED_PACKAGE_MANAGER:
        return DoctorResult("Package manager", "error", "not detected")
    return DoctorResult("Package manager", "ok", f"detected: {manager}")


@_isolated_check("Provider config")
def check_provider_config() -> DoctorResult:
    config_path = paths.providers_file()
    if not config_path.is_file():
        return DoctorResult("Provider config", "error", "providers.json missing")
    if config_path.stat().st_size == 0:
        return DoctorResult("Provider config", "error", "providers.json is empty")

    try:
        summary = config_service.show_config()
    except Exception:
        return DoctorResult("Provider config", "error", "providers.json is invalid or unreadable")

    if not summary.active_provider:
        return DoctorResult("Provider config", "error", "active provider missing")
    return DoctorResult("Provider config", "ok", f"active: {summary.active_provider}")


@_isolated_check("Provider API key")
def check_provider_key() -> DoctorResult:
    config_path = paths.providers_file()
    if not config_path.is_file() or config_path.stat().st_size == 0:
        return DoctorResult("Provider API key", "error", "provider config unavailable")

    try:
        summary = config_service.show_config()
        config = provider_config_writer.read_config()
        provider = config["providers"][summary.active_provider]
    except Exception:
        return DoctorResult("Provider API key", "error", "cannot determine active provider key")

    api_key_env = provider.get("api_key_env")
    if api_key_env is None:
        return DoctorResult("Provider API key", "ok", "not required")
    if not isinstance(api_key_env, str) or not _ENV_NAME_RE.fullmatch(api_key_env):
        return DoctorResult("Provider API key", "error", "api_key_env is invalid")

    if not dotenv_writer.has_key(api_key_env):
        return DoctorResult("Provider API key", "error", f"{api_key_env} missing")
    return DoctorResult("Provider API key", "ok", f"{api_key_env} configured")


@_isolated_check("Ollama")
def check_ollama() -> DoctorResult:
    if shutil.which("ollama") is None:
        return DoctorResult("Ollama", "warning", "optional fallback is not installed")
    return DoctorResult("Ollama", "ok", "available")


@_isolated_check("RAG default database")
def check_rag_database() -> DoctorResult:
    db_path = paths.rag_default_db()
    if not db_path.is_file():
        return DoctorResult("RAG default database", "warning", "default.db has not been seeded")

    try:
        conn = _read_only_connection(db_path)
        try:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
            ).fetchone()
            if table is None:
                return DoctorResult("RAG default database", "warning", "default.db has not been seeded")
            row = conn.execute("SELECT 1 FROM documents LIMIT 1").fetchone()
        finally:
            conn.close()
    except Exception:
        return DoctorResult("RAG default database", "error", "default.db is unreadable")

    if row is None:
        return DoctorResult("RAG default database", "warning", "default.db has not been seeded")
    return DoctorResult("RAG default database", "ok", "seeded")


@_isolated_check("Session database")
def check_session_database() -> DoctorResult:
    data_directory = paths.data_dir()
    if not _probe_directory_writable(data_directory):
        return DoctorResult("Session database", "error", "data directory is not writable")

    db_path = paths.sessions_db()
    if not db_path.exists():
        return DoctorResult("Session database", "ok", "data directory writable; database not created yet")
    if not db_path.is_file():
        return DoctorResult("Session database", "error", "sessions.db is not a regular file")

    try:
        conn = _read_only_connection(db_path)
        try:
            expected_tables = {
                "sessions": {field.name for field in fields(memory_schemas.SessionRecord)},
                "messages": {field.name for field in fields(memory_schemas.MessageRecord)},
            }
            for table_name, expected_columns in expected_tables.items():
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}
                if not expected_columns.issubset(columns):
                    return DoctorResult("Session database", "error", "sessions.db schema is incompatible")
                memory_schemas.check_schema_version(
                    conn,
                    table_name,
                    expected_version=memory_schemas.SESSION_SCHEMA_VERSION,
                )
        finally:
            conn.close()
    except Exception:
        return DoctorResult("Session database", "error", "sessions.db is unreadable or schema is incompatible")

    return DoctorResult("Session database", "ok", "writable and schema valid")


@_isolated_check("Audit directory")
def check_audit_path() -> DoctorResult:
    if not _probe_directory_writable(paths.audit_log_path().parent):
        return DoctorResult("Audit directory", "error", "not writable")
    return DoctorResult("Audit directory", "ok", "writable")

