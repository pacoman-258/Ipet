from __future__ import annotations

import hashlib
from typing import Any


ACTIVE_VISION_MAX_CANDIDATES = 12


def clean_vision_text(value: Any, *, max_length: int) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = " ".join(text.split())
    return text[:max_length]


def active_candidate_id(source: str, app: str, title: str, index: int) -> str:
    seed = "\x00".join([source, app, title, str(index)])
    return f"{source}:{hashlib.sha1(seed.encode('utf-8', errors='ignore')).hexdigest()[:14]}"


def active_target_id(app: str, title: str, bounds: dict[str, int], index: int) -> str:
    seed = "\x00".join(
        [
            clean_vision_text(app, max_length=120),
            clean_vision_text(title, max_length=200),
            str(bounds.get("x", 0)),
            str(bounds.get("y", 0)),
            str(bounds.get("width", 0)),
            str(bounds.get("height", 0)),
            str(index),
        ]
    )
    return "macos:" + hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]


def safe_focus_point(bounds: dict[str, int]) -> dict[str, int]:
    x = int(bounds.get("x") or 0)
    y = int(bounds.get("y") or 0)
    width = max(0, int(bounds.get("width") or 0))
    titlebar_y = y + 12
    focus_x = x + min(max(width // 2, 24), 180)
    return {"x": focus_x, "y": titlebar_y}


def sanitize_active_candidate(candidate: dict, *, index: int = 0, source: str = "") -> dict:
    item = candidate if isinstance(candidate, dict) else {}
    candidate_source = clean_vision_text(item.get("source") or source or "unknown", max_length=40)
    app = clean_vision_text(item.get("app"), max_length=120)
    title = clean_vision_text(item.get("title") or item.get("window_title") or app, max_length=200)
    target_id = clean_vision_text(item.get("target_id") or item.get("candidate_id"), max_length=120)
    if not target_id:
        target_id = active_candidate_id(candidate_source, app, title, index)
    bounds_source = item.get("bounds") if isinstance(item.get("bounds"), dict) else {}
    bounds = {
        "x": int(bounds_source.get("x") or 0),
        "y": int(bounds_source.get("y") or 0),
        "width": max(0, int(bounds_source.get("width") or 0)),
        "height": max(0, int(bounds_source.get("height") or 0)),
    }
    focus_point = item.get("focus_point") if isinstance(item.get("focus_point"), dict) else {}
    focusable = bool(item.get("focusable", candidate_source in {"window_enumeration", "running_app", "desktop_context"}))
    if candidate_source == "screenshot_region":
        focusable = False
    sanitized = {
        "target_id": target_id,
        "source": candidate_source,
        "app": app,
        "title": title,
        "bounds": bounds,
        "frontmost": bool(item.get("frontmost", False)),
        "minimized": bool(item.get("minimized", False)),
        "focus_point": dict(focus_point) if focus_point else {},
        "focusable": focusable,
        "score": float(item.get("score") or (90 if candidate_source == "window_enumeration" else 70 if focusable else 20)),
    }
    for key in ("bundle_id", "pid", "reason"):
        text = clean_vision_text(item.get(key), max_length=160)
        if text:
            sanitized[key] = text
    return sanitized


def active_candidates_from_desktop_targets(desktop_targets: list[dict]) -> list[dict]:
    candidates: list[dict] = []
    for index, target in enumerate(desktop_targets or []):
        if not isinstance(target, dict):
            continue
        candidates.append(
            sanitize_active_candidate(
                {
                    **target,
                    "source": target.get("source") or "window_enumeration",
                    "focusable": not bool(target.get("minimized", False)),
                    "score": 90 if target.get("frontmost") else 85,
                },
                index=index,
                source="window_enumeration",
            )
        )
    return candidates


def active_screenshot_candidate(display_layout: list[dict] | None = None) -> dict:
    bounds = {"x": 0, "y": 0, "width": 0, "height": 0}
    if display_layout:
        try:
            min_x = min(int(item.get("x") or 0) for item in display_layout)
            min_y = min(int(item.get("y") or 0) for item in display_layout)
            max_x = max(int(item.get("x") or 0) + int(item.get("width") or 0) for item in display_layout)
            max_y = max(int(item.get("y") or 0) + int(item.get("height") or 0) for item in display_layout)
            bounds = {"x": min_x, "y": min_y, "width": max(0, max_x - min_x), "height": max(0, max_y - min_y)}
        except Exception:
            bounds = {"x": 0, "y": 0, "width": 0, "height": 0}
    return sanitize_active_candidate(
        {
            "target_id": "screenshot:full_desktop",
            "source": "screenshot_region",
            "title": "active full desktop screenshot",
            "bounds": bounds,
            "focusable": False,
            "score": 15,
        },
        source="screenshot_region",
    )


def active_vision_name_key(name: str) -> str:
    return clean_vision_text(name, max_length=120).casefold()


def merge_active_target_candidates(*candidate_groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen_ids: set[str] = set()
    for group in candidate_groups:
        for index, item in enumerate(group or []):
            if not isinstance(item, dict):
                continue
            candidate = sanitize_active_candidate(item, index=index)
            target_id = str(candidate.get("target_id") or "")
            if not target_id or target_id in seen_ids:
                continue
            seen_ids.add(target_id)
            merged.append(candidate)
    return sorted(merged, key=lambda value: float(value.get("score") or 0.0), reverse=True)[:ACTIVE_VISION_MAX_CANDIDATES]


def boost_candidates_for_target_hint(candidates: list[dict], target_hint: str) -> list[dict]:
    hint = active_vision_name_key(target_hint)
    if not hint:
        return list(candidates or [])
    aliases = {
        "微信": {"微信", "wechat"},
        "wechat": {"微信", "wechat"},
        "chrome": {"google chrome", "chrome"},
        "谷歌": {"google chrome", "chrome"},
    }
    boosted: list[dict] = []
    for item in candidates or []:
        candidate = dict(item)
        app = active_vision_name_key(str(candidate.get("app") or candidate.get("title") or ""))
        match = bool(app and app in hint)
        for key, values in aliases.items():
            if key in hint:
                normalized_values = {active_vision_name_key(value) for value in values}
                if app in normalized_values:
                    match = True
                    break
        if match:
            candidate["score"] = max(float(candidate.get("score") or 0.0), 96.0)
            candidate["reason"] = "target_hint_match"
        boosted.append(candidate)
    return boosted


def find_desktop_target(desktop_targets: list[dict] | tuple[dict, ...] | None, target_id: str) -> dict:
    wanted = clean_vision_text(target_id, max_length=120)
    for target in desktop_targets or []:
        if not isinstance(target, dict):
            continue
        if clean_vision_text(target.get("target_id"), max_length=120) == wanted:
            return dict(target)
    return {}


def find_active_target(
    desktop_targets: list[dict] | tuple[dict, ...] | None,
    target_candidates: list[dict] | tuple[dict, ...] | None,
    target_id: str,
) -> dict:
    target = find_desktop_target(desktop_targets, target_id)
    if target:
        target.setdefault("source", "window_enumeration")
        target.setdefault("focusable", True)
        return target
    wanted = clean_vision_text(target_id, max_length=120)
    for candidate in target_candidates or []:
        if not isinstance(candidate, dict):
            continue
        if clean_vision_text(candidate.get("target_id"), max_length=120) == wanted:
            return dict(candidate)
    return {}
