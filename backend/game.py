from __future__ import annotations

import time
from collections import deque
from copy import deepcopy
from typing import Any, Callable
from uuid import uuid4


GAME_ID = "slay_the_spire_2"
HEARTBEAT_TIMEOUT_SEC = 12.0
NORMAL_COALESCE_SEC = 0.75
NORMAL_EVENT_TTL_SEC = 12.0
IMPORTANT_EVENT_TTL_SEC = 30.0
DELIVERY_FEEDBACK_TTL_SEC = 30.0
MAX_GAME_EVENTS = 120
MAX_GAME_OPPORTUNITIES = 48
MAX_GAME_AUDIT = 120
MAX_REACTION_TEXT_LENGTH = 80

GAME_CATEGORIES = ("combat", "growth", "route", "resources", "outcome")
DEFAULT_GAME_CONFIG: dict[str, Any] = {
    "enabled": True,
    "adapter": GAME_ID,
    "min_reaction_interval_sec": 5,
    "max_reactions_per_minute": 6,
    "reaction_instruction": "",
    "categories": {category: True for category in GAME_CATEGORIES},
}

EVENT_DEFINITIONS: dict[str, tuple[str, str]] = {
    "combat_started": ("combat", "normal"),
    "turn_started": ("combat", "context"),
    "large_damage_taken": ("combat", "normal"),
    "low_hp_entered": ("combat", "important"),
    "enemy_defeated": ("combat", "normal"),
    "combat_won": ("combat", "normal"),
    "combat_lost": ("combat", "important"),
    "card_reward_opened": ("growth", "context"),
    "card_selected": ("growth", "normal"),
    "card_skipped": ("growth", "normal"),
    "relic_obtained": ("growth", "normal"),
    "potion_obtained": ("growth", "normal"),
    "card_upgraded": ("growth", "normal"),
    "card_removed": ("growth", "normal"),
    "run_started": ("route", "normal"),
    "act_started": ("route", "normal"),
    "room_entered": ("route", "context"),
    "elite_entered": ("route", "normal"),
    "boss_entered": ("route", "normal"),
    "event_choice_made": ("route", "normal"),
    "rest_site_choice": ("route", "normal"),
    "shop_entered": ("resources", "context"),
    "shop_purchase": ("resources", "normal"),
    "potion_used": ("resources", "normal"),
    "heal_received": ("resources", "normal"),
    "run_won": ("outcome", "important"),
    "run_lost": ("outcome", "important"),
    "run_abandoned": ("outcome", "important"),
}


