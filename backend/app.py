from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import mimetypes
import os
import re
import secrets
import sys
import threading

import httpx
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .agent_graph import AgentGraphRuntime, ApprovalDecision, GraphDependencies
from .active_vision import build_active_observe_metadata, decide_active_observation, normalize_active_observation_config
from .chat_topics import DEFAULT_TOPIC_TITLE, TopicStore, normalize_topic_id
from .conversation_store import ConversationStore, normalize_conversation_id
from .hermes import (
    DEFAULT_HERMES_CONFIG,
    HermesClient,
    HermesUnavailable,
    hermes_config_from_raw,
    normalize_hermes_config,
)
from .runtime_adapters import RUNTIME_MOCK, RuntimeUnavailable
from .runtime_config import (
    DEFAULT_RUNTIME_CONFIG,
    RUNTIME_ASTRBOT,
    RUNTIME_HERMES,
    apply_runtime_secret_actions,
    mirror_runtime_compat,
    normalize_runtime_config,
    redact_runtime_config,
    secret_preview,
)
from .realtime_stream import DEFAULT_HEARTBEAT_AFTER_SEC, DEFAULT_HEARTBEAT_INTERVAL_SEC, realtime_stream_events
from .runtime_service import resolve_runtime_adapter
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
    SkillDeleteRequest,
    SkillImportGitRequest,
    SkillImportLocalRequest,
    TTSRequest,
)
from .ollama_client import OLLAMA_BASE_URL, chat_once as provider_chat_once, is_ollama_alive, list_models as provider_list_models
from .runtime_prompts import (
    EXPR_PROTOCOL_PROMPT,
    REACT_SKILL_VISIBILITY_NOTE,
    build_decision_messages as _rt_build_decision_messages,
    build_expression_protocol_prompt as _rt_build_expression_protocol_prompt,
    build_system_prompt as _rt_build_system_prompt,
    continuation_recheck_prompt as _rt_continuation_recheck_prompt,
)
from .skills import ResolvedSkillSet, SkillAwareToolBridge, SkillManager, SkillRuntime, tool_name_matches_pattern
from .storage_paths import (
    hermes_mcp_dir,
    hermes_root,
    hermes_skills_dir,
    legacy_third_party_mcp_dir,
    legacy_third_party_skills_dir,
)
from .tool_runtime import Tool, ToolRegistry, ToolResult
from .tooling.security import normalize_file_allowlist
from .tts import (
    DEFAULT_PROVIDER,
    cleanup_old_audio,
    list_supported_providers,
    synthesize_to_audio,
    tts_available,
)
from .vision import (
    DEFAULT_VISION_CONFIG,
    VisionDisabledError,
    VisionError,
    VisionService,
    build_grounded_context_prefix,
    normalize_vision_config,
    should_force_grounding,
)
from .vision_analyzer import VisionAnalyzer, merge_analysis_into_payload, merge_unknowns, normalize_analyzer_config
from .ipet_memory_store import IpetMemoryStore
from .memory_harness import MemoryHarness


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
HERMES_ROOT_DIR = hermes_root(ROOT_DIR)
HERMES_SKILLS_IMPORTED_DIR = hermes_skills_dir(ROOT_DIR)
HERMES_MCP_DIR = hermes_mcp_dir(ROOT_DIR)
LEGACY_THIRD_PARTY_SKILLS_DIR = legacy_third_party_skills_dir(ROOT_DIR)
LEGACY_THIRD_PARTY_MCP_DIR = legacy_third_party_mcp_dir(ROOT_DIR)
THIRD_PARTY_SKILLS_DIR = HERMES_SKILLS_IMPORTED_DIR
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
LOCAL_API_TOKEN_ENV = "IPET_LOCAL_API_TOKEN"
LOCAL_API_TOKEN_HEADER = "X-Ipet-Local-Token"
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
DEFAULT_ACTIVE_SKILL_IDS = ("browser-automation",)
DEFAULT_HERMES_MODEL = "gpt-5.4"
HERMES_INTERNAL_PROVIDER = "openai-codex"
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
_CONVERSATION_STORE: ConversationStore | None = None
_IPET_MEMORY_STORE: IpetMemoryStore | None = None
_ASR_SERVICE: ASRService | None = None
_ASR_WARMUP_TASK: asyncio.Task[str] | None = None
_VISION_SERVICE: VisionService | None = None
_VISION_ANALYSIS_LOCK = threading.Lock()
_TURN_TOOL_BRIDGE_CACHE: dict[str, Any] = {}

LOGGER = logging.getLogger(__name__)
_REALTIME_HEARTBEAT_AFTER_SEC = DEFAULT_HEARTBEAT_AFTER_SEC
_REALTIME_HEARTBEAT_INTERVAL_SEC = DEFAULT_HEARTBEAT_INTERVAL_SEC
ACTIVE_OBSERVE_MAX_ATTEMPTS = 3
ACTIVE_OBSERVE_TOTAL_TIMEOUT_SEC = 20.0


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
        "hermes": json.loads(json.dumps(DEFAULT_HERMES_CONFIG)),
        "runtime": json.loads(json.dumps(DEFAULT_RUNTIME_CONFIG)),
        "vision": json.loads(json.dumps(DEFAULT_VISION_CONFIG)),
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
            "model": DEFAULT_HERMES_MODEL,
            "session_id": "default",
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
                "default_active_ids": list(DEFAULT_ACTIVE_SKILL_IDS),
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


def _default_active_skill_ids(*, manager: SkillManager | None = None) -> list[str]:
    return _canonicalize_skill_ids(list(DEFAULT_ACTIVE_SKILL_IDS), manager=manager)


def _normalize_chat_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in CHAT_MODE_VALUES else CHAT_MODE_REACT


def _normalize_hermes_model(value: Any) -> str:
    return str(value or DEFAULT_HERMES_MODEL).strip() or DEFAULT_HERMES_MODEL


def _hermes_runtime_config(model: Any = "") -> dict[str, str]:
    return {
        "model": _normalize_hermes_model(model),
        "llm_provider": HERMES_INTERNAL_PROVIDER,
        "api_base_url": "",
        "api_key": "",
    }


def _normalize_hermes_agent_config(config: dict[str, Any]) -> None:
    mirror_runtime_compat(config, root_dir=ROOT_DIR)


def _hermes_agent_config_from_raw(raw_config: dict[str, Any] | None = None):
    source = raw_config if isinstance(raw_config, dict) else _load_full_config()
    runtime = normalize_runtime_config(source.get("runtime"), root_dir=ROOT_DIR, legacy_hermes=source.get("hermes", {}))
    return hermes_config_from_raw(runtime["adapters"][RUNTIME_HERMES], root_dir=ROOT_DIR)


def _get_hermes_client(raw_config: dict[str, Any] | None = None) -> HermesClient:
    return HermesClient(_hermes_agent_config_from_raw(raw_config))


def _runtime_config_from_raw(raw_config: dict[str, Any] | None = None) -> dict[str, Any]:
    source = raw_config if isinstance(raw_config, dict) else _load_full_config()
    return normalize_runtime_config(source.get("runtime"), root_dir=ROOT_DIR, legacy_hermes=source.get("hermes", {}))


def _get_runtime_client(raw_config: dict[str, Any] | None = None):
    source = raw_config if isinstance(raw_config, dict) else _load_full_config()
    raw_runtime = source.get("runtime") if isinstance(source.get("runtime"), dict) else {}
    if str(raw_runtime.get("active") or "").strip().lower() == RUNTIME_MOCK:
        return resolve_runtime_adapter(raw_runtime)
    runtime_cfg = _runtime_config_from_raw(raw_config)
    if runtime_cfg.get("active") == RUNTIME_HERMES:
        return _get_hermes_client(raw_config)
    return resolve_runtime_adapter(runtime_cfg)


def _resolve_router_request_config(req: ChatStreamRequest, settings_config: dict[str, Any]) -> dict[str, Any]:
    chat_cfg = settings_config.get("chat", {}) if isinstance(settings_config, dict) else {}
    runtime_cfg = _hermes_runtime_config(req.model or chat_cfg.get("model"))
    return {
        "enabled": False,
        **runtime_cfg,
    }


def _topic_history_config(settings_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": True,
        "summary_interval_assistant_turns": 0,
        "major_summary_group_size": 0,
    }


