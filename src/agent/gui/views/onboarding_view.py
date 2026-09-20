"""Onboarding first-run wizard (docs/ui_ux_spec.md mục 4.1,
gui_implementation_plan.md Phase 18) — Welcome -> System scan -> Choose
provider -> Safety model -> Ready.

`OnboardingView` is a standalone top-level window (same idiom as
`MainWindow`), shown by `app.py` instead of `MainWindow` on a fresh install
(no `paths.onboarding_marker_file()` yet). It owns the one
`OnboardingController` for the whole flow; each step widget below is pure
presentation (reused widgets — `ProviderForm` from Settings Phase 10 — plus
plain labels/buttons), wired to the controller only from this view, exactly
like `SystemPage`/`SettingsPage` wire their own controller.

Per the resolved scope decision for this phase: there is no per-step resume
state. If the user closes the app before reaching Ready (no marker written),
the NEXT launch always restarts at Welcome — nothing here persists which step
was open. Going Back/Forward WITHIN one running session does not lose data,
because step widgets are created once and kept alive in a `QStackedWidget`
(never recreated on navigation).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from agent.gui import theme
from agent.gui.controllers.onboarding_controller import OnboardingController
from agent.gui.resources import icon_manager
from agent.gui.widgets.buttons import PrimaryButton, SecondaryButton
from agent.gui.widgets.provider_form import ProviderForm

_TOTAL_STEPS = 4
_STEP_WELCOME, _STEP_SCAN, _STEP_PROVIDER, _STEP_SAFETY = range(_TOTAL_STEPS)
_APP_NAME = "Linux Agent"
_WELCOME_DESCRIPTION = (
    "A Linux-first AI agent that inspects your system, retrieves Linux "
    "knowledge, and takes safe, confirmed actions on your machine."
)

# Mirrors SystemPage's exact copy (docs/ui_ux_spec.md mục 10) so the same
# underlying state reads identically wherever it appears in the app.
_SCAN_LOADING_STATUS = "⟳ Scanning..."
_SCAN_ERROR_STATUS = "⚠ Could not scan system"

_TEST_BUTTON_TEXT = "Test connection"
_TESTING_TEXT = "Testing..."
_SAVE_FAILED_STATUS = "Could not save provider configuration."

_TIER1_TEXT = "Runs instantly, read-only."
_TIER2_TEXT = "Always asks before changing your system."


def _plain_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class _StepIndicator(QLabel):
    """"Step N of 4" — hidden on Welcome (docs/ui_ux_spec.md mục 4.1: Welcome
    has no step indicator, only "Get started")."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(theme.general_font("label"))
        self.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)

    def set_step(self, step_index: int) -> None:
        if step_index == 0:
            self.setVisible(False)
            self.setText("")
            return
        self.setVisible(True)
        self.setText(f"Step {step_index + 1} of {_TOTAL_STEPS}")


class WelcomeStep(QWidget):
    get_started_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        title = _plain_label(_APP_NAME)
        title.setFont(theme.general_font("page_title"))
        title.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        description = _plain_label(_WELCOME_DESCRIPTION)
        description.setFont(theme.general_font("body"))
        description.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setMaximumWidth(440)

        self._get_started_button = PrimaryButton("Get started")
        self._get_started_button.clicked.connect(self.get_started_requested)

        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._get_started_button, 0, Qt.AlignmentFlag.AlignCenter)

    def get_started_button(self) -> PrimaryButton:
        return self._get_started_button


# (onboarding label, source label in SystemController.scan_finished rows) —
# reduced to the 3 fields ui_ux_spec.md mục 4.1 lists for this step, unlike
# the full 9-row table on the System page (Phase 4).
_SCAN_FIELDS: tuple[tuple[str, str], ...] = (
    ("Distro", "OS"),
    ("Package manager", "Package Manager"),
    ("Environment", "Environment"),
)


def _reduce_scan_rows(rows: list[tuple[str, str]]) -> dict[str, str]:
    by_label = dict(rows)
    version = by_label.get("Version", "")
    distro = by_label.get("OS", "")
    if version:
        distro = f"{distro} {version}".strip()
    return {
        "Distro": distro,
        "Package manager": by_label.get("Package Manager", ""),
        "Environment": by_label.get("Environment", ""),
    }


