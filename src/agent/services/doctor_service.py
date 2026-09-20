"""Application service chạy tuần tự toàn bộ check của ``agent doctor``."""

from __future__ import annotations

from agent.core import doctor_checks
from agent.core.doctor_checks import DoctorResult

_CHECK_NAMES: tuple[str, ...] = (
    "check_python",
    "check_sqlite",
    "check_fts5",
    "check_package_manager",
    "check_system_profile",
    "check_provider_config",
    "check_provider_key",
    "check_ollama",
    "check_rag_database",
    "check_session_database",
    "check_audit_path",
)


def run_all_checks() -> list[DoctorResult]:
    """Chạy đủ mọi check, kể cả khi một check bất ngờ raise.

    Các hàm thật đã tự cô lập exception; lớp bảo vệ thứ hai ở service giữ đúng
    contract ngay cả khi một check được thay thế/inject trong test hoặc plugin.
    Nội dung exception không được đưa vào message để tránh lộ secret.
    """
    results: list[DoctorResult] = []
    for check_name in _CHECK_NAMES:
        check = getattr(doctor_checks, check_name)
        try:
            result = check()
        except Exception as exc:
            display_name = check_name.removeprefix("check_").replace("_", " ").title()
            result = DoctorResult(display_name, "error", f"check failed ({type(exc).__name__})")
        results.append(result)
    return results

