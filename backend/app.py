from __future__ import annotations

import asyncio
import json
import mimetypes
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .agent_graph import AgentGraphRuntime, ApprovalDecision, GraphDependencies
from .agent_orchestrator import (
    classify_route,
    execute_tool_calls,
    decide_turn,
    stream_final_reply,
)
from .mcp_bridge import MCPBridge
from .mcp.third_party_manager import ThirdPartyMCPManager
from .models import (
    ChatApprovalRequest,
    ChatStreamRequest,
    MCPDeleteRequest,
    MCPServerCreateRequest,
    MCPToggleRequest,
    ModelListRequest,
    SkillDeleteRequest,
    SkillImportGitRequest,
    SkillImportLocalRequest,
    TTSRequest,
)
from .ollama_client import OLLAMA_BASE_URL, is_ollama_alive, list_models
from .skills import ResolvedSkillSet, SkillAwareToolBridge, SkillManager, SkillRuntime
from .tool_runtime import Tool, ToolRegistry, ToolResult
from .tooling.security import normalize_file_allowlist
from .tts import (
    DEFAULT_PROVIDER,
    cleanup_old_audio,
    list_supported_providers,
    synthesize_to_audio,
    tts_available,
)


app = FastAPI(title="Desktop Pet Backend", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "pet_config.json"
AUDIO_CACHE_DIR = ROOT_DIR / "backend" / "audio_cache"
SETTINGS_HTML_PATH = ROOT_DIR / "settings.html"
SETTINGS_CSS_PATH = ROOT_DIR / "settings.css"
SETTINGS_JS_PATH = ROOT_DIR / "settings.js"
SKILLS_BUILTIN_DIR = ROOT_DIR / "skills" / "builtin"
THIRD_PARTY_SKILLS_DIR = ROOT_DIR / "third_party_skills"
RUNTIME_COMMAND_PATH = ROOT_DIR / ".pet_runtime_command.json"
RUNTIME_COMMAND_RESPONSE_PATH = ROOT_DIR / ".pet_runtime_command.response.json"
RUNTIME_HOST_HEARTBEAT_PATH = ROOT_DIR / ".pet_runtime_host.heartbeat.json"
AGENT_GRAPH_CHECKPOINT_PATH = ROOT_DIR / "backend" / "agent_graph_state.pkl"
SESSION_STORE: dict[str, list[dict[str, str]]] = {}
PENDING_CHAT_TURNS: dict[str, dict[str, Any]] = {}
EXPR_OUTPUT_FORMAT = "ndjson_v1"
DISPLAY_TEXT_DELIMITER = "**"
DEFAULT_TOOL_TIMEOUT_SEC = 180
LEGACY_TOOL_TIMEOUT_SEC = 10
DEFAULT_BACKEND_URL = "http://127.0.0.1:8008"
RUNTIME_PICK_TIMEOUT_SEC = 180.0
RUNTIME_HOST_HEARTBEAT_MAX_AGE_SEC = 5.0
AUTOGEN_MODEL_SUFFIX = ".autogen.model3.json"
CHAT_MODE_REACT = "react"
CHAT_MODE_CHAT = "chat"
CHAT_MODE_SKILL = "skill"
CHAT_MODE_VALUES = {CHAT_MODE_REACT, CHAT_MODE_CHAT, CHAT_MODE_SKILL}
CHAT_MODE_TAVILY_TOOL_PREFIX = "tavily-mcp."
EXPR_PROTOCOL_PROMPT = (
    "Output must be strict NDJSON. One JSON object per line with no extra commentary. "
    "Only fields expr and text are allowed. expr is an expression name string (or empty), "
    "text is plain response content. Example: {\"expr\":\"happy\",\"text\":\"hello\"}"
)
DEFAULT_TOOLING = {
    "enabled": True,
    "mode": "mcp_local_phase2",
    "file_allowlist": [str(ROOT_DIR)],
    "network_allow_domains": [],
    "max_tool_calls_per_turn": 6,
    "tool_timeout_sec": DEFAULT_TOOL_TIMEOUT_SEC,
    "third_party": {
        "enabled": True,
        "servers": [],
    },
}
TTS_PRESETS = {
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
MCP_SERVER_PRESETS = {
    "playwright_mcp": {
        "label": "Playwright MCP",
        "name": "playwright_mcp",
        "config": {
            "mcpServers": {
                "playwright_mcp": {
                    "command": "npx",
                    "args": ["@playwright/mcp@latest"],
                }
            }
        },
        "notes": "Uses the official Playwright MCP pattern from the repository README.",
    }
}

_MCP_BRIDGE: MCPBridge | None = None
_MCP_CONFIG_SNAPSHOT = ""
_AGENT_GRAPH_RUNTIME: AgentGraphRuntime | None = None
_SKILL_MANAGER: SkillManager | None = None


def _settings_static_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(base))
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_full_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"chat": {"tooling": json.loads(json.dumps(DEFAULT_TOOLING))}}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"chat": {"tooling": json.loads(json.dumps(DEFAULT_TOOLING))}}
    return data if isinstance(data, dict) else {"chat": {"tooling": json.loads(json.dumps(DEFAULT_TOOLING))}}


def _save_full_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _canonicalize_model_source_path(model_path: Path) -> Path:
    original = model_path
    candidate = model_path
    while candidate.name.endswith(AUTOGEN_MODEL_SUFFIX):
        source_name = f"{candidate.name[: -len(AUTOGEN_MODEL_SUFFIX)]}.json"
        source_path = candidate.with_name(source_name)
        if source_path.exists():
            candidate = source_path
            continue
        return original
    return candidate


def _resolve_model_path(path_text: str) -> Path:
    text = str(path_text or "").strip()
    if not text:
        return ROOT_DIR
    model_path = Path(text)
    if not model_path.is_absolute():
        model_path = ROOT_DIR / model_path
    try:
        resolved = model_path.resolve()
    except Exception:
        resolved = model_path
    return _canonicalize_model_source_path(resolved)


def _find_default_model() -> str:
    preferred = ROOT_DIR / "model" / "hiyori_free_zh" / "runtime" / "hiyori_free_t08.model3.json"
    if preferred.exists():
        return preferred.relative_to(ROOT_DIR).as_posix()
    for candidate in (ROOT_DIR / "model").rglob("*.model3.json"):
        try:
            return candidate.relative_to(ROOT_DIR).as_posix()
        except ValueError:
            return str(candidate)
    return ""


def _default_settings_config() -> dict[str, Any]:
    return {
        "model_path": _find_default_model(),
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
            "backend_url": DEFAULT_BACKEND_URL,
            "llm_provider": "ollama",
            "api_base_url": "http://127.0.0.1:11434",
            "api_key": "",
            "model": "qwen3:8b",
            "router_enabled": False,
            "router_llm_provider": "ollama",
            "router_api_base_url": "http://127.0.0.1:11434",
            "router_api_key": "",
            "router_model": "qwen3:8b",
            "session_id": "default",
            "memory_window": 10,
            "voice": "zh-CN-XiaoxiaoNeural",
            "rate_pct": 0,
            "tts_provider": DEFAULT_PROVIDER,
            "tts_provider_url": "",
            "expression_mode": True,
            "expression_output_format": EXPR_OUTPUT_FORMAT,
            "react_enabled": True,
            "react_visibility": "inline",
            "max_reasoning_steps": 10,
            "tooling": json.loads(json.dumps(DEFAULT_TOOLING)),
            "skills": {
                "enabled": True,
                "default_active_ids": [],
            },
            "system_prompt": "",
        },
    }


def _normalize_model_path(path_text: str) -> str:
    text = str(path_text or "").strip()
    if not text:
        return ""
    resolved = _resolve_model_path(text)
    try:
        return resolved.relative_to(ROOT_DIR).as_posix()
    except Exception:
        try:
            return str(resolved)
        except Exception:
            return text


