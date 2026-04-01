from __future__ import annotations

import asyncio
import importlib
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import numpy as np

AutoModel = None
_FUNASR_IMPORT_ERROR: Exception | None = None
_TORCH_IMPORT_ERROR: Exception | None = None
_FUNASR_NANO_CLASS: Any | None = None
_FUNASR_NANO_IMPORT_ERROR: Exception | None = None


DEFAULT_ASR_PROVIDER = "funasr"
DEFAULT_ASR_LANGUAGE = "zh"
DEFAULT_ASR_API_BASE_URL = "http://127.0.0.1:8012"
DEFAULT_PUSH_TO_TALK_KEY = "Alt"
SUPPORTED_PUSH_TO_TALK_KEYS = ("Alt", "Ctrl", "Space")
ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ASR_CACHE_ROOT = ROOT_DIR / ".cache" / "funasr"
DEFAULT_NANO_MODEL_DIR = DEFAULT_ASR_CACHE_ROOT / "modelscope" / "FunAudioLLM" / "Fun-ASR-Nano-2512"
DEFAULT_STREAMING_MODEL_NAME = "paraformer-zh-streaming"
DEFAULT_PUNCTUATION_MODEL_NAME = "ct-punc"
DEFAULT_ASR_CONFIG = {
    "enabled": True,
    "provider": DEFAULT_ASR_PROVIDER,
    "api_base_url": DEFAULT_ASR_API_BASE_URL,
    "push_to_talk_key": DEFAULT_PUSH_TO_TALK_KEY,
    "interim_results": True,
}


def _prepare_funasr_cache_env(cache_root: Path) -> None:
    cache_root = Path(cache_root)
    modelscope_cache = cache_root / "modelscope"
    huggingface_cache = cache_root / "huggingface"
    credentials_path = modelscope_cache / "credentials"
    for path in (cache_root, modelscope_cache, huggingface_cache):
        path.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_root))
    os.environ.setdefault("MODELSCOPE_CACHE", str(modelscope_cache))
    os.environ.setdefault("MODELSCOPE_CREDENTIALS_PATH", str(credentials_path))
    os.environ.setdefault("HF_HOME", str(huggingface_cache))


def _default_model_candidates(model_name: str) -> tuple[dict[str, str], ...]:
    normalized = str(model_name or "").strip()
    return (
        {"model": normalized, "hub": "ms"},
        {"model": normalized, "hub": "hf"},
    )


def _get_auto_model_class(cache_root: Path | None = None):
    global AutoModel, _FUNASR_IMPORT_ERROR
    if AutoModel is not None:
        return AutoModel
    _prepare_funasr_cache_env(cache_root or DEFAULT_ASR_CACHE_ROOT)
    try:
        AutoModel = importlib.import_module("funasr").AutoModel
        _FUNASR_IMPORT_ERROR = None
    except Exception as exc:  # pragma: no cover - optional dependency
        _FUNASR_IMPORT_ERROR = exc
        AutoModel = None
    return AutoModel


def _get_torch_module():
    global _TORCH_IMPORT_ERROR
    try:
        return importlib.import_module("torch")
    except Exception as exc:  # pragma: no cover - optional dependency
        _TORCH_IMPORT_ERROR = exc
        return None


def _get_funasr_nano_class() -> Any | None:
    global _FUNASR_NANO_CLASS, _FUNASR_NANO_IMPORT_ERROR
    if _FUNASR_NANO_CLASS is not None:
        return _FUNASR_NANO_CLASS
    auto_model_class = _get_auto_model_class(DEFAULT_ASR_CACHE_ROOT)
    if auto_model_class is None:
        _FUNASR_NANO_IMPORT_ERROR = _FUNASR_IMPORT_ERROR
        return None
    try:
        funasr_module = importlib.import_module("funasr")
        funasr_root = Path(funasr_module.__file__).resolve().parent
        nano_module_dir = funasr_root / "models" / "fun_asr_nano"
        nano_module_path = str(nano_module_dir)
        if nano_module_dir.exists() and nano_module_path not in sys.path:
            sys.path.insert(0, nano_module_path)
        _FUNASR_NANO_CLASS = importlib.import_module("funasr.models.fun_asr_nano.model").FunASRNano
        _FUNASR_NANO_IMPORT_ERROR = None
    except Exception as exc:  # pragma: no cover - optional dependency
        _FUNASR_NANO_IMPORT_ERROR = exc
        _FUNASR_NANO_CLASS = None
    return _FUNASR_NANO_CLASS


