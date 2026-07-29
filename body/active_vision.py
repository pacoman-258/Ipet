from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import time
from typing import Any

from app import desktop_runtime as _desktop_runtime
from backend.active_vision import normalize_active_observation_config
from backend.vision import normalize_vision_config
from body import active_vision_macos as _active_macos
from body import active_vision_targets as _active_targets
from body import macos_accessibility as _macos_accessibility
from body import screen_capture as _screen_capture


ACTIVE_VISION_ALLOWED_ACTIONS = {"focus_target"}
ACTIVE_VISION_CLICK_POLICY = "window_focus_only"
ACTIVE_VISION_SURVEY_MODE = "desktop_survey"
ACTIVE_VISION_FOCUS_MODE = "focus_target"
ACTIVE_VISION_MIN_MAX_WIDTH = 1920
ACTIVE_VISION_MIN_JPEG_QUALITY = 88
ACTIVE_VISION_MAX_CANDIDATES = _active_targets.ACTIVE_VISION_MAX_CANDIDATES
ACTIVE_VISION_WINDOW_ENUMERATION_TIMEOUT_SEC = _active_macos.ACTIVE_VISION_WINDOW_ENUMERATION_TIMEOUT_SEC
ACTIVE_VISION_RUNNING_APP_FALLBACK_TIMEOUT_SEC = _active_macos.ACTIVE_VISION_RUNNING_APP_FALLBACK_TIMEOUT_SEC
ACTIVE_VISION_DOCK_ITEM_ENUMERATION_TIMEOUT_SEC = _active_macos.ACTIVE_VISION_DOCK_ITEM_ENUMERATION_TIMEOUT_SEC


def _is_macos(platform_name: str | None = None) -> bool:
    return _desktop_runtime.is_macos(platform_name, sys_platform=sys.platform)


def _clean_vision_text(value: Any, *, max_length: int) -> str:
    return _screen_capture._clean_vision_text(value, max_length=max_length)


def _osascript_args(script: str) -> list[str]:
    return _active_macos._osascript_args(script)


def _active_target_id(app: str, title: str, bounds: dict[str, int], index: int) -> str:
    return _active_targets.active_target_id(app, title, bounds, index)


def _safe_focus_point(bounds: dict[str, int]) -> dict[str, int]:
    return _active_targets.safe_focus_point(bounds)


def _active_vision_capture_config(vision_cfg: dict) -> dict:
    config = dict(vision_cfg or {})
    config["max_width"] = max(int(config.get("max_width") or 0), ACTIVE_VISION_MIN_MAX_WIDTH)
    config["jpeg_quality"] = max(int(config.get("jpeg_quality") or 0), ACTIVE_VISION_MIN_JPEG_QUALITY)
    return config


def _parse_macos_desktop_targets(output: str) -> list[dict]:
    return _active_macos._parse_macos_desktop_targets(output)


def discover_macos_active_vision_desktop_targets(
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
) -> tuple[list[dict], list[str]]:
    return _active_macos.discover_macos_active_vision_desktop_targets(platform_name=platform_name, runner=runner)


def enumerate_active_vision_desktop_targets(
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
) -> list[dict]:
    return _active_macos.enumerate_active_vision_desktop_targets(platform_name=platform_name, runner=runner)


def _active_candidate_id(source: str, app: str, title: str, index: int) -> str:
    return _active_targets.active_candidate_id(source, app, title, index)


def _sanitize_active_candidate(candidate: dict, *, index: int = 0, source: str = "") -> dict:
    return _active_targets.sanitize_active_candidate(candidate, index=index, source=source)


def _parse_macos_dock_item_candidates(output: str, *, start_index: int = 0) -> list[dict]:
    return _active_macos._parse_macos_dock_item_candidates(output, start_index=start_index)


def enumerate_macos_dock_item_candidates(
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    include_errors: bool = False,
) -> list[dict] | tuple[list[dict], list[str]]:
    return _active_macos.enumerate_macos_dock_item_candidates(
        platform_name=platform_name,
        runner=runner,
        include_errors=include_errors,
    )


def _active_candidates_from_desktop_targets(desktop_targets: list[dict]) -> list[dict]:
    return _active_targets.active_candidates_from_desktop_targets(desktop_targets)


def _active_screenshot_candidate(display_layout: list[dict] | None = None) -> dict:
    return _active_targets.active_screenshot_candidate(display_layout)


def _is_active_vision_system_process_name(name: str) -> bool:
    return _active_macos._is_active_vision_system_process_name(name)


def _active_vision_name_key(name: str) -> str:
    return _active_macos._active_vision_name_key(name)


def _app_bundle_info_from_process_path(process_path: str) -> dict[str, str]:
    return _active_macos._app_bundle_info_from_process_path(process_path)


def _app_bundle_name_from_process_path(process_path: str) -> str:
    return _active_macos._app_bundle_name_from_process_path(process_path)


def _running_app_process_path_score(name: str, location: str) -> float:
    return _active_macos._running_app_process_path_score(name, location)


def _running_app_candidate_score(name: str, *, frontmost: bool = False, from_process_path: bool = False) -> float:
    return _active_macos._running_app_candidate_score(name, frontmost=frontmost, from_process_path=from_process_path)


def _parse_system_events_running_app_candidates(output: str, *, start_index: int = 0) -> list[dict]:
    return _active_macos._parse_system_events_running_app_candidates(output, start_index=start_index)