def _long_term_memory_config(settings_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": False,
        "project": "hermes",
        "read_enabled": False,
        "write_enabled": False,
        "ask_before_save": False,
        "prefer_topic_history": False,
        "save_from_major_summary": False,
        "save_on_explicit_request": False,
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
    return _hermes_runtime_config(model or chat_cfg.get("model"))


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
    return None


async def _finalize_chat_exchange(
    *,
    session_id: str,
    user_text: str,
    assistant_text: str,
    working_messages: list[dict[str, str]],
    settings_config: dict[str, Any],
) -> dict[str, Any]:
    topic_id = normalize_topic_id(session_id)
    store = _get_chat_topic_store()
    store.append_exchange(topic_id, user_text=user_text, assistant_text=assistant_text)
    SESSION_STORE[topic_id] = store.load_full_messages(topic_id)
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
    for tool in [*list(resolved.resource_tools), *list(resolved.adapter_tools)]:
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
    _normalize_hermes_agent_config(merged)
    merged["vision"] = normalize_vision_config(merged.get("vision", {}))
    merged["model_path"] = _normalize_model_path(merged.get("model_path", ""))

    chat = merged.get("chat", {})
    if not isinstance(chat, dict):
        chat = _default_settings_config()["chat"]
        merged["chat"] = chat
    chat["backend_url"] = str(chat.get("backend_url") or DEFAULT_BACKEND_URL).strip() or DEFAULT_BACKEND_URL
    chat["model"] = _normalize_hermes_model(chat.get("model"))
    chat["session_id"] = str(chat.get("session_id") or "default").strip() or "default"
    for legacy_key in (
        "llm_provider",
        "api_base_url",
        "api_key",
        "router_enabled",
        "router_llm_provider",
        "router_api_base_url",
        "router_api_key",
        "router_model",
        "memory_window",
        "topic_history",
        "long_term_memory",
    ):
        chat.pop(legacy_key, None)
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
    default_skill_ids = skills_cfg.get("default_active_ids", _default_active_skill_ids())
    skills_cfg["default_active_ids"] = _canonicalize_skill_ids(default_skill_ids)
    chat["skills"] = skills_cfg
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


def _apply_vision_analyzer_secret_actions(
    merged: dict[str, Any],
    raw_config: dict[str, Any],
    previous: dict[str, Any],
) -> None:
    raw_vision = raw_config.get("vision") if isinstance(raw_config.get("vision"), dict) else {}
    raw_analyzer = raw_vision.get("analyzer") if isinstance(raw_vision.get("analyzer"), dict) else None
    if raw_analyzer is None:
        return
    vision = merged.setdefault("vision", {})
    if not isinstance(vision, dict):
        vision = {}
        merged["vision"] = vision
    analyzer = vision.setdefault("analyzer", {})
    if not isinstance(analyzer, dict):
        analyzer = {}
        vision["analyzer"] = analyzer
    previous_key = ""
    previous_analyzer = (
        (previous.get("vision") or {}).get("analyzer")
        if isinstance(previous.get("vision"), dict)
        else {}
    )
    if isinstance(previous_analyzer, dict):
        previous_key = str(previous_analyzer.get("api_key") or "").strip()
    action = str(raw_analyzer.get("api_key_action") or "").strip().lower()
    raw_key = str(raw_analyzer.get("api_key") or "").strip()
    if action == "clear":
        analyzer["api_key"] = ""
    elif action == "replace" or raw_key:
        analyzer["api_key"] = raw_key
    else:
        analyzer["api_key"] = previous_key
    analyzer.pop("api_key_action", None)


def _load_settings_config() -> dict[str, Any]:
    return _derive_settings_config(_load_full_config())


def _public_settings_config(config: dict[str, Any]) -> dict[str, Any]:
    public = json.loads(json.dumps(config if isinstance(config, dict) else {}))
    runtime = public.get("runtime") if isinstance(public.get("runtime"), dict) else {}
    public["runtime"] = redact_runtime_config(runtime)
    public["vision"] = normalize_vision_config(public.get("vision", {}))
    analyzer = public["vision"].get("analyzer") if isinstance(public["vision"].get("analyzer"), dict) else None
    if analyzer is not None:
        raw_key = str(analyzer.get("api_key") or "").strip()
        analyzer["api_key"] = ""
        analyzer["api_key_action"] = "keep"
        analyzer["api_key_set"] = bool(raw_key)
        analyzer["api_key_preview"] = secret_preview(raw_key)
    return public


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
        _MCP_BRIDGE = MCPBridge(
            tooling_cfg,
            ROOT_DIR,
            mcp_base_dir=HERMES_MCP_DIR,
            legacy_mcp_base_dir=None,
        )
        _MCP_CONFIG_SNAPSHOT = snapshot
    return _MCP_BRIDGE


def _get_skill_manager(force_reload: bool = False) -> SkillManager:
    global _SKILL_MANAGER
    if force_reload or _SKILL_MANAGER is None:
        _SKILL_MANAGER = SkillManager(
            ROOT_DIR,
            builtin_dir=SKILLS_BUILTIN_DIR,
            imported_dir=HERMES_SKILLS_IMPORTED_DIR,
            legacy_imported_dir=None,
        )
    return _SKILL_MANAGER


def _get_mcp_manager() -> ThirdPartyMCPManager:
    return ThirdPartyMCPManager(
        ROOT_DIR,
        base_dir=HERMES_MCP_DIR,
        legacy_base_dir=None,
    )


def _get_chat_topic_store(force_reload: bool = False) -> TopicStore:
    global _CHAT_TOPIC_STORE
    if force_reload or _CHAT_TOPIC_STORE is None:
        _CHAT_TOPIC_STORE = TopicStore(CHAT_TOPICS_ROOT)
    return _CHAT_TOPIC_STORE


def _get_conversation_store(force_reload: bool = False) -> ConversationStore:
    global _CONVERSATION_STORE
    if force_reload or _CONVERSATION_STORE is None:
        _CONVERSATION_STORE = ConversationStore(ROOT_DIR / "data" / "ipet_conversations")
    return _CONVERSATION_STORE


def _get_ipet_memory_store(force_reload: bool = False) -> IpetMemoryStore:
    global _IPET_MEMORY_STORE
    if force_reload or _IPET_MEMORY_STORE is None:
        _IPET_MEMORY_STORE = IpetMemoryStore(ROOT_DIR / "data" / "ipet_memory")
    return _IPET_MEMORY_STORE


def _get_asr_service(force_reload: bool = False) -> ASRService:
    global _ASR_SERVICE
    if force_reload or _ASR_SERVICE is None:
        _ASR_SERVICE = ASRService()
    return _ASR_SERVICE


def _get_vision_service(settings: dict[str, Any] | None = None) -> VisionService:
    global _VISION_SERVICE
    config_source = settings if isinstance(settings, dict) else _load_settings_config()
    vision_cfg = normalize_vision_config(config_source.get("vision", {}) if isinstance(config_source, dict) else {})
    if _VISION_SERVICE is None:
        _VISION_SERVICE = VisionService(vision_cfg)
    else:
        _VISION_SERVICE.configure(vision_cfg)
    return _VISION_SERVICE


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
        model=str(state.get("model") or DEFAULT_HERMES_MODEL),
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
    session_id = normalize_topic_id(raw_session_id or "default")
    router_cfg = _resolve_router_request_config(req, settings_config)
    runtime_cfg = _hermes_runtime_config(req.model or settings_config.get("chat", {}).get("model"))
    req.model = runtime_cfg["model"]
    resolved_skills = perf.time_call(
        "resolve_skills",
        _resolve_request_skills,
        req.skill_ids,
        settings_config,
        chat_mode=chat_mode,
    )
    topic_snapshot = None
    store = _get_chat_topic_store()
    topic_snapshot = perf.time_call("load_topic_snapshot", store.get_runtime_snapshot, session_id)
    if topic_snapshot is not None:
        SESSION_STORE[session_id] = [dict(item) for item in topic_snapshot.full_messages]
        history_messages = [
            {"role": str(item.get("role") or ""), "content": str(item.get("content") or "")}
            for item in topic_snapshot.full_messages
            if str(item.get("role") or "") in {"user", "assistant"} and str(item.get("content") or "")
        ]
    else:
        history_messages = [
            {"role": str(item.get("role") or ""), "content": str(item.get("content") or "")}
            for item in SESSION_STORE.get(session_id, [])
            if isinstance(item, dict) and str(item.get("role") or "") in {"user", "assistant"} and str(item.get("content") or "")
        ]
    perf.add("load_long_term_memory", 0.0)
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
    global _AGENT_GRAPH_RUNTIME, _SKILL_MANAGER, _CHAT_TOPIC_STORE, _CONVERSATION_STORE, _IPET_MEMORY_STORE, _ASR_SERVICE, _ASR_WARMUP_TASK
    _AGENT_GRAPH_RUNTIME = None
    _SKILL_MANAGER = None
    _CHAT_TOPIC_STORE = None
    _CONVERSATION_STORE = None
    _IPET_MEMORY_STORE = None
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


def _mcp_storage_scope(manifest_or_status: dict[str, Any]) -> str:
    scope = str(manifest_or_status.get("storage_scope") or "").strip().lower()
    if scope == "managed":
        return "hermes"
    return scope


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _skill_storage_scope(skill: Any, *, manager: SkillManager | None = None) -> str:
    if str(getattr(skill, "source_type", "") or "").strip().lower() == "builtin":
        return "builtin"
    root_path = Path(getattr(skill, "package_root", None) or getattr(skill, "root_path", ""))
    imported_dir = Path(getattr(manager, "imported_dir", HERMES_SKILLS_IMPORTED_DIR))
    raw_legacy_imported_dir = getattr(manager, "legacy_imported_dir", None)
    legacy_imported_dir = Path(raw_legacy_imported_dir) if raw_legacy_imported_dir else None
    if _path_is_under(root_path, imported_dir):
        return "hermes"
    if legacy_imported_dir is not None and _path_is_under(root_path, legacy_imported_dir):
        return "legacy"
    return "external"


def _skill_summary_payload(
    skill: Any,
    *,
    default_active: bool = False,
    manager: SkillManager | None = None,
) -> dict[str, Any]:
    summary = skill.to_summary(default_active=default_active)
    summary["storage_scope"] = _skill_storage_scope(skill, manager=manager)
    return summary


def _iter_registered_mcp_server_configs() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tooling_cfg = _load_runtime_tooling_config()
    third_cfg = tooling_cfg.get("third_party", {})
    if not isinstance(third_cfg, dict):
        third_cfg = {"enabled": False, "servers": []}
    configured = third_cfg.get("servers", [])
    manager = _get_mcp_manager()
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
                "storage_scope": _mcp_storage_scope(manifest),
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
        "storage_scope": "builtin",
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

    manager = _get_mcp_manager()
    servers: list[dict[str, Any]] = [_builtin_local_mcp_server_status()]
    for server_cfg in server_cfgs:
        name = str(server_cfg.get("name") or "").strip()
        if not name:
            continue
        if name in cached_status:
            cached = dict(cached_status[name])
            cached.setdefault("name", name)
            if cached.get("storage_scope"):
                cached["storage_scope"] = _mcp_storage_scope(cached)
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
            "storage_scope": str(server_cfg.get("storage_scope", "")),
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
            status["storage_scope"] = _mcp_storage_scope(manifest)
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
    manager = _get_mcp_manager()
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
        "storage_scope": _mcp_storage_scope(manifest),
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
        "skills": [
            _skill_summary_payload(
                item,
                default_active=item.skill_id in default_active_ids,
                manager=manager,
            )
            for item in records
        ],
        "config": settings,
        "runtime": "hermes",
        "storage": {
            "runtime": "Hermes",
            "imported_dir": str(HERMES_SKILLS_IMPORTED_DIR),
            "legacy": False,
        },
    }