def normalize_push_to_talk_key(value: str | None) -> str:
    text = str(value or "").strip()
    return text if text in SUPPORTED_PUSH_TO_TALK_KEYS else DEFAULT_PUSH_TO_TALK_KEY


def asr_available(provider: str | None = None) -> bool:
    normalized = str(provider or DEFAULT_ASR_PROVIDER).strip() or DEFAULT_ASR_PROVIDER
    if normalized != DEFAULT_ASR_PROVIDER:
        return False
    return _get_auto_model_class() is not None


class ASRError(RuntimeError):
    pass


@dataclass(slots=True)
class ASRTranscript:
    text: str
    is_final: bool


@dataclass(slots=True)
class ASRSession:
    session_id: str
    key: str
    language: str
    punctuation: bool
    interim_results: bool
    runtime_state: Any = field(default_factory=dict)
    partial_text: str = ""
    bytes_received: int = 0


class ASRRuntimeProtocol(Protocol):
    def available(self) -> bool: ...

    def availability_message(self) -> str: ...

    def start_session(self, *, language: str, punctuation: bool) -> Any: ...

    def push_audio(self, runtime_state: Any, pcm16_chunk: bytes) -> str: ...

    def stop_session(self, runtime_state: Any) -> str: ...

    def discard_session(self, runtime_state: Any) -> None: ...


