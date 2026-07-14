from __future__ import annotations

import os
from pathlib import Path


QWEN_TTS_LOCAL_PROVIDER = "qwen_tts_local"
QWEN_TTS_PROJECT_DIR = Path(
    os.environ.get("IPET_QWEN_TTS_PROJECT_DIR", "/Users/lyj/lyj/tool/TTS")
).expanduser()
QWEN_TTS_API_BASE_URL = str(
    os.environ.get("IPET_QWEN_TTS_API_BASE_URL", "http://127.0.0.1:8000")
).rstrip("/")
QWEN_TTS_MODEL = "qwen3-tts-0.6b-bf16"
QWEN_TTS_REFERENCE_AUDIO = QWEN_TTS_PROJECT_DIR / "ex.mp3"
QWEN_TTS_REFERENCE_TEXT = "就按照小昭审美就没有比较帅的"
QWEN_TTS_REFERENCE_ID = "ipet_xiaozhao"
QWEN_TTS_LANGUAGE = "Chinese"


def qwen_tts_python() -> Path:
    return QWEN_TTS_PROJECT_DIR / ".venv" / "bin" / "python"


def qwen_tts_clone_url(provider_url: str = "") -> str:
    base_url = str(provider_url or "").strip().rstrip("/") or QWEN_TTS_API_BASE_URL
    return f"{base_url}/v1/audio/speech/clone"


def qwen_tts_stream_url(provider_url: str = "") -> str:
    base_url = str(provider_url or "").strip().rstrip("/") or QWEN_TTS_API_BASE_URL
    return f"{base_url}/v1/audio/speech/clone/stream"


def qwen_tts_health_url() -> str:
    return f"{QWEN_TTS_API_BASE_URL}/healthz"
