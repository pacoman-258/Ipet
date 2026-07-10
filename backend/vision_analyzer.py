from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from .vision import (
    ALLOWED_MIME_TYPES,
    MAX_OBSERVATIONS,
    MAX_UNKNOWN_LENGTH,
    _clean_text,
    _sanitize_observations,
    _sanitize_unknowns,
)


DEFAULT_ANALYZER_CONFIG: dict[str, Any] = {
    "enabled": False,
    "provider": "none",
    "timeout_sec": 90.0,
    "max_text_chars": 600,
    "max_image_bytes": 3_000_000,
    "api_key": "",
    "model": "",
    "base_url": "",
    "api_key_env": "",
    "image_detail": "low",
    "max_observations": 4,
}

GOOGLE_AISTUDIO_DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"
GOOGLE_AISTUDIO_DEFAULT_MODEL = "gemini-3.5-flash"
ANALYZER_PROVIDERS = {"none", "macos_vision_ocr", "openai_compatible_vlm", "local_vlm", "google_aistudio_vlm"}
ANALYZER_IMAGE_DETAILS = {"low", "high", "auto"}
ANALYZER_STATUS_KEYS = {"enabled", "provider", "status", "last_error", "observations_added", "unknowns_added"}
DATA_URL_PREFIX = "data:"


@dataclass(frozen=True)
class VisionAnalysisResult:
    payload: dict[str, Any]
    status: dict[str, Any]


def normalize_analyzer_config(config: Any) -> dict[str, Any]:
    source = config if isinstance(config, dict) else {}
    provider = _clean_text(source.get("provider") or DEFAULT_ANALYZER_CONFIG["provider"], max_length=40) or "none"
    normalized = {
        "enabled": bool(source.get("enabled", DEFAULT_ANALYZER_CONFIG["enabled"])),
        "provider": provider,
        "timeout_sec": _clamp_float(
            source.get("timeout_sec", DEFAULT_ANALYZER_CONFIG["timeout_sec"]),
            fallback=DEFAULT_ANALYZER_CONFIG["timeout_sec"],
            min_value=0.2,
            max_value=30.0,
        ),
        "max_text_chars": _clamp_int(
            source.get("max_text_chars", DEFAULT_ANALYZER_CONFIG["max_text_chars"]),
            fallback=DEFAULT_ANALYZER_CONFIG["max_text_chars"],
            min_value=40,
            max_value=2000,
        ),
        "max_image_bytes": _clamp_int(
            source.get("max_image_bytes", DEFAULT_ANALYZER_CONFIG["max_image_bytes"]),
            fallback=DEFAULT_ANALYZER_CONFIG["max_image_bytes"],
            min_value=1024,
            max_value=8_000_000,
        ),
        "api_key": _clean_text(source.get("api_key"), max_length=500),
        "model": _clean_text(source.get("model"), max_length=120),
        "base_url": _clean_text(source.get("base_url"), max_length=300).rstrip("/"),
        "api_key_env": _clean_text(source.get("api_key_env") or DEFAULT_ANALYZER_CONFIG["api_key_env"], max_length=80),
        "image_detail": _clean_text(source.get("image_detail") or DEFAULT_ANALYZER_CONFIG["image_detail"], max_length=20),
        "max_observations": _clamp_int(
            source.get("max_observations", DEFAULT_ANALYZER_CONFIG["max_observations"]),
            fallback=DEFAULT_ANALYZER_CONFIG["max_observations"],
            min_value=1,
            max_value=MAX_OBSERVATIONS,
        ),
    }
    if normalized["image_detail"] not in ANALYZER_IMAGE_DETAILS:
        normalized["image_detail"] = DEFAULT_ANALYZER_CONFIG["image_detail"]
    if provider == "local_vlm" and not normalized["base_url"]:
        normalized["base_url"] = "http://127.0.0.1:11434/v1"
        if not source.get("api_key_env"):
            normalized["api_key_env"] = ""
    elif provider == "openai_compatible_vlm" and not normalized["base_url"]:
        normalized["base_url"] = "https://api.openai.com/v1"
    elif provider == "google_aistudio_vlm":
        if not normalized["base_url"]:
            normalized["base_url"] = GOOGLE_AISTUDIO_DEFAULT_ENDPOINT
        if not normalized["model"]:
            normalized["model"] = GOOGLE_AISTUDIO_DEFAULT_MODEL
    return normalized