class FunASRRuntime:
    def __init__(self) -> None:
        self._model: Any | None = None
        self._punc_model: Any | None = None
        self._lock = threading.RLock()
        self._cache_root = DEFAULT_ASR_CACHE_ROOT
        self._streaming_model_candidates = _default_model_candidates(DEFAULT_STREAMING_MODEL_NAME)
        self._punc_model_candidates = _default_model_candidates(DEFAULT_PUNCTUATION_MODEL_NAME)
        self._chunk_size = [0, 10, 5]
        self._encoder_chunk_look_back = 4
        self._decoder_chunk_look_back = 1

    def available(self) -> bool:
        return asr_available(DEFAULT_ASR_PROVIDER)

    def availability_message(self) -> str:
        if self.available():
            return ""
        if _FUNASR_IMPORT_ERROR is not None:
            return f"FunASR import failed: {_FUNASR_IMPORT_ERROR}"
        return "FunASR is unavailable. Run `uv sync` to install ASR dependencies."

    def readiness_message(self) -> str:
        if not self.available():
            return self.availability_message()
        try:
            self._ensure_models()
        except Exception as exc:
            return str(exc) or exc.__class__.__name__
        return ""

    def start_session(self, *, language: str, punctuation: bool) -> dict[str, Any]:
        self._ensure_models()
        return {
            "cache": {},
            "audio_buffer": np.empty((0,), dtype=np.float32),
            "text": "",
            "language": str(language or DEFAULT_ASR_LANGUAGE).strip() or DEFAULT_ASR_LANGUAGE,
            "punctuation": bool(punctuation),
        }

    def push_audio(self, runtime_state: dict[str, Any], pcm16_chunk: bytes) -> str:
        self._ensure_models()
        speech = self._pcm16_to_float32(pcm16_chunk)
        if speech.size == 0:
            return str(runtime_state.get("text") or "")
        runtime_state["audio_buffer"] = np.concatenate((runtime_state["audio_buffer"], speech))
        chunk_stride = self._chunk_size[1] * 960
        latest_text = str(runtime_state.get("text") or "")
        while runtime_state["audio_buffer"].shape[0] >= chunk_stride:
            speech_chunk = runtime_state["audio_buffer"][:chunk_stride]
            runtime_state["audio_buffer"] = runtime_state["audio_buffer"][chunk_stride:]
            candidate = self._run_streaming_generate(
                speech_chunk,
                cache=runtime_state["cache"],
                is_final=False,
                language=str(runtime_state.get("language") or DEFAULT_ASR_LANGUAGE),
            )
            if candidate:
                latest_text = candidate
                runtime_state["text"] = latest_text
        return latest_text

    def stop_session(self, runtime_state: dict[str, Any]) -> str:
        self._ensure_models()
        latest_text = str(runtime_state.get("text") or "")
        speech_chunk = runtime_state.get("audio_buffer")
        if speech_chunk is None:
            speech_chunk = np.empty((0,), dtype=np.float32)
        try:
            candidate = self._run_streaming_generate(
                speech_chunk,
                cache=runtime_state["cache"],
                is_final=True,
                language=str(runtime_state.get("language") or DEFAULT_ASR_LANGUAGE),
            )
            if candidate:
                latest_text = candidate
        except Exception:
            if not latest_text:
                raise
        if latest_text and bool(runtime_state.get("punctuation", True)):
            latest_text = self._apply_punctuation(latest_text)
        runtime_state["text"] = latest_text.strip()
        runtime_state["audio_buffer"] = np.empty((0,), dtype=np.float32)
        return str(runtime_state.get("text") or "")

    def discard_session(self, runtime_state: dict[str, Any]) -> None:
        cache = runtime_state.get("cache")
        if isinstance(cache, dict):
            cache.clear()
        runtime_state["audio_buffer"] = np.empty((0,), dtype=np.float32)

    def _ensure_models(self) -> None:
        auto_model_class = _get_auto_model_class(self._cache_root)
        if auto_model_class is None:
            raise ASRError(self.availability_message())
        with self._lock:
            if self._model is None:
                self._prepare_model_cache()
                self._model = self._load_auto_model(auto_model_class, self._streaming_model_candidates, label="streaming ASR")
            if self._punc_model is None:
                self._prepare_model_cache()
                self._punc_model = self._load_auto_model(auto_model_class, self._punc_model_candidates, label="punctuation")

    def _prepare_model_cache(self) -> None:
        _prepare_funasr_cache_env(self._cache_root)

    def _load_auto_model(self, auto_model_class: Any, candidates: tuple[dict[str, str], ...], *, label: str) -> Any:
        errors: list[str] = []
        for candidate in candidates:
            kwargs: dict[str, Any] = {
                "model": candidate["model"],
                "disable_update": True,
                "check_latest": False,
            }
            hub = str(candidate.get("hub") or "").strip()
            if hub:
                kwargs["hub"] = hub
            try:
                return auto_model_class(**kwargs)
            except Exception as exc:
                errors.append(f"{hub or 'local'}:{candidate['model']} -> {exc}")
        joined = "; ".join(errors) if errors else f"{label} initialization failed"
        raise ASRError(
            f"FunASR {label} model is unavailable. "
            f"Cache root: {self._cache_root}. "
            f"Tried: {joined}"
        )

    def _run_streaming_generate(
        self,
        speech_chunk: np.ndarray,
        *,
        cache: dict[str, Any],
        is_final: bool,
        language: str,
    ) -> str:
        if self._model is None:
            raise ASRError(self.availability_message())
        result = self._model.generate(
            input=speech_chunk,
            cache=cache,
            is_final=is_final,
            chunk_size=self._chunk_size,
            encoder_chunk_look_back=self._encoder_chunk_look_back,
            decoder_chunk_look_back=self._decoder_chunk_look_back,
            language=language,
        )
        return self._extract_text(result)

    def _apply_punctuation(self, text: str) -> str:
        if not text or self._punc_model is None:
            return text
        try:
            result = self._punc_model.generate(input=text)
        except Exception:
            return text
        punctuated = self._extract_text(result)
        return punctuated or text

    def _extract_text(self, result: Any) -> str:
        if isinstance(result, str):
            return result.strip()
        if isinstance(result, dict):
            text = result.get("text")
            if isinstance(text, str):
                return text.strip()
            if isinstance(text, list):
                joined = "".join(str(item or "") for item in text)
                return joined.strip()
        if isinstance(result, (list, tuple)):
            for item in result:
                text = self._extract_text(item)
                if text:
                    return text
        return ""

    def _pcm16_to_float32(self, pcm16_chunk: bytes) -> np.ndarray:
        if not pcm16_chunk:
            return np.empty((0,), dtype=np.float32)
        chunk = bytes(pcm16_chunk)
        if len(chunk) % 2 == 1:
            chunk = chunk[:-1]
        if not chunk:
            return np.empty((0,), dtype=np.float32)
        return np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0


