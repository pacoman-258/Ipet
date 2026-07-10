from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any, Callable


class DesktopConfigActions:
    def __init__(
        self,
        owner,
        *,
        json_module=_json,
        config_path: Path,
        default_config: dict,
        default_asr_config: dict,
        default_backend_url: str,
        default_brain_model: str,
        normalize_model_path: Callable[[str], str],
        normalize_vision_config: Callable[[dict], dict],
        keep_neo_config_shape: Callable[[dict], None],
        normalize_neo_chat_config: Callable[[dict], None],
        screen_provider: Callable[[], Any],
        load_config: Callable[[], dict] | None = None,
        set_geometry: Callable[[Any, int, int, int, int], None] | None = None,
        apply_window_geometry_from_config: Callable[[Any], None] | None = None,
    ) -> None:
        self.owner = owner
        self.json = json_module
        self.config_path = Path(config_path)
        self.default_config = default_config
        self.default_asr_config = default_asr_config
        self.default_backend_url = default_backend_url
        self.default_brain_model = default_brain_model
        self.normalize_model_path = normalize_model_path
        self.normalize_vision_config = normalize_vision_config
        self.keep_neo_config_shape = keep_neo_config_shape
        self.normalize_neo_chat_config = normalize_neo_chat_config
        self.screen_provider = screen_provider
        self.load_config = load_config
        self.set_geometry = set_geometry or self._set_owner_geometry
        self.apply_window_geometry_from_config = (
            apply_window_geometry_from_config or self._apply_owner_window_geometry_from_config
        )

    def apply_from_panel(self) -> None:
        owner = self.owner
        panel = owner.control_panel
        config = owner.config

        config["model_path"] = self.normalize_model_path(panel.model_path_input.text().strip())

        current_pet = config.get("pet", {})
        config["pet"] = {
            "scale": float(panel.scale_spin.value()),
            "offset_x": int(panel.offset_x_spin.value()),
            "offset_y": int(panel.offset_y_spin.value()),
            "rotation": float(panel.rotation_spin.value()),
            "opacity": float(panel.opacity_spin.value()),
            "edit_mode": bool(panel.edit_mode_check.isChecked()),
            "follow_mouse": bool(panel.follow_mouse_check.isChecked()),
            "background_enabled": bool(current_pet.get("background_enabled", False)),
            "background_image": str(current_pet.get("background_image", "")),
            "background_overlay_opacity": float(current_pet.get("background_overlay_opacity", 0.42) or 0.42),
        }

        chat_cfg = config.get("chat", {})
        config["chat"] = {
            "backend_url": str(chat_cfg.get("backend_url", self.default_backend_url)),
            "model": panel.chat_model_input.text().strip() or self.default_brain_model,
            "session_id": str(chat_cfg.get("session_id", "default")),
            "voice": panel.chat_voice_input.text().strip() or "zh-CN-XiaoxiaoNeural",
            "rate_pct": max(-50, min(100, int(panel.chat_rate_slider.value()))),
            "tts_provider": panel.chat_tts_provider_combo.currentText().strip() or "edge_tts",
            "tts_provider_url": panel.chat_tts_provider_url_input.text().strip(),
            "expression_mode": bool(panel.expression_mode_check.isChecked()),
            "expression_output_format": str(chat_cfg.get("expression_output_format", "ndjson_v1")),
            "asr": self._copy_config(chat_cfg.get("asr", self.default_asr_config)),
            "system_prompt": panel.system_prompt_input.toPlainText().strip(),
        }
        self.keep_neo_config_shape(config)
        self.normalize_neo_chat_config(config)

        config["window"] = {
            "x": int(panel.win_x_spin.value()),
            "y": int(panel.win_y_spin.value()),
            "width": int(panel.win_w_spin.value()),
            "height": int(panel.win_h_spin.value()),
            "locked": bool(panel.lock_window_check.isChecked()),
        }
        owner.window_locked = config["window"]["locked"]

        self.set_geometry(
            owner,
            config["window"]["x"],
            config["window"]["y"],
            config["window"]["width"],
            config["window"]["height"],
        )

        owner.refresh_motion_list(prefer_reset=False)
        owner.apply_config_to_web()

    def save_config(self) -> None:
        owner = self.owner
        config = owner.config
        config["model_path"] = self.normalize_model_path(config.get("model_path", ""))
        config["vision"] = self.normalize_vision_config(config.get("vision", {}))
        self.keep_neo_config_shape(config)
        self.normalize_neo_chat_config(config)
        config["window"]["x"] = owner.x()
        config["window"]["y"] = owner.y()
        config["window"]["width"] = owner.width()
        config["window"]["height"] = owner.height()

        with self.config_path.open("w", encoding="utf-8") as f:
            self.json.dump(config, f, ensure_ascii=False, indent=2)
        owner._config_mtime = owner._config_mtime_token()

    def config_mtime_token(self):
        try:
            stat = self.config_path.stat()
        except Exception:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def reload_config_from_disk(self) -> None:
        if self.load_config is None:
            raise RuntimeError("load_config dependency is required")

        owner = self.owner
        owner.config = self.load_config()
        owner.window_locked = bool(owner.config["window"]["locked"])
        owner.apply_window_geometry_from_config()
        owner.refresh_motion_list(prefer_reset=False)
        chat_cfg = owner.config.get("chat", {})
        asr_cfg = chat_cfg.get("asr", {}) if isinstance(chat_cfg, dict) else {}
        if isinstance(asr_cfg, dict) and bool(asr_cfg.get("enabled", True)):
            owner.ensure_asr_service()
        else:
            owner.stop_asr_service()
        owner.vision_controller.apply_config(owner.config)
        owner.apply_config_to_web()

    def reset_to_default(self) -> None:
        owner = self.owner
        owner.config = self._copy_config(self.default_config)

        screen = self.screen_provider()
        if screen is not None:
            available = screen.availableGeometry()
            owner.config["window"]["x"] = available.right() - owner.config["window"]["width"] - 40
            owner.config["window"]["y"] = available.bottom() - owner.config["window"]["height"] - 60

        owner.window_locked = bool(owner.config["window"]["locked"])
        self.apply_window_geometry_from_config(owner)
        owner.refresh_motion_list(prefer_reset=True)
        owner.apply_config_to_web()

    def _copy_config(self, config: dict) -> dict:
        return self.json.loads(self.json.dumps(config))

    @staticmethod
    def _set_owner_geometry(owner, x: int, y: int, width: int, height: int) -> None:
        owner.setGeometry(x, y, width, height)

    @staticmethod
    def _apply_owner_window_geometry_from_config(owner) -> None:
        owner.apply_window_geometry_from_config()
