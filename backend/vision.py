from __future__ import annotations

import hashlib
import time
from typing import Any, Callable

from .vision_state import (
    DEFAULT_VISION_ROUTING_CONFIG,
    VisionRoutingState,
    normalize_vision_routing_config,
    sanitize_desktop_context,
    sanitize_route_decision,
)


DEFAULT_VISION_CONFIG: dict[str, Any] = {
    "enabled": False,
    "capture_interval_ms": 5000,
    "max_width": 1280,
    "jpeg_quality": 75,
    "context_ttl_sec": 30,
    "inject_policy": "when_requested",
    "force_grounding": False,
    "grounding_mode": "auto",
    "analyzer": {
        "enabled": False,
        "provider": "none",
        "timeout_sec": 15.0,
        "max_text_chars": 600,
        "max_image_bytes": 3_000_000,
        "api_key": "",
        "model": "",
        "base_url": "",
        "api_key_env": "",
        "image_detail": "low",
        "max_observations": 4,
        "fallback_to_runtime": True,
    },
    "routing": dict(DEFAULT_VISION_ROUTING_CONFIG),
    "active_observation": {
        "enabled": True,
        "allowed_interaction": "light",
        "timeout_sec": 15.0,
        "settle_ms": 500,
    },
    "passive_capture": {
        "use_for_forced": False,
    },
    "include_ui_metadata": True,
    "persist_frames": False,
}

VISION_INJECT_POLICIES = {"off", "when_requested", "always_summary"}
VISION_GROUNDING_MODES = {"auto", "always", "off"}
VISION_FALLBACK_SUMMARY = "当前有一张最近捕获的屏幕帧，但尚无可验证视觉证据。"
MAX_DATA_URL_LENGTH = 3_000_000
MAX_FRAME_ID_LENGTH = 80
MAX_FRAME_HASH_LENGTH = 96
MAX_SUMMARY_LENGTH = 500
MAX_OBSERVATIONS = 8
MAX_OBSERVATION_CLAIM_LENGTH = 240
MAX_OBSERVATION_EVIDENCE_LENGTH = 360
MAX_OBSERVATION_REGION_LENGTH = 80
MAX_OBSERVATION_SOURCE_LENGTH = 40
MAX_UNKNOWNS = 8
MAX_UNKNOWN_LENGTH = 160
MAX_ACTIVE_OBSERVATION_ACTIONS = 8
MAX_PASSIVE_TIMELINE_EVENTS = 5
MAX_TIMELINE_SUMMARY_LENGTH = 180
MAX_TIMELINE_TEXT_ITEMS = 6
MAX_TIMELINE_TEXT_LENGTH = 120
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png"}
VISION_ANALYZER_PROVIDERS = {"none", "macos_vision_ocr", "openai_compatible_vlm", "local_vlm"}
VISION_ANALYZER_IMAGE_DETAILS = {"low", "high", "auto"}
VISION_ACTIVE_INTERACTION_LEVELS = {"light", "none"}
VISION_REQUEST_KEYWORDS = (
    "屏幕",
    "截图",
    "画面",
    "视觉",
    "看一下",
    "看看",
    "看到",
    "看见",
    "看得见",
    "当前界面",
    "当前窗口",
    "窗口",
    "浏览器",
    "网站",
    "网页",
    "页面",
    "image",
    "screen",
    "screenshot",
    "visual",
    "look",
    "see",
    "browser",
    "website",
    "webpage",
    "web page",
)
NON_VISUAL_OBSERVATION_SOURCES = {"macos-system-events"}
NON_VISUAL_OBSERVATION_REGIONS = {"macos menu bar left"}


class VisionError(ValueError):
    pass


class VisionDisabledError(VisionError):
    pass


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


def _clean_text(value: Any, *, max_length: int) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = " ".join(text.split())
    return text[:max_length]


