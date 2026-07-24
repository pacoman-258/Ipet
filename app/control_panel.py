from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from app import configuration as _configuration
from app import desktop_runtime as _desktop_runtime
from body import live2d_assets as _live2d_assets


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BACKEND_URL = "http://127.0.0.1:8008"
DEFAULT_CHAT_MODEL = "gpt-5.4"
DEFAULT_BRAIN_MODEL = DEFAULT_CHAT_MODEL
CUSTOM_HTTP_TTS_PRESETS = _configuration.CUSTOM_HTTP_TTS_PRESETS


def _apply_qt_runtime_env(platform_name: str | None = None) -> dict[str, str]:
    return _desktop_runtime.apply_qt_runtime_env(
        platform_name_value=platform_name,
        environ=os.environ,
        sys_platform=sys.platform,
    )


def _prefer_pyqt_bindings(platform_name: str | None = None) -> bool:
    return _desktop_runtime.prefer_pyqt_bindings(platform_name, sys_platform=sys.platform)


_apply_qt_runtime_env()

if _prefer_pyqt_bindings():
    try:
        from PyQt6.QtCore import Qt, QSignalBlocker
        from PyQt6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QFileDialog,
            QFormLayout,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QPlainTextEdit,
            QPushButton,
            QSlider,
            QSpinBox,
            QVBoxLayout,
            QWidget,
        )
    except ImportError:
        from PySide6.QtCore import Qt, QSignalBlocker
        from PySide6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QFileDialog,
            QFormLayout,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QPlainTextEdit,
            QPushButton,
            QSlider,
            QSpinBox,
            QVBoxLayout,
            QWidget,
        )
else:
    try:
        from PySide6.QtCore import Qt, QSignalBlocker
        from PySide6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QFileDialog,
            QFormLayout,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QPlainTextEdit,
            QPushButton,
            QSlider,
            QSpinBox,
            QVBoxLayout,
            QWidget,
        )
    except ImportError:
        from PyQt6.QtCore import Qt, QSignalBlocker
        from PyQt6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QFileDialog,
            QFormLayout,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QPlainTextEdit,
            QPushButton,
            QSlider,
            QSpinBox,
            QVBoxLayout,
            QWidget,
        )


def build_custom_http_tts_preset(key: str) -> str:
    return _configuration.build_custom_http_tts_preset(key)


def normalize_model_path(path_text: str) -> str:
    return _live2d_assets.normalize_model_path(path_text, root_dir=ROOT_DIR)


def is_service_healthy(base_url: str, *, require_asr: bool = False) -> bool:
    return _desktop_runtime.is_service_healthy(
        base_url,
        require_asr=require_asr,
        requests_module=requests,
    )


def is_backend_live(backend_url: str) -> bool:
    return _desktop_runtime.is_backend_live(
        backend_url,
        service_healthy=is_service_healthy,
    )


def backend_supports_required_routes(base_url: str) -> bool:
    return _desktop_runtime.backend_supports_required_routes(
        base_url,
        service_healthy=is_service_healthy,
    )


def is_backend_healthy(backend_url: str) -> bool:
    return _desktop_runtime.is_backend_healthy(
        backend_url,
        backend_live=is_backend_live,
        supports_required_routes=backend_supports_required_routes,
    )


