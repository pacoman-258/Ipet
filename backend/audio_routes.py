from __future__ import annotations

import mimetypes
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from .models import TTSRequest
from .tts import is_qwen_tts_local_provider


@dataclass(frozen=True)
class AudioRouteDependencies:
    audio_cache_dir: Path
    tts_available: Callable[[str | None, str | None], bool]
    cleanup_old_audio: Callable[[Path], None]
    synthesize_to_audio: Callable[..., Awaitable[Any]]
    stream_qwen_tts_local: Callable[..., AsyncIterator[bytes]] | None = None


def safe_audio_path(file_name: str, *, audio_cache_dir: Path) -> Path:
    candidate = Path(file_name)
    if candidate.name != file_name:
        raise HTTPException(status_code=404, detail="Audio file not found.")
    path = audio_cache_dir / candidate.name
    try:
        path.relative_to(audio_cache_dir)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Audio file not found.") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found.")
    return path


async def synthesize_tts_response(request: TTSRequest, *, deps: AudioRouteDependencies) -> dict[str, Any]:
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="text is required.")
    if not deps.tts_available(request.provider, request.provider_url):
        if is_qwen_tts_local_provider(request.provider):
            raise HTTPException(status_code=503, detail="TTS异常")
        raise HTTPException(status_code=503, detail=f"TTS provider is unavailable: {request.provider}")
    try:
        deps.cleanup_old_audio(deps.audio_cache_dir)
        result = await deps.synthesize_to_audio(
            text=request.text,
            cache_dir=deps.audio_cache_dir,
            voice=request.voice,
            rate=request.rate,
            volume=request.volume,
            provider=request.provider,
            provider_url=request.provider_url,
        )
    except Exception as exc:
        if is_qwen_tts_local_provider(request.provider):
            raise HTTPException(status_code=503, detail="TTS异常") from exc
        raise HTTPException(status_code=503, detail=f"TTS synthesis failed: {exc}") from exc
    return {
        "ok": True,
        "file_id": result.file_id,
        "url": f"/api/audio/{result.path.name}",
        "audio_url": f"/api/audio/{result.path.name}",
        "duration_ms": result.duration_ms,
        "media_type": result.media_type,
    }


async def synthesize_qwen_tts_stream_response(
    request: TTSRequest,
    *,
    deps: AudioRouteDependencies,
) -> StreamingResponse:
    """Expose Qwen's PCM stream while keeping the normal TTS route unchanged."""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="text is required.")
    if not is_qwen_tts_local_provider(request.provider):
        raise HTTPException(status_code=400, detail="Streaming TTS only supports qwenTTS local.")
    if not deps.tts_available(request.provider, request.provider_url) or not deps.stream_qwen_tts_local:
        raise HTTPException(status_code=503, detail="TTS异常")

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in deps.stream_qwen_tts_local(
                text=request.text,
                provider_url=request.provider_url,
            ):
                yield chunk
        except Exception:
            # Headers may already be sent.  The frontend recognizes this final
            # event and surfaces the same concise Qwen error as the normal API.
            yield b'{"type":"error","detail":"TTS error"}\n'

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store"},
    )


def audio_response(file_name: str, *, audio_cache_dir: Path) -> FileResponse:
    path = safe_audio_path(file_name, audio_cache_dir=audio_cache_dir)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


def create_audio_router(deps: AudioRouteDependencies) -> APIRouter:
    router = APIRouter()

    @router.post("/api/tts")
    async def tts_route(request: TTSRequest) -> dict[str, Any]:
        return await synthesize_tts_response(request, deps=deps)

    @router.post("/api/tts/stream")
    async def tts_stream_route(request: TTSRequest) -> StreamingResponse:
        return await synthesize_qwen_tts_stream_response(request, deps=deps)

    @router.get("/api/audio/{file_name}")
    async def audio_route(file_name: str) -> FileResponse:
        return audio_response(file_name, audio_cache_dir=deps.audio_cache_dir)

    return router


def register_audio_routes(app: FastAPI, deps: AudioRouteDependencies) -> None:
    app.include_router(create_audio_router(deps))
