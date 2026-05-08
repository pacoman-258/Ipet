from __future__ import annotations

import copy
import os
import re
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .hermes import DEFAULT_HERMES_CONFIG, normalize_hermes_config


RUNTIME_HERMES = "hermes"
RUNTIME_ASTRBOT = "astrbot"
RUNTIME_IDS = {RUNTIME_HERMES, RUNTIME_ASTRBOT}

DEFAULT_ASTRBOT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "auto_start": False,
    "command": [],
    "cwd": "",
    "base_url": "http://127.0.0.1:6185",
    "health_path": "/",
    "startup_timeout_sec": 20,
    "api_key": "",
    "api_key_env": "ASTRBOT_API_KEY",
    "username": "ipet",
    "napcat_qq": {
        "enabled": False,
        "adapter_name": "aiocqhttp",
        "adapter_type": "OneBot v11",
        "reverse_ws_host": "127.0.0.1",
        "reverse_ws_port": 6199,
        "reverse_ws_url": "ws://127.0.0.1:6199/ws",
        "webui_url": "",
        "token_configured": False,
        "guide_url": "https://docs.astrbot.app/platform/aiocqhttp.html",
    },
}

DEFAULT_RUNTIME_CONFIG: dict[str, Any] = {
    "active": RUNTIME_HERMES,
    "adapters": {
        RUNTIME_HERMES: copy.deepcopy(DEFAULT_HERMES_CONFIG),
        RUNTIME_ASTRBOT: copy.deepcopy(DEFAULT_ASTRBOT_CONFIG),
    },
}


def normalize_runtime_id(value: Any) -> str:
    runtime_id = str(value or "").strip().lower()
    return runtime_id if runtime_id in RUNTIME_IDS else RUNTIME_HERMES


def normalize_command(value: Any) -> list[str]:
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return []


def _normalize_cwd(value: Any, *, root_dir: Path | None = None) -> str:
    cwd = str(value or "").strip()
    if cwd and root_dir is not None:
        path = Path(cwd)
        if not path.is_absolute():
            cwd = str((root_dir / path).resolve())
    return cwd


def _normalize_health_path(value: Any, default: str) -> str:
    text = str(value or default).strip() or default
    if not text.startswith("/"):
        text = f"/{text}"
    return text


def _normalize_timeout(value: Any, default: float = 20.0) -> float:
    try:
        timeout = float(value)
    except Exception:
        timeout = float(default)
    return max(0.5, timeout)


def _normalize_base_url(value: Any, default: str) -> str:
    text = str(value or default).strip() or default
    return text.rstrip("/") or default


