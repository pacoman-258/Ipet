from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from body import live2d_assets as _live2d_assets


def _run_browser_javascript(owner: Any, script: str) -> None:
    owner.browser.page().runJavaScript(script)


@dataclass(frozen=True)
class BodyBridge:
    resolve_model_path: Callable[[str], Path]
    default_model_path_text: str
    ensure_live2d_model: Callable[[Path], tuple[Path, dict, list[dict]]]
    resolve_background_image_path: Callable[[str], Path]
    local_file_url: Callable[[Path], str]
    extract_pet_display_name: Callable[[str], str]
    normalize_vision_config: Callable[[dict], dict]
    json_dumps: Callable[..., str] = json.dumps
    run_javascript: Callable[[Any, str], None] = _run_browser_javascript
    extract_lipsync_meta: Callable[[dict], dict] = _live2d_assets.extract_lipsync_meta
    action_items_from_defs: Callable[[dict, list[dict]], list[dict]] = _live2d_assets.action_items_from_defs
    json_loads: Callable[[str], Any] = json.loads

    def refresh_motion_list(self, owner: Any, prefer_reset: bool) -> None:
        panel = getattr(owner, "control_panel", None)
        model_path_text = (
            panel.model_path_input.text().strip()
            if panel is not None
            else owner.config.get("model_path", "")
        )
        model_path = self.resolve_model_path(model_path_text or owner.config.get("model_path", ""))
        owner.live2d_model_path, motion_groups, expr_defs = self.ensure_live2d_model(model_path)
        owner.lipsync_meta = {"gain": 1.0, "mouth_open_ids": [], "mouth_form_ids": []}
        if model_path.exists():
            try:
                with model_path.open("r", encoding="utf-8") as f:
                    owner.lipsync_meta = self.extract_lipsync_meta(self.json_loads(f.read()))
            except Exception:
                pass

        owner.motion_items = self.action_items_from_defs(motion_groups, expr_defs)
        owner.expression_names = [
            str(item.get("Name") or "").strip()
            for item in expr_defs
            if isinstance(item, dict) and str(item.get("Name") or "").strip()
        ]
        if not owner.motion_items:
            owner.motion_items = [{"type": "motion", "group": "Idle", "index": 0, "label": "Idle[0]"}]

        if panel is not None:
            owner.sync_panel()
            if prefer_reset and panel.motion_combo.count() > 0:
                panel.motion_combo.setCurrentIndex(0)

    def on_web_state_changed(self, owner: Any, payload: str) -> None:
        try:
            state = self.json_loads(payload)
        except Exception:
            return
        if not isinstance(state, dict):
            return

        for key in ("scale", "offset_x", "offset_y", "rotation", "opacity"):
            if key in state:
                owner.config["pet"][key] = state[key]
        panel = getattr(owner, "control_panel", None)
        if panel is not None:
            panel.update_pet_widgets_from_web_state(state)

    def current_body_payload(self, owner: Any) -> dict:
        config = getattr(owner, "config", {})
        if not isinstance(config, dict):
            config = {}

        model_path = self.resolve_model_path(config.get("model_path", ""))
        if not model_path.exists() and self.default_model_path_text:
            model_path = self.resolve_model_path(self.default_model_path_text)
        live2d_path, _, _ = self.ensure_live2d_model(model_path)
        owner.live2d_model_path = live2d_path

        pet_cfg = dict(config.get("pet", {}) if isinstance(config.get("pet", {}), dict) else {})
        background_path = self.resolve_background_image_path(pet_cfg.get("background_image", ""))
        if pet_cfg.get("background_enabled") and background_path.exists() and background_path.is_file():
            pet_cfg["background_image_url"] = self.local_file_url(background_path)
        else:
            pet_cfg["background_image_url"] = ""

        chat_cfg = dict(config.get("chat", {}) if isinstance(config.get("chat", {}), dict) else {})
        lipsync_meta = getattr(owner, "lipsync_meta", {})
        if not isinstance(lipsync_meta, dict):
            lipsync_meta = {}

        return {
            "model_url": self.local_file_url(live2d_path),
            "pet": pet_cfg,
            "chat": {
                **chat_cfg,
                "pet_display_name": self.extract_pet_display_name(chat_cfg.get("system_prompt", "")),
                "available_expressions": list(getattr(owner, "expression_names", [])),
                "lip_sync_gain": float(lipsync_meta.get("gain", 1.0) or 1.0),
                "mouth_parameter_ids": list(lipsync_meta.get("mouth_open_ids", [])),
                "mouth_form_parameter_ids": list(lipsync_meta.get("mouth_form_ids", [])),
            },
            "vision": self.normalize_vision_config(config.get("vision", {})),
        }

    def apply_config_to_web(self, owner: Any, after_script: str | None = None) -> None:
        payload = self.json_dumps(self.current_body_payload(owner), ensure_ascii=False)
        if after_script:
            script = (
                "window.PET_APP && window.PET_APP.applyConfig("
                f"{payload}).then(() => {{ {after_script} }});"
            )
        else:
            script = f"window.PET_APP && window.PET_APP.applyConfig({payload});"
        self.run_javascript(owner, script)

    def play_motion(self, owner: Any, group: str, index: int = 0, reload_model: bool = False) -> None:
        action_script = (
            "window.PET_APP && window.PET_APP.playMotion("
            f"{self.json_dumps(group, ensure_ascii=False)}, {int(index)});"
        )
        if reload_model:
            self.apply_config_to_web(owner, after_script=action_script)
            return
        self.run_javascript(owner, action_script)

    def play_expression(self, owner: Any, name: str, reload_model: bool = False) -> None:
        action_script = (
            "window.PET_APP && window.PET_APP.playExpression("
            f"{self.json_dumps(name, ensure_ascii=False)});"
        )
        if reload_model:
            self.apply_config_to_web(owner, after_script=action_script)
            return
        self.run_javascript(owner, action_script)

    def play_action(self, owner: Any, action: dict) -> None:
        action_type = str(action.get("type", "motion"))
        if action_type == "expression":
            name = str(action.get("name", "")).strip()
            if name:
                play_expression = getattr(owner, "play_expression", None)
                if callable(play_expression):
                    play_expression(name)
                else:
                    self.play_expression(owner, name)
            return

        group = str(action.get("group", "Idle"))
        index = int(action.get("index", 0))
        play_motion = getattr(owner, "play_motion", None)
        if callable(play_motion):
            play_motion(group, index)
            return
        self.play_motion(owner, group, index)
