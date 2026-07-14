from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx
from body.qwen_tts import (
    QWEN_TTS_LANGUAGE,
    QWEN_TTS_LOCAL_PROVIDER,
    QWEN_TTS_MODEL,
    QWEN_TTS_REFERENCE_AUDIO,
    QWEN_TTS_REFERENCE_ID,
    qwen_tts_clone_url,
    qwen_tts_stream_url,
)

try:
    import edge_tts
except Exception:
    edge_tts = None

DEFAULT_PROVIDER = "edge_tts"
DEFAULT_AUDIO_DURATION_MS = 600
DEFAULT_CUSTOM_HTTP_TIMEOUT_SEC = 60.0
QWEN_TTS_LOCAL_TIMEOUT_SEC = 180.0

MEDIA_TYPE_TO_SUFFIX = {
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
    "audio/x-flac": ".flac",
    "audio/x-m4a": ".m4a",
    "audio/x-wav": ".wav",
}

STANDARD_CUSTOM_HTTP_FIELDS = ("text", "voice", "rate", "volume")
PATH_LIKE_KEYWORDS = ("path", "file")
CONTROL_CHAR_ESCAPES = {
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


@dataclass(slots=True)
class SynthesizedAudio:
    file_id: str
    path: Path
    duration_ms: int
    media_type: str


def normalize_provider(provider: str | None) -> str:
    value = str(provider or "").strip().lower()
    if not value:
        return DEFAULT_PROVIDER
    return value


def list_supported_providers() -> list[str]:
    return ["edge_tts", "custom_http", QWEN_TTS_LOCAL_PROVIDER]


def is_qwen_tts_local_provider(provider: str | None) -> bool:
    return normalize_provider(provider) == QWEN_TTS_LOCAL_PROVIDER


def tts_available(provider: str | None = None, provider_url: str | None = None) -> bool:
    p = normalize_provider(provider)
    if p == "edge_tts":
        return edge_tts is not None
    if p == "custom_http":
        return bool(str(provider_url or "").strip())
    if p == QWEN_TTS_LOCAL_PROVIDER:
        return QWEN_TTS_REFERENCE_AUDIO.is_file()
    return False


def cleanup_old_audio(cache_dir: Path, max_age_seconds: int = 1800) -> None:
    if not cache_dir.exists():
        return
    now = time.time()
    for path in cache_dir.iterdir():
        if not path.is_file():
            continue
        try:
            if now - path.stat().st_mtime > max_age_seconds:
                path.unlink(missing_ok=True)
        except Exception:
            continue


def _guess_duration_ms(text: str) -> int:
    return max(DEFAULT_AUDIO_DURATION_MS, int(len(text) * 220))


def _normalize_media_type(value: str | None) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _decode_base64_audio(value: str) -> bytes:
    raw = str(value or "").strip()
    if not raw:
        return b""
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    return base64.b64decode(raw)


def _escape_invalid_json_backslashes(raw: str) -> str:
    out: list[str] = []
    in_string = False
    pending_backslash = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue

        if pending_backslash:
            if ch in '"\\/bfnrt':
                out.append("\\")
                out.append(ch)
            elif ch == "u" and i + 4 < len(raw) and all(c in "0123456789abcdefABCDEF" for c in raw[i + 1 : i + 5]):
                out.append("\\")
                out.append(ch)
            else:
                out.append("\\\\")
                out.append(ch)
            pending_backslash = False
            i += 1
            continue

        if ch == "\\":
            pending_backslash = True
            i += 1
            continue

        out.append(ch)
        if ch == '"':
            in_string = False
        i += 1

    if pending_backslash:
        out.append("\\\\")
    return "".join(out)


def _load_custom_http_json_config(raw: str) -> dict[str, object]:
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as first_exc:
        escaped = _escape_invalid_json_backslashes(raw)
        if escaped != raw:
            try:
                config = json.loads(escaped)
            except json.JSONDecodeError:
                hint = " If you pasted a Windows path, try D:/path/file.wav or keep \\ escaped."
                raise RuntimeError(f"custom_http provider_url JSON is invalid: {first_exc.msg}.{hint}") from first_exc
        else:
            hint = " If you pasted a Windows path, try D:/path/file.wav or keep \\ escaped."
            raise RuntimeError(f"custom_http provider_url JSON is invalid: {first_exc.msg}.{hint}") from first_exc
    if not isinstance(config, dict):
        raise RuntimeError("custom_http provider_url JSON must be an object.")
    return _repair_path_like_values(config)


def _repair_path_like_values(value, key: str = ""):
    if isinstance(value, dict):
        return {str(k): _repair_path_like_values(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_repair_path_like_values(item, key) for item in value]
    if isinstance(value, str):
        lower_key = key.lower()
        if any(token in lower_key for token in PATH_LIKE_KEYWORDS) and any(ch in value for ch in CONTROL_CHAR_ESCAPES):
            repaired = value
            for bad, escaped in CONTROL_CHAR_ESCAPES.items():
                repaired = repaired.replace(bad, escaped)
            return repaired
    return value


def _guess_audio_format(audio_bytes: bytes, media_type: str | None) -> tuple[str, str]:
    normalized = _normalize_media_type(media_type)
    if normalized in MEDIA_TYPE_TO_SUFFIX:
        return MEDIA_TYPE_TO_SUFFIX[normalized], normalized

    if normalized.startswith("audio/"):
        subtype = normalized.split("/", 1)[1].strip().removeprefix("x-")
        if subtype in {"wave", "wav"}:
            return ".wav", "audio/wav"
        if subtype == "mpeg":
            return ".mp3", "audio/mpeg"
        if subtype:
            return f".{subtype}", normalized

    if audio_bytes.startswith(b"RIFF") and audio_bytes[8:12] == b"WAVE":
        return ".wav", "audio/wav"
    if audio_bytes.startswith(b"ID3") or audio_bytes[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return ".mp3", "audio/mpeg"
    if audio_bytes.startswith(b"OggS"):
        return ".ogg", "audio/ogg"
    if audio_bytes.startswith(b"fLaC"):
        return ".flac", "audio/flac"
    if len(audio_bytes) >= 12 and audio_bytes[4:8] == b"ftyp":
        return ".m4a", "audio/mp4"

    return ".mp3", "audio/mpeg"


def _parse_custom_http_config(
    provider_url: str,
    *,
    text: str,
    voice: str,
    rate: str,
    volume: str,
) -> tuple[str, dict[str, str], dict[str, str], dict[str, object], httpx.Timeout]:
    raw = str(provider_url or "").strip()
    if not raw:
        raise RuntimeError("custom_http provider_url is empty.")

    headers: dict[str, str] = {}
    query: dict[str, str] = {}
    payload: dict[str, object] = {
        "text": text,
        "voice": voice,
        "rate": rate,
        "volume": volume,
    }
    timeout_sec = DEFAULT_CUSTOM_HTTP_TIMEOUT_SEC
    url = raw

    if raw.startswith("{"):
        config = _load_custom_http_json_config(raw)

        url = str(config.get("url") or "").strip()
        if not url:
            raise RuntimeError("custom_http provider_url JSON missing url.")

        payload_cfg = config.get("payload", {})
        headers_cfg = config.get("headers", {})
        query_cfg = config.get("query", {})
        inject_fields_cfg = config.get("inject_fields", list(STANDARD_CUSTOM_HTTP_FIELDS))
        if payload_cfg is not None and not isinstance(payload_cfg, dict):
            raise RuntimeError("custom_http payload must be an object.")
        if headers_cfg is not None and not isinstance(headers_cfg, dict):
            raise RuntimeError("custom_http headers must be an object.")
        if query_cfg is not None and not isinstance(query_cfg, dict):
            raise RuntimeError("custom_http query must be an object.")
        if inject_fields_cfg is None:
            inject_fields: list[str] = list(STANDARD_CUSTOM_HTTP_FIELDS)
        elif isinstance(inject_fields_cfg, list) and all(isinstance(item, str) for item in inject_fields_cfg):
            inject_fields = [item.strip() for item in inject_fields_cfg if item.strip()]
        else:
            raise RuntimeError("custom_http inject_fields must be a string array.")

        payload = dict(payload_cfg or {})
        runtime_fields = {
            "text": text,
            "voice": voice,
            "rate": rate,
            "volume": volume,
        }
        unknown_fields = [item for item in inject_fields if item not in runtime_fields]
        if unknown_fields:
            joined = ", ".join(sorted(set(unknown_fields)))
            raise RuntimeError(f"custom_http inject_fields contains unsupported fields: {joined}")
        for field_name in inject_fields:
            payload[field_name] = runtime_fields[field_name]
        headers = {str(k): str(v) for k, v in (headers_cfg or {}).items()}
        query = {str(k): str(v) for k, v in (query_cfg or {}).items()}

        try:
            timeout_sec = float(config.get("timeout_sec", DEFAULT_CUSTOM_HTTP_TIMEOUT_SEC))
        except Exception as exc:
            raise RuntimeError("custom_http timeout_sec must be a number.") from exc
        timeout_sec = min(max(timeout_sec, 1.0), 600.0)

    timeout = httpx.Timeout(connect=8.0, read=timeout_sec, write=20.0, pool=8.0)
    return url, headers, query, payload, timeout


async def _extract_custom_http_audio(
    *,
    client: httpx.AsyncClient,
    response: httpx.Response,
    request_url: str,
    text: str,
) -> tuple[bytes, int, str, str]:
    response.raise_for_status()
    content_type = _normalize_media_type(response.headers.get("content-type"))
    audio_bytes: bytes | None = None
    duration_ms = _guess_duration_ms(text)
    media_type = content_type

    if content_type == "application/json" or content_type.endswith("+json"):
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("custom_http JSON response must be an object.")

        media_type = _normalize_media_type(body.get("media_type") or body.get("content_type") or content_type)
        try:
            duration_ms = int(body.get("duration_ms", duration_ms))
        except Exception:
            pass

        base64_value = ""
        for key in ("audio_base64", "audio_bytes_base64", "audio"):
            value = str(body.get(key) or "").strip()
            if value:
                base64_value = value
                break

        if base64_value:
            audio_bytes = _decode_base64_audio(base64_value)
        else:
            audio_url = ""
            for key in ("audio_url", "url"):
                value = str(body.get(key) or "").strip()
                if value:
                    audio_url = value
                    break
            if audio_url:
                nested_resp = await client.get(urljoin(request_url, audio_url))
                nested_resp.raise_for_status()
                audio_bytes = nested_resp.content
                nested_type = _normalize_media_type(nested_resp.headers.get("content-type"))
                if nested_type:
                    media_type = nested_type
            else:
                raise RuntimeError("custom_http JSON response missing audio_base64/audio_url.")
    else:
        audio_bytes = response.content

    if not audio_bytes:
        raise RuntimeError("custom_http response has empty audio bytes.")

    suffix, media_type = _guess_audio_format(audio_bytes, media_type)
    return audio_bytes, duration_ms, suffix, media_type


async def _synthesize_edge_tts(
    *,
    text: str,
    output_path: Path,
    voice: str,
    rate: str,
    volume: str,
) -> int:
    if edge_tts is None:
        raise RuntimeError("edge-tts is not installed.")
    communicator = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)
    await communicator.save(str(output_path))
    return _guess_duration_ms(text)


async def _synthesize_custom_http(
    *,
    text: str,
    cache_dir: Path,
    file_id: str,
    provider_url: str,
    voice: str,
    rate: str,
    volume: str,
) -> SynthesizedAudio:
    url, headers, query, payload, timeout = _parse_custom_http_config(
        provider_url,
        text=text,
        voice=voice,
        rate=rate,
        volume=volume,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, params=query, headers=headers, json=payload)
        audio_bytes, duration_ms, suffix, media_type = await _extract_custom_http_audio(
            client=client,
            response=resp,
            request_url=url,
            text=text,
        )

    output_path = cache_dir / f"{file_id}{suffix}"
    output_path.write_bytes(audio_bytes)
    return SynthesizedAudio(
        file_id=file_id,
        path=output_path,
        duration_ms=duration_ms,
        media_type=media_type,
    )


async def _synthesize_qwen_tts_local(
    *,
    text: str,
    cache_dir: Path,
    file_id: str,
    provider_url: str,
) -> SynthesizedAudio:
    if not QWEN_TTS_REFERENCE_AUDIO.is_file():
        raise RuntimeError("Qwen TTS reference audio is unavailable.")

    endpoint = qwen_tts_clone_url(provider_url)
    timeout = httpx.Timeout(connect=2.0, read=QWEN_TTS_LOCAL_TIMEOUT_SEC, write=20.0, pool=2.0)
    payload = {
        "model": QWEN_TTS_MODEL,
        "input": text,
        "reference_id": QWEN_TTS_REFERENCE_ID,
        "language": QWEN_TTS_LANGUAGE,
    }
    # The local service must not inherit a global SOCKS/HTTP proxy.  Some
    # Ipet environments set ALL_PROXY while not installing httpx[socks].
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.post(endpoint, data=payload)
        audio_bytes, duration_ms, suffix, media_type = await _extract_custom_http_audio(
            client=client,
            response=response,
            request_url=endpoint,
            text=text,
        )

    output_path = cache_dir / f"{file_id}{suffix}"
    output_path.write_bytes(audio_bytes)
    return SynthesizedAudio(
        file_id=file_id,
        path=output_path,
        duration_ms=duration_ms,
        media_type=media_type,
    )


async def stream_qwen_tts_local(
    *,
    text: str,
    provider_url: str = "",
) -> AsyncIterator[bytes]:
    """Proxy Qwen's native PCM stream without buffering it into a WAV file."""
    if not QWEN_TTS_REFERENCE_AUDIO.is_file():
        raise RuntimeError("Qwen TTS reference audio is unavailable.")

    endpoint = qwen_tts_stream_url(provider_url)
    timeout = httpx.Timeout(connect=2.0, read=QWEN_TTS_LOCAL_TIMEOUT_SEC, write=20.0, pool=2.0)
    payload = {
        "model": QWEN_TTS_MODEL,
        "input": text,
        "reference_id": QWEN_TTS_REFERENCE_ID,
        "language": QWEN_TTS_LANGUAGE,
        "streaming_interval": 0.5,
    }
    # Do not inherit a SOCKS/HTTP proxy for this localhost request.
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        async with client.stream("POST", endpoint, json=payload) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk


async def synthesize_to_audio(
    *,
    text: str,
    cache_dir: Path,
    voice: str = "zh-CN-XiaoxiaoNeural",
    rate: str = "+0%",
    volume: str = "+0%",
    provider: str = DEFAULT_PROVIDER,
    provider_url: str = "",
) -> SynthesizedAudio:
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4().hex
    p = normalize_provider(provider)

    if p == "edge_tts":
        output_path = cache_dir / f"{file_id}.mp3"
        duration_ms = await _synthesize_edge_tts(
            text=text,
            output_path=output_path,
            voice=voice,
            rate=rate,
            volume=volume,
        )
        return SynthesizedAudio(
            file_id=file_id,
            path=output_path,
            duration_ms=duration_ms,
            media_type="audio/mpeg",
        )

    if p == "custom_http":
        return await _synthesize_custom_http(
            text=text,
            cache_dir=cache_dir,
            file_id=file_id,
            provider_url=provider_url,
            voice=voice,
            rate=rate,
            volume=volume,
        )

    if p == QWEN_TTS_LOCAL_PROVIDER:
        return await _synthesize_qwen_tts_local(
            text=text,
            cache_dir=cache_dir,
            file_id=file_id,
            provider_url=provider_url,
        )

    raise RuntimeError(f"Unsupported TTS provider: {provider}")


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
    result = await synthesize_to_audio(
        text=text,
        cache_dir=cache_dir,
        voice=voice,
        rate=rate,
        volume=volume,
        provider=provider,
        provider_url=provider_url,
    )
    return result.file_id, result.path, result.duration_ms
