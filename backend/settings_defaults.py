from __future__ import annotations

from copy import deepcopy
from typing import Any

from .tts import DEFAULT_PROVIDER as DEFAULT_TTS_PROVIDER


NEO_DEFAULTS: dict[str, Any] = {
    "model_path": "",
    "chat": {
        "backend_url": "http://127.0.0.1:8008",
        "model": "gpt-5.4",
        "session_id": "default",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate_pct": 0,
        "tts_provider": DEFAULT_TTS_PROVIDER,
        "tts_provider_url": "",
        "expression_mode": True,
        "expression_output_format": "ndjson_v1",
        "system_prompt": "",
        "asr": {
            "enabled": False,
            "provider": "disabled",
            "api_base_url": "",
            "provider_url": "https://api.groq.com/openai/v1/audio/transcriptions",
            "model": "whisper-large-v3-turbo",
            "api_key": "",
            "push_to_talk_key": "Alt",
            "interim_results": False,
        },
    },
    "pet": {
        "scale": 0.3,
        "offset_x": 0,
        "offset_y": 40,
        "rotation": 0.0,
        "opacity": 1.0,
        "edit_mode": False,
        "follow_mouse": True,
        "background_enabled": False,
        "background_image": "",
        "background_overlay_opacity": 0.42,
    },
    "window": {
        "x": 120,
        "y": 80,
        "width": 420,
        "height": 640,
        "locked": False,
    },
    "brain": {
        "provider": "openai_compatible",
        "model_endpoint": "",
        "model_name": "gpt-5.4",
        "max_output_tokens": 1024,
        "persona_prompt_file": "",
        "persona": "",
        "self_state": "等待用户目标，并在 act / remember / learn_skill 前请求批准。",
        "response_style": "",
        "decision_temperature": 0.4,
        "reasoning_effort": "",
        "streaming_enabled": False,
        "web_search_enabled": False,
    },
    "human_ops": {
        "playwright_profile": "",
        "observe_screen": True,
        "accessibility": True,
        "require_act_review": True,
        "require_memory_review": True,
        "require_skill_review": True,
        "clipboard_write_review": True,
        "click_preview": {"x": 160, "y": 54, "label": "目标位置", "size": 16},
        "filesystem": {
            "enabled": True,
            "allowed_roots": [],
            "max_read_bytes": 1000000,
            "max_write_bytes": 1000000,
            "max_list_entries": 200,
        },
        "observe_model": {
            "enabled": False,
            "provider": "openai_compatible",
            "model_endpoint": "",
            "model_name": "",
            "max_output_tokens": 512,
            "timeout_sec": 90.0,
        },
    },
    "memory": {
        "conversation_saving": True,
        "long_term_enabled": True,
        "preferences_enabled": True,
        "relationship_enabled": True,
        "retention_days": 365,
        "review_limit": 20,
        "review_queue": [],
    },
    "skills": {
        "recipes_enabled": True,
        "auto_propose": True,
        "review_required": True,
        "recipes": [],
        "proposal_queue": [],
    },
    "diagnostics": {
        "enabled": True,
        "log_level": "info",
        "last_error": "",
    },
}

ALLOWED_CONFIG_KEYS = set(NEO_DEFAULTS)


def settings_defaults() -> dict[str, Any]:
    return deepcopy(NEO_DEFAULTS)


def allowed_config_keys() -> set[str]:
    return set(ALLOWED_CONFIG_KEYS)


__all__ = [
    "ALLOWED_CONFIG_KEYS",
    "NEO_DEFAULTS",
    "allowed_config_keys",
    "settings_defaults",
]