def _normalize_skill_ids(values: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    candidates = values if isinstance(values, (list, tuple)) else (values or [])
    for item in candidates:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _canonicalize_skill_ids(values: Any, *, manager: SkillManager | None = None) -> list[str]:
    normalized = _normalize_skill_ids(values)
    if not normalized:
        return []
    active_manager = manager or _get_skill_manager()
    return active_manager.canonicalize_skill_ids(normalized)


def _normalize_chat_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in CHAT_MODE_VALUES else CHAT_MODE_REACT


def _resolve_router_request_config(req: ChatStreamRequest, settings_config: dict[str, Any]) -> dict[str, Any]:
    chat_cfg = settings_config.get("chat", {}) if isinstance(settings_config, dict) else {}
    router_enabled = chat_cfg.get("router_enabled", False) if req.router_enabled is None else req.router_enabled
    main_provider = str(req.llm_provider or chat_cfg.get("llm_provider") or "ollama").strip() or "ollama"
    main_base_url = str(req.api_base_url or chat_cfg.get("api_base_url") or "").strip()
    main_api_key = str(req.api_key or chat_cfg.get("api_key") or "")
    main_model = str(req.model or chat_cfg.get("model") or "qwen3:8b").strip() or "qwen3:8b"
    provider = str(req.router_llm_provider or chat_cfg.get("router_llm_provider") or main_provider).strip() or main_provider
    base_url = str(req.router_api_base_url or chat_cfg.get("router_api_base_url") or main_base_url).strip()
    api_key = str(req.router_api_key or chat_cfg.get("router_api_key") or "")
    model = str(req.router_model or chat_cfg.get("router_model") or main_model).strip() or main_model
    return {
        "enabled": bool(router_enabled),
        "llm_provider": provider,
        "api_base_url": base_url,
        "api_key": api_key,
        "model": model,
    }


def _skill_summaries_for_route(
    *,
    chat_mode: str,
    requested_skill_ids: list[str],
    settings_config: dict[str, Any],
) -> list[dict[str, str]]:
    if chat_mode == CHAT_MODE_CHAT:
        return []
    manager = _get_skill_manager()
    available = {str(getattr(item, "skill_id", "")): item for item in manager.list_skills()}
    if chat_mode == CHAT_MODE_SKILL:
        candidate_ids = _canonicalize_skill_ids(requested_skill_ids, manager=manager)
        if not candidate_ids:
            candidate_ids = list(available.keys())
    else:
        candidate_ids = _canonicalize_skill_ids(requested_skill_ids, manager=manager)
        if not candidate_ids:
            candidate_ids = list(available.keys())
    items: list[dict[str, str]] = []
    for skill_id in candidate_ids:
        record = available.get(skill_id)
        if record is None:
            continue
        items.append(
            {
                "id": skill_id,
                "name": str(getattr(record, "display_name", "") or getattr(record, "name", "") or skill_id),
                "description": str(getattr(record, "short_description", "") or getattr(record, "description", "") or ""),
            }
        )
    return items


def _build_router_tool_schemas(chat_mode: str, active_skill_ids: list[str]) -> list[dict[str, Any]]:
    bridge = _build_runtime_tool_bridge({"chat_mode": chat_mode, "active_skill_ids": list(active_skill_ids)})
    if bridge is None or not hasattr(bridge, "list_tools"):
        return []
    try:
        return list(bridge.list_tools() or [])
    except Exception:
        return []


def _pick_tavily_tool_name(tools: list[dict[str, Any]]) -> str:
    candidates: list[str] = []
    for schema in tools:
        if not isinstance(schema, dict):
            continue
        function = schema.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if name.startswith(CHAT_MODE_TAVILY_TOOL_PREFIX):
            candidates.append(name)
    for suffix in (".tavily_search", ".search"):
        for name in candidates:
            if name.endswith(suffix):
                return name
    return candidates[0] if candidates else ""


def _route_guidance_message(tool_candidates: list[str]) -> str:
    names = [str(item or "").strip() for item in tool_candidates if str(item or "").strip()]
    if not names:
        return ""
    joined = ", ".join(dict.fromkeys(names))
    return (
        "Routing hint for this turn: prefer these tools first if they fit the request: "
        f"{joined}. Keep the full visible toolset available."
    )


class _RegistryToolBridge:
    def __init__(self, tools: list[Tool] | tuple[Tool, ...] | None = None, *, missing_message: str) -> None:
        self.registry = ToolRegistry()
        self.missing_message = str(missing_message or "tool not available")
        for tool in tools or []:
            self.registry.register(tool, overwrite=True)

    def list_registered_tools(self) -> list[Tool]:
        return self.registry.list()

    def list_tools(self) -> list[dict[str, Any]]:
        return self.registry.to_llm_schemas()

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            return self.registry.invoke(tool_name, arguments)
        except KeyError:
            return ToolResult.from_error(f"{self.missing_message}: {tool_name}")


class _FilteredToolBridge:
    def __init__(
        self,
        base_bridge: Any,
        *,
        allow_predicate,
        missing_message: str,
    ) -> None:
        self.base_bridge = base_bridge
        self.allow_predicate = allow_predicate
        self.missing_message = str(missing_message or "tool not available")

    def _tool_name_from_schema(self, schema: dict[str, Any]) -> str:
        if not isinstance(schema, dict):
            return ""
        function = schema.get("function")
        if not isinstance(function, dict):
            return ""
        return str(function.get("name") or "").strip()

    def _is_allowed(self, tool_name: str) -> bool:
        name = str(tool_name or "").strip()
        return bool(name and self.allow_predicate(name))

    def list_registered_tools(self) -> list[Tool]:
        if self.base_bridge is None or not hasattr(self.base_bridge, "list_registered_tools"):
            return []
        return [tool for tool in self.base_bridge.list_registered_tools() if self._is_allowed(getattr(tool, "name", ""))]

    def list_tools(self) -> list[dict[str, Any]]:
        if self.base_bridge is None:
            return []
        if hasattr(self.base_bridge, "list_registered_tools"):
            return [tool.to_llm_schema() for tool in self.list_registered_tools()]
        if hasattr(self.base_bridge, "list_tools"):
            return [schema for schema in self.base_bridge.list_tools() if self._is_allowed(self._tool_name_from_schema(schema))]
        return []

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        name = str(tool_name or "").strip()
        if not self._is_allowed(name):
            return ToolResult.from_error(f"{self.missing_message}: {name}")
        if self.base_bridge is None or not hasattr(self.base_bridge, "call_tool"):
            return ToolResult.from_error(f"{self.missing_message}: {name}")
        return self.base_bridge.call_tool(name, arguments)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base_bridge, name)


