from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import mimetypes
import re
import sys

import httpx
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .agent_graph import AgentGraphRuntime, ApprovalDecision, GraphDependencies
from .chat_topics import DEFAULT_TOPIC_TITLE, TopicStore, normalize_topic_id
from .asr import (
    ASRError,
    ASRService,
    DEFAULT_ASR_API_BASE_URL,
    DEFAULT_ASR_CONFIG,
    DEFAULT_ASR_LANGUAGE,
    DEFAULT_ASR_PROVIDER,
    DEFAULT_PUSH_TO_TALK_KEY,
    asr_available,
    normalize_push_to_talk_key,
)
from .agent_orchestrator import (
    _compact_text,
    build_execution_plan,
    classify_route,
    decide_turn,
    plan_capabilities,
    search_capabilities,
    execute_tool_calls,
    stream_final_reply,
)
from .mcp.local_server import LocalMCPServer
from .mcp_bridge import MCPBridge
from .mcp.third_party_manager import ThirdPartyMCPManager
from .models import (
    ChatApprovalRequest,
    ChatMemoryDecisionRequest,
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
from .ollama_client import OLLAMA_BASE_URL, chat_once as provider_chat_once, is_ollama_alive, list_models
from .runtime_prompts import (
    EXPR_PROTOCOL_PROMPT,
    REACT_SKILL_VISIBILITY_NOTE,
    build_decision_messages as _rt_build_decision_messages,
    build_expression_protocol_prompt as _rt_build_expression_protocol_prompt,
    build_system_prompt as _rt_build_system_prompt,
    continuation_recheck_prompt as _rt_continuation_recheck_prompt,
)
from .skills import ResolvedSkillSet, SkillAwareToolBridge, SkillManager, SkillRuntime, tool_name_matches_pattern
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
CHAT_TOPICS_ROOT = ROOT_DIR / "data" / "chat_topics"
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
LONG_TERM_MEMORY_TOOL_PREFIX = "basic_memory."
LONG_TERM_MEMORY_TOOL_SEARCH = f"{LONG_TERM_MEMORY_TOOL_PREFIX}search_notes"
LONG_TERM_MEMORY_TOOL_READ = f"{LONG_TERM_MEMORY_TOOL_PREFIX}read_note"
LONG_TERM_MEMORY_TOOL_WRITE = f"{LONG_TERM_MEMORY_TOOL_PREFIX}write_note"
LONG_TERM_MEMORY_FOLDER = "ipet"
LONG_TERM_MEMORY_TAGS = ["ipet", "long-term-memory"]
CHAT_MODE_NO_LIVE_SEARCH_PROMPT = (
    "Live web search is unavailable in this chat-mode turn. "
    "Answer using only the conversation context and your general built-in knowledge. "
    "Do not claim that you searched, browsed, fetched, checked, or verified anything on the web. "
    "If the answer may depend on current web information, say you cannot verify it live right now."
)
PHASE_SKILL_SELECTION = "skill_selection"
PHASE_SKILL_EXECUTION = "skill_execution"
PHASE_AGENT_LOOP = "agent_loop"
SYSTEM_TOOL_AGENT_LOOP = "system.agent_loop"
REACT_SKILL_PROMPT_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
REACT_SKILL_PROMPT_DEPENDENCIES_RE = re.compile(r"^\s*Dependencies\s*:\s*$", re.IGNORECASE)
REACT_SKILL_PROMPT_FILE_EXTENSIONS = {
    "css",
    "csv",
    "docx",
    "gif",
    "html",
    "jpeg",
    "jpg",
    "js",
    "json",
    "md",
    "pdf",
    "png",
    "pptx",
    "py",
    "svg",
    "toml",
    "txt",
    "xlsx",
    "yaml",
    "yml",
}
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
    },
    "basic_memory": {
        "label": "Basic Memory",
        "name": "basic_memory",
        "config": {
            "mcpServers": {
                "basic_memory": {
                    "command": "uvx",
                    "args": ["basic-memory", "mcp"],
                }
            }
        },
        "notes": "Persistent long-term memory. This is for cross-chat memory, not the current topic context.",
    }
}

_MCP_BRIDGE: MCPBridge | None = None
_MCP_CONFIG_SNAPSHOT = ""
_AGENT_GRAPH_RUNTIME: AgentGraphRuntime | None = None
_SKILL_MANAGER: SkillManager | None = None
_CHAT_TOPIC_STORE: TopicStore | None = None
_ASR_SERVICE: ASRService | None = None
_ASR_WARMUP_TASK: asyncio.Task[str] | None = None
_TURN_TOOL_BRIDGE_CACHE: dict[str, Any] = {}

LOGGER = logging.getLogger(__name__)


def _is_macos() -> bool:
    return str(sys.platform or "").strip().lower() == "darwin"


def _default_asr_enabled() -> bool:
    return not _is_macos()


def _default_asr_config() -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_ASR_CONFIG))
    config["enabled"] = _default_asr_enabled()
    config["api_base_url"] = str(config.get("api_base_url") or DEFAULT_ASR_API_BASE_URL).strip() or DEFAULT_ASR_API_BASE_URL
    return config


def _asr_disabled_message() -> str:
    if _is_macos():
        return "ASR is disabled by default on macOS v1. Chat and settings remain available."
    return "ASR is disabled in settings."


@dataclass(frozen=True)
class ChatRequestContext:
    raw_config: dict[str, Any]
    settings_config: dict[str, Any]
    tooling_cfg: dict[str, Any]
    topic_history_cfg: dict[str, Any]
    router_cfg: dict[str, Any]
    resolved_skills: ResolvedSkillSet
    session_id: str
    pet_display_name: str
    history_messages: list[dict[str, Any]]
    working_messages: list[dict[str, Any]]
    topic_snapshot: Any | None = None


@dataclass
class PerfTracker:
    started_at: float = field(default_factory=time.perf_counter)
    timings_ms: dict[str, float] = field(default_factory=dict)

    def add(self, name: str, elapsed_sec: float) -> None:
        self.timings_ms[name] = round(float(self.timings_ms.get(name, 0.0)) + max(0.0, elapsed_sec) * 1000.0, 3)

    def time_call(self, name: str, fn, /, *args, **kwargs):
        started = time.perf_counter()
        result = fn(*args, **kwargs)
        self.add(name, time.perf_counter() - started)
        return result

    async def time_await(self, name: str, awaitable):
        started = time.perf_counter()
        result = await awaitable
        self.add(name, time.perf_counter() - started)
        return result


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
    default_asr_config = _default_asr_config()
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
            "topic_history": {
                "enabled": True,
                "summary_interval_assistant_turns": 10,
            },
            "long_term_memory": {
                "enabled": False,
                "project": "ipet-default",
                "read_enabled": True,
                "write_enabled": True,
                "ask_before_save": True,
                "prefer_topic_history": True,
                "save_from_major_summary": True,
                "save_on_explicit_request": True,
            },
            "asr": json.loads(json.dumps(default_asr_config)),
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


def _topic_history_config(settings_config: dict[str, Any]) -> dict[str, Any]:
    chat_cfg = settings_config.get("chat", {}) if isinstance(settings_config, dict) else {}
    topic_history_cfg = chat_cfg.get("topic_history", {}) if isinstance(chat_cfg, dict) else {}
    if not isinstance(topic_history_cfg, dict):
        topic_history_cfg = {}
    try:
        interval = max(1, int(topic_history_cfg.get("summary_interval_assistant_turns", 10)))
    except Exception:
        interval = 10
    return {
        "enabled": bool(topic_history_cfg.get("enabled", True)),
        "summary_interval_assistant_turns": interval,
        "major_summary_group_size": 3,
    }


def _long_term_memory_config(settings_config: dict[str, Any]) -> dict[str, Any]:
    chat_cfg = settings_config.get("chat", {}) if isinstance(settings_config, dict) else {}
    raw_cfg = chat_cfg.get("long_term_memory", {}) if isinstance(chat_cfg, dict) else {}
    if not isinstance(raw_cfg, dict):
        raw_cfg = {}
    return {
        "enabled": bool(raw_cfg.get("enabled", False)),
        "project": str(raw_cfg.get("project") or "ipet-default").strip() or "ipet-default",
        "read_enabled": bool(raw_cfg.get("read_enabled", True)),
        "write_enabled": bool(raw_cfg.get("write_enabled", True)),
        "ask_before_save": bool(raw_cfg.get("ask_before_save", True)),
        "prefer_topic_history": bool(raw_cfg.get("prefer_topic_history", True)),
        "save_from_major_summary": bool(raw_cfg.get("save_from_major_summary", True)),
        "save_on_explicit_request": bool(raw_cfg.get("save_on_explicit_request", True)),
    }


def _asr_config(settings_config: dict[str, Any]) -> dict[str, Any]:
    default_asr_config = _default_asr_config()
    chat_cfg = settings_config.get("chat", {}) if isinstance(settings_config, dict) else {}
    asr_cfg = chat_cfg.get("asr", {}) if isinstance(chat_cfg, dict) else {}
    if not isinstance(asr_cfg, dict):
        asr_cfg = {}
    return {
        "enabled": bool(asr_cfg.get("enabled", default_asr_config["enabled"])),
        "provider": str(asr_cfg.get("provider") or DEFAULT_ASR_PROVIDER).strip() or DEFAULT_ASR_PROVIDER,
        "api_base_url": str(asr_cfg.get("api_base_url") or default_asr_config["api_base_url"]).strip() or DEFAULT_ASR_API_BASE_URL,
        "push_to_talk_key": normalize_push_to_talk_key(asr_cfg.get("push_to_talk_key")),
        "interim_results": bool(asr_cfg.get("interim_results", default_asr_config["interim_results"])),
    }


def _should_use_external_asr(settings_config: dict[str, Any], asr_cfg: dict[str, Any]) -> bool:
    chat_cfg = settings_config.get("chat", {}) if isinstance(settings_config, dict) else {}
    backend_url = str(chat_cfg.get("backend_url") or DEFAULT_BACKEND_URL).strip().rstrip("/")
    asr_url = str(asr_cfg.get("api_base_url") or "").strip().rstrip("/")
    return bool(asr_url) and asr_url != backend_url


async def _external_asr_health(asr_base_url: str) -> bool:
    url = f"{str(asr_base_url or '').rstrip('/')}/api/health"
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return False
        payload = resp.json()
        return bool(payload.get("asr"))
    except Exception:
        return False


def _resolve_summary_request_config(
    settings_config: dict[str, Any],
    *,
    llm_provider: str,
    api_base_url: str,
    api_key: str,
    model: str,
) -> dict[str, str]:
    chat_cfg = settings_config.get("chat", {}) if isinstance(settings_config, dict) else {}
    main_provider = str(llm_provider or chat_cfg.get("llm_provider") or "ollama").strip() or "ollama"
    main_base_url = str(api_base_url or chat_cfg.get("api_base_url") or "").strip()
    main_api_key = str(api_key or chat_cfg.get("api_key") or "")
    main_model = str(model or chat_cfg.get("model") or "qwen3:8b").strip() or "qwen3:8b"
    return {
        "llm_provider": str(chat_cfg.get("router_llm_provider") or main_provider).strip() or main_provider,
        "api_base_url": str(chat_cfg.get("router_api_base_url") or main_base_url).strip(),
        "api_key": str(chat_cfg.get("router_api_key") or main_api_key),
        "model": str(chat_cfg.get("router_model") or main_model).strip() or main_model,
    }


def _topic_summary_source_text(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        speaker = "User" if role == "user" else "Assistant"
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines).strip()


def _topic_summary_blocks_text(blocks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, block in enumerate(blocks, start=1):
        if not isinstance(block, dict):
            continue
        start_turn = int(block.get("start_assistant_turn") or 0)
        end_turn = int(block.get("end_assistant_turn") or 0)
        content = str(block.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"Mini Summary {index} | assistant turns {start_turn}-{end_turn}:\n{content}")
    return "\n\n".join(lines).strip()


def _extract_message_content(message: dict[str, Any]) -> str:
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return _sanitize_ai_text(content).strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
        return _sanitize_ai_text("\n".join(parts)).strip()
    return _sanitize_ai_text(str(content or "")).strip()


def _call_basic_memory_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    tooling_cfg: dict[str, Any] | None = None,
) -> ToolResult | None:
    bridge = _get_mcp_bridge_for_tooling(tooling_cfg) if isinstance(tooling_cfg, dict) else _get_mcp_bridge()
    if bridge is None:
        return None
    try:
        registered = list(bridge.list_registered_tools() or [])
    except Exception:
        registered = []
    names = {str(getattr(item, "name", "") or "").strip() for item in registered}
    if str(tool_name or "").strip() not in names:
        return None
    return bridge.call_tool(tool_name, arguments)


def _tool_result_payload(result: ToolResult | None) -> Any:
    if result is None:
        return None
    if result.structured_data is not None:
        return result.structured_data
    content = str(result.content or "").strip()
    if not content:
        return None
    try:
        return json.loads(content)
    except Exception:
        return content