class SystemScanStep(QWidget):
    continue_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        title = _plain_label("Here's your system")
        title.setFont(theme.general_font("card_title"))
        title.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
        layout.addWidget(title)

        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background-color: {theme.COLORS['bg_card']}; "
            f"border: {theme.LAYOUT['border_width']}px solid {theme.COLORS['border']}; "
            f"border-radius: {theme.LAYOUT['radius']}px; }}"
        )
        card_layout = QVBoxLayout(card)
        margin = theme.LAYOUT["content_padding"]
        card_layout.setContentsMargins(margin, margin, margin, margin)
        card_layout.setSpacing(6)

        self._value_labels: dict[str, QLabel] = {}
        for onboarding_label, _source_label in _SCAN_FIELDS:
            row = QHBoxLayout()
            name = QLabel(onboarding_label)
            name.setFont(theme.general_font("label"))
            name.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
            value = QLabel("—")
            value.setFont(theme.general_font("body"))
            value.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
            value.setWordWrap(True)
            self._value_labels[onboarding_label] = value
            row.addWidget(name)
            row.addWidget(value, 1)
            card_layout.addLayout(row)
        layout.addWidget(card)

        self._status_label = QLabel("")
        self._status_label.setFont(theme.general_font("label"))
        layout.addWidget(self._status_label)

        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._continue_button = PrimaryButton("Continue")
        self._continue_button.setEnabled(False)
        self._continue_button.clicked.connect(self.continue_requested)
        footer.addWidget(self._continue_button)
        layout.addLayout(footer)

    def continue_button(self) -> PrimaryButton:
        return self._continue_button

    def value_text(self, onboarding_label: str) -> str:
        return self._value_labels[onboarding_label].text()

    def status_text(self) -> str:
        return self._status_label.text()

    def on_loading_changed(self, loading: bool) -> None:
        self._continue_button.setEnabled(not loading)
        if loading:
            self._status_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
            self._status_label.setText(_SCAN_LOADING_STATUS)

    def on_scan_finished(self, rows: list[tuple[str, str]]) -> None:
        self._status_label.setText("")
        for onboarding_label, value in _reduce_scan_rows(rows).items():
            self._value_labels[onboarding_label].setText(value or "—")

    def on_scan_failed(self, _message: str) -> None:
        self._status_label.setStyleSheet(f"color: {theme.COLORS['danger']};")
        self._status_label.setText(_SCAN_ERROR_STATUS)


class ChooseProviderStep(QWidget):
    continue_requested = Signal()
    test_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        title = _plain_label("Choose your AI provider")
        title.setFont(theme.general_font("card_title"))
        title.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
        layout.addWidget(title)

        self._form = ProviderForm()
        layout.addWidget(self._form)

        self._status_label = QLabel("")
        self._status_label.setFont(theme.general_font("label"))
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        layout.addStretch(1)

        footer = QHBoxLayout()
        self._test_button = SecondaryButton(_TEST_BUTTON_TEXT)
        self._test_button.clicked.connect(self.test_requested)
        footer.addWidget(self._test_button)
        footer.addStretch(1)
        self._continue_button = PrimaryButton("Continue")
        self._continue_button.clicked.connect(self.continue_requested)
        footer.addWidget(self._continue_button)
        layout.addLayout(footer)

    def form(self) -> ProviderForm:
        return self._form

    def test_button(self) -> SecondaryButton:
        return self._test_button

    def continue_button(self) -> PrimaryButton:
        return self._continue_button

    def status_text(self) -> str:
        return self._status_label.text()

    def on_test_loading_changed(self, loading: bool) -> None:
        self._test_button.setEnabled(not loading)
        self._continue_button.setEnabled(not loading)
        self._test_button.setText(_TESTING_TEXT if loading else _TEST_BUTTON_TEXT)
        if loading:
            self._status_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
            self._status_label.setText(_TESTING_TEXT)

    def on_test_finished(self, result: object) -> None:
        if result.status == "success":
            self._status_label.setStyleSheet(f"color: {theme.COLORS['success']};")
            self._status_label.setText(f"✓ {result.message}")
        else:
            # mục 9.5: "User vẫn có thể sửa field ngay tại chỗ" / "Không xóa
            # API key field khi request fail" — no field is touched here.
            self._status_label.setStyleSheet(f"color: {theme.COLORS['danger']};")
            self._status_label.setText(f"✕ {result.message}")

    def on_save_failed(self) -> None:
        self._continue_button.setEnabled(True)
        self._status_label.setStyleSheet(f"color: {theme.COLORS['danger']};")
        self._status_label.setText(_SAVE_FAILED_STATUS)

    def on_save_succeeded(self) -> None:
        # Only Continue after a SUCCESSFUL save clears the key field (mục
        # 9.5) — same rule as SettingsPage._on_save_succeeded (Phase 10).
        self._form.clear_api_key()
        self._status_label.setText("")


