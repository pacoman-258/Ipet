from __future__ import annotations

import json
from typing import Any

from backend.environment import DEFAULT_ENVIRONMENT_CONFIG


def _clone_json_value(value: Any) -> Any:
    return json.loads(json.dumps(value))


def create_default_config(
    *,
    default_vision_config: dict[str, Any],
    default_asr_config: dict[str, Any],
    default_backend_url: str,
    default_chat_model: str,
    default_brain_model: str,
    default_model_path: str,
    default_environment_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "vision": _clone_json_value(default_vision_config),
        "environment": _clone_json_value(default_environment_config or DEFAULT_ENVIRONMENT_CONFIG),
        "model_path": default_model_path,
        "brain": {
            "provider": "openai_compatible",
            "model_endpoint": "",
            "model_name": default_chat_model,
            "api_key": "",
            "max_output_tokens": 1024,
            "persona_prompt_file": "",
            "persona": "",
            "self_state": "等待用户目标，并在 act / remember / learn_skill 前交给 Human Ops 处理。",
            "response_style": "",
            "decision_temperature": 0.4,
            "reasoning_effort": "",
            "streaming_enabled": False,
            "web_search_enabled": False,
        },
        "human_ops": {
            "authorization_mode": "review",
            "playwright_profile": "",
            "observe_screen": True,
            "accessibility": True,
            "require_act_review": True,
            "require_memory_review": True,
            "require_skill_review": True,
            "clipboard_write_review": True,
            "click_preview": {"x": 160, "y": 54, "label": "目标位置", "size": 16},
            "observe_model": {
                "enabled": False,
                "provider": "openai_compatible",
                "model_endpoint": "",
                "model_name": "",
                "api_key": "",
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
        "window": {
            "x": 120,
            "y": 80,
            "width": 420,
            "height": 640,
            "locked": False,
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
        "chat": {
            "backend_url": default_backend_url,
            "model": default_brain_model,
            "session_id": "default",
            "voice": "zh-CN-XiaoxiaoNeural",
            "rate_pct": 0,
            "tts_provider": "edge_tts",
            "tts_provider_url": "",
            "tts_api_key": "",
            "tts_voice_id": "",
            "tts_model": "s2.1-pro-free",
            "expression_mode": True,
            "expression_output_format": "ndjson_v1",
            "react_enabled": True,
            "react_visibility": "inline",
            "max_reasoning_steps": 10,
            "asr": _clone_json_value(default_asr_config),
            "system_prompt": "",
        },
    }