def _system_events_running_app_candidates(*, runner=subprocess.run) -> tuple[list[dict], list[str]]:
    return _active_macos._system_events_running_app_candidates(runner=runner)


def enumerate_active_vision_running_app_candidates(
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    include_errors: bool = False,
) -> list[dict] | tuple[list[dict], list[str]]:
    return _active_macos.enumerate_active_vision_running_app_candidates(
        platform_name=platform_name,
        runner=runner,
        include_errors=include_errors,
    )


def _merge_active_target_candidates(*candidate_groups: list[dict]) -> list[dict]:
    return _active_targets.merge_active_target_candidates(*candidate_groups)


def _boost_candidates_for_target_hint(candidates: list[dict], target_hint: str) -> list[dict]:
    return _active_targets.boost_candidates_for_target_hint(candidates, target_hint)


def discover_active_vision_target_candidates(
    *,
    desktop_targets: list[dict] | None = None,
    desktop_targets_provider=enumerate_active_vision_desktop_targets,
    dock_items_provider=enumerate_macos_dock_item_candidates,
    running_apps_provider=enumerate_active_vision_running_app_candidates,
    target_candidates: list[dict] | None = None,
    display_layout: list[dict] | None = None,
    target_hint: str = "",
    platform_name: str | None = None,
    runner=subprocess.run,
) -> dict:
    discovery_errors: list[str] = []
    discovered_targets = list(desktop_targets or [])
    if not discovered_targets and desktop_targets_provider:
        try:
            if desktop_targets_provider is enumerate_active_vision_desktop_targets:
                discovered_targets, errors = discover_macos_active_vision_desktop_targets(
                    platform_name=platform_name or sys.platform,
                    runner=runner,
                )
                discovery_errors.extend(errors)
            else:
                raw_targets = desktop_targets_provider(platform_name=platform_name or sys.platform)
                if isinstance(raw_targets, dict):
                    discovered_targets = list(raw_targets.get("desktop_targets") or [])
                    discovery_errors.extend(str(item) for item in raw_targets.get("discovery_errors") or [])
                    target_candidates = list(target_candidates or []) + list(raw_targets.get("target_candidates") or [])
                else:
                    discovered_targets = list(raw_targets or [])
        except TypeError:
            try:
                discovered_targets = list(desktop_targets_provider() or [])
            except Exception as exc:
                discovery_errors.append(f"desktop target discovery failed: {exc}")
        except Exception as exc:
            discovery_errors.append(str(exc))
    dock_candidates: list[dict] = []
    if dock_items_provider:
        try:
            if dock_items_provider is enumerate_macos_dock_item_candidates:
                dock_result = dock_items_provider(
                    platform_name=platform_name or sys.platform,
                    runner=runner,
                    include_errors=True,
                )
                if isinstance(dock_result, tuple):
                    dock_candidates = list(dock_result[0] or [])
                    discovery_errors.extend(str(item) for item in (dock_result[1] or []))
                else:
                    dock_candidates = list(dock_result or [])
            else:
                dock_candidates = list(dock_items_provider(platform_name=platform_name or sys.platform) or [])
        except TypeError:
            try:
                dock_candidates = list(dock_items_provider() or [])
            except Exception as exc:
                discovery_errors.append(f"Dock item fallback failed: {exc}")
        except Exception as exc:
            discovery_errors.append(f"Dock item fallback failed: {exc}")
    dock_candidates = _boost_candidates_for_target_hint(dock_candidates, target_hint)
    running_candidates: list[dict] = []
    if running_apps_provider:
        try:
            if running_apps_provider is enumerate_active_vision_running_app_candidates:
                running_result = running_apps_provider(
                    platform_name=platform_name or sys.platform,
                    runner=runner,
                    include_errors=True,
                )
                if isinstance(running_result, tuple):
                    running_candidates = list(running_result[0] or [])
                    discovery_errors.extend(str(item) for item in (running_result[1] or []))
                else:
                    running_candidates = list(running_result or [])
            else:
                running_candidates = list(running_apps_provider(platform_name=platform_name or sys.platform) or [])
        except TypeError:
            try:
                running_candidates = list(running_apps_provider() or [])
            except Exception as exc:
                discovery_errors.append(f"running app fallback failed: {exc}")
        except Exception as exc:
            discovery_errors.append(f"running app fallback failed: {exc}")
    candidates = _merge_active_target_candidates(
        _active_candidates_from_desktop_targets(discovered_targets),
        list(target_candidates or []),
        dock_candidates,
        running_candidates,
        [_active_screenshot_candidate(display_layout)],
    )
    return {
        "desktop_targets": discovered_targets[:12],
        "target_candidates": candidates,
        "discovery_errors": [_clean_vision_text(item, max_length=180) for item in discovery_errors if str(item or "").strip()][:6],
    }


def _find_desktop_target(desktop_targets: list[dict] | tuple[dict, ...] | None, target_id: str) -> dict:
    return _active_targets.find_desktop_target(desktop_targets, target_id)


def _find_active_target(
    desktop_targets: list[dict] | tuple[dict, ...] | None,
    target_candidates: list[dict] | tuple[dict, ...] | None,
    target_id: str,
) -> dict:
    return _active_targets.find_active_target(desktop_targets, target_candidates, target_id)