class FunASRNanoRuntime:
    def __init__(self, model_dir: Path | None = None) -> None:
        self._model_dir = Path(model_dir or DEFAULT_NANO_MODEL_DIR)
        self._model: Any | None = None
        self._infer_kwargs: dict[str, Any] | None = None
        self._lock = threading.RLock()
        self._warmup_started = False
        self._warming = False
        self._warmup_error = ""

    def available(self) -> bool:
        return not self.availability_message()

    def availability_message(self) -> str:
        if not self._model_dir.exists():
            return f"Fun-ASR-Nano-2512 model is missing: {self._model_dir}"
        if _get_auto_model_class(DEFAULT_ASR_CACHE_ROOT) is None:
            if _FUNASR_IMPORT_ERROR is not None:
                return f"FunASR import failed: {_FUNASR_IMPORT_ERROR}"
            return "FunASR is unavailable. Run `uv sync` to install ASR dependencies."
        if _get_torch_module() is None:
            if _TORCH_IMPORT_ERROR is not None:
                return f"Torch import failed: {_TORCH_IMPORT_ERROR}"
            return "Torch is unavailable. Run `uv sync` to install ASR dependencies."
        if _get_funasr_nano_class() is None:
            if _FUNASR_NANO_IMPORT_ERROR is not None:
                return f"FunASR Nano import failed: {_FUNASR_NANO_IMPORT_ERROR}"
            return "FunASR Nano runtime is unavailable."
        return ""

    def readiness_message(self) -> str:
        availability_message = self.availability_message()
        if availability_message:
            return availability_message
        if self._model is not None and self._infer_kwargs is not None:
            return ""
        if not self._warmup_started:
            return "ASR 尚未启动，首次按住 Ctrl 将开始初始化。"
        if self._warming:
            return "ASR 正在加载模型，请稍后再试。"
        if not self._warmup_error:
            return "ASR 正在加载模型，请稍后再试。"
        return str(self._warmup_error or "")

    def warmup(self) -> str:
        self._warmup_started = True
        self._warming = True
        self._warmup_error = ""
        try:
            self._ensure_model()
        except Exception as exc:
            self._warmup_error = str(exc) or exc.__class__.__name__
            return self._warmup_error
        finally:
            self._warming = False
        return ""

    def start_session(self, *, language: str, punctuation: bool) -> dict[str, Any]:
        self._ensure_model()
        return {
            "audio_buffer": np.empty((0,), dtype=np.float32),
            "language": str(language or DEFAULT_ASR_LANGUAGE).strip() or DEFAULT_ASR_LANGUAGE,
            "punctuation": bool(punctuation),
        }

    def push_audio(self, runtime_state: dict[str, Any], pcm16_chunk: bytes) -> str:
        speech = self._pcm16_to_float32(pcm16_chunk)
        if speech.size == 0:
            return ""
        runtime_state["audio_buffer"] = np.concatenate((runtime_state["audio_buffer"], speech))
        return ""

    def stop_session(self, runtime_state: dict[str, Any]) -> str:
        self._ensure_model()
        speech_chunk = runtime_state.get("audio_buffer")
        if speech_chunk is None or not isinstance(speech_chunk, np.ndarray) or speech_chunk.size == 0:
            return ""
        torch = _get_torch_module()
        if torch is None:
            raise ASRError(self.availability_message())
        audio_tensor = torch.from_numpy(speech_chunk.astype(np.float32, copy=False))
        infer_kwargs = dict(self._infer_kwargs or {})
        infer_kwargs["language"] = self._normalize_language(str(runtime_state.get("language") or DEFAULT_ASR_LANGUAGE))
        result = self._model.inference(data_in=[audio_tensor], **infer_kwargs)
        runtime_state["audio_buffer"] = np.empty((0,), dtype=np.float32)
        return self._extract_text(result)

    def discard_session(self, runtime_state: dict[str, Any]) -> None:
        runtime_state["audio_buffer"] = np.empty((0,), dtype=np.float32)

    def _ensure_model(self) -> None:
        message = self.availability_message()
        if message:
            raise ASRError(message)
        with self._lock:
            self._warmup_started = True
            if self._model is not None and self._infer_kwargs is not None:
                return
            nano_class = _get_funasr_nano_class()
            if nano_class is None:
                raise ASRError(self.availability_message())
            _prepare_funasr_cache_env(DEFAULT_ASR_CACHE_ROOT)
            model, infer_kwargs = nano_class.from_pretrained(
                model=str(self._model_dir),
                device="cpu",
            )
            self._model = model
            self._infer_kwargs = dict(infer_kwargs or {})

    def _normalize_language(self, language: str) -> str:
        normalized = str(language or DEFAULT_ASR_LANGUAGE).strip().lower()
        if normalized.startswith("en"):
            return "英文"
        if normalized.startswith("ja") or normalized.startswith("jp"):
            return "日文"
        return "中文"

    def _extract_text(self, result: Any) -> str:
        if isinstance(result, str):
            return result.strip()
        if isinstance(result, dict):
            text = result.get("text")
            if isinstance(text, str):
                return text.strip()
        if isinstance(result, (list, tuple)):
            for item in result:
                text = self._extract_text(item)
                if text:
                    return text
        return ""

    def _pcm16_to_float32(self, pcm16_chunk: bytes) -> np.ndarray:
        if not pcm16_chunk:
            return np.empty((0,), dtype=np.float32)
        chunk = bytes(pcm16_chunk)
        if len(chunk) % 2 == 1:
            chunk = chunk[:-1]
        if not chunk:
            return np.empty((0,), dtype=np.float32)
        return np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0