def _normalize_settings_config(config: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_merge(_default_settings_config(), config if isinstance(config, dict) else {})
    merged["model_path"] = _normalize_model_path(merged.get("model_path", ""))

    chat = merged.get("chat", {})
    if not isinstance(chat, dict):
        chat = _default_settings_config()["chat"]
        merged["chat"] = chat
    chat["backend_url"] = str(chat.get("backend_url") or DEFAULT_BACKEND_URL).strip() or DEFAULT_BACKEND_URL
    chat["llm_provider"] = str(chat.get("llm_provider") or "ollama").strip() or "ollama"
    chat["api_base_url"] = str(chat.get("api_base_url") or "").strip()
    chat["api_key"] = str(chat.get("api_key") or "")
    chat["model"] = str(chat.get("model") or "qwen3:8b").strip() or "qwen3:8b"
    chat["router_enabled"] = bool(chat.get("router_enabled", False))
    chat["router_llm_provider"] = str(chat.get("router_llm_provider") or chat["llm_provider"]).strip() or chat["llm_provider"]
    chat["router_api_base_url"] = str(chat.get("router_api_base_url") or chat["api_base_url"]).strip()
    chat["router_api_key"] = str(chat.get("router_api_key") or "")
    chat["router_model"] = str(chat.get("router_model") or chat["model"]).strip() or chat["model"]
    chat["session_id"] = str(chat.get("session_id") or "default").strip() or "default"
    try:
        chat["memory_window"] = max(1, min(50, int(chat.get("memory_window", 10))))
    except Exception:
        chat["memory_window"] = 10
    chat["voice"] = str(chat.get("voice") or "zh-CN-XiaoxiaoNeural").strip() or "zh-CN-XiaoxiaoNeural"
    try:
        chat["rate_pct"] = max(-50, min(100, int(chat.get("rate_pct", 0))))
    except Exception:
        chat["rate_pct"] = 0
    provider = str(chat.get("tts_provider") or DEFAULT_PROVIDER).strip() or DEFAULT_PROVIDER
    chat["tts_provider"] = provider if provider in list_supported_providers() else DEFAULT_PROVIDER
    chat["tts_provider_url"] = str(chat.get("tts_provider_url") or "").strip()
    chat["expression_mode"] = bool(chat.get("expression_mode", True))
    chat["expression_output_format"] = str(chat.get("expression_output_format") or EXPR_OUTPUT_FORMAT).strip() or EXPR_OUTPUT_FORMAT
    chat["react_enabled"] = bool(chat.get("react_enabled", True))
    chat["react_visibility"] = "inline"
    try:
        chat["max_reasoning_steps"] = max(1, min(12, int(chat.get("max_reasoning_steps", 10))))
    except Exception:
        chat["max_reasoning_steps"] = 10
    chat["system_prompt"] = str(chat.get("system_prompt") or "")
    skills_cfg = chat.get("skills", {})
    if not isinstance(skills_cfg, dict):
        skills_cfg = {}
    skills_cfg["enabled"] = bool(skills_cfg.get("enabled", True))
    skills_cfg["default_active_ids"] = _canonicalize_skill_ids(skills_cfg.get("default_active_ids"))
    chat["skills"] = skills_cfg

    window = merged.get("window", {})
    if not isinstance(window, dict):
        window = _default_settings_config()["window"]
        merged["window"] = window
    for key, fallback in {"x": 120, "y": 80, "width": 420, "height": 640}.items():
        try:
            window[key] = int(window.get(key, fallback))
        except Exception:
            window[key] = fallback
    window["width"] = max(120, window["width"])
    window["height"] = max(120, window["height"])
    window["locked"] = bool(window.get("locked", False))

    pet = merged.get("pet", {})
    if not isinstance(pet, dict):
        pet = _default_settings_config()["pet"]
        merged["pet"] = pet
    for key, fallback in {"scale": 0.3, "rotation": 0.0, "opacity": 1.0}.items():
        try:
            pet[key] = float(pet.get(key, fallback))
        except Exception:
            pet[key] = fallback
    pet["scale"] = max(0.05, min(5.0, pet["scale"]))
    pet["opacity"] = max(0.1, min(1.0, pet["opacity"]))
    for key, fallback in {"offset_x": 0, "offset_y": 40}.items():
        try:
            pet[key] = int(pet.get(key, fallback))
        except Exception:
            pet[key] = fallback
    pet["edit_mode"] = bool(pet.get("edit_mode", False))
    pet["follow_mouse"] = bool(pet.get("follow_mouse", True))
    pet["background_enabled"] = bool(pet.get("background_enabled", False))
    pet["background_image"] = str(pet.get("background_image") or "").strip()
    try:
        pet["background_overlay_opacity"] = float(pet.get("background_overlay_opacity", 0.42))
    except Exception:
        pet["background_overlay_opacity"] = 0.42
    pet["background_overlay_opacity"] = max(0.0, min(0.9, pet["background_overlay_opacity"]))

    tooling = chat.get("tooling", {})
    if not isinstance(tooling, dict):
        tooling = json.loads(json.dumps(DEFAULT_TOOLING))
        chat["tooling"] = tooling
    tooling = _deep_merge(DEFAULT_TOOLING, tooling)
    tooling["enabled"] = bool(tooling.get("enabled", True))
    tooling["mode"] = "mcp_local_phase2"
    allowlist = normalize_file_allowlist(tooling.get("file_allowlist"), default_paths=[str(ROOT_DIR)])
    tooling["file_allowlist"] = [str(item) for item in allowlist]
    domains = tooling.get("network_allow_domains") or []
    tooling["network_allow_domains"] = [str(item).strip() for item in domains if str(item).strip()]
    try:
        tooling["max_tool_calls_per_turn"] = max(1, int(tooling.get("max_tool_calls_per_turn", 6)))
    except Exception:
        tooling["max_tool_calls_per_turn"] = 6
    try:
        tooling["tool_timeout_sec"] = int(tooling.get("tool_timeout_sec", DEFAULT_TOOL_TIMEOUT_SEC))
    except Exception:
        tooling["tool_timeout_sec"] = DEFAULT_TOOL_TIMEOUT_SEC
    _migrate_tool_timeout(tooling)
    third_party = tooling.get("third_party", {})
    if not isinstance(third_party, dict):
        third_party = {"enabled": True, "servers": []}
    third_party["enabled"] = bool(third_party.get("enabled", True))
    servers = third_party.get("servers", [])
    third_party["servers"] = servers if isinstance(servers, list) else []
    tooling["third_party"] = third_party
    chat["tooling"] = tooling
    return merged


def _load_settings_config() -> dict[str, Any]:
    return _normalize_settings_config(_load_full_config())


def _extract_motion_groups(model_json: dict[str, Any]) -> dict[str, Any]:
    groups = model_json.get("FileReferences", {}).get("Motions")
    if isinstance(groups, dict):
        return groups
    groups = model_json.get("Motions", {})
    return groups if isinstance(groups, dict) else {}


def _infer_motion_group(file_name: str) -> str:
    name = file_name[:-13] if file_name.lower().endswith(".motion3.json") else file_name
    token = next((part for part in name.replace("-", "_").split("_") if part), "")
    if not token:
        return "Auto"
    mapping = {"idle": "Idle", "tap": "Tap", "flick": "Flick"}
    return mapping.get(token.lower(), token[:1].upper() + token[1:])


def _scan_motion_groups(model_path: Path) -> dict[str, list[dict[str, str]]]:
    model_dir = model_path.parent
    groups: dict[str, list[dict[str, str]]] = {}
    for file_path in sorted(model_dir.rglob("*.motion3.json")):
        try:
            rel = file_path.relative_to(model_dir).as_posix()
        except ValueError:
            rel = file_path.name
        group = _infer_motion_group(file_path.name)
        groups.setdefault(group, []).append({"File": rel})
    return groups


def _extract_expression_defs(model_json: dict[str, Any]) -> list[dict[str, Any]]:
    exprs = model_json.get("FileReferences", {}).get("Expressions")
    return exprs if isinstance(exprs, list) else []


def _scan_expression_defs(model_path: Path) -> list[dict[str, str]]:
    model_dir = model_path.parent
    defs: list[dict[str, str]] = []
    for file_path in sorted(model_dir.rglob("*.exp3.json")):
        try:
            rel = file_path.relative_to(model_dir).as_posix()
        except ValueError:
            rel = file_path.name
        name = file_path.name[:-10] if file_path.name.lower().endswith(".exp3.json") else file_path.stem
        defs.append({"Name": name, "File": rel})
    return defs


def _model_metadata(path_text: str) -> dict[str, Any]:
    model_path = _resolve_model_path(path_text)
    metadata = {
        "motions": [],
        "expressions": [],
        "motion_actions": [],
        "expression_actions": [],
    }
    if not model_path.exists():
        return metadata
    try:
        model_json = json.loads(model_path.read_text(encoding="utf-8"))
    except Exception:
        model_json = {}
    motion_groups = _extract_motion_groups(model_json) or _scan_motion_groups(model_path)
    expr_defs = _extract_expression_defs(model_json) or _scan_expression_defs(model_path)
    motion_labels: list[str] = []
    motion_actions: list[dict[str, Any]] = []
    for group_name, entries in motion_groups.items():
        if isinstance(entries, list):
            for idx, _ in enumerate(entries):
                label = f"{group_name}[{idx}]"
                motion_labels.append(label)
                motion_actions.append({"group": str(group_name), "index": idx, "label": label})
    expr_labels = [str(item.get("Name") or "").strip() for item in expr_defs if isinstance(item, dict)]
    metadata["motions"] = [item for item in motion_labels if item]
    metadata["expressions"] = [item for item in expr_labels if item]
    metadata["motion_actions"] = motion_actions
    metadata["expression_actions"] = [{"name": item, "label": item} for item in metadata["expressions"]]
    return metadata


def _list_local_models() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    model_root = ROOT_DIR / "model"
    if not model_root.exists():
        return items
    seen: set[str] = set()
    for path in sorted(model_root.rglob("*.model3.json")):
        if path.name.endswith(".autogen.model3.json"):
            continue
        try:
            rel = path.relative_to(ROOT_DIR).as_posix()
        except ValueError:
            rel = str(path)
        if rel in seen:
            continue
        seen.add(rel)
        meta = _model_metadata(rel)
        items.append(
            {
                "path": rel,
                "label": f"{path.parent.name} / {path.name}",
                "motions": meta["motions"],
                "expressions": meta["expressions"],
                "motion_actions": meta["motion_actions"],
                "expression_actions": meta["expression_actions"],
            }
        )
    return items


def _write_runtime_command(command_type: str, payload: dict[str, Any]) -> None:
    command = {
        "nonce": str(time.time_ns()),
        "type": command_type,
        "payload": payload,
    }
    RUNTIME_COMMAND_PATH.write_text(json.dumps(command, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_runtime_command_with_response(
    command_type: str,
    payload: dict[str, Any],
    *,
    response_path: Path,
) -> dict[str, Any]:
    command = {
        "nonce": str(time.time_ns()),
        "type": command_type,
        "payload": {
            **(payload if isinstance(payload, dict) else {}),
            "response_path": str(response_path),
        },
    }
    RUNTIME_COMMAND_PATH.write_text(json.dumps(command, ensure_ascii=False, indent=2), encoding="utf-8")
    return command


def _wait_for_runtime_command_response(
    *,
    nonce: str,
    response_path: Path,
    timeout_sec: float,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + max(0.1, float(timeout_sec))
    while time.monotonic() < deadline:
        try:
            if response_path.exists():
                raw = json.loads(response_path.read_text(encoding="utf-8"))
            else:
                raw = None
        except Exception:
            raw = None
        if isinstance(raw, dict) and str(raw.get("nonce") or "").strip() == nonce:
            return raw
        time.sleep(0.1)
    return None


def _runtime_host_is_online(max_age_sec: float = RUNTIME_HOST_HEARTBEAT_MAX_AGE_SEC) -> bool:
    try:
        if not RUNTIME_HOST_HEARTBEAT_PATH.exists():
            return False
        stat = RUNTIME_HOST_HEARTBEAT_PATH.stat()
    except Exception:
        return False
    age_sec = time.time() - float(stat.st_mtime)
    return age_sec <= max(1.0, float(max_age_sec))


def _load_runtime_tooling_config() -> dict[str, Any]:
    data = _load_full_config()
    chat = data.get("chat", {}) if isinstance(data, dict) else {}
    tooling = chat.get("tooling", {}) if isinstance(chat, dict) else {}
    merged = _deep_merge(DEFAULT_TOOLING, tooling if isinstance(tooling, dict) else {})
    merged["mode"] = "mcp_local_phase2"
    merged["file_allowlist"] = [str(item) for item in normalize_file_allowlist(merged.get("file_allowlist"), default_paths=[str(ROOT_DIR)])]
    third = merged.get("third_party", {})
    if not isinstance(third, dict):
        merged["third_party"] = {"enabled": True, "servers": []}
    else:
        third.setdefault("enabled", True)
        third.setdefault("servers", [])
    _migrate_tool_timeout(merged)
    return merged


def _migrate_tool_timeout(tooling_cfg: dict[str, Any]) -> None:
    try:
        timeout_sec = int(tooling_cfg.get("tool_timeout_sec", DEFAULT_TOOL_TIMEOUT_SEC))
    except Exception:
        timeout_sec = DEFAULT_TOOL_TIMEOUT_SEC
    if timeout_sec == LEGACY_TOOL_TIMEOUT_SEC:
        tooling_cfg["tool_timeout_sec"] = DEFAULT_TOOL_TIMEOUT_SEC


def _tooling_snapshot(tooling_cfg: dict[str, Any]) -> str:
    return json.dumps(tooling_cfg, ensure_ascii=False, sort_keys=True)


def _get_mcp_bridge(force_reload: bool = False) -> MCPBridge:
    global _MCP_BRIDGE, _MCP_CONFIG_SNAPSHOT
    tooling_cfg = _load_runtime_tooling_config()
    snapshot = _tooling_snapshot(tooling_cfg)
    if force_reload or _MCP_BRIDGE is None or snapshot != _MCP_CONFIG_SNAPSHOT:
        if _MCP_BRIDGE is not None:
            try:
                _MCP_BRIDGE.stop()
            except Exception:
                pass
        _MCP_BRIDGE = MCPBridge(tooling_cfg, ROOT_DIR)
        _MCP_CONFIG_SNAPSHOT = snapshot
    return _MCP_BRIDGE


def _get_skill_manager(force_reload: bool = False) -> SkillManager:
    global _SKILL_MANAGER
    if force_reload or _SKILL_MANAGER is None:
        _SKILL_MANAGER = SkillManager(ROOT_DIR, builtin_dir=SKILLS_BUILTIN_DIR, imported_dir=THIRD_PARTY_SKILLS_DIR)
    return _SKILL_MANAGER


def _get_skill_runtime(force_reload: bool = False) -> SkillRuntime:
    return SkillRuntime(_get_skill_manager(force_reload=force_reload))


def _resolve_request_skills(
    req_skill_ids: list[str] | None = None,
    settings: dict[str, Any] | None = None,
    *,
    chat_mode: str = CHAT_MODE_REACT,
) -> ResolvedSkillSet:
    if _normalize_chat_mode(chat_mode) == CHAT_MODE_CHAT:
        return ResolvedSkillSet(defaulted=not _normalize_skill_ids(req_skill_ids or []))
    config = settings or _load_settings_config()
    chat_cfg = config.get("chat", {}) if isinstance(config, dict) else {}
    skills_cfg = chat_cfg.get("skills", {}) if isinstance(chat_cfg, dict) else {}
    enabled = bool(skills_cfg.get("enabled", True))
    manager = _get_skill_manager()
    default_active_ids = _canonicalize_skill_ids(skills_cfg.get("default_active_ids"), manager=manager)
    requested_ids = _canonicalize_skill_ids(req_skill_ids or [], manager=manager)
    runtime = _get_skill_runtime()
    return runtime.resolve_active_skills(
        requested_ids,
        default_active_ids=default_active_ids,
        enabled=enabled,
    )


def _build_runtime_tool_bridge(state: dict[str, Any]) -> Any:
    chat_mode = _normalize_chat_mode(state.get("chat_mode"))
    if chat_mode == CHAT_MODE_CHAT:
        return _FilteredToolBridge(
            _get_mcp_bridge(),
            allow_predicate=lambda name: str(name or "").startswith(CHAT_MODE_TAVILY_TOOL_PREFIX),
            missing_message="tool not available in 聊天模式",
        )

    skill_ids = _normalize_skill_ids(state.get("active_skill_ids"))
    if chat_mode == CHAT_MODE_SKILL:
        settings = _load_settings_config()
        resolved = _resolve_request_skills(skill_ids, settings=settings, chat_mode=chat_mode)
        return _RegistryToolBridge(
            list(resolved.resource_tools) + list(resolved.adapter_tools) + list(resolved.script_tools),
            missing_message="tool not available in Skill模式",
        )

    if not skill_ids:
        return _get_mcp_bridge()
    settings = _load_settings_config()
    resolved = _resolve_request_skills(skill_ids, settings=settings, chat_mode=chat_mode)
    base_bridge = _get_mcp_bridge()
    if not resolved.tool_allowlist and not resolved.resource_tools and not resolved.adapter_tools and not resolved.script_tools:
        return base_bridge
    return _get_skill_runtime().build_tool_bridge(base_bridge, resolved)


def _agent_graph_dependencies() -> GraphDependencies:
    return GraphDependencies(
        decide_turn=decide_turn,
        execute_tool_calls=execute_tool_calls,
        get_mcp_bridge=_get_mcp_bridge,
        load_tooling_config=_load_runtime_tooling_config,
        build_tool_bridge=_build_runtime_tool_bridge,
    )


def _get_agent_graph_runtime(force_reload: bool = False) -> AgentGraphRuntime:
    global _AGENT_GRAPH_RUNTIME
    if force_reload or _AGENT_GRAPH_RUNTIME is None:
        _AGENT_GRAPH_RUNTIME = AgentGraphRuntime(
            dependency_provider=_agent_graph_dependencies,
            checkpoint_path=AGENT_GRAPH_CHECKPOINT_PATH,
        )
    return _AGENT_GRAPH_RUNTIME


def _reset_agent_graph_runtime() -> None:
    global _AGENT_GRAPH_RUNTIME, _SKILL_MANAGER
    _AGENT_GRAPH_RUNTIME = None
    _SKILL_MANAGER = None
    PENDING_CHAT_TURNS.clear()
    try:
        AGENT_GRAPH_CHECKPOINT_PATH.unlink()
    except FileNotFoundError:
        pass


def _has_chat_mode_tavily_tools() -> bool:
    bridge = _FilteredToolBridge(
        _get_mcp_bridge(),
        allow_predicate=lambda name: str(name or "").startswith(CHAT_MODE_TAVILY_TOOL_PREFIX),
        missing_message="tool not available in 聊天模式",
    )
    return bool(bridge.list_tools())


def _iter_registered_mcp_server_configs() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tooling_cfg = _load_runtime_tooling_config()
    third_cfg = tooling_cfg.get("third_party", {})
    if not isinstance(third_cfg, dict):
        third_cfg = {"enabled": False, "servers": []}
    configured = third_cfg.get("servers", [])
    manager = ThirdPartyMCPManager(ROOT_DIR)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(configured, list):
        for item in configured:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            manifest_path = str(item.get("manifest_path") or "").strip()
            if not name or not manifest_path:
                continue
            out.append(dict(item))
            seen.add(name)
    for manifest in manager.list_manifests():
        name = str(manifest.get("name") or "").strip()
        if not name or name in seen:
            continue
        out.append(
            {
                "name": name,
                "enabled": bool(manifest.get("enabled", False)),
                "source_type": "local",
                "source": str(manifest.get("server_dir", "")),
                "manifest_path": str(manifest.get("manifest_path", "")),
                "runtime": str(manifest.get("runtime", "")),
            }
        )
    return third_cfg, out


def _lightweight_mcp_server_list() -> list[dict[str, Any]]:
    third_cfg, server_cfgs = _iter_registered_mcp_server_configs()
    third_enabled = bool(third_cfg.get("enabled", False))
    cached_status = {}
    global _MCP_BRIDGE
    if _MCP_BRIDGE is not None:
        cached_status = {
            str(name): dict(status)
            for name, status in getattr(_MCP_BRIDGE, "third_party_status", {}).items()
            if isinstance(status, dict)
        }

    manager = ThirdPartyMCPManager(ROOT_DIR)
    servers: list[dict[str, Any]] = []
    for server_cfg in server_cfgs:
        name = str(server_cfg.get("name") or "").strip()
        if not name:
            continue
        if name in cached_status:
            cached = dict(cached_status[name])
            cached.setdefault("name", name)
            servers.append(cached)
            continue

        status = {
            "name": name,
            "enabled": bool(server_cfg.get("enabled", True)),
            "version": "",
            "runtime": str(server_cfg.get("runtime", "")),
            "source_type": str(server_cfg.get("source_type", "local")),
            "source": str(server_cfg.get("source", "")),
            "manifest_path": str(server_cfg.get("manifest_path", "")),
            "install_status": "ready" if str(server_cfg.get("runtime", "")).strip() else "unknown",
            "health_status": "disabled" if (not third_enabled or not bool(server_cfg.get("enabled", True))) else "not_loaded",
            "tools": [],
            "error": "",
        }
        try:
            manifest = manager.load_manifest(status["manifest_path"])
            status["version"] = str(manifest.get("version", ""))
            status["runtime"] = str(manifest.get("runtime", status["runtime"]))
            status["enabled"] = bool(manifest.get("enabled", status["enabled"]))
            status["manifest_path"] = str(manifest.get("manifest_path", status["manifest_path"]))
            if not third_enabled or not status["enabled"]:
                status["health_status"] = "disabled"
            else:
                install_type = str(manifest.get("install", {}).get("type") or "").strip()
                status["install_status"] = "ready" if install_type else status["install_status"]
                status["health_status"] = "not_loaded"
        except Exception as exc:
            status["install_status"] = "failed"
            status["health_status"] = "failed"
            status["error"] = str(exc)
        servers.append(status)
    return servers


def _update_third_party_config(entry: dict[str, Any]) -> None:
    config = _load_full_config()
    chat = config.setdefault("chat", {})
    tooling = chat.setdefault("tooling", json.loads(json.dumps(DEFAULT_TOOLING)))
    if not isinstance(tooling, dict):
        tooling = json.loads(json.dumps(DEFAULT_TOOLING))
        chat["tooling"] = tooling
    third = tooling.setdefault("third_party", {"enabled": True, "servers": []})
    if not isinstance(third, dict):
        third = {"enabled": True, "servers": []}
        tooling["third_party"] = third
    servers = third.setdefault("servers", [])
    if not isinstance(servers, list):
        servers = []
        third["servers"] = servers

    target_name = str(entry.get("name") or "").strip()
    replaced = False
    for idx, item in enumerate(servers):
        if isinstance(item, dict) and str(item.get("name") or "").strip() == target_name:
            servers[idx] = entry
            replaced = True
            break
    if not replaced:
        servers.append(entry)
    _save_full_config(config)


def _set_third_party_enabled(name: str, enabled: bool) -> None:
    config = _load_full_config()
    chat = config.setdefault("chat", {})
    tooling = chat.setdefault("tooling", json.loads(json.dumps(DEFAULT_TOOLING)))
    third = tooling.setdefault("third_party", {"enabled": True, "servers": []})
    servers = third.setdefault("servers", [])
    for item in servers:
        if isinstance(item, dict) and str(item.get("name") or "").strip() == str(name or "").strip():
            item["enabled"] = bool(enabled)
    _save_full_config(config)


def _remove_third_party_config(name: str) -> None:
    config = _load_full_config()
    chat = config.setdefault("chat", {})
    tooling = chat.setdefault("tooling", json.loads(json.dumps(DEFAULT_TOOLING)))
    third = tooling.setdefault("third_party", {"enabled": True, "servers": []})
    servers = third.setdefault("servers", [])
    target_name = str(name or "").strip()
    third["servers"] = [
        item
        for item in servers
        if not (isinstance(item, dict) and str(item.get("name") or "").strip() == target_name)
    ]
    _save_full_config(config)


def _parse_mcp_server_config_json(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("mcp server config cannot be empty")
    try:
        data = json.loads(text)
    except Exception as exc:
        raise ValueError(f"invalid mcp server json: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("mcp server json must be an object")
    return data


def _infer_mcp_server_name(config_payload: dict[str, Any], preferred_name: str = "") -> str:
    explicit = str(preferred_name or "").strip()
    if explicit:
        return explicit
    if isinstance(config_payload, dict) and isinstance(config_payload.get("mcpServers"), dict):
        servers = config_payload.get("mcpServers") or {}
        if len(servers) == 1:
            return str(next(iter(servers.keys())) or "").strip()
    if isinstance(config_payload, dict):
        command = str(config_payload.get("command") or "").strip()
        if command:
            return Path(command).name
    return ""


def _ensure_mcp_server_from_draft(name: str, config_json: str) -> dict[str, Any] | None:
    raw_json = str(config_json or "").strip()
    if not raw_json:
        return None
    config_payload = _parse_mcp_server_config_json(raw_json)
    inferred_name = _infer_mcp_server_name(config_payload, name)
    _third_cfg, registered_servers = _iter_registered_mcp_server_configs()
    existing = next(
        (
            dict(item)
            for item in registered_servers
            if isinstance(item, dict) and str(item.get("name") or "").strip() == inferred_name
        ),
        None,
    )
    if existing:
        return existing
    manager = ThirdPartyMCPManager(ROOT_DIR)
    manifest_path = manager.register_server_config(config_payload, inferred_name)
    try:
        tooling_cfg = _load_runtime_tooling_config()
        timeout_sec = int(tooling_cfg.get("tool_timeout_sec", DEFAULT_TOOL_TIMEOUT_SEC))
        manager.prepare_server(manifest_path, timeout_sec=timeout_sec)
        manifest = manager.load_manifest(manifest_path)
    except Exception as exc:
        try:
            manager.delete_server(manifest_path)
        except Exception:
            pass
        raise RuntimeError(f"mcp registration failed during initial package download or initialization: {exc}") from exc
    entry = {
        "name": manifest["name"],
        "enabled": True,
        "source_type": "config",
        "source": "frontend_config",
        "manifest_path": manifest["manifest_path"],
        "runtime": manifest["runtime"],
    }
    _update_third_party_config(entry)
    return entry


def _trim_messages(messages: list[dict[str, str]], memory_window: int) -> list[dict[str, str]]:
    limit = max(1, memory_window) * 2
    if len(messages) <= limit:
        return messages
    return messages[-limit:]


def _extract_pet_display_name(system_prompt: str) -> str:
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


def _phase_payload(phase: str, text: str, speaker: str = "pet") -> dict[str, Any]:
    return {
        "phase": str(phase or "").strip(),
        "text": _sanitize_ai_text(str(text or "")).strip(),
        "speaker": str(speaker or "pet"),
    }


def _store_pending_turn(turn_id: str, payload: dict[str, Any]) -> None:
    entry = dict(payload)
    entry["created_at"] = time.time()
    PENDING_CHAT_TURNS[turn_id] = entry


def _pop_pending_turn(turn_id: str) -> dict[str, Any] | None:
    return PENDING_CHAT_TURNS.pop(str(turn_id or "").strip(), None)


def _purge_stale_pending_turns(max_age_sec: int = 1800) -> None:
    now = time.time()
    stale = [
        key
        for key, value in PENDING_CHAT_TURNS.items()
        if now - float(value.get("created_at", now)) > max_age_sec
    ]
    for key in stale:
        PENDING_CHAT_TURNS.pop(key, None)


def _tool_call_signature(name: str, arguments: dict[str, Any] | None) -> str:
    try:
        args_text = json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True)
    except Exception:
        args_text = json.dumps(str(arguments or {}), ensure_ascii=False)
    return f"{str(name or '').strip()}:{args_text}"


def _filter_repeated_tool_calls(
    tool_calls: list[Any],
    seen_signatures: set[str],
) -> list[Any]:
    filtered: list[Any] = []
    local_seen: set[str] = set()
    for item in tool_calls:
        name = str(getattr(item, "name", "") or "").strip()
        arguments = getattr(item, "arguments", {}) or {}
        if not name:
            continue
        signature = _tool_call_signature(name, arguments)
        if signature in seen_signatures or signature in local_seen:
            continue
        local_seen.add(signature)
        filtered.append(item)
    return filtered


def _continuation_recheck_prompt(user_text: str, remaining_steps: int) -> str:
    clean_text = _sanitize_ai_text(str(user_text or "")).strip()
    lines = [
        "Re-check whether the user's original request is truly finished.",
        "Do not say the task is complete if any requested step is still unfinished.",
        "If the user asked for a sequence of actions, and only part of the sequence has been completed, you must request the next tool step.",
        f"Remaining approved tool steps available: {max(0, int(remaining_steps))}.",
        "Return JSON only in the normal decision format.",
    ]
    if clean_text:
        lines.append(f"Original user request: {clean_text}")
    return "\n".join(lines)


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sanitize_ai_text(text: str) -> str:
    return (text or "").replace("*", "").replace("#", "")


def _build_expression_protocol_prompt(available_expressions: list[str] | None = None) -> str:
    allowed = [str(item).strip() for item in (available_expressions or []) if str(item).strip()]
    if allowed:
        expr_list = ", ".join(json.dumps(item, ensure_ascii=False) for item in allowed)
        return (
            f"{EXPR_PROTOCOL_PROMPT} "
            f"Available expr values are: {expr_list}. "
            'If none fits, use "" for expr.'
        )
    return f'{EXPR_PROTOCOL_PROMPT} If no matching expression exists, use "" for expr.'


def _build_system_prompt(
    user_prompt: str,
    skill_prompt: str,
    expression_mode: bool,
    output_format: str,
    available_expressions: list[str] | None = None,
) -> str:
    parts = [str(user_prompt or "").strip(), str(skill_prompt or "").strip()]
    base = "\n\n".join([item for item in parts if item])
    if not expression_mode or output_format != EXPR_OUTPUT_FORMAT:
        return base
    protocol_prompt = _build_expression_protocol_prompt(available_expressions)
    return f"{base}\n\n{protocol_prompt}" if base else protocol_prompt


def _parse_ndjson_line(line: str) -> dict[str, str | None] | None:
    raw = (line or "").strip()
    if not raw or raw.startswith("```"):
        return None
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("ndjson item is not an object")
        text = _sanitize_ai_text(str(obj.get("text") or "")).strip()
        expr = obj.get("expr")
        expr_name = str(expr).strip() if isinstance(expr, str) else None
        if not text:
            return None
        return {"text": text, "expr": expr_name or None}
    except Exception:
        fallback = _sanitize_ai_text(raw).strip()
        return {"text": fallback, "expr": None} if fallback else None


def _append_display_line(current_text: str, raw_line: str) -> tuple[str, str | None]:
    cleaned = _sanitize_ai_text((raw_line or "").rstrip("\r"))
    if cleaned.strip():
        chunk = f"\n{cleaned}" if current_text else cleaned
        return current_text + chunk, chunk
    if current_text and not current_text.endswith("\n"):
        return current_text + "\n", "\n"
    return current_text, None


def _split_display_delimiter(raw_line: str) -> tuple[bool, str]:
    source = (raw_line or "").rstrip("\r")
    stripped = source.lstrip()
    if not stripped.startswith(DISPLAY_TEXT_DELIMITER):
        return False, source
    remainder = stripped[len(DISPLAY_TEXT_DELIMITER) :].lstrip()
    return True, remainder


async def _iter_ndjson_segments(delta_stream):
    buffer = ""
    async for delta in delta_stream:
        if not delta:
            continue
        buffer += delta
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            segment = _parse_ndjson_line(line)
            if segment is not None:
                yield segment
    if buffer.strip():
        segment = _parse_ndjson_line(buffer)
        if segment is not None:
            yield segment


def _skills_response_payload() -> dict[str, Any]:
    settings = _load_settings_config()
    chat_cfg = settings.get("chat", {}) if isinstance(settings, dict) else {}
    skills_cfg = chat_cfg.get("skills", {}) if isinstance(chat_cfg, dict) else {}
    manager = _get_skill_manager()
    default_active_ids = _canonicalize_skill_ids(skills_cfg.get("default_active_ids"), manager=manager)
    enabled = bool(skills_cfg.get("enabled", True))
    records = manager.list_skills()
    return {
        "ok": True,
        "enabled": enabled,
        "default_active_ids": default_active_ids,
        "skills": [item.to_summary(default_active=item.skill_id in default_active_ids) for item in records],
        "config": settings,
    }


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _AGENT_GRAPH_RUNTIME, _MCP_BRIDGE, _SKILL_MANAGER
    if _MCP_BRIDGE is not None:
        try:
            _MCP_BRIDGE.stop()
        except Exception:
            pass
        _MCP_BRIDGE = None
    _AGENT_GRAPH_RUNTIME = None
    _SKILL_MANAGER = None


@app.get("/api/health")
async def health() -> dict[str, Any]:
    ollama_ok = await is_ollama_alive(OLLAMA_BASE_URL)
    tooling_cfg = _load_runtime_tooling_config()
    third_enabled = bool(tooling_cfg.get("third_party", {}).get("enabled", False))
    third_party_health: dict[str, Any] = {"enabled": third_enabled, "servers": [], "online": 0}
    global _MCP_BRIDGE
    if _MCP_BRIDGE is not None:
        try:
            third_party_health = _MCP_BRIDGE.health()
        except Exception as exc:
            third_party_health = {
                "enabled": third_enabled,
                "servers": [],
                "online": 0,
                "error": str(exc),
            }
    return {
        "ok": True,
        "ollama": ollama_ok,
        "tts": tts_available(DEFAULT_PROVIDER),
        "tools": bool(tooling_cfg.get("enabled", True)),
        "third_party_mcp": third_party_health,
    }


@app.get("/settings")
async def settings_page() -> FileResponse:
    return FileResponse(
        SETTINGS_HTML_PATH,
        media_type="text/html; charset=utf-8",
        headers=_settings_static_headers(),
    )


@app.get("/settings.css")
async def settings_css() -> FileResponse:
    return FileResponse(
        SETTINGS_CSS_PATH,
        media_type="text/css; charset=utf-8",
        headers=_settings_static_headers(),
    )


@app.get("/settings.js")
async def settings_js() -> FileResponse:
    return FileResponse(
        SETTINGS_JS_PATH,
        media_type="application/javascript; charset=utf-8",
        headers=_settings_static_headers(),
    )


@app.get("/api/settings/config")
async def get_settings_config() -> dict[str, Any]:
    return {
        "config": _load_settings_config(),
        "defaults": _default_settings_config(),
        "tts_presets": TTS_PRESETS,
        "mcp_server_presets": MCP_SERVER_PRESETS,
    }


@app.put("/api/settings/config")
async def put_settings_config(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if isinstance(payload, dict):
        draft = payload.get("mcp_draft")
        if isinstance(draft, dict):
            draft_name = str(draft.get("name") or "").strip()
            draft_json = str(draft.get("config_json") or "")
            if draft_json.strip():
                _ensure_mcp_server_from_draft(draft_name, draft_json)
    previous = _load_settings_config()
    raw_config = payload.get("config", payload) if isinstance(payload, dict) else {}
    if not isinstance(raw_config, dict):
        raise HTTPException(status_code=400, detail="settings payload must be an object")
    merged = _deep_merge(_load_settings_config(), raw_config)
    normalized = _normalize_settings_config(merged)
    _save_full_config(normalized)
    if normalized.get("model_path") and normalized.get("model_path") != previous.get("model_path"):
        _write_runtime_command("load_model", {"model_path": normalized["model_path"]})
    _get_mcp_bridge(force_reload=True)
    return {
        "config": normalized,
        "defaults": _default_settings_config(),
        "tts_presets": TTS_PRESETS,
        "mcp_server_presets": MCP_SERVER_PRESETS,
    }


@app.get("/api/skills")
async def list_skills() -> dict[str, Any]:
    return _skills_response_payload()


@app.post("/api/skills/import-local")
async def import_local_skill(req: SkillImportLocalRequest) -> dict[str, Any]:
    source_dir = str(req.path or req.directory or "").strip()
    if not source_dir:
        raise HTTPException(status_code=400, detail="path is required")
    try:
        result = _get_skill_manager(force_reload=True).import_local_directory(source_dir, name=req.name)
        _get_skill_manager(force_reload=True)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"skill already exists: {exc}") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "skill": result.skill.to_summary(),
        "target_path": str(result.target_path),
        **_skills_response_payload(),
    }


@app.post("/api/skills/import-git")
async def import_git_skill(req: SkillImportGitRequest) -> dict[str, Any]:
    repo_url = str(req.repo_url or req.url or "").strip()
    if not repo_url:
        raise HTTPException(status_code=400, detail="repo_url is required")
    ref = str(req.ref or req.branch or "").strip()
    try:
        result = _get_skill_manager(force_reload=True).install_from_git(
            repo_url,
            name=req.name,
            ref=ref,
            subdir=req.subdir,
        )
        _get_skill_manager(force_reload=True)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"skill already exists: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"git import failed: {exc}") from exc
    return {
        "ok": True,
        "skill": result.skill.to_summary(),
        "target_path": str(result.target_path),
        **_skills_response_payload(),
    }


@app.post("/api/skills/delete")
async def delete_skill(req: SkillDeleteRequest) -> dict[str, Any]:
    skill_id = _get_skill_manager().canonicalize_skill_id(str(req.skill_id or req.id or req.name or "").strip())
    if not skill_id:
        raise HTTPException(status_code=400, detail="skill_id is required")
    try:
        _get_skill_manager(force_reload=True).delete_imported_skill(skill_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    config = _load_settings_config()
    chat_cfg = config.setdefault("chat", {})
    skills_cfg = chat_cfg.setdefault("skills", {"enabled": True, "default_active_ids": []})
    skills_cfg["default_active_ids"] = [
        item for item in _normalize_skill_ids(skills_cfg.get("default_active_ids")) if item != skill_id
    ]
    _save_full_config(_normalize_settings_config(config))
    _get_skill_manager(force_reload=True)
    return {"ok": True, "skill_id": skill_id, **_skills_response_payload()}


@app.post("/api/settings/file-allowlist/pick")
async def post_settings_file_allowlist_pick(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    if not _runtime_host_is_online():
        return {
            "ok": False,
            "cancelled": False,
            "path": "",
            "detail": "桌宠宿主未连接，无法打开原生目录选择器。请先启动桌宠主程序。",
        }
    request_id = str(uuid4())
    response_path = RUNTIME_COMMAND_RESPONSE_PATH.with_name(f".pet_runtime_command.response.{request_id}.json")
    try:
        response_path.unlink(missing_ok=True)
    except Exception:
        pass
    start_dir = str(
        data.get("start_dir")
        or data.get("start_path")
        or data.get("current_path")
        or data.get("path")
        or data.get("directory")
        or ""
    ).strip()
    pick_kind = str(data.get("kind") or data.get("mode") or "directory").strip().lower()
    command_type = "pick_image_file" if pick_kind in {"image", "file", "image_file"} else "pick_directory"
    command = _write_runtime_command_with_response(
        command_type,
        {"request_id": request_id, "start_dir": start_dir, "start_path": start_dir},
        response_path=response_path,
    )
    try:
        response = await asyncio.to_thread(
            _wait_for_runtime_command_response,
            nonce=str(command.get("nonce") or ""),
            response_path=response_path,
            timeout_sec=RUNTIME_PICK_TIMEOUT_SEC,
        )
    finally:
        try:
            response_path.unlink(missing_ok=True)
        except Exception:
            pass
    if not isinstance(response, dict):
        return {
            "ok": False,
            "cancelled": False,
            "path": "",
            "detail": "桌宠宿主当前不可用，或目录选择操作已超时。",
        }
    status = str(response.get("status") or "").strip().lower()
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    path = str(result.get("path") or result.get("directory") or "").strip()
    if status == "success" and path:
        return {"ok": True, "cancelled": False, "path": path, "detail": ""}
    if status == "cancelled":
        return {"ok": False, "cancelled": True, "path": "", "detail": "已取消目录选择。"}
    detail = str(result.get("error") or response.get("error") or "目录选择失败。").strip()
    return {"ok": False, "cancelled": False, "path": "", "detail": detail}


@app.get("/api/settings/models-local")
async def get_local_models() -> dict[str, Any]:
    return {"models": _list_local_models()}


@app.post("/api/settings/preview-action")
async def post_settings_preview_action(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="preview payload must be an object")

    command_type = str(payload.get("type") or "").strip()
    if command_type not in {"load_model", "play_motion", "play_expression"}:
        raise HTTPException(status_code=400, detail="unsupported preview action")

    command_payload: dict[str, Any] = {}
    model_path = _normalize_model_path(payload.get("model_path", ""))
    if model_path:
        command_payload["model_path"] = model_path

    if command_type == "load_model":
        if not model_path:
            raise HTTPException(status_code=400, detail="model_path is required")
    elif command_type == "play_motion":
        group = str(payload.get("group") or "").strip()
        if not group:
            raise HTTPException(status_code=400, detail="group is required")
        command_payload["group"] = group
        try:
            command_payload["index"] = max(0, int(payload.get("index", 0)))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid motion index: {exc}") from exc
    else:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        command_payload["name"] = name

    _write_runtime_command(command_type, command_payload)
    return {"ok": True, "command": {"type": command_type, "payload": command_payload}}


@app.post("/api/chat/stream")
async def chat_stream(req: ChatStreamRequest) -> StreamingResponse:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty")
    session_id = req.session_id or "default"
    chat_mode = _normalize_chat_mode(req.chat_mode)
    settings_config = _load_settings_config()
    router_cfg = _resolve_router_request_config(req, settings_config)
    resolved_skills = _resolve_request_skills(req.skill_ids, settings=settings_config, chat_mode=chat_mode)
    tooling_cfg = _load_runtime_tooling_config()
    tools_enabled = bool(tooling_cfg.get("enabled", True) and req.tools_enabled)
    if chat_mode == CHAT_MODE_CHAT:
        if not tools_enabled:
            raise HTTPException(status_code=400, detail="?????????????? tavily-mcp?")
        if not _has_chat_mode_tavily_tools():
            raise HTTPException(status_code=400, detail="?????? tavily-mcp????????? tavily-mcp ???")
    if chat_mode == CHAT_MODE_SKILL and not router_cfg["enabled"] and not resolved_skills.skill_ids:
        raise HTTPException(status_code=400, detail="Skill?????????????")

    pet_display_name = _extract_pet_display_name(req.system_prompt)
    history = SESSION_STORE.get(session_id, [])
    history = _trim_messages(history, req.memory_window)
    working = history + [{"role": "user", "content": text}]
    max_reasoning_steps = max(1, min(12, int(req.max_reasoning_steps or 10)))
    router_used = False
    route_kind = ""
    route_thought_summary = ""
    route_skill_ids: list[str] = []
    route_tool_candidates: list[str] = []
    route_tool_call: dict[str, Any] = {}
    route_search_needed = False
    route_search_query = ""
    decision_messages = list(working)

    if router_cfg["enabled"]:
        route_tools = _build_router_tool_schemas(chat_mode, list(resolved_skills.skill_ids))
        route_skill_summaries = _skill_summaries_for_route(
            chat_mode=chat_mode,
            requested_skill_ids=req.skill_ids,
            settings_config=settings_config,
        )
        try:
            route = await classify_route(
                chat_mode=chat_mode,
                messages=working,
                model=router_cfg["model"],
                tools=route_tools,
                skill_summaries=route_skill_summaries,
                llm_provider=router_cfg["llm_provider"],
                api_base_url=router_cfg["api_base_url"],
                api_key=router_cfg["api_key"],
            )
            router_used = True
            route_kind = str(route.route_kind or "")
            route_thought_summary = str(route.thought_summary or "")
            route_search_needed = bool(route.search_needed)
            route_search_query = str(route.search_query or "")
            route_tool_candidates = list(route.tool_candidates or [])
            route_skill_ids = list(route.skill_ids or [])
            if route.tool_call is not None:
                route_tool_call = route.tool_call.to_dict()

            if chat_mode == CHAT_MODE_REACT:
                if route_kind == "skill_task" and route_skill_ids:
                    resolved_skills = _resolve_request_skills(route_skill_ids, settings=settings_config, chat_mode=chat_mode)
                elif route_kind == "complex_task" and route_tool_candidates:
                    guidance = _route_guidance_message(route_tool_candidates)
                    if guidance:
                        decision_messages = list(history) + [{"role": "system", "content": guidance}, {"role": "user", "content": text}]
            elif chat_mode == CHAT_MODE_CHAT:
                if route_search_needed and route_search_query:
                    search_tool_name = _pick_tavily_tool_name(route_tools)
                    if not search_tool_name:
                        raise HTTPException(status_code=400, detail="?????? tavily-mcp????????? tavily-mcp ???")
                    route_kind = "simple_tool_task"
                    route_tool_call = {
                        "name": search_tool_name,
                        "arguments": {"query": route_search_query},
                        "action_message": f"?????????{route_search_query}",
                    }
                else:
                    route_kind = "direct_answer"
            elif chat_mode == CHAT_MODE_SKILL:
                resolved_skills = _resolve_request_skills(route_skill_ids, settings=settings_config, chat_mode=chat_mode)
                if not resolved_skills.skill_ids:
                    raise HTTPException(status_code=400, detail="Skill???? API ?????????????? skill ??????")
                route_kind = "skill_task"
                route_skill_ids = list(resolved_skills.skill_ids)
        except HTTPException:
            raise
        except Exception:
            router_used = False
            route_kind = ""
            route_thought_summary = ""
            route_skill_ids = []
            route_tool_candidates = []
            route_tool_call = {}
            route_search_needed = False
            route_search_query = ""
            decision_messages = list(working)

    async def event_gen():
        skill_prompt_text = resolved_skills.prompt_text if chat_mode != CHAT_MODE_CHAT else ""
        sys_prompt = _build_system_prompt(
            req.system_prompt,
            skill_prompt_text,
            bool(req.expression_mode),
            str(req.expression_output_format or ""),
            req.available_expressions,
        )
        prompt_msgs = [{"role": "system", "content": sys_prompt}] + working if sys_prompt else list(working)
        tools_enabled = bool(tooling_cfg.get("enabled", True) and req.tools_enabled)

        yield _sse(
            "meta",
            {
                "session_id": session_id,
                "model": req.model,
                "llm_provider": req.llm_provider,
                "tool_mode": req.tool_mode,
                "tools_enabled": tools_enabled,
                "chat_mode": chat_mode,
                "router_used": router_used,
                "route_kind": route_kind,
                "router_model": router_cfg["model"],
                "router_llm_provider": router_cfg["llm_provider"],
                "react_enabled": bool(req.react_enabled),
                "react_visibility": str(req.react_visibility or "inline"),
                "pet_display_name": pet_display_name,
                "active_skill_ids": list(resolved_skills.skill_ids),
            },
        )

        full_answer = ""
        cleanup_turn_id = ""
        try:
            outcome = await _get_agent_graph_runtime().start_turn(
                {
                    "turn_id": uuid4().hex,
                    "session_id": session_id,
                    "chat_mode": chat_mode,
                    "router_used": router_used,
                    "route_kind": route_kind,
                    "route_thought_summary": route_thought_summary,
                    "route_skill_ids": list(route_skill_ids),
                    "route_tool_candidates": list(route_tool_candidates),
                    "route_tool_call": dict(route_tool_call),
                    "route_search_needed": route_search_needed,
                    "route_search_query": route_search_query,
                    "memory_window": int(req.memory_window),
                    "model": req.model,
                    "llm_provider": req.llm_provider,
                    "api_base_url": req.api_base_url,
                    "api_key": req.api_key,
                    "system_prompt": req.system_prompt,
                    "active_skill_ids": list(resolved_skills.skill_ids),
                    "skill_prompt_text": skill_prompt_text,
                    "expression_mode": bool(req.expression_mode),
                    "expression_output_format": str(req.expression_output_format or ""),
                    "available_expressions": list(req.available_expressions),
                    "tools_enabled": tools_enabled,
                    "react_enabled": bool(req.react_enabled),
                    "max_reasoning_steps": max_reasoning_steps,
                    "reasoning_step": 1,
                    "user_text": text,
                    "pet_display_name": pet_display_name,
                    "working_messages": working,
                    "decision_messages": decision_messages,
                    "prompt_messages": prompt_msgs,
                }
            )
            if outcome.thought_summary:
                yield _sse("phase", _phase_payload("thought", outcome.thought_summary))
            if outcome.is_pending and outcome.approval_request:
                yield _sse(
                    "approval_required",
                    {
                        "turn_id": outcome.turn_id,
                        "phase": "action",
                        "text": str(outcome.approval_request.get("text") or ""),
                        "tools": list(outcome.approval_request.get("tools") or []),
                        "speaker": str(outcome.approval_request.get("speaker") or "pet"),
                    },
                )
                return

            cleanup_turn_id = outcome.turn_id
            event_stream = stream_final_reply(
                messages=outcome.final_messages or prompt_msgs,
                model=req.model,
                llm_provider=req.llm_provider,
                api_base_url=req.api_base_url,
                api_key=req.api_key,
            )
            use_ndjson = bool(req.expression_mode) and str(req.expression_output_format or "") == EXPR_OUTPUT_FORMAT
            if use_ndjson:
                buffer = ""
                spoken_answer = ""
                display_answer = ""
                display_mode = False
                async for item in event_stream:
                    if str(item.get("type") or "") != "final_delta":
                        continue
                    delta = str(item.get("delta") or "")
                    if not delta:
                        continue
                    buffer += delta
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if not display_mode:
                            display_mode, remainder = _split_display_delimiter(line)
                            if display_mode:
                                if remainder:
                                    display_answer, display_chunk = _append_display_line(display_answer, remainder)
                                    if display_chunk is not None:
                                        yield _sse("display_segment", {"text": display_chunk})
                                continue
                        if display_mode:
                            display_answer, display_chunk = _append_display_line(display_answer, line)
                            if display_chunk is not None:
                                yield _sse("display_segment", {"text": display_chunk})
                            continue
                        segment = _parse_ndjson_line(line)
                        if segment is None:
                            continue
                        seg_text = str(segment.get("text") or "")
                        seg_expr = segment.get("expr")
                        if seg_text:
                            spoken_answer += seg_text
                            yield _sse("segment", {"text": seg_text, "expr": seg_expr})
                if buffer.strip():
                    if not display_mode:
                        display_mode, remainder = _split_display_delimiter(buffer)
                        if display_mode:
                            if remainder:
                                display_answer, display_chunk = _append_display_line(display_answer, remainder)
                                if display_chunk is not None:
                                    yield _sse("display_segment", {"text": display_chunk})
                        else:
                            segment = _parse_ndjson_line(buffer)
                            if segment is not None:
                                seg_text = str(segment.get("text") or "")
                                seg_expr = segment.get("expr")
                                if seg_text:
                                    spoken_answer += seg_text
                                    yield _sse("segment", {"text": seg_text, "expr": seg_expr})
                    elif display_mode:
                        display_answer, display_chunk = _append_display_line(display_answer, buffer)
                        if display_chunk is not None:
                            yield _sse("display_segment", {"text": display_chunk})
                full_answer = display_answer or spoken_answer
            else:
                async for item in event_stream:
                    if str(item.get("type") or "") != "final_delta":
                        continue
                    clean_delta = _sanitize_ai_text(str(item.get("delta") or ""))
                    if not clean_delta:
                        continue
                    full_answer += clean_delta
                    yield _sse("token", {"delta": clean_delta})
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})
            return

        full_answer = _sanitize_ai_text(full_answer)
        updated = working + [{"role": "assistant", "content": full_answer}]
        SESSION_STORE[session_id] = _trim_messages(updated, req.memory_window)
        yield _sse("done", {"text": full_answer})
        if cleanup_turn_id:
            _get_agent_graph_runtime().delete_turn(cleanup_turn_id)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/api/chat/approval")
async def chat_approval(req: ChatApprovalRequest) -> StreamingResponse:
    runtime = _get_agent_graph_runtime()
    if runtime.get_pending_approval(req.turn_id) is None:
        raise HTTPException(status_code=404, detail="pending turn not found or already handled")

    async def event_gen():
        full_answer = ""
        cleanup_turn_id = req.turn_id
        state: dict[str, Any] = {}
        try:
            outcome = await runtime.resume_turn(ApprovalDecision(turn_id=req.turn_id, approved=req.approved))
            state = outcome.state
            if req.approved:
                yield _sse("phase", _phase_payload("action", "好呀，那我这就开始处理这件事。"))
                if outcome.thought_summary:
                    yield _sse("phase", _phase_payload("thought", outcome.thought_summary))
            else:
                yield _sse("phase", _phase_payload("action", "那这次我先不调用工具，直接按现有信息回答你。"))
            if outcome.is_pending and outcome.approval_request:
                cleanup_turn_id = ""
                yield _sse(
                    "approval_required",
                    {
                        "turn_id": outcome.turn_id,
                        "phase": "action",
                        "text": str(outcome.approval_request.get("text") or ""),
                        "tools": list(outcome.approval_request.get("tools") or []),
                        "speaker": str(outcome.approval_request.get("speaker") or "pet"),
                    },
                )
                return

            event_stream = stream_final_reply(
                messages=outcome.final_messages or list(state.get("prompt_messages") or []),
                model=str(state.get("model") or "qwen3:8b"),
                llm_provider=str(state.get("llm_provider") or "ollama"),
                api_base_url=str(state.get("api_base_url") or ""),
                api_key=str(state.get("api_key") or ""),
            )
            use_ndjson = bool(state.get("expression_mode", True)) and str(
                state.get("expression_output_format") or ""
            ) == EXPR_OUTPUT_FORMAT
            if use_ndjson:
                buffer = ""
                spoken_answer = ""
                display_answer = ""
                display_mode = False
                async for item in event_stream:
                    if str(item.get("type") or "") != "final_delta":
                        continue
                    delta = str(item.get("delta") or "")
                    if not delta:
                        continue
                    buffer += delta
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if not display_mode:
                            display_mode, remainder = _split_display_delimiter(line)
                            if display_mode:
                                if remainder:
                                    display_answer, display_chunk = _append_display_line(display_answer, remainder)
                                    if display_chunk is not None:
                                        yield _sse("display_segment", {"text": display_chunk})
                                continue
                        if display_mode:
                            display_answer, display_chunk = _append_display_line(display_answer, line)
                            if display_chunk is not None:
                                yield _sse("display_segment", {"text": display_chunk})
                            continue
                        segment = _parse_ndjson_line(line)
                        if segment is None:
                            continue
                        seg_text = str(segment.get("text") or "")
                        seg_expr = segment.get("expr")
                        if seg_text:
                            spoken_answer += seg_text
                            yield _sse("segment", {"text": seg_text, "expr": seg_expr})
                if buffer.strip():
                    if not display_mode:
                        display_mode, remainder = _split_display_delimiter(buffer)
                        if display_mode:
                            if remainder:
                                display_answer, display_chunk = _append_display_line(display_answer, remainder)
                                if display_chunk is not None:
                                    yield _sse("display_segment", {"text": display_chunk})
                        else:
                            segment = _parse_ndjson_line(buffer)
                            if segment is not None:
                                seg_text = str(segment.get("text") or "")
                                seg_expr = segment.get("expr")
                                if seg_text:
                                    spoken_answer += seg_text
                                    yield _sse("segment", {"text": seg_text, "expr": seg_expr})
                    elif display_mode:
                        display_answer, display_chunk = _append_display_line(display_answer, buffer)
                        if display_chunk is not None:
                            yield _sse("display_segment", {"text": display_chunk})
                full_answer = display_answer or spoken_answer
            else:
                async for item in event_stream:
                    if str(item.get("type") or "") != "final_delta":
                        continue
                    clean_delta = _sanitize_ai_text(str(item.get("delta") or ""))
                    if not clean_delta:
                        continue
                    full_answer += clean_delta
                    yield _sse("token", {"delta": clean_delta})
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})
            return

        full_answer = _sanitize_ai_text(full_answer)
        updated = list(state.get("working_messages") or []) + [{"role": "assistant", "content": full_answer}]
        session_id = str(state.get("session_id") or "default")
        memory_window = int(state.get("memory_window") or 10)
        SESSION_STORE[session_id] = _trim_messages(updated, memory_window)
        yield _sse("done", {"text": full_answer})
        if cleanup_turn_id:
            runtime.delete_turn(cleanup_turn_id)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/api/models")
async def models(req: ModelListRequest) -> dict[str, Any]:
    try:
        items = await list_models(provider=req.llm_provider, base_url=req.api_base_url, api_key=req.api_key)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"list models failed: {exc}") from exc
    return {"models": items}


@app.get("/api/mcp/servers")
async def list_mcp_servers() -> dict[str, Any]:
    return {"servers": _lightweight_mcp_server_list()}


@app.get("/api/mcp/health")
async def mcp_health() -> dict[str, Any]:
    bridge = _get_mcp_bridge()
    return bridge.health()


@app.post("/api/mcp/create-config")
async def create_mcp_from_config(req: MCPServerCreateRequest) -> dict[str, Any]:
    try:
        result = _ensure_mcp_server_from_draft(req.name, req.config_json)
        bridge = _get_mcp_bridge(force_reload=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"create mcp from config failed: {exc}") from exc
    if not result:
        raise HTTPException(status_code=400, detail="empty mcp server config")
    return {"ok": True, "server": next((s for s in bridge.list_servers() if s.get("name") == result["name"]), result)}


@app.post("/api/mcp/delete")
async def delete_mcp(req: MCPDeleteRequest) -> dict[str, Any]:
    try:
        bridge = _get_mcp_bridge()
        server_cfg = bridge.get_server_config(req.name)
        if not server_cfg:
            raise FileNotFoundError(f"server not found: {req.name}")
        manifest_path = str(server_cfg.get("manifest_path") or "").strip()
        if not manifest_path:
            raise FileNotFoundError(f"manifest path missing for server: {req.name}")
        bridge.stop()
        bridge.manager.delete_server(manifest_path)
        _remove_third_party_config(req.name)
        bridge = _get_mcp_bridge(force_reload=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"delete mcp failed: {exc}") from exc
    return {"ok": True, "name": req.name, "servers": bridge.list_servers()}


@app.post("/api/mcp/toggle")
async def toggle_mcp(req: MCPToggleRequest) -> dict[str, Any]:
    try:
        bridge = _get_mcp_bridge()
        result = bridge.toggle_server(req.name, req.enabled)
        _set_third_party_enabled(req.name, req.enabled)
        bridge = _get_mcp_bridge(force_reload=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"toggle mcp failed: {exc}") from exc
    return {"ok": True, "server": next((s for s in bridge.list_servers() if s.get("name") == result["name"]), result)}


@app.post("/api/mcp/reload")
async def reload_mcp() -> dict[str, Any]:
    bridge = _get_mcp_bridge(force_reload=True)
    return {"ok": True, "servers": bridge.list_servers()}


@app.post("/api/tts")
async def tts(req: TTSRequest) -> dict[str, Any]:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty")
    provider = req.provider or DEFAULT_PROVIDER
    if not tts_available(provider, req.provider_url):
        raise HTTPException(status_code=503, detail=f"tts provider unavailable: {provider}")

    cleanup_old_audio(AUDIO_CACHE_DIR)
    try:
        result = await synthesize_to_audio(
            text=text,
            cache_dir=AUDIO_CACHE_DIR,
            voice=req.voice,
            rate=req.rate,
            volume=req.volume,
            provider=provider,
            provider_url=req.provider_url,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TTS synthesis failed: {exc}") from exc
    return {
        "audio_url": f"/api/audio/{result.path.name}",
        "duration_ms": result.duration_ms,
        "provider": provider,
        "supported_providers": list_supported_providers(),
    }


@app.get("/api/audio/{file_name}")
async def get_audio(file_name: str) -> FileResponse:
    safe_name = Path(file_name).name
    if safe_name != file_name:
        raise HTTPException(status_code=400, detail="invalid audio file name")
    path = AUDIO_CACHE_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="audio file not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)
