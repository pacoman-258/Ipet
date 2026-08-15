from __future__ import annotations

import json
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .asr import (
    ASRError,
    ASRService,
    DEFAULT_ASR_CONFIG,
    DEFAULT_ASR_LANGUAGE,
    DEFAULT_ASR_PROVIDER,
    DEFAULT_GROQ_ASR_API_URL,
    DEFAULT_GROQ_ASR_MODEL,
    GroqWhisperRuntime,
    normalize_push_to_talk_key,
    _default_asr_runtime,
)
from .parent_watchdog import start_parent_watchdog

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "pet_config.json"

app = FastAPI(title="Desktop Pet ASR Backend", version="0.1.0")
_PARENT_WATCHDOG_THREAD = start_parent_watchdog()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_ASR_SERVICE: ASRService | None = None
_ASR_SERVICE_SIGNATURE: tuple[str, str, str, str, bool] | None = None
_ASR_WARMUP_TASK: asyncio.Task | None = None


def _is_macos() -> bool:
    return str(sys.platform or "").strip().lower() == "darwin"


def _default_asr_enabled() -> bool:
    return True


def _asr_disabled_message() -> str:
    return "ASR is disabled in settings."


def _load_settings_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _asr_config(settings_config: dict[str, Any]) -> dict[str, Any]:
    chat_cfg = settings_config.get("chat", {}) if isinstance(settings_config, dict) else {}
    asr_cfg = chat_cfg.get("asr", {}) if isinstance(chat_cfg, dict) else {}
    if not isinstance(asr_cfg, dict):
        asr_cfg = {}
    return {
        "enabled": bool(asr_cfg.get("enabled", _default_asr_enabled())),
        "provider": str(asr_cfg.get("provider") or DEFAULT_ASR_PROVIDER).strip() or DEFAULT_ASR_PROVIDER,
        "provider_url": str(asr_cfg.get("provider_url") or DEFAULT_GROQ_ASR_API_URL).strip()
        or DEFAULT_GROQ_ASR_API_URL,
        "model": str(asr_cfg.get("model") or DEFAULT_GROQ_ASR_MODEL).strip() or DEFAULT_GROQ_ASR_MODEL,
        "api_key": str(asr_cfg.get("api_key") or "").strip(),
        "push_to_talk_key": normalize_push_to_talk_key(asr_cfg.get("push_to_talk_key")),
        "interim_results": bool(asr_cfg.get("interim_results", DEFAULT_ASR_CONFIG["interim_results"])),
    }


def _asr_api_key(config: dict[str, Any]) -> str:
    return str(config.get("api_key") or os.environ.get("GROQ_API_KEY") or "").strip()


def _create_asr_runtime(config: dict[str, Any]):
    provider = str(config.get("provider") or DEFAULT_ASR_PROVIDER).strip().lower()
    if provider == "groq":
        return GroqWhisperRuntime(
            api_key=_asr_api_key(config),
            api_url=str(config.get("provider_url") or DEFAULT_GROQ_ASR_API_URL),
            model=str(config.get("model") or DEFAULT_GROQ_ASR_MODEL),
        )
    return _default_asr_runtime()


def _get_asr_service(force_reload: bool = False) -> ASRService:
    global _ASR_SERVICE, _ASR_SERVICE_SIGNATURE
    config = _asr_config(_load_settings_config())
    signature = (
        str(config.get("provider") or ""),
        str(config.get("provider_url") or ""),
        str(config.get("model") or ""),
        _asr_api_key(config),
        bool(config.get("interim_results")),
    )
    if force_reload or _ASR_SERVICE is None or (_ASR_SERVICE_SIGNATURE is not None and signature != _ASR_SERVICE_SIGNATURE):
        _ASR_SERVICE = ASRService(runtime=_create_asr_runtime(config))
    _ASR_SERVICE_SIGNATURE = signature
    return _ASR_SERVICE


@app.on_event("startup")
async def startup_event() -> None:
    global _ASR_WARMUP_TASK
    if not _asr_config(_load_settings_config())["enabled"]:
        return
    service = _get_asr_service()
    if not service.available():
        return
    if _ASR_WARMUP_TASK is None or _ASR_WARMUP_TASK.done():
        _ASR_WARMUP_TASK = asyncio.create_task(service.warmup(), name="ipet-asr-warmup")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global _ASR_WARMUP_TASK
    task = _ASR_WARMUP_TASK
    _ASR_WARMUP_TASK = None
    if task is None or task.done():
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    asr_cfg = _asr_config(_load_settings_config())
    service = _get_asr_service()
    readiness_message = ""
    asr_ok = False
    if asr_cfg["enabled"]:
        readiness_message = service.readiness_message()
        asr_ok = not readiness_message
    elif _is_macos():
        readiness_message = _asr_disabled_message()
    return {
        "ok": True,
        "asr": bool(asr_ok),
        "provider": asr_cfg["provider"],
        "message": readiness_message,
    }


@app.post("/api/asr/warmup")
async def asr_warmup() -> dict[str, Any]:
    asr_cfg = _asr_config(_load_settings_config())
    if not asr_cfg["enabled"]:
        return {
            "ok": False,
            "enabled": False,
            "available": False,
            "ready": False,
            "message": _asr_disabled_message(),
        }
    service = _get_asr_service()
    message = await service.warmup()
    return {
        "ok": not bool(message),
        "enabled": True,
        "available": service.available(),
        "ready": not bool(message),
        "message": str(message or ""),
    }


@app.websocket("/api/asr/stream")
async def asr_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    asr_cfg = _asr_config(_load_settings_config())
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