def normalize_vision_config(config: Any) -> dict[str, Any]:
    source = config if isinstance(config, dict) else {}
    normalized = dict(DEFAULT_VISION_CONFIG)
    normalized["enabled"] = bool(source.get("enabled", DEFAULT_VISION_CONFIG["enabled"]))
    normalized["capture_interval_ms"] = _clamp_int(
        source.get("capture_interval_ms", DEFAULT_VISION_CONFIG["capture_interval_ms"]),
        fallback=DEFAULT_VISION_CONFIG["capture_interval_ms"],
        min_value=1000,
        max_value=60000,
    )
    normalized["max_width"] = _clamp_int(
        source.get("max_width", DEFAULT_VISION_CONFIG["max_width"]),
        fallback=DEFAULT_VISION_CONFIG["max_width"],
        min_value=320,
        max_value=2560,
    )
    normalized["jpeg_quality"] = _clamp_int(
        source.get("jpeg_quality", DEFAULT_VISION_CONFIG["jpeg_quality"]),
        fallback=DEFAULT_VISION_CONFIG["jpeg_quality"],
        min_value=35,
        max_value=95,
    )
    normalized["context_ttl_sec"] = _clamp_int(
        source.get("context_ttl_sec", DEFAULT_VISION_CONFIG["context_ttl_sec"]),
        fallback=DEFAULT_VISION_CONFIG["context_ttl_sec"],
        min_value=5,
        max_value=300,
    )
    policy = str(source.get("inject_policy") or DEFAULT_VISION_CONFIG["inject_policy"]).strip()
    normalized["inject_policy"] = policy if policy in VISION_INJECT_POLICIES else "when_requested"
    normalized["force_grounding"] = bool(source.get("force_grounding", DEFAULT_VISION_CONFIG["force_grounding"]))
    mode = str(source.get("grounding_mode") or DEFAULT_VISION_CONFIG["grounding_mode"]).strip()
    normalized["grounding_mode"] = mode if mode in VISION_GROUNDING_MODES else "auto"
    analyzer_defaults = DEFAULT_VISION_CONFIG["analyzer"]
    analyzer = source.get("analyzer") if isinstance(source.get("analyzer"), dict) else {}
    analyzer_provider = _clean_text(analyzer.get("provider") or analyzer_defaults["provider"], max_length=40) or "none"
    normalized["analyzer"] = {
        "enabled": bool(analyzer.get("enabled", analyzer_defaults["enabled"])),
        "provider": analyzer_provider,
        "timeout_sec": _clamp_float(
            analyzer.get("timeout_sec", analyzer_defaults["timeout_sec"]),
            fallback=analyzer_defaults["timeout_sec"],
            min_value=0.2,
            max_value=30.0,
        ),
        "max_text_chars": _clamp_int(
            analyzer.get("max_text_chars", analyzer_defaults["max_text_chars"]),
            fallback=analyzer_defaults["max_text_chars"],
            min_value=40,
            max_value=2000,
        ),
        "max_image_bytes": _clamp_int(
            analyzer.get("max_image_bytes", analyzer_defaults["max_image_bytes"]),
            fallback=analyzer_defaults["max_image_bytes"],
            min_value=1024,
            max_value=8_000_000,
        ),
        "api_key": _clean_text(analyzer.get("api_key"), max_length=500),
        "model": _clean_text(analyzer.get("model"), max_length=120),
        "base_url": _clean_text(analyzer.get("base_url"), max_length=300).rstrip("/"),
        "api_key_env": _clean_text(analyzer.get("api_key_env") or analyzer_defaults["api_key_env"], max_length=80),
        "image_detail": _clean_text(analyzer.get("image_detail") or analyzer_defaults["image_detail"], max_length=20),
        "max_observations": _clamp_int(
            analyzer.get("max_observations", analyzer_defaults["max_observations"]),
            fallback=analyzer_defaults["max_observations"],
            min_value=1,
            max_value=MAX_OBSERVATIONS,
        ),
        "fallback_to_runtime": bool(analyzer.get("fallback_to_runtime", analyzer_defaults["fallback_to_runtime"])),
    }
    if normalized["analyzer"]["image_detail"] not in VISION_ANALYZER_IMAGE_DETAILS:
        normalized["analyzer"]["image_detail"] = analyzer_defaults["image_detail"]
    if analyzer_provider == "local_vlm" and not normalized["analyzer"]["base_url"]:
        normalized["analyzer"]["base_url"] = "http://127.0.0.1:11434/v1"
        if not analyzer.get("api_key_env"):
            normalized["analyzer"]["api_key_env"] = ""
    elif analyzer_provider == "openai_compatible_vlm" and not normalized["analyzer"]["base_url"]:
        normalized["analyzer"]["base_url"] = "https://api.openai.com/v1"
    normalized["include_ui_metadata"] = bool(
        source.get("include_ui_metadata", DEFAULT_VISION_CONFIG["include_ui_metadata"])
    )
    normalized["routing"] = normalize_vision_routing_config(source.get("routing", DEFAULT_VISION_ROUTING_CONFIG))
    active_defaults = DEFAULT_VISION_CONFIG["active_observation"]
    active_source = source.get("active_observation") if isinstance(source.get("active_observation"), dict) else {}
    allowed_interaction = _clean_text(
        active_source.get("allowed_interaction") or active_defaults["allowed_interaction"],
        max_length=40,
    )
    normalized["active_observation"] = {
        "enabled": bool(active_source.get("enabled", active_defaults["enabled"])),
        "allowed_interaction": allowed_interaction if allowed_interaction in VISION_ACTIVE_INTERACTION_LEVELS else "light",
        "timeout_sec": _clamp_float(
            active_source.get("timeout_sec", active_defaults["timeout_sec"]),
            fallback=active_defaults["timeout_sec"],
            min_value=1.0,
            max_value=20.0,
        ),
        "settle_ms": _clamp_int(
            active_source.get("settle_ms", active_defaults["settle_ms"]),
            fallback=active_defaults["settle_ms"],
            min_value=100,
            max_value=2000,
        ),
    }
    passive_defaults = DEFAULT_VISION_CONFIG["passive_capture"]
    passive_source = source.get("passive_capture") if isinstance(source.get("passive_capture"), dict) else {}
    normalized["passive_capture"] = {
        "use_for_forced": bool(passive_source.get("use_for_forced", passive_defaults["use_for_forced"]))
    }
    normalized["persist_frames"] = False
    return normalized


def _looks_like_visual_request(text: str) -> bool:
    normalized = str(text or "").lower()
    return any(keyword in normalized for keyword in VISION_REQUEST_KEYWORDS)


def should_force_grounding(text: str, config: Any) -> bool:
    vision_cfg = normalize_vision_config(config)
    if vision_cfg["grounding_mode"] == "off":
        return False
    if vision_cfg["force_grounding"] or vision_cfg["grounding_mode"] == "always":
        return True
    return _looks_like_visual_request(text)


def _sanitize_observations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    observations: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        claim = _clean_text(item.get("claim"), max_length=MAX_OBSERVATION_CLAIM_LENGTH)
        evidence = _clean_text(item.get("evidence"), max_length=MAX_OBSERVATION_EVIDENCE_LENGTH)
        if not claim:
            continue
        source = _clean_text(item.get("source"), max_length=MAX_OBSERVATION_SOURCE_LENGTH)
        region = _clean_text(item.get("region"), max_length=MAX_OBSERVATION_REGION_LENGTH)
        if source.lower() in NON_VISUAL_OBSERVATION_SOURCES or region.lower() in NON_VISUAL_OBSERVATION_REGIONS:
            continue
        observation = {
            "claim": claim,
            "evidence": evidence,
            "region": region,
            "confidence": _clamp_float(item.get("confidence"), fallback=0.0, min_value=0.0, max_value=1.0),
            "source": source,
        }
        observations.append(observation)
        if len(observations) >= MAX_OBSERVATIONS:
            break
    return observations