class _SafetyPanel(QFrame):
    def __init__(self, *, icon_name: str, icon_color: str, title: str, description: str, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background-color: {theme.COLORS['bg_card']}; "
            f"border: {theme.LAYOUT['border_width']}px solid {theme.COLORS['border']}; "
            f"border-radius: {theme.LAYOUT['radius']}px; }}"
        )
        layout = QVBoxLayout(self)
        margin = theme.LAYOUT["content_padding"]
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(8)

        heading = QHBoxLayout()
        icon_label = QLabel()
        icon_size = icon_manager.BUTTON_ICON_SIZE
        icon_label.setPixmap(icon_manager.icon(icon_name, color=icon_color, size=icon_size).pixmap(icon_size, icon_size))
        title_label = _plain_label(title)
        title_label.setFont(theme.general_font("card_title"))
        title_label.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
        heading.addWidget(icon_label)
        heading.addWidget(title_label, 1)
        layout.addLayout(heading)

        description_label = _plain_label(description)
        description_label.setFont(theme.general_font("body"))
        description_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")
        layout.addWidget(description_label)


class SafetyStep(QWidget):
    ready_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        title = _plain_label("How Linux Agent stays safe")
        title.setFont(theme.general_font("card_title"))
        title.setStyleSheet(f"color: {theme.COLORS['text_primary']};")
        layout.addWidget(title)

        panels = QHBoxLayout()
        panels.setSpacing(16)
        panels.addWidget(
            _SafetyPanel(
                icon_name="check", icon_color="accent", title="Tier 1", description=_TIER1_TEXT,
            ),
            1,
        )
        panels.addWidget(
            _SafetyPanel(
                icon_name="alert-triangle", icon_color="warning", title="Tier 2", description=_TIER2_TEXT,
            ),
            1,
        )
        layout.addLayout(panels)
        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._ready_button = PrimaryButton("Got it, let's go")
        self._ready_button.clicked.connect(self.ready_requested)
        footer.addWidget(self._ready_button)
        layout.addLayout(footer)

    def ready_button(self) -> PrimaryButton:
        return self._ready_button


