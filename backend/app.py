from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from brain.decisions import BrainDecision
from brain.llm import BrainLLMError, run_brain_turn

from .chat_topics import DEFAULT_TOPIC_TITLE, TopicStore, normalize_topic_id
from .models import TTSRequest
from .tts import (
    DEFAULT_PROVIDER as DEFAULT_TTS_PROVIDER,
    cleanup_old_audio,
    synthesize_to_audio,
    tts_available,
)


app = FastAPI(title="Ipet Neo Aspect Backend", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "pet_config.json"
AUDIO_CACHE_DIR = ROOT_DIR / "backend" / "audio_cache"
CHAT_TOPICS_ROOT = ROOT_DIR / "data" / "chat_topics"
SETTINGS_HTML_PATH = ROOT_DIR / "settings.html"
SETTINGS_CSS_PATH = ROOT_DIR / "settings.css"
SETTINGS_JS_PATH = ROOT_DIR / "settings.js"

TOPIC_STORE = TopicStore(CHAT_TOPICS_ROOT)

REMOVED_DETAIL = "This old management endpoint was removed in Ipet Neo Aspect."

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
        "persona": "你是 Ipet，一个有身体、有记忆、会一步一步操作电脑的陪伴助手。",
        "self_state": "等待用户目标，并在 act / remember / learn_skill 前请求批准。",
        "response_style": "lively",
        "decision_temperature": 0.4,
    },
    "human_ops": {
        "observe_screen": True,
        "accessibility": True,
        "require_act_review": True,
        "require_memory_review": True,
        "require_skill_review": True,
        "clipboard_write_review": True,
        "click_preview": {"x": 160, "y": 54, "label": "目标位置", "size": 16},
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


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if key not in base:
            continue
        base_value = base.get(key)
        if isinstance(base_value, dict):
            if isinstance(value, dict):
                merged[key] = _deep_merge(base_value, value)
            continue
        if isinstance(value, dict):
            continue
        merged[key] = deepcopy(value)
    return merged


def _load_raw_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_private_config(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else _load_raw_config()
    selected = {key: source[key] for key in ALLOWED_CONFIG_KEYS if key in source}
    config = _deep_merge(NEO_DEFAULTS, selected)
    brain = config.setdefault("brain", {})
    raw_brain = source.get("brain") if isinstance(source.get("brain"), dict) else {}
    if raw_brain.get("api_key"):
        brain["api_key"] = str(raw_brain.get("api_key") or "")
    return config


def _secret_preview(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "*" * len(text)
    return f"{text[:2]}***{text[-2:]}"


def _public_config(private_config: dict[str, Any]) -> dict[str, Any]:
    public = deepcopy(private_config)
    brain = public.get("brain")
    if not isinstance(brain, dict):
        brain = {}
        public["brain"] = brain
    secret = str(brain.pop("api_key", "") or "")
    brain.pop("api_key_clear", None)
    brain["api_key_set"] = bool(secret)
    brain["api_key_preview"] = _secret_preview(secret)
    return public


def _settings_payload(private_config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = _normalize_private_config(private_config)
    return {
        "config": _public_config(config),
        "defaults": _public_config(_normalize_private_config(NEO_DEFAULTS)),
    }


def _save_config(private_config: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(
        json.dumps(private_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sanitize_brain_error(error: Exception, brain_config: dict[str, Any]) -> str:
    text = str(error or "").strip() or "unknown provider error"
    secret = str(brain_config.get("api_key") or "")
    endpoint = str(brain_config.get("model_endpoint") or "")
    for sensitive in (secret, endpoint):
        if sensitive:
            text = text.replace(sensitive, "[redacted]")
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|token|key)=)[^\s&]+", r"\1[redacted]", text)
    return text[:240] + "..." if len(text) > 240 else text


def _decision_from_completion(completion: Any) -> BrainDecision:
    decision = getattr(completion, "decision", None)
    if isinstance(decision, BrainDecision):
        return decision
    return BrainDecision.say(str(getattr(completion, "text", "") or ""))


def _gone() -> None:
    raise HTTPException(status_code=410, detail=REMOVED_DETAIL)


def _safe_audio_path(file_name: str) -> Path:
    candidate = Path(file_name)
    if candidate.name != file_name:
        raise HTTPException(status_code=404, detail="Audio file not found.")
    path = AUDIO_CACHE_DIR / candidate.name
    try:
        path.relative_to(AUDIO_CACHE_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Audio file not found.") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found.")
    return path


@app.get("/api/health")
async def health() -> dict[str, Any]:
    config = _normalize_private_config()
    return {
        "status": "ok",
        "service": "ipet-neo-aspect-backend",
        "version": app.version,
        "neo_aspect": {
            "body": True,
            "brain": True,
            "human_ops": True,
            "memory": bool(config.get("memory", {}).get("conversation_saving", True)),
            "skills": bool(config.get("skills", {}).get("recipes_enabled", True)),
        },
        "asr": False,
        "tts": tts_available(
            config.get("chat", {}).get("tts_provider"),
            config.get("chat", {}).get("tts_provider_url"),
        ),
        "message": "Neo Aspect backend is running with the new Body, Brain, Human Ops, Memory & Skills contract.",
    }


@app.get("/settings")
async def settings_page() -> FileResponse:
    if not SETTINGS_HTML_PATH.exists():
        raise HTTPException(status_code=404, detail="settings.html not found.")
    return FileResponse(SETTINGS_HTML_PATH)


@app.get("/settings.css")
async def settings_css() -> FileResponse:
    if not SETTINGS_CSS_PATH.exists():
        raise HTTPException(status_code=404, detail="settings.css not found.")
    return FileResponse(SETTINGS_CSS_PATH, media_type="text/css")


@app.get("/settings.js")
async def settings_js() -> FileResponse:
    if not SETTINGS_JS_PATH.exists():
        raise HTTPException(status_code=404, detail="settings.js not found.")
    return FileResponse(SETTINGS_JS_PATH, media_type="application/javascript")


@app.get("/api/settings/config")
async def get_settings_config() -> dict[str, Any]:
    return _settings_payload()


@app.put("/api/settings/config")
async def put_settings_config(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    incoming = payload.get("config") if isinstance(payload, dict) else {}
    if not isinstance(incoming, dict):
        raise HTTPException(status_code=400, detail="config must be an object.")

    current = _normalize_private_config()
    next_config = _deep_merge(current, incoming)
    incoming_brain = incoming.get("brain") if isinstance(incoming.get("brain"), dict) else {}
    brain = next_config.setdefault("brain", {})
    if incoming_brain.get("api_key_clear"):
        brain.pop("api_key", None)
    elif "api_key" in incoming_brain:
        secret = str(incoming_brain.get("api_key") or "")
        if secret:
            brain["api_key"] = secret
    brain.pop("api_key_clear", None)

    sanitized = _normalize_private_config(next_config)
    _save_config(sanitized)
    return _settings_payload(sanitized)


@app.post("/api/chat/stream")
async def chat_stream(payload: dict[str, Any] | None = Body(default=None)) -> StreamingResponse:
    request_payload = payload if isinstance(payload, dict) else {}
    text = str(request_payload.get("text") or request_payload.get("message") or "").strip()
    session_id = normalize_topic_id(str(request_payload.get("session_id") or "default"))
    turn_id = uuid4().hex
    private_config = _normalize_private_config()
    brain_config = private_config.get("brain", {}) if isinstance(private_config.get("brain"), dict) else {}
    model = str(request_payload.get("model") or brain_config.get("model_name") or "gpt-5.4")
    endpoint_configured = bool(str(brain_config.get("model_endpoint") or "").strip())
    provider_hint = str(brain_config.get("provider") or "openai_compatible")
    initial_provider = provider_hint if endpoint_configured else "local_placeholder"

    async def resolve_reply() -> tuple[str, str, str, BrainDecision]:
        if not endpoint_configured:
            reply = (
                "Neo Brain placeholder: 我已经收到你的消息。当前还没有配置 Brain 模型端点；"
                "请在设置页选择 provider 并填写模型端点。"
            )
            return (
                reply,
                initial_provider,
                model,
                BrainDecision.say(reply),
            )
        try:
            completion = await run_brain_turn(
                brain_config,
                user_text=text,
                request_system_prompt=str(request_payload.get("system_prompt") or ""),
            )
            decision = _decision_from_completion(completion)
            return str(completion.text or decision.summary), completion.provider, completion.model, decision
        except BrainLLMError as exc:
            detail = _sanitize_brain_error(exc, brain_config)
            reply = f"Brain 调用失败：{detail}"
            return reply, provider_hint, model, BrainDecision.say(reply)
        except Exception as exc:
            detail = _sanitize_brain_error(exc, brain_config)
            reply = f"Brain 调用失败：{detail}"
            return reply, provider_hint, model, BrainDecision.say(reply)

    async def event_stream():
        yield _sse(
            "meta",
            {
                "turn_id": turn_id,
                "session_id": session_id,
                "backend": "neo_aspect",
                "provider": initial_provider,
                "model": model,
            },
        )
        yield _sse("phase", {"name": "neo_brain", "status": "running", "text": f"Brain provider: {initial_provider}"})
        reply, provider, used_model, decision = await resolve_reply()
        await asyncio.sleep(0)
        yield _sse("token", {"text": reply})
        yield _sse("segment", {"text": reply, "expression": "normal"})
        yield _sse("display_segment", {"text": reply})
        TOPIC_STORE.append_exchange(session_id, user_text=text, assistant_text=reply)
        yield _sse(
            "done",
            {
                "turn_id": turn_id,
                "session_id": session_id,
                "text": reply,
                "provider": provider,
                "model": used_model,
                "decision": decision.to_dict(),
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/skills")
async def list_skills() -> dict[str, Any]:
    return {"skills": [], "recipes": [], "items": []}


@app.post("/api/chat/topics")
async def create_chat_topic(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    topic_id = body.get("topic_id") or body.get("session_id")
    title = str(body.get("title") or DEFAULT_TOPIC_TITLE)
    persisted = bool(body.get("persisted", True))
    topic = TOPIC_STORE.create_topic(topic_id=topic_id, title=title, persisted=persisted)
    return {"ok": True, "topic": topic, **topic}


@app.get("/api/chat/topics")
async def list_chat_topics() -> dict[str, Any]:
    return {"ok": True, "topics": TOPIC_STORE.list_topics()}


@app.get("/api/chat/topics/{topic_id}")
async def get_chat_topic(topic_id: str) -> dict[str, Any]:
    detail = TOPIC_STORE.get_topic_detail(topic_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Topic not found.")
    return {"ok": True, **detail}


@app.delete("/api/chat/topics/{topic_id}")
async def delete_chat_topic(topic_id: str) -> dict[str, Any]:
    deleted = TOPIC_STORE.delete_topic(topic_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Topic not found.")
    return {"ok": True, "deleted": True, "topics": TOPIC_STORE.list_topics()}


@app.post("/api/chat/topics/{topic_id}/delete")
async def delete_chat_topic_post(topic_id: str) -> dict[str, Any]:
    return await delete_chat_topic(topic_id)


@app.post("/api/asr/warmup")
async def asr_warmup() -> dict[str, Any]:
    return {
        "ok": False,
        "enabled": False,
        "available": False,
        "detail": "ASR is disabled in the Neo Aspect minimal backend.",
    }


@app.websocket("/api/asr/stream")
async def asr_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json(
        {
            "type": "error",
            "detail": "ASR streaming is disabled in the Neo Aspect minimal backend.",
        }
    )
    await websocket.close(code=1000)


@app.post("/api/tts")
async def tts(request: TTSRequest) -> dict[str, Any]:
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="text is required.")
    if not tts_available(request.provider, request.provider_url):
        raise HTTPException(status_code=503, detail=f"TTS provider is unavailable: {request.provider}")
    try:
        cleanup_old_audio(AUDIO_CACHE_DIR)
        result = await synthesize_to_audio(
            text=request.text,
            cache_dir=AUDIO_CACHE_DIR,
            voice=request.voice,
            rate=request.rate,
            volume=request.volume,
            provider=request.provider,
            provider_url=request.provider_url,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"TTS synthesis failed: {exc}") from exc
    return {
        "ok": True,
        "file_id": result.file_id,
        "url": f"/api/audio/{result.path.name}",
        "audio_url": f"/api/audio/{result.path.name}",
        "duration_ms": result.duration_ms,
        "media_type": result.media_type,
    }


@app.get("/api/audio/{file_name}")
async def audio(file_name: str) -> FileResponse:
    path = _safe_audio_path(file_name)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@app.get("/api/runtime/status")
async def runtime_status() -> None:
    _gone()


@app.get("/api/hermes/status")
async def hermes_status() -> None:
    _gone()


@app.get("/api/mcp/servers")
async def mcp_servers() -> None:
    _gone()


@app.get("/api/mcp/health")
async def mcp_health() -> None:
    _gone()


@app.post("/api/mcp/create-config")
async def mcp_create_config(_: Request) -> None:
    _gone()


@app.post("/api/mcp/delete")
async def mcp_delete(_: Request) -> None:
    _gone()


@app.post("/api/mcp/toggle")
async def mcp_toggle(_: Request) -> None:
    _gone()


@app.post("/api/mcp/reload")
async def mcp_reload(_: Request) -> None:
    _gone()


@app.post("/api/skills/import-local")
async def skills_import_local(_: Request) -> None:
    _gone()


@app.post("/api/skills/import-git")
async def skills_import_git(_: Request) -> None:
    _gone()


@app.post("/api/skills/delete")
async def skills_delete(_: Request) -> None:
    _gone()


@app.post("/api/models")
async def models(_: Request) -> None:
    _gone()


@app.post("/api/chat/approval")
async def chat_approval(_: Request) -> None:
    _gone()


@app.post("/api/chat/memory/decision")
async def chat_memory_decision(_: Request) -> None:
    _gone()