class ControlPanel(QWidget):
    def __init__(self, pet_window: "DesktopPet"):
        super().__init__()
        self.pet_window = pet_window
        self.setWindowTitle("桌宠控制面板")
        self.resize(520, 520)

        self._build_ui()
        self._wire_events()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        model_box = QGroupBox("模型")
        model_layout = QGridLayout(model_box)

        self.model_path_input = QLineEdit()
        self.browse_button = QPushButton("浏览...")
        self.reload_button = QPushButton("重载模型")

        self.motion_combo = QComboBox()
        self.play_motion_button = QPushButton("播放动作")

        model_layout.addWidget(QLabel("model3.json 路径"), 0, 0)
        model_layout.addWidget(self.model_path_input, 0, 1)
        model_layout.addWidget(self.browse_button, 0, 2)
        model_layout.addWidget(self.reload_button, 1, 2)
        model_layout.addWidget(QLabel("动作"), 1, 0)
        model_layout.addWidget(self.motion_combo, 1, 1)
        model_layout.addWidget(self.play_motion_button, 2, 2)

        chat_box = QGroupBox("对话")
        chat_layout = QGridLayout(chat_box)
        self.chat_model_input = QLineEdit()
        self.chat_model_input.setPlaceholderText(DEFAULT_BRAIN_MODEL)
        self.chat_voice_input = QLineEdit()
        self.chat_voice_input.setPlaceholderText("zh-CN-XiaoxiaoNeural")
        self.chat_tts_provider_combo = QComboBox()
        self.chat_tts_provider_combo.addItem("edge_tts")
        self.chat_tts_provider_combo.addItem("custom_http")
        self.chat_tts_provider_combo.addItem("qwen_tts_local")
        self.chat_tts_provider_combo.addItem("fish_audio")
        self.chat_tts_preset_combo = QComboBox()
        self.chat_tts_preset_combo.addItem("选择预设...", "")
        for preset_key, preset in CUSTOM_HTTP_TTS_PRESETS.items():
            self.chat_tts_preset_combo.addItem(str(preset.get("label") or preset_key), preset_key)
        self.chat_tts_preset_apply_button = QPushButton("一键填入")
        self.chat_tts_provider_url_input = QLineEdit()
        self.chat_tts_provider_url_input.setPlaceholderText("自定义语音接口地址或配置")
        self.chat_tts_provider_url_input.setToolTip(
            '直接填 URL，或用“一键填入”生成 JSON 预设，再修改参考音频和提示文本'
        )
        self.chat_rate_slider = QSlider(Qt.Orientation.Horizontal)
        self.chat_rate_slider.setRange(-50, 100)
        self.chat_rate_slider.setValue(0)
        self.chat_rate_value_label = QLabel("+0%")
        self.expression_mode_check = QCheckBox("表情联动")
        self.expression_mode_check.setChecked(True)
        self.expression_format_value_label = QLabel("ndjson_v1")
        self.system_prompt_input = QPlainTextEdit()
        self.system_prompt_input.setPlaceholderText("系统提示词：定义桌宠人设、语气、规则等")
        self.system_prompt_input.setFixedHeight(88)
        self.backend_test_button = QPushButton("测试后端")
        self.backend_status_label = QLabel("unknown")
        rate_row = QWidget()
        rate_row_layout = QHBoxLayout(rate_row)
        rate_row_layout.setContentsMargins(0, 0, 0, 0)
        rate_row_layout.setSpacing(6)
        rate_row_layout.addWidget(self.chat_rate_slider, 1)
        rate_row_layout.addWidget(self.chat_rate_value_label, 0)
        chat_layout.addWidget(QLabel("Brain 模型"), 0, 0)
        chat_layout.addWidget(self.chat_model_input, 0, 1)
        chat_layout.addWidget(self.backend_test_button, 0, 2)
        chat_layout.addWidget(QLabel("语音"), 1, 0)
        chat_layout.addWidget(self.chat_voice_input, 1, 1, 1, 2)
        chat_layout.addWidget(QLabel("TTS方式"), 2, 0)
        chat_layout.addWidget(self.chat_tts_provider_combo, 2, 1, 1, 2)
        chat_layout.addWidget(QLabel("TTS预设"), 3, 0)
        chat_layout.addWidget(self.chat_tts_preset_combo, 3, 1)
        chat_layout.addWidget(self.chat_tts_preset_apply_button, 3, 2)
        chat_layout.addWidget(QLabel("TTS接口"), 4, 0)
        chat_layout.addWidget(self.chat_tts_provider_url_input, 4, 1, 1, 2)
        chat_layout.addWidget(QLabel("语速"), 5, 0)
        chat_layout.addWidget(rate_row, 5, 1, 1, 2)
        chat_layout.addWidget(QLabel("表情驱动"), 6, 0)
        chat_layout.addWidget(self.expression_mode_check, 6, 1, 1, 2)
        chat_layout.addWidget(QLabel("协议版本"), 7, 0)
        chat_layout.addWidget(self.expression_format_value_label, 7, 1, 1, 2)
        chat_layout.addWidget(QLabel("系统提示词"), 8, 0)
        chat_layout.addWidget(self.system_prompt_input, 8, 1, 1, 2)
        chat_layout.addWidget(QLabel("状态"), 9, 0)
        chat_layout.addWidget(self.backend_status_label, 9, 1, 1, 2)

        pet_box = QGroupBox("形象参数")
        pet_form = QFormLayout(pet_box)

        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.05, 5.0)
        self.scale_spin.setSingleStep(0.05)
        self.scale_spin.setDecimals(2)

        self.offset_x_spin = QSpinBox()
        self.offset_x_spin.setRange(-5000, 5000)

        self.offset_y_spin = QSpinBox()
        self.offset_y_spin.setRange(-5000, 5000)

        self.rotation_spin = QDoubleSpinBox()
        self.rotation_spin.setRange(-180.0, 180.0)
        self.rotation_spin.setSingleStep(1.0)
        self.rotation_spin.setDecimals(1)

        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0.1, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.setDecimals(2)

        self.edit_mode_check = QCheckBox("编辑模式（模型可拖拽，滚轮缩放）")
        self.follow_mouse_check = QCheckBox("视线跟随鼠标")

        pet_form.addRow("缩放", self.scale_spin)
        pet_form.addRow("偏移 X", self.offset_x_spin)
        pet_form.addRow("偏移 Y", self.offset_y_spin)
        pet_form.addRow("旋转", self.rotation_spin)
        pet_form.addRow("透明度", self.opacity_spin)
        pet_form.addRow(self.edit_mode_check)
        pet_form.addRow(self.follow_mouse_check)

        window_box = QGroupBox("窗口")
        window_form = QFormLayout(window_box)

        self.win_x_spin = QSpinBox()
        self.win_x_spin.setRange(-10000, 10000)

        self.win_y_spin = QSpinBox()
        self.win_y_spin.setRange(-10000, 10000)

        self.win_w_spin = QSpinBox()
        self.win_w_spin.setRange(120, 2000)

        self.win_h_spin = QSpinBox()
        self.win_h_spin.setRange(120, 2000)

        self.lock_window_check = QCheckBox("锁定窗口位置（仍可右键）")

        window_form.addRow("窗口 X", self.win_x_spin)
        window_form.addRow("窗口 Y", self.win_y_spin)
        window_form.addRow("窗口宽", self.win_w_spin)
        window_form.addRow("窗口高", self.win_h_spin)
        window_form.addRow(self.lock_window_check)

        button_row = QHBoxLayout()
        self.apply_button = QPushButton("应用")
        self.save_button = QPushButton("保存配置")
        self.reset_button = QPushButton("恢复默认")
        self.hide_button = QPushButton("关闭面板")
        button_row.addWidget(self.apply_button)
        button_row.addWidget(self.save_button)
        button_row.addWidget(self.reset_button)
        button_row.addWidget(self.hide_button)

        hint = QLabel("提示：按住 Alt + 左键可拖动桌宠窗口。")

        root.addWidget(model_box)
        root.addWidget(chat_box)
        root.addWidget(pet_box)
        root.addWidget(window_box)
        root.addWidget(hint)
        root.addLayout(button_row)

    def _wire_events(self) -> None:
        self.browse_button.clicked.connect(self.on_browse_model)
        self.reload_button.clicked.connect(self.on_reload_model)
        self.play_motion_button.clicked.connect(self.on_play_motion)
        self.apply_button.clicked.connect(self.pet_window.apply_from_panel)
        self.save_button.clicked.connect(self.on_save)
        self.reset_button.clicked.connect(self.on_reset)
        self.hide_button.clicked.connect(self.hide)
        self.backend_test_button.clicked.connect(self.on_test_backend)
        self.chat_rate_slider.valueChanged.connect(self.on_chat_rate_changed)
        self.chat_tts_preset_apply_button.clicked.connect(self.on_apply_tts_preset)

    def on_chat_rate_changed(self, value: int) -> None:
        self.chat_rate_value_label.setText(f"{int(value):+d}%")

    def on_apply_tts_preset(self) -> None:
        preset_key = str(self.chat_tts_preset_combo.currentData() or "").strip()
        if not preset_key:
            return
        self.chat_tts_provider_combo.setCurrentText("custom_http")
        self.chat_tts_provider_url_input.setText(build_custom_http_tts_preset(preset_key))

    def on_browse_model(self) -> None:
        dialog = QFileDialog(self, "选择 Live2D 模型配置", str(ROOT_DIR))
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setNameFilters(
            [
                "Live2D 模型 (*.model3.json *.model.json)",
                "Cubism 4 (*.model3.json)",
                "Legacy (*.model.json)",
                "All Files (*)",
            ]
        )
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        if not dialog.exec():
            return
        selected = dialog.selectedFiles()
        if not selected:
            return
        path = selected[0]
        if not path:
            return

        normalized = normalize_model_path(path)
        self.model_path_input.setText(normalized)
        # Keep config in sync before refresh to avoid input being overwritten.
        self.pet_window.config["model_path"] = normalized
        self.pet_window.refresh_motion_list(prefer_reset=True)
        self.pet_window.apply_config_to_web()

    def on_reload_model(self) -> None:
        self.pet_window.apply_from_panel()
        self.pet_window.apply_config_to_web()

    def on_test_backend(self) -> None:
        self.pet_window.apply_from_panel()
        chat_cfg = self.pet_window.config.get("chat", {})
        backend_url = str(chat_cfg.get("backend_url", DEFAULT_BACKEND_URL))
        ok = is_backend_healthy(backend_url)
        self.backend_status_label.setText("online" if ok else "offline")

    def on_play_motion(self) -> None:
        data = self.motion_combo.currentData()
        if not isinstance(data, dict):
            return
        self.pet_window.play_action(data)

    def on_save(self) -> None:
        self.pet_window.apply_from_panel()
        self.pet_window.save_config()

    def on_reset(self) -> None:
        self.pet_window.reset_to_default()

    def set_from_config(self, config: dict, motions: list[dict]) -> None:
        widgets = [
            self.model_path_input,
            self.scale_spin,
            self.offset_x_spin,
            self.offset_y_spin,
            self.rotation_spin,
            self.opacity_spin,
            self.edit_mode_check,
            self.follow_mouse_check,
            self.win_x_spin,
            self.win_y_spin,
            self.win_w_spin,
            self.win_h_spin,
            self.lock_window_check,
            self.motion_combo,
            self.chat_voice_input,
            self.chat_tts_provider_combo,
            self.chat_tts_preset_combo,
            self.chat_tts_provider_url_input,
            self.chat_rate_slider,
            self.expression_mode_check,
            self.system_prompt_input,
        ]

        blockers = [QSignalBlocker(w) for w in widgets]
        _ = blockers

        self.model_path_input.setText(config.get("model_path", ""))
        self.chat_model_input.setText(config.get("chat", {}).get("model", DEFAULT_BRAIN_MODEL))
        chat_config = config.get("chat", {}) if isinstance(config.get("chat", {}), dict) else {}
        provider = str(chat_config.get("tts_provider", "edge_tts")).strip() or "edge_tts"
        provider = provider if provider in ("edge_tts", "custom_http", "qwen_tts_local", "fish_audio") else "edge_tts"
        voice_fallback = "Fish 音色模型 ID" if provider == "fish_audio" else "zh-CN-XiaoxiaoNeural"
        voice_text = str(
            chat_config.get("tts_voice_id") if provider == "fish_audio" else chat_config.get("voice", voice_fallback)
        ).strip() or voice_fallback
        self.chat_voice_input.setText(voice_text)
        self.chat_voice_input.setPlaceholderText(voice_fallback)
        self.chat_tts_provider_combo.setCurrentText(provider)
        self.chat_tts_preset_combo.setCurrentIndex(0)
        self.chat_tts_provider_url_input.setText(str(chat_config.get("tts_provider_url", "")).strip())
        try:
            rate_pct = int(config.get("chat", {}).get("rate_pct", 0))
        except Exception:
            rate_pct = 0
        rate_pct = max(-50, min(100, rate_pct))
        self.chat_rate_slider.setValue(rate_pct)
        self.chat_rate_value_label.setText(f"{rate_pct:+d}%")
        self.expression_mode_check.setChecked(bool(config.get("chat", {}).get("expression_mode", True)))
        self.expression_format_value_label.setText(str(config.get("chat", {}).get("expression_output_format", "ndjson_v1")))
        self.system_prompt_input.setPlainText(config.get("chat", {}).get("system_prompt", ""))
        self.scale_spin.setValue(float(config["pet"]["scale"]))
        self.offset_x_spin.setValue(int(config["pet"]["offset_x"]))
        self.offset_y_spin.setValue(int(config["pet"]["offset_y"]))
        self.rotation_spin.setValue(float(config["pet"]["rotation"]))
        self.opacity_spin.setValue(float(config["pet"]["opacity"]))
        self.edit_mode_check.setChecked(bool(config["pet"]["edit_mode"]))
        self.follow_mouse_check.setChecked(bool(config["pet"]["follow_mouse"]))

        self.win_x_spin.setValue(int(config["window"]["x"]))
        self.win_y_spin.setValue(int(config["window"]["y"]))
        self.win_w_spin.setValue(int(config["window"]["width"]))
        self.win_h_spin.setValue(int(config["window"]["height"]))
        self.lock_window_check.setChecked(bool(config["window"]["locked"]))

        self.motion_combo.clear()
        for item in motions:
            self.motion_combo.addItem(item["label"], item)

    def update_pet_widgets_from_web_state(self, state: dict) -> None:
        widgets = [
            self.scale_spin,
            self.offset_x_spin,
            self.offset_y_spin,
            self.rotation_spin,
            self.opacity_spin,
        ]
        blockers = [QSignalBlocker(w) for w in widgets]
        _ = blockers

        if "scale" in state:
            self.scale_spin.setValue(float(state["scale"]))
        if "offset_x" in state:
            self.offset_x_spin.setValue(int(state["offset_x"]))
        if "offset_y" in state:
            self.offset_y_spin.setValue(int(state["offset_y"]))
        if "rotation" in state:
            self.rotation_spin.setValue(float(state["rotation"]))
        if "opacity" in state:
            self.opacity_spin.setValue(float(state["opacity"]))


__all__ = ["ControlPanel"]
