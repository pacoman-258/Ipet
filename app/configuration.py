from __future__ import annotations

import json
from pathlib import Path
from typing import Callable


CUSTOM_HTTP_TTS_PRESETS = {
    "basic": {
        "label": "自定义 HTTP 模板",
        "config": {
            "url": "http://127.0.0.1:9880/",
            "payload": {},
            "headers": {},
            "query": {},
            "timeout_sec": 60,
        },
    },
    "gpt_sovits": {
        "label": "GPT-SoVITS 预设",
        "config": {
            "url": "http://127.0.0.1:9880/",
            "payload": {
                "text_language": "ja",
                "refer_wav_path": "tts-voice-model/reference.wav",
                "prompt_text": "请改成参考音频对应文本",
                "prompt_language": "ja",
            },
            "inject_fields": ["text"],
            "headers": {},
            "query": {},
            "timeout_sec": 300,
        },
    },
}


def copy_config(config: dict) -> dict:
    return json.loads(json.dumps(config))


def build_custom_http_tts_preset(key: str) -> str:
    preset = CUSTOM_HTTP_TTS_PRESETS.get(key) or CUSTOM_HTTP_TTS_PRESETS["basic"]
    return json.dumps(preset["config"], ensure_ascii=False, indent=2)


def deep_merge(base: dict, override: dict) -> dict:
    merged = copy_config(base)
    for key, value in override.items():
        if key not in merged:
            continue
        base_value = merged.get(key)
        if isinstance(base_value, dict):
            if isinstance(value, dict):
                merged[key] = deep_merge(base_value, value)
            continue
        if isinstance(value, dict):
            continue
        merged[key] = value
    return merged


def keep_neo_config_shape(config: dict, *, default_config: dict) -> None:
    allowed = set(default_config)
    for key in list(config):
        if key not in allowed:
            config.pop(key, None)


def normalize_neo_chat_config(
    config: dict,
    *,
    default_config: dict,
    default_brain_model: str,
) -> None:
    chat_cfg = config.get("chat")
    if not isinstance(chat_cfg, dict):
        chat_cfg = {}
        config["chat"] = chat_cfg
    allowed = set(default_config["chat"])
    for key in list(chat_cfg):
        if key not in allowed:
            chat_cfg.pop(key, None)
    model = str(chat_cfg.get("model") or "").strip()
    chat_cfg["model"] = model or default_brain_model
    session_id = str(chat_cfg.get("session_id") or "").strip()
    chat_cfg["session_id"] = session_id or "default"


def load_config(
    *,
    config_path: Path,
    default_config: dict,
    default_brain_model: str,
    normalize_model_path_func: Callable[[str], str],
    normalize_vision_config_func: Callable[[dict], dict],
) -> dict:
    if not config_path.exists():
        return copy_config(default_config)

    try:
        with config_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return copy_config(default_config)

    config = deep_merge(default_config, raw if isinstance(raw, dict) else {})
    config["model_path"] = normalize_model_path_func(config.get("model_path", ""))
    pet_cfg = config.get("pet", {})
    if not isinstance(pet_cfg, dict):
        pet_cfg = {}
        config["pet"] = pet_cfg
    pet_cfg["background_enabled"] = bool(pet_cfg.get("background_enabled", False))
    pet_cfg["background_image"] = str(pet_cfg.get("background_image") or "").strip()
    try:
        pet_cfg["background_overlay_opacity"] = float(pet_cfg.get("background_overlay_opacity", 0.42))
    except Exception:
        pet_cfg["background_overlay_opacity"] = 0.42
    pet_cfg["background_overlay_opacity"] = max(0.0, min(0.9, pet_cfg["background_overlay_opacity"]))
    config["vision"] = normalize_vision_config_func(config.get("vision", {}))
    keep_neo_config_shape(config, default_config=default_config)
    normalize_neo_chat_config(
        config,
        default_config=default_config,
        default_brain_model=default_brain_model,
    )
    return config


def extract_pet_display_name(system_prompt: str) -> str:
    raw = str(system_prompt or "").strip()
    if not raw:
        return "桌宠"
    try:
        data = json.loads(raw)
    except Exception:
        return "桌宠"
    if not isinstance(data, dict):
        return "桌宠"
    character = data.get("character")
    if isinstance(character, dict):
        for key in ("name_cn", "name"):
            value = str(character.get(key) or "").strip()
            if value:
                return value
    for key in ("name", "title"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return "桌宠"


_keep_neo_config_shape = keep_neo_config_shape
_normalize_neo_chat_config = normalize_neo_chat_config