def _default_asr_runtime() -> ASRRuntimeProtocol:
    nano_runtime = FunASRNanoRuntime()
    if nano_runtime.available():
        return nano_runtime
    return FunASRRuntime()


class ASRService:
    def __init__(self, runtime: ASRRuntimeProtocol | None = None) -> None:
        self.runtime = runtime or _default_asr_runtime()
        self._sessions: dict[str, ASRSession] = {}
        self._lock = asyncio.Lock()

    def available(self) -> bool:
        return bool(self.runtime.available())

    def availability_message(self) -> str:
        return str(self.runtime.availability_message() or "")

    def readiness_message(self) -> str:
        if not self.available():
            return self.availability_message()
        runtime_message = getattr(self.runtime, "readiness_message", None)
        if callable(runtime_message):
            return str(runtime_message() or "")
        return ""

    def ready(self) -> bool:
        return not self.readiness_message()

    async def warmup(self) -> str:
        if not self.available():
            return self.availability_message()
        runtime_warmup = getattr(self.runtime, "warmup", None)
        if callable(runtime_warmup):
            return str(await asyncio.to_thread(runtime_warmup) or "")
        return str(self.readiness_message() or "")

    async def start_session(
        self,
        *,
        key: str,
        language: str = DEFAULT_ASR_LANGUAGE,
        punctuation: bool = True,
        interim_results: bool = True,
    ) -> ASRSession:
        readiness_message = self.readiness_message()
        if readiness_message:
            raise ASRError(readiness_message)
        runtime_state = await asyncio.to_thread(
            self.runtime.start_session,
            language=str(language or DEFAULT_ASR_LANGUAGE).strip() or DEFAULT_ASR_LANGUAGE,
            punctuation=bool(punctuation),
        )
        session = ASRSession(
            session_id=uuid4().hex,
            key=normalize_push_to_talk_key(key),
            language=str(language or DEFAULT_ASR_LANGUAGE).strip() or DEFAULT_ASR_LANGUAGE,
            punctuation=bool(punctuation),
            interim_results=bool(interim_results),
            runtime_state=runtime_state,
        )
        async with self._lock:
            self._sessions[session.session_id] = session
        return session

    async def push_audio(self, session_id: str, pcm16_chunk: bytes) -> ASRTranscript | None:
        session = await self._require_session(session_id)
        chunk = bytes(pcm16_chunk or b"")
        if not chunk:
            return None
        session.bytes_received += len(chunk)
        text = await asyncio.to_thread(self.runtime.push_audio, session.runtime_state, chunk)
        normalized = str(text or "").strip()
        if not session.interim_results or not normalized or normalized == session.partial_text:
            return None
        session.partial_text = normalized
        return ASRTranscript(text=normalized, is_final=False)

    async def stop_session(self, session_id: str) -> ASRTranscript:
        session = await self._pop_session(session_id)
        try:
            text = await asyncio.to_thread(self.runtime.stop_session, session.runtime_state)
        finally:
            await asyncio.to_thread(self.runtime.discard_session, session.runtime_state)
        normalized = str(text or "").strip() or session.partial_text.strip()
        return ASRTranscript(text=normalized, is_final=True)

    async def discard_session(self, session_id: str) -> None:
        session = await self._pop_session(session_id, required=False)
        if session is None:
            return
        await asyncio.to_thread(self.runtime.discard_session, session.runtime_state)

    async def _require_session(self, session_id: str) -> ASRSession:
        async with self._lock:
            session = self._sessions.get(str(session_id or "").strip())
        if session is None:
            raise ASRError("ASR session not found")
        return session

    async def _pop_session(self, session_id: str, *, required: bool = True) -> ASRSession | None:
        async with self._lock:
            session = self._sessions.pop(str(session_id or "").strip(), None)
        if session is None and required:
            raise ASRError("ASR session not found")
        return session
