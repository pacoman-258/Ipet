from __future__ import annotations

import base64
import time
import uuid
from pathlib import Path

import httpx

try:
    import edge_tts
except Exception:
    edge_tts = None

DEFAULT_PROVIDER = "edge_tts"


def normalize_provider(provider: str | None) -> str:
    value = str(provider or "").strip().lower()
    if not value:
        return DEFAULT_PROVIDER
    return value


def list_supported_providers() -> list[str]:
    return ["edge_tts", "custom_http"]


def tts_available(provider: str | None = None, provider_url: str | None = None) -> bool:
    p = normalize_provider(provider)
    if p == "edge_tts":
        return edge_tts is not None
    if p == "custom_http":
        return bool(str(provider_url or "").strip())
    return False


def cleanup_old_audio(cache_dir: Path, max_age_seconds: int = 1800) -> None:
    if not cache_dir.exists():
        return
    now = time.time()
    for path in cache_dir.glob("*.mp3"):
        try:
            if now - path.stat().st_mtime > max_age_seconds:
                path.unlink(missing_ok=True)
        except Exception:
            continue


async def _synthesize_edge_tts(
    *,
    text: str,
    cache_dir: Path,
    output_path: Path,
    voice: str,
    rate: str,
    volume: str,
) -> int:
    if edge_tts is None:
        raise RuntimeError("edge-tts is not installed.")
    communicator = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)
    await communicator.save(str(output_path))
    return max(600, int(len(text) * 220))


async def _synthesize_custom_http(
    *,
    text: str,
    output_path: Path,
    provider_url: str,
    voice: str,
    rate: str,
    volume: str,
) -> int:
    url = str(provider_url or "").strip()
    if not url:
        raise RuntimeError("custom_http provider_url is empty.")

    payload = {"text": text, "voice": voice, "rate": rate, "volume": volume}
    timeout = httpx.Timeout(connect=8.0, read=60.0, write=20.0, pool=8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload)
    resp.raise_for_status()

    content_type = (resp.headers.get("content-type") or "").lower()
    audio_bytes: bytes | None = None
    duration_ms = max(600, int(len(text) * 220))

    # Custom endpoint can return raw audio bytes, or JSON with audio_base64.
    if "application/json" in content_type:
        body = resp.json()
        b64 = str(body.get("audio_base64", "") or "").strip()
        if not b64:
            raise RuntimeError("custom_http response missing audio_base64.")
        audio_bytes = base64.b64decode(b64)
        try:
            duration_ms = int(body.get("duration_ms", duration_ms))
        except Exception:
            pass
    else:
        audio_bytes = resp.content

    if not audio_bytes:
        raise RuntimeError("custom_http response has empty audio bytes.")

    output_path.write_bytes(audio_bytes)
    return duration_ms


async def synthesize_to_mp3(
    *,
    text: str,
    cache_dir: Path,
    voice: str = "zh-CN-XiaoxiaoNeural",
    rate: str = "+0%",
    volume: str = "+0%",
    provider: str = DEFAULT_PROVIDER,
    provider_url: str = "",
) -> tuple[str, Path, int]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4().hex
    output_path = cache_dir / f"{file_id}.mp3"
    p = normalize_provider(provider)

    if p == "edge_tts":
        duration_ms = await _synthesize_edge_tts(
            text=text,
            cache_dir=cache_dir,
            output_path=output_path,
            voice=voice,
            rate=rate,
            volume=volume,
        )
        return file_id, output_path, duration_ms

    if p == "custom_http":
        duration_ms = await _synthesize_custom_http(
            text=text,
            output_path=output_path,
            provider_url=provider_url,
            voice=voice,
            rate=rate,
            volume=volume,
        )
        return file_id, output_path, duration_ms

    raise RuntimeError(f"Unsupported TTS provider: {provider}")
