from __future__ import annotations

import re
import threading
import time
from collections import deque
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4


DEFAULT_ENVIRONMENT_CONFIG: dict[str, Any] = {
    "mode": "off",
    "intensity": "balanced",
    "metadata_enabled": True,
    "screen_context_enabled": False,
    "include_window_titles": False,
    "sensor_interval_sec": 5,
    "event_ttl_sec": 600,
    "poll_interval_sec": 15,
    "initiative_cooldown_minutes": 30,
    "unanswered_backoff_minutes": 120,
    "daily_initiative_limit": 6,
    "minimum_confidence": 0.75,
    "away_threshold_minutes": 10,
    "focus_minutes": 45,
    "quiet_hours_start": 22,
    "quiet_hours_end": 8,
    "speak_enabled": True,
    "open_chat_on_speak": True,
    "blocked_apps": [],
}

ENVIRONMENT_MODES = {"off", "shadow", "active"}
ENVIRONMENT_INTENSITIES = {"quiet", "balanced", "active", "neuro_like"}
SAFE_ACTIVITIES = {
    "test_passed",
    "test_failed",
    "build_succeeded",
    "build_failed",
    "task_completed",
}
MAX_APP_LENGTH = 80
MAX_TITLE_LENGTH = 160
MAX_AUDIT_REASON_LENGTH = 160
MAX_EVENTS = 120
MAX_OPPORTUNITIES = 80
MAX_AUDIT_ENTRIES = 160
MAX_SESSION_DELIVERIES = 80
MAX_DAILY_COUNTERS = 8
MAX_PROACTIVE_MESSAGE_LENGTH = 80
CLAIM_TIMEOUT_SEC = 180

_SENSITIVE_MARKERS = (
    "1password",
    "bitwarden",
    "keepass",
    "lastpass",
    "password",
    "passwords",
    "keychain",
    "private browsing",
    "incognito",
    "bank",
    "banking",
    "payment",
    "wallet",
    "alipay",
    "wechat pay",
    "medical",
    "health record",
    "identity card",
    "身份证",
    "银行卡",
    "网银",
    "支付",
    "钱包",
    "密码",
    "钥匙串",
    "无痕浏览",
    "隐私浏览",
    "病历",
    "医保",
)

_ACTIVITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("test_failed", re.compile(r"(?:tests?\s+failed|failed\s+tests?|\bfail(?:ed|ure)?\b|测试失败)", re.I)),
    ("test_passed", re.compile(r"(?:tests?\s+passed|passed\s+tests?|\bpassed\b|测试通过)", re.I)),
    ("build_failed", re.compile(r"(?:build\s+failed|compilation\s+failed|编译失败|构建失败)", re.I)),
    ("build_succeeded", re.compile(r"(?:build\s+(?:succeeded|success)|compiled\s+successfully|编译成功|构建成功)", re.I)),
)


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _clamp_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def _clamp_float(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def _normalized_choice(value: Any, allowed: set[str], fallback: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    return normalized if normalized in allowed else fallback


def normalize_environment_config(config: Any) -> dict[str, Any]:
    source = config if isinstance(config, dict) else {}
    defaults = DEFAULT_ENVIRONMENT_CONFIG
    blocked_apps = source.get("blocked_apps") if isinstance(source.get("blocked_apps"), list) else []
    normalized_apps: list[str] = []
    for item in blocked_apps:
        text = _clean_text(item, MAX_APP_LENGTH)
        if text and text.casefold() not in {value.casefold() for value in normalized_apps}:
            normalized_apps.append(text)
        if len(normalized_apps) >= 50:
            break
    return {
        "mode": _normalized_choice(source.get("mode"), ENVIRONMENT_MODES, defaults["mode"]),
        "intensity": _normalized_choice(
            source.get("intensity"), ENVIRONMENT_INTENSITIES, defaults["intensity"]
        ),
        "metadata_enabled": source.get("metadata_enabled", defaults["metadata_enabled"]) is not False,
        "screen_context_enabled": source.get("screen_context_enabled", defaults["screen_context_enabled"]) is True,
        "include_window_titles": source.get("include_window_titles", defaults["include_window_titles"]) is True,
        "sensor_interval_sec": _clamp_int(source.get("sensor_interval_sec"), 5, 2, 60),
        "event_ttl_sec": _clamp_int(source.get("event_ttl_sec"), 600, 60, 3600),
        "poll_interval_sec": _clamp_int(source.get("poll_interval_sec"), 15, 5, 300),
        "initiative_cooldown_minutes": _clamp_int(
            source.get("initiative_cooldown_minutes"), 30, 1, 1440
        ),
        "unanswered_backoff_minutes": _clamp_int(
            source.get("unanswered_backoff_minutes"), 120, 5, 10080
        ),
        "daily_initiative_limit": _clamp_int(source.get("daily_initiative_limit"), 6, 1, 48),
        "minimum_confidence": _clamp_float(source.get("minimum_confidence"), 0.75, 0.5, 1.0),
        "away_threshold_minutes": _clamp_int(source.get("away_threshold_minutes"), 10, 1, 240),
        "focus_minutes": _clamp_int(source.get("focus_minutes"), 45, 5, 240),
        "quiet_hours_start": _clamp_int(source.get("quiet_hours_start"), 22, 0, 23),
        "quiet_hours_end": _clamp_int(source.get("quiet_hours_end"), 8, 0, 23),
        "speak_enabled": source.get("speak_enabled", defaults["speak_enabled"]) is not False,
        "open_chat_on_speak": source.get("open_chat_on_speak", defaults["open_chat_on_speak"]) is not False,
        "blocked_apps": normalized_apps,
    }


def is_quiet_hour(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def classify_screen_activity(value: Any) -> str:
    text = _clean_text(value, 1200)
    if not text:
        return ""
    for activity, pattern in _ACTIVITY_PATTERNS:
        if pattern.search(text):
            return activity
    return ""


class EnvironmentService:
    """Process-local, bounded presence state and proactive opportunity gate.

    Raw screenshots and OCR text are deliberately outside this service. Only small,
    allow-listed facts are accepted and all state disappears when the backend exits.
    """

    def __init__(
        self,
        config: Any | None = None,
        *,
        now: Callable[[], float] | None = None,
        local_hour: Callable[[float], int] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._now = now or time.time
        self._local_hour = local_hour or (lambda timestamp: datetime.fromtimestamp(timestamp).astimezone().hour)
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._config = normalize_environment_config(config or {})
        self._lock = threading.RLock()
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._opportunities: deque[dict[str, Any]] = deque(maxlen=MAX_OPPORTUNITIES)
        self._audit: deque[dict[str, Any]] = deque(maxlen=MAX_AUDIT_ENTRIES)
        self._presence: dict[str, Any] = {
            "foreground_app": "",
            "window_title": "",
            "idle_seconds": 0,
            "privacy_blocked": False,
            "last_observed_at": 0.0,
        }
        self._focus_started_at = 0.0
        self._focus_candidate_emitted = False
        self._last_candidate_at: dict[str, float] = {}
        self._last_delivery_at = 0.0
        self._daily_deliveries: dict[str, int] = {}
        self._last_delivered_by_session: dict[str, dict[str, Any]] = {}

    @property
    def config(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._config)

    def configure(self, config: Any) -> None:
        with self._lock:
            previous_mode = self._config.get("mode")
            self._config = normalize_environment_config(config)
            next_mode = self._config["mode"]
            if previous_mode == "active" and next_mode != "active":
                for opportunity in self._opportunities:
                    if opportunity.get("status") in {"pending", "claimed", "awaiting_delivery"}:
                        opportunity["status"] = "disabled"
            if next_mode == "off":
                self._events.clear()
                self._opportunities.clear()
                self._presence = self._empty_presence()
                self._focus_started_at = 0.0
                self._focus_candidate_emitted = False
                self._last_candidate_at.clear()
                self._last_delivered_by_session.clear()
                return
            if not self._config["metadata_enabled"]:
                self._events = deque(
                    (item for item in self._events if item.get("kind") == "screen_activity"),
                    maxlen=MAX_EVENTS,
                )
                self._presence = self._empty_presence()
                self._focus_started_at = 0.0
                self._focus_candidate_emitted = False
                for opportunity in self._opportunities:
                    if opportunity.get("kind") in {"user_returned", "focus_milestone", "app_changed"} and opportunity.get(
                        "status"
                    ) in {"pending", "claimed", "awaiting_delivery"}:
                        opportunity["status"] = "disabled"
            if not self._config["screen_context_enabled"]:
                for opportunity in self._opportunities:
                    if opportunity.get("kind") in SAFE_ACTIVITIES and opportunity.get("status") in {
                        "pending",
                        "claimed",
                        "awaiting_delivery",
                    }:
                        opportunity["status"] = "disabled"

            current_app = str(self._presence.get("foreground_app") or "")
            current_title = str(self._presence.get("window_title") or "")
            if self._sensitive_context(current_app, current_title):
                observed_at = float(self._presence.get("last_observed_at") or self._now())
                self._presence = {
                    **self._empty_presence(),
                    "privacy_blocked": True,
                    "last_observed_at": observed_at,
                }
                self._audit_event("privacy_blocked", reason="config_updated")
            elif not self._config["include_window_titles"]:
                self._presence["window_title"] = ""

            for event in self._events:
                app = str(event.get("foreground_app") or "")
                title = str(event.get("window_title") or "")
                if not self._config["metadata_enabled"]:
                    event["foreground_app"] = ""
                    event["window_title"] = ""
                elif self._sensitive_context(app, title):
                    event["foreground_app"] = ""
                    event["window_title"] = ""
                    event["kind"] = "privacy_blocked"
                elif not self._config["include_window_titles"]:
                    event["window_title"] = ""
            for opportunity in self._opportunities:
                facts = opportunity.get("facts") if isinstance(opportunity.get("facts"), dict) else {}
                app = str(facts.get("foreground_app") or "")
                if not self._config["metadata_enabled"]:
                    facts["foreground_app"] = ""
                elif self._sensitive_context(app, ""):
                    facts["foreground_app"] = ""
                    if opportunity.get("status") in {"pending", "claimed", "awaiting_delivery"}:
                        opportunity["status"] = "disabled"

    @staticmethod
    def _empty_presence() -> dict[str, Any]:
        return {
            "foreground_app": "",
            "window_title": "",
            "idle_seconds": 0,
            "privacy_blocked": False,
            "last_observed_at": 0.0,
        }

    def _audit_event(self, action: str, kind: str = "", reason: str = "", *, now: float | None = None) -> None:
        self._audit.append(
            {
                "timestamp": float(self._now() if now is None else now),
                "action": _clean_text(action, 48),
                "kind": _clean_text(kind, 48),
                "reason": _clean_text(reason, MAX_AUDIT_REASON_LENGTH),
            }
        )

    def _cleanup(self, now: float) -> None:
        cutoff = now - float(self._config["event_ttl_sec"])
        self._events = deque(
            (item for item in self._events if float(item.get("timestamp") or 0.0) >= cutoff),
            maxlen=MAX_EVENTS,
        )
        for opportunity in self._opportunities:
            status = str(opportunity.get("status") or "")
            created_at = float(opportunity.get("created_at") or 0.0)
            if status == "claimed":
                if now - float(opportunity.get("claimed_at") or 0.0) > CLAIM_TIMEOUT_SEC:
                    opportunity["status"] = "expired" if created_at < cutoff else "pending"
                    opportunity["claimed_at"] = 0.0
                continue
            if status in {"pending", "awaiting_delivery"} and created_at < cutoff:
                opportunity["status"] = "expired"
        if float(self._presence.get("last_observed_at") or 0.0) < cutoff:
            self._presence = self._empty_presence()
            self._focus_started_at = 0.0
            self._focus_candidate_emitted = False

        session_cutoff = now - max(
            float(self._config["event_ttl_sec"]),
            float(self._config["unanswered_backoff_minutes"]) * 60.0,
        )
        recent_sessions = sorted(
            (
                (session_id, opportunity)
                for session_id, opportunity in self._last_delivered_by_session.items()
                if float(opportunity.get("delivered_at") or 0.0) >= session_cutoff
            ),
            key=lambda item: float(item[1].get("delivered_at") or 0.0),
        )[-MAX_SESSION_DELIVERIES:]
        self._last_delivered_by_session = dict(recent_sessions)
        if len(self._daily_deliveries) > MAX_DAILY_COUNTERS:
            retained_days = sorted(self._daily_deliveries)[-MAX_DAILY_COUNTERS:]
            self._daily_deliveries = {key: self._daily_deliveries[key] for key in retained_days}

    def _sensitive_context(self, app: str, title: str) -> bool:
        haystack = f"{app}\n{title}".casefold()
        if any(marker.casefold() in haystack for marker in _SENSITIVE_MARKERS):
            return True
        return any(str(item).casefold() in haystack for item in self._config["blocked_apps"] if str(item).strip())

    def sanitize_metadata(self, payload: Any) -> dict[str, Any]:
        source = payload if isinstance(payload, dict) else {}
        desktop_context = source.get("desktop_context") if isinstance(source.get("desktop_context"), dict) else {}
        app = _clean_text(
            source.get("foreground_app") or desktop_context.get("foreground_app"),
            MAX_APP_LENGTH,
        )
        title = _clean_text(
            source.get("window_title") or desktop_context.get("window_title"),
            MAX_TITLE_LENGTH,
        )
        sensitive = self._sensitive_context(app, title)
        if sensitive:
            return {
                "foreground_app": "",
                "window_title": "",
                "idle_seconds": _clamp_int(source.get("idle_seconds"), 0, 0, 86400 * 30),
                "privacy_blocked": True,
            }
        return {
            "foreground_app": app,
            "window_title": title if self._config["include_window_titles"] else "",
            "idle_seconds": _clamp_int(source.get("idle_seconds"), 0, 0, 86400 * 30),
            "privacy_blocked": False,
        }

    def _in_quiet_hours(self, now: float) -> bool:
        return is_quiet_hour(
            int(self._local_hour(now)),
            int(self._config["quiet_hours_start"]),
            int(self._config["quiet_hours_end"]),
        )

    def _kind_enabled(self, kind: str) -> bool:
        intensity = self._config["intensity"]
        if kind in {"user_returned", "test_failed", "build_failed"}:
            return True
        if kind in {"test_passed", "build_succeeded", "task_completed", "focus_milestone"}:
            return intensity in {"balanced", "active", "neuro_like"}
        if kind == "app_changed":
            return intensity == "neuro_like"
        return False

    def _offer(self, kind: str, facts: dict[str, Any], confidence: float, now: float) -> dict[str, Any] | None:
        if not self._kind_enabled(kind):
            return None
        if confidence < float(self._config["minimum_confidence"]):
            self._audit_event("suppressed", kind, "low_confidence", now=now)
            return None
        last_at = float(self._last_candidate_at.get(kind) or 0.0)
        cooldown = float(self._config["initiative_cooldown_minutes"]) * 60.0
        if last_at and now - last_at < cooldown:
            return None
        if self._in_quiet_hours(now):
            self._audit_event("suppressed", kind, "quiet_hours", now=now)
            self._last_candidate_at[kind] = now
            return None
        mode = self._config["mode"]
        if mode == "off":
            return None
        opportunity = {
            "id": self._id_factory(),
            "kind": kind,
            "facts": deepcopy(facts),
            "confidence": round(float(confidence), 3),
            "created_at": now,
            "status": "shadowed" if mode == "shadow" else "pending",
            "claimed_at": 0.0,
            "retry_after": 0.0,
            "generated_text": "",
            "session_id": "",
            "delivered_at": 0.0,
            "responded_at": 0.0,
            "context_consumed": False,
        }
        self._opportunities.append(opportunity)
        self._last_candidate_at[kind] = now
        self._audit_event("would_speak" if mode == "shadow" else "queued", kind, "", now=now)
        return deepcopy(opportunity)

    def ingest_event(self, payload: Any) -> dict[str, Any]:
        with self._lock:
            now = float(self._now())
            self._cleanup(now)
            if self._config["mode"] == "off" or not self._config["metadata_enabled"]:
                return self.status()
            source = payload if isinstance(payload, dict) else {}
            metadata = self.sanitize_metadata(source)
            previous = dict(self._presence)
            self._presence = {**metadata, "last_observed_at": now}
            event_kind = "privacy_blocked" if metadata["privacy_blocked"] else "desktop_state"
            activity = str(source.get("activity") or "").strip().lower()
            if activity not in SAFE_ACTIVITIES or not self._config["screen_context_enabled"]:
                activity = ""
            event = {
                "timestamp": now,
                "kind": event_kind,
                "foreground_app": metadata["foreground_app"],
                "window_title": metadata["window_title"],
                "idle_seconds": metadata["idle_seconds"],
                "activity": activity,
            }
            self._events.append(event)
            if metadata["privacy_blocked"]:
                self._focus_started_at = 0.0
                self._focus_candidate_emitted = False
                self._audit_event("privacy_blocked", reason="sensitive_context", now=now)
                return self.status()

            previous_idle = int(previous.get("idle_seconds") or 0)
            idle_seconds = int(metadata["idle_seconds"])
            away_threshold = int(self._config["away_threshold_minutes"]) * 60
            if previous_idle >= away_threshold and idle_seconds < 30:
                self._offer(
                    "user_returned",
                    {"away_minutes": max(1, round(previous_idle / 60)), "foreground_app": metadata["foreground_app"]},
                    0.96,
                    now,
                )

            app = metadata["foreground_app"]
            previous_app = str(previous.get("foreground_app") or "")
            if app and previous_app and app.casefold() != previous_app.casefold():
                self._offer("app_changed", {"foreground_app": app}, 0.78, now)

            if idle_seconds >= 60 or not app:
                self._focus_started_at = 0.0
                self._focus_candidate_emitted = False
            elif not self._focus_started_at or (previous_app and app.casefold() != previous_app.casefold()):
                self._focus_started_at = now
                self._focus_candidate_emitted = False
            focus_seconds = now - self._focus_started_at if self._focus_started_at else 0.0
            focus_threshold = int(self._config["focus_minutes"]) * 60
            if focus_seconds >= focus_threshold and not self._focus_candidate_emitted:
                self._focus_candidate_emitted = True
                self._offer(
                    "focus_milestone",
                    {"focus_minutes": int(focus_seconds // 60), "foreground_app": app},
                    0.82,
                    now,
                )

            if activity:
                confidence = _clamp_float(source.get("confidence"), 0.86, 0.0, 1.0)
                self._offer(activity, {"foreground_app": app}, confidence, now)
            return self.status()

    def ingest_screen_activity(self, text: Any, *, foreground_app: str = "", confidence: float = 0.86) -> str:
        activity = classify_screen_activity(text)
        if not activity:
            return ""
        with self._lock:
            now = float(self._now())
            self._cleanup(now)
            if self._config["mode"] == "off" or not self._config["screen_context_enabled"]:
                return ""
            metadata = self.sanitize_metadata({"foreground_app": foreground_app})
            if metadata["privacy_blocked"]:
                self._audit_event("privacy_blocked", reason="sensitive_screen_context", now=now)
                return ""
            app = metadata["foreground_app"] if self._config["metadata_enabled"] else ""
            self._events.append(
                {
                    "timestamp": now,
                    "kind": "screen_activity",
                    "foreground_app": app,
                    "window_title": "",
                    "idle_seconds": 0,
                    "activity": activity,
                }
            )
            normalized_confidence = _clamp_float(confidence, 0.86, 0.0, 1.0)
            self._offer(activity, {"foreground_app": app}, normalized_confidence, now)
            return activity

    def _date_key(self, now: float) -> str:
        return datetime.fromtimestamp(now).astimezone().date().isoformat()

    def claim_opportunity(
        self,
        *,
        session_id: str,
        client_busy: bool = False,
        memory_mode: str = "persistent",
    ) -> dict[str, Any] | None:
        with self._lock:
            now = float(self._now())
            self._cleanup(now)
            if (
                self._config["mode"] != "active"
                or client_busy
                or memory_mode != "persistent"
                or self._in_quiet_hours(now)
            ):
                return None
            date_key = self._date_key(now)
            if self._daily_deliveries.get(date_key, 0) >= int(self._config["daily_initiative_limit"]):
                return None
            global_cooldown = int(self._config["initiative_cooldown_minutes"]) * 60
            if self._last_delivery_at and now - self._last_delivery_at < global_cooldown:
                return None
            previous = self._last_delivered_by_session.get(str(session_id or "default"))
            unanswered_backoff = int(self._config["unanswered_backoff_minutes"]) * 60
            if (
                previous
                and previous.get("status") == "delivered"
                and not previous.get("responded_at")
                and now - float(previous.get("delivered_at") or 0.0) < unanswered_backoff
            ):
                return None
            for opportunity in self._opportunities:
                if opportunity.get("status") != "pending":
                    continue
                if float(opportunity.get("retry_after") or 0.0) > now:
                    continue
                opportunity["status"] = "claimed"
                opportunity["claimed_at"] = now
                opportunity["session_id"] = str(session_id or "default")[:80]
                return deepcopy(opportunity)
            return None

    def complete_generation(self, opportunity_id: str, text: str) -> dict[str, Any] | None:
        with self._lock:
            message = _clean_text(text, MAX_PROACTIVE_MESSAGE_LENGTH)
            for opportunity in self._opportunities:
                if opportunity.get("id") != opportunity_id or opportunity.get("status") != "claimed":
                    continue
                if not message:
                    opportunity["status"] = "dismissed"
                    self._audit_event("suppressed", str(opportunity.get("kind") or ""), "empty_generation")
                    return None
                opportunity["generated_text"] = message
                opportunity["status"] = "awaiting_delivery"
                return deepcopy(opportunity)
            return None

    def release_claim(self, opportunity_id: str, reason: str = "generation_failed") -> None:
        with self._lock:
            now = float(self._now())
            for opportunity in self._opportunities:
                if opportunity.get("id") != opportunity_id or opportunity.get("status") != "claimed":
                    continue
                opportunity["status"] = "pending"
                opportunity["claimed_at"] = 0.0
                opportunity["retry_after"] = now + max(60, int(self._config["poll_interval_sec"]) * 4)
                self._audit_event("generation_failed", str(opportunity.get("kind") or ""), reason, now=now)
                break

    def feedback(self, opportunity_id: str, outcome: str, *, session_id: str = "") -> bool:
        normalized = str(outcome or "").strip().lower()
        if normalized not in {"delivered", "deferred", "dismissed"}:
            return False
        with self._lock:
            now = float(self._now())
            for opportunity in self._opportunities:
                if opportunity.get("id") != opportunity_id:
                    continue
                kind = str(opportunity.get("kind") or "")
                if normalized == "delivered" and opportunity.get("status") == "awaiting_delivery":
                    opportunity["status"] = "delivered"
                    opportunity["delivered_at"] = now
                    opportunity["session_id"] = str(session_id or opportunity.get("session_id") or "default")[:80]
                    self._last_delivery_at = now
                    date_key = self._date_key(now)
                    self._daily_deliveries[date_key] = self._daily_deliveries.get(date_key, 0) + 1
                    self._last_delivered_by_session[opportunity["session_id"]] = opportunity
                    self._audit_event("delivered", kind, now=now)
                    return True
                if normalized == "deferred" and opportunity.get("status") in {"claimed", "awaiting_delivery"}:
                    opportunity["status"] = "pending"
                    opportunity["retry_after"] = now + max(60, int(self._config["poll_interval_sec"]) * 2)
                    self._audit_event("deferred", kind, "client_became_busy", now=now)
                    return True
                if normalized == "dismissed":
                    opportunity["status"] = "dismissed"
                    self._audit_event("dismissed", kind, now=now)
                    return True
            return False

    def consume_user_reply_context(self, session_id: str) -> str:
        with self._lock:
            opportunity = self._last_delivered_by_session.get(str(session_id or "default"))
            if not opportunity or opportunity.get("context_consumed"):
                return ""
            now = float(self._now())
            if now - float(opportunity.get("delivered_at") or 0.0) > int(self._config["event_ttl_sec"]):
                opportunity["context_consumed"] = True
                return ""
            opportunity["responded_at"] = now
            opportunity["context_consumed"] = True
            self._audit_event("user_responded", str(opportunity.get("kind") or ""), now=now)
            message = _clean_text(opportunity.get("generated_text"), 240)
            if not message:
                return ""
            return (
                "[本轮主动陪伴上下文]\n"
                f"你刚才主动对用户说过：{message}\n"
                "用户当前消息可能是在回应这句话；自然衔接即可，不要声称看到了更多环境细节。"
            )

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = float(self._now())
            self._cleanup(now)
            pending_count = sum(
                1
                for item in self._opportunities
                if item.get("status") in {"pending", "claimed", "awaiting_delivery"}
            )
            presence = dict(self._presence)
            if presence.get("privacy_blocked"):
                presence["foreground_app"] = ""
                presence["window_title"] = ""
            elif not self._config["include_window_titles"]:
                presence["window_title"] = ""
            return {
                "mode": self._config["mode"],
                "intensity": self._config["intensity"],
                "metadata_enabled": self._config["metadata_enabled"],
                "screen_context_enabled": self._config["screen_context_enabled"],
                "include_window_titles": self._config["include_window_titles"],
                "speak_enabled": self._config["speak_enabled"],
                "open_chat_on_speak": self._config["open_chat_on_speak"],
                "poll_interval_sec": self._config["poll_interval_sec"],
                "pending_count": pending_count,
                "presence": presence,
                "recent_audit": [dict(item) for item in list(self._audit)[-20:]],
            }

    def clear(self) -> dict[str, Any]:
        with self._lock:
            self._events.clear()
            self._opportunities.clear()
            self._audit.clear()
            self._presence = self._empty_presence()
            self._focus_started_at = 0.0
            self._focus_candidate_emitted = False
            self._last_candidate_at.clear()
            self._last_delivery_at = 0.0
            self._daily_deliveries.clear()
            self._last_delivered_by_session.clear()
            return self.status()


__all__ = [
    "DEFAULT_ENVIRONMENT_CONFIG",
    "EnvironmentService",
    "classify_screen_activity",
    "is_quiet_hour",
    "normalize_environment_config",
]
