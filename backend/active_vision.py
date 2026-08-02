from __future__ import annotations

import json
import hashlib
import re
from typing import Any, Callable

from .vision import normalize_vision_config


DEFAULT_ACTIVE_OBSERVATION_CONFIG: dict[str, Any] = {
    "enabled": True,
    "allowed_interaction": "light",
    "timeout_sec": 15.0,
    "settle_ms": 500,
}

ALLOWED_INTERACTION_LEVELS = {"light", "none"}
ALLOWED_LIGHT_ACTIONS = {"focus_target"}
DEFAULT_CLICK_POLICY = "window_focus_only"
ACTIVE_SURVEY_TARGET_ID = "desktop_survey"
MAX_TARGET_CANDIDATES = 12
MAX_DISCOVERY_ERRORS = 6
SELF_OCCLUDED_APP_MARKERS = (
    "ipet",
    "desktoppet",
    "desktop pet",
    "codex",
    "python3",
    "python",
)
SELF_OCCLUDED_TITLE_MARKERS = (
    "桌宠",
    "astrbot/ipet chat",
    "ipet chat",
    "desktop pet",
    "codex",
)
UNRELATED_WINDOW_MARKERS = (
    "blank",
    "empty",
    "black screen",
    "white screen",
    "error window",
    "permission",
    "denied",
    "unrelated",
    "not relevant",
    "无关",
    "空白",
    "错误窗口",
    "权限",
    "拒绝",
)


def _clean_text(value: Any, *, max_length: int = 160) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = " ".join(text.split())
    return text[:max_length]