def _active_vision_focus_script(target: dict, click_policy: str) -> str:
    if click_policy != ACTIVE_VISION_CLICK_POLICY:
        return ""
    app = json.dumps(_clean_vision_text(target.get("app"), max_length=120))
    title = json.dumps(_clean_vision_text(target.get("title"), max_length=200))
    focus_point = target.get("focus_point") if isinstance(target.get("focus_point"), dict) else {}
    try:
        click_x = int(focus_point.get("x"))
        click_y = int(focus_point.get("y"))
    except Exception:
        return ""
    return f"""
tell application "System Events"
    set targetApp to {app}
    set targetTitle to {title}
    set accessibilityRaiseStatus to "unknown"
    set windowFocusClickStatus to "unknown"
    if targetApp is "" then return "accessibility_raise=unknown:missing_app" & linefeed & "window_focus_click=unknown:missing_app"
    if not (exists application process targetApp) then return "accessibility_raise=unknown:app_not_found" & linefeed & "window_focus_click=unknown:app_not_found"
    tell application process targetApp
        set frontmost to true
        try
            if targetTitle is not "" then
                perform action "AXRaise" of first window whose name is targetTitle
            else
                perform action "AXRaise" of first window
            end if
            set accessibilityRaiseStatus to "success"
        on error errMsg
            set accessibilityRaiseStatus to "unknown:" & errMsg
        end try
    end tell
    try
        click at {{{click_x}, {click_y}}}
        set windowFocusClickStatus to "success"
    on error errMsg
        set windowFocusClickStatus to "unknown:" & errMsg
    end try
    return "accessibility_raise=" & accessibilityRaiseStatus & linefeed & "window_focus_click=" & windowFocusClickStatus
end tell
""".strip()


def _parse_active_vision_focus_result(output: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for line in str(output or "").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in {"accessibility_raise", "window_focus_click"}:
            statuses[key] = _clean_vision_text(value, max_length=160) or "unknown"
    return statuses


def _activate_running_app_with_open(target: dict, *, runner=subprocess.run) -> tuple[bool, str]:
    app_name = _clean_vision_text(target.get("app") or target.get("title"), max_length=120)
    if not app_name:
        return False, "running app activation unavailable: missing app name"
    try:
        result = runner(["/usr/bin/open", "-a", app_name], capture_output=True, text=True, timeout=1.2, check=False)
    except Exception as exc:
        return False, f"open -a activation failed: {exc}"
    if getattr(result, "returncode", 1) == 0:
        return True, "success"
    detail = _clean_vision_text(getattr(result, "stderr", "") or getattr(result, "stdout", ""), max_length=160)
    return False, f"open -a activation failed: {detail or 'open returned non-zero exit'}"


def _activate_running_app_candidate(target: dict, *, runner=subprocess.run) -> tuple[bool, str]:
    native_detail = ""
    try:
        import AppKit  # type: ignore

        app = None
        pid_text = str(target.get("pid") or "").strip()
        if pid_text:
            try:
                app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid_text))
            except Exception:
                app = None
        bundle_id = str(target.get("bundle_id") or "").strip()
        if app is None and bundle_id:
            matches = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
            app = list(matches or [None])[0]
        if app is None:
            native_detail = "native running app activation unavailable"
        else:
            options = getattr(AppKit, "NSApplicationActivateIgnoringOtherApps", 1)
            ok = bool(app.activateWithOptions_(options))
            if ok:
                return True, "success"
            native_detail = "native running app activation returned false"
    except Exception as exc:
        native_detail = f"native running app activation unavailable: {exc}"
    fallback_ok, fallback_detail = _activate_running_app_with_open(target, runner=runner)
    if fallback_ok:
        return True, fallback_detail
    if native_detail:
        return False, f"{native_detail}; {fallback_detail}"
    return False, fallback_detail


def _can_activate_app_level_candidate(target: dict) -> bool:
    if target.get("focus_point"):
        return False
    if not bool(target.get("focusable")):
        return False
    return bool(_clean_vision_text(target.get("app") or target.get("title"), max_length=120))


