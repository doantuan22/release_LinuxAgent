"""Entry point `agent-gui` (gui_implementation_plan.md Phase 1).

Luồng: agent-gui -> bootstrap -> QApplication -> Onboarding (nếu chưa có
`paths.onboarding_marker_file()`) -> MainWindow.

PySide6 chỉ được import bên trong hàm (không ở module scope của bất kỳ
module nào mà `agent.cli` chạm tới), và chỉ khi package được cài kèm extra
`gui` (`pip install linux-agent[gui]`). Nếu thiếu, in thông báo an toàn thay
vì để ImportError thoát ra thành traceback.
"""

from __future__ import annotations

import sys

from agent import bootstrap

_PYSIDE6_MISSING_MESSAGE = (
    "PySide6 chưa được cài. Cài GUI bằng: pip install 'linux-agent[gui]'"
)
_BOOTSTRAP_FAILED_MESSAGE = (
    "Không thể khởi tạo cấu hình/dữ liệu GUI. Chạy `agent doctor` để chẩn đoán."
)
_STARTUP_FAILED_MESSAGE = "Could not start the GUI. Run `agent doctor` for diagnostics."


def _import_qapplication():
    from PySide6.QtWidgets import QApplication

    return QApplication


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv

    try:
        QApplication = _import_qapplication()
    except ImportError:
        print(_PYSIDE6_MISSING_MESSAGE, file=sys.stderr)
        return 1
    except Exception:
        print(_STARTUP_FAILED_MESSAGE, file=sys.stderr)
        return 1

    try:
        bootstrap.ensure_bootstrapped()
    except Exception:
        # Lưới an toàn: lỗi bootstrap có thể chứa đường dẫn/permission chi
        # tiết không nên phơi ra người dùng cuối dưới dạng traceback thô.
        print(_BOOTSTRAP_FAILED_MESSAGE, file=sys.stderr)
        return 1

    try:
        from agent.gui.controllers.onboarding_controller import OnboardingController
        from agent.gui.main_window import MainWindow
        from agent.gui.views.onboarding_view import OnboardingView

        app = QApplication.instance() or QApplication(argv)
        state: dict[str, object] = {}

        def _open_main_window() -> None:
            window = MainWindow()
            window.show()
            state["window"] = window

        if OnboardingController.is_complete():
            _open_main_window()
        else:
            onboarding = OnboardingView()
            state["window"] = onboarding

            def _on_onboarding_completed() -> None:
                try:
                    # Show MainWindow BEFORE closing onboarding: preserve
                    # quitOnLastWindowClose and the existing lifecycle order.
                    _open_main_window()
                    onboarding.close()
                except Exception:
                    # Qt slots must contain failures locally: otherwise Qt
                    # sends the raw exception to sys.excepthook.
                    print(_STARTUP_FAILED_MESSAGE, file=sys.stderr)
                    app.exit(1)

            onboarding.completed.connect(_on_onboarding_completed)
            onboarding.show()
    except Exception:
        # Imports and constructors can fail with credential-bearing messages.
        print(_STARTUP_FAILED_MESSAGE, file=sys.stderr)
        return 1

    exit_code = app.exec()
    # app.exec() trả về khi event loop dừng (user đóng cửa sổ, quit()...)
    # nhưng không đảm bảo window đã nhận closeEvent trong mọi trường hợp —
    # gọi lại tường minh (idempotent) để page/window hiện tại (MainWindow,
    # hoặc OnboardingView nếu người dùng đóng app giữa chừng onboarding)
    # luôn được chờ worker thật kết thúc an toàn trước khi tiến trình thoát.
    state["window"].close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