def _match_tokens(value: Any) -> list[str]:
    text = _clean_text(value, max_length=500).lower()
    return [token for token in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", text) if len(token) >= 2]


def _clamp_float(value: Any, *, fallback: float, min_value: float, max_value: float) -> float:
    try:
        number = float(value)
    except Exception:
        number = fallback
    return max(min_value, min(max_value, number))


def _clamp_int(value: Any, *, fallback: int, min_value: int, max_value: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = fallback
    return max(min_value, min(max_value, number))


def normalize_active_observation_config(config: Any) -> dict[str, Any]:
    source = config if isinstance(config, dict) else {}
    allowed_interaction = _clean_text(
        source.get("allowed_interaction") or DEFAULT_ACTIVE_OBSERVATION_CONFIG["allowed_interaction"],
        max_length=40,
    )
    if allowed_interaction not in ALLOWED_INTERACTION_LEVELS:
        allowed_interaction = "light"
    return {
        "enabled": bool(source.get("enabled", DEFAULT_ACTIVE_OBSERVATION_CONFIG["enabled"])),
        "allowed_interaction": allowed_interaction,
        "timeout_sec": _clamp_float(
            source.get("timeout_sec", DEFAULT_ACTIVE_OBSERVATION_CONFIG["timeout_sec"]),
            fallback=DEFAULT_ACTIVE_OBSERVATION_CONFIG["timeout_sec"],
            min_value=1.0,
            max_value=20.0,
        ),
        "settle_ms": _clamp_int(
            source.get("settle_ms", DEFAULT_ACTIVE_OBSERVATION_CONFIG["settle_ms"]),
            fallback=DEFAULT_ACTIVE_OBSERVATION_CONFIG["settle_ms"],
            min_value=100,
            max_value=2000,
        ),
    }


def _sanitize_actions(value: Any, *, allow_default: bool = False) -> list[str]:
    if not isinstance(value, list):
        return ["focus_target"] if allow_default else []
    actions: list[str] = []
    for item in value:
        action = _clean_text(item, max_length=40)
        if action in ALLOWED_LIGHT_ACTIONS and action not in actions:
            actions.append(action)
    if allow_default and not actions:
        actions.append("focus_target")
    return actions


def _infer_target_hint(text: str, target_hint: Any = "") -> str:
    supplied = _clean_text(target_hint, max_length=80)
    if supplied:
        return supplied
    return ACTIVE_SURVEY_TARGET_ID


def _coerce_decider_output(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("invalid decision JSON")


def _normalize_seen_entry(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            "target_id": _clean_text(value.get("target_id"), max_length=120),
            "mode": _clean_text(value.get("mode"), max_length=40),
            "target_hint": _clean_text(value.get("target_hint"), max_length=80),
            "foreground_app": _clean_text(value.get("foreground_app"), max_length=120),
            "window_title": _clean_text(value.get("window_title"), max_length=200),
            "frame_hash": _clean_text(value.get("frame_hash"), max_length=120),
        }
    text = _clean_text(value, max_length=120)
    return {"target_id": text, "mode": "", "target_hint": text, "foreground_app": "", "window_title": "", "frame_hash": ""}


def _normalize_exclude_seen(value: Any) -> list[dict[str, str]]:
    raw = value if isinstance(value, list) else []
    entries: list[dict[str, str]] = []
    for item in raw[:10]:
        entry = _normalize_seen_entry(item)
        if any(entry.values()):
            entries.append(entry)
    return entries


def _desktop_context(payload: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    context = payload.get("desktop_context") if isinstance(payload.get("desktop_context"), dict) else {}
    return {
        "foreground_app": context.get("foreground_app") or payload.get("foreground_app") or trace.get("foreground_app") or "",
        "frontmost_process": context.get("frontmost_process") or payload.get("frontmost_process") or trace.get("frontmost_process") or "",
        "window_title": context.get("window_title") or payload.get("window_title") or trace.get("window_title") or "",
    }


def _sanitize_bounds(value: Any) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    return {
        "x": _clamp_int(source.get("x"), fallback=0, min_value=-100000, max_value=100000),
        "y": _clamp_int(source.get("y"), fallback=0, min_value=-100000, max_value=100000),
        "width": _clamp_int(source.get("width"), fallback=0, min_value=0, max_value=100000),
        "height": _clamp_int(source.get("height"), fallback=0, min_value=0, max_value=100000),
    }


def _sanitize_point(value: Any) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    return {
        "x": _clamp_int(source.get("x"), fallback=0, min_value=-100000, max_value=100000),
        "y": _clamp_int(source.get("y"), fallback=0, min_value=-100000, max_value=100000),
    }


def _sanitize_discovery_errors(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else [value] if value not in (None, "") else []
    errors: list[str] = []
    for item in raw[:MAX_DISCOVERY_ERRORS]:
        text = _clean_text(item, max_length=180)
        if text and text not in errors:
            errors.append(text)
    return errors


def _sanitize_desktop_targets(value: Any) -> list[dict[str, Any]]:
    raw = value if isinstance(value, list) else []
    targets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        target_id = _clean_text(item.get("target_id"), max_length=120)
        if not target_id or target_id in seen_ids:
            continue
        app = _clean_text(item.get("app"), max_length=120)
        title = _clean_text(item.get("title"), max_length=200)
        if _is_self_occluded(app, title, {}):
            continue
        target = {
            "target_id": target_id,
            "app": app,
            "title": title,
            "bounds": _sanitize_bounds(item.get("bounds")),
            "frontmost": bool(item.get("frontmost", False)),
            "minimized": bool(item.get("minimized", False)),
            "focus_point": _sanitize_point(item.get("focus_point")),
        }
        if target["minimized"]:
            continue
        seen_ids.add(target_id)
        targets.append(target)
        if len(targets) >= 12:
            break
    return targets


def _candidate_default_score(source: str, *, focusable: bool, frontmost: bool) -> float:
    base_scores = {
        "window_enumeration": 80.0,
        "running_app": 60.0,
        "desktop_context": 45.0,
        "passive_timeline": 25.0,
        "screenshot_region": 15.0,
    }
    score = base_scores.get(source, 30.0)
    if focusable:
        score += 10.0
    if frontmost:
        score += 5.0
    return score


def _target_candidate_id(source: str, app: str, title: str, index: int) -> str:
    if source == "screenshot_region":
        return "screenshot:full_desktop"
    seed = "\x00".join([source, app, title, str(index)])
    return f"{source or 'candidate'}:{hashlib.sha1(seed.encode('utf-8', errors='ignore')).hexdigest()[:12]}"


def _sanitize_target_candidate(value: Any, *, index: int = 0, source: str = "") -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    candidate_source = _clean_text(item.get("source") or source or "unknown", max_length=40)
    app = _clean_text(item.get("app"), max_length=120)
    title = _clean_text(item.get("title") or item.get("window_title"), max_length=200)
    if candidate_source not in {"screenshot_region", "passive_timeline"} and _is_self_occluded(app, title, {}):
        return {}
    target_id = _clean_text(item.get("target_id") or item.get("candidate_id"), max_length=120)
    if not target_id:
        target_id = _target_candidate_id(candidate_source, app, title, index)
    bounds = _sanitize_bounds(item.get("bounds"))
    focus_point = _sanitize_point(item.get("focus_point"))
    frontmost = bool(item.get("frontmost", False))
    minimized = bool(item.get("minimized", False))
    if minimized:
        return {}
    focusable = bool(item.get("focusable", candidate_source in {"window_enumeration", "running_app", "desktop_context"}))
    if candidate_source == "screenshot_region":
        focusable = False
    try:
        score = float(item.get("score"))
    except Exception:
        score = _candidate_default_score(candidate_source, focusable=focusable, frontmost=frontmost)
    candidate = {
        "target_id": target_id,
        "source": candidate_source,
        "app": app,
        "title": title,
        "bounds": bounds,
        "frontmost": frontmost,
        "minimized": minimized,
        "focus_point": focus_point,
        "focusable": focusable,
        "score": round(max(0.0, min(100.0, score)), 3),
    }
    for key in ("bundle_id", "pid", "reason"):
        text = _clean_text(item.get(key), max_length=160)
        if text:
            candidate[key] = text
    return candidate


def _candidate_from_desktop_target(target: dict[str, Any], *, index: int = 0) -> dict[str, Any]:
    if not isinstance(target, dict):
        return {}
    return _sanitize_target_candidate(
        {
            **target,
            "source": target.get("source") or "window_enumeration",
            "focusable": not bool(target.get("minimized", False)),
        },
        index=index,
        source="window_enumeration",
    )


def _candidate_text_match_score(query_text: str, candidate: dict[str, Any]) -> float:
    query = _clean_text(query_text, max_length=500).lower()
    if not query:
        return 0.0
    fields = [
        _clean_text(candidate.get("app"), max_length=120),
        _clean_text(candidate.get("title"), max_length=200),
        _clean_text(candidate.get("source"), max_length=40),
        _clean_text(candidate.get("reason"), max_length=160),
    ]
    field_values = [field.lower() for field in fields if field]
    haystack = " ".join(field_values)
    if not haystack:
        return 0.0
    score = 0.0
    for field in field_values:
        if len(field) >= 2 and field in query:
            score = max(score, 30.0)
        if len(query) >= 2 and query in field:
            score = max(score, 30.0)
    token_hits = sum(1 for token in _match_tokens(query) if token in haystack)
    if token_hits:
        score += min(16.0, token_hits * 8.0)
    return min(score, 40.0)


def _same_desktop_text(left: str, right: str, *, min_substring_length: int = 4) -> bool:
    first = _clean_text(left, max_length=200).lower()
    second = _clean_text(right, max_length=200).lower()
    if not first or not second:
        return False
    if first == second:
        return True
    if min(len(first), len(second)) < min_substring_length:
        return False
    return first in second or second in first


def _candidate_is_current_desktop(candidate: dict[str, Any], foreground_app: str, window_title: str) -> bool:
    if bool(candidate.get("frontmost")):
        return True
    app = _clean_text(candidate.get("app"), max_length=120)
    title = _clean_text(candidate.get("title"), max_length=200)
    return _same_desktop_text(app, foreground_app) or _same_desktop_text(title, window_title, min_substring_length=6)


def _passive_desktop_context_candidate(passive_context: dict[str, Any]) -> dict[str, Any]:
    desktop_context = passive_context.get("desktop_context") if isinstance(passive_context.get("desktop_context"), dict) else {}
    app = _clean_text(desktop_context.get("foreground_app") or desktop_context.get("frontmost_process"), max_length=120)
    title = _clean_text(desktop_context.get("window_title") or app, max_length=200)
    if not app:
        return {}
    return {
        "source": "desktop_context",
        "app": app,
        "title": title,
        "focusable": True,
        "score": 55,
        "reason": "passive desktop context",
    }


def _target_candidates_from_sources(
    frame: dict[str, Any],
    trace: dict[str, Any],
    *,
    desktop_targets: list[dict[str, Any]],
    query_text: str = "",
    foreground_app: str = "",
    window_title: str = "",
    passive_context: Any | None = None,
) -> list[dict[str, Any]]:
    active = frame.get("active_observation") if isinstance(frame.get("active_observation"), dict) else {}
    raw_candidates: list[Any] = []
    has_explicit_candidates = False
    for source in (
        active.get("target_candidates"),
        trace.get("target_candidates"),
        frame.get("target_candidates"),
    ):
        if isinstance(source, list):
            has_explicit_candidates = has_explicit_candidates or bool(source)
            raw_candidates.extend(source)
    for index, item in enumerate(desktop_targets):
        raw_candidates.append(_candidate_from_desktop_target(item, index=index))
    if frame and not desktop_targets and not has_explicit_candidates:
        raw_candidates.append(
            {
                "target_id": "screenshot:full_desktop",
                "source": "screenshot_region",
                "title": "active full desktop screenshot",
                "focusable": False,
                "score": 15,
            }
        )
    passive = passive_context if isinstance(passive_context, dict) else {}
    passive_desktop_candidate = _passive_desktop_context_candidate(passive)
    if passive_desktop_candidate:
        raw_candidates.append(passive_desktop_candidate)
    timeline = passive.get("timeline") if isinstance(passive.get("timeline"), list) else []
    for index, event in enumerate(timeline[:3]):
        if not isinstance(event, dict):
            continue
        frame_hash = _clean_text(event.get("frame_hash"), max_length=80)
        summary = _clean_text(event.get("change_summary"), max_length=120)
        raw_candidates.append(
            {
                "target_id": f"passive:{frame_hash or index}",
                "source": "passive_timeline",
                "title": summary,
                "focusable": False,
                "score": 20,
            }
        )
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_candidates):
        normalized = _sanitize_target_candidate(item, index=index)
        if not normalized:
            continue
        target_id = _clean_text(normalized.get("target_id"), max_length=120)
        if not target_id or target_id in seen_ids:
            continue
        text_match_score = _candidate_text_match_score(query_text, normalized)
        if text_match_score:
            normalized["score"] = round(min(100.0, float(normalized.get("score") or 0.0) + text_match_score), 3)
            normalized["match_score"] = round(text_match_score, 3)
        if _candidate_is_current_desktop(normalized, foreground_app, window_title):
            normalized["current_observed"] = True
            normalized["score"] = round(max(0.0, float(normalized.get("score") or 0.0) - 45.0), 3)
        seen_ids.add(normalized["target_id"])
        candidates.append(normalized)
        if len(candidates) >= MAX_TARGET_CANDIDATES:
            break
    candidates.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    return candidates


def _selected_target_candidate(candidates: list[dict[str, Any]], seen_targets: set[str]) -> dict[str, Any]:
    unseen = [item for item in candidates if str(item.get("target_id") or "") not in seen_targets]
    for candidate in unseen:
        if bool(candidate.get("focusable")) and not bool(candidate.get("current_observed")):
            return dict(candidate)
    for candidate in unseen:
        if not bool(candidate.get("current_observed")):
            return dict(candidate)
    return {}


def _target_candidate_by_id(candidates: list[dict[str, Any]], target_id: str) -> dict[str, Any]:
    wanted = _clean_text(target_id, max_length=120)
    if not wanted:
        return {}
    for candidate in candidates:
        if _clean_text(candidate.get("target_id"), max_length=120) == wanted:
            return dict(candidate)
    return {}


def _desktop_targets_from(frame: dict[str, Any], trace: dict[str, Any]) -> list[dict[str, Any]]:
    active = frame.get("active_observation") if isinstance(frame.get("active_observation"), dict) else {}
    return _sanitize_desktop_targets(
        active.get("desktop_targets")
        or trace.get("desktop_targets")
        or frame.get("desktop_targets")
    )


def _is_self_occluded(foreground_app: str, window_title: str, trace: dict[str, Any]) -> bool:
    if bool(trace.get("self_occluded")):
        return True
    app_text = str(foreground_app or "").lower()
    title_text = str(window_title or "").lower()
    status_text = str(trace.get("status") or "").lower()
    if "self_occluded" in status_text:
        return True
    if any(marker in app_text for marker in SELF_OCCLUDED_APP_MARKERS):
        return True
    return any(marker in title_text for marker in SELF_OCCLUDED_TITLE_MARKERS)


def _trace_issue_hint(foreground_app: str, window_title: str, trace: dict[str, Any]) -> str:
    unknowns = trace.get("unknowns") if isinstance(trace.get("unknowns"), list) else []
    text = " ".join(
        [
            str(trace.get("status") or ""),
            str(trace.get("reason") or ""),
            str(trace.get("error") or ""),
            " ".join(str(item or "") for item in unknowns[:4]),
            foreground_app,
            window_title,
        ]
    ).lower()
    if any(marker in text for marker in UNRELATED_WINDOW_MARKERS):
        return "not_relevant:bad_frame"
    return ""


def _has_observations(payload: dict[str, Any]) -> bool:
    observations = payload.get("observations")
    return isinstance(observations, list) and any(isinstance(item, dict) for item in observations)


def build_active_observe_metadata(
    attempt: Any,
    frame_payload: Any,
    trace: Any | None = None,
    *,
    passive_context: Any | None = None,
) -> dict[str, Any]:
    """Build model-facing metadata for one Observe-Reason-Retry active vision attempt.

    This helper is intentionally stateless: the model/tool caller passes
    ``exclude_seen`` from previous attempts, while the backend reports enough
    metadata for the model to decide whether another active observe call is
    useful. It does not run an automatic retry loop.
    """

    attempt_source = attempt if isinstance(attempt, dict) else {}
    frame = frame_payload if isinstance(frame_payload, dict) else {}
    trace_source = trace if isinstance(trace, dict) else {}
    frame_active_source = frame.get("active_observation") if isinstance(frame.get("active_observation"), dict) else {}
    seen = _normalize_exclude_seen(attempt_source.get("exclude_seen"))
    attempt_index = _clamp_int(
        attempt_source.get("attempt_index"),
        fallback=len(seen) + 1,
        min_value=1,
        max_value=99,
    )
    mode = _clean_text(
        attempt_source.get("mode")
        or trace_source.get("mode")
        or frame_active_source.get("mode")
        or ACTIVE_SURVEY_TARGET_ID,
        max_length=40,
    )
    if mode not in {ACTIVE_SURVEY_TARGET_ID, "focus_target"}:
        mode = ACTIVE_SURVEY_TARGET_ID
    target_id = _clean_text(
        attempt_source.get("target_id")
        or trace_source.get("target_id")
        or frame_active_source.get("target_id")
        or (ACTIVE_SURVEY_TARGET_ID if mode == ACTIVE_SURVEY_TARGET_ID else ""),
        max_length=120,
    )
    target_hint = _clean_text(
        attempt_source.get("target_hint")
        or trace_source.get("target_hint")
        or frame_active_source.get("target_hint")
        or target_id
        or _infer_target_hint(str(attempt_source.get("text") or "")),
        max_length=80,
    )
    attempt_reason = _clean_text(
        attempt_source.get("attempt_reason") or trace_source.get("attempt_reason") or trace_source.get("reason"),
        max_length=240,
    )
    desktop = _desktop_context(frame, trace_source)
    foreground_app = _clean_text(desktop.get("foreground_app"), max_length=120)
    window_title = _clean_text(desktop.get("window_title"), max_length=200)
    frame_hash = _clean_text(frame.get("frame_hash") or trace_source.get("frame_hash"), max_length=120)
    self_occluded = _is_self_occluded(foreground_app, window_title, trace_source)
    desktop_targets = _desktop_targets_from(frame, trace_source)
    target_candidates = _target_candidates_from_sources(
        frame,
        trace_source,
        desktop_targets=desktop_targets,
        query_text=_clean_text(attempt_source.get("text"), max_length=500),
        foreground_app=foreground_app,
        window_title=window_title,
        passive_context=passive_context,
    )
    discovery_errors = _sanitize_discovery_errors(
        frame_active_source.get("discovery_errors")
        or trace_source.get("discovery_errors")
        or frame.get("discovery_errors")
    )

    attempted_targets = [entry["target_id"] or entry["target_hint"] for entry in seen if entry.get("target_id") or entry.get("target_hint")]
    current_target_key = target_id or target_hint or (ACTIVE_SURVEY_TARGET_ID if mode == ACTIVE_SURVEY_TARGET_ID else "")
    if current_target_key and current_target_key not in attempted_targets:
        attempted_targets.append(current_target_key)
    seen_targets = set(attempted_targets)
    next_unseen_candidate = _selected_target_candidate(target_candidates, seen_targets)
    selected_candidate = (
        _target_candidate_by_id(target_candidates, target_id)
        if mode == "focus_target"
        else next_unseen_candidate
    )
    available_next_targets = [
        str(candidate.get("target_id") or "")
        for candidate in target_candidates
        if bool(candidate.get("focusable"))
        and not bool(candidate.get("current_observed"))
        and str(candidate.get("target_id") or "")
        and str(candidate.get("target_id") or "") not in seen_targets
    ]

    repeated_frame = bool(frame_hash) and any(entry.get("frame_hash") == frame_hash for entry in seen)
    repeated_window = bool(foreground_app or window_title) and any(
        entry.get("foreground_app") == foreground_app
        and entry.get("window_title") == window_title
        and (entry.get("foreground_app") or entry.get("window_title"))
        for entry in seen
    )
    trace_issue_hint = _trace_issue_hint(foreground_app, window_title, trace_source)
    if repeated_frame:
        relevance_hint = "stop:repeated_frame"
        stop = True
        available_next_targets = []
    elif repeated_window:
        relevance_hint = "stop:repeated_window"
        stop = True
        available_next_targets = []
    elif attempt_index >= 3:
        relevance_hint = "stop:max_attempts"
        stop = True
        available_next_targets = []
    elif self_occluded and available_next_targets:
        relevance_hint = "not_relevant:self_occluded"
        stop = False
    elif trace_issue_hint:
        relevance_hint = trace_issue_hint
        stop = False
    elif not _has_observations(frame) and available_next_targets:
        relevance_hint = "insufficient_evidence"
        stop = False
    elif not _has_observations(frame) and next_unseen_candidate:
        relevance_hint = "insufficient_evidence:fallback_candidates"
        stop = False
    elif not available_next_targets:
        relevance_hint = "stop:no_new_targets"
        stop = True
    else:
        relevance_hint = "likely_relevant"
        stop = False

    return {
        "attempt_index": attempt_index,
        "mode": mode,
        "target_id": target_id,
        "target_hint": target_hint,
        "attempt_reason": attempt_reason,
        "foreground_app": foreground_app,
        "window_title": window_title,
        "frame_hash": frame_hash,
        "desktop_targets": desktop_targets,
        "target_candidates": target_candidates,
        "selected_candidate": selected_candidate,
        "discovery_errors": discovery_errors,
        "focused_target": (
            trace_source.get("focused_target")
            if isinstance(trace_source.get("focused_target"), dict)
            else frame_active_source.get("focused_target")
            if isinstance(frame_active_source.get("focused_target"), dict)
            else {}
        ),
        "action_trace": (
            trace_source.get("action_trace")
            if isinstance(trace_source.get("action_trace"), list)
            else frame_active_source.get("action_trace")
            if isinstance(frame_active_source.get("action_trace"), list)
            else []
        ),
        "click_point": (
            trace_source.get("click_point")
            if isinstance(trace_source.get("click_point"), dict)
            else frame_active_source.get("click_point")
            if isinstance(frame_active_source.get("click_point"), dict)
            else {}
        ),
        "click_policy": _clean_text(
            trace_source.get("click_policy") or frame_active_source.get("click_policy") or DEFAULT_CLICK_POLICY,
            max_length=80,
        ),
        "focus_result": (
            trace_source.get("focus_result")
            if isinstance(trace_source.get("focus_result"), dict)
            else frame_active_source.get("focus_result")
            if isinstance(frame_active_source.get("focus_result"), dict)
            else {}
        ),
        "verify_result": (
            trace_source.get("verify_result")
            if isinstance(trace_source.get("verify_result"), dict)
            else frame_active_source.get("verify_result")
            if isinstance(frame_active_source.get("verify_result"), dict)
            else {}
        ),
        "detail_frames_count": _clamp_int(
            trace_source.get("detail_frames_count", frame_active_source.get("detail_frames_count")),
            fallback=0,
            min_value=0,
            max_value=8,
        ),
        "self_occluded": self_occluded,
        "relevance_hint": relevance_hint,
        "available_next_targets": available_next_targets,
        "attempted_targets": attempted_targets,
        "stop": stop,
    }


def decide_active_observation(
    text: str,
    vision_config: Any,
    *,
    target_hint: str = "",
    force: bool = False,
    decider: Callable[[str, dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    active_cfg = normalize_active_observation_config(
        (vision_config or {}).get("active_observation") if isinstance(vision_config, dict) else {}
    )
    vision_enabled = bool((vision_config or {}).get("enabled")) if isinstance(vision_config, dict) else False
    if not vision_enabled:
        return {
            "needs_observation": False,
            "target_hint": "",
            "actions": [],
            "reason": "vision-disabled",
            "source": "fallback",
        }
    if not active_cfg["enabled"]:
        return {
            "needs_observation": False,
            "target_hint": "",
            "actions": [],
            "reason": "active-observation-disabled",
            "source": "fallback",
        }

    if decider is not None:
        try:
            parsed = _coerce_decider_output(decider(text, active_cfg))
            needs_observation = bool(parsed.get("needs_observation"))
            mode = _clean_text(parsed.get("mode") or target_hint or ACTIVE_SURVEY_TARGET_ID, max_length=40)
            if mode not in {ACTIVE_SURVEY_TARGET_ID, "focus_target"}:
                mode = ACTIVE_SURVEY_TARGET_ID
            actions = _sanitize_actions(parsed.get("actions"), allow_default=needs_observation and mode == "focus_target")
            if active_cfg["allowed_interaction"] == "none":
                actions = []
            target_id = _clean_text(parsed.get("target_id"), max_length=120)
            return {
                "needs_observation": needs_observation,
                "mode": mode,
                "target_id": target_id,
                "target_hint": _infer_target_hint(text, parsed.get("target_hint") or target_id or mode),
                "actions": actions,
                "click_policy": DEFAULT_CLICK_POLICY,
                "reason": _clean_text(parsed.get("reason") or "decider", max_length=240),
                "source": "decider",
            }
        except Exception as exc:
            fallback_reason = f"invalid decision JSON; fallback: {exc}"
        else:
            fallback_reason = "fallback"
    else:
        fallback_reason = "fallback"

    vision_cfg = normalize_vision_config(vision_config)
    configured_grounding = bool(
        vision_cfg["grounding_mode"] != "off"
        and (
            vision_cfg["force_grounding"]
            or vision_cfg["grounding_mode"] == "always"
        )
    )
    needs_observation = bool(force) or configured_grounding
    mode = "focus_target" if target_hint and target_hint != ACTIVE_SURVEY_TARGET_ID else ACTIVE_SURVEY_TARGET_ID
    target_id = _clean_text(target_hint if mode == "focus_target" else ACTIVE_SURVEY_TARGET_ID, max_length=120)
    actions = ["focus_target"] if needs_observation and mode == "focus_target" and active_cfg["allowed_interaction"] != "none" else []
    return {
        "needs_observation": needs_observation,
        "mode": mode,
        "target_id": target_id,
        "target_hint": _infer_target_hint(text, target_id or target_hint),
        "actions": actions,
        "click_policy": DEFAULT_CLICK_POLICY,
        "reason": fallback_reason if needs_observation else "no-visual-grounding-needed",
        "source": "fallback",
    }