def _flatten_memory_search_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("results", "items", "notes", "matches", "entries"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []


def _memory_item_identifier(item: dict[str, Any]) -> str:
    for key in ("identifier", "permalink", "path", "slug", "url", "id", "title"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _memory_item_title(item: dict[str, Any], fallback: str = "Untitled Memory") -> str:
    for key in ("title", "name", "path", "identifier", "slug"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return fallback


def _memory_item_excerpt(item: dict[str, Any]) -> str:
    for key in ("summary", "excerpt", "preview", "content", "text"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_memory_note_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("content", "text", "markdown", "body", "note"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
            if isinstance(nested, dict):
                nested_text = _extract_memory_note_text(nested)
                if nested_text:
                    return nested_text
        return ""
    return ""


def _looks_like_explicit_memory_request(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    if not text:
        return False
    markers = (
        "记住这个",
        "记下来",
        "帮我记住",
        "请记住",
        "别忘了",
        "不要忘",
        "remember this",
        "remember that",
        "save this",
        "keep this in mind",
    )
    return any(marker in text for marker in markers)


def _looks_like_cross_topic_memory_query(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    if not text:
        return False
    markers = (
        "以前说过",
        "之前说过",
        "之前提过",
        "以前提过",
        "之前定过",
        "之前决定",
        "还记得",
        "我喜欢什么",
        "我不喜欢什么",
        "我的偏好",
        "上次聊过",
        "before",
        "previously",
        "earlier",
        "remember about",
        "what do i like",
        "what did i say",
        "what did we decide",
        "my preference",
    )
    return any(marker in text for marker in markers)


def _should_read_long_term_memory(
    *,
    user_text: str,
    topic_snapshot: Any | None,
    long_term_memory_cfg: dict[str, Any],
) -> bool:
    if not long_term_memory_cfg.get("enabled") or not long_term_memory_cfg.get("read_enabled"):
        return False
    if not _looks_like_cross_topic_memory_query(user_text):
        return False
    if not long_term_memory_cfg.get("prefer_topic_history", True):
        return True
    if topic_snapshot is None:
        return True
    assistant_turn_count = int((topic_snapshot.meta or {}).get("assistant_turn_count") or 0)
    return assistant_turn_count <= 1


def _build_long_term_memory_message(
    *,
    user_text: str,
    topic_snapshot: Any | None,
    long_term_memory_cfg: dict[str, Any],
    tooling_cfg: dict[str, Any],
) -> dict[str, str] | None:
    if not _should_read_long_term_memory(
        user_text=user_text,
        topic_snapshot=topic_snapshot,
        long_term_memory_cfg=long_term_memory_cfg,
    ):
        return None
    project = str(long_term_memory_cfg.get("project") or "ipet-default").strip() or "ipet-default"
    search_result = _call_basic_memory_tool(
        LONG_TERM_MEMORY_TOOL_SEARCH,
        {"query": str(user_text or "").strip(), "project": project},
        tooling_cfg=tooling_cfg,
    )
    if search_result is None or not search_result.ok:
        return None
    search_items = _flatten_memory_search_items(_tool_result_payload(search_result))[:3]
    if not search_items:
        return None
    lines = ["[Long-term Memory]"]
    for index, item in enumerate(search_items, start=1):
        title = _memory_item_title(item, fallback=f"Memory {index}")
        identifier = _memory_item_identifier(item)
        note_text = ""
        if identifier:
            read_result = _call_basic_memory_tool(
                LONG_TERM_MEMORY_TOOL_READ,
                {"identifier": identifier, "project": project},
                tooling_cfg=tooling_cfg,
            )
            if read_result is not None and read_result.ok:
                note_text = _extract_memory_note_text(_tool_result_payload(read_result))
        preview = _compact_text(note_text or _memory_item_excerpt(item), 240)
        if not preview:
            continue
        lines.append(f"{index}. {title}\n{preview}")
    if len(lines) <= 1:
        return None
    return {"role": "system", "content": "\n\n".join(lines)}


def _latest_major_summary_block(summary_document: dict[str, Any]) -> dict[str, Any] | None:
    for block in reversed(list(summary_document.get("blocks") or [])):
        if isinstance(block, dict) and str(block.get("type") or "") == "major_summary":
            return block
    return None


def _memory_lines_from_text(value: str, *, limit: int = 5) -> list[str]:
    parts: list[str] = []
    for raw_line in str(value or "").replace("\r", "\n").split("\n"):
        text = str(raw_line or "").strip(" -\t")
        if not text:
            continue
        for fragment in re.split(r"[。！？!?]\s*|\.\s+", text):
            cleaned = str(fragment or "").strip(" -\t")
            if cleaned:
                parts.append(cleaned)
        if len(parts) >= limit:
            break
    seen: set[str] = set()
    out: list[str] = []
    for item in parts:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _memory_preference_lines(lines: list[str]) -> list[str]:
    keywords = (
        "喜欢",
        "不喜欢",
        "偏好",
        "习惯",
        "决定",
        "需要",
        "不要",
        "限制",
        "计划",
        "prefer",
        "like",
        "dislike",
        "decid",
        "need",
        "must",
        "avoid",
    )
    selected = [item for item in lines if any(keyword in item.lower() for keyword in keywords)]
    return selected[:3]


def _build_long_term_memory_candidate_content(
    *,
    summary_text: str,
    topic_meta: dict[str, Any],
    marker: str,
    start_turn: int,
    end_turn: int,
) -> str:
    summary = str(summary_text or "").strip()
    bullet_lines = _memory_lines_from_text(summary)
    stable_lines = bullet_lines[:3]
    preference_lines = _memory_preference_lines(bullet_lines)
    updated_at = str(topic_meta.get("updated_at") or "")
    title = str(topic_meta.get("title") or DEFAULT_TOPIC_TITLE).strip() or DEFAULT_TOPIC_TITLE
    topic_id = str(topic_meta.get("topic_id") or "").strip()
    content_lines = [
        "# Summary",
        summary or "No summary available.",
        "",
        "## Stable Facts",
    ]
    if stable_lines:
        content_lines.extend([f"- {item}" for item in stable_lines])
    else:
        content_lines.append("- None noted.")
    content_lines.extend(["", "## Preferences / Decisions"])
    if preference_lines:
        content_lines.extend([f"- {item}" for item in preference_lines])
    else:
        content_lines.append("- None noted.")
    content_lines.extend(
        [
            "",
            "## Source",
            f"- topic_id: {topic_id}",
            f"- title: {title}",
            f"- start_turn: {int(start_turn or 0)}",
            f"- end_turn: {int(end_turn or 0)}",
            f"- updated_at: {updated_at}",
            f"- marker: {marker}",
        ]
    )
    return "\n".join(content_lines).strip()


def _build_long_term_memory_candidate(
    *,
    topic_meta: dict[str, Any],
    marker: str,
    source_kind: str,
    summary_text: str,
    start_turn: int,
    end_turn: int,
) -> dict[str, Any]:
    title = str(topic_meta.get("title") or DEFAULT_TOPIC_TITLE).strip() or DEFAULT_TOPIC_TITLE
    return {
        "marker": marker,
        "title": f"{title} | {marker}",
        "content": _build_long_term_memory_candidate_content(
            summary_text=summary_text,
            topic_meta=topic_meta,
            marker=marker,
            start_turn=start_turn,
            end_turn=end_turn,
        ),
        "source_kind": str(source_kind or "").strip(),
        "start_turn": int(start_turn or 0),
        "end_turn": int(end_turn or 0),
        "folder": LONG_TERM_MEMORY_FOLDER,
        "tags": list(LONG_TERM_MEMORY_TAGS),
    }


def _marker_already_handled(meta: dict[str, Any], marker: str) -> bool:
    normalized = str(marker or "").strip()
    if not normalized:
        return True
    return normalized in {
        str(meta.get("last_long_term_memory_saved_marker") or "").strip(),
        str(meta.get("last_long_term_memory_dismissed_marker") or "").strip(),
    }


def _build_long_term_memory_suggestion(
    *,
    detail: dict[str, Any] | None,
    previous_meta: dict[str, Any] | None,
    user_text: str,
    assistant_text: str,
    long_term_memory_cfg: dict[str, Any],
) -> dict[str, Any] | None:
    if detail is None:
        return None
    topic_meta = detail.get("meta") if isinstance(detail.get("meta"), dict) else {}
    summary_document = detail.get("summary") if isinstance(detail.get("summary"), dict) else {}
    current_meta = topic_meta if isinstance(topic_meta, dict) else {}
    previous = previous_meta if isinstance(previous_meta, dict) else {}
    if long_term_memory_cfg.get("save_on_explicit_request") and _looks_like_explicit_memory_request(user_text):
        end_turn = int(current_meta.get("assistant_turn_count") or 0)
        marker = f"explicit:{end_turn}"
        if not _marker_already_handled(current_meta, marker):
            summary_text = f"User request: {str(user_text or '').strip()}\nAssistant reply: {str(assistant_text or '').strip()}".strip()
            return _build_long_term_memory_candidate(
                topic_meta=current_meta,
                marker=marker,
                source_kind="explicit",
                summary_text=summary_text,
                start_turn=end_turn,
                end_turn=end_turn,
            )
    previous_major_count = int(previous.get("major_summary_count") or 0)
    current_major_count = int(current_meta.get("major_summary_count") or 0)
    if not long_term_memory_cfg.get("save_from_major_summary") or current_major_count <= previous_major_count:
        return None
    major_block = _latest_major_summary_block(summary_document)
    if major_block is None:
        return None
    end_turn = int(major_block.get("end_assistant_turn") or 0)
    marker = f"major:{end_turn}"
    if _marker_already_handled(current_meta, marker):
        return None
    return _build_long_term_memory_candidate(
        topic_meta=current_meta,
        marker=marker,
        source_kind="major",
        summary_text=str(major_block.get("content") or "").strip(),
        start_turn=int(major_block.get("start_assistant_turn") or 0),
        end_turn=end_turn,
    )


def _save_long_term_memory_candidate(
    *,
    topic_id: str,
    candidate: dict[str, Any],
    long_term_memory_cfg: dict[str, Any],
    tooling_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    marker = str(candidate.get("marker") or "").strip()
    title = str(candidate.get("title") or "").strip()
    content = str(candidate.get("content") or "").strip()
    if not marker or not title or not content:
        raise ValueError("memory candidate is incomplete")
    project = str(long_term_memory_cfg.get("project") or "ipet-default").strip() or "ipet-default"
    search_result = _call_basic_memory_tool(
        LONG_TERM_MEMORY_TOOL_SEARCH,
        {"query": f"{normalize_topic_id(topic_id)} {marker}", "project": project},
        tooling_cfg=tooling_cfg,
    )
    if search_result is not None and search_result.ok:
        for item in _flatten_memory_search_items(_tool_result_payload(search_result)):
            haystack = " ".join(
                [
                    _memory_item_title(item, fallback=""),
                    _memory_item_identifier(item),
                    _memory_item_excerpt(item),
                ]
            ).strip()
            if marker and normalize_topic_id(topic_id) in haystack and marker in haystack:
                return {"ok": True, "saved": False, "duplicate": True}
    write_result = _call_basic_memory_tool(
        LONG_TERM_MEMORY_TOOL_WRITE,
        {
            "project": project,
            "title": title,
            "folder": str(candidate.get("folder") or LONG_TERM_MEMORY_FOLDER),
            "tags": list(candidate.get("tags") or LONG_TERM_MEMORY_TAGS),
            "content": content,
        },
        tooling_cfg=tooling_cfg,
    )
    if write_result is None:
        raise RuntimeError("basic_memory.write_note is not available")
    if not write_result.ok:
        raise RuntimeError(str(write_result.error or "basic_memory.write_note failed"))
    return {"ok": True, "saved": True, "duplicate": False}


async def _generate_topic_summary_text(
    *,
    kind: str,
    settings_config: dict[str, Any],
    llm_provider: str,
    api_base_url: str,
    api_key: str,
    model: str,
    source_text: str,
    start_assistant_turn: int,
    end_assistant_turn: int,
) -> str:
    config = _resolve_summary_request_config(
        settings_config,
        llm_provider=llm_provider,
        api_base_url=api_base_url,
        api_key=api_key,
        model=model,
    )
    if kind == "major":
        system_prompt = (
            "You write long-horizon conversation summaries for a desktop assistant. "
            "Preserve stable user preferences, completed decisions, open tasks, constraints, rejected approaches, and important facts. "
            "Return plain text only."
        )
        user_prompt = (
            f"Write one major summary covering assistant turns {start_assistant_turn}-{end_assistant_turn}. "
            "Compress the supplied mini summaries into one durable context block.\n\n"
            f"{source_text}"
        )
    else:
        system_prompt = (
            "You write concise rolling conversation summaries for a desktop assistant. "
            "Preserve facts, user preferences, decisions, constraints, open tasks, and unresolved questions. "
            "Return plain text only."
        )
        user_prompt = (
            f"Write one mini summary covering assistant turns {start_assistant_turn}-{end_assistant_turn}. "
            "Summarize the conversation transcript below.\n\n"
            f"{source_text}"
        )
    message = await provider_chat_once(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=config["model"],
        provider=config["llm_provider"],
        base_url=config["api_base_url"],
        api_key=config["api_key"],
    )
    summary_text = _extract_message_content(message)
    if not summary_text:
        raise ValueError(f"empty {kind} summary response")
    return summary_text


async def _maybe_update_topic_summaries(
    *,
    topic_id: str,
    settings_config: dict[str, Any],
    llm_provider: str,
    api_base_url: str,
    api_key: str,
    model: str,
) -> None:
    topic_cfg = _topic_history_config(settings_config)
    if not topic_cfg["enabled"]:
        return
    store = _get_chat_topic_store()
    interval = int(topic_cfg["summary_interval_assistant_turns"] or 10)
    major_group_size = int(topic_cfg.get("major_summary_group_size") or 3)
    while True:
        candidate = store.get_pending_mini_summary(topic_id, interval)
        if candidate is None:
            break
        source_text = _topic_summary_source_text(list(candidate.get("messages") or []))
        if not source_text:
            break
        try:
            summary_text = await _generate_topic_summary_text(
                kind="mini",
                settings_config=settings_config,
                llm_provider=llm_provider,
                api_base_url=api_base_url,
                api_key=api_key,
                model=model,
                source_text=source_text,
                start_assistant_turn=int(candidate.get("start_assistant_turn") or 0),
                end_assistant_turn=int(candidate.get("end_assistant_turn") or 0),
            )
        except Exception:
            break
        store.apply_mini_summary(topic_id, candidate, summary_text)
    while True:
        major_candidate = store.get_pending_major_summary(topic_id, major_group_size)
        if major_candidate is None:
            break
        source_text = _topic_summary_blocks_text(list(major_candidate.get("blocks") or []))
        if not source_text:
            break
        try:
            summary_text = await _generate_topic_summary_text(
                kind="major",
                settings_config=settings_config,
                llm_provider=llm_provider,
                api_base_url=api_base_url,
                api_key=api_key,
                model=model,
                source_text=source_text,
                start_assistant_turn=int(major_candidate.get("start_assistant_turn") or 0),
                end_assistant_turn=int(major_candidate.get("end_assistant_turn") or 0),
            )
        except Exception:
            break
        store.apply_major_summary(topic_id, major_candidate, summary_text)


async def _finalize_chat_exchange(
    *,
    session_id: str,
    user_text: str,
    assistant_text: str,
    working_messages: list[dict[str, str]],
    memory_window: int,
    settings_config: dict[str, Any],
    llm_provider: str,
    api_base_url: str,
    api_key: str,
    model: str,
) -> dict[str, Any]:
    topic_cfg = _topic_history_config(settings_config)
    long_term_memory_cfg = _long_term_memory_config(settings_config)
    if topic_cfg["enabled"]:
        topic_id = normalize_topic_id(session_id)
        store = _get_chat_topic_store()
        previous_meta = store.load_meta(topic_id) or {}
        store.append_exchange(topic_id, user_text=user_text, assistant_text=assistant_text)
        await _maybe_update_topic_summaries(
            topic_id=topic_id,
            settings_config=settings_config,
            llm_provider=llm_provider,
            api_base_url=api_base_url,
            api_key=api_key,
            model=model,
        )
        detail = store.get_topic_detail(topic_id)
        suggestion = _build_long_term_memory_suggestion(
            detail=detail,
            previous_meta=previous_meta,
            user_text=user_text,
            assistant_text=assistant_text,
            long_term_memory_cfg=long_term_memory_cfg,
        )
        snapshot = store.get_runtime_snapshot(topic_id)
        if snapshot is not None:
            SESSION_STORE[topic_id] = [dict(item) for item in snapshot.full_messages]
        else:
            SESSION_STORE[topic_id] = store.load_full_messages(topic_id)
        if suggestion is not None and long_term_memory_cfg.get("enabled") and long_term_memory_cfg.get("write_enabled"):
            if long_term_memory_cfg.get("ask_before_save", True):
                return {"memory_save_suggestion": suggestion}
            save_result = _save_long_term_memory_candidate(
                topic_id=topic_id,
                candidate=suggestion,
                long_term_memory_cfg=long_term_memory_cfg,
                tooling_cfg=_derive_runtime_tooling_config(settings_config),
            )
            store.update_long_term_memory_marker(topic_id, saved_marker=str(suggestion.get("marker") or ""))
            return {"memory_saved": save_result}
        return {}
    updated = list(working_messages) + [{"role": "assistant", "content": assistant_text}]
    SESSION_STORE[session_id] = _trim_messages(updated, memory_window)
    return {}


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
        chat_cfg = settings_config.get("chat", {}) if isinstance(settings_config, dict) else {}
        skills_cfg = chat_cfg.get("skills", {}) if isinstance(chat_cfg, dict) else {}
        candidate_ids = _canonicalize_skill_ids(requested_skill_ids, manager=manager)
        if not candidate_ids:
            candidate_ids = _canonicalize_skill_ids(skills_cfg.get("default_active_ids"), manager=manager)
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


def _explicit_skill_request_detected(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    if not text:
        return False
    cues = (
        "用skill",
        "按skill",
        "skill里的",
        "skill 里的",
        "skill方式",
        "skill 流程",
        "skill流程",
        "use skill",
        "use the skill",
        "follow the skill",
        "技能方式",
        "技能流程",
        "按技能",
        "用技能",
    )
    return any(marker in text for marker in cues)


def _infer_forced_skill_ids_from_request(user_text: str, resolved_skills: ResolvedSkillSet) -> list[str]:
    text = str(user_text or "").strip().lower()
    if not text:
        return []
    matched: list[str] = []
    for skill in tuple(getattr(resolved_skills, "skills", ()) or ()):
        skill_id = str(getattr(skill, "skill_id", "") or "").strip()
        labels = [
            skill_id,
            str(getattr(skill, "name", "") or "").strip(),
            str(getattr(skill, "display_name", "") or "").strip(),
            *[str(item or "").strip() for item in (getattr(skill, "aliases", ()) or ())],
        ]
        if any(label and len(label) >= 2 and label.lower() in text for label in labels):
            matched.append(skill_id)
    if matched:
        return _canonicalize_skill_ids(matched, manager=_get_skill_manager())
    if _explicit_skill_request_detected(text) and len(tuple(getattr(resolved_skills, "skill_ids", ()) or ())) == 1:
        return list(getattr(resolved_skills, "skill_ids", ()) or ())
    return []


def _build_router_tool_schemas(
    chat_mode: str,
    active_skill_ids: list[str],
    *,
    settings_config: dict[str, Any] | None = None,
    tooling_cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    normalized_mode = _normalize_chat_mode(chat_mode)
    if normalized_mode == CHAT_MODE_CHAT:
        bridge = _build_runtime_tool_bridge(
            {
                "chat_mode": chat_mode,
                "active_skill_ids": list(active_skill_ids),
                "settings_config": settings_config or {},
                "tooling_config": tooling_cfg or {},
            }
        )
        if bridge is None or not hasattr(bridge, "list_tools"):
            return []
        try:
            return list(bridge.list_tools() or [])
        except Exception:
            return []
    if normalized_mode == CHAT_MODE_REACT:
        bridge = _CompositeToolBridge(
            _RegistryToolBridge(
                _build_system_tools(
                    {
                        "execution_phase": PHASE_SKILL_SELECTION,
                        "active_skill_ids": list(active_skill_ids),
                        "selection_origin": "none",
                        "selected_skill_ids": [],
                        "selected_tool_names": [],
                        "planner_excluded_skill_ids": [],
                        "planner_excluded_tool_names": [],
                        "planner_retry_used": False,
                    }
                ),
                missing_message="router system tool unavailable",
            ),
            _RegistryToolBridge(
                _build_system_tools(
                    {
                        "execution_phase": PHASE_AGENT_LOOP,
                        "active_skill_ids": list(active_skill_ids),
                        "selection_origin": "none",
                        "selected_skill_ids": [],
                        "selected_tool_names": [],
                        "planner_excluded_skill_ids": [],
                        "planner_excluded_tool_names": [],
                        "planner_retry_used": False,
                    }
                ),
                missing_message="router system tool unavailable",
            ),
        )
        return bridge.list_tools()
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


class _CompositeToolBridge:
    def __init__(self, *bridges: Any) -> None:
        self.bridges = [bridge for bridge in bridges if bridge is not None]

    def list_registered_tools(self) -> list[Tool]:
        out: list[Tool] = []
        seen: set[str] = set()
        for bridge in self.bridges:
            if bridge is None or not hasattr(bridge, "list_registered_tools"):
                continue
            try:
                tools = list(bridge.list_registered_tools() or [])
            except Exception:
                continue
            for tool in tools:
                name = str(getattr(tool, "name", "") or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                out.append(tool)
        return out

    def list_tools(self) -> list[dict[str, Any]]:
        if any(hasattr(bridge, "list_registered_tools") for bridge in self.bridges):
            return [tool.to_llm_schema() for tool in self.list_registered_tools()]
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for bridge in self.bridges:
            if bridge is None or not hasattr(bridge, "list_tools"):
                continue
            try:
                schemas = list(bridge.list_tools() or [])
            except Exception:
                continue
            for schema in schemas:
                name = _tool_name_from_schema(schema)
                if not name or name in seen:
                    continue
                seen.add(name)
                out.append(schema)
        return out

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        target = str(tool_name or "").strip()
        for bridge in self.bridges:
            if bridge is None:
                continue
            names: set[str] = set()
            if hasattr(bridge, "list_registered_tools"):
                try:
                    names = {str(getattr(tool, "name", "") or "").strip() for tool in bridge.list_registered_tools() or []}
                except Exception:
                    names = set()
            elif hasattr(bridge, "list_tools"):
                try:
                    names = {_tool_name_from_schema(schema) for schema in bridge.list_tools() or []}
                except Exception:
                    names = set()
            if target in names and hasattr(bridge, "call_tool"):
                return bridge.call_tool(target, arguments)
        return ToolResult.from_error(f"tool not available: {target}")


def _tool_name_from_schema(schema: dict[str, Any]) -> str:
    if not isinstance(schema, dict):
        return ""
    function = schema.get("function")
    if not isinstance(function, dict):
        return ""
    return str(function.get("name") or "").strip()


def _tool_name_matches_pattern(tool_name: str, pattern: str) -> bool:
    name = str(tool_name or "").strip()
    matcher = str(pattern or "").strip()
    if not name or not matcher:
        return False
    if matcher.endswith("*"):
        return name.startswith(matcher[:-1])
    if matcher.endswith("."):
        return name.startswith(matcher)
    return name == matcher or name.startswith(f"{matcher}.")


def _tokenize_search_query(text: str) -> list[str]:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return []
    return [part for part in normalized.replace("_", " ").replace("-", " ").split() if part]


def _normalize_name_list(values: Any) -> list[str]:
    items = values if isinstance(values, list) else []
    seen: set[str] = set()
    normalized: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _extract_capability_search_query(arguments: dict[str, Any] | None) -> str:
    payload = arguments if isinstance(arguments, dict) else {}
    for key in ("query", "keyword", "keywords", "task", "intent", "capability"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_capability_search_reason(arguments: dict[str, Any] | None) -> str:
    payload = arguments if isinstance(arguments, dict) else {}
    return str(payload.get("reason") or "").strip()


def _normalized_capability_search_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


def _explicit_capability_inventory_scope(value: Any) -> str:
    text = _normalized_capability_search_text(value)
    if not text:
        return ""
    list_markers = (
        "有没有",
        "有哪些",
        "可用",
        "列出",
        "枚举",
        "list",
        "show",
        "available",
        "what ",
        "which ",
    )
    search_markers = ("搜索", "搜", "查", "查看")
    visibility_markers = (
        "能不能看到",
        "能看到",
        "能不能识别",
        "识别",
        "发现",
        "现在有什么",
        "当前有什么",
    )
    skill_markers = ("skill", "skills", "技能")
    mcp_markers = ("mcp", "mcps")
    generic_markers = ("工具", "tool", "tools", "能力", "capability", "capabilities")
    list_hit = any(marker in text for marker in list_markers)
    search_hit = any(marker in text for marker in search_markers)
    visibility_hit = any(marker in text for marker in visibility_markers)
    skill_hit = any(marker in text for marker in skill_markers)
    mcp_hit = any(marker in text for marker in mcp_markers)
    generic_hit = any(marker in text for marker in generic_markers)
    if not (skill_hit or mcp_hit or generic_hit):
        return ""
    if not (list_hit or visibility_hit or (search_hit and (skill_hit or mcp_hit))):
        return ""
    if skill_hit and not mcp_hit and not generic_hit:
        return "skill"
    if mcp_hit and not skill_hit and not generic_hit:
        return "mcp"
    if skill_hit and not mcp_hit:
        return "skill"
    if mcp_hit and not skill_hit:
        return "mcp"
    return "both"


def _inventory_summary_text(scope: str, skill_catalog: list[dict[str, Any]], tool_catalog: list[dict[str, Any]]) -> str:
    skill_count = len(skill_catalog)
    tool_count = len(tool_catalog)
    normalized_scope = str(scope or "both").strip().lower() or "both"
    if normalized_scope == "skill":
        if skill_count:
            return f"I found {skill_count} planner-visible skill(s) in the current settings."
        return "I checked the current settings and did not find any planner-visible skills."
    if normalized_scope == "mcp":
        if tool_count:
            return f"I found {tool_count} runtime-available non-skill MCP tool(s)."
        return "I checked the current runtime and did not find any available non-skill MCP tools."
    if skill_count or tool_count:
        return f"I listed the currently available capabilities: {skill_count} skill(s) and {tool_count} non-skill MCP tool(s)."
    return "I checked the current environment and did not enumerate any planner-visible skills or non-skill MCP tools."


def _list_non_skill_tools(tooling_cfg: dict[str, Any] | None = None) -> list[Tool]:
    bridge = _get_mcp_bridge_for_tooling(tooling_cfg) if isinstance(tooling_cfg, dict) else _get_mcp_bridge()
    if bridge is None or not hasattr(bridge, "list_registered_tools"):
        return []
    try:
        tools = list(bridge.list_registered_tools() or [])
    except Exception:
        return []
    out: list[Tool] = []
    for tool in tools:
        name = str(getattr(tool, "name", "") or "").strip()
        if not name or name.startswith("skill.") or name.startswith(LONG_TERM_MEMORY_TOOL_PREFIX):
            continue
        out.append(tool)
    return out


def _build_capability_search_request(
    arguments: dict[str, Any] | None,
    *,
    state_excluded_skill_ids: list[str],
    state_excluded_tool_names: list[str],
) -> dict[str, Any]:
    payload = arguments if isinstance(arguments, dict) else {}
    query = _extract_capability_search_query(payload)
    inventory_scope = _explicit_capability_inventory_scope(query)
    return {
        "kind": "capability_search",
        "task": query,
        "query": query,
        "reason": _extract_capability_search_reason(payload),
        "mode": "inventory" if inventory_scope else "plan",
        "inventory_scope": inventory_scope,
        "exclude_skill_ids": _canonicalize_skill_ids(
            _normalize_skill_ids(payload.get("exclude_skill_ids")) + list(state_excluded_skill_ids),
            manager=_get_skill_manager(),
        ),
        "exclude_tool_names": _normalize_name_list(payload.get("exclude_tool_names")) + [
            item
            for item in state_excluded_tool_names
            if item not in _normalize_name_list(payload.get("exclude_tool_names"))
        ],
    }


def _capability_search_tool_name_list_for_skill(
    resolved: ResolvedSkillSet,
    *,
    available_mcp_tools: list[Tool],
) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for tool in [*list(resolved.resource_tools), *list(resolved.adapter_tools), *list(resolved.script_tools)]:
        name = str(getattr(tool, "name", "") or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    for pattern in (resolved.tool_allowlist or []):
        matcher = str(pattern or "").strip()
        if not matcher:
            continue
        for tool in available_mcp_tools:
            tool_name = str(getattr(tool, "name", "") or "").strip()
            if not tool_name or tool_name in seen:
                continue
            if tool_name_matches_pattern(tool_name, matcher):
                seen.add(tool_name)
                names.append(tool_name)
    return names


def _build_skill_capability_catalog(tooling_cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    manager = _get_skill_manager()
    runtime = _get_skill_runtime()
    available_mcp_tools = _list_non_skill_tools(tooling_cfg)
    items: list[dict[str, Any]] = []
    for record in manager.list_skills():
        skill_id = str(getattr(record, "skill_id", "") or "").strip()
        if not skill_id or not bool(getattr(record, "ok", False)):
            continue
        resolved = runtime.resolve_active_skills([skill_id], default_active_ids=[], enabled=True)
        tool_names = _capability_search_tool_name_list_for_skill(resolved, available_mcp_tools=available_mcp_tools)
        items.append(
            {
                "skill_id": skill_id,
                "display_name": str(getattr(record, "display_name", "") or getattr(record, "name", "") or skill_id).strip(),
                "description": str(getattr(record, "short_description", "") or getattr(record, "description", "") or "").strip(),
                "tool_names": tool_names,
                "prompt_excerpt": _compact_text(str(getattr(record, "prompt_body", "") or "").strip(), 220),
                "source": str(getattr(record, "source_type", "") or "").strip(),
            }
        )
    return items


def _build_mcp_capability_catalog(tooling_cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for tool in _list_non_skill_tools(tooling_cfg):
        name = str(getattr(tool, "name", "") or "").strip()
        if not name:
            continue
        items.append(
            {
                "name": name,
                "description": str(getattr(tool, "description", "") or "").strip(),
                "source": str(getattr(tool, "source", "") or "").strip(),
            }
        )
    return items


def _classify_mcp_task_type(tool_name: str) -> str:
    name = str(tool_name or "").strip().lower()
    leaf = name.split(".")[-1]
    if name.startswith("playwright") or "browser_" in name or leaf.startswith("browser_"):
        return "browser_automation"
    if name.startswith("tavily") or any(token in name for token in ("search", "fetch", "crawl")):
        return "web_search"
    file_prefixes = (
        "read",
        "write",
        "edit",
        "list",
        "move",
        "copy",
        "mkdir",
        "glob",
        "stat",
        "delete",
        "remove",
    )
    if any(leaf.startswith(prefix) for prefix in file_prefixes):
        return "file_io"
    return "general_mcp"


def _build_mcp_task_type_catalog(tooling_cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _build_mcp_capability_catalog(tooling_cfg):
        enriched = dict(item)
        enriched["task_type"] = _classify_mcp_task_type(str(item.get("name") or ""))
        items.append(enriched)
    return items


def _build_system_tools(state: dict[str, Any]) -> list[Tool]:
    phase = str(state.get("execution_phase") or "").strip().lower()
    tools: list[Tool] = []
    if phase == PHASE_SKILL_SELECTION:
        tools.append(
            Tool(
                name=SYSTEM_TOOL_AGENT_LOOP,
                description="Fallback to the general agent loop when no current visible skill can solve the task.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "Restate the task to continue in the general agent loop."},
                        "preferred_tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional soft hints for what kind of tools may help.",
                        },
                        "max_steps": {"type": "integer", "description": "Optional step budget hint for the fallback loop."},
                    },
                    "required": ["task"],
                },
                invoke=lambda arguments: {
                    "kind": "agent_loop",
                    "task": str((arguments or {}).get("task") or ""),
                    "preferred_tools": [str(item).strip() for item in ((arguments or {}).get("preferred_tools") or []) if str(item).strip()],
                    "max_steps": int((arguments or {}).get("max_steps") or 0),
                },
                source="system",
            )
        )
    return tools


def _build_skill_only_bridge(
    skill_ids: list[str],
    *,
    missing_message: str,
    resolved: ResolvedSkillSet | None = None,
    settings_config: dict[str, Any] | None = None,
) -> Any:
    active_resolved = resolved or _resolve_request_skills(
        skill_ids,
        settings=settings_config,
        chat_mode=CHAT_MODE_REACT,
    )
    return _RegistryToolBridge(
        list(active_resolved.resource_tools) + list(active_resolved.adapter_tools) + list(active_resolved.script_tools),
        missing_message=missing_message,
    )


def _build_allowlisted_bridge(
    patterns: list[str] | tuple[str, ...],
    *,
    missing_message: str,
    base_bridge: Any | None = None,
) -> Any:
    normalized = [str(item or "").strip() for item in patterns if str(item or "").strip()]
    if not normalized:
        return None
    return _FilteredToolBridge(
        base_bridge or _get_mcp_bridge(),
        allow_predicate=lambda name, allowed=tuple(normalized): any(_tool_name_matches_pattern(str(name or ""), pattern) for pattern in allowed),
        missing_message=missing_message,
    )


def _build_tool_name_bridge(tool_names: set[str], *, missing_message: str, base_bridge: Any | None = None) -> Any:
    if not tool_names:
        return None
    return _FilteredToolBridge(
        base_bridge or _get_mcp_bridge(),
        allow_predicate=lambda name, allowed=set(tool_names): str(name or "").strip() in allowed,
        missing_message=missing_message,
    )


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
        chat["max_reasoning_steps"] = max(1, int(chat.get("max_reasoning_steps", 10)))
    except Exception:
        chat["max_reasoning_steps"] = 10
    chat["system_prompt"] = str(chat.get("system_prompt") or "")
    skills_cfg = chat.get("skills", {})
    if not isinstance(skills_cfg, dict):
        skills_cfg = {}
    skills_cfg["enabled"] = bool(skills_cfg.get("enabled", True))
    skills_cfg["default_active_ids"] = _canonicalize_skill_ids(skills_cfg.get("default_active_ids"))
    chat["skills"] = skills_cfg
    topic_history_cfg = chat.get("topic_history", {})
    if not isinstance(topic_history_cfg, dict):
        topic_history_cfg = {}
    topic_history_cfg["enabled"] = bool(topic_history_cfg.get("enabled", True))
    try:
        topic_history_cfg["summary_interval_assistant_turns"] = max(1, int(topic_history_cfg.get("summary_interval_assistant_turns", 10)))
    except Exception:
        topic_history_cfg["summary_interval_assistant_turns"] = 10
    chat["topic_history"] = topic_history_cfg
    long_term_memory_cfg = chat.get("long_term_memory", {})
    if not isinstance(long_term_memory_cfg, dict):
        long_term_memory_cfg = {}
    long_term_memory_cfg["enabled"] = bool(long_term_memory_cfg.get("enabled", False))
    long_term_memory_cfg["project"] = str(long_term_memory_cfg.get("project") or "ipet-default").strip() or "ipet-default"
    long_term_memory_cfg["read_enabled"] = bool(long_term_memory_cfg.get("read_enabled", True))
    long_term_memory_cfg["write_enabled"] = bool(long_term_memory_cfg.get("write_enabled", True))
    long_term_memory_cfg["ask_before_save"] = bool(long_term_memory_cfg.get("ask_before_save", True))
    long_term_memory_cfg["prefer_topic_history"] = bool(long_term_memory_cfg.get("prefer_topic_history", True))
    long_term_memory_cfg["save_from_major_summary"] = bool(long_term_memory_cfg.get("save_from_major_summary", True))
    long_term_memory_cfg["save_on_explicit_request"] = bool(long_term_memory_cfg.get("save_on_explicit_request", True))
    chat["long_term_memory"] = long_term_memory_cfg
    asr_cfg = chat.get("asr", {})
    if not isinstance(asr_cfg, dict):
        asr_cfg = {}
    default_asr_config = _default_asr_config()
    asr_cfg["enabled"] = bool(asr_cfg.get("enabled", default_asr_config["enabled"]))
    provider = str(asr_cfg.get("provider") or DEFAULT_ASR_PROVIDER).strip() or DEFAULT_ASR_PROVIDER
    asr_cfg["provider"] = provider if provider == DEFAULT_ASR_PROVIDER else DEFAULT_ASR_PROVIDER
    asr_cfg["api_base_url"] = str(asr_cfg.get("api_base_url") or default_asr_config["api_base_url"]).strip() or DEFAULT_ASR_API_BASE_URL
    asr_cfg["push_to_talk_key"] = normalize_push_to_talk_key(asr_cfg.get("push_to_talk_key"))
    asr_cfg["interim_results"] = bool(asr_cfg.get("interim_results", default_asr_config["interim_results"]))
    chat["asr"] = asr_cfg

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
    return _derive_settings_config(_load_full_config())


def _derive_settings_config(raw_config: dict[str, Any]) -> dict[str, Any]:
    return _normalize_settings_config(raw_config if isinstance(raw_config, dict) else {})


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
    return _derive_runtime_tooling_config(_load_full_config())


def _derive_runtime_tooling_config(data: dict[str, Any]) -> dict[str, Any]:
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
    tooling_cfg = _load_runtime_tooling_config()
    return _get_mcp_bridge_for_tooling(tooling_cfg, force_reload=force_reload)


def _get_mcp_bridge_for_tooling(tooling_cfg: dict[str, Any], force_reload: bool = False) -> MCPBridge:
    global _MCP_BRIDGE, _MCP_CONFIG_SNAPSHOT
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


def _get_chat_topic_store(force_reload: bool = False) -> TopicStore:
    global _CHAT_TOPIC_STORE
    if force_reload or _CHAT_TOPIC_STORE is None:
        _CHAT_TOPIC_STORE = TopicStore(CHAT_TOPICS_ROOT)
    return _CHAT_TOPIC_STORE


def _get_asr_service(force_reload: bool = False) -> ASRService:
    global _ASR_SERVICE
    if force_reload or _ASR_SERVICE is None:
        _ASR_SERVICE = ASRService()
    return _ASR_SERVICE


def _ensure_internal_asr_warmup_started() -> tuple[bool, str]:
    global _ASR_WARMUP_TASK
    service = _get_asr_service()
    readiness_message = service.readiness_message()
    if not readiness_message:
        return False, ""
    if _ASR_WARMUP_TASK is not None and not _ASR_WARMUP_TASK.done():
        return False, readiness_message
    loop = asyncio.get_running_loop()
    _ASR_WARMUP_TASK = loop.create_task(service.warmup())
    return True, "ASR 正在加载模型，请稍后再试。"


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


def _normalized_runtime_phase(state: dict[str, Any]) -> str:
    phase = str(state.get("execution_phase") or PHASE_SKILL_SELECTION).strip().lower()
    selected_skill_ids = _normalize_skill_ids(state.get("selected_skill_ids"))
    if phase == PHASE_SKILL_SELECTION and selected_skill_ids:
        phase = PHASE_SKILL_EXECUTION
    return phase


def _tool_bridge_cache_key(state: dict[str, Any]) -> str:
    turn_id = str(state.get("turn_id") or "").strip()
    if not turn_id:
        return ""
    payload = {
        "turn_id": turn_id,
        "chat_mode": _normalize_chat_mode(state.get("chat_mode")),
        "execution_phase": _normalized_runtime_phase(state),
        "active_skill_ids": _normalize_skill_ids(state.get("active_skill_ids")),
        "selection_origin": str(state.get("selection_origin") or "none").strip().lower() or "none",
        "selected_skill_ids": _normalize_skill_ids(state.get("selected_skill_ids")),
        "selected_tool_names": _normalize_name_list(state.get("selected_tool_names")),
        "planner_excluded_skill_ids": _normalize_skill_ids(state.get("planner_excluded_skill_ids")),
        "planner_excluded_tool_names": _normalize_name_list(state.get("planner_excluded_tool_names")),
        "planner_retry_used": bool(state.get("planner_retry_used", False)),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _cache_turn_tool_bridge(state: dict[str, Any], bridge: Any) -> Any:
    cache_key = _tool_bridge_cache_key(state)
    if cache_key:
        _TURN_TOOL_BRIDGE_CACHE[cache_key] = bridge
    return bridge


def _get_cached_turn_tool_bridge(state: dict[str, Any]) -> Any | None:
    cache_key = _tool_bridge_cache_key(state)
    if not cache_key:
        return None
    return _TURN_TOOL_BRIDGE_CACHE.get(cache_key)


def _clear_turn_tool_bridge_cache(turn_id: str) -> None:
    target = str(turn_id or "").strip()
    if not target:
        return
    for key in [key for key in _TURN_TOOL_BRIDGE_CACHE if f'"turn_id": "{target}"' in key]:
        _TURN_TOOL_BRIDGE_CACHE.pop(key, None)


def _build_bridge_from_resolved_skills(resolved: ResolvedSkillSet, *, missing_message: str, base_bridge: Any | None = None) -> Any:
    skill_bridge = _RegistryToolBridge(
        list(resolved.resource_tools) + list(resolved.adapter_tools) + list(resolved.script_tools),
        missing_message=missing_message,
    )
    allowlisted_bridge = _build_allowlisted_bridge(
        list(resolved.tool_allowlist),
        missing_message=missing_message,
        base_bridge=base_bridge,
    )
    return _CompositeToolBridge(skill_bridge, allowlisted_bridge)


def _prime_turn_tool_bridge_cache(
    *,
    turn_id: str,
    chat_mode: str,
    settings_config: dict[str, Any],
    tooling_cfg: dict[str, Any],
    active_skill_ids: list[str],
    active_resolved_skills: ResolvedSkillSet,
    selection_origin: str,
    selected_skill_ids: list[str],
    selected_resolved_skills: ResolvedSkillSet | None,
) -> None:
    normalized_mode = _normalize_chat_mode(chat_mode)
    state_base = {
        "turn_id": turn_id,
        "chat_mode": normalized_mode,
        "settings_config": settings_config,
        "tooling_config": tooling_cfg,
        "active_skill_ids": list(active_skill_ids),
        "selection_origin": str(selection_origin or "none").strip().lower() or "none",
        "selected_skill_ids": list(selected_skill_ids),
        "selected_tool_names": [],
        "planner_excluded_skill_ids": [],
        "planner_excluded_tool_names": [],
        "planner_retry_used": False,
    }
    base_mcp_bridge = _get_mcp_bridge_for_tooling(tooling_cfg)
    if normalized_mode == CHAT_MODE_CHAT:
        _cache_turn_tool_bridge(
            {
                **state_base,
                "execution_phase": "",
            },
            _FilteredToolBridge(
                base_mcp_bridge,
                allow_predicate=lambda name: str(name or "").startswith(CHAT_MODE_TAVILY_TOOL_PREFIX),
                missing_message="tool not available in chat mode",
            ),
        )
        return
    if normalized_mode == CHAT_MODE_SKILL:
        _cache_turn_tool_bridge(
            {
                **state_base,
                "execution_phase": "",
            },
            _build_bridge_from_resolved_skills(
                active_resolved_skills,
                missing_message="tool not available in skill mode",
                base_bridge=base_mcp_bridge,
            ),
        )
        return
    system_bridge = _RegistryToolBridge(
        _build_system_tools(
            {
                "execution_phase": PHASE_SKILL_SELECTION,
                "active_skill_ids": list(active_skill_ids),
                "selection_origin": "none",
                "selected_skill_ids": [],
                "selected_tool_names": [],
                "planner_excluded_skill_ids": [],
                "planner_excluded_tool_names": [],
                "planner_retry_used": False,
            }
        ),
        missing_message="system tool unavailable",
    )
    _cache_turn_tool_bridge(
        {
            **state_base,
            "execution_phase": PHASE_SKILL_SELECTION,
            "selection_origin": "none",
            "selected_skill_ids": [],
        },
        _CompositeToolBridge(
            _build_bridge_from_resolved_skills(
                active_resolved_skills,
                missing_message="tool not available in skill selection",
                base_bridge=None,
            ),
            system_bridge,
        ),
    )
    if selected_resolved_skills is not None and selected_skill_ids:
        _cache_turn_tool_bridge(
            {
                **state_base,
                "execution_phase": PHASE_SKILL_EXECUTION,
                "selection_origin": str(selection_origin or "none").strip().lower() or "none",
                "selected_skill_ids": list(selected_skill_ids),
            },
            _build_bridge_from_resolved_skills(
                selected_resolved_skills,
                missing_message="tool not available in active skill execution",
                base_bridge=base_mcp_bridge,
            ),
        )


def _resolve_capability_planner_config(state: dict[str, Any]) -> dict[str, str]:
    settings_config = state.get("settings_config") if isinstance(state.get("settings_config"), dict) else _load_settings_config()
    return _resolve_summary_request_config(
        settings_config,
        llm_provider=str(state.get("llm_provider") or "ollama"),
        api_base_url=str(state.get("api_base_url") or ""),
        api_key=str(state.get("api_key") or ""),
        model=str(state.get("model") or "qwen3:8b"),
    )


def _resolve_selected_skill_prompt_text(skill_ids: list[str], state: dict[str, Any]) -> str:
    if not skill_ids:
        return ""
    settings_config = state.get("settings_config") if isinstance(state.get("settings_config"), dict) else None
    resolved = _resolve_request_skills(
        skill_ids,
        settings=settings_config,
        chat_mode=_normalize_chat_mode(state.get("chat_mode")),
    )
    return str(getattr(resolved, "prompt_text", "") or "").strip()


def _planner_exposed_skill_ids(state: dict[str, Any], *, manager: SkillManager | None = None) -> list[str]:
    settings_config = state.get("settings_config") if isinstance(state.get("settings_config"), dict) else _load_settings_config()
    chat_cfg = settings_config.get("chat", {}) if isinstance(settings_config, dict) else {}
    skills_cfg = chat_cfg.get("skills", {}) if isinstance(chat_cfg, dict) else {}
    return _canonicalize_skill_ids(skills_cfg.get("default_active_ids"), manager=manager)


def _build_capability_inventory_payload(
    state: dict[str, Any],
    request_payload: dict[str, Any],
    *,
    tooling_cfg: dict[str, Any],
    manager: SkillManager,
) -> dict[str, Any]:
    scope = str(request_payload.get("inventory_scope") or "both").strip().lower() or "both"
    planner_visible_skill_ids = set(_planner_exposed_skill_ids(state, manager=manager))
    skill_catalog = [
        item
        for item in _build_skill_capability_catalog(tooling_cfg)
        if str(item.get("skill_id") or "").strip() in planner_visible_skill_ids
    ]
    tool_catalog = list(_build_mcp_capability_catalog(tooling_cfg))
    if scope == "skill":
        tool_catalog = []
    elif scope == "mcp":
        skill_catalog = []
    inventory_summary = _inventory_summary_text(scope, skill_catalog, tool_catalog)
    return {
        "mode": "inventory",
        "selection_kind": "none",
        "skill_ids": [],
        "tool_names": [],
        "query": str(request_payload.get("query") or ""),
        "thought_summary": inventory_summary,
        "reason": str(request_payload.get("reason") or "Explicit capability inventory request."),
        "inventory_scope": scope,
        "inventory_skill_ids": [str(item.get("skill_id") or "").strip() for item in skill_catalog if str(item.get("skill_id") or "").strip()],
        "inventory_tool_names": [str(item.get("name") or "").strip() for item in tool_catalog if str(item.get("name") or "").strip()],
        "inventory_summary": inventory_summary,
        "inventory_skills": skill_catalog,
        "inventory_tools": tool_catalog,
    }


async def _search_capabilities_for_state(state: dict[str, Any], arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    tooling_cfg = state.get("tooling_config") if isinstance(state.get("tooling_config"), dict) else _load_runtime_tooling_config()
    manager = _get_skill_manager()
    request_payload = _build_capability_search_request(
        arguments,
        state_excluded_skill_ids=[],
        state_excluded_tool_names=[],
    )
    if str(request_payload.get("mode") or "").strip().lower() == "inventory":
        return _build_capability_inventory_payload(
            state,
            request_payload,
            tooling_cfg=tooling_cfg,
            manager=manager,
        )

    planner_visible_skill_ids = set(_planner_exposed_skill_ids(state, manager=manager))
    skill_catalog = [
        item
        for item in _build_skill_capability_catalog(tooling_cfg)
        if str(item.get("skill_id") or "").strip() in planner_visible_skill_ids
    ]
    tool_catalog = list(_build_mcp_task_type_catalog(tooling_cfg))
    planner_cfg = _resolve_capability_planner_config(state)
    result = await search_capabilities(
        messages=list(state.get("followup_messages") or state.get("decision_messages") or state.get("working_messages") or []),
        model=planner_cfg["model"],
        skill_catalog=skill_catalog,
        tool_catalog=tool_catalog,
        llm_provider=planner_cfg["llm_provider"],
        api_base_url=planner_cfg["api_base_url"],
        api_key=planner_cfg["api_key"],
    )
    payload = result.to_dict()
    if not payload.get("query"):
        payload["query"] = str(request_payload.get("query") or "")
    return payload


async def _build_execution_plan_for_state(state: dict[str, Any], searcher_result: dict[str, Any]) -> dict[str, Any]:
    tooling_cfg = state.get("tooling_config") if isinstance(state.get("tooling_config"), dict) else _load_runtime_tooling_config()
    mode = str(searcher_result.get("mode") or "task_types").strip().lower() or "task_types"
    planner_cfg = _resolve_capability_planner_config(state)
    resolved_skill_prompt = ""
    tool_catalog: list[dict[str, Any]] = []
    if mode == "skill":
        skill_id = str(searcher_result.get("skill_id") or "").strip()
        if skill_id:
            resolved_skill_prompt = _resolve_selected_skill_prompt_text([skill_id], state)
    else:
        matched_tool_names = set(_normalize_name_list(searcher_result.get("matched_tool_names")))
        tool_catalog = [
            item
            for item in _build_mcp_task_type_catalog(tooling_cfg)
            if str(item.get("name") or "").strip() in matched_tool_names
        ]
    plan = await build_execution_plan(
        messages=list(state.get("followup_messages") or state.get("decision_messages") or state.get("working_messages") or []),
        model=planner_cfg["model"],
        searcher_result=dict(searcher_result or {}),
        resolved_skill_prompt=resolved_skill_prompt,
        tool_catalog=tool_catalog,
        llm_provider=planner_cfg["llm_provider"],
        api_base_url=planner_cfg["api_base_url"],
        api_key=planner_cfg["api_key"],
    )
    return plan.to_dict()


async def _plan_capabilities_for_state(state: dict[str, Any], arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    tooling_cfg = state.get("tooling_config") if isinstance(state.get("tooling_config"), dict) else _load_runtime_tooling_config()
    manager = _get_skill_manager()
    request_payload = _build_capability_search_request(
        arguments,
        state_excluded_skill_ids=_normalize_skill_ids(state.get("planner_excluded_skill_ids")),
        state_excluded_tool_names=_normalize_name_list(state.get("planner_excluded_tool_names")),
    )
    if str(request_payload.get("mode") or "").strip().lower() == "inventory":
        return _build_capability_inventory_payload(
            state,
            request_payload,
            tooling_cfg=tooling_cfg,
            manager=manager,
        )
    excluded_skill_ids = _canonicalize_skill_ids(request_payload.get("exclude_skill_ids"), manager=manager)
    planner_visible_skill_ids = _planner_exposed_skill_ids(state, manager=manager)
    excluded_tool_names = _normalize_name_list(request_payload.get("exclude_tool_names"))
    skill_catalog = [
        item
        for item in _build_skill_capability_catalog(tooling_cfg)
        if (
            str(item.get("skill_id") or "").strip() in set(planner_visible_skill_ids)
            and str(item.get("skill_id") or "").strip() not in set(excluded_skill_ids)
        )
    ]
    tool_catalog = [
        item
        for item in _build_mcp_capability_catalog(tooling_cfg)
        if str(item.get("name") or "").strip() not in set(excluded_tool_names)
    ]
    planner_cfg = _resolve_capability_planner_config(state)
    plan = await plan_capabilities(
        messages=list(state.get("followup_messages") or state.get("decision_messages") or state.get("working_messages") or []),
        model=planner_cfg["model"],
        skill_catalog=skill_catalog,
        tool_catalog=tool_catalog,
        exclude_skill_ids=excluded_skill_ids,
        exclude_tool_names=excluded_tool_names,
        llm_provider=planner_cfg["llm_provider"],
        api_base_url=planner_cfg["api_base_url"],
        api_key=planner_cfg["api_key"],
    )
    payload = plan.to_dict()
    if not payload.get("query"):
        payload["query"] = str(request_payload.get("query") or "")
    return payload


def _log_perf_event(
    event: str,
    *,
    session_id: str,
    chat_mode: str,
    router_used: bool,
    route_kind: str,
    topic_history_enabled: bool,
    timings_ms: dict[str, float],
    ok: bool,
    error: str = "",
) -> None:
    payload = {
        "event": event,
        "session_id": session_id,
        "chat_mode": chat_mode,
        "router_used": bool(router_used),
        "route_kind": route_kind,
        "topic_history_enabled": bool(topic_history_enabled),
        "ok": bool(ok),
        "error": str(error or ""),
        "timings_ms": {key: round(float(value), 3) for key, value in sorted(timings_ms.items())},
    }
    LOGGER.info(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _ensure_perf_keys(perf: PerfTracker, *keys: str) -> None:
    for key in keys:
        perf.timings_ms.setdefault(str(key), 0.0)


def _exception_message(exc: Exception, fallback: str) -> str:
    text = str(exc or "").strip()
    if text:
        return text
    name = type(exc).__name__.strip()
    return name or str(fallback or "internal error")


def _build_chat_request_context(req: ChatStreamRequest, perf: PerfTracker) -> ChatRequestContext:
    raw_config = perf.time_call("load_config", _load_full_config)
    derive_started = time.perf_counter()
    settings_config = _derive_settings_config(raw_config)
    tooling_cfg = _derive_runtime_tooling_config(raw_config)
    perf.add("derive_settings_tooling", time.perf_counter() - derive_started)
    text = req.text.strip()
    raw_session_id = str(req.session_id or "").strip()
    chat_mode = _normalize_chat_mode(req.chat_mode)
    topic_history_cfg = _topic_history_config(settings_config)
    long_term_memory_cfg = _long_term_memory_config(settings_config)
    session_id = normalize_topic_id(raw_session_id or "default") if topic_history_cfg["enabled"] else (raw_session_id or "default")
    router_cfg = _resolve_router_request_config(req, settings_config)
    resolved_skills = perf.time_call(
        "resolve_skills",
        _resolve_request_skills,
        req.skill_ids,
        settings_config,
        chat_mode=chat_mode,
    )
    topic_snapshot = None
    if topic_history_cfg["enabled"]:
        store = _get_chat_topic_store()
        topic_snapshot = perf.time_call("load_topic_snapshot", store.get_runtime_snapshot, session_id)
        if topic_snapshot is not None:
            history_messages = [dict(item) for item in topic_snapshot.model_messages]
            SESSION_STORE[session_id] = [dict(item) for item in topic_snapshot.full_messages]
        else:
            history_messages = _trim_messages(SESSION_STORE.get(session_id, []), req.memory_window)
    else:
        perf.add("load_topic_snapshot", 0.0)
        history_messages = _trim_messages(SESSION_STORE.get(session_id, []), req.memory_window)
    long_term_memory_message = perf.time_call(
        "load_long_term_memory",
        _build_long_term_memory_message,
        user_text=text,
        topic_snapshot=topic_snapshot,
        long_term_memory_cfg=long_term_memory_cfg,
        tooling_cfg=tooling_cfg,
    )
    if long_term_memory_message is not None:
        history_messages = list(history_messages) + [long_term_memory_message]
    working_messages = list(history_messages) + [{"role": "user", "content": text}]
    return ChatRequestContext(
        raw_config=raw_config,
        settings_config=settings_config,
        tooling_cfg=tooling_cfg,
        topic_history_cfg=topic_history_cfg,
        router_cfg=router_cfg,
        resolved_skills=resolved_skills,
        session_id=session_id,
        pet_display_name=_extract_pet_display_name(req.system_prompt),
        history_messages=list(history_messages),
        working_messages=working_messages,
        topic_snapshot=topic_snapshot,
    )


def _build_runtime_tool_bridge(state: dict[str, Any]) -> Any:
    cached = _get_cached_turn_tool_bridge(state)
    if cached is not None:
        return cached

    chat_mode = _normalize_chat_mode(state.get("chat_mode"))
    settings_config = state.get("settings_config") if isinstance(state.get("settings_config"), dict) else None
    tooling_cfg = state.get("tooling_config") if isinstance(state.get("tooling_config"), dict) else None
    base_mcp_bridge: Any | None = None

    def _base_bridge() -> Any:
        nonlocal base_mcp_bridge
        if base_mcp_bridge is None:
            base_mcp_bridge = _get_mcp_bridge_for_tooling(tooling_cfg) if tooling_cfg else _get_mcp_bridge()
        return base_mcp_bridge

    if chat_mode == CHAT_MODE_CHAT:
        return _cache_turn_tool_bridge(
            state,
            _FilteredToolBridge(
                _base_bridge(),
                allow_predicate=lambda name: str(name or "").startswith(CHAT_MODE_TAVILY_TOOL_PREFIX),
                missing_message="tool not available in chat mode",
            ),
        )

    skill_ids = _normalize_skill_ids(state.get("active_skill_ids"))
    if chat_mode == CHAT_MODE_SKILL:
        resolved = _resolve_request_skills(skill_ids, settings=settings_config, chat_mode=chat_mode)
        return _cache_turn_tool_bridge(
            state,
            _build_bridge_from_resolved_skills(
                resolved,
                missing_message="tool not available in skill mode",
                base_bridge=_base_bridge(),
            ),
        )

    phase = _normalized_runtime_phase(state)
    selected_skill_ids = _normalize_skill_ids(state.get("selected_skill_ids"))
    selected_tool_names = set(_normalize_name_list(state.get("selected_tool_names")))
    system_tools = _build_system_tools(state)
    system_bridge = _RegistryToolBridge(system_tools, missing_message="system tool unavailable")

    if phase == PHASE_AGENT_LOOP:
        if selected_tool_names:
            selected_bridge = _build_tool_name_bridge(
                selected_tool_names,
                missing_message="tool not available in planner-selected MCP handoff",
                base_bridge=_base_bridge(),
            )
            return _cache_turn_tool_bridge(state, _CompositeToolBridge(selected_bridge, system_bridge))
        runtime_tool_names = {
            str(getattr(tool, "name", "") or "").strip()
            for tool in _list_non_skill_tools(tooling_cfg)
            if str(getattr(tool, "name", "") or "").strip()
        }
        runtime_bridge = _build_tool_name_bridge(
            runtime_tool_names,
            missing_message="tool not available in agent loop",
            base_bridge=_base_bridge(),
        )
        if runtime_bridge is not None and system_tools:
            return _cache_turn_tool_bridge(state, _CompositeToolBridge(runtime_bridge, system_bridge))
        if runtime_bridge is not None:
            return _cache_turn_tool_bridge(state, runtime_bridge)
        return _cache_turn_tool_bridge(state, system_bridge)

    if phase == PHASE_SKILL_EXECUTION and selected_skill_ids:
        resolved = _resolve_request_skills(selected_skill_ids, settings=settings_config, chat_mode=chat_mode)
        skill_bridge = _build_bridge_from_resolved_skills(
            resolved,
            missing_message="tool not available in active skill execution",
            base_bridge=_base_bridge(),
        )
        return _cache_turn_tool_bridge(
            state,
            _CompositeToolBridge(skill_bridge, system_bridge)
            if str(state.get("selection_origin") or "none").strip().lower() == "planner"
            else skill_bridge,
        )

    visible_skill_ids = _canonicalize_skill_ids(skill_ids, manager=_get_skill_manager())
    resolved_visible = _resolve_request_skills(visible_skill_ids, settings=settings_config, chat_mode=chat_mode)
    skill_bridge = _build_skill_only_bridge(
        visible_skill_ids,
        missing_message="tool not available in skill selection",
        resolved=resolved_visible,
        settings_config=settings_config,
    )
    return _cache_turn_tool_bridge(state, _CompositeToolBridge(skill_bridge, system_bridge))


def _agent_graph_dependencies() -> GraphDependencies:
    return GraphDependencies(
        decide_turn=decide_turn,
        search_capabilities=_search_capabilities_for_state,
        build_execution_plan=_build_execution_plan_for_state,
        plan_capabilities=_plan_capabilities_for_state,
        execute_tool_calls=execute_tool_calls,
        get_mcp_bridge=_get_mcp_bridge,
        load_tooling_config=_load_runtime_tooling_config,
        build_tool_bridge=_build_runtime_tool_bridge,
        resolve_skill_prompt_text=_resolve_selected_skill_prompt_text,
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
    global _AGENT_GRAPH_RUNTIME, _SKILL_MANAGER, _CHAT_TOPIC_STORE, _ASR_SERVICE, _ASR_WARMUP_TASK
    _AGENT_GRAPH_RUNTIME = None
    _SKILL_MANAGER = None
    _CHAT_TOPIC_STORE = None
    _ASR_SERVICE = None
    _ASR_WARMUP_TASK = None
    _TURN_TOOL_BRIDGE_CACHE.clear()
    PENDING_CHAT_TURNS.clear()
    try:
        AGENT_GRAPH_CHECKPOINT_PATH.unlink()
    except FileNotFoundError:
        pass


def _has_chat_mode_tavily_tools(tooling_cfg: dict[str, Any] | None = None) -> bool:
    bridge = _FilteredToolBridge(
        _get_mcp_bridge_for_tooling(tooling_cfg) if isinstance(tooling_cfg, dict) else _get_mcp_bridge(),
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


def _builtin_local_mcp_tools() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for item in getattr(LocalMCPServer, "TOOL_SPECS", []) or []:
        if isinstance(item, dict):
            tools.append(json.loads(json.dumps(item, ensure_ascii=False)))
    return tools


def _builtin_local_mcp_server_status(tooling_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = tooling_cfg if isinstance(tooling_cfg, dict) else _load_runtime_tooling_config()
    allowlist = [
        str(item)
        for item in normalize_file_allowlist(
            cfg.get("file_allowlist"),
            default_paths=[str(ROOT_DIR)],
        )
    ]
    enabled = bool(cfg.get("enabled", True))
    return {
        "name": "builtin_file_tools",
        "enabled": enabled,
        "builtin": True,
        "managed": False,
        "version": "builtin",
        "runtime": "inproc",
        "source_type": "builtin",
        "source": "backend.mcp.local_server",
        "manifest_path": "",
        "install_status": "ready",
        "health_status": "online" if enabled else "disabled",
        "tools": _builtin_local_mcp_tools(),
        "allowlist": allowlist,
        "error": "",
    }


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
    servers: list[dict[str, Any]] = [_builtin_local_mcp_server_status()]
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


def _phase_payload(phase: str, text: str, speaker: str = "pet", **extra: Any) -> dict[str, Any]:
    payload = {
        "phase": str(phase or "").strip(),
        "text": _sanitize_ai_text(str(text or "")).strip(),
        "speaker": str(speaker or "pet"),
    }
    for key, value in extra.items():
        if value is None:
            continue
        payload[str(key)] = value
    return payload


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
    return _rt_continuation_recheck_prompt(_sanitize_ai_text(str(user_text or "")).strip(), remaining_steps)


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sanitize_ai_text(text: str) -> str:
    return (text or "").replace("*", "").replace("#", "")


def _build_expression_protocol_prompt(available_expressions: list[str] | None = None) -> str:
    return _rt_build_expression_protocol_prompt(available_expressions)


def _build_system_prompt(
    user_prompt: str,
    skill_prompt: str,
    expression_mode: bool,
    output_format: str,
    available_expressions: list[str] | None = None,
) -> str:
    return _rt_build_system_prompt(
        user_prompt,
        skill_prompt,
        expression_mode,
        output_format,
        available_expressions,
    )


def _looks_like_hidden_tool_reference(value: str) -> bool:
    normalized = str(value or "").strip().strip("`")
    if not normalized or " " in normalized:
        return False
    lowered = normalized.lower()
    if lowered.startswith(("http://", "https://")):
        return False
    if normalized.startswith("skill.") or normalized.endswith(".*"):
        return True
    if normalized.count(".") < 1:
        return False
    segments = [segment for segment in normalized.split(".") if segment]
    if len(segments) < 2:
        return False
    if segments[-1].lower() in REACT_SKILL_PROMPT_FILE_EXTENSIONS:
        return False
    return all(re.fullmatch(r"[A-Za-z0-9_-]+", segment) for segment in segments)


def _sanitize_skill_prompt_for_react_visibility(skill_prompt: str) -> str:
    prompt = str(skill_prompt or "").strip()
    if not prompt:
        return ""

    visible_lines: list[str] = []
    dropping_dependencies = False
    for raw_line in prompt.splitlines():
        if REACT_SKILL_PROMPT_DEPENDENCIES_RE.match(raw_line):
            dropping_dependencies = True
            continue
        if dropping_dependencies:
            continue
        visible_lines.append(raw_line)

    sanitized = "\n".join(visible_lines).strip()
    if not sanitized:
        return ""

    def _replace_inline_code(match: re.Match[str]) -> str:
        token = str(match.group(1) or "").strip()
        if not _looks_like_hidden_tool_reference(token):
            return match.group(0)
        if token.startswith("skill."):
            return "`skill-local tool`"
        return "`discovered MCP tool`"

    sanitized = REACT_SKILL_PROMPT_INLINE_CODE_RE.sub(_replace_inline_code, sanitized).strip()
    if not sanitized:
        return ""
    return f"{REACT_SKILL_VISIBILITY_NOTE}\n\n{sanitized}"


def _build_decision_messages(base_messages: list[dict[str, Any]], skill_prompt: str) -> list[dict[str, Any]]:
    return _rt_build_decision_messages(base_messages, skill_prompt)


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
    global _AGENT_GRAPH_RUNTIME, _MCP_BRIDGE, _SKILL_MANAGER, _CHAT_TOPIC_STORE, _ASR_SERVICE, _ASR_WARMUP_TASK
    if _MCP_BRIDGE is not None:
        try:
            _MCP_BRIDGE.stop()
        except Exception:
            pass
        _MCP_BRIDGE = None
    _AGENT_GRAPH_RUNTIME = None
    _SKILL_MANAGER = None
    _CHAT_TOPIC_STORE = None
    _ASR_SERVICE = None
    _ASR_WARMUP_TASK = None
    _TURN_TOOL_BRIDGE_CACHE.clear()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    ollama_ok = await is_ollama_alive(OLLAMA_BASE_URL)
    settings_config = _load_settings_config()
    asr_cfg = _asr_config(settings_config)
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
    asr_ok = False
    asr_message = ""
    if asr_cfg["enabled"]:
        if _should_use_external_asr(settings_config, asr_cfg):
            asr_ok = await _external_asr_health(asr_cfg["api_base_url"])
            if not asr_ok:
                asr_message = _get_asr_service().readiness_message()
                asr_ok = not asr_message
        else:
            asr_message = _get_asr_service().readiness_message()
            asr_ok = not asr_message
    elif _is_macos():
        asr_message = _asr_disabled_message()
    return {
        "ok": True,
        "ollama": ollama_ok,
        "tts": tts_available(DEFAULT_PROVIDER),
        "asr": bool(asr_ok),
        "message": asr_message,
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
    chat_mode = _normalize_chat_mode(req.chat_mode)
    perf = PerfTracker()
    text = req.text.strip()
    session_id = str(req.session_id or "").strip() or "default"
    topic_history_enabled = False
    max_reasoning_steps = max(1, int(req.max_reasoning_steps or 10))
    router_used = False
    route_kind = ""
    route_thought_summary = ""
    route_skill_ids: list[str] = []
    route_tool_candidates: list[str] = []
    route_tool_call: dict[str, Any] = {}
    route_search_needed = False
    route_search_query = ""
    chat_mode_search_fallback_prompt = ""
    try:
        if not text:
            raise HTTPException(status_code=400, detail="text cannot be empty")
        context = _build_chat_request_context(req, perf)
        settings_config = context.settings_config
        tooling_cfg = context.tooling_cfg
        topic_history_cfg = context.topic_history_cfg
        router_cfg = context.router_cfg
        resolved_skills = context.resolved_skills
        session_id = context.session_id
        topic_history_enabled = bool(topic_history_cfg["enabled"])
        pet_display_name = context.pet_display_name
        tools_enabled = bool(tooling_cfg.get("enabled", True) and req.tools_enabled)
        if chat_mode == CHAT_MODE_CHAT:
            if not tools_enabled:
                raise HTTPException(status_code=400, detail="chat mode requires tavily-mcp tools to be enabled")
        if chat_mode == CHAT_MODE_SKILL and not router_cfg["enabled"] and not resolved_skills.skill_ids:
            raise HTTPException(status_code=400, detail="Skill mode requires at least one active skill")

        if router_cfg["enabled"]:
            route_tools = _build_router_tool_schemas(
                chat_mode,
                list(resolved_skills.skill_ids),
                settings_config=settings_config,
                tooling_cfg=tooling_cfg,
            )
            route_skill_summaries = _skill_summaries_for_route(
                chat_mode=chat_mode,
                requested_skill_ids=req.skill_ids,
                settings_config=settings_config,
            )
            try:
                route = await perf.time_await(
                    "router_classify",
                    classify_route(
                        chat_mode=chat_mode,
                        messages=context.working_messages,
                        model=router_cfg["model"],
                        tools=route_tools,
                        skill_summaries=route_skill_summaries,
                        llm_provider=router_cfg["llm_provider"],
                        api_base_url=router_cfg["api_base_url"],
                        api_key=router_cfg["api_key"],
                    ),
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

                if chat_mode == CHAT_MODE_CHAT:
                    if route_search_needed and route_search_query:
                        search_tool_name = _pick_tavily_tool_name(route_tools)
                        if search_tool_name:
                            route_kind = "simple_tool_task"
                            route_tool_call = {
                                "name": search_tool_name,
                                "arguments": {"query": route_search_query},
                                "action_message": f"Search the web for {route_search_query}",
                            }
                        else:
                            chat_mode_search_fallback_prompt = CHAT_MODE_NO_LIVE_SEARCH_PROMPT
                            route_kind = "direct_answer"
                            route_thought_summary = (
                                "I do not have live web search in this turn, so I will answer from the current context."
                            )
                            route_tool_candidates = []
                            route_tool_call = {}
                            route_search_needed = False
                            route_search_query = ""
                    else:
                        route_kind = "direct_answer"
                elif chat_mode == CHAT_MODE_SKILL:
                    routed_skill_ids = _canonicalize_skill_ids(route_skill_ids, manager=_get_skill_manager())
                    if routed_skill_ids != list(resolved_skills.skill_ids):
                        resolved_skills = perf.time_call(
                            "resolve_skills",
                            _resolve_request_skills,
                            routed_skill_ids,
                            settings_config,
                            chat_mode=chat_mode,
                        )
                    if not resolved_skills.skill_ids:
                        raise HTTPException(status_code=400, detail="Skill route did not resolve to an installed skill")
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

        inventory_scope = _explicit_capability_inventory_scope(text)
        if chat_mode == CHAT_MODE_REACT and inventory_scope and route_kind in {"", "direct_answer"}:
            route_kind = "complex_task"
            route_skill_ids = []
            route_tool_candidates = []
            route_tool_call = {}
            route_search_needed = False
            route_search_query = ""
            route_thought_summary = "I should check the currently available capabilities before I answer."

        forced_skill_ids = _infer_forced_skill_ids_from_request(text, resolved_skills)
        if (
            forced_skill_ids
            and chat_mode == CHAT_MODE_REACT
            and route_kind in {"", "direct_answer", "complex_task"}
        ):
            route_kind = "skill_task"
            route_skill_ids = list(forced_skill_ids)
            route_tool_candidates = []
            route_tool_call = {}
            route_search_needed = False
            route_search_query = ""
            route_thought_summary = "I should follow the matched skill workflow for this task."

        decision_resolved_skills = resolved_skills
        if chat_mode == CHAT_MODE_REACT and route_kind == "skill_task" and route_skill_ids:
            if list(route_skill_ids) != list(resolved_skills.skill_ids):
                decision_resolved_skills = perf.time_call(
                    "resolve_skills",
                    _resolve_request_skills,
                    route_skill_ids,
                    settings_config,
                    chat_mode=chat_mode,
                )
        raw_skill_prompt_text = decision_resolved_skills.prompt_text if chat_mode != CHAT_MODE_CHAT else ""
        skill_prompt_text = (
            _sanitize_skill_prompt_for_react_visibility(raw_skill_prompt_text)
            if chat_mode == CHAT_MODE_REACT
            else raw_skill_prompt_text
        )
    except Exception as exc:
        _ensure_perf_keys(
            perf,
            "load_config",
            "derive_settings_tooling",
            "resolve_skills",
            "load_topic_snapshot",
            "load_long_term_memory",
            "router_classify",
            "build_messages",
            "graph_start",
            "finalize_exchange",
        )
        detail = exc.detail if isinstance(exc, HTTPException) else f"{type(exc).__name__}: {exc}"
        _log_perf_event(
            "chat_stream_perf",
            session_id=session_id,
            chat_mode=chat_mode,
            router_used=router_used,
            route_kind=route_kind,
            topic_history_enabled=topic_history_enabled,
            timings_ms=perf.timings_ms,
            ok=False,
            error=str(detail),
        )
        raise

    build_started = time.perf_counter()
    decision_messages = _build_decision_messages(context.working_messages, skill_prompt_text)
    sys_prompt = _build_system_prompt(
        req.system_prompt,
        "",
        bool(req.expression_mode),
        str(req.expression_output_format or ""),
        req.available_expressions,
    )
    prompt_msgs = (
        [{"role": "system", "content": sys_prompt}] + list(context.working_messages)
        if sys_prompt
        else list(context.working_messages)
    )
    if chat_mode_search_fallback_prompt:
        prefix_messages = []
        if sys_prompt:
            prefix_messages.append({"role": "system", "content": sys_prompt})
        prefix_messages.append({"role": "system", "content": chat_mode_search_fallback_prompt})
        prompt_msgs = prefix_messages + list(context.working_messages)
    perf.add("build_messages", time.perf_counter() - build_started)
    tools_enabled = bool(tooling_cfg.get("enabled", True) and req.tools_enabled)
    selection_origin = "route" if chat_mode == CHAT_MODE_REACT and route_kind == "skill_task" and route_skill_ids else "none"
    selected_skill_ids = list(route_skill_ids) if chat_mode == CHAT_MODE_REACT and route_kind == "skill_task" else []
    execution_phase = (
        PHASE_AGENT_LOOP
        if chat_mode == CHAT_MODE_REACT and route_kind == "complex_task"
        else PHASE_SKILL_EXECUTION
        if chat_mode == CHAT_MODE_REACT and route_kind == "skill_task" and route_skill_ids
        else PHASE_SKILL_SELECTION
        if chat_mode == CHAT_MODE_REACT
        else ""
    )
    turn_id = uuid4().hex
    _prime_turn_tool_bridge_cache(
        turn_id=turn_id,
        chat_mode=chat_mode,
        settings_config=settings_config,
        tooling_cfg=tooling_cfg,
        active_skill_ids=list(resolved_skills.skill_ids),
        active_resolved_skills=resolved_skills,
        selection_origin=selection_origin,
        selected_skill_ids=selected_skill_ids,
        selected_resolved_skills=decision_resolved_skills if selected_skill_ids else None,
    )

    async def event_gen():
        yield _sse(
            "meta",
            {
                "session_id": session_id,
                "topic_id": session_id,
                "topic_history_enabled": topic_history_enabled,
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
        stream_ok = False
        error_message = ""
        pending_turn = False
        finalize_result: dict[str, Any] = {}
        runtime = _get_agent_graph_runtime()
        try:
            outcome = await perf.time_await(
                "graph_start",
                runtime.start_turn(
                    {
                        "turn_id": turn_id,
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
                        "working_messages": list(context.working_messages),
                        "decision_messages": decision_messages,
                        "prompt_messages": prompt_msgs,
                        "settings_config": settings_config,
                        "tooling_config": tooling_cfg,
                        "execution_phase": execution_phase,
                        "selection_origin": selection_origin,
                        "selected_skill_ids": selected_skill_ids,
                        "selected_tool_names": [],
                        "planner_excluded_skill_ids": [],
                        "planner_excluded_tool_names": [],
                        "planner_retry_used": False,
                        "loop_round": 1,
                        "last_expected_effect": "",
                        "last_assessment": "",
                        "last_inventory_result": {},
                        "rejected_call_signatures": [],
                        "no_progress_streak": 0,
                        "phase_events": [],
                    }
                ),
            )
            if outcome.phase_events:
                for item in outcome.phase_events:
                    yield _sse(
                        "phase",
                        _phase_payload(
                            str(item.get("phase") or ""),
                            str(item.get("text") or ""),
                            speaker=str(item.get("speaker") or "pet"),
                            loop_round=item.get("loop_round"),
                            thought_role=item.get("thought_role"),
                            plan_id=item.get("plan_id"),
                            step_id=item.get("step_id"),
                            step_index=item.get("step_index"),
                            step_title=item.get("step_title"),
                            step_status=item.get("step_status"),
                            batch_id=item.get("batch_id"),
                            batch_size=item.get("batch_size"),
                        ),
                    )
            elif outcome.thought_summary:
                yield _sse("phase", _phase_payload("thought", outcome.thought_summary))
            if outcome.is_pending and outcome.approval_request:
                pending_turn = True
                stream_ok = True
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
            full_answer = _sanitize_ai_text(full_answer)
            finalize_result = await perf.time_await(
                "finalize_exchange",
                _finalize_chat_exchange(
                    session_id=session_id,
                    user_text=text,
                    assistant_text=full_answer,
                    working_messages=list(context.working_messages),
                    memory_window=int(req.memory_window),
                    settings_config=settings_config,
                    llm_provider=str(req.llm_provider or "ollama"),
                    api_base_url=str(req.api_base_url or ""),
                    api_key=str(req.api_key or ""),
                    model=str(req.model or "qwen3:8b"),
                ),
            )
            suggestion = finalize_result.get("memory_save_suggestion")
            if isinstance(suggestion, dict):
                yield _sse("memory_save_suggestion", {"topic_id": session_id, "candidate": suggestion})
            yield _sse("done", {"text": full_answer, "topic_id": session_id})
            stream_ok = True
        except Exception as exc:
            message = _exception_message(exc, "chat stream failed")
            error_message = f"{type(exc).__name__}: {message}"
            yield _sse("error", {"message": message})
            return
        finally:
            if cleanup_turn_id:
                runtime.delete_turn(cleanup_turn_id)
                _clear_turn_tool_bridge_cache(cleanup_turn_id)
            elif error_message and not pending_turn:
                _clear_turn_tool_bridge_cache(turn_id)
            _ensure_perf_keys(
                perf,
                "load_config",
                "derive_settings_tooling",
                "resolve_skills",
                "load_topic_snapshot",
                "load_long_term_memory",
                "router_classify",
                "build_messages",
                "graph_start",
                "finalize_exchange",
            )
            _log_perf_event(
                "chat_stream_perf",
                session_id=session_id,
                chat_mode=chat_mode,
                router_used=router_used,
                route_kind=route_kind,
                topic_history_enabled=topic_history_enabled,
                timings_ms=perf.timings_ms,
                ok=stream_ok and not error_message,
                error=error_message,
            )

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/api/chat/approval")
async def chat_approval(req: ChatApprovalRequest) -> StreamingResponse:
    runtime = _get_agent_graph_runtime()
    if runtime.get_pending_approval(req.turn_id) is None:
        raise HTTPException(status_code=404, detail="pending turn not found or already handled")
    perf = PerfTracker()

    async def event_gen():
        full_answer = ""
        cleanup_turn_id = req.turn_id
        state: dict[str, Any] = {}
        stream_ok = False
        error_message = ""
        pending_turn = False
        finalize_result: dict[str, Any] = {}
        try:
            outcome = await perf.time_await(
                "approval_resume",
                runtime.resume_turn(
                    ApprovalDecision(
                        turn_id=req.turn_id,
                        approved=req.approved,
                        user_text=str(req.user_text or ""),
                    )
                ),
            )
            state = outcome.state
            if outcome.phase_events:
                for item in outcome.phase_events:
                    yield _sse(
                        "phase",
                        _phase_payload(
                            str(item.get("phase") or ""),
                            str(item.get("text") or ""),
                            speaker=str(item.get("speaker") or "pet"),
                            loop_round=item.get("loop_round"),
                            thought_role=item.get("thought_role"),
                            plan_id=item.get("plan_id"),
                            step_id=item.get("step_id"),
                            step_index=item.get("step_index"),
                            step_title=item.get("step_title"),
                            step_status=item.get("step_status"),
                            batch_id=item.get("batch_id"),
                            batch_size=item.get("batch_size"),
                        ),
                    )
            elif req.approved:
                yield _sse("phase", _phase_payload("action", "好呀，那我这就开始处理这件事。"))
                if outcome.thought_summary:
                    yield _sse("phase", _phase_payload("thought", outcome.thought_summary))
            elif str(req.user_text or "").strip():
                yield _sse("phase", _phase_payload("action", "收到你的补充啦，我按新的说明接着继续这轮任务。"))
                if outcome.thought_summary:
                    yield _sse("phase", _phase_payload("thought", outcome.thought_summary))
            else:
                yield _sse("phase", _phase_payload("action", "那这次我先不调用工具，直接按现有信息回答你。"))
            if outcome.is_pending and outcome.approval_request:
                cleanup_turn_id = ""
                pending_turn = True
                stream_ok = True
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
            message = _exception_message(exc, "chat approval failed")
            error_message = f"{type(exc).__name__}: {message}"
            yield _sse("error", {"message": message})
            return

        full_answer = _sanitize_ai_text(full_answer)
        session_id = str(state.get("session_id") or "default")
        memory_window = int(state.get("memory_window") or 10)
        settings_config = state.get("settings_config") if isinstance(state.get("settings_config"), dict) else _load_settings_config()
        try:
            finalize_result = await perf.time_await(
                "finalize_exchange",
                _finalize_chat_exchange(
                    session_id=session_id,
                    user_text=str(state.get("user_text") or ""),
                    assistant_text=full_answer,
                    working_messages=list(state.get("working_messages") or []),
                    memory_window=memory_window,
                    settings_config=settings_config,
                    llm_provider=str(state.get("llm_provider") or "ollama"),
                    api_base_url=str(state.get("api_base_url") or ""),
                    api_key=str(state.get("api_key") or ""),
                    model=str(state.get("model") or "qwen3:8b"),
                ),
            )
            suggestion = finalize_result.get("memory_save_suggestion")
            if isinstance(suggestion, dict):
                yield _sse("memory_save_suggestion", {"topic_id": session_id, "candidate": suggestion})
            yield _sse("done", {"text": full_answer, "topic_id": session_id})
            stream_ok = True
        except Exception as exc:
            message = _exception_message(exc, "chat approval failed")
            error_message = f"{type(exc).__name__}: {message}"
            yield _sse("error", {"message": message})
            return
        finally:
            if cleanup_turn_id:
                runtime.delete_turn(cleanup_turn_id)
                _clear_turn_tool_bridge_cache(cleanup_turn_id)
            elif error_message and not pending_turn:
                _clear_turn_tool_bridge_cache(req.turn_id)
            _ensure_perf_keys(perf, "approval_resume", "load_long_term_memory", "finalize_exchange")
            _log_perf_event(
                "chat_approval_perf",
                session_id=str(state.get("session_id") or "default"),
                chat_mode=str(state.get("chat_mode") or ""),
                router_used=bool(state.get("router_used")),
                route_kind=str(state.get("route_kind") or ""),
                topic_history_enabled=bool(_topic_history_config(settings_config)["enabled"]),
                timings_ms=perf.timings_ms,
                ok=stream_ok and not error_message,
                error=error_message,
            )

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/api/chat/memory/decision")
async def post_chat_memory_decision(req: ChatMemoryDecisionRequest) -> dict[str, Any]:
    topic_id = str(req.topic_id or "").strip()
    if not topic_id:
        raise HTTPException(status_code=400, detail="topic_id is required")

    detail = _get_chat_topic_store().get_topic_detail(topic_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="topic not found")

    marker = str(req.candidate.marker or "").strip()
    if not marker:
        raise HTTPException(status_code=400, detail="candidate.marker is required")

    action = str(req.action or "").strip().lower()
    store = _get_chat_topic_store()
    if action == "dismiss":
        meta = store.update_long_term_memory_marker(topic_id, dismissed_marker=marker)
        return {"ok": True, "action": "dismiss", "topic_id": topic_id, "marker": marker, "meta": meta or {}}

    if action != "save":
        raise HTTPException(status_code=400, detail="action must be save or dismiss")

    settings_config = _load_settings_config()
    long_term_memory_cfg = _long_term_memory_config(settings_config)
    if not long_term_memory_cfg.get("enabled") or not long_term_memory_cfg.get("write_enabled"):
        raise HTTPException(status_code=400, detail="long-term memory writing is disabled")
    candidate_payload = req.candidate.model_dump() if hasattr(req.candidate, "model_dump") else req.candidate.dict()
    save_result = _save_long_term_memory_candidate(
        topic_id=topic_id,
        candidate=candidate_payload,
        long_term_memory_cfg=long_term_memory_cfg,
        tooling_cfg=_derive_runtime_tooling_config(settings_config),
    )
    meta = store.update_long_term_memory_marker(topic_id, saved_marker=marker)
    return {
        "ok": True,
        "action": "save",
        "topic_id": topic_id,
        "marker": marker,
        "result": save_result,
        "meta": meta or {},
    }


@app.post("/api/chat/topics")
async def create_chat_topic(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    settings_config = _load_settings_config()
    topic_cfg = _topic_history_config(settings_config)
    requested_topic_id = ""
    requested_title = DEFAULT_TOPIC_TITLE
    if isinstance(payload, dict):
        requested_topic_id = str(payload.get("topic_id") or payload.get("session_id") or "").strip()
        requested_title = str(payload.get("title") or DEFAULT_TOPIC_TITLE).strip() or DEFAULT_TOPIC_TITLE
    meta = _get_chat_topic_store().create_topic(
        topic_id=requested_topic_id or None,
        persisted=False,
        title=requested_title,
    )
    topic_id = str(meta.get("topic_id") or "")
    if topic_id:
        SESSION_STORE.setdefault(topic_id, [])
    return {
        "ok": True,
        "topic": meta,
        "topic_id": topic_id,
        "persisted": bool(meta.get("persisted", False)),
    }


@app.get("/api/chat/topics")
async def list_chat_topics() -> dict[str, Any]:
    settings_config = _load_settings_config()
    topic_cfg = _topic_history_config(settings_config)
    topics = _get_chat_topic_store().list_topics()
    return {
        "ok": True,
        "enabled": bool(topic_cfg["enabled"]),
        "topics": topics,
    }


@app.get("/api/chat/topics/{topic_id}")
async def get_chat_topic(topic_id: str) -> dict[str, Any]:
    detail = _get_chat_topic_store().get_topic_detail(normalize_topic_id(topic_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="topic not found")
    return {
        "ok": True,
        "meta": detail.get("meta") or {},
        "messages": detail.get("messages") or [],
        "summary": detail.get("summary") or {},
    }


@app.delete("/api/chat/topics/{topic_id}")
@app.post("/api/chat/topics/{topic_id}/delete")
async def delete_chat_topic(topic_id: str) -> dict[str, Any]:
    normalized_topic_id = normalize_topic_id(topic_id)
    store = _get_chat_topic_store()
    deleted = store.delete_topic(normalized_topic_id)
    session_deleted = SESSION_STORE.pop(normalized_topic_id, None) is not None
    if not deleted and not session_deleted:
        raise HTTPException(status_code=404, detail="topic not found")
    return {
        "ok": True,
        "topic_id": normalized_topic_id,
        "deleted": True,
        "topics": store.list_topics(),
    }


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
    tooling_cfg = _load_runtime_tooling_config()
    bridge = _get_mcp_bridge_for_tooling(tooling_cfg)
    payload = bridge.health()
    servers = [_builtin_local_mcp_server_status(tooling_cfg), *list(payload.get("servers") or [])]
    return {
        **payload,
        "builtin_enabled": bool(tooling_cfg.get("enabled", True)),
        "servers": servers,
        "online": sum(1 for item in servers if str(item.get("health_status") or "").strip().lower() == "online"),
    }


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


@app.post("/api/asr/warmup")
async def warmup_asr() -> dict[str, Any]:
    settings_config = _load_settings_config()
    asr_cfg = _asr_config(settings_config)
    if not asr_cfg["enabled"]:
        return {"ok": False, "started": False, "ready": False, "message": _asr_disabled_message()}
    started, message = _ensure_internal_asr_warmup_started()
    readiness_message = _get_asr_service().readiness_message()
    ready = not readiness_message
    return {
        "ok": True,
        "started": bool(started),
        "ready": bool(ready),
        "message": "" if ready else (message or readiness_message),
    }


@app.websocket("/api/asr/stream")
async def asr_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    settings_config = _load_settings_config()
    asr_cfg = _asr_config(settings_config)
    if not asr_cfg["enabled"]:
        await websocket.send_json({"type": "error", "message": _asr_disabled_message()})
        await websocket.close(code=1008)
        return

    service = _get_asr_service()
    session_id = ""
    try:
        start_packet = await websocket.receive()
        start_text = start_packet.get("text") if isinstance(start_packet, dict) else None
        if not start_text:
            await websocket.send_json({"type": "error", "message": "Expected a JSON start message."})
            await websocket.close(code=1003)
            return
        try:
            payload = json.loads(start_text)
        except Exception:
            await websocket.send_json({"type": "error", "message": "Invalid start payload JSON."})
            await websocket.close(code=1003)
            return
        if str(payload.get("type") or "") != "start":
            await websocket.send_json({"type": "error", "message": "The first ASR message must be type=start."})
            await websocket.close(code=1008)
            return

        session = await service.start_session(
            key=str(payload.get("key") or asr_cfg["push_to_talk_key"]),
            language=str(payload.get("language") or DEFAULT_ASR_LANGUAGE),
            punctuation=bool(payload.get("punctuation", True)),
            interim_results=bool(payload.get("interim_results", asr_cfg["interim_results"])),
        )
        session_id = session.session_id
        await websocket.send_json({"type": "ready", "session_id": session_id})

        while True:
            packet = await websocket.receive()
            packet_type = str(packet.get("type") or "")
            if packet_type == "websocket.disconnect":
                break
            audio_bytes = packet.get("bytes") if isinstance(packet, dict) else None
            if audio_bytes is not None:
                partial = await service.push_audio(session_id, bytes(audio_bytes))
                if partial is not None and partial.text:
                    await websocket.send_json({"type": "partial", "text": partial.text})
                continue
            packet_text = packet.get("text") if isinstance(packet, dict) else None
            if not packet_text:
                continue
            try:
                payload = json.loads(packet_text)
            except Exception:
                await websocket.send_json({"type": "error", "message": "Invalid ASR control payload."})
                continue
            if str(payload.get("type") or "") != "stop":
                await websocket.send_json({"type": "error", "message": "Unsupported ASR control message."})
                continue
            final_result = await service.stop_session(session_id)
            session_id = ""
            await websocket.send_json({"type": "final", "text": final_result.text})
            return
    except WebSocketDisconnect:
        pass
    except ASRError as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if session_id:
            try:
                await service.discard_session(session_id)
            except Exception:
                pass


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