def run_active_vision_light_interaction(
    *,
    mode: str = ACTIVE_VISION_SURVEY_MODE,
    target_id: str = "",
    target_hint: str = "",
    desktop_targets: list[dict] | tuple[dict, ...] | None = None,
    target_candidates: list[dict] | tuple[dict, ...] | None = None,
    click_policy: str = ACTIVE_VISION_CLICK_POLICY,
    actions: list[str] | tuple[str, ...] | None = None,
    platform_name: str | None = None,
    runner=subprocess.run,
) -> dict:
    requested = [str(item or "").strip() for item in (actions or []) if str(item or "").strip()]
    normalized_mode = _clean_vision_text(mode, max_length=40) or ACTIVE_VISION_SURVEY_MODE
    if normalized_mode != ACTIVE_VISION_FOCUS_MODE:
        requested = []
    allowed = [action for action in requested if action in ACTIVE_VISION_ALLOWED_ACTIONS]
    blocked = [action for action in requested if action not in ACTIVE_VISION_ALLOWED_ACTIONS]
    target = _find_active_target(desktop_targets, target_candidates, target_id)
    trace = {
        "status": "success",
        "mode": normalized_mode,
        "target_id": _clean_vision_text(target_id, max_length=120),
        "target_hint": _clean_vision_text(target_hint, max_length=80),
        "click_policy": ACTIVE_VISION_CLICK_POLICY,
        "desktop_targets": list(desktop_targets or [])[:12],
        "target_candidates": list(target_candidates or [])[:12],
        "selected_candidate": dict(target) if target else {},
        "focused_target": {},
        "click_point": {},
        "action_trace": [],
        "actions": [],
        "blocked_actions": blocked,
        "unknowns": [],
        "focus_result": {},
    }
    if normalized_mode == ACTIVE_VISION_SURVEY_MODE:
        return trace
    if click_policy and click_policy != ACTIVE_VISION_CLICK_POLICY:
        trace["blocked_actions"].append(f"click_policy:{_clean_vision_text(click_policy, max_length=40)}")
    if not _is_macos(platform_name):
        trace["unknowns"].append("active vision light interaction is only implemented on macOS")
        return trace
    if not target:
        trace["status"] = "error"
        trace["unknowns"].append("active vision target not found")
        trace["focus_result"] = {"status": "error", "method": "target_lookup", "reason": "active vision target not found"}
        return trace
    for action in allowed:
        if action == ACTIVE_VISION_FOCUS_MODE and _can_activate_app_level_candidate(target):
            ok, detail = _activate_running_app_candidate(target, runner=runner)
            entry = {
                "action": "activate_running_app",
                "status": "success" if ok else "unknown",
                "target_id": trace["target_id"],
                "app": _clean_vision_text(target.get("app"), max_length=120),
                "title": _clean_vision_text(target.get("title"), max_length=200),
            }
            if not ok:
                entry["detail"] = detail
                trace["unknowns"].append(f"activate_running_app failed: {detail}")
            trace["action_trace"].append(entry)
            trace["focus_result"] = {
                "status": "success" if ok else "error",
                "method": "activate_running_app",
                "reason": "" if ok else detail,
            }
            if ok:
                trace["actions"].append(action)
                trace["focused_target"] = dict(target)
            continue
        script = _active_vision_focus_script(target, click_policy) if action == ACTIVE_VISION_FOCUS_MODE else ""
        if not script:
            trace["blocked_actions"].append(action)
            continue
        try:
            result = runner(["osascript", "-e", script], capture_output=True, text=True, timeout=1.2, check=False)
        except Exception as exc:
            trace["unknowns"].append(f"{action} failed: {exc}")
            continue
        if getattr(result, "returncode", 1) == 0:
            step_statuses = _parse_active_vision_focus_result(getattr(result, "stdout", ""))
            focus_point = target.get("focus_point") if isinstance(target.get("focus_point"), dict) else {}
            app_name = _clean_vision_text(target.get("app"), max_length=120)
            title = _clean_vision_text(target.get("title"), max_length=200)
            for step in ("accessibility_raise", "window_focus_click"):
                status = step_statuses.get(step) or "unknown"
                entry = {
                    "action": step,
                    "status": "success" if status == "success" else "unknown",
                    "target_id": trace["target_id"],
                    "app": app_name,
                    "title": title,
                }
                if step == "window_focus_click" and status == "success":
                    entry["click_policy"] = ACTIVE_VISION_CLICK_POLICY
                    entry["click_point"] = dict(focus_point)
                    trace["click_point"] = dict(focus_point)
                if status != "success":
                    entry["detail"] = status
                    trace["unknowns"].append(f"{step} failed: {status}")
                trace["action_trace"].append(entry)
            if step_statuses.get("accessibility_raise") == "success" and step_statuses.get("window_focus_click") == "success":
                trace["actions"].append(action)
                trace["focused_target"] = dict(target)
                trace["focus_result"] = {"status": "success", "method": ACTIVE_VISION_CLICK_POLICY}
        else:
            detail = _clean_vision_text(getattr(result, "stderr", ""), max_length=120)
            trace["unknowns"].append(f"{action} failed: {detail or 'osascript failed'}")
    if trace["unknowns"]:
        trace["status"] = "partial" if trace["actions"] else "error"
        if not trace.get("focus_result"):
            trace["focus_result"] = {
                "status": trace["status"],
                "method": ACTIVE_VISION_CLICK_POLICY,
                "reason": "; ".join(trace["unknowns"][:3]),
            }
    elif trace["actions"] and not trace.get("focus_result"):
        trace["focus_result"] = {"status": "success", "method": ACTIVE_VISION_CLICK_POLICY}
    return trace


def _vision_inline_frame_from_payload(frame_payload: dict, *, purpose: str = "main") -> dict:
    mime_type = str(frame_payload.get("mime_type") or "").strip().lower()
    data_url = str(frame_payload.get("data_url") or "").strip()
    if mime_type not in {"image/jpeg", "image/png"} or not data_url.startswith("data:image/"):
        return {}
    inline = {
        "mime_type": mime_type,
        "data_url": data_url,
        "frame_id": _clean_vision_text(frame_payload.get("frame_id"), max_length=80),
        "frame_hash": _clean_vision_text(frame_payload.get("frame_hash"), max_length=120),
        "purpose": _clean_vision_text(purpose, max_length=40),
    }
    return {key: value for key, value in inline.items() if value}


def _decode_frame_image(data_url: str):
    try:
        header, encoded = str(data_url or "").split(",", 1)
    except ValueError:
        return None
    if not header.startswith("data:image/"):
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception:
        return None
    image = _screen_capture.load_qt_screen_capture_dependencies().q_image()
    try:
        if not image.loadFromData(raw):
            return None
    except Exception:
        return None
    return image


