from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Callable


DEFAULT_VISION_ROUTING_CONFIG: dict[str, Any] = {
    "enabled": True,
    "visual_change_threshold": 0,
    "vlm_cooldown_sec": 10.0,
    "stable_after_change_ms": 700,
}

MAX_CONTEXT_FIELDS = 16
MAX_CONTEXT_KEY_LENGTH = 64
MAX_CONTEXT_VALUE_LENGTH = 240
MAX_ROUTE_REASON_LENGTH = 80
MAX_EVENT_NAME_LENGTH = 80
MAX_HASH_LENGTH = 96
MAX_CAPTURE_BACKEND_LENGTH = 40
MAX_CAPTURE_SCOPE_LENGTH = 80
RECENT_EVENT_LIMIT = 12

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_SENSITIVE_CONTEXT_KEYS = {
    "access_token",
    "apikey",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "data_url",
    "image",
    "password",
    "screenshot",
    "secret",
    "session",
    "token",
}


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


def _is_sensitive_context_key(key: str) -> bool:
    normalized = str(key or "").strip().casefold().replace("-", "_").replace(" ", "_")
    compact = normalized.replace("_", "")
    if normalized in _SENSITIVE_CONTEXT_KEYS or compact in _SENSITIVE_CONTEXT_KEYS:
        return True
    return any(
        marker in normalized or marker in compact
        for marker in ("apikey", "api_key", "access_token", "auth_token", "credential", "password", "secret", "token")
    )


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return json.dumps(str(value), ensure_ascii=False)


def _sanitize_context_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _clean_text(raw_key, max_length=MAX_CONTEXT_KEY_LENGTH)
            if not key or _is_sensitive_context_key(key):
                continue
            nested = _sanitize_context_value(raw_value)
            if nested in ("", [], {}):
                continue
            cleaned[key] = nested
            if len(cleaned) >= MAX_CONTEXT_FIELDS:
                break
        return cleaned
    if isinstance(value, (list, tuple)):
        cleaned_list = []
        for item in value[:MAX_CONTEXT_FIELDS]:
            nested = _sanitize_context_value(item)
            if nested in ("", [], {}):
                continue
            cleaned_list.append(nested)
        return cleaned_list
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _clean_text(value, max_length=MAX_CONTEXT_VALUE_LENGTH)
    return _clean_text(str(value), max_length=MAX_CONTEXT_VALUE_LENGTH)


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def normalize_vision_routing_config(config: Any) -> dict[str, Any]:
    source = config if isinstance(config, dict) else {}
    defaults = DEFAULT_VISION_ROUTING_CONFIG
    return {
        "enabled": bool(source.get("enabled", defaults["enabled"])),
        "visual_change_threshold": _clamp_int(
            source.get("visual_change_threshold", defaults["visual_change_threshold"]),
            fallback=int(defaults["visual_change_threshold"]),
            min_value=0,
            max_value=64,
        ),
        "vlm_cooldown_sec": _clamp_float(
            source.get("vlm_cooldown_sec", defaults["vlm_cooldown_sec"]),
            fallback=float(defaults["vlm_cooldown_sec"]),
            min_value=0.0,
            max_value=300.0,
        ),
        "stable_after_change_ms": _clamp_int(
            source.get("stable_after_change_ms", defaults["stable_after_change_ms"]),
            fallback=int(defaults["stable_after_change_ms"]),
            min_value=0,
            max_value=10000,
        ),
    }


