from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .agent_orchestrator import stream_reply
from .mcp_bridge import MCPBridge
from .models import (
    ChatStreamRequest,
    MCPInstallRequest,
    MCPRegisterLocalRequest,
    MCPToggleRequest,
    ModelListRequest,
    TTSRequest,
)
from .ollama_client import OLLAMA_BASE_URL, is_ollama_alive, list_models
from .tts import (
    DEFAULT_PROVIDER,
    cleanup_old_audio,
    list_supported_providers,
    synthesize_to_mp3,
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
SESSION_STORE: dict[str, list[dict[str, str]]] = {}
EXPR_OUTPUT_FORMAT = "ndjson_v1"
DEFAULT_TOOL_TIMEOUT_SEC = 180
LEGACY_TOOL_TIMEOUT_SEC = 10
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

_MCP_BRIDGE: MCPBridge | None = None
_MCP_CONFIG_SNAPSHOT = ""


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


def _load_runtime_tooling_config() -> dict[str, Any]:
    data = _load_full_config()
    chat = data.get("chat", {}) if isinstance(data, dict) else {}
    tooling = chat.get("tooling", {}) if isinstance(chat, dict) else {}
    merged = _deep_merge(DEFAULT_TOOLING, tooling if isinstance(tooling, dict) else {})
    merged["mode"] = "mcp_local_phase2"
    if not merged.get("file_allowlist"):
        merged["file_allowlist"] = [str(ROOT_DIR)]
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


def _trim_messages(messages: list[dict[str, str]], memory_window: int) -> list[dict[str, str]]:
    limit = max(1, memory_window) * 2
    if len(messages) <= limit:
        return messages
    return messages[-limit:]


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sanitize_ai_text(text: str) -> str:
    return (text or "").replace("*", "").replace("#", "")


def _build_system_prompt(user_prompt: str, expression_mode: bool, output_format: str) -> str:
    base = (user_prompt or "").strip()
    if not expression_mode or output_format != EXPR_OUTPUT_FORMAT:
        return base
    return f"{base}\n\n{EXPR_PROTOCOL_PROMPT}" if base else EXPR_PROTOCOL_PROMPT


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


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _MCP_BRIDGE
    if _MCP_BRIDGE is not None:
        try:
            _MCP_BRIDGE.stop()
        except Exception:
            pass
        _MCP_BRIDGE = None


@app.get("/api/health")
async def health() -> dict[str, Any]:
    ollama_ok = await is_ollama_alive(OLLAMA_BASE_URL)
    tooling_cfg = _load_runtime_tooling_config()
    bridge = _get_mcp_bridge()
    return {
        "ok": True,
        "ollama": ollama_ok,
        "tts": tts_available(DEFAULT_PROVIDER),
        "tools": bool(tooling_cfg.get("enabled", True)),
        "third_party_mcp": bridge.health(),
    }


@app.post("/api/chat/stream")
async def chat_stream(req: ChatStreamRequest) -> StreamingResponse:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty")

    async def event_gen():
        session_id = req.session_id or "default"
        tooling_cfg = _load_runtime_tooling_config()
        history = SESSION_STORE.get(session_id, [])
        history = _trim_messages(history, req.memory_window)
        working = history + [{"role": "user", "content": text}]
        prompt_msgs = working
        sys_prompt = _build_system_prompt(
            req.system_prompt,
            bool(req.expression_mode),
            str(req.expression_output_format or ""),
        )
        if sys_prompt:
            prompt_msgs = [{"role": "system", "content": sys_prompt}] + working

        tools_enabled = bool(tooling_cfg.get("enabled", True) and req.tools_enabled)
        yield _sse(
            "meta",
            {
                "session_id": session_id,
                "model": req.model,
                "llm_provider": req.llm_provider,
                "tool_mode": req.tool_mode,
                "tools_enabled": tools_enabled,
            },
        )
        full_answer = ""
        try:
            mcp_bridge = _get_mcp_bridge() if tools_enabled else None
            delta_stream = stream_reply(
                messages=prompt_msgs,
                model=req.model,
                tools_enabled=tools_enabled,
                mcp_bridge=mcp_bridge,
                max_tool_calls=int(tooling_cfg.get("max_tool_calls_per_turn", 6)),
                llm_provider=req.llm_provider,
                api_base_url=req.api_base_url,
                api_key=req.api_key,
            )
            use_ndjson = bool(req.expression_mode) and str(req.expression_output_format or "") == EXPR_OUTPUT_FORMAT
            if use_ndjson:
                async for segment in _iter_ndjson_segments(delta_stream):
                    seg_text = str(segment.get("text") or "")
                    seg_expr = segment.get("expr")
                    if not seg_text:
                        continue
                    full_answer += seg_text
                    yield _sse("segment", {"text": seg_text, "expr": seg_expr})
                    yield _sse("token", {"delta": seg_text})
            else:
                async for delta in delta_stream:
                    clean_delta = _sanitize_ai_text(delta)
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
    bridge = _get_mcp_bridge()
    return {"servers": bridge.list_servers()}


@app.get("/api/mcp/health")
async def mcp_health() -> dict[str, Any]:
    bridge = _get_mcp_bridge()
    return bridge.health()


@app.post("/api/mcp/install")
async def install_mcp(req: MCPInstallRequest) -> dict[str, Any]:
    try:
        bridge = _get_mcp_bridge()
        result = bridge.install_from_git(req.repo_url, req.name)
        _update_third_party_config(
            {
                "name": result["name"],
                "enabled": True,
                "source_type": "git",
                "source": req.repo_url,
                "manifest_path": result["manifest_path"],
                "runtime": result["runtime"],
            }
        )
        bridge = _get_mcp_bridge(force_reload=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"install mcp failed: {exc}") from exc
    return {"ok": True, "server": next((s for s in bridge.list_servers() if s.get("name") == result["name"]), result)}


@app.post("/api/mcp/register-local")
async def register_local_mcp(req: MCPRegisterLocalRequest) -> dict[str, Any]:
    try:
        bridge = _get_mcp_bridge()
        result = bridge.register_local(req.path, req.name)
        _update_third_party_config(
            {
                "name": result["name"],
                "enabled": True,
                "source_type": "local",
                "source": req.path,
                "manifest_path": result["manifest_path"],
                "runtime": result["runtime"],
            }
        )
        bridge = _get_mcp_bridge(force_reload=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"register local mcp failed: {exc}") from exc
    return {"ok": True, "server": next((s for s in bridge.list_servers() if s.get("name") == result["name"]), result)}


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
        file_id, _, duration_ms = await synthesize_to_mp3(
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
        "audio_url": f"/api/audio/{file_id}.mp3",
        "duration_ms": duration_ms,
        "provider": provider,
        "supported_providers": list_supported_providers(),
    }


@app.get("/api/audio/{file_id}.mp3")
async def get_audio(file_id: str) -> FileResponse:
    path = AUDIO_CACHE_DIR / f"{file_id}.mp3"
    if not path.exists():
        raise HTTPException(status_code=404, detail="audio file not found")
    return FileResponse(path, media_type="audio/mpeg", filename=path.name)