def _display_union_bounds(display_layout: list[dict] | None) -> dict[str, int]:
    layout = display_layout if isinstance(display_layout, list) else []
    if not layout:
        return {"x": 0, "y": 0, "width": 0, "height": 0}
    try:
        min_x = min(int(item.get("x") or 0) for item in layout if isinstance(item, dict))
        min_y = min(int(item.get("y") or 0) for item in layout if isinstance(item, dict))
        max_x = max(int(item.get("x") or 0) + int(item.get("width") or 0) for item in layout if isinstance(item, dict))
        max_y = max(int(item.get("y") or 0) + int(item.get("height") or 0) for item in layout if isinstance(item, dict))
    except Exception:
        return {"x": 0, "y": 0, "width": 0, "height": 0}
    return {"x": min_x, "y": min_y, "width": max(0, max_x - min_x), "height": max(0, max_y - min_y)}


def _active_detail_frames_from_capture(frame_payload: dict, selected_candidate: dict, config: dict) -> list[dict]:
    bounds = selected_candidate.get("bounds") if isinstance(selected_candidate.get("bounds"), dict) else {}
    width = int(bounds.get("width") or 0)
    height = int(bounds.get("height") or 0)
    if width < 80 or height < 40:
        return []
    image = _decode_frame_image(str(frame_payload.get("data_url") or ""))
    if image is None or image.isNull():
        return []
    display_bounds = _display_union_bounds(frame_payload.get("display_layout") if isinstance(frame_payload.get("display_layout"), list) else None)
    if display_bounds["width"] <= 0 or display_bounds["height"] <= 0:
        display_bounds = {"x": 0, "y": 0, "width": image.width(), "height": image.height()}
    scale_x = image.width() / max(1, display_bounds["width"])
    scale_y = image.height() / max(1, display_bounds["height"])
    pad_x = max(20, int(width * 0.06))
    pad_y = max(20, int(height * 0.06))
    crop_x = int((int(bounds.get("x") or 0) - display_bounds["x"] - pad_x) * scale_x)
    crop_y = int((int(bounds.get("y") or 0) - display_bounds["y"] - pad_y) * scale_y)
    crop_w = int((width + pad_x * 2) * scale_x)
    crop_h = int((height + pad_y * 2) * scale_y)
    crop_x = max(0, min(image.width() - 1, crop_x))
    crop_y = max(0, min(image.height() - 1, crop_y))
    crop_w = max(1, min(image.width() - crop_x, crop_w))
    crop_h = max(1, min(image.height() - crop_y, crop_h))
    try:
        crop = image.copy(crop_x, crop_y, crop_w, crop_h)
        mime_type, data_url, image_width, image_height = _screen_capture._encoded_frame_parts(
            _screen_capture._encode_pixmap_frame(crop, config)
        )
    except Exception:
        return []
    frame_hash = _screen_capture._vision_hash_from_data_url(data_url)
    detail_payload = {
        "mime_type": mime_type,
        "data_url": data_url,
        "frame_id": f"{_clean_vision_text(frame_payload.get('frame_id'), max_length=60) or 'active'}-detail-1",
        "frame_hash": frame_hash,
        "purpose": "detail_crop",
        "source_frame_id": _clean_vision_text(frame_payload.get("frame_id"), max_length=80),
        "target_id": _clean_vision_text(selected_candidate.get("target_id"), max_length=120),
        "crop_bounds": {"x": crop_x, "y": crop_y, "width": crop_w, "height": crop_h},
    }
    if image_width > 0 and image_height > 0:
        detail_payload["image_width"] = image_width
        detail_payload["image_height"] = image_height
    return [detail_payload]