def sanitize_desktop_context(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    sanitized: dict[str, Any] = {}
    for raw_key, raw_value in source.items():
        key = _clean_text(raw_key, max_length=MAX_CONTEXT_KEY_LENGTH)
        if not key or _is_sensitive_context_key(key):
            continue
        value_out = _sanitize_context_value(raw_value)
        if isinstance(value_out, (dict, list)):
            value_out = _clean_text(_stable_json(value_out), max_length=MAX_CONTEXT_VALUE_LENGTH)
        sanitized[key] = value_out
        if len(sanitized) >= MAX_CONTEXT_FIELDS:
            break
    return sanitized


def sanitize_route_decision(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "action": _clean_text(source.get("action") or "skip", max_length=40) or "skip",
        "reason": _clean_text(source.get("reason") or "", max_length=MAX_ROUTE_REASON_LENGTH),
        "events": [
            _clean_text(item, max_length=MAX_EVENT_NAME_LENGTH)
            for item in (source.get("events") if isinstance(source.get("events"), list) else [])
            if _clean_text(item, max_length=MAX_EVENT_NAME_LENGTH)
        ][:RECENT_EVENT_LIMIT],
        "should_analyze": bool(source.get("should_analyze", False)),
        "reuse_evidence": bool(source.get("reuse_evidence", False)),
        "pending_analysis": bool(source.get("pending_analysis", False)),
        "cooldown_remaining_sec": round(
            _clamp_float(source.get("cooldown_remaining_sec", 0.0), fallback=0.0, min_value=0.0, max_value=300.0),
            3,
        ),
        "visual_distance": source.get("visual_distance") if isinstance(source.get("visual_distance"), int) else None,
        "context_key": _clean_text(source.get("context_key") or "", max_length=MAX_HASH_LENGTH),
        "frame_hash": _clean_text(source.get("frame_hash") or "", max_length=MAX_HASH_LENGTH),
    }


def _payload_frame_hash(payload: dict[str, Any]) -> str:
    supplied = _clean_text(payload.get("frame_hash"), max_length=MAX_HASH_LENGTH)
    if supplied:
        return supplied
    data_url = str(payload.get("data_url") or "")
    return "sha256:" + hashlib.sha256(data_url.encode("utf-8")).hexdigest()


def _normalize_hash_text(value: Any) -> str:
    text = _clean_text(value, max_length=MAX_HASH_LENGTH)
    if ":" in text:
        text = text.split(":", 1)[1]
    return text.strip().lower()


def _hash_distance(left: str, right: str) -> int | None:
    a = _normalize_hash_text(left)
    b = _normalize_hash_text(right)
    if not a or not b:
        return None
    if a == b:
        return 0
    if len(a) == len(b) and _HEX_RE.fullmatch(a) and _HEX_RE.fullmatch(b):
        return (int(a, 16) ^ int(b, 16)).bit_count()
    return max(len(a), len(b), 1) * 4


def _first_context_value(context: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _clean_text(context.get(key), max_length=MAX_CONTEXT_VALUE_LENGTH)
        if value:
            return value
    return ""


def _display_layout_hash(payload: dict[str, Any]) -> str:
    supplied = _clean_text(payload.get("display_layout_hash"), max_length=MAX_HASH_LENGTH)
    if supplied:
        return supplied
    layout = payload.get("display_layout")
    if isinstance(layout, list):
        return _digest(layout)
    return ""


def _frame_signature(payload: Any) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    frame_hash = _payload_frame_hash(source)
    visual_hash = _clean_text(source.get("visual_hash") or frame_hash, max_length=MAX_HASH_LENGTH)
    desktop_context = sanitize_desktop_context(source.get("desktop_context"))
    if not desktop_context:
        desktop_context = sanitize_desktop_context(
            {
                "foreground_app": source.get("foreground_app"),
                "window_title": source.get("window_title"),
            }
        )
    display_count = _clamp_int(source.get("display_count", 0), fallback=0, min_value=0, max_value=16)
    display_layout_hash = _display_layout_hash(source)
    capture_backend = _clean_text(source.get("capture_backend"), max_length=MAX_CAPTURE_BACKEND_LENGTH)
    capture_scope = _clean_text(source.get("capture_scope"), max_length=MAX_CAPTURE_SCOPE_LENGTH)
    foreground_app = _first_context_value(desktop_context, "foreground_app", "frontmost_app", "app", "app_name")
    window_title = _first_context_value(desktop_context, "window_title", "title", "active_window")
    desktop_context_key = _stable_json(desktop_context)
    context_basis = {
        "display_count": display_count,
        "display_layout_hash": display_layout_hash,
        "foreground_app": foreground_app,
        "window_title": window_title,
    }
    change_basis = {
        **context_basis,
        "desktop_context": desktop_context,
        "visual_hash": visual_hash,
    }
    return {
        "frame_hash": frame_hash,
        "visual_hash": visual_hash,
        "display_count": display_count,
        "display_layout_hash": display_layout_hash,
        "capture_backend": capture_backend,
        "capture_scope": capture_scope,
        "desktop_context": desktop_context,
        "desktop_context_key": desktop_context_key,
        "foreground_app": foreground_app,
        "window_title": window_title,
        "context_key": _digest(context_basis),
        "change_key": _digest(change_basis),
    }


class VisionRoutingState:
    def __init__(self, *, now: Callable[[], float] | None = None) -> None:
        self._now = now or time.time
        self._last_signature: dict[str, Any] | None = None
        self._last_analyzed_signature: dict[str, Any] | None = None
        self._last_decision: dict[str, Any] = sanitize_route_decision({})
        self._last_vlm_at: float | None = None
        self._last_vlm_by_context: dict[str, float] = {}
        self._pending_analysis = False
        self._pending_change_key = ""
        self._pending_since: float | None = None
        self._pending_events: list[str] = []
        self._recent_events: list[dict[str, Any]] = []

    def evaluate(
        self,
        payload: Any,
        config: Any | None = None,
        *,
        analyzer_enabled: bool,
        analysis_in_flight: bool = False,
        force_analyze: bool = False,
    ) -> dict[str, Any]:
        routing = normalize_vision_routing_config(config or {})
        signature = _frame_signature(payload)
        now = float(self._now())
        previous = self._last_signature
        events = self._events_for_change(previous, signature, routing)

        if (
            routing["enabled"]
            and not force_analyze
            and self._pending_change_key
            and signature["change_key"] == self._pending_change_key
        ):
            pending_since = self._pending_since if self._pending_since is not None else now
            elapsed_ms = (now - float(pending_since)) * 1000.0
            if elapsed_ms < int(routing["stable_after_change_ms"]):
                self._last_signature = signature
                return self._finish_decision(
                    signature,
                    action="defer",
                    reason="stabilizing",
                    events=list(self._pending_events),
                    should_analyze=False,
                    pending_analysis=True,
                    visual_distance=self._visual_distance(previous, signature),
                )
            events = list(dict.fromkeys([*self._pending_events, "stable_after_change"]))
            self._pending_change_key = ""
            self._pending_since = None
            self._pending_events = []

        if (
            routing["enabled"]
            and not force_analyze
            and events
            and previous is not None
            and int(routing["stable_after_change_ms"]) > 0
            and "stable_after_change" not in events
        ):
            self._pending_change_key = str(signature["change_key"])
            self._pending_since = now
            self._pending_events = list(events)
            self._last_signature = signature
            return self._finish_decision(
                signature,
                action="defer",
                reason="stabilizing",
                events=events,
                should_analyze=False,
                pending_analysis=True,
                visual_distance=self._visual_distance(previous, signature),
            )

        if force_analyze:
            self._pending_change_key = ""
            self._pending_since = None
            self._pending_events = []

        self._last_signature = signature

        if not routing["enabled"]:
            if analyzer_enabled:
                return self._analyze_decision(signature, ["routing_disabled"], reason="routing_disabled")
            return self._finish_decision(signature, action="skip", reason="analyzer_disabled")

        if not events and not force_analyze:
            return self._finish_decision(
                signature,
                action="reuse",
                reason="no_change",
                reuse_evidence=self._can_reuse_evidence(signature),
                visual_distance=self._visual_distance(previous, signature),
            )

        if not analyzer_enabled:
            return self._finish_decision(
                signature,
                action="skip",
                reason="analyzer_disabled",
                events=events,
                visual_distance=self._visual_distance(previous, signature),
            )

        if analysis_in_flight:
            self._pending_change_key = str(signature.get("change_key") or "")
            self._pending_since = now
            self._pending_events = list(events or ["analysis_in_flight"])
            return self._finish_decision(
                signature,
                action="defer",
                reason="analysis_in_flight",
                events=events,
                should_analyze=False,
                pending_analysis=True,
                visual_distance=self._visual_distance(previous, signature),
            )

        remaining = 0.0 if force_analyze else self._cooldown_remaining(signature, routing)
        if remaining > 0.0:
            return self._finish_decision(
                signature,
                action="skip",
                reason="cooldown",
                events=events,
                cooldown_remaining_sec=remaining,
                visual_distance=self._visual_distance(previous, signature),
            )

        return self._analyze_decision(
            signature,
            events or ["forced_visual_request"],
            reason="force_analyze" if force_analyze else "change_detected",
            visual_distance=self._visual_distance(previous, signature),
        )

    def _events_for_change(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any],
        routing: dict[str, Any],
    ) -> list[str]:
        if previous is None:
            return ["initial_frame"]
        events: list[str] = []
        if (
            previous.get("display_count") != current.get("display_count")
            or previous.get("display_layout_hash") != current.get("display_layout_hash")
        ):
            events.append("display_layout_changed")
        if previous.get("foreground_app") != current.get("foreground_app"):
            events.append("foreground_app_changed")
        if previous.get("window_title") != current.get("window_title"):
            events.append("window_title_changed")
        if previous.get("desktop_context_key") != current.get("desktop_context_key"):
            events.append("desktop_context_changed")
        distance = self._visual_distance(previous, current)
        if distance is not None and distance > int(routing["visual_change_threshold"]):
            events.append("visual_hash_changed")
        return events

    def _visual_distance(self, previous: dict[str, Any] | None, current: dict[str, Any]) -> int | None:
        if previous is None:
            return None
        return _hash_distance(str(previous.get("visual_hash") or ""), str(current.get("visual_hash") or ""))

    def _can_reuse_evidence(self, signature: dict[str, Any]) -> bool:
        if not self._last_analyzed_signature:
            return False
        return str(self._last_analyzed_signature.get("change_key") or "") == str(signature.get("change_key") or "")

    def _cooldown_remaining(self, signature: dict[str, Any], routing: dict[str, Any]) -> float:
        cooldown = float(routing["vlm_cooldown_sec"])
        if cooldown <= 0.0:
            return 0.0
        last_at = self._last_vlm_by_context.get(str(signature.get("context_key") or ""))
        if last_at is None:
            return 0.0
        elapsed = float(self._now()) - float(last_at)
        return max(0.0, cooldown - elapsed)

    def _analyze_decision(
        self,
        signature: dict[str, Any],
        events: list[str],
        *,
        reason: str,
        visual_distance: int | None = None,
    ) -> dict[str, Any]:
        now = float(self._now())
        self._last_vlm_at = now
        self._last_vlm_by_context[str(signature.get("context_key") or "")] = now
        self._last_analyzed_signature = dict(signature)
        return self._finish_decision(
            signature,
            action="analyze",
            reason=reason,
            events=events,
            should_analyze=True,
            visual_distance=visual_distance,
        )

    def _finish_decision(
        self,
        signature: dict[str, Any],
        *,
        action: str,
        reason: str,
        events: list[str] | None = None,
        should_analyze: bool = False,
        reuse_evidence: bool = False,
        pending_analysis: bool = False,
        cooldown_remaining_sec: float = 0.0,
        visual_distance: int | None = None,
    ) -> dict[str, Any]:
        self._pending_analysis = bool(pending_analysis)
        decision = sanitize_route_decision(
            {
                "action": action,
                "reason": reason,
                "events": list(events or []),
                "should_analyze": should_analyze,
                "reuse_evidence": reuse_evidence,
                "pending_analysis": pending_analysis,
                "cooldown_remaining_sec": cooldown_remaining_sec,
                "visual_distance": visual_distance,
                "context_key": signature.get("context_key") or "",
                "frame_hash": signature.get("frame_hash") or "",
            }
        )
        self._last_decision = decision
        self._record_events(signature, decision)
        return dict(decision)

    def _record_events(self, signature: dict[str, Any], decision: dict[str, Any]) -> None:
        events = decision.get("events") if isinstance(decision.get("events"), list) else []
        for event in events:
            self._recent_events.append(
                {
                    "event": _clean_text(event, max_length=MAX_EVENT_NAME_LENGTH),
                    "at": float(self._now()),
                    "frame_hash": _clean_text(signature.get("frame_hash"), max_length=MAX_HASH_LENGTH),
                    "reason": _clean_text(decision.get("reason"), max_length=MAX_ROUTE_REASON_LENGTH),
                }
            )
        if len(self._recent_events) > RECENT_EVENT_LIMIT:
            self._recent_events = self._recent_events[-RECENT_EVENT_LIMIT:]

    def status(self) -> dict[str, Any]:
        signature = self._last_signature or {}
        return {
            "last_route_decision": dict(self._last_decision),
            "pending_analysis": bool(self._pending_analysis),
            "last_vlm_at": self._last_vlm_at,
            "capture_backend": signature.get("capture_backend") or "",
            "capture_scope": signature.get("capture_scope") or "",
            "display_count": int(signature.get("display_count") or 0),
            "desktop_context": dict(signature.get("desktop_context") or {}),
            "recent_events": [dict(item) for item in self._recent_events[-RECENT_EVENT_LIMIT:]],
        }