def _normalize_napcat_qq_config(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    merged = {**DEFAULT_ASTRBOT_CONFIG["napcat_qq"], **data}
    try:
        port = int(merged.get("reverse_ws_port") or 6199)
    except Exception:
        port = 6199
    port = max(1, min(65535, port))
    host = str(merged.get("reverse_ws_host") or "127.0.0.1").strip() or "127.0.0.1"
    url = str(merged.get("reverse_ws_url") or "").strip()
    if not url:
        url = f"ws://{host}:{port}/ws"
    return {
        "enabled": bool(merged.get("enabled", False)),
        "adapter_name": str(merged.get("adapter_name") or "aiocqhttp").strip() or "aiocqhttp",
        "adapter_type": str(merged.get("adapter_type") or "OneBot v11").strip() or "OneBot v11",
        "reverse_ws_host": host,
        "reverse_ws_port": port,
        "reverse_ws_url": url,
        "webui_url": str(merged.get("webui_url") or "").strip(),
        "token_configured": bool(merged.get("token_configured", False)),
        "guide_url": str(merged.get("guide_url") or DEFAULT_ASTRBOT_CONFIG["napcat_qq"]["guide_url"]).strip(),
    }


def normalize_astrbot_config(raw: Any, *, root_dir: Path | None = None) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    merged = {**DEFAULT_ASTRBOT_CONFIG, **data}
    return {
        "enabled": bool(merged.get("enabled", False)),
        "auto_start": bool(merged.get("auto_start", False)),
        "command": normalize_command(merged.get("command")),
        "cwd": _normalize_cwd(merged.get("cwd"), root_dir=root_dir),
        "base_url": _normalize_base_url(merged.get("base_url"), DEFAULT_ASTRBOT_CONFIG["base_url"]),
        "health_path": _normalize_health_path(merged.get("health_path"), DEFAULT_ASTRBOT_CONFIG["health_path"]),
        "startup_timeout_sec": _normalize_timeout(
            merged.get("startup_timeout_sec"),
            float(DEFAULT_ASTRBOT_CONFIG["startup_timeout_sec"]),
        ),
        "api_key": str(merged.get("api_key") or "").strip(),
        "api_key_env": str(merged.get("api_key_env") or DEFAULT_ASTRBOT_CONFIG["api_key_env"]).strip()
        or DEFAULT_ASTRBOT_CONFIG["api_key_env"],
        "username": str(merged.get("username") or DEFAULT_ASTRBOT_CONFIG["username"]).strip()
        or DEFAULT_ASTRBOT_CONFIG["username"],
        "napcat_qq": _normalize_napcat_qq_config(merged.get("napcat_qq") or merged.get("napcat")),
    }


def normalize_runtime_config(raw: Any, *, root_dir: Path | None = None, legacy_hermes: Any = None) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    adapters = data.get("adapters") if isinstance(data.get("adapters"), dict) else {}
    hermes_raw = adapters.get(RUNTIME_HERMES)
    default_hermes = normalize_hermes_config(DEFAULT_HERMES_CONFIG, root_dir=root_dir)
    legacy_hermes_norm = normalize_hermes_config(legacy_hermes, root_dir=root_dir) if legacy_hermes is not None else default_hermes
    if legacy_hermes is not None and legacy_hermes_norm != default_hermes:
        hermes_raw = legacy_hermes
    elif hermes_raw is None:
        hermes_raw = legacy_hermes
    if hermes_raw is None:
        hermes_raw = DEFAULT_HERMES_CONFIG
    astrobot_raw = adapters.get(RUNTIME_ASTRBOT, data.get(RUNTIME_ASTRBOT))
    return {
        "active": normalize_runtime_id(data.get("active")),
        "adapters": {
            RUNTIME_HERMES: normalize_hermes_config(hermes_raw, root_dir=root_dir),
            RUNTIME_ASTRBOT: normalize_astrbot_config(astrobot_raw, root_dir=root_dir),
        },
    }


def apply_runtime_secret_actions(
    merged_config: dict[str, Any],
    raw_config: dict[str, Any],
    previous_config: dict[str, Any],
) -> None:
    raw_astrbot = (
        ((raw_config.get("runtime") or {}).get("adapters") or {}).get(RUNTIME_ASTRBOT)
        if isinstance(raw_config.get("runtime"), dict)
        else None
    )
    if not isinstance(raw_astrbot, dict):
        raw_astrbot = raw_config.get("astrbot") if isinstance(raw_config.get("astrbot"), dict) else {}
    action = str(raw_astrbot.get("api_key_action") or "").strip().lower()
    if action not in {"keep", "replace", "clear"}:
        return

    runtime = merged_config.setdefault("runtime", {})
    adapters = runtime.setdefault("adapters", {})
    astrobot = adapters.setdefault(RUNTIME_ASTRBOT, {})
    previous_key = (
        (((previous_config.get("runtime") or {}).get("adapters") or {}).get(RUNTIME_ASTRBOT) or {}).get("api_key")
        if isinstance(previous_config.get("runtime"), dict)
        else ""
    )
    if not previous_key and isinstance(previous_config.get("astrbot"), dict):
        previous_key = previous_config["astrbot"].get("api_key", "")

    if action == "keep":
        astrobot["api_key"] = str(previous_key or "").strip()
    elif action == "replace":
        astrobot["api_key"] = str(raw_astrbot.get("api_key") or "").strip()
    else:
        astrobot["api_key"] = ""
    astrobot.pop("api_key_action", None)


def resolved_astrbot_api_key(config: dict[str, Any]) -> str:
    key = str(config.get("api_key") or "").strip()
    if key:
        return key
    env_name = str(config.get("api_key_env") or "").strip()
    if env_name:
        return str(os.environ.get(env_name) or "").strip()
    return ""


def secret_preview(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def redact_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    public = copy.deepcopy(config if isinstance(config, dict) else {})
    adapters = public.get("adapters") if isinstance(public.get("adapters"), dict) else {}
    astrobot = adapters.get(RUNTIME_ASTRBOT) if isinstance(adapters.get(RUNTIME_ASTRBOT), dict) else None
    if astrobot is not None:
        raw_key = str(astrobot.get("api_key") or "").strip()
        env_key = ""
        env_name = str(astrobot.get("api_key_env") or "").strip()
        if env_name:
            env_key = str(os.environ.get(env_name) or "").strip()
        astrobot["api_key"] = ""
        astrobot["api_key_action"] = "keep"
        astrobot["api_key_set"] = bool(raw_key or env_key)
        astrobot["api_key_preview"] = secret_preview(raw_key or env_key)
        astrobot["api_key_source"] = "config" if raw_key else ("env" if env_key else "")
    return public


def mirror_runtime_compat(config: dict[str, Any], *, root_dir: Path | None = None) -> dict[str, Any]:
    runtime = normalize_runtime_config(
        config.get("runtime"),
        root_dir=root_dir,
        legacy_hermes=config.get("hermes"),
    )
    config["runtime"] = runtime
    config["hermes"] = copy.deepcopy(runtime["adapters"][RUNTIME_HERMES])
    return config


def runtime_sidecar_config(config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    active = normalize_runtime_id(runtime.get("active"))
    adapters = runtime.get("adapters") if isinstance(runtime.get("adapters"), dict) else {}
    adapter = adapters.get(active) if isinstance(adapters.get(active), dict) else {}
    return active, adapter


def local_service_health_url(adapter_config: dict[str, Any]) -> str:
    base_url = str(adapter_config.get("base_url") or "").strip().rstrip("/")
    health_path = str(adapter_config.get("health_path") or "/").strip()
    if not health_path.startswith("/"):
        health_path = f"/{health_path}"
    return f"{base_url}{health_path}"


def trust_env_for_base_url(base_url: str) -> bool:
    host = (urlparse(str(base_url or "")).hostname or "").lower()
    return host not in {"127.0.0.1", "localhost", "::1"}


def ephemeral_session_id(value: Any = "") -> str:
    text = str(value or "").strip() or "default"
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", text)[:96] or "default"