def _crop_application_frame_payload(
    frame_payload: dict,
    target_bounds: dict,
    config: dict,
) -> dict:
    try:
        bounds = {
            key: int(round(float(target_bounds.get(key) or 0)))
            for key in ("x", "y", "width", "height")
        }
    except (TypeError, ValueError):
        return frame_payload
    if bounds["width"] < 80 or bounds["height"] < 40:
        return frame_payload
    image = _decode_frame_image(str(frame_payload.get("data_url") or ""))
    if image is None or image.isNull():
        return frame_payload
    display_bounds = _display_union_bounds(
        frame_payload.get("display_layout")
        if isinstance(frame_payload.get("display_layout"), list)
        else None
    )
    if display_bounds["width"] <= 0 or display_bounds["height"] <= 0:
        return frame_payload
    left = max(display_bounds["x"], bounds["x"])
    top = max(display_bounds["y"], bounds["y"])
    right = min(
        display_bounds["x"] + display_bounds["width"],
        bounds["x"] + bounds["width"],
    )
    bottom = min(
        display_bounds["y"] + display_bounds["height"],
        bounds["y"] + bounds["height"],
    )
    if right - left < 80 or bottom - top < 40:
        return frame_payload
    scale_x = image.width() / max(1, display_bounds["width"])
    scale_y = image.height() / max(1, display_bounds["height"])
    crop_x = max(
        0,
        min(
            image.width() - 1,
            int(round((left - display_bounds["x"]) * scale_x)),
        ),
    )
    crop_y = max(
        0,
        min(
            image.height() - 1,
            int(round((top - display_bounds["y"]) * scale_y)),
        ),
    )
    crop_right = max(
        crop_x + 1,
        min(
            image.width(),
            int(round((right - display_bounds["x"]) * scale_x)),
        ),
    )
    crop_bottom = max(
        crop_y + 1,
        min(
            image.height(),
            int(round((bottom - display_bounds["y"]) * scale_y)),
        ),
    )
    try:
        crop = image.copy(
            crop_x,
            crop_y,
            crop_right - crop_x,
            crop_bottom - crop_y,
        )
        mime_type, data_url, image_width, image_height = (
            _screen_capture._encoded_frame_parts(
                _screen_capture._encode_pixmap_frame(crop, config)
            )
        )
    except Exception:
        return frame_payload
    cropped = dict(frame_payload)
    cropped.update(
        {
            "mime_type": mime_type,
            "data_url": data_url,
            "frame_hash": _screen_capture._vision_hash_from_data_url(
                data_url
            ),
            "visual_hash": _screen_capture._visual_hash_from_image_like(
                crop
            ),
            "source_capture_scope": str(
                frame_payload.get("capture_scope") or ""
            )[:80],
            "capture_scope": "application",
            "capture_region": "target_application_window",
            "source_frame_hash": str(
                frame_payload.get("frame_hash") or ""
            )[:120],
            "display_count": 1,
            "display_layout": [
                {
                    "x": left,
                    "y": top,
                    "width": right - left,
                    "height": bottom - top,
                    "name": "target_application_window",
                    "device_pixel_ratio": 1.0,
                }
            ],
        }
    )
    if image_width > 0 and image_height > 0:
        cropped["image_width"] = image_width
        cropped["image_height"] = image_height
    return cropped


def _primary_screen():
    return _screen_capture.load_qt_screen_capture_dependencies().q_gui_application.primaryScreen()


def _flush_qt_window_state() -> None:
    """Deliver a hide/show request before blocking the Qt UI thread."""

    try:
        application_type = (
            _screen_capture.load_qt_screen_capture_dependencies()
            .q_gui_application
        )
        application = application_type.instance()
        if application is not None:
            application.processEvents()
    except Exception:
        pass