def merge_observations(caller_observations: Any, analyzer_observations: Any) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for observation in _sanitize_observations(caller_observations) + _sanitize_observations(analyzer_observations):
        key = (
            str(observation.get("claim") or "").casefold(),
            str(observation.get("source") or "").casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(observation)
        if len(merged) >= MAX_OBSERVATIONS:
            break
    return merged


def merge_unknowns(caller_unknowns: Any, analyzer_unknowns: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in _sanitize_unknowns(caller_unknowns) + _sanitize_unknowns(analyzer_unknowns):
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= 8:
            break
    return merged


def sanitize_analysis_metadata(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    status = {
        "enabled": bool(source.get("enabled", False)),
        "provider": _clean_text(source.get("provider") or "none", max_length=40) or "none",
        "status": _clean_text(source.get("status") or "", max_length=40),
        "last_error": _clean_text(source.get("last_error") or "", max_length=MAX_UNKNOWN_LENGTH),
        "observations_added": _clamp_int(source.get("observations_added", 0), fallback=0, min_value=0, max_value=MAX_OBSERVATIONS),
        "unknowns_added": _clamp_int(source.get("unknowns_added", 0), fallback=0, min_value=0, max_value=8),
    }
    return status


class VisionAnalyzer:
    def __init__(
        self,
        config: Any | None = None,
        *,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
        http_client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = normalize_analyzer_config(config or {})
        self._runner = runner or subprocess.run
        self._http_client_factory = http_client_factory or httpx.Client

    def enrich_payload(self, payload: Any) -> VisionAnalysisResult:
        if not isinstance(payload, dict):
            return VisionAnalysisResult({}, self._status("error", "frame payload must be an object"))
        if not self.config["enabled"] or self.config["provider"] == "none":
            return VisionAnalysisResult(dict(payload), self._status("disabled", ""))

        if self.config["provider"] == "macos_vision_ocr":
            analysis = self._analyze_with_macos_vision_ocr(payload)
        elif self.config["provider"] in {"openai_compatible_vlm", "local_vlm"}:
            analysis = self._analyze_with_openai_compatible_vlm(payload)
        elif self.config["provider"] == "google_aistudio_vlm":
            analysis = self._analyze_with_google_aistudio_vlm(payload)
        else:
            analysis = {
                "observations": [],
                "unknowns": [f"vision analyzer provider is configured but unavailable locally: {self.config['provider']}"],
                "last_error": f"provider unavailable locally: {self.config['provider']}",
            }
        return self._merge_payload(payload, analysis)

    def _merge_payload(self, payload: dict[str, Any], analysis: dict[str, Any]) -> VisionAnalysisResult:
        return merge_analysis_into_payload(payload, self.config, analysis)

    def _analyze_with_macos_vision_ocr(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            mime_type, image_bytes = _decode_frame_data_url(payload, max_bytes=int(self.config["max_image_bytes"]))
        except Exception as exc:
            message = f"macOS OCR input invalid: {exc}"
            return {"observations": [], "unknowns": [message], "last_error": message}

        suffix = ".png" if mime_type == "image/png" else ".jpg"
        image_path = ""
        script_path = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="ipet-vision-", suffix=suffix, delete=False) as image_file:
                image_file.write(image_bytes)
                image_path = image_file.name
            with tempfile.NamedTemporaryFile("w", prefix="ipet-vision-ocr-", suffix=".swift", delete=False) as script_file:
                script_file.write(_MACOS_VISION_OCR_SWIFT)
                script_path = script_file.name
            cache_dir = _swift_module_cache_dir()
            env = os.environ.copy()
            env.setdefault("CLANG_MODULE_CACHE_PATH", str(cache_dir))
            env.setdefault("SWIFT_MODULE_CACHE_PATH", str(cache_dir))
            result = self._runner(
                [
                    "/usr/bin/swift",
                    "-module-cache-path",
                    str(cache_dir),
                    script_path,
                    image_path,
                    str(int(self.config["max_text_chars"])),
                ],
                capture_output=True,
                env=env,
                text=True,
                timeout=float(self.config["timeout_sec"]),
            )
        except Exception as exc:
            message = f"macOS OCR failed: {exc}"
            return {"observations": [], "unknowns": [message], "last_error": message}
        finally:
            for path in (image_path, script_path):
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass

        if getattr(result, "returncode", 1) != 0:
            detail = _clean_text(getattr(result, "stderr", "") or getattr(result, "stdout", ""), max_length=MAX_UNKNOWN_LENGTH)
            message = f"macOS OCR failed: {detail or 'swift helper failed'}"
            return {"observations": [], "unknowns": [message], "last_error": message}

        text = _extract_ocr_text(getattr(result, "stdout", ""), max_chars=int(self.config["max_text_chars"]))
        if not text:
            return {"observations": [], "unknowns": ["macOS OCR did not detect readable screen text"], "last_error": ""}

        observation = {
            "claim": f"屏幕上可见文字：{text}",
            "evidence": f"OCR text: {text}",
            "region": "screen_ocr",
            "confidence": 0.72,
            "source": "macos-vision-ocr",
        }
        return {
            "summary": "macOS Vision OCR 识别到屏幕文字。",
            "observations": [observation],
            "unknowns": [],
            "last_error": "",
        }

    def _analyze_with_openai_compatible_vlm(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            _decode_frame_data_url(payload, max_bytes=int(self.config["max_image_bytes"]))
        except Exception as exc:
            message = f"VLM input invalid: {exc}"
            return {"observations": [], "unknowns": [message], "last_error": message}

        model = _clean_text(self.config.get("model"), max_length=120)
        base_url = _clean_text(self.config.get("base_url"), max_length=300).rstrip("/")
        if not model:
            message = "VLM analyzer model is not configured"
            return {"observations": [], "unknowns": [message], "last_error": message}
        if not base_url:
            message = "VLM analyzer base_url is not configured"
            return {"observations": [], "unknowns": [message], "last_error": message}

        headers = {"Content-Type": "application/json"}
        api_key = _resolved_api_key(self.config.get("api_key"), self.config.get("api_key_env"))
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        active = payload.get("active_observation") if isinstance(payload.get("active_observation"), dict) else {}
        request_body = {
            "model": model,
            "messages": [
                {"role": "system", "content": _VLM_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": build_vlm_user_prompt(
                                max_observations=int(self.config["max_observations"]),
                                target_hint=active.get("target_hint"),
                                observe_prompt=active.get("observe_prompt"),
                                image_resolution=active.get("image_resolution"),
                                screen_resolution=active.get("screen_resolution"),
                                screen_bounds=active.get("screen_bounds"),
                                coordinate_scale=active.get("coordinate_scale"),
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": str(payload.get("data_url") or ""),
                                "detail": self.config["image_detail"],
                            },
                        },
                    ],
                },
            ],
            "temperature": 0,
            "max_tokens": 700,
        }
        try:
            with self._http_client_factory(
                **_vlm_client_kwargs(base_url, self.config["timeout_sec"], provider=self.config["provider"])
            ) as client:
                response = client.post(_chat_completions_endpoint(base_url), json=request_body, headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            message = f"VLM analyzer failed: {exc}"
            return {"observations": [], "unknowns": [message], "last_error": message}

        try:
            content = openai_message_content(data)
            return normalize_vlm_text_analysis(content, source=_vlm_source_name(self.config["provider"]))
        except Exception as exc:
            message = f"VLM analyzer returned invalid response: {exc}"
            return {"observations": [], "unknowns": [message], "last_error": message}

    def _analyze_with_google_aistudio_vlm(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            mime_type, image_bytes = _decode_frame_data_url(payload, max_bytes=int(self.config["max_image_bytes"]))
        except Exception as exc:
            message = f"VLM input invalid: {exc}"
            return {"observations": [], "unknowns": [message], "last_error": message}

        model = _clean_text(self.config.get("model"), max_length=120) or GOOGLE_AISTUDIO_DEFAULT_MODEL
        base_url = _clean_text(self.config.get("base_url"), max_length=300).rstrip("/") or GOOGLE_AISTUDIO_DEFAULT_ENDPOINT
        api_key = _resolved_api_key(self.config.get("api_key"), self.config.get("api_key_env"))
        if not api_key:
            message = "Google AI Studio VLM API key is not configured"
            return {"observations": [], "unknowns": [message], "last_error": message}

        active = payload.get("active_observation") if isinstance(payload.get("active_observation"), dict) else {}
        request_body = {
            "system_instruction": {"parts": [{"text": _VLM_SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": build_vlm_user_prompt(
                                max_observations=int(self.config["max_observations"]),
                                target_hint=active.get("target_hint"),
                                observe_prompt=active.get("observe_prompt"),
                                image_resolution=active.get("image_resolution"),
                                screen_resolution=active.get("screen_resolution"),
                                screen_bounds=active.get("screen_bounds"),
                                coordinate_scale=active.get("coordinate_scale"),
                            ),
                        },
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            },
                        },
                    ],
                }
            ],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 700},
        }
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        try:
            with self._http_client_factory(
                **_vlm_client_kwargs(base_url, self.config["timeout_sec"], provider=self.config["provider"])
            ) as client:
                response = client.post(_google_generate_content_endpoint(base_url, model), json=request_body, headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            message = f"VLM analyzer failed: {exc}"
            return {"observations": [], "unknowns": [message], "last_error": message}

        try:
            content = google_content_text(data)
            return normalize_vlm_text_analysis(content, source=_vlm_source_name(self.config["provider"]))
        except Exception as exc:
            message = f"VLM analyzer returned invalid response: {exc}"
            return {"observations": [], "unknowns": [message], "last_error": message}

    def _status(
        self,
        status: str,
        last_error: str,
        *,
        observations_added: int = 0,
        unknowns_added: int = 0,
    ) -> dict[str, Any]:
        return sanitize_analysis_metadata(
            {
                "enabled": bool(self.config["enabled"]),
                "provider": self.config["provider"],
                "status": status,
                "last_error": last_error,
                "observations_added": observations_added,
                "unknowns_added": unknowns_added,
            }
        )


def _decode_frame_data_url(payload: dict[str, Any], *, max_bytes: int) -> tuple[str, bytes]:
    mime_type = str(payload.get("mime_type") or "").strip().lower()
    data_url = str(payload.get("data_url") or "")
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ValueError("unsupported frame mime_type")
    expected_prefix = f"data:{mime_type};base64,"
    if not data_url.startswith(expected_prefix):
        raise ValueError("frame data_url mime type mismatch")
    encoded = data_url[len(expected_prefix) :]
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("frame data_url is not valid base64") from exc
    if not image_bytes:
        raise ValueError("frame image is empty")
    if len(image_bytes) > max_bytes:
        raise ValueError("frame image is too large")
    return mime_type, image_bytes


def _extract_ocr_text(output: Any, *, max_chars: int) -> str:
    raw = str(output or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        raw = str(parsed.get("text") or "")
    elif isinstance(parsed, list):
        raw = " ".join(str(item) for item in parsed)
    return _clean_text(raw, max_length=max_chars)


def merge_analysis_into_payload(payload: dict[str, Any], config: Any, analysis: dict[str, Any]) -> VisionAnalysisResult:
    analyzer_config = normalize_analyzer_config(config)
    observations = merge_observations(payload.get("observations"), analysis.get("observations"))
    unknowns = merge_unknowns(payload.get("unknowns"), analysis.get("unknowns"))
    added_observations = max(0, len(observations) - len(_sanitize_observations(payload.get("observations"))))
    added_unknowns = max(0, len(unknowns) - len(_sanitize_unknowns(payload.get("unknowns"))))
    last_error = _clean_text(analysis.get("last_error") or "", max_length=MAX_UNKNOWN_LENGTH)
    status_name = "error" if last_error else "ok"
    status = sanitize_analysis_metadata(
        {
            "enabled": bool(analyzer_config["enabled"]),
            "provider": analyzer_config["provider"],
            "status": status_name,
            "last_error": last_error,
            "observations_added": added_observations,
            "unknowns_added": added_unknowns,
        }
    )
    enriched = dict(payload)
    if observations:
        enriched["observations"] = observations
    else:
        enriched.pop("observations", None)
    if unknowns:
        enriched["unknowns"] = unknowns
    else:
        enriched.pop("unknowns", None)
    summary = _clean_text(payload.get("summary"), max_length=500)
    analysis_summary = _clean_text(analysis.get("summary"), max_length=500)
    if not summary and analysis_summary:
        enriched["summary"] = analysis_summary
    observe_answer = _clean_text(analysis.get("observe_answer") or analysis_summary, max_length=1200)
    if observe_answer:
        enriched["observe_answer"] = observe_answer
    enriched["analysis"] = status
    return VisionAnalysisResult(enriched, status)


def build_vlm_user_prompt(
    *,
    max_observations: int,
    target_hint: str = "",
    observe_prompt: Any = "",
    image_resolution: Any = None,
    screen_resolution: Any = None,
    screen_bounds: Any = None,
    coordinate_scale: Any = None,
) -> str:
    hint = _clean_text(target_hint, max_length=220)
    question = _clean_text(observe_prompt or hint or "请描述当前截图中可见的主要内容。", max_length=700)
    image = image_resolution if isinstance(image_resolution, dict) else {}
    resolution = screen_resolution if isinstance(screen_resolution, dict) else {}
    bounds = screen_bounds if isinstance(screen_bounds, dict) else {}
    scale = coordinate_scale if isinstance(coordinate_scale, dict) else {}
    try:
        image_width = int(image.get("width") or 0)
        image_height = int(image.get("height") or 0)
    except Exception:
        image_width = 0
        image_height = 0
    try:
        width = int(resolution.get("width") or 0)
        height = int(resolution.get("height") or 0)
    except Exception:
        width = 0
        height = 0
    try:
        origin_x = int(bounds.get("x") or 0)
        origin_y = int(bounds.get("y") or 0)
        bounds_width = int(bounds.get("width") or width or 0)
        bounds_height = int(bounds.get("height") or height or 0)
    except Exception:
        origin_x = 0
        origin_y = 0
        bounds_width = width
        bounds_height = height
    try:
        image_to_screen_x = float(scale.get("image_to_screen_x") or 0.0)
        image_to_screen_y = float(scale.get("image_to_screen_y") or 0.0)
    except Exception:
        image_to_screen_x = 0.0
        image_to_screen_y = 0.0
    try:
        screen_to_image_x = float(scale.get("screen_to_image_x") or 0.0)
        screen_to_image_y = float(scale.get("screen_to_image_y") or 0.0)
    except Exception:
        screen_to_image_x = 0.0
        screen_to_image_y = 0.0
    if image_to_screen_x <= 0 and image_width > 0 and bounds_width > 0:
        image_to_screen_x = bounds_width / image_width
    if image_to_screen_y <= 0 and image_height > 0 and bounds_height > 0:
        image_to_screen_y = bounds_height / image_height
    if screen_to_image_x <= 0 and image_to_screen_x > 0:
        screen_to_image_x = 1.0 / image_to_screen_x
    if screen_to_image_y <= 0 and image_to_screen_y > 0:
        screen_to_image_y = 1.0 / image_to_screen_y
    def format_scale(value: float) -> str:
        return f"{value:.6f}".rstrip("0").rstrip(".")

    image_text = f"{image_width}x{image_height}" if image_width > 0 and image_height > 0 else "unknown"
    resolution_text = f"{width}x{height}" if width > 0 and height > 0 else "unknown"
    bounds_text = f"origin=({origin_x}, {origin_y}), size={bounds_width}x{bounds_height} points" if bounds_width > 0 and bounds_height > 0 else "origin unknown, size unknown"
    transform_text = ""
    if image_to_screen_x > 0 and image_to_screen_y > 0:
        transform_text = (
            f"Coordinate transform: screen_x = {origin_x} + image_x * {format_scale(image_to_screen_x)}; "
            f"screen_y = {origin_y} + image_y * {format_scale(image_to_screen_y)}; "
            f"image-to-screen scale: x={format_scale(image_to_screen_x)}, y={format_scale(image_to_screen_y)}."
        )
        if screen_to_image_x > 0 and screen_to_image_y > 0:
            transform_text += (
                f" Inverse transform: image_x = (screen_x - {origin_x}) * {format_scale(screen_to_image_x)}; "
                f"image_y = (screen_y - {origin_y}) * {format_scale(screen_to_image_y)}; "
                f"screen-to-image scale: x={format_scale(screen_to_image_x)}, y={format_scale(screen_to_image_y)}."
            )
    task_lines = [
        f"Brain asks: {question}",
        f"Attached image pixel size: {image_text}.",
        f"macOS screen coordinate space: {bounds_text}. Screen point size: {resolution_text}.",
        "If you mention coordinates, use macOS screen coordinates, not screenshot pixel coordinates or cropped image pixels.",
    ]
    if transform_text:
        task_lines.append(transform_text)
    if hint:
        task_lines.append(f"Target hint: {hint}.")
    return (
        "你是 Ipet 的视觉观察助手。请只根据这张截图回答 Brain 的问题。"
        "用自然语言回答，不要输出 JSON、Markdown 代码块或固定 schema。"
        "如果问题要求找位置或点击目标，请用自然语言说明可见依据，并给出 macOS 屏幕坐标，例如 x=123, y=456。"
        "Return macOS screen coordinates for click points, not screenshot pixel coordinates. "
        "返回点击坐标前，请把你准备给出的 macOS 坐标用 screen-to-image scale 反投回截图，确认该点确实落在目标图标或目标控件中心，而不是落在相邻图标、文字标签、窗口边缘或背景上。"
        "Dock/App 图标任务中，先找图标本身的视觉中心；不要只根据 Dock 图标序号或相邻关系估算。"
        "如果坐标落到相邻图标，必须修正坐标；如果无法确认，就说明不确定，不要编造坐标。"
        "如果看不到或不确定，就直接说明不确定以及原因。"
        f"回答尽量简洁，最多列出 {max_observations} 个关键可见点。"
        + " "
        + " ".join(task_lines)
    )


def normalize_vlm_text_analysis(content: Any, *, source: str) -> dict[str, Any]:
    answer = _clean_text(content, max_length=1200)
    if not answer:
        message = "VLM analyzer returned an empty natural-language response"
        return {"observations": [], "unknowns": [message], "last_error": message}
    return {
        "summary": answer,
        "observe_answer": answer,
        "observations": [
            {
                "claim": answer,
                "evidence": "VLM natural-language answer to the current screenshot question",
                "region": "screen",
                "confidence": 0.72,
                "source": source,
            }
        ],
        "unknowns": [],
        "last_error": "",
    }


def openai_message_content(data: Any) -> str:
    if not isinstance(data, dict):
        raise ValueError("response is not an object")
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        parts.append(str(item.get("text") or ""))
                    else:
                        parts.append(str(item))
                return "\n".join(part for part in parts if part)
    for key in ("content", "text", "message", "answer"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, str) and value:
            return value
    nested = data.get("data") if isinstance(data, dict) else None
    if isinstance(nested, dict):
        return openai_message_content(nested)
    raise ValueError("missing message content")


def google_content_text(data: Any) -> str:
    if not isinstance(data, dict):
        raise ValueError("response is not an object")
    candidates = data.get("candidates")
    if isinstance(candidates, list) and candidates:
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if isinstance(parts, list):
            text = "\n".join(str(item.get("text") or "") for item in parts if isinstance(item, dict)).strip()
            if text:
                return text
    for key in ("text", "answer"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    raise ValueError("missing message content")


def _chat_completions_endpoint(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _vlm_client_kwargs(base_url: str, timeout_sec: Any, *, provider: str = "") -> dict[str, Any]:
    try:
        read_timeout = float(timeout_sec)
    except Exception:
        read_timeout = float(DEFAULT_ANALYZER_CONFIG["timeout_sec"])
    kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(connect=8.0, read=max(1.0, read_timeout), write=20.0, pool=8.0),
        "trust_env": False,
    }
    proxy = None if provider == "local_vlm" else _http_proxy_for_remote_url(base_url)
    if proxy:
        kwargs["proxy"] = proxy
    return kwargs


def _http_proxy_for_remote_url(base_url: str) -> str | None:
    parsed = urlsplit(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").strip().lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return None
    if host.startswith("127.") or host.endswith(".local"):
        return None
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = str(os.environ.get(name) or "").strip()
        if value.lower().startswith(("http://", "https://")):
            return value
    return None


def _google_generate_content_endpoint(base_url: str, model: str) -> str:
    base = str(base_url or "").strip().rstrip("/") or GOOGLE_AISTUDIO_DEFAULT_ENDPOINT
    model_name = str(model or GOOGLE_AISTUDIO_DEFAULT_MODEL).strip() or GOOGLE_AISTUDIO_DEFAULT_MODEL
    resource = model_name if model_name.startswith("models/") else f"models/{model_name}"
    return f"{base}/{resource}:generateContent"


def _resolved_api_key(api_key: Any, api_key_env: Any = "") -> str:
    direct_key = _clean_text(api_key, max_length=500)
    if direct_key:
        return direct_key
    env_name = _clean_text(api_key_env, max_length=80)
    if not env_name:
        return ""
    return str(os.environ.get(env_name) or "").strip()


def _vlm_source_name(provider: str) -> str:
    if provider == "local_vlm":
        return "local-vlm"
    if provider == "google_aistudio_vlm":
        return "google-aistudio-vlm"
    return "openai-compatible-vlm"


def _swift_module_cache_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "ipet-vision-swift-module-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clamp_int(value: Any, *, fallback: int, min_value: int, max_value: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = fallback
    return max(min_value, min(max_value, number))


def _clamp_float(value: Any, *, fallback: float, min_value: float, max_value: float) -> float:
    try:
        number = float(value)
    except Exception:
        number = fallback
    return max(min_value, min(max_value, number))


_MACOS_VISION_OCR_SWIFT = textwrap.dedent(
    r'''
    import Foundation
    import Vision
    import CoreImage

    let args = CommandLine.arguments
    guard args.count >= 2 else {
        FileHandle.standardError.write(Data("missing image path\n".utf8))
        exit(2)
    }

    let imageURL = URL(fileURLWithPath: args[1])
    let maxChars = args.count >= 3 ? (Int(args[2]) ?? 600) : 600
    guard let ciImage = CIImage(contentsOf: imageURL) else {
        FileHandle.standardError.write(Data("cannot load image\n".utf8))
        exit(3)
    }

    var recognized: [String] = []
    let request = VNRecognizeTextRequest { request, error in
        if let error = error {
            FileHandle.standardError.write(Data(error.localizedDescription.utf8))
            return
        }
        let observations = request.results as? [VNRecognizedTextObservation] ?? []
        for observation in observations {
            if let candidate = observation.topCandidates(1).first {
                recognized.append(candidate.string)
            }
        }
    }
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(ciImage: ciImage, options: [:])
    do {
        try handler.perform([request])
        let joined = recognized.joined(separator: " ")
        print(String(joined.prefix(maxChars)))
    } catch {
        FileHandle.standardError.write(Data(error.localizedDescription.utf8))
        exit(4)
    }
    '''
).strip()


_VLM_SYSTEM_PROMPT = (
    "You are Ipet's screenshot observation helper. Answer Brain's screenshot question in natural language only. "
    "Do not invent anything outside the visible image."
)