def _chat_request_payload(req: ChatStreamRequest) -> dict[str, Any]:
    if hasattr(req, "model_dump"):
        return req.model_dump()
    return req.dict()


def _approval_request_payload(req: ChatApprovalRequest) -> dict[str, Any]:
    if hasattr(req, "model_dump"):
        return req.model_dump()
    return req.dict()


def _active_observation_failure_context(service: VisionService, unknowns: list[str], trace: dict[str, Any]) -> dict[str, Any]:
    if hasattr(service, "record_active_observation_failure"):
        service.record_active_observation_failure(unknowns, trace)
    context = service.active_context(include_image=False)
    if not context.get("unknowns"):
        context = {**context, "unknowns": list(unknowns)}
    if not context.get("active_observation"):
        context = {**context, "active_observation": dict(trace)}
    return context


def _active_observation_seen_entry(metadata: dict[str, Any]) -> dict[str, str]:
    return {
        "mode": str(metadata.get("mode") or "")[:40],
        "target_id": str(metadata.get("target_id") or "")[:120],
        "target_hint": str(metadata.get("target_hint") or "")[:80],
        "foreground_app": str(metadata.get("foreground_app") or "")[:120],
        "window_title": str(metadata.get("window_title") or "")[:200],
        "frame_hash": str(metadata.get("frame_hash") or "")[:120],
    }


def _active_observation_next_target(metadata: dict[str, Any]) -> str:
    attempted = {
        str(item or "").strip()
        for item in (metadata.get("attempted_targets") if isinstance(metadata.get("attempted_targets"), list) else [])
    }
    for target in metadata.get("available_next_targets") or []:
        candidate = str(target or "").strip()
        if candidate and candidate not in attempted:
            return candidate
    return ""


def _active_observation_should_retry(metadata: dict[str, Any], *, grounded: bool, deadline: float) -> bool:
    if metadata.get("stop"):
        return False
    if time.monotonic() >= deadline:
        return False
    relevance_hint = str(metadata.get("relevance_hint") or "").strip()
    if grounded and relevance_hint in {"", "likely_relevant"}:
        return False
    return bool(_active_observation_next_target(metadata))


def _active_observation_timeout_config(vision_cfg: dict[str, Any], remaining_sec: float) -> dict[str, Any]:
    active_cfg = normalize_active_observation_config(vision_cfg.get("active_observation", {}))
    next_cfg = dict(vision_cfg)
    next_active = dict(active_cfg)
    next_active["timeout_sec"] = max(1.0, min(float(active_cfg["timeout_sec"]), remaining_sec, ACTIVE_OBSERVE_TOTAL_TIMEOUT_SEC))
    next_cfg["active_observation"] = next_active
    return next_cfg


def _active_attempted_targets_line(context: dict[str, Any]) -> str:
    active = context.get("active_observation") if isinstance(context.get("active_observation"), dict) else {}
    attempted = active.get("attempted_targets") if isinstance(active.get("attempted_targets"), list) else []
    targets: list[str] = []
    for item in attempted:
        text = str(item or "").strip()
        if text and text not in targets:
            targets.append(text)
    target_hint = str(active.get("target_hint") or "").strip()
    if target_hint and target_hint not in targets:
        targets.append(target_hint)
    if not targets:
        return ""
    return "已尝试目标：" + ", ".join(targets[:8])


def _active_payload_has_runtime_vision_frames(payload: dict[str, Any]) -> bool:
    if _active_runtime_vision_frame(payload.get("vision_frame")):
        return True
    return bool(_active_runtime_vision_frames(payload.get("vision_frames")))


def _active_visual_evidence_prefix(context: dict[str, Any], *, forced: bool, attached_active_frames: bool = False) -> str:
    prefix = build_grounded_context_prefix(context, forced=forced, attached_active_frames=attached_active_frames)
    attempted = _active_attempted_targets_line(context)
    lines = ["主动视觉证据："]
    if prefix:
        lines.append(prefix)
    elif forced:
        lines.extend(
            [
                "[强制视觉证据]",
                "约束：当前没有新鲜、可验证的主动截图观察结果。",
                "回答要求：必须回答“我无法从当前截图确认”，除非用户问题不需要屏幕内容。",
            ]
        )
    if attempted:
        lines.append(attempted)
    return "\n".join(line for line in lines if str(line or "").strip())


def _active_runtime_vision_frame(value: Any) -> dict[str, Any]:
    frame = value if isinstance(value, dict) else {}
    mime_type = str(frame.get("mime_type") or "").strip().lower()
    data_url = str(frame.get("data_url") or "").strip()
    if mime_type not in {"image/png", "image/jpeg"} or not data_url.startswith("data:image/"):
        return {}
    inline = {
        "mime_type": mime_type,
        "data_url": data_url,
        "frame_id": str(frame.get("frame_id") or "")[:80],
        "frame_hash": str(frame.get("frame_hash") or "")[:120],
    }
    purpose = str(frame.get("purpose") or "").strip()[:40]
    if purpose:
        inline["purpose"] = purpose
    if frame.get("max_image_bytes") is not None:
        inline["max_image_bytes"] = int(frame.get("max_image_bytes") or 0)
    return inline


def _active_runtime_vision_frames(value: Any, fallback: Any = None) -> list[dict[str, Any]]:
    raw_frames = value if isinstance(value, list) else []
    frames: list[dict[str, Any]] = []
    seen_data_urls: set[str] = set()
    for item in raw_frames[:3]:
        inline = _active_runtime_vision_frame(item)
        data_url = str(inline.get("data_url") or "")
        if inline and data_url not in seen_data_urls:
            seen_data_urls.add(data_url)
            frames.append(inline)
    if not frames:
        inline = _active_runtime_vision_frame(fallback)
        if inline:
            frames.append(inline)
    return frames


def _with_active_runtime_vision_frame(payload: dict[str, Any], active_result: dict[str, Any] | None) -> dict[str, Any]:
    result = active_result if isinstance(active_result, dict) else {}
    inline_frame = _active_runtime_vision_frame(result.get("inline_frame"))
    inline_frames = _active_runtime_vision_frames(result.get("inline_frames"), inline_frame)
    if not inline_frame and inline_frames:
        inline_frame = inline_frames[0]
    if not inline_frame:
        return payload
    next_payload = dict(payload)
    next_payload["vision_frame"] = inline_frame
    if inline_frames:
        next_payload["vision_frames"] = inline_frames
    return next_payload


def _passive_background_prefix(service: VisionService) -> str:
    prefix = service.passive_timeline_prefix()
    if not prefix:
        return ""
    return "\n".join(
        [
            "被动后台视觉：",
            prefix,
            "说明：这是后台观察到的近期屏幕变化；它不是本轮主动截图证据。",
        ]
    )


