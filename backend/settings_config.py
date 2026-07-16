from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if key not in base:
            continue
        base_value = base.get(key)
        if isinstance(base_value, dict):
            if isinstance(value, dict):
                merged[key] = deep_merge(base_value, value)
            continue
        if isinstance(value, dict):
            continue
        merged[key] = deepcopy(value)
    return merged


def load_raw_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def normalize_private_config(
    raw: dict[str, Any] | None = None,
    *,
    config_path: Path,
    defaults: dict[str, Any],
    allowed_keys: Iterable[str],
) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else load_raw_config(config_path)
    selected = {key: source[key] for key in allowed_keys if key in source}
    config = deep_merge(defaults, selected)
    brain = config.setdefault("brain", {})
    raw_brain = source.get("brain") if isinstance(source.get("brain"), dict) else {}
    if raw_brain.get("api_key"):
        brain["api_key"] = str(raw_brain.get("api_key") or "")
    chat = config.setdefault("chat", {})
    raw_chat = source.get("chat") if isinstance(source.get("chat"), dict) else {}
    asr = chat.setdefault("asr", {})
    raw_asr = raw_chat.get("asr") if isinstance(raw_chat.get("asr"), dict) else {}
    if raw_asr.get("api_key"):
        asr["api_key"] = str(raw_asr.get("api_key") or "")
    human_ops = config.setdefault("human_ops", {})
    raw_human_ops = source.get("human_ops") if isinstance(source.get("human_ops"), dict) else {}
    observe_model = human_ops.setdefault("observe_model", {})
    raw_observe_model = raw_human_ops.get("observe_model") if isinstance(raw_human_ops.get("observe_model"), dict) else {}
    if raw_observe_model.get("api_key"):
        observe_model["api_key"] = str(raw_observe_model.get("api_key") or "")
    vision = config.setdefault("vision", {})
    raw_vision = source.get("vision") if isinstance(source.get("vision"), dict) else {}
    analyzer = vision.setdefault("analyzer", {})
    raw_analyzer = raw_vision.get("analyzer") if isinstance(raw_vision.get("analyzer"), dict) else {}
    if raw_analyzer.get("api_key"):
        analyzer["api_key"] = str(raw_analyzer.get("api_key") or "")
    return config


