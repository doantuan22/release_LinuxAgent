"""ProviderForm (docs/ui_ux_spec.md mục 4.6/9.5, gui_implementation_plan.md
Phase 10) — dropdown preset + API key (masked) + Base URL + Model.

Dùng được cho cả "Add provider"/"Edit provider" ở Settings page VÀ bước
Choose Provider của Onboarding (Phase 18) — mục 9.5 yêu cầu Onboarding "sử
dụng CÙNG COMPONENT với Settings", nên widget này chỉ đọc/ghi field thuần,
KHÔNG tự gọi config_service/factory hay biết gì về Settings/Onboarding cụ
thể — caller (`SettingsController` hiện tại, controller Onboarding sau này)
tự quyết định gọi API nào với giá trị lấy từ đây.

Không tự xoá API key khi test connection fail (đúng mục 9.5: "Không xóa API
key field khi request fail") — chỉ `clear_api_key()` được gọi từ ngoài, và
chỉ nên gọi sau khi LƯU thành công, không phải sau test connection.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QFormLayout, QLineEdit, QWidget

from agent.core.provider_presets import PROVIDER_PRESETS, ProviderPreset
from agent.gui import theme

_KEY_PLACEHOLDER_ADD = "API key"
_KEY_PLACEHOLDER_EDIT = "Leave blank to keep current key"


class ProviderForm(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._preset_locked = False

        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._preset_combo = QComboBox()
        self._preset_combo.setFont(theme.general_font("input"))
        for preset in PROVIDER_PRESETS:
            self._preset_combo.addItem(preset.display_name, preset)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText(_KEY_PLACEHOLDER_ADD)

        self._base_url_edit = QLineEdit()
        self._model_edit = QLineEdit()

        for edit in (self._api_key_edit, self._base_url_edit, self._model_edit):
            edit.setFont(theme.general_font("input"))

        layout.addRow("Provider", self._preset_combo)
        layout.addRow("API key", self._api_key_edit)
        layout.addRow("Base URL", self._base_url_edit)
        layout.addRow("Model", self._model_edit)

        self._apply_preset(PROVIDER_PRESETS[0])

    # --- public API dùng chung Add/Edit/Onboarding ---

    def set_add_mode(self, preset: ProviderPreset | None = None) -> None:
        """Preset chọn được, API key placeholder về đúng nghĩa "nhập key mới"."""
        self._preset_locked = False
        self._preset_combo.setEnabled(True)
        self._api_key_edit.setPlaceholderText(_KEY_PLACEHOLDER_ADD)
        target = preset or PROVIDER_PRESETS[0]
        self._preset_combo.setCurrentIndex(self._index_of_preset(target))
        self._apply_preset(target)

    def set_edit_mode(
        self, *, provider_type: str, model: str, base_url: str | None, requires_api_key: bool
    ) -> None:
        """Khoá preset (không đổi provider_type qua form sửa) — chỉ cho sửa
        model/base_url/key. Bỏ trống API key nghĩa là giữ key cũ."""
        preset = next((p for p in PROVIDER_PRESETS if p.provider_type == provider_type), None)
        if preset is not None:
            self._preset_combo.setCurrentIndex(self._index_of_preset(preset))
        self._preset_locked = True
        self._preset_combo.setEnabled(False)

        self._model_edit.setText(model)
        self._base_url_edit.setText(base_url or "")
        self._base_url_edit.setEnabled(preset.ask_base_url if preset else True)
        self._api_key_edit.clear()
        self._api_key_edit.setPlaceholderText(_KEY_PLACEHOLDER_EDIT)
        self._api_key_edit.setEnabled(requires_api_key)

    def current_preset(self) -> ProviderPreset:
        return self._preset_combo.currentData()

    def current_model(self) -> str:
        return self._model_edit.text().strip()

    def current_base_url(self) -> str | None:
        text = self._base_url_edit.text().strip()
        return text or None

    def current_api_key(self) -> str | None:
        text = self._api_key_edit.text()
        return text or None

    def clear_api_key(self) -> None:
        """Chỉ gọi sau khi LƯU thành công (mục 9.5) — không gọi sau test
        connection, thất bại hay thành công."""
        self._api_key_edit.clear()

    def api_key_field(self) -> QLineEdit:
        return self._api_key_edit

    def preset_combo(self) -> QComboBox:
        return self._preset_combo

    def model_field(self) -> QLineEdit:
        return self._model_edit

    def base_url_field(self) -> QLineEdit:
        return self._base_url_edit

    # --- internal ---

    def _index_of_preset(self, preset: ProviderPreset) -> int:
        for index in range(self._preset_combo.count()):
            if self._preset_combo.itemData(index) is preset:
                return index
        return 0

    def _on_preset_changed(self, _index: int) -> None:
        if self._preset_locked:
            return
        preset = self._preset_combo.currentData()
        if preset is not None:
            self._apply_preset(preset)

    def _apply_preset(self, preset: ProviderPreset) -> None:
        self._model_edit.setText(preset.default_model)
        self._base_url_edit.setText(preset.default_base_url or "")
        self._base_url_edit.setEnabled(preset.ask_base_url)
        self._api_key_edit.clear()
        self._api_key_edit.setEnabled(preset.requires_api_key)