class OnboardingView(QWidget):
    """Top-level onboarding window (same idiom as `MainWindow`) — `app.py`
    shows this instead of `MainWindow` until `completed` fires."""

    completed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{_APP_NAME} — Setup")
        self.setMinimumSize(theme.LAYOUT["min_window_width"], theme.LAYOUT["min_window_height"])
        self.setStyleSheet(f"background-color: {theme.COLORS['bg_app']};")

        self._controller = OnboardingController(self)
        self._scan_started = False

        self._welcome = WelcomeStep()
        self._scan = SystemScanStep()
        self._provider = ChooseProviderStep()
        self._safety = SafetyStep()

        self._stack = QStackedWidget()
        for step in (self._welcome, self._scan, self._provider, self._safety):
            self._stack.addWidget(step)

        self._step_indicator = _StepIndicator()

        self._back_button = SecondaryButton("Back")
        self._back_button.clicked.connect(self._go_back)
        back_row = QHBoxLayout()
        back_row.addWidget(self._back_button)
        back_row.addStretch(1)

        self._closing_label = QLabel("")
        self._closing_label.setFont(theme.general_font("label"))
        self._closing_label.setStyleSheet(f"color: {theme.COLORS['text_secondary']};")

        # ui_ux_spec.md mục 8.8: "onboarding dùng QScrollArea" — nội dung
        # từng bước có thể cao hơn viewport ở 560px, cuộn để CTA (Continue/
        # Test connection/Got it, let's go) luôn tới được thay vì bị đẩy khỏi
        # màn hình. QScrollArea tự hiện/ẩn scrollbar; resizeEvent bên dưới
        # chỉ điều chỉnh khoảng cách header theo chiều cao cửa sổ.
        self._stack_scroll = QScrollArea()
        self._stack_scroll.setWidgetResizable(True)
        self._stack_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._stack_scroll.setWidget(self._stack)

        outer = QVBoxLayout(self)
        margin = theme.LAYOUT["content_padding"]
        outer.setContentsMargins(margin, margin, margin, margin)
        outer.addWidget(self._step_indicator)
        outer.addWidget(self._stack_scroll, 1)
        outer.addLayout(back_row)
        outer.addWidget(self._closing_label)

        self._close_pending = False

        self._welcome.get_started_requested.connect(self._on_get_started)
        self._scan.continue_requested.connect(lambda: self._go_to_step(_STEP_PROVIDER))
        self._provider.test_requested.connect(self._on_test_requested)
        self._provider.continue_requested.connect(self._on_provider_continue)
        self._safety.ready_requested.connect(self._on_ready)

        self._controller.scan_loading_changed.connect(self._scan.on_loading_changed)
        self._controller.scan_finished.connect(self._scan.on_scan_finished)
        self._controller.scan_failed.connect(self._scan.on_scan_failed)
        self._controller.test_loading_changed.connect(self._provider.on_test_loading_changed)
        self._controller.test_finished.connect(self._provider.on_test_finished)
        self._controller.provider_saved.connect(self._on_provider_saved)
        self._controller.provider_save_failed.connect(self._provider.on_save_failed)

        self._go_to_step(_STEP_WELCOME)
        self._apply_header_spacing()

    def _apply_header_spacing(self) -> None:
        short_height = theme.is_short_height(self.height())
        spacing = theme.LAYOUT[
            "onboarding_header_spacing_short" if short_height else "onboarding_header_spacing"
        ]
        for step in (self._welcome, self._scan, self._provider, self._safety):
            step.layout().setSpacing(spacing)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_header_spacing()

    # --- public accessors for tests ---

    def step_indicator_text(self) -> str:
        return self._step_indicator.text()

    def current_step_index(self) -> int:
        return self._stack.currentIndex()

    def welcome_step(self) -> WelcomeStep:
        return self._welcome

    def scan_step(self) -> SystemScanStep:
        return self._scan

    def provider_step(self) -> ChooseProviderStep:
        return self._provider

    def safety_step(self) -> SafetyStep:
        return self._safety

    def back_button(self) -> SecondaryButton:
        return self._back_button

    def is_busy(self) -> bool:
        return self._controller.is_busy()

    def closing_status_text(self) -> str:
        return self._closing_label.text()

    def shutdown(self) -> None:
        self._controller.shutdown()

    # --- navigation ---

    def _go_to_step(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._step_indicator.set_step(index)
        self._back_button.setVisible(index > 0)

    def _go_back(self) -> None:
        self._go_to_step(max(0, self._stack.currentIndex() - 1))

    def _on_get_started(self) -> None:
        if not self._scan_started:
            self._scan_started = True
            self._controller.start_system_scan()
        self._go_to_step(_STEP_SCAN)

    def _on_test_requested(self) -> None:
        form = self._provider.form()
        preset = form.current_preset()
        self._controller.test_connection(
            preset.provider_type,
            model=form.current_model(),
            api_key=form.current_api_key(),
            base_url=form.current_base_url(),
        )

    def _on_provider_continue(self) -> None:
        form = self._provider.form()
        preset = form.current_preset()
        self._provider.continue_button().setEnabled(False)
        self._controller.save_provider(
            preset,
            api_key=form.current_api_key(),
            model=form.current_model(),
            base_url=form.current_base_url(),
        )

    def _on_provider_saved(self, _name: str) -> None:
        self._provider.on_save_succeeded()
        self._provider.continue_button().setEnabled(True)
        self._go_to_step(_STEP_SAFETY)

    def _on_ready(self) -> None:
        try:
            self._controller.mark_complete()
        except Exception:
            self._closing_label.setStyleSheet(f"color: {theme.COLORS['danger']};")
            self._closing_label.setText("Could not save setup completion. Check state directory permissions and try again.")
            return
        self._closing_label.setText("")
        self.completed.emit()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # Marker is intentionally NOT written here — closing before "Got it,
        # let's go" must leave onboarding incomplete (see module docstring).
        #
        # Measured (not assumed): `QThread.wait(timeout_ms)` genuinely blocks
        # the calling (UI) thread's event loop for up to `timeout_ms` — it
        # does not pump events. Worse, if a worker is still running when that
        # bound is exceeded, letting `OnboardingController`/its QThread get
        # destroyed anyway (the old blocking-`shutdown()`-then-close path)
        # hits Qt's fatal "QThread: Destroyed while thread is still running"
        # abort — reproduced directly: closing while `SystemScanWorker` was
        # deliberately kept from finishing crashed the process. Same
        # non-blocking, cooperative pattern as `MainWindow.closeEvent()`
        # (Phase 17) instead: ignore the close, wait for the worker to
        # finish on its own via its normal *_loading_changed(False) signal,
        # then retry. The window is never destroyed while `is_busy()` is
        # true, so the crash path above is unreachable.
        if self._controller.is_busy():
            event.ignore()
            if self._close_pending:
                return
            self._close_pending = True
            self._closing_label.setText("Closing… waiting for the current step to finish safely.")
            self._controller.scan_loading_changed.connect(self._retry_close, Qt.ConnectionType.QueuedConnection)
            self._controller.test_loading_changed.connect(self._retry_close, Qt.ConnectionType.QueuedConnection)
            return

        # Not busy: nothing to wait for, so this stays a fast, synchronous
        # no-op drain (matches SystemPage/SettingsPage shutdown()).
        self._controller.shutdown()
        super().closeEvent(event)

    def _retry_close(self, *_args: object) -> None:
        """The scan/test worker that blocked the earlier close attempt has
        now finished naturally (never killed) — retry the close it
        deferred. A second `*_loading_changed` signal (e.g. scan finishing
        while test is still running) can call this again; `is_busy()` guards
        against retrying early."""
        if self._controller.is_busy():
            return
        self.close()