def _clamp_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def normalize_game_config(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    raw_categories = source.get("categories") if isinstance(source.get("categories"), dict) else {}
    return {
        "enabled": source.get("enabled", True) is not False,
        "adapter": GAME_ID,
        "min_reaction_interval_sec": _clamp_int(
            source.get("min_reaction_interval_sec"), 5, 0, 60
        ),
        "max_reactions_per_minute": _clamp_int(
            source.get("max_reactions_per_minute"), 6, 1, 30
        ),
        "reaction_instruction": _clean_text(source.get("reaction_instruction"), 500),
        "categories": {
            category: raw_categories.get(category, True) is not False
            for category in GAME_CATEGORIES
        },
    }


class GameService:
    """Bounded, process-local state for a read-only game companion session."""

    def __init__(
        self,
        config: Any | None = None,
        *,
        now: Callable[[], float] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._now = now or time.time
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._config = normalize_game_config(config)
        self._session: dict[str, Any] = {}
        self._last_sequence = 0
        self._low_hp_latched = False
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_GAME_EVENTS)
        self._opportunities: deque[dict[str, Any]] = deque(maxlen=MAX_GAME_OPPORTUNITIES)
        self._audit: deque[dict[str, Any]] = deque(maxlen=MAX_GAME_AUDIT)
        self._delivery_times: deque[float] = deque(maxlen=60)
        self._last_delivery_at = 0.0

        # FastAPI runs these methods on one event loop in normal operation. The
        # service deliberately has no background thread and therefore needs no
        # second scheduler or persistent store.

    @property
    def config(self) -> dict[str, Any]:
        return deepcopy(self._config)

    def configure(self, value: Any) -> None:
        next_config = normalize_game_config(value)
        was_enabled = bool(self._config.get("enabled"))
        self._config = next_config
        if was_enabled and not next_config["enabled"]:
            self._discard_pending("disabled")
            if self._session:
                self._session["paused"] = False

    def _audit_event(self, action: str, *, kind: str = "", reason: str = "", now: float | None = None) -> None:
        self._audit.append(
            {
                "timestamp": float(self._now() if now is None else now),
                "action": _clean_text(action, 32),
                "kind": _clean_text(kind, 48),
                "reason": _clean_text(reason, 120),
            }
        )

    def _discard_pending(self, reason: str) -> None:
        for item in self._opportunities:
            if item.get("status") in {"pending", "claimed", "awaiting_delivery"}:
                item["status"] = reason

    def _disconnect_if_stale(self, now: float) -> None:
        if not self._session:
            return
        last_seen = float(self._session.get("last_seen_at") or 0.0)
        if now - last_seen < HEARTBEAT_TIMEOUT_SEC:
            return
        previous_id = str(self._session.get("session_id") or "")
        self._discard_pending("disconnected")
        self._session = {}
        self._last_sequence = 0
        self._low_hp_latched = False
        self._audit_event("disconnected", reason=previous_id, now=now)

    def _cleanup(self, now: float) -> None:
        self._disconnect_if_stale(now)
        for item in self._opportunities:
            if item.get("status") not in {"pending", "claimed", "awaiting_delivery"}:
                continue
            expires_at = float(item.get("expires_at") or 0.0)
            if expires_at and now >= expires_at:
                item["status"] = "expired"
        while self._delivery_times and now - self._delivery_times[0] >= 60.0:
            self._delivery_times.popleft()

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = float(self._now())
        self._cleanup(now)
        session_id = str(payload["session_id"])
        run_id = str(payload.get("run_id") or "")
        previous_session_id = str(self._session.get("session_id") or "")
        previous_run_id = str(self._session.get("run_id") or "")
        same_run = previous_session_id == session_id and previous_run_id == run_id
        previous_last_event = deepcopy(self._session.get("last_event") or {}) if same_run else {}
        previous_latest_snapshot = deepcopy(self._session.get("latest_snapshot") or {}) if same_run else {}
        session_changed = bool(previous_session_id and previous_session_id != session_id)
        run_ended = bool(
            previous_session_id == session_id
            and previous_run_id
            and not run_id
        )
        # The bridge connects before a Run exists.  The first non-empty run ID
        # is still a new Run and must clear a pause made while sitting at the
        # main menu.
        run_changed = bool(
            previous_session_id == session_id
            and run_id
            and previous_run_id != run_id
        )
        if not previous_session_id or session_changed or run_changed:
            self._discard_pending("session_changed")
            self._last_sequence = 0
            self._low_hp_latched = False
            self._delivery_times.clear()
            self._last_delivery_at = 0.0
            self._audit_event(
                "connected" if not previous_session_id or session_changed else "run_changed",
                reason=session_id,
                now=now,
            )
        elif run_ended:
            for item in self._opportunities:
                if item.get("status") in {"pending", "claimed", "awaiting_delivery"} and item.get(
                    "category"
                ) != "outcome":
                    item["status"] = "run_ended"
            self._audit_event("run_ended", reason=previous_run_id, now=now)
        compatible = payload.get("compatible", True) is not False
        local_player_identified = payload.get("local_player_identified", True) is not False
        paused = (
            False
            if not previous_session_id or session_changed or run_changed or run_ended
            else bool(self._session.get("paused"))
        )
        self._session = {
            "session_id": session_id,
            "run_id": run_id,
            "game_version": str(payload.get("game_version") or ""),
            "adapter_version": str(payload.get("adapter_version") or ""),
            "compatible": compatible,
            "local_player_identified": local_player_identified,
            "bridge_sequence": int(payload.get("event_sequence") or 0),
            "last_seen_at": now,
            "connected_at": float(self._session.get("connected_at") or now),
            "paused": paused,
            "run_summary": deepcopy(payload.get("run_summary") or {}),
        }
        if previous_last_event:
            self._session["last_event"] = previous_last_event
        if previous_latest_snapshot:
            self._session["latest_snapshot"] = previous_latest_snapshot
        if not compatible:
            self._discard_pending("incompatible")
        elif not local_player_identified:
            self._discard_pending("local_player_unknown")
        return self.status()

    def _session_accepts_events(self, payload: dict[str, Any]) -> tuple[bool, str]:
        if not self._config["enabled"]:
            return False, "disabled"
        if not self._session:
            return False, "heartbeat_required"
        if str(payload.get("session_id") or "") != str(self._session.get("session_id") or ""):
            return False, "session_mismatch"
        if str(payload.get("run_id") or "") != str(self._session.get("run_id") or ""):
            return False, "run_mismatch"
        if self._session.get("paused"):
            return False, "paused"
        if not self._session.get("compatible"):
            return False, "incompatible"
        if not self._session.get("local_player_identified"):
            return False, "local_player_unknown"
        return True, ""

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _passes_threshold(self, event: dict[str, Any]) -> bool:
        kind = str(event.get("kind") or "")
        facts = event.get("facts") if isinstance(event.get("facts"), dict) else {}
        snapshot = event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {}
        player = snapshot.get("player") if isinstance(snapshot.get("player"), dict) else {}
        max_hp = max(self._number(facts.get("max_hp")), self._number(player.get("max_hp")))
        hp = self._number(facts.get("hp")) if "hp" in facts else self._number(player.get("hp"))
        if max_hp > 0 and hp / max_hp > 0.35:
            self._low_hp_latched = False
        if kind == "large_damage_taken":
            return self._number(facts.get("damage")) >= max(10.0, max_hp * 0.2)
        if kind == "low_hp_entered":
            if max_hp <= 0 or hp / max_hp > 0.25 or self._low_hp_latched:
                return False
            self._low_hp_latched = True
        if kind == "heal_received":
            return max_hp > 0 and self._number(facts.get("healed")) >= max_hp * 0.2
        return True

    def _queue_opportunity(self, event: dict[str, Any], category: str, priority: str, now: float) -> None:
        ttl = IMPORTANT_EVENT_TTL_SEC if priority == "important" else NORMAL_EVENT_TTL_SEC
        opportunity = {
            "id": self._id_factory(),
            "session_id": str(self._session.get("session_id") or ""),
            "run_id": str(self._session.get("run_id") or ""),
            "kind": str(event.get("kind") or ""),
            "category": category,
            "priority": priority,
            "facts": deepcopy(event.get("facts") or {}),
            "snapshot": deepcopy(event.get("snapshot") or {}),
            "created_at": now,
            "ready_at": now if priority == "important" else now + NORMAL_COALESCE_SEC,
            "expires_at": now + ttl,
            "status": "pending",
            "generated_text": "",
        }
        if priority == "important":
            for item in self._opportunities:
                if item.get("status") in {"pending", "claimed", "awaiting_delivery"} and item.get(
                    "priority"
                ) != "important":
                    item["status"] = "replaced"
            self._opportunities.append(opportunity)
            return
        for item in reversed(self._opportunities):
            if item.get("status") not in {"pending", "claimed", "awaiting_delivery"} or item.get(
                "priority"
            ) == "important":
                continue
            if item.get("status") != "pending" or now - float(item.get("created_at") or 0.0) <= NORMAL_COALESCE_SEC:
                item["status"] = "replaced"
            break
        self._opportunities.append(opportunity)

    def ingest_events(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = float(self._now())
        self._cleanup(now)
        accepted_session, reason = self._session_accepts_events(payload)
        if not accepted_session:
            first_kind = str(next(iter(payload.get("events") or []), {}).get("kind") or "")
            self._audit_event("ignored", kind=first_kind, reason=reason, now=now)
            return {"ok": False, "accepted": 0, "ignored": len(payload.get("events") or []), "reason": reason}
        accepted = 0
        ignored = 0
        for event in payload.get("events") or []:
            sequence = int(event.get("sequence") or 0)
            if sequence <= self._last_sequence:
                ignored += 1
                self._audit_event("ignored", kind=str(event.get("kind") or ""), reason="duplicate_or_out_of_order", now=now)
                continue
            occurred_at = float(event.get("occurred_at_ms") or 0) / 1000.0
            kind = str(event.get("kind") or "")
            if kind not in EVENT_DEFINITIONS:
                ignored += 1
                self._audit_event("ignored", kind=kind, reason="unknown_event_kind", now=now)
                continue
            category, priority = EVENT_DEFINITIONS[kind]
            ttl = IMPORTANT_EVENT_TTL_SEC if priority == "important" else NORMAL_EVENT_TTL_SEC
            if occurred_at <= 0 or occurred_at > now + 5.0 or now - occurred_at > ttl:
                ignored += 1
                self._audit_event("ignored", kind=kind, reason="stale_timestamp", now=now)
                continue
            self._last_sequence = sequence
            event_record = {
                **deepcopy(event),
                "category": category,
                "priority": priority,
                "received_at": now,
            }
            self._events.append(event_record)
            self._session["last_event"] = {"kind": kind, "category": category, "timestamp": occurred_at}
            if isinstance(event.get("snapshot"), dict) and event["snapshot"]:
                self._session["latest_snapshot"] = deepcopy(event["snapshot"])
            passes_threshold = self._passes_threshold(event)
            if priority == "context":
                self._audit_event("context", kind=kind, now=now)
                accepted += 1
                continue
            if not passes_threshold:
                self._audit_event("context", kind=kind, reason="below_threshold", now=now)
                accepted += 1
                continue
            if not self._config["categories"].get(category, True):
                self._audit_event("suppressed", kind=kind, reason="category_disabled", now=now)
                accepted += 1
                continue
            self._queue_opportunity(event, category, priority, now)
            self._audit_event("queued", kind=kind, reason=priority, now=now)
            accepted += 1
        return {"ok": True, "accepted": accepted, "ignored": ignored, "last_sequence": self._last_sequence}

    def claim_opportunity(self, *, client_state: dict[str, Any] | None = None) -> dict[str, Any] | None:
        now = float(self._now())
        self._cleanup(now)
        if not self._config["enabled"] or not self._session or self._session.get("paused"):
            return None
        if not self._session.get("compatible") or not self._session.get("local_player_identified"):
            return None
        state = client_state if isinstance(client_state, dict) else {}
        hard_busy = bool(state.get("chat_busy") or state.get("asr_busy") or state.get("document_hidden"))
        speech_busy = bool(state.get("speech_busy"))
        if hard_busy:
            return None
        if len(self._delivery_times) >= int(self._config["max_reactions_per_minute"]):
            return None
        candidates = [
            item
            for item in self._opportunities
            if item.get("status") == "pending" and float(item.get("ready_at") or 0.0) <= now
            and (str(self._session.get("run_id") or "") or item.get("category") == "outcome")
        ]
        if not candidates:
            return None
        important = [item for item in candidates if item.get("priority") == "important"]
        candidate = important[0] if important else candidates[-1]
        if speech_busy and candidate.get("priority") != "important":
            return None
        if candidate.get("priority") != "important" and now - self._last_delivery_at < float(
            self._config["min_reaction_interval_sec"]
        ):
            return None
        candidate["status"] = "claimed"
        candidate["claimed_at"] = now
        return deepcopy(candidate)

    def complete_generation(self, opportunity_id: str, text: str) -> dict[str, Any] | None:
        now = float(self._now())
        self._cleanup(now)
        for item in self._opportunities:
            if item.get("id") != opportunity_id or item.get("status") != "claimed":
                continue
            clean = _clean_text(text, MAX_REACTION_TEXT_LENGTH)
            if not clean or now >= float(item.get("expires_at") or 0.0):
                item["status"] = "expired" if clean else "dismissed"
                return None
            item["generated_text"] = clean
            item["status"] = "awaiting_delivery"
            # Start the spacing window when the reaction is handed to the
            # speech pipeline. Measuring from playback completion adds the
            # whole utterance duration to the configured interval.
            self._last_delivery_at = now
            # The reaction was generated while the opportunity was fresh.
            # Give local synthesis and playback their own bounded confirmation
            # window instead of expiring an utterance that is already playing.
            item["expires_at"] = now + DELIVERY_FEEDBACK_TTL_SEC
            return deepcopy(item)
        return None

    def generation_failed(self, opportunity_id: str, reason: str) -> None:
        for item in self._opportunities:
            if item.get("id") == opportunity_id and item.get("status") == "claimed":
                item["status"] = "generation_failed"
                self._audit_event("generation_failed", kind=str(item.get("kind") or ""), reason=reason)
                return

    def feedback(self, opportunity_id: str, outcome: str) -> bool:
        allowed = {"delivered", "expired", "replaced", "failed", "dismissed"}
        normalized = str(outcome or "").strip().lower()
        if normalized not in allowed:
            return False
        now = float(self._now())
        self._cleanup(now)
        for item in self._opportunities:
            if item.get("id") != opportunity_id or item.get("status") not in {"claimed", "awaiting_delivery"}:
                continue
            item["status"] = normalized
            if normalized == "delivered":
                self._delivery_times.append(now)
            self._audit_event(normalized, kind=str(item.get("kind") or ""), now=now)
            return True
        return False

    def pause(self, paused: bool, *, session_id: str = "") -> dict[str, Any]:
        now = float(self._now())
        self._cleanup(now)
        if not self._session:
            return {"ok": False, "reason": "not_connected", **self.status()}
        if session_id and session_id != str(self._session.get("session_id") or ""):
            return {"ok": False, "reason": "session_mismatch", **self.status()}
        self._session["paused"] = bool(paused)
        if paused:
            self._discard_pending("paused")
        self._audit_event("paused" if paused else "resumed", now=now)
        return {"ok": True, **self.status()}

    def clear(self) -> dict[str, Any]:
        self._events.clear()
        self._opportunities.clear()
        self._audit.clear()
        self._delivery_times.clear()
        self._last_delivery_at = 0.0
        if self._session:
            self._session.pop("last_event", None)
            self._session.pop("latest_snapshot", None)
        return {"ok": True, **self.status()}

    def status(self) -> dict[str, Any]:
        now = float(self._now())
        self._cleanup(now)
        connected = bool(self._session)
        if not self._config["enabled"]:
            state = "disabled"
        elif not connected:
            state = "waiting"
        elif not self._session.get("compatible") or not self._session.get("local_player_identified"):
            state = "incompatible"
        elif not str(self._session.get("run_id") or ""):
            state = "connected"
        elif self._session.get("paused"):
            state = "paused"
        else:
            state = "active"
        active_pending = sum(
            1 for item in self._opportunities if item.get("status") in {"pending", "claimed", "awaiting_delivery"}
        )
        return {
            "enabled": bool(self._config["enabled"]),
            "game": GAME_ID,
            "state": state,
            "connected": connected,
            "session_id": str(self._session.get("session_id") or ""),
            "run_id": str(self._session.get("run_id") or ""),
            "game_version": str(self._session.get("game_version") or ""),
            "adapter_version": str(self._session.get("adapter_version") or ""),
            "compatible": bool(self._session.get("compatible", True)) if connected else True,
            "local_player_identified": bool(self._session.get("local_player_identified", True)) if connected else True,
            "paused": bool(self._session.get("paused")) if connected else False,
            "last_seen_at": float(self._session.get("last_seen_at") or 0.0),
            "bridge_sequence": int(self._session.get("bridge_sequence") or 0),
            "accepted_sequence": int(self._last_sequence),
            "run_summary": deepcopy(self._session.get("run_summary") or {}),
            "last_event": deepcopy(self._session.get("last_event") or {}),
            "pending_count": active_pending,
            "recent_audit": list(deepcopy(self._audit))[-20:],
        }


__all__ = [
    "DEFAULT_GAME_CONFIG",
    "EVENT_DEFINITIONS",
    "GAME_CATEGORIES",
    "GAME_ID",
    "GameService",
    "HEARTBEAT_TIMEOUT_SEC",
    "MAX_REACTION_TEXT_LENGTH",
    "normalize_game_config",
]