def _with_ipet_visual_evidence_payload(
    payload: dict[str, Any],
    req: ChatStreamRequest,
    settings_config: dict[str, Any],
    *,
    forced_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    service = _get_vision_service(settings_config)
    vision_cfg = settings_config.get("vision", {}) if isinstance(settings_config, dict) else {}
    forced = should_force_grounding(req.text, vision_cfg)
    should_inject = service.should_inject(req.text)
    passive_prefix = _passive_background_prefix(service)
    if not forced and not should_inject:
        return payload

    sections: list[str] = []
    if forced:
        if forced_context is not None:
            active_context = forced_context
        else:
            active_cfg = vision_cfg.get("active_observation") if isinstance(vision_cfg.get("active_observation"), dict) else {}
            reason = "active observation disabled" if active_cfg.get("enabled") is False else "active observation unavailable"
            active_context = {
                "enabled": bool((vision_cfg or {}).get("enabled")),
                "available": False,
                "grounded": False,
                "observations": [],
                "unknowns": [reason],
                "active_observation": {
                    "enabled": bool(active_cfg.get("enabled", True)),
                    "status": "skipped" if active_cfg.get("enabled") is False else "unavailable",
                    "unknowns": [reason],
                },
            }
        sections.append(
            _active_visual_evidence_prefix(
                active_context,
                forced=True,
                attached_active_frames=_active_payload_has_runtime_vision_frames(payload),
            )
        )
    elif should_inject:
        sections.append(build_grounded_context_prefix(service.context(include_image=False), forced=False))
    if passive_prefix:
        sections.append(passive_prefix)

    prefix = "\n\n".join(section for section in sections if str(section or "").strip())
    if not prefix:
        return payload
    next_payload = dict(payload)
    next_payload["text"] = f"{prefix}\n\n用户消息：{payload.get('text', req.text)}"
    return next_payload


async def _send_active_vision_capture_command(
    decision: dict[str, Any],
    vision_cfg: dict[str, Any],
    *,
    text: str,
    target_hint: str = "",
    target_id: str = "",
    mode: str = "",
    attempt_reason: str = "",
    exclude_seen: list[Any] | None = None,
    desktop_targets: list[Any] | None = None,
    target_candidates: list[Any] | None = None,
) -> dict[str, Any]:
    active_cfg = normalize_active_observation_config(vision_cfg.get("active_observation", {}))
    request_id = str(uuid4())
    response_path = RUNTIME_COMMAND_RESPONSE_PATH.with_name(f".pet_runtime_command.response.{request_id}.json")
    try:
        response_path.unlink(missing_ok=True)
    except Exception:
        pass
    command = _write_runtime_command_with_response(
        "active_vision_capture",
        {
            "request_id": request_id,
            "text": str(text or "")[:500],
            "mode": str(mode or decision.get("mode") or "desktop_survey")[:40],
            "target_id": str(target_id or decision.get("target_id") or "")[:120],
            "target_hint": str(target_hint or decision.get("target_hint") or "")[:80],
            "attempt_reason": str(attempt_reason or "")[:240],
            "exclude_seen": list(exclude_seen or [])[:10],
            "desktop_targets": list(desktop_targets or decision.get("desktop_targets") or [])[:12],
            "target_candidates": list(target_candidates or decision.get("target_candidates") or [])[:12],
            "actions": list(decision.get("actions") or []),
            "allowed_interaction": active_cfg["allowed_interaction"],
            "click_policy": str(decision.get("click_policy") or "window_focus_only")[:80],
            "settle_ms": int(active_cfg["settle_ms"]),
            "timeout_sec": float(active_cfg["timeout_sec"]),
        },
        response_path=response_path,
    )
    try:
        response = await asyncio.to_thread(
            _wait_for_runtime_command_response,
            nonce=str(command.get("nonce") or ""),
            response_path=response_path,
            timeout_sec=float(active_cfg["timeout_sec"]),
        )
    finally:
        try:
            response_path.unlink(missing_ok=True)
        except Exception:
            pass
    if not isinstance(response, dict):
        return {
            "ok": False,
            "error": "active vision host command timed out",
            "trace": {
                "status": "error",
                "mode": str(decision.get("mode") or mode or ""),
                "target_id": str(decision.get("target_id") or target_id or ""),
                "target_hint": str(decision.get("target_hint") or target_hint or ""),
                "actions": list(decision.get("actions") or []),
                "unknowns": ["active vision host command timed out"],
            },
        }
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    status = str(response.get("status") or "").strip().lower()
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else result.get("active_observation")
    if not isinstance(trace, dict):
        trace = {}
    if status == "success" and isinstance(result.get("frame"), dict):
        return {"ok": True, "frame": dict(result["frame"]), "trace": trace}
    error = str(result.get("error") or response.get("error") or "active vision host command failed").strip()
    return {
        "ok": False,
        "error": error,
        "trace": {
            **trace,
            "status": "error",
            "mode": str(trace.get("mode") or decision.get("mode") or mode or ""),
            "target_id": str(trace.get("target_id") or decision.get("target_id") or target_id or ""),
            "target_hint": str(trace.get("target_hint") or decision.get("target_hint") or target_hint or ""),
            "actions": list(trace.get("actions") or decision.get("actions") or []),
            "unknowns": list(trace.get("unknowns") or [error]),
            "error": error,
        },
    }


async def _perform_active_vision_observation(
    text: str,
    settings_config: dict[str, Any],
    *,
    force: bool = False,
    target_hint: str = "",
    attempt_reason: str = "",
    exclude_seen: list[Any] | None = None,
    defer_runtime_analysis: bool = False,
) -> dict[str, Any]:
    vision_cfg = normalize_vision_config(settings_config.get("vision", {}) if isinstance(settings_config, dict) else {})
    service = _get_vision_service(settings_config)
    active_cfg = normalize_active_observation_config(vision_cfg.get("active_observation", {}))
    deadline = time.monotonic() + min(ACTIVE_OBSERVE_TOTAL_TIMEOUT_SEC, float(active_cfg["timeout_sec"]))
    seen: list[Any] = list(exclude_seen or [])[:10]
    current_mode = "desktop_survey"
    current_target_id = ""
    current_target_hint = "desktop_survey"
    current_attempt_reason = str(attempt_reason or "")
    desktop_targets: list[Any] = []
    target_candidates: list[Any] = []
    discovery_errors: list[str] = []
    last_result: dict[str, Any] | None = None

    for attempt_number in range(ACTIVE_OBSERVE_MAX_ATTEMPTS):
        remaining_sec = max(0.0, deadline - time.monotonic())
        if attempt_number > 0 and remaining_sec < 1.0:
            break
        attempt_vision_cfg = _active_observation_timeout_config(vision_cfg, remaining_sec or float(active_cfg["timeout_sec"]))
        decision = decide_active_observation(
            text,
            attempt_vision_cfg,
            target_hint=current_target_id if current_mode == "focus_target" else "",
            force=force,
        )
        decision = {
            **decision,
            "mode": current_mode,
            "target_id": current_target_id or ("desktop_survey" if current_mode == "desktop_survey" else ""),
            "target_hint": current_target_hint,
            "desktop_targets": list(desktop_targets)[:12],
            "target_candidates": list(target_candidates)[:12],
            "actions": ["focus_target"] if current_mode == "focus_target" and active_cfg["allowed_interaction"] != "none" else [],
            "click_policy": "window_focus_only",
        }
        if not decision.get("needs_observation"):
            return {"ok": False, "skipped": True, "decision": decision, "context": service.context(include_image=False)}

        resolved_target_hint = str(decision.get("target_hint") or current_target_hint or "")
        host_result = await _send_active_vision_capture_command(
            decision,
            attempt_vision_cfg,
            text=text,
            target_hint=resolved_target_hint,
            target_id=str(decision.get("target_id") or current_target_id or ""),
            mode=str(decision.get("mode") or current_mode),
            attempt_reason=current_attempt_reason,
            exclude_seen=seen,
            desktop_targets=desktop_targets,
            target_candidates=target_candidates,
        )
        trace = host_result.get("trace") if isinstance(host_result.get("trace"), dict) else {}
        attempt_metadata_input = {
            "text": text,
            "mode": current_mode,
            "target_id": str(decision.get("target_id") or current_target_id or ""),
            "target_hint": resolved_target_hint,
            "attempt_reason": current_attempt_reason,
            "exclude_seen": list(seen)[:10],
        }
        if not host_result.get("ok") or not isinstance(host_result.get("frame"), dict):
            unknowns = list(trace.get("unknowns") or [])
            error = str(host_result.get("error") or trace.get("error") or "active vision capture failed").strip()
            if error and error not in unknowns:
                unknowns.append(error)
            trace = {
                **trace,
                **build_active_observe_metadata(
                    attempt_metadata_input,
                    {},
                    trace,
                    passive_context=service.passive_context(),
                ),
            }
            context = _active_observation_failure_context(service, unknowns, trace)
            return {"ok": False, "decision": decision, "trace": trace, "context": context, "error": error}

        frame_payload = dict(host_result["frame"])
        trace_desktop_targets = trace.get("desktop_targets") if isinstance(trace.get("desktop_targets"), list) else []
        frame_active = frame_payload.get("active_observation") if isinstance(frame_payload.get("active_observation"), dict) else {}
        frame_desktop_targets = frame_active.get("desktop_targets") if isinstance(frame_active.get("desktop_targets"), list) else []
        trace_target_candidates = trace.get("target_candidates") if isinstance(trace.get("target_candidates"), list) else []
        frame_target_candidates = frame_active.get("target_candidates") if isinstance(frame_active.get("target_candidates"), list) else []
        frame_discovery_errors = frame_active.get("discovery_errors") if isinstance(frame_active.get("discovery_errors"), list) else []
        trace_discovery_errors = trace.get("discovery_errors") if isinstance(trace.get("discovery_errors"), list) else []
        for item in list(frame_discovery_errors) + list(trace_discovery_errors):
            text_item = str(item or "").strip()
            if text_item and text_item not in discovery_errors:
                discovery_errors.append(text_item)
        if frame_desktop_targets:
            desktop_targets = list(frame_desktop_targets)[:12]
        elif trace_desktop_targets:
            desktop_targets = list(trace_desktop_targets)[:12]
        if frame_target_candidates:
            target_candidates = list(frame_target_candidates)[:12]
        elif trace_target_candidates:
            target_candidates = list(trace_target_candidates)[:12]
        active_metadata = build_active_observe_metadata(
            attempt_metadata_input,
            frame_payload,
            {**trace, "discovery_errors": discovery_errors},
            passive_context=service.passive_context(),
        )
        if defer_runtime_analysis and str(active_metadata.get("relevance_hint") or "") in {
            "insufficient_evidence",
            "insufficient_evidence:fallback_candidates",
        }:
            active_metadata = {
                **active_metadata,
                "relevance_hint": "likely_relevant:image_attached",
                "available_next_targets": [],
                "stop": True,
            }
        frame_payload["force_analyze"] = True
        frame_payload["vision_force_analyze"] = True
        frame_payload["active_observation"] = {
            **trace,
            **active_metadata,
            "enabled": True,
            "status": str(trace.get("status") or "success"),
            "mode": str(active_metadata.get("mode") or current_mode),
            "target_id": str(active_metadata.get("target_id") or decision.get("target_id") or current_target_id or ""),
            "target_hint": str(active_metadata.get("target_hint") or trace.get("target_hint") or resolved_target_hint),
            "actions": list(trace.get("actions") or decision.get("actions") or []),
            "click_policy": str(trace.get("click_policy") or decision.get("click_policy") or "window_focus_only"),
            "reason": str(trace.get("reason") or decision.get("reason") or ""),
        }
        analyzer_cfg = vision_cfg.get("analyzer", {}) if isinstance(vision_cfg.get("analyzer"), dict) else {}
        route_decision = service.evaluate_route(
            frame_payload,
            analyzer_enabled=bool(analyzer_cfg.get("enabled")),
            analysis_in_flight=False,
            force_analyze=True,
        )
        frame_payload["route_decision"] = route_decision
        try:
            if (
                not defer_runtime_analysis
                and bool(analyzer_cfg.get("enabled"))
                and route_decision.get("should_analyze")
            ):
                frame_payload = await _enrich_vision_payload(
                    frame_payload,
                    settings_config,
                    vision_cfg,
                    allow_runtime_fallback=False,
                )
                frame_payload["route_decision"] = route_decision
                frame_payload.setdefault(
                    "active_observation",
                    {
                        **trace,
                        "enabled": True,
                        "status": "success",
                        "target_hint": resolved_target_hint,
                        "actions": list(decision.get("actions") or []),
                    },
                )
        except Exception as exc:
            message = f"active vision analyzer failed: {exc}"
            frame_payload["observations"] = []
            frame_payload["unknowns"] = merge_unknowns(frame_payload.get("unknowns"), [message])
            frame_payload["analysis"] = {
                "enabled": bool(analyzer_cfg.get("enabled")),
                "provider": str(analyzer_cfg.get("provider") or "none"),
                "status": "error",
                "last_error": message,
                "observations_added": 0,
                "unknowns_added": 1,
            }
        try:
            frame_payload["lane"] = "active"
            service.update_frame(frame_payload)
            context = service.active_context(include_image=False)
            result = {
                "ok": bool(context.get("grounded")),
                "decision": decision,
                "trace": frame_payload.get("active_observation") or trace,
                "status": service.status(),
                "context": context,
                "inline_frame": _active_runtime_vision_frame(frame_payload),
                "inline_frames": _active_runtime_vision_frames(frame_payload.get("vision_frames"), frame_payload),
            }
            last_result = result
            if not _active_observation_should_retry(
                frame_payload.get("active_observation") if isinstance(frame_payload.get("active_observation"), dict) else active_metadata,
                grounded=bool(context.get("grounded")),
                deadline=deadline,
            ):
                return result
            seen_entry = _active_observation_seen_entry(
                frame_payload.get("active_observation") if isinstance(frame_payload.get("active_observation"), dict) else active_metadata
            )
            if any(seen_entry.values()):
                seen.append(seen_entry)
            next_target = _active_observation_next_target(
                frame_payload.get("active_observation") if isinstance(frame_payload.get("active_observation"), dict) else active_metadata
            )
            if not next_target:
                return result
            current_mode = "focus_target"
            current_target_id = next_target
            current_target_hint = next_target
            current_attempt_reason = (
                "retry after "
                + str(
                    (
                        frame_payload.get("active_observation")
                        if isinstance(frame_payload.get("active_observation"), dict)
                        else active_metadata
                    ).get("relevance_hint")
                    or "insufficient_evidence"
                )
            )[:240]
        except (VisionDisabledError, VisionError) as exc:
            trace = {**trace, "status": "error", "error": str(exc), "unknowns": [str(exc)]}
            context = _active_observation_failure_context(service, [str(exc)], trace)
            return {"ok": False, "decision": decision, "trace": trace, "context": context, "error": str(exc)}

    if last_result is not None:
        return last_result
    decision = decide_active_observation(text, vision_cfg, target_hint=target_hint, force=force)
    trace = {
        "status": "error",
        "target_hint": str(decision.get("target_hint") or target_hint or ""),
        "unknowns": ["active observation retry budget exhausted"],
    }
    context = _active_observation_failure_context(service, trace["unknowns"], trace)
    return {"ok": False, "decision": decision, "trace": trace, "context": context, "error": trace["unknowns"][0]}


def _with_vision_context_prefix(
    payload: dict[str, Any],
    req: ChatStreamRequest,
    settings_config: dict[str, Any],
    *,
    forced_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    service = _get_vision_service(settings_config)
    vision_cfg = settings_config.get("vision", {}) if isinstance(settings_config, dict) else {}
    forced = should_force_grounding(req.text, vision_cfg)
    if not forced and not service.should_inject(req.text):
        return payload
    if forced and forced_context is not None:
        context = forced_context
    elif forced and not (vision_cfg.get("passive_capture") or {}).get("use_for_forced", False):
        active_cfg = vision_cfg.get("active_observation") if isinstance(vision_cfg.get("active_observation"), dict) else {}
        reason = "active observation disabled" if active_cfg.get("enabled") is False else "active observation unavailable"
        context = {
            "enabled": bool((vision_cfg or {}).get("enabled")),
            "available": False,
            "grounded": False,
            "observations": [],
            "unknowns": [reason],
            "active_observation": {
                "enabled": bool(active_cfg.get("enabled", True)),
                "status": "skipped" if active_cfg.get("enabled") is False else "unavailable",
                "unknowns": [reason],
            },
        }
    else:
        context = service.context(include_image=False)
    prefix = build_grounded_context_prefix(context, forced=forced)
    if not prefix:
        return payload
    next_payload = dict(payload)
    next_payload["text"] = f"{prefix}\n\n用户消息：{payload.get('text', req.text)}"
    return next_payload


def _with_passive_vision_timeline_payload(
    payload: dict[str, Any],
    req: ChatStreamRequest,
    settings_config: dict[str, Any],
) -> dict[str, Any]:
    service = _get_vision_service(settings_config)
    prefix = service.passive_timeline_prefix()
    if not prefix:
        return payload
    lines = [
        prefix,
        "说明：这是后台观察到的近期屏幕变化；没有主动截图工具结果时，不要声称刚刚亲自看过屏幕。",
        "如果这些后台变化不足以回答视觉问题，应明确说明当前后台视觉无法确认。",
    ]
    next_payload = dict(payload)
    next_payload["text"] = f"{chr(10).join(lines)}\n\n用户消息：{payload.get('text', req.text)}"
    return next_payload


def _should_use_runtime_vision_fallback(analyzer_config: dict[str, Any], result_payload: dict[str, Any], status: dict[str, Any]) -> tuple[bool, str]:
    analyzer = normalize_analyzer_config(analyzer_config)
    if not analyzer.get("enabled") or not analyzer.get("fallback_to_runtime", True):
        return False, ""
    provider = str(analyzer.get("provider") or "").strip()
    observations = result_payload.get("observations") if isinstance(result_payload, dict) else None
    if provider in {"", "none"}:
        return True, "provider-empty"
    if str(status.get("status") or "") == "error":
        return True, str(status.get("last_error") or "provider-error")
    if not observations:
        return True, "no-observations"
    return False, ""


async def _runtime_vision_fallback(
    payload: dict[str, Any],
    settings_config: dict[str, Any],
    analyzer_config: dict[str, Any],
    reason: str = "",
) -> dict[str, Any]:
    runtime_client = _get_runtime_client(settings_config)
    if hasattr(runtime_client, "analyze_vision_frame"):
        result = await runtime_client.analyze_vision_frame(payload, analyzer_config)
        return result.payload
    message = f"active runtime vision fallback unavailable: {getattr(runtime_client, 'runtime_id', 'unknown')}"
    if reason:
        message += f"; reason={reason}"
    return merge_analysis_into_payload(
        payload,
        {"enabled": True, "provider": "active_runtime_vlm", **(analyzer_config if isinstance(analyzer_config, dict) else {})},
        {"observations": [], "unknowns": [message], "last_error": message},
    ).payload


async def _enrich_vision_payload(
    payload: dict[str, Any],
    settings_config: dict[str, Any],
    vision_cfg: dict[str, Any],
    *,
    allow_runtime_fallback: bool = False,
) -> dict[str, Any]:
    analyzer_cfg = vision_cfg.get("analyzer", {}) if isinstance(vision_cfg.get("analyzer"), dict) else {}
    result = await asyncio.to_thread(VisionAnalyzer(analyzer_cfg).enrich_payload, payload)
    use_fallback, reason = _should_use_runtime_vision_fallback(analyzer_cfg, result.payload, result.status)
    if allow_runtime_fallback and use_fallback:
        return await _runtime_vision_fallback(result.payload, settings_config, analyzer_cfg, reason=reason)
    return result.payload


def _pending_vision_analysis_metadata(analyzer_config: dict[str, Any]) -> dict[str, Any]:
    analyzer = normalize_analyzer_config(analyzer_config)
    return {
        "enabled": bool(analyzer.get("enabled")),
        "provider": str(analyzer.get("provider") or "none"),
        "status": "pending",
        "last_error": "",
        "observations_added": 0,
        "unknowns_added": 0,
    }


def _vision_frame_still_current(service: VisionService, payload: dict[str, Any]) -> bool:
    expected_hash = str(payload.get("frame_hash") or "").strip()
    if not expected_hash:
        return True
    try:
        current_hash = str(service.status().get("frame_hash") or "").strip()
    except Exception:
        current_hash = ""
    return not current_hash or current_hash == expected_hash


async def _complete_vision_analysis_task(
    payload: dict[str, Any],
    settings_config: dict[str, Any],
    vision_cfg: dict[str, Any],
    route_decision: dict[str, Any],
) -> None:
    try:
        try:
            enriched_payload = await _enrich_vision_payload(
                dict(payload),
                settings_config,
                vision_cfg,
                allow_runtime_fallback=False,
            )
            enriched_payload["route_decision"] = route_decision
        except Exception as exc:
            analyzer_cfg = vision_cfg.get("analyzer", {}) if isinstance(vision_cfg.get("analyzer"), dict) else {}
            message = f"vision analyzer failed: {exc}"
            enriched_payload = dict(payload)
            enriched_payload["unknowns"] = merge_unknowns(enriched_payload.get("unknowns"), [message])
            enriched_payload["analysis"] = {
                "enabled": bool(analyzer_cfg.get("enabled")),
                "provider": str(analyzer_cfg.get("provider") or "none"),
                "status": "error",
                "last_error": message,
                "observations_added": 0,
                "unknowns_added": 1,
            }
            enriched_payload["route_decision"] = route_decision

        service = _get_vision_service(settings_config)
        if _vision_frame_still_current(service, payload):
            service.update_frame(enriched_payload)
    except asyncio.CancelledError:
        return
    finally:
        if _VISION_ANALYSIS_LOCK.locked():
            _VISION_ANALYSIS_LOCK.release()


async def _runtime_json_or_unavailable(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = _get_runtime_client()
    try:
        return await client.request_json(method, path, json_payload=payload)
    except (HermesUnavailable, RuntimeUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Runtime request failed: {exc}") from exc


async def _hermes_json_or_unavailable(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = _get_hermes_client()
    try:
        return await client.request_json(method, path, json_payload=payload)
    except HermesUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Hermes request failed: {exc}") from exc


def _unavailable_inventory_payload(kind: str, detail: str) -> dict[str, Any]:
    key = "skills" if kind == "skills" else "servers"
    runtime_id = _runtime_config_from_raw().get("active", RUNTIME_HERMES)
    runtime_label = "AstrBot" if runtime_id == RUNTIME_ASTRBOT else "Hermes Agent"
    return {
        "ok": False,
        "runtime": runtime_id,
        "detail": detail,
        key: [],
        "storage": {
            "runtime": runtime_label,
            "proxied": True,
            "legacy": False,
        },
    }


async def _chat_stream_via_runtime(req: ChatStreamRequest) -> StreamingResponse:
    client = _get_runtime_client()
    payload = _chat_request_payload(req)
    settings_config = _load_settings_config()
    vision_cfg = normalize_vision_config(settings_config.get("vision", {}) if isinstance(settings_config, dict) else {})
    forced = should_force_grounding(req.text, vision_cfg)
    active_context: dict[str, Any] | None = None
    active_result: dict[str, Any] | None = None
    original_text = req.text
    if (
        forced
        and vision_cfg.get("enabled")
        and (vision_cfg.get("active_observation") or {}).get("enabled")
    ):
        active_result = await _perform_active_vision_observation(
            req.text,
            settings_config,
            force=True,
            defer_runtime_analysis=True,
        )
        context = active_result.get("context") if isinstance(active_result, dict) else None
        if isinstance(context, dict):
            active_context = context
    payload = _with_active_runtime_vision_frame(payload, active_result)
    payload = _with_ipet_visual_evidence_payload(payload, req, settings_config, forced_context=active_context)
    payload["original_text"] = original_text
    harness = MemoryHarness(
        conversation_store=_get_conversation_store(),
        memory_store=_get_ipet_memory_store(),
        runtime_client=client,
    )

    async def event_gen():
        try:
            runtime_events = harness.stream_chat(payload)
            async for event, data in realtime_stream_events(
                runtime_events,
                request_text=req.text,
                heartbeat_after_sec=_REALTIME_HEARTBEAT_AFTER_SEC,
                heartbeat_interval_sec=_REALTIME_HEARTBEAT_INTERVAL_SEC,
            ):
                yield _sse(event, data)
        except (HermesUnavailable, RuntimeUnavailable) as exc:
            yield _sse("error", {"message": str(exc), "runtime": getattr(client, "runtime_id", RUNTIME_HERMES)})
        except Exception as exc:
            yield _sse("error", {"message": f"Runtime stream failed: {exc}", "runtime": getattr(client, "runtime_id", RUNTIME_HERMES)})

    return StreamingResponse(event_gen(), media_type="text/event-stream")


async def _chat_stream_via_hermes(req: ChatStreamRequest) -> StreamingResponse:
    client = _get_hermes_client()
    payload = _chat_request_payload(req)

    async def event_gen():
        try:
            async for event, data in client.stream_sse("/api/chat/stream", payload):
                yield _sse(event, data)
        except HermesUnavailable as exc:
            yield _sse("error", {"message": str(exc), "runtime": "hermes"})
        except Exception as exc:
            yield _sse("error", {"message": f"Hermes stream failed: {exc}", "runtime": "hermes"})

    return StreamingResponse(event_gen(), media_type="text/event-stream")


async def _chat_approval_via_runtime(req: ChatApprovalRequest) -> StreamingResponse:
    client = _get_runtime_client()
    payload = _approval_request_payload(req)
    harness = MemoryHarness(
        conversation_store=_get_conversation_store(),
        memory_store=_get_ipet_memory_store(),
        runtime_client=client,
    )

    async def event_gen():
        try:
            async for event, data in harness.stream_approval(payload):
                yield _sse(event, data)
        except (HermesUnavailable, RuntimeUnavailable) as exc:
            yield _sse("error", {"message": str(exc), "runtime": getattr(client, "runtime_id", RUNTIME_HERMES)})
        except Exception as exc:
            yield _sse("error", {"message": f"Runtime approval failed: {exc}", "runtime": getattr(client, "runtime_id", RUNTIME_HERMES)})

    return StreamingResponse(event_gen(), media_type="text/event-stream")


async def _chat_approval_via_hermes(req: ChatApprovalRequest) -> StreamingResponse:
    client = _get_hermes_client()
    payload = _approval_request_payload(req)

    async def event_gen():
        try:
            async for event, data in client.stream_sse("/api/chat/approval", payload):
                yield _sse(event, data)
        except HermesUnavailable as exc:
            yield _sse("error", {"message": str(exc), "runtime": "hermes"})
        except Exception as exc:
            yield _sse("error", {"message": f"Hermes approval failed: {exc}", "runtime": "hermes"})

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _AGENT_GRAPH_RUNTIME, _MCP_BRIDGE, _SKILL_MANAGER, _CHAT_TOPIC_STORE, _CONVERSATION_STORE, _IPET_MEMORY_STORE, _ASR_SERVICE, _ASR_WARMUP_TASK, _VISION_SERVICE
    if _MCP_BRIDGE is not None:
        try:
            _MCP_BRIDGE.stop()
        except Exception:
            pass
        _MCP_BRIDGE = None
    _AGENT_GRAPH_RUNTIME = None
    _SKILL_MANAGER = None
    _CHAT_TOPIC_STORE = None
    _CONVERSATION_STORE = None
    _IPET_MEMORY_STORE = None
    _ASR_SERVICE = None
    _ASR_WARMUP_TASK = None
    _VISION_SERVICE = None
    _TURN_TOOL_BRIDGE_CACHE.clear()


def _is_local_request(request: Request) -> bool:
    host = ""
    try:
        host = str(request.client.host if request.client else "")
    except Exception:
        host = ""
    return host in {"", "127.0.0.1", "::1", "localhost", "testclient"} or host.startswith("127.")


def _require_local_api_token(request: Request) -> None:
    expected = str(os.environ.get(LOCAL_API_TOKEN_ENV) or "").strip()
    if not expected:
        raise HTTPException(status_code=403, detail="local API token is not configured")
    supplied = str(request.headers.get(LOCAL_API_TOKEN_HEADER) or "").strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="invalid local API token")


@app.get("/api/vision/status")
async def vision_status() -> dict[str, Any]:
    service = _get_vision_service(_load_settings_config())
    return service.status()


@app.post("/api/vision/frame")
async def vision_frame(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="vision frame capture is limited to local requests")
    _require_local_api_token(request)
    settings_config = _load_settings_config()
    service = _get_vision_service(settings_config)
    vision_cfg = normalize_vision_config(settings_config.get("vision", {}) if isinstance(settings_config, dict) else {})
    analysis_payload: dict[str, Any] | None = None
    analysis_route_decision: dict[str, Any] | None = None
    analysis_lock_acquired = False
    if vision_cfg["enabled"]:
        payload = dict(payload) if isinstance(payload, dict) else {}
        analyzer_cfg = vision_cfg.get("analyzer", {}) if isinstance(vision_cfg.get("analyzer"), dict) else {}
        route_decision = service.evaluate_route(
            payload,
            analyzer_enabled=bool(analyzer_cfg.get("enabled")),
            analysis_in_flight=_VISION_ANALYSIS_LOCK.locked(),
            force_analyze=bool(payload.get("force_analyze") or payload.get("vision_force_analyze")),
        )
        payload["route_decision"] = route_decision
        if route_decision.get("should_analyze"):
            analysis_lock_acquired = _VISION_ANALYSIS_LOCK.acquire(blocking=False)
            if analysis_lock_acquired:
                analysis_payload = dict(payload)
                analysis_route_decision = dict(route_decision)
                payload["analysis"] = _pending_vision_analysis_metadata(analyzer_cfg)
                payload["route_decision"] = {**route_decision, "pending_analysis": True}
            else:
                route_decision = service.evaluate_route(
                    payload,
                    analyzer_enabled=bool(analyzer_cfg.get("enabled")),
                    analysis_in_flight=True,
                    force_analyze=bool(payload.get("force_analyze") or payload.get("vision_force_analyze")),
                )
                payload["route_decision"] = route_decision
    try:
        status = service.update_frame(payload)
    except VisionDisabledError as exc:
        if analysis_lock_acquired and _VISION_ANALYSIS_LOCK.locked():
            _VISION_ANALYSIS_LOCK.release()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VisionError as exc:
        if analysis_lock_acquired and _VISION_ANALYSIS_LOCK.locked():
            _VISION_ANALYSIS_LOCK.release()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if analysis_payload is not None and analysis_route_decision is not None:
        analysis_payload["frame_id"] = status.get("frame_id") or analysis_payload.get("frame_id")
        analysis_payload["frame_hash"] = status.get("frame_hash") or analysis_payload.get("frame_hash")
        background_tasks.add_task(
            _complete_vision_analysis_task,
            analysis_payload,
            settings_config,
            vision_cfg,
            analysis_route_decision,
        )
    return status


@app.post("/api/vision/observe")
async def vision_observe(
    request: Request,
    lane: str = "active",
    include_image: bool = False,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="vision observe is limited to local requests")
    _require_local_api_token(request)
    settings_config = _load_settings_config()
    vision_cfg = normalize_vision_config(settings_config.get("vision", {}) if isinstance(settings_config, dict) else {})
    if not vision_cfg["enabled"]:
        raise HTTPException(status_code=409, detail="vision is disabled")
    data = payload if isinstance(payload, dict) else {}
    requested_lane = str(lane or "active").strip().lower()
    if requested_lane != "active":
        raise HTTPException(status_code=400, detail="vision observe only supports lane=active")
    result = await _perform_active_vision_observation(
        str(data.get("text") or ""),
        settings_config,
        force=bool(data.get("force")),
        target_hint=str(data.get("target_hint") or ""),
        attempt_reason=str(data.get("attempt_reason") or ""),
        exclude_seen=data.get("exclude_seen") if isinstance(data.get("exclude_seen"), list) else [],
    )
    service = _get_vision_service(settings_config)
    context = result.get("context") if isinstance(result.get("context"), dict) else service.active_context(include_image=False)
    if include_image:
        context = service.active_context(include_image=True)
    image_urls: list[str] = []
    image = context.get("image") if isinstance(context, dict) and isinstance(context.get("image"), dict) else {}
    data_url = str(image.get("data_url") or "")
    if include_image and data_url.startswith("data:image/") and "," in data_url:
        image_urls.append("base64://" + data_url.split(",", 1)[1])
    result_frames = result.get("inline_frames") if isinstance(result.get("inline_frames"), list) else []
    if include_image and result_frames:
        image_urls = []
        for frame in result_frames[:3]:
            frame_url = str(frame.get("data_url") or "") if isinstance(frame, dict) else ""
            if frame_url.startswith("data:image/") and "," in frame_url:
                image_urls.append("base64://" + frame_url.split(",", 1)[1])
    active_observation = context.get("active_observation") if isinstance(context.get("active_observation"), dict) else {}
    return {
        "ok": bool(result.get("ok")),
        "lane": "active",
        "status": result.get("status") if isinstance(result.get("status"), dict) else service.status(),
        "context": context,
        "desktop_targets": active_observation.get("desktop_targets") if isinstance(active_observation.get("desktop_targets"), list) else [],
        "target_candidates": active_observation.get("target_candidates") if isinstance(active_observation.get("target_candidates"), list) else [],
        "discovery_errors": active_observation.get("discovery_errors") if isinstance(active_observation.get("discovery_errors"), list) else [],
        "selected_candidate": active_observation.get("selected_candidate") if isinstance(active_observation.get("selected_candidate"), dict) else {},
        "focused_target": active_observation.get("focused_target") if isinstance(active_observation.get("focused_target"), dict) else {},
        "focus_result": active_observation.get("focus_result") if isinstance(active_observation.get("focus_result"), dict) else {},
        "verify_result": active_observation.get("verify_result") if isinstance(active_observation.get("verify_result"), dict) else {},
        "detail_frames_count": int(active_observation.get("detail_frames_count") or 0),
        "action_trace": active_observation.get("action_trace") if isinstance(active_observation.get("action_trace"), list) else [],
        "click_point": active_observation.get("click_point") if isinstance(active_observation.get("click_point"), dict) else {},
        "frame_hash": str(context.get("frame_hash") or ""),
        "available_next_targets": active_observation.get("available_next_targets") if isinstance(active_observation.get("available_next_targets"), list) else [],
        "image_urls": image_urls,
        "decision": result.get("decision") if isinstance(result.get("decision"), dict) else {},
        "trace": result.get("trace") if isinstance(result.get("trace"), dict) else {},
        "error": str(result.get("error") or ""),
    }


@app.get("/api/vision/context")
async def vision_context(request: Request, include_image: bool = False, lane: str = "merged") -> dict[str, Any]:
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="vision context is limited to local requests")
    _require_local_api_token(request)
    service = _get_vision_service(_load_settings_config())
    requested_lane = str(lane or "merged").strip().lower()
    if requested_lane == "passive":
        return service.passive_context()
    if requested_lane == "active":
        context = service.active_context(include_image=include_image)
        context["lane"] = "active"
        return context
    if requested_lane != "merged":
        raise HTTPException(status_code=400, detail="lane must be passive, active, or merged")
    context = service.context(include_image=include_image)
    context["lane"] = "merged"
    return context


@app.post("/api/vision/models")
async def vision_models(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    saved = _load_settings_config()
    saved_analyzer = (
        (saved.get("vision") or {}).get("analyzer")
        if isinstance(saved.get("vision"), dict)
        else {}
    )
    source = dict(saved_analyzer if isinstance(saved_analyzer, dict) else {})
    incoming = payload if isinstance(payload, dict) else {}
    source.update({key: value for key, value in incoming.items() if key != "api_key" or str(value or "").strip()})
    analyzer = normalize_analyzer_config(source)
    if not analyzer.get("base_url"):
        raise HTTPException(status_code=400, detail="VLM Base URL is required to fetch model list.")
    api_key = str(incoming.get("api_key") or source.get("api_key") or analyzer.get("api_key") or "").strip()
    try:
        models = await provider_list_models(
            provider="openai_compat",
            base_url=analyzer["base_url"],
            api_key=api_key,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"VLM model list failed: {exc}") from exc
    return {
        "models": models,
        "provider": analyzer.get("provider") or "openai_compatible_vlm",
        "base_url": analyzer["base_url"],
    }


@app.post("/api/vision/clear")
async def vision_clear(request: Request) -> dict[str, Any]:
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="vision clear is limited to local requests")
    _require_local_api_token(request)
    service = _get_vision_service(_load_settings_config())
    return service.clear()


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
        "vision": _get_vision_service(settings_config).status(),
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
    config = _load_settings_config()
    return {
        "config": _public_settings_config(config),
        "defaults": _public_settings_config(_normalize_settings_config(_default_settings_config())),
        "tts_presets": TTS_PRESETS,
        "mcp_server_presets": MCP_SERVER_PRESETS,
    }


@app.get("/api/runtime/status")
async def get_runtime_status() -> dict[str, Any]:
    client = _get_runtime_client()
    try:
        return await client.status()
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "configured": False,
            "runtime": _runtime_config_from_raw().get("active", RUNTIME_HERMES),
            "detail": str(exc),
        }


@app.get("/api/hermes/status")
async def get_hermes_status() -> dict[str, Any]:
    return await _get_hermes_client().status()


@app.put("/api/settings/config")
async def put_settings_config(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if isinstance(payload, dict):
        draft = payload.get("mcp_draft")
        if isinstance(draft, dict):
            draft_name = str(draft.get("name") or "").strip()
            draft_json = str(draft.get("config_json") or "")
            if draft_json.strip():
                await _runtime_json_or_unavailable(
                    "POST",
                    "/api/mcp/create-config",
                    payload={"name": draft_name, "config_json": draft_json},
                )
    previous = _load_settings_config()
    raw_config = payload.get("config", payload) if isinstance(payload, dict) else {}
    if not isinstance(raw_config, dict):
        raise HTTPException(status_code=400, detail="settings payload must be an object")
    merged = _deep_merge(_load_settings_config(), raw_config)
    apply_runtime_secret_actions(merged, raw_config, previous)
    _apply_vision_analyzer_secret_actions(merged, raw_config, previous)
    normalized = _normalize_settings_config(merged)
    _save_full_config(normalized)
    if normalized.get("model_path") and normalized.get("model_path") != previous.get("model_path"):
        _write_runtime_command("load_model", {"model_path": normalized["model_path"]})
    return {
        "config": _public_settings_config(normalized),
        "defaults": _public_settings_config(_normalize_settings_config(_default_settings_config())),
        "tts_presets": TTS_PRESETS,
        "mcp_server_presets": MCP_SERVER_PRESETS,
    }


@app.get("/api/skills")
async def list_skills() -> dict[str, Any]:
    client = _get_runtime_client()
    try:
        return await client.request_json("GET", "/api/skills")
    except Exception as exc:
        return _unavailable_inventory_payload("skills", f"Runtime skills are unavailable: {exc}")


@app.post("/api/skills/import-local")
async def import_local_skill(req: SkillImportLocalRequest) -> dict[str, Any]:
    return await _runtime_json_or_unavailable("POST", "/api/skills/import-local", payload={
        "path": req.path,
        "directory": req.directory,
        "name": req.name,
    })


@app.post("/api/skills/import-git")
async def import_git_skill(req: SkillImportGitRequest) -> dict[str, Any]:
    return await _runtime_json_or_unavailable("POST", "/api/skills/import-git", payload={
        "url": req.url,
        "repo_url": req.repo_url,
        "name": req.name,
        "ref": req.ref,
        "branch": req.branch,
        "subdir": req.subdir,
    })


@app.post("/api/skills/delete")
async def delete_skill(req: SkillDeleteRequest) -> dict[str, Any]:
    return await _runtime_json_or_unavailable("POST", "/api/skills/delete", payload={
        "skill_id": req.skill_id,
        "id": req.id,
        "name": req.name,
    })


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
    return await _chat_stream_via_runtime(req)

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
        runtime_cfg = _hermes_runtime_config(router_cfg.get("model"))
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

        if chat_mode == CHAT_MODE_CHAT and not route_kind:
            route_kind = "direct_answer"

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
                "model": runtime_cfg["model"],
                "runtime": "hermes",
                "tool_mode": req.tool_mode,
                "tools_enabled": tools_enabled,
                "chat_mode": chat_mode,
                "router_used": router_used,
                "route_kind": route_kind,
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
                        "model": runtime_cfg["model"],
                        "llm_provider": runtime_cfg["llm_provider"],
                        "api_base_url": runtime_cfg["api_base_url"],
                        "api_key": runtime_cfg["api_key"],
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
                model=runtime_cfg["model"],
                llm_provider=runtime_cfg["llm_provider"],
                api_base_url=runtime_cfg["api_base_url"],
                api_key=runtime_cfg["api_key"],
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
                    settings_config=settings_config,
                ),
            )
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
    return await _chat_approval_via_runtime(req)

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
                model=str(state.get("model") or DEFAULT_HERMES_MODEL),
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
        settings_config = state.get("settings_config") if isinstance(state.get("settings_config"), dict) else _load_settings_config()
        try:
            finalize_result = await perf.time_await(
                "finalize_exchange",
                _finalize_chat_exchange(
                    session_id=session_id,
                    user_text=str(state.get("user_text") or ""),
                    assistant_text=full_answer,
                    working_messages=list(state.get("working_messages") or []),
                    settings_config=settings_config,
                ),
            )
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
    raise HTTPException(status_code=410, detail="Hermes manages memory internally; app-level memory decisions are disabled.")


@app.post("/api/chat/topics")
async def create_chat_topic(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    store = _get_conversation_store()
    topic = store.create_conversation(
        conversation_id=str(source.get("session_id") or source.get("topic_id") or source.get("conversation_id") or ""),
        title=str(source.get("title") or ""),
        memory_mode=str(source.get("memory_mode") or "persistent"),
        persisted=True,
        runtime="",
    )
    return {
        "ok": True,
        "runtime": "ipet",
        "enabled": True,
        "topic_id": topic["topic_id"],
        "session_id": topic["session_id"],
        "conversation_id": topic["conversation_id"],
        "persisted": bool(topic.get("persisted", True)),
        "topic": topic,
    }


@app.get("/api/chat/topics")
async def list_chat_topics() -> dict[str, Any]:
    return {
        "ok": True,
        "runtime": "ipet",
        "enabled": True,
        "topics": _get_conversation_store().list_topics(),
    }


@app.get("/api/chat/topics/{topic_id}")
async def get_chat_topic(topic_id: str) -> dict[str, Any]:
    detail = _get_conversation_store().get_topic_detail(normalize_conversation_id(topic_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="chat topic not found")
    return detail


@app.delete("/api/chat/topics/{topic_id}")
@app.post("/api/chat/topics/{topic_id}/delete")
async def delete_chat_topic(topic_id: str) -> dict[str, Any]:
    conversation_id = normalize_conversation_id(topic_id)
    SESSION_STORE.pop(conversation_id, None)
    payload = _get_conversation_store().delete_conversation(conversation_id)
    return {
        **payload,
        "runtime": "ipet",
        "deleted": True,
    }


@app.post("/api/models")
async def models() -> dict[str, Any]:
    runtime_id = _runtime_config_from_raw().get("active", RUNTIME_HERMES)
    return {
        "models": [],
        "runtime": runtime_id,
        "deprecated": True,
        "detail": "Model listing is managed by the active runtime; settings only store the current model/session hint.",
    }


@app.get("/api/mcp/servers")
async def list_mcp_servers() -> dict[str, Any]:
    client = _get_runtime_client()
    try:
        return await client.request_json("GET", "/api/mcp/servers")
    except Exception as exc:
        return _unavailable_inventory_payload("mcp", f"Runtime MCP is unavailable: {exc}")


@app.get("/api/mcp/health")
async def mcp_health() -> dict[str, Any]:
    client = _get_runtime_client()
    try:
        return await client.request_json("GET", "/api/mcp/health")
    except Exception as exc:
        return _unavailable_inventory_payload("mcp", f"Runtime MCP health is unavailable: {exc}")


@app.post("/api/mcp/create-config")
async def create_mcp_from_config(req: MCPServerCreateRequest) -> dict[str, Any]:
    return await _runtime_json_or_unavailable("POST", "/api/mcp/create-config", payload={
        "config_json": req.config_json,
        "name": req.name,
    })


@app.post("/api/mcp/delete")
async def delete_mcp(req: MCPDeleteRequest) -> dict[str, Any]:
    return await _runtime_json_or_unavailable("POST", "/api/mcp/delete", payload={"name": req.name})


@app.post("/api/mcp/toggle")
async def toggle_mcp(req: MCPToggleRequest) -> dict[str, Any]:
    return await _runtime_json_or_unavailable("POST", "/api/mcp/toggle", payload={"name": req.name, "enabled": req.enabled})


@app.post("/api/mcp/reload")
async def reload_mcp() -> dict[str, Any]:
    return await _runtime_json_or_unavailable("POST", "/api/mcp/reload", payload={})


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