def _sanitize_unknowns(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    unknowns: list[str] = []
    for item in value:
        text = _clean_text(item, max_length=MAX_UNKNOWN_LENGTH)
        if not text:
            continue
        unknowns.append(text)
        if len(unknowns) >= MAX_UNKNOWNS:
            break
    return unknowns


def _sanitize_analysis_metadata(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(source.get("enabled", False)),
        "provider": _clean_text(source.get("provider") or "none", max_length=40) or "none",
        "status": _clean_text(source.get("status") or "", max_length=40),
        "last_error": _clean_text(source.get("last_error") or "", max_length=MAX_UNKNOWN_LENGTH),
        "observations_added": _clamp_int(source.get("observations_added", 0), fallback=0, min_value=0, max_value=MAX_OBSERVATIONS),
        "unknowns_added": _clamp_int(source.get("unknowns_added", 0), fallback=0, min_value=0, max_value=MAX_UNKNOWNS),
    }


def _sanitize_active_observation_metadata(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    actions = source.get("actions") if isinstance(source.get("actions"), list) else []
    blocked = source.get("blocked_actions") if isinstance(source.get("blocked_actions"), list) else []
    available_next_targets = source.get("available_next_targets") if isinstance(source.get("available_next_targets"), list) else []
    attempted_targets = source.get("attempted_targets") if isinstance(source.get("attempted_targets"), list) else []
    desktop_targets = source.get("desktop_targets") if isinstance(source.get("desktop_targets"), list) else []
    action_trace = source.get("action_trace") if isinstance(source.get("action_trace"), list) else []
    focused_target = source.get("focused_target") if isinstance(source.get("focused_target"), dict) else {}
    click_point = source.get("click_point") if isinstance(source.get("click_point"), dict) else {}
    discovery_errors = source.get("discovery_errors") if isinstance(source.get("discovery_errors"), list) else []
    target_candidates = source.get("target_candidates") if isinstance(source.get("target_candidates"), list) else []
    selected_candidate = source.get("selected_candidate") if isinstance(source.get("selected_candidate"), dict) else {}
    focus_result = source.get("focus_result") if isinstance(source.get("focus_result"), dict) else {}
    verify_result = source.get("verify_result") if isinstance(source.get("verify_result"), dict) else {}

    def _sanitize_point(value: Any) -> dict[str, int]:
        point = value if isinstance(value, dict) else {}
        return {
            "x": _clamp_int(point.get("x"), fallback=0, min_value=-100000, max_value=100000),
            "y": _clamp_int(point.get("y"), fallback=0, min_value=-100000, max_value=100000),
        }

    def _sanitize_bounds(value: Any) -> dict[str, int]:
        bounds = value if isinstance(value, dict) else {}
        return {
            "x": _clamp_int(bounds.get("x"), fallback=0, min_value=-100000, max_value=100000),
            "y": _clamp_int(bounds.get("y"), fallback=0, min_value=-100000, max_value=100000),
            "width": _clamp_int(bounds.get("width"), fallback=0, min_value=0, max_value=100000),
            "height": _clamp_int(bounds.get("height"), fallback=0, min_value=0, max_value=100000),
        }

    def _sanitize_target(item: Any) -> dict[str, Any]:
        target = item if isinstance(item, dict) else {}
        return {
            "target_id": _clean_text(target.get("target_id"), max_length=120),
            "app": _clean_text(target.get("app"), max_length=120),
            "title": _clean_text(target.get("title"), max_length=200),
            "bounds": _sanitize_bounds(target.get("bounds")),
            "frontmost": bool(target.get("frontmost", False)),
            "minimized": bool(target.get("minimized", False)),
            "focus_point": _sanitize_point(target.get("focus_point")),
        }

    def _sanitize_candidate(item: Any) -> dict[str, Any]:
        candidate = item if isinstance(item, dict) else {}
        sanitized = {
            "target_id": _clean_text(candidate.get("target_id"), max_length=120),
            "source": _clean_text(candidate.get("source"), max_length=40),
            "app": _clean_text(candidate.get("app"), max_length=120),
            "title": _clean_text(candidate.get("title"), max_length=200),
            "bounds": _sanitize_bounds(candidate.get("bounds")),
            "frontmost": bool(candidate.get("frontmost", False)),
            "minimized": bool(candidate.get("minimized", False)),
            "focus_point": _sanitize_point(candidate.get("focus_point")),
            "focusable": bool(candidate.get("focusable", False)),
            "score": _clamp_float(candidate.get("score"), fallback=0.0, min_value=0.0, max_value=100.0),
        }
        for key in ("bundle_id", "pid", "reason"):
            text = _clean_text(candidate.get(key), max_length=160)
            if text:
                sanitized[key] = text
        return sanitized

    def _sanitize_step_result(item: Any) -> dict[str, Any]:
        result = item if isinstance(item, dict) else {}
        sanitized = {
            "status": _clean_text(result.get("status"), max_length=40),
            "method": _clean_text(result.get("method"), max_length=80),
            "reason": _clean_text(result.get("reason"), max_length=160),
            "frame_hash": _clean_text(result.get("frame_hash"), max_length=120),
        }
        return {key: value for key, value in sanitized.items() if value not in ("", None)}

    return {
        "enabled": bool(source.get("enabled", True)),
        "status": _clean_text(source.get("status"), max_length=40),
        "mode": _clean_text(source.get("mode"), max_length=40),
        "target_id": _clean_text(source.get("target_id"), max_length=120),
        "target_hint": _clean_text(source.get("target_hint"), max_length=80),
        "attempt_index": _clamp_int(source.get("attempt_index", 0), fallback=0, min_value=0, max_value=99),
        "attempt_reason": _clean_text(source.get("attempt_reason"), max_length=240),
        "foreground_app": _clean_text(source.get("foreground_app"), max_length=120),
        "window_title": _clean_text(source.get("window_title"), max_length=200),
        "frame_hash": _clean_text(source.get("frame_hash"), max_length=120),
        "desktop_targets": [
            target
            for target in (_sanitize_target(item) for item in desktop_targets[:12])
            if target.get("target_id")
        ],
        "discovery_errors": [
            _clean_text(item, max_length=180)
            for item in discovery_errors[:6]
            if _clean_text(item, max_length=180)
        ],
        "target_candidates": [
            candidate
            for candidate in (_sanitize_candidate(item) for item in target_candidates[:12])
            if candidate.get("target_id") and candidate.get("source")
        ],
        "selected_candidate": _sanitize_candidate(selected_candidate) if selected_candidate else {},
        "focus_result": _sanitize_step_result(focus_result) if focus_result else {},
        "verify_result": _sanitize_step_result(verify_result) if verify_result else {},
        "detail_frames_count": _clamp_int(source.get("detail_frames_count", 0), fallback=0, min_value=0, max_value=8),
        "focused_target": _sanitize_target(focused_target) if focused_target else {},
        "action_trace": [
            {
                "action": _clean_text(item.get("action"), max_length=80),
                "status": _clean_text(item.get("status"), max_length=40),
                "target_id": _clean_text(item.get("target_id"), max_length=120),
                "app": _clean_text(item.get("app"), max_length=120),
                "title": _clean_text(item.get("title"), max_length=200),
            }
            for item in action_trace[:8]
            if isinstance(item, dict)
        ],
        "click_point": _sanitize_point(click_point) if click_point else {},
        "click_policy": _clean_text(source.get("click_policy"), max_length=80),
        "self_occluded": bool(source.get("self_occluded", False)),
        "relevance_hint": _clean_text(source.get("relevance_hint"), max_length=80),
        "available_next_targets": [
            _clean_text(item, max_length=80)
            for item in available_next_targets[:8]
            if _clean_text(item, max_length=80)
        ],
        "attempted_targets": [
            _clean_text(item, max_length=80)
            for item in attempted_targets[:10]
            if _clean_text(item, max_length=80)
        ],
        "stop": bool(source.get("stop", False)),
        "actions": [
            _clean_text(item, max_length=40)
            for item in actions[:MAX_ACTIVE_OBSERVATION_ACTIONS]
            if _clean_text(item, max_length=40)
        ],
        "blocked_actions": [
            _clean_text(item, max_length=40)
            for item in blocked[:MAX_ACTIVE_OBSERVATION_ACTIONS]
            if _clean_text(item, max_length=40)
        ],
        "reason": _clean_text(source.get("reason"), max_length=160),
        "error": _clean_text(source.get("error"), max_length=MAX_UNKNOWN_LENGTH),
        "unknowns": _sanitize_unknowns(source.get("unknowns")),
    }


def _sanitize_text_items(value: Any, *, max_items: int = MAX_TIMELINE_TEXT_ITEMS) -> list[str]:
    raw_items = value if isinstance(value, list) else [value] if value not in (None, "") else []
    items: list[str] = []
    for item in raw_items:
        text = _clean_text(item, max_length=MAX_TIMELINE_TEXT_LENGTH)
        if text and text not in items:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _best_observation_confidence(observations: list[dict[str, Any]]) -> float:
    if not observations:
        return 0.0
    return max(_clamp_float(item.get("confidence"), fallback=0.0, min_value=0.0, max_value=1.0) for item in observations)


def _passive_timeline_change_summary(payload: dict[str, Any], summary: str, observations: list[dict[str, Any]]) -> str:
    explicit = _clean_text(payload.get("change_summary"), max_length=MAX_TIMELINE_SUMMARY_LENGTH)
    if explicit:
        return explicit
    if summary:
        return _clean_text(summary, max_length=MAX_TIMELINE_SUMMARY_LENGTH)
    claims = [_clean_text(item.get("claim"), max_length=MAX_TIMELINE_SUMMARY_LENGTH) for item in observations[:2]]
    claims = [item for item in claims if item]
    return "；".join(claims)[:MAX_TIMELINE_SUMMARY_LENGTH]


def _should_record_passive_timeline_event(route_decision: dict[str, Any], summary: str, observations: list[dict[str, Any]], payload: dict[str, Any]) -> bool:
    if not (summary or observations or payload.get("change_summary")):
        return False
    reason = str(route_decision.get("reason") or "").strip()
    action = str(route_decision.get("action") or "").strip()
    if reason in {"no_change", "cooldown", "stabilizing", "analysis_in_flight"} or action in {"reuse", "defer"}:
        return False
    events = route_decision.get("events") if isinstance(route_decision.get("events"), list) else []
    if events or route_decision.get("should_analyze") or observations or payload.get("change_summary"):
        return True
    return False


def _public_analyzer_status(config: Any, analysis: Any = None) -> dict[str, Any]:
    analyzer = config if isinstance(config, dict) else {}
    status = analysis if isinstance(analysis, dict) else {}
    return {
        "enabled": bool(analyzer.get("enabled", False)),
        "provider": _clean_text(analyzer.get("provider") or "none", max_length=40) or "none",
        "last_status": _clean_text(status.get("status") or "", max_length=40),
        "last_error": _clean_text(status.get("last_error") or "", max_length=MAX_UNKNOWN_LENGTH),
        "observations_added": _clamp_int(
            status.get("observations_added", 0),
            fallback=0,
            min_value=0,
            max_value=MAX_OBSERVATIONS,
        ),
        "unknowns_added": _clamp_int(
            status.get("unknowns_added", 0),
            fallback=0,
            min_value=0,
            max_value=MAX_UNKNOWNS,
        ),
    }


def _sanitize_capture_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "capture_backend": _clean_text(payload.get("capture_backend"), max_length=40),
        "capture_scope": _clean_text(payload.get("capture_scope"), max_length=80),
        "display_count": _clamp_int(payload.get("display_count", 0), fallback=0, min_value=0, max_value=16),
        "desktop_context": sanitize_desktop_context(payload.get("desktop_context")),
    }


def _frame_hash(data_url: str) -> str:
    return "sha256:" + hashlib.sha256(data_url.encode("utf-8")).hexdigest()


def _frame_metadata(payload: dict[str, Any], data_url: str, captured_at: float) -> tuple[str, str]:
    computed_hash = _frame_hash(data_url)
    frame_hash = _clean_text(payload.get("frame_hash"), max_length=MAX_FRAME_HASH_LENGTH) or computed_hash
    frame_id = _clean_text(payload.get("frame_id"), max_length=MAX_FRAME_ID_LENGTH)
    if not frame_id:
        frame_id = f"frame-{int(captured_at * 1000)}-{computed_hash.removeprefix('sha256:')[:12]}"
    return frame_id, frame_hash


def build_grounded_context_prefix(
    context: dict[str, Any], *, forced: bool = False, attached_active_frames: bool = False
) -> str:
    available = bool(context.get("available"))
    grounded = bool(context.get("grounded"))
    observations = context.get("observations") if isinstance(context.get("observations"), list) else []
    frame_id = _clean_text(context.get("frame_id"), max_length=MAX_FRAME_ID_LENGTH) or "unknown"
    frame_hash = _clean_text(context.get("frame_hash"), max_length=MAX_FRAME_HASH_LENGTH) or "unknown"
    captured_at = context.get("captured_at")
    age_sec = context.get("age_sec")
    header = "[强制视觉证据]" if forced else "[视觉上下文]"

    if forced and attached_active_frames:
        lines = [
            header,
            "约束：本轮附带 active screenshot/vision_frames；直接看本轮附带的 active screenshot/vision_frames，以图片内容作为主要视觉证据。",
            "读图顺序：先读主内容区、大标题、显著文字；detail crop/局部图只辅助读字，不代替主图判断。",
            "metadata / target_candidates / app/window title 仅用于路由和调试，不能当作答案事实。",
            "回答要求：如果图片里读不清或看不到用户问的内容，就明确说读不清/看不到；不要因为缺少 structured observations 自动拒答。",
            f"帧：frame_id={frame_id} frame_hash={frame_hash} captured_at={captured_at} age_sec={age_sec}",
        ]
        if grounded and observations:
            summary = _clean_text(context.get("verified_summary") or context.get("summary"), max_length=MAX_SUMMARY_LENGTH)
            if summary:
                lines.append(f"结构化视觉摘要（辅助核对）：{summary}")
            lines.append("结构化 observations（辅助核对，不是回答必要条件）：")
            for index, observation in enumerate(observations[:MAX_OBSERVATIONS], 1):
                claim = _clean_text(observation.get("claim"), max_length=MAX_OBSERVATION_CLAIM_LENGTH)
                evidence = _clean_text(observation.get("evidence"), max_length=MAX_OBSERVATION_EVIDENCE_LENGTH)
                region = _clean_text(observation.get("region"), max_length=MAX_OBSERVATION_REGION_LENGTH)
                confidence = _clamp_float(observation.get("confidence"), fallback=0.0, min_value=0.0, max_value=1.0)
                detail = f"{index}. {claim} (confidence={confidence:.2f}"
                if region:
                    detail += f", region={region}"
                detail += ")"
                if evidence:
                    detail += f" evidence={evidence}"
                lines.append(detail)
        unknowns = context.get("unknowns") if isinstance(context.get("unknowns"), list) else []
        if unknowns:
            lines.append(
                "分析器备注（仅调试，不作为回答事实）："
                + "；".join(_clean_text(item, max_length=MAX_UNKNOWN_LENGTH) for item in unknowns[:MAX_UNKNOWNS])
            )
        return "\n".join(lines)

    if grounded and observations:
        lines = [
            header,
            "约束：只能基于下面列出的当前屏幕证据回答；不要把未列出的内容当作事实。",
            "摘要要求：只复述证据支持的内容；不要根据应用名、窗口名或截图状态推测用户正在做什么，也不要给出与屏幕证据无关的延伸建议。",
            "读图要求：若本轮附带 active screenshot/vision_frames，先读主内容区、大标题和显著文字；局部图只用于补充读字，读不清就明确说读不清。",
        ]
        if forced:
            lines.append("如果证据不足以回答用户问题，必须回答“我无法从当前截图确认”。")
            lines.append("强制视觉模式下只回答用户问题本身；不要推测用户正在做什么、程序用途、窗口背后的内容或未列出的屏幕细节。")
        lines.append(f"帧：frame_id={frame_id} frame_hash={frame_hash} captured_at={captured_at} age_sec={age_sec}")
        summary = _clean_text(context.get("verified_summary") or context.get("summary"), max_length=MAX_SUMMARY_LENGTH)
        if summary:
            lines.append(f"摘要：{summary}")
        lines.append("可见证据：")
        for index, observation in enumerate(observations[:MAX_OBSERVATIONS], 1):
            claim = _clean_text(observation.get("claim"), max_length=MAX_OBSERVATION_CLAIM_LENGTH)
            evidence = _clean_text(observation.get("evidence"), max_length=MAX_OBSERVATION_EVIDENCE_LENGTH)
            region = _clean_text(observation.get("region"), max_length=MAX_OBSERVATION_REGION_LENGTH)
            confidence = _clamp_float(observation.get("confidence"), fallback=0.0, min_value=0.0, max_value=1.0)
            detail = f"{index}. {claim} (confidence={confidence:.2f}"
            if region:
                detail += f", region={region}"
            detail += ")"
            if evidence:
                detail += f" evidence={evidence}"
            lines.append(detail)
        unknowns = context.get("unknowns") if isinstance(context.get("unknowns"), list) else []
        if unknowns:
            lines.append("无法确认：" + "；".join(_clean_text(item, max_length=MAX_UNKNOWN_LENGTH) for item in unknowns[:MAX_UNKNOWNS]))
        return "\n".join(lines)

    if available:
        if forced:
            lines = [
                header,
                "约束：有最新截图，但尚无可验证视觉证据。不能根据截图内容进行猜测。",
                f"帧：frame_id={frame_id} frame_hash={frame_hash} captured_at={captured_at} age_sec={age_sec}",
                "读图要求：若本轮附带 active screenshot/vision_frames，先读主内容区、大标题和显著文字；读不清就明确说读不清。",
                "回答要求：必须回答“我无法从当前截图确认”，除非用户问题不需要屏幕内容。",
                "不要推测失败原因，不要承诺重新截图、刷新画面或稍后再看就能确认；只说明缺少可验证视觉证据。",
            ]
            unknowns = context.get("unknowns") if isinstance(context.get("unknowns"), list) else []
            if unknowns:
                lines.append("无法确认：" + "；".join(_clean_text(item, max_length=MAX_UNKNOWN_LENGTH) for item in unknowns[:MAX_UNKNOWNS]))
            return "\n".join(lines)
        return "\n".join(
            [
                header,
                "状态：有最近屏幕帧，但尚无可验证视觉证据；不要据此推断具体窗口、文字或控件。",
                "回复要求：如果用户询问具体屏幕内容，应说明无法从当前截图确认；不要推测失败原因，不要承诺重新截图、刷新画面或稍后再看就能确认。",
            ]
        )

    if forced:
        lines = [
            header,
            "约束：当前没有新鲜、可验证的屏幕观察结果。",
            "回答要求：必须回答“我无法从当前截图确认”，除非用户问题不需要屏幕内容。",
            "不要推测失败原因，不要承诺重新截图、刷新画面或稍后再看就能确认；只说明缺少可验证视觉证据。",
        ]
        unknowns = context.get("unknowns") if isinstance(context.get("unknowns"), list) else []
        if unknowns:
            lines.append("无法确认：" + "；".join(_clean_text(item, max_length=MAX_UNKNOWN_LENGTH) for item in unknowns[:MAX_UNKNOWNS]))
        return "\n".join(lines)
    return ""


class VisionService:
    def __init__(self, config: Any | None = None, *, now: Callable[[], float] | None = None) -> None:
        self._now = now or time.time
        self._config = normalize_vision_config(config or {})
        self._frame: dict[str, Any] | None = None
        self._last_error = ""
        self._last_failure_unknowns: list[str] = []
        self._last_active_observation: dict[str, Any] = {}
        self._active_observation_frame: dict[str, Any] | None = None
        self._passive_timeline: list[dict[str, Any]] = []
        self._routing = VisionRoutingState(now=self._now)

    @property
    def config(self) -> dict[str, Any]:
        return dict(self._config)

    def configure(self, config: Any) -> None:
        self._config = normalize_vision_config(config)

    def evaluate_route(
        self,
        payload: Any,
        *,
        analyzer_enabled: bool,
        analysis_in_flight: bool = False,
        force_analyze: bool = False,
    ) -> dict[str, Any]:
        return self._routing.evaluate(
            payload,
            self._config.get("routing", {}),
            analyzer_enabled=analyzer_enabled,
            analysis_in_flight=analysis_in_flight,
            force_analyze=force_analyze,
        )

    def update_frame(self, payload: Any) -> dict[str, Any]:
        if not self._config["enabled"]:
            self._last_error = "vision is disabled"
            raise VisionDisabledError(self._last_error)
        if not isinstance(payload, dict):
            self._last_error = "frame payload must be an object"
            raise VisionError(self._last_error)
        mime_type = str(payload.get("mime_type") or "").strip().lower()
        data_url = str(payload.get("data_url") or "")
        if mime_type not in ALLOWED_MIME_TYPES:
            self._last_error = "unsupported frame mime_type"
            raise VisionError(self._last_error)
        if not data_url.startswith("data:image/"):
            self._last_error = "frame data_url must start with data:image/"
            raise VisionError(self._last_error)
        if len(data_url) > MAX_DATA_URL_LENGTH:
            self._last_error = "frame data_url is too large"
            raise VisionError(self._last_error)
        captured_at = float(self._now())
        frame_id, frame_hash = _frame_metadata(payload, data_url, captured_at)
        previous_frame = self._active_frame()
        route_decision = sanitize_route_decision(payload.get("route_decision"))
        reuse_existing_evidence = bool(route_decision.get("reuse_evidence")) and previous_frame is not None
        summary = _clean_text(payload.get("summary"), max_length=MAX_SUMMARY_LENGTH)
        observations = _sanitize_observations(payload.get("observations"))
        unknowns = _sanitize_unknowns(payload.get("unknowns"))
        analysis = _sanitize_analysis_metadata(payload.get("analysis"))
        active_observation = _sanitize_active_observation_metadata(payload.get("active_observation"))
        lane = _clean_text(payload.get("lane"), max_length=20).lower()
        if lane not in {"active", "passive"}:
            lane = "active" if (active_observation.get("status") or active_observation.get("target_hint")) else "passive"
        if reuse_existing_evidence:
            if not summary:
                summary = _clean_text(previous_frame.get("summary"), max_length=MAX_SUMMARY_LENGTH)
            if not observations:
                observations = _sanitize_observations(previous_frame.get("observations"))
            if not unknowns:
                unknowns = _sanitize_unknowns(previous_frame.get("unknowns"))
            if not analysis.get("status"):
                analysis = _sanitize_analysis_metadata(previous_frame.get("analysis"))
        capture = _sanitize_capture_metadata(payload)
        self._frame = {
            "frame_id": frame_id,
            "frame_hash": frame_hash,
            "mime_type": mime_type,
            "data_url": data_url,
            "summary": summary,
            "verified_summary": summary if observations else "",
            "observations": observations,
            "unknowns": unknowns,
            "analysis": analysis,
            "active_observation": active_observation,
            "lane": lane,
            "route_decision": route_decision,
            **capture,
            "captured_at": captured_at,
        }
        if lane == "active":
            self._active_observation_frame = dict(self._frame)
        else:
            self._record_passive_timeline_event(
                payload,
                frame_id=frame_id,
                frame_hash=frame_hash,
                captured_at=captured_at,
                summary=summary,
                observations=observations,
                route_decision=route_decision,
            )
        self._last_error = analysis.get("last_error") or ""
        if active_observation.get("status") or active_observation.get("target_hint"):
            self._last_active_observation = active_observation
        self._last_failure_unknowns = []
        return self.status()

    def _active_frame(self) -> dict[str, Any] | None:
        if not self._config["enabled"]:
            return None
        if not self._frame:
            return None
        age = float(self._now()) - float(self._frame.get("captured_at") or 0.0)
        if age > float(self._config["context_ttl_sec"]):
            return None
        return self._frame

    def _summary_for_frame(self, frame: dict[str, Any]) -> str:
        summary = str(frame.get("verified_summary") or frame.get("summary") or "").strip()
        return summary or VISION_FALLBACK_SUMMARY

    def _record_passive_timeline_event(
        self,
        payload: dict[str, Any],
        *,
        frame_id: str,
        frame_hash: str,
        captured_at: float,
        summary: str,
        observations: list[dict[str, Any]],
        route_decision: dict[str, Any],
    ) -> None:
        if not _should_record_passive_timeline_event(route_decision, summary, observations, payload):
            if self._passive_timeline:
                self._passive_timeline[-1]["last_observed_at"] = captured_at
            return
        change_summary = _passive_timeline_change_summary(payload, summary, observations)
        if not change_summary:
            if self._passive_timeline:
                self._passive_timeline[-1]["last_observed_at"] = captured_at
            return
        important_objects = _sanitize_text_items(payload.get("important_objects"))
        if not important_objects:
            important_objects = _sanitize_text_items(
                [
                    item.get("region")
                    for item in observations
                    if isinstance(item, dict) and str(item.get("region") or "").strip()
                ]
            )
        visible_text = _sanitize_text_items(payload.get("visible_text"))
        if not visible_text:
            visible_text = _sanitize_text_items(
                [
                    item.get("evidence")
                    for item in observations
                    if isinstance(item, dict) and str(item.get("evidence") or "").strip()
                ],
                max_items=3,
            )
        confidence = _clamp_float(
            payload.get("confidence"),
            fallback=_best_observation_confidence(observations),
            min_value=0.0,
            max_value=1.0,
        )
        self._passive_timeline.append(
            {
                "timestamp": captured_at,
                "last_observed_at": captured_at,
                "frame_id": frame_id,
                "frame_hash": frame_hash,
                "change_summary": change_summary,
                "important_objects": important_objects,
                "visible_text": visible_text,
                "confidence": confidence,
            }
        )
        if len(self._passive_timeline) > MAX_PASSIVE_TIMELINE_EVENTS:
            self._passive_timeline = self._passive_timeline[-MAX_PASSIVE_TIMELINE_EVENTS:]

    def passive_timeline(self, *, limit: int = MAX_PASSIVE_TIMELINE_EVENTS) -> list[dict[str, Any]]:
        count = _clamp_int(limit, fallback=MAX_PASSIVE_TIMELINE_EVENTS, min_value=1, max_value=MAX_PASSIVE_TIMELINE_EVENTS)
        if not self._config["enabled"]:
            return []
        return [dict(item) for item in self._passive_timeline[-count:]]

    def passive_context(self, *, limit: int = MAX_PASSIVE_TIMELINE_EVENTS) -> dict[str, Any]:
        return {
            "enabled": bool(self._config["enabled"]),
            "lane": "passive",
            "available": bool(self.passive_timeline(limit=limit)),
            "timeline": self.passive_timeline(limit=limit),
            "prefix": self.passive_timeline_prefix(limit=limit),
        }

    def passive_timeline_prefix(self, *, limit: int = MAX_PASSIVE_TIMELINE_EVENTS) -> str:
        timeline = self.passive_timeline(limit=limit)
        if not timeline:
            return ""
        lines = ["最近屏幕变化："]
        for index, item in enumerate(timeline, 1):
            summary = _clean_text(item.get("change_summary"), max_length=MAX_TIMELINE_SUMMARY_LENGTH)
            if not summary:
                continue
            detail = f"{index}. {summary}"
            objects = _sanitize_text_items(item.get("important_objects"), max_items=3)
            text = _sanitize_text_items(item.get("visible_text"), max_items=3)
            confidence = _clamp_float(item.get("confidence"), fallback=0.0, min_value=0.0, max_value=1.0)
            extras = []
            if objects:
                extras.append("对象=" + ", ".join(objects))
            if text:
                extras.append("文字=" + ", ".join(text))
            extras.append(f"confidence={confidence:.2f}")
            lines.append(f"{detail} ({'；'.join(extras)})")
        return "\n".join(lines).strip()

    def _active_observation_active_frame(self) -> dict[str, Any] | None:
        if not self._config["enabled"] or not self._active_observation_frame:
            return None
        age = float(self._now()) - float(self._active_observation_frame.get("captured_at") or 0.0)
        if age > float(self._config["context_ttl_sec"]):
            return None
        return self._active_observation_frame

    def active_context(self, *, include_image: bool = False) -> dict[str, Any]:
        frame = self._active_observation_active_frame()
        if frame is None:
            return {
                "enabled": bool(self._config["enabled"]),
                "lane": "active",
                "available": False,
                "summary": "",
                "verified_summary": "",
                "grounded": False,
                "evidence_count": 0,
                "observations": [],
                "unknowns": list(self._last_failure_unknowns),
                "analyzer": _public_analyzer_status(self._config["analyzer"]),
                "active_observation": dict(self._last_active_observation),
                "context_ttl_sec": int(self._config["context_ttl_sec"]),
            }
        captured_at = float(frame.get("captured_at") or 0.0)
        observations = frame.get("observations") if isinstance(frame.get("observations"), list) else []
        analysis = frame.get("analysis") if isinstance(frame.get("analysis"), dict) else {}
        payload: dict[str, Any] = {
            "enabled": bool(self._config["enabled"]),
            "lane": "active",
            "available": True,
            "frame_id": frame.get("frame_id") or "",
            "frame_hash": frame.get("frame_hash") or "",
            "summary": self._summary_for_frame(frame),
            "verified_summary": str(frame.get("verified_summary") or ""),
            "grounded": bool(observations),
            "evidence_count": len(observations),
            "observations": [dict(item) for item in observations],
            "unknowns": list(frame.get("unknowns") or []),
            "analyzer": _public_analyzer_status(self._config["analyzer"], analysis),
            "active_observation": dict(frame.get("active_observation") or self._last_active_observation or {}),
            "mime_type": frame.get("mime_type"),
            "captured_at": captured_at,
            "age_sec": max(0.0, float(self._now()) - captured_at),
            "expires_at": captured_at + float(self._config["context_ttl_sec"]),
            "context_ttl_sec": int(self._config["context_ttl_sec"]),
        }
        if include_image:
            payload["image"] = {
                "mime_type": frame.get("mime_type"),
                "data_url": frame.get("data_url"),
            }
        return payload

    def status(self) -> dict[str, Any]:
        frame = self._active_frame()
        captured_at = float(frame.get("captured_at")) if frame else None
        age_sec = max(0.0, float(self._now()) - captured_at) if captured_at is not None else None
        observations = frame.get("observations") if frame and isinstance(frame.get("observations"), list) else []
        grounded = bool(observations)
        analysis = frame.get("analysis") if frame else {}
        analyzer_status = _public_analyzer_status(self._config["analyzer"], analysis)
        route_status = self._routing.status()
        pending_analysis = bool(route_status.get("pending_analysis")) or analyzer_status.get("last_status") == "pending"
        if frame:
            route_status = {
                **route_status,
                "last_route_decision": dict(frame.get("route_decision") or route_status.get("last_route_decision") or {}),
                "capture_backend": frame.get("capture_backend") or route_status.get("capture_backend") or "",
                "capture_scope": frame.get("capture_scope") or route_status.get("capture_scope") or "",
                "display_count": int(frame.get("display_count") or route_status.get("display_count") or 0),
                "desktop_context": dict(frame.get("desktop_context") or route_status.get("desktop_context") or {}),
            }
        route_status["pending_analysis"] = pending_analysis
        return {
            "enabled": bool(self._config["enabled"]),
            "has_frame": frame is not None,
            "frame_id": frame.get("frame_id") if frame else "",
            "frame_hash": frame.get("frame_hash") if frame else "",
            "mime_type": frame.get("mime_type") if frame else "",
            "captured_at": captured_at,
            "age_sec": age_sec,
            "freshness": {
                "fresh": frame is not None,
                "age_sec": age_sec,
                "ttl_sec": int(self._config["context_ttl_sec"]),
            },
            "grounded": grounded,
            "evidence_count": len(observations),
            "context_ttl_sec": int(self._config["context_ttl_sec"]),
            "inject_policy": self._config["inject_policy"],
            "force_grounding": bool(self._config["force_grounding"]),
            "grounding_mode": self._config["grounding_mode"],
            "analyzer": analyzer_status,
            "active_observation": {
                **dict(self._config.get("active_observation") or {}),
                "last_trace": dict(self._last_active_observation),
            },
            "passive_capture": dict(self._config.get("passive_capture") or {}),
            "include_ui_metadata": bool(self._config["include_ui_metadata"]),
            "persist_frames": False,
            "last_error": self._last_error,
            **route_status,
        }

    def context(self, *, include_image: bool = False) -> dict[str, Any]:
        frame = self._active_frame()
        route_status = self._routing.status()
        if not frame:
            return {
                "enabled": bool(self._config["enabled"]),
                "available": False,
                "summary": "",
                "verified_summary": "",
                "grounded": False,
                "evidence_count": 0,
                "observations": [],
                "unknowns": list(self._last_failure_unknowns),
                "analyzer": _public_analyzer_status(self._config["analyzer"]),
                "active_observation": dict(self._last_active_observation),
                "context_ttl_sec": int(self._config["context_ttl_sec"]),
                "last_route_decision": route_status["last_route_decision"],
                "pending_analysis": route_status["pending_analysis"],
                "last_vlm_at": route_status["last_vlm_at"],
                "capture_backend": route_status["capture_backend"],
                "capture_scope": route_status["capture_scope"],
                "display_count": route_status["display_count"],
                "desktop_context": route_status["desktop_context"],
                "recent_events": route_status["recent_events"],
                "timeline": self.passive_timeline(),
            }
        captured_at = float(frame.get("captured_at") or 0.0)
        observations = frame.get("observations") if isinstance(frame.get("observations"), list) else []
        analysis = frame.get("analysis") if isinstance(frame.get("analysis"), dict) else {}
        pending_analysis = bool(route_status.get("pending_analysis")) or str(analysis.get("status") or "") == "pending"
        payload: dict[str, Any] = {
            "enabled": bool(self._config["enabled"]),
            "available": True,
            "frame_id": frame.get("frame_id") or "",
            "frame_hash": frame.get("frame_hash") or "",
            "summary": self._summary_for_frame(frame),
            "verified_summary": str(frame.get("verified_summary") or ""),
            "grounded": bool(observations),
            "evidence_count": len(observations),
            "observations": [dict(item) for item in observations],
            "unknowns": list(frame.get("unknowns") or []),
            "analyzer": _public_analyzer_status(self._config["analyzer"], analysis),
            "active_observation": dict(frame.get("active_observation") or self._last_active_observation or {}),
            "mime_type": frame.get("mime_type"),
            "captured_at": captured_at,
            "age_sec": max(0.0, float(self._now()) - captured_at),
            "expires_at": captured_at + float(self._config["context_ttl_sec"]),
            "context_ttl_sec": int(self._config["context_ttl_sec"]),
            "last_route_decision": dict(frame.get("route_decision") or route_status.get("last_route_decision") or {}),
            "pending_analysis": pending_analysis,
            "last_vlm_at": route_status.get("last_vlm_at"),
            "capture_backend": frame.get("capture_backend") or route_status.get("capture_backend") or "",
            "capture_scope": frame.get("capture_scope") or route_status.get("capture_scope") or "",
            "display_count": int(frame.get("display_count") or route_status.get("display_count") or 0),
            "desktop_context": dict(frame.get("desktop_context") or route_status.get("desktop_context") or {}),
            "recent_events": list(route_status.get("recent_events") or []),
            "timeline": self.passive_timeline(),
        }
        if include_image:
            payload["image"] = {
                "mime_type": frame.get("mime_type"),
                "data_url": frame.get("data_url"),
            }
        return payload

    def clear(self) -> dict[str, Any]:
        self._frame = None
        self._last_error = ""
        self._last_failure_unknowns = []
        self._last_active_observation = {}
        self._active_observation_frame = None
        self._passive_timeline = []
        return self.status()

    def record_active_observation_failure(self, unknowns: Any, active_observation: Any = None) -> dict[str, Any]:
        self._last_failure_unknowns = _sanitize_unknowns(unknowns)
        self._last_active_observation = _sanitize_active_observation_metadata(active_observation)
        self._last_error = self._last_failure_unknowns[0] if self._last_failure_unknowns else ""
        return self.status()

    def should_inject(self, text: str) -> bool:
        if not self._config["enabled"]:
            return False
        if self._config["inject_policy"] == "off":
            return False
        if self._active_frame() is None:
            return False
        if self._config["inject_policy"] == "always_summary":
            return True
        return _looks_like_visual_request(text)