def secret_preview(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "*" * len(text)
    return f"{text[:2]}***{text[-2:]}"


def public_config(private_config: dict[str, Any]) -> dict[str, Any]:
    public = deepcopy(private_config)
    brain = public.get("brain")
    if not isinstance(brain, dict):
        brain = {}
        public["brain"] = brain
    secret = str(brain.pop("api_key", "") or "")
    brain.pop("api_key_clear", None)
    brain["api_key_set"] = bool(secret)
    brain["api_key_preview"] = secret_preview(secret)
    chat = public.get("chat")
    if not isinstance(chat, dict):
        chat = {}
        public["chat"] = chat
    asr = chat.get("asr")
    if not isinstance(asr, dict):
        asr = {}
        chat["asr"] = asr
    asr_secret = str(asr.pop("api_key", "") or "")
    asr.pop("api_key_clear", None)
    asr["api_key_set"] = bool(asr_secret)
    asr["api_key_preview"] = secret_preview(asr_secret)
    human_ops = public.get("human_ops")
    if not isinstance(human_ops, dict):
        human_ops = {}
        public["human_ops"] = human_ops
    observe_model = human_ops.get("observe_model")
    if not isinstance(observe_model, dict):
        observe_model = {}
        human_ops["observe_model"] = observe_model
    observe_secret = str(observe_model.pop("api_key", "") or "")
    observe_model.pop("api_key_clear", None)
    observe_model["api_key_set"] = bool(observe_secret)
    observe_model["api_key_preview"] = secret_preview(observe_secret)
    vision = public.get("vision")
    if not isinstance(vision, dict):
        vision = {}
        public["vision"] = vision
    analyzer = vision.get("analyzer")
    if not isinstance(analyzer, dict):
        analyzer = {}
        vision["analyzer"] = analyzer
    analyzer_secret = str(analyzer.pop("api_key", "") or "")
    analyzer.pop("api_key_clear", None)
    analyzer["api_key_set"] = bool(analyzer_secret)
    analyzer["api_key_preview"] = secret_preview(analyzer_secret)
    return public


def settings_payload(
    private_config: dict[str, Any] | None = None,
    *,
    config_path: Path,
    defaults: dict[str, Any],
    allowed_keys: Iterable[str],
) -> dict[str, Any]:
    config = normalize_private_config(
        private_config,
        config_path=config_path,
        defaults=defaults,
        allowed_keys=allowed_keys,
    )
    return {
        "config": public_config(config),
        "defaults": public_config(
            normalize_private_config(
                defaults,
                config_path=config_path,
                defaults=defaults,
                allowed_keys=allowed_keys,
            )
        ),
    }


def _apply_optional_secret_update(target: dict[str, Any], incoming: dict[str, Any], *, existing_secret: str = "") -> None:
    retained_secret = str(existing_secret or target.get("api_key") or "")
    if incoming.get("api_key_clear"):
        target.pop("api_key", None)
    elif "api_key" in incoming:
        secret = str(incoming.get("api_key") or "")
        if secret:
            target["api_key"] = secret
        elif retained_secret:
            target["api_key"] = retained_secret
    target.pop("api_key_clear", None)


def apply_settings_update(
    incoming: dict[str, Any],
    *,
    current: dict[str, Any] | None = None,
    config_path: Path,
    defaults: dict[str, Any],
    allowed_keys: Iterable[str],
) -> dict[str, Any]:
    current_config = normalize_private_config(
        current,
        config_path=config_path,
        defaults=defaults,
        allowed_keys=allowed_keys,
    )
    current_brain = current_config.get("brain") if isinstance(current_config.get("brain"), dict) else {}
    current_brain_secret = str(current_brain.get("api_key") or "")
    current_chat = current_config.get("chat") if isinstance(current_config.get("chat"), dict) else {}
    current_asr = current_chat.get("asr") if isinstance(current_chat.get("asr"), dict) else {}
    current_asr_secret = str(current_asr.get("api_key") or "")
    current_human_ops = current_config.get("human_ops") if isinstance(current_config.get("human_ops"), dict) else {}
    current_observe_model = (
        current_human_ops.get("observe_model") if isinstance(current_human_ops.get("observe_model"), dict) else {}
    )
    current_observe_secret = str(current_observe_model.get("api_key") or "")
    current_vision = current_config.get("vision") if isinstance(current_config.get("vision"), dict) else {}
    current_analyzer = current_vision.get("analyzer") if isinstance(current_vision.get("analyzer"), dict) else {}
    current_analyzer_secret = str(current_analyzer.get("api_key") or "")
    next_config = deep_merge(current_config, incoming)

    incoming_brain = incoming.get("brain") if isinstance(incoming.get("brain"), dict) else {}
    brain = next_config.setdefault("brain", {})
    _apply_optional_secret_update(brain, incoming_brain, existing_secret=current_brain_secret)

    incoming_chat = incoming.get("chat") if isinstance(incoming.get("chat"), dict) else {}
    incoming_asr = incoming_chat.get("asr") if isinstance(incoming_chat.get("asr"), dict) else {}
    chat = next_config.setdefault("chat", {})
    asr = chat.setdefault("asr", {})
    _apply_optional_secret_update(asr, incoming_asr, existing_secret=current_asr_secret)

    incoming_human_ops = incoming.get("human_ops") if isinstance(incoming.get("human_ops"), dict) else {}
    incoming_observe_model = (
        incoming_human_ops.get("observe_model") if isinstance(incoming_human_ops.get("observe_model"), dict) else {}
    )
    human_ops = next_config.setdefault("human_ops", {})
    observe_model = human_ops.setdefault("observe_model", {})
    _apply_optional_secret_update(observe_model, incoming_observe_model, existing_secret=current_observe_secret)

    incoming_vision = incoming.get("vision") if isinstance(incoming.get("vision"), dict) else {}
    incoming_analyzer = (
        incoming_vision.get("analyzer") if isinstance(incoming_vision.get("analyzer"), dict) else {}
    )
    vision = next_config.setdefault("vision", {})
    analyzer = vision.setdefault("analyzer", {})
    _apply_optional_secret_update(analyzer, incoming_analyzer, existing_secret=current_analyzer_secret)

    return normalize_private_config(
        next_config,
        config_path=config_path,
        defaults=defaults,
        allowed_keys=allowed_keys,
    )


def save_config(private_config: dict[str, Any], *, config_path: Path) -> None:
    config_path.write_text(
        json.dumps(private_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