def _accessibility_frame_payload(
    accessibility: dict[str, Any],
    *,
    target_app: str,
    target_hint: str,
) -> dict[str, Any]:
    captured_at = time.time()
    canonical = json.dumps(accessibility, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    app = accessibility.get("app") if isinstance(accessibility.get("app"), dict) else {}
    observations = (
        accessibility.get("observations")
        if isinstance(accessibility.get("observations"), list)
        else []
    )
    search = accessibility.get("search") if isinstance(accessibility.get("search"), dict) else {}
    usable = bool(accessibility.get("usable"))
    failure_reason = _clean_vision_text(
        accessibility.get("reason") or "macOS Accessibility did not expose a usable application tree",
        max_length=240,
    )
    accessibility_unknowns = (
        [] if usable else [f"accessibility_unavailable: {failure_reason}"]
    )
    trace = {
        "enabled": True,
        "status": "success" if usable else "partial",
        "mode": ACTIVE_VISION_SURVEY_MODE,
        "target_id": ACTIVE_VISION_SURVEY_MODE,
        "target_hint": target_hint,
        "target_app": target_app,
        "capture_scope": "application",
        "click_policy": ACTIVE_VISION_CLICK_POLICY,
        "desktop_targets": [],
        "target_candidates": [],
        "discovery_errors": [],
        "focused_target": {},
        "selected_candidate": {},
        "action_trace": [],
        "click_point": {},
        "actions": [],
        "blocked_actions": [],
        "unknowns": accessibility_unknowns,
        "focus_result": {},
        "verify_result": {
            "status": "observed",
            "method": "macos_accessibility",
            "element_count": int(accessibility.get("element_count") or 0),
            "total_element_count": int(accessibility.get("total_element_count") or 0),
            "snapshot_id": str(accessibility.get("snapshot_id") or ""),
        },
        "detail_frames_count": 0,
        "accessibility_attempt": {
            "status": str(accessibility.get("status") or ""),
            "supported": bool(accessibility.get("supported")),
            "usable": usable,
            "cache_hit": bool(search.get("cache_hit")),
        },
    }
    return {
        "frame_id": f"ax-{int(captured_at * 1000)}-{digest[:12]}",
        "frame_hash": f"sha256:{digest}",
        "captured_at": captured_at,
        "capture_backend": "macos_accessibility",
        "capture_scope": "application",
        "target_app": target_app,
        "foreground_app": {
            "name": str(app.get("name") or target_app),
            "bundle_id": str(app.get("bundle_id") or ""),
        },
        "desktop_context": {
            "foreground_app": str(app.get("name") or target_app),
            "frontmost_process": str(app.get("name") or target_app),
        },
        "accessibility": accessibility,
        "observe_answer": str(accessibility.get("text") or "").strip(),
        "observations": [dict(item) for item in observations if isinstance(item, dict)],
        "unknowns": list(accessibility_unknowns),
        "active_observation": trace,
    }


def capture_active_vision_frame_payload(
    window,
    command_payload: dict,
    config: dict,
    *,
    screen_provider=None,
    frame_encoder=_screen_capture.capture_screen_frame_payload,
    observation_provider=_screen_capture.collect_screen_observations,
    desktop_targets_provider=enumerate_active_vision_desktop_targets,
    dock_items_provider=enumerate_macos_dock_item_candidates,
    running_apps_provider=enumerate_active_vision_running_app_candidates,
    interaction_runner=run_active_vision_light_interaction,
    sleeper=time.sleep,
    platform_name: str | None = None,
    payload_normalizer=None,
    default_frame_encoder=None,
    vision_config_normalizer=normalize_vision_config,
    active_observation_config_normalizer=normalize_active_observation_config,
    accessibility_provider=_macos_accessibility.capture_macos_accessibility,
) -> dict:
    del observation_provider
    vision_cfg = vision_config_normalizer(config or {})
    active_capture_cfg = _active_vision_capture_config(vision_cfg)
    active_cfg = active_observation_config_normalizer(vision_cfg.get("active_observation", {}))
    payload = command_payload if isinstance(command_payload, dict) else {}
    mode = _clean_vision_text(payload.get("mode") or ACTIVE_VISION_SURVEY_MODE, max_length=40)
    if mode not in {ACTIVE_VISION_SURVEY_MODE, ACTIVE_VISION_FOCUS_MODE}:
        mode = ACTIVE_VISION_SURVEY_MODE
    target_id = _clean_vision_text(payload.get("target_id"), max_length=120)
    target_hint = _clean_vision_text(payload.get("target_hint"), max_length=80)
    target_app = _clean_vision_text(payload.get("target_app"), max_length=120)
    accessibility_query = _clean_vision_text(
        payload.get("accessibility_query") or target_hint,
        max_length=240,
    )
    accessibility_cache_mode = (
        "prefer_cache"
        if str(payload.get("accessibility_cache_mode") or "").strip().lower() == "prefer_cache"
        else "refresh"
    )
    try:
        accessibility_result_limit = int(payload.get("accessibility_result_limit") or 10)
    except (TypeError, ValueError):
        accessibility_result_limit = 10
    accessibility_result_limit = max(1, min(16, accessibility_result_limit))
    try:
        accessibility_timeout_sec = float(
            payload.get("accessibility_timeout_sec") or 4.0
        )
    except (TypeError, ValueError):
        accessibility_timeout_sec = 4.0
    accessibility_timeout_sec = max(0.5, min(6.0, accessibility_timeout_sec))
    capture_scope = "application" if target_app else "desktop"
    target_surface_verified = bool(
        target_app
        and mode == ACTIVE_VISION_SURVEY_MODE
        and payload.get("target_surface_verified") is True
    )
    target_bounds = (
        payload.get("target_bounds")
        if isinstance(payload.get("target_bounds"), dict)
        else {}
    )
    requested_actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    if mode == ACTIVE_VISION_FOCUS_MODE and not requested_actions:
        requested_actions = [ACTIVE_VISION_FOCUS_MODE]
    if mode == ACTIVE_VISION_SURVEY_MODE:
        requested_actions = []
    if active_cfg.get("allowed_interaction") == "none":
        requested_actions = []
    click_policy = _clean_vision_text(payload.get("click_policy") or ACTIVE_VISION_CLICK_POLICY, max_length=80)
    try:
        settle_ms = int(
            payload["settle_ms"]
            if "settle_ms" in payload
            else active_cfg["settle_ms"]
        )
    except (TypeError, ValueError):
        settle_ms = int(active_cfg["settle_ms"])
    settle_ms = max(0, min(2000, settle_ms))
    accessibility_attempt: dict[str, Any] = {}
    settled_before_capture = False
    if (
        mode == ACTIVE_VISION_SURVEY_MODE
        and target_app
        and _is_macos(platform_name)
        and bool(payload.get("accessibility_enabled", True))
        and accessibility_provider is not None
    ):
        try:
            sleeper(max(0.0, min(2.0, settle_ms / 1000.0)))
            settled_before_capture = True
        except Exception:
            pass
        try:
            candidate = accessibility_provider(
                target_app,
                platform_name=platform_name or sys.platform,
                query=accessibility_query,
                cache_mode=accessibility_cache_mode,
                result_limit=accessibility_result_limit,
                timeout_sec=accessibility_timeout_sec,
            )
            accessibility_attempt = candidate if isinstance(candidate, dict) else {}
        except Exception as exc:
            accessibility_attempt = {
                "status": "error",
                "supported": True,
                "usable": False,
                "reason": _clean_vision_text(exc, max_length=240),
            }
        if (
            bool(accessibility_attempt.get("usable"))
            or payload.get("defer_visual_fallback") is True
        ):
            return _accessibility_frame_payload(
                accessibility_attempt,
                target_app=target_app,
                target_hint=target_hint,
            )
    desktop_targets = payload.get("desktop_targets") if isinstance(payload.get("desktop_targets"), list) else []
    target_candidates = payload.get("target_candidates") if isinstance(payload.get("target_candidates"), list) else []
    if target_surface_verified:
        # The desktop router already activated and verified this application.
        # Re-enumerating System Events, running apps, and the Dock cannot
        # improve an application-scoped survey and costs several seconds.
        discovery_errors = []
    else:
        discovery = discover_active_vision_target_candidates(
            desktop_targets=desktop_targets,
            desktop_targets_provider=desktop_targets_provider,
            dock_items_provider=dock_items_provider,
            running_apps_provider=running_apps_provider,
            target_candidates=target_candidates,
            target_hint=target_hint,
            platform_name=platform_name or sys.platform,
        )
        desktop_targets = discovery["desktop_targets"]
        target_candidates = discovery["target_candidates"]
        discovery_errors = discovery["discovery_errors"]
    was_visible = False
    try:
        was_visible = bool(window.isVisible()) if hasattr(window, "isVisible") else False
    except Exception:
        was_visible = False
    trace = {
        "enabled": True,
        "status": "success",
        "mode": mode,
        "target_id": target_id or (ACTIVE_VISION_SURVEY_MODE if mode == ACTIVE_VISION_SURVEY_MODE else ""),
        "target_hint": target_hint,
        "target_app": target_app,
        "capture_scope": capture_scope,
        "target_surface_verified": target_surface_verified,
        "click_policy": ACTIVE_VISION_CLICK_POLICY,
        "desktop_targets": list(desktop_targets or [])[:12],
        "target_candidates": list(target_candidates or [])[:12],
        "discovery_errors": list(discovery_errors or [])[:6],
        "focused_target": {},
        "selected_candidate": {},
        "action_trace": [],
        "click_point": {},
        "actions": [],
        "blocked_actions": [],
        "unknowns": [],
        "focus_result": {},
        "verify_result": {},
        "detail_frames_count": 0,
        "accessibility_attempt": {
            "status": str(accessibility_attempt.get("status") or ""),
            "supported": bool(accessibility_attempt.get("supported")),
            "usable": False,
            "reason": _clean_vision_text(accessibility_attempt.get("reason"), max_length=240),
        }
        if accessibility_attempt
        else {},
    }
    hidden_for_capture = bool(was_visible and hasattr(window, "hide"))
    try:
        if hidden_for_capture:
            window.hide()
            _flush_qt_window_state()
        if mode == ACTIVE_VISION_FOCUS_MODE:
            interaction_trace = interaction_runner(
                mode=mode,
                target_id=target_id,
                target_hint=target_hint,
                desktop_targets=desktop_targets,
                target_candidates=target_candidates,
                click_policy=click_policy,
                actions=requested_actions,
                platform_name=platform_name or sys.platform,
            )
            if isinstance(interaction_trace, dict):
                trace.update({key: value for key, value in interaction_trace.items() if key != "data_url"})
        # A visual application capture must not contain Ipet's own chat/worklog
        # window. Hiding the Qt host is asynchronous on macOS, so it needs its
        # own compositor settle even when an earlier AX attempt already waited.
        if hidden_for_capture or not settled_before_capture:
            try:
                sleeper(max(0.0, min(2.0, settle_ms / 1000.0)))
            except Exception:
                pass
        provider = screen_provider or _primary_screen
        screen = provider()
        if screen is None:
            raise RuntimeError("primary screen is unavailable")
        normalizer = payload_normalizer or (lambda value: value)
        default_encoder = default_frame_encoder or _screen_capture.capture_screen_frame_payload
        if frame_encoder is default_encoder and _is_macos(platform_name):
            frame_payload = frame_encoder(screen, active_capture_cfg, allow_qt_fallback=False)
        else:
            frame_payload = frame_encoder(screen, active_capture_cfg)
        frame_payload = normalizer(frame_payload)
        if capture_scope == "application" and target_bounds:
            frame_payload = _crop_application_frame_payload(
                frame_payload,
                target_bounds,
                active_capture_cfg,
            )
        if not target_candidates and not target_surface_verified:
            discovery = discover_active_vision_target_candidates(
                desktop_targets=desktop_targets,
                desktop_targets_provider=None,
                dock_items_provider=dock_items_provider,
                running_apps_provider=None,
                target_candidates=[],
                display_layout=frame_payload.get("display_layout") if isinstance(frame_payload.get("display_layout"), list) else None,
                target_hint=target_hint,
                platform_name=platform_name or sys.platform,
            )
            target_candidates = discovery["target_candidates"]
            trace["target_candidates"] = list(target_candidates or [])[:12]
        selected_candidate = trace.get("selected_candidate") if isinstance(trace.get("selected_candidate"), dict) else {}
        if not selected_candidate and target_id:
            selected_candidate = _find_active_target(desktop_targets, target_candidates, target_id)
            trace["selected_candidate"] = dict(selected_candidate) if selected_candidate else {}
        detail_frames = _active_detail_frames_from_capture(frame_payload, selected_candidate, active_capture_cfg) if selected_candidate else []
        vision_frames = [_vision_inline_frame_from_payload(frame_payload, purpose="main")]
        vision_frames.extend(_vision_inline_frame_from_payload(item, purpose="detail_crop") for item in detail_frames)
        vision_frames = [item for item in vision_frames if item]
        if vision_frames:
            frame_payload["vision_frames"] = vision_frames[:3]
        trace["detail_frames_count"] = len(detail_frames)
        trace["verify_result"] = {
            "status": "captured",
            "method": str(frame_payload.get("capture_backend") or "screen_capture")[:80],
            "frame_hash": str(frame_payload.get("frame_hash") or "")[:120],
        }
        frame_payload["active_observation"] = trace
        return frame_payload
    except Exception:
        trace["status"] = "error"
        raise
    finally:
        if hidden_for_capture and hasattr(window, "show"):
            try:
                window.show()
                _flush_qt_window_state()
            except Exception:
                pass
