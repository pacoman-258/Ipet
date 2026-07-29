from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from app import desktop_runtime as _desktop_runtime
from body import active_vision_targets as _active_targets


ACTIVE_VISION_MAX_CANDIDATES = _active_targets.ACTIVE_VISION_MAX_CANDIDATES
ACTIVE_VISION_WINDOW_ENUMERATION_TIMEOUT_SEC = 3.0
ACTIVE_VISION_RUNNING_APP_FALLBACK_TIMEOUT_SEC = 2.5
ACTIVE_VISION_DOCK_ITEM_ENUMERATION_TIMEOUT_SEC = 3.0


def _is_macos(platform_name: str | None = None) -> bool:
    return _desktop_runtime.is_macos(platform_name, sys_platform=sys.platform)


def _clean_vision_text(value: Any, *, max_length: int) -> str:
    return _active_targets.clean_vision_text(value, max_length=max_length)


def _osascript_args(script: str) -> list[str]:
    args = ["osascript"]
    for line in str(script or "").splitlines():
        stripped = line.rstrip()
        if stripped:
            args.extend(["-e", stripped])
    return args


def _active_target_id(app: str, title: str, bounds: dict[str, int], index: int) -> str:
    return _active_targets.active_target_id(app, title, bounds, index)


def _safe_focus_point(bounds: dict[str, int]) -> dict[str, int]:
    return _active_targets.safe_focus_point(bounds)


def _active_candidate_id(source: str, app: str, title: str, index: int) -> str:
    return _active_targets.active_candidate_id(source, app, title, index)


def _sanitize_active_candidate(candidate: dict, *, index: int = 0, source: str = "") -> dict:
    return _active_targets.sanitize_active_candidate(candidate, index=index, source=source)


def _parse_macos_desktop_targets(output: str) -> list[dict]:
    targets: list[dict] = []
    for index, line in enumerate(str(output or "").splitlines()):
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        app = _clean_vision_text(parts[0], max_length=120)
        title = _clean_vision_text(parts[1], max_length=200)
        try:
            x, y, width, height = [int(float(part or 0)) for part in parts[2:6]]
        except Exception:
            continue
        if not app or width < 80 or height < 40:
            continue
        frontmost = str(parts[6]).strip().lower() == "true"
        minimized = str(parts[7]).strip().lower() == "true"
        bounds = {"x": x, "y": y, "width": width, "height": height}
        targets.append(
            {
                "target_id": _active_target_id(app, title, bounds, index),
                "app": app,
                "title": title,
                "bounds": bounds,
                "frontmost": frontmost,
                "minimized": minimized,
                "focus_point": _safe_focus_point(bounds),
            }
        )
    return targets[:12]


def discover_macos_active_vision_desktop_targets(
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
) -> tuple[list[dict], list[str]]:
    if not _is_macos(platform_name):
        return [], []
    script = r"""
tell application "System Events"
    set windowRows to {}
    repeat with proc in application processes
        try
            if background only of proc is false then
                set appName to name of proc as text
                set isFront to frontmost of proc
                repeat with w in windows of proc
                    try
                        set wTitle to name of w as text
                        set wPos to position of w
                        set wSize to size of w
                        set wMinimized to false
                        try
                            set wMinimized to value of attribute "AXMinimized" of w
                        end try
                        set rowText to appName & tab & wTitle & tab & (item 1 of wPos as integer) & tab & (item 2 of wPos as integer) & tab & (item 1 of wSize as integer) & tab & (item 2 of wSize as integer) & tab & (isFront as text) & tab & (wMinimized as text)
                        set end of windowRows to rowText
                    end try
                end repeat
            end if
        end try
    end repeat
    set AppleScript's text item delimiters to linefeed
    set joinedRows to windowRows as text
    set AppleScript's text item delimiters to ""
    return joinedRows
end tell
""".strip()
    try:
        result = runner(
            _osascript_args(script),
            capture_output=True,
            text=True,
            timeout=ACTIVE_VISION_WINDOW_ENUMERATION_TIMEOUT_SEC,
            check=False,
        )
    except Exception as exc:
        return [], [f"System Events window enumeration failed: {exc}"]
    if getattr(result, "returncode", 1) != 0:
        detail = _clean_vision_text(getattr(result, "stderr", "") or getattr(result, "stdout", ""), max_length=180)
        return [], [f"System Events window enumeration failed: {detail or 'osascript failed'}"]
    return _parse_macos_desktop_targets(str(getattr(result, "stdout", "") or "")), []


def enumerate_active_vision_desktop_targets(
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
) -> list[dict]:
    targets, _errors = discover_macos_active_vision_desktop_targets(platform_name=platform_name, runner=runner)
    return targets


def _parse_macos_dock_item_candidates(output: str, *, start_index: int = 0) -> list[dict]:
    candidates: list[dict] = []
    for line in str(output or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        name = _clean_vision_text(parts[0], max_length=120)
        if not name or name.lower() == "missing value":
            continue
        try:
            x, y, width, height = [int(float(part or 0)) for part in parts[1:5]]
        except Exception:
            continue
        if width < 8 or height < 8:
            continue
        bounds = {"x": x, "y": y, "width": width, "height": height}
        candidates.append(
            _sanitize_active_candidate(
                {
                    "target_id": _active_candidate_id("dock_item", name, "Dock", start_index + len(candidates)),
                    "source": "dock_item",
                    "app": name,
                    "title": name,
                    "bounds": bounds,
                    "focus_point": {"x": x + width // 2, "y": y + height // 2},
                    "focusable": True,
                    "score": 88.0,
                },
                index=start_index + len(candidates),
                source="dock_item",
            )
        )
    return candidates


def enumerate_macos_dock_item_candidates(
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    include_errors: bool = False,
) -> list[dict] | tuple[list[dict], list[str]]:
    if not _is_macos(platform_name):
        return ([], []) if include_errors else []
    script = r"""
tell application "System Events"
    set dockRows to {}
    tell process "Dock"
        try
            repeat with dockItem in UI elements of list 1
                try
                    set itemName to name of dockItem as text
                    set itemPos to position of dockItem
                    set itemSize to size of dockItem
                    if itemName is not "missing value" then
                        set rowText to itemName & tab & (item 1 of itemPos as integer) & tab & (item 2 of itemPos as integer) & tab & (item 1 of itemSize as integer) & tab & (item 2 of itemSize as integer)
                        set end of dockRows to rowText
                    end if
                end try
            end repeat
        end try
    end tell
    set AppleScript's text item delimiters to linefeed
    set joinedRows to dockRows as text
    set AppleScript's text item delimiters to ""
    return joinedRows
end tell
""".strip()
    errors: list[str] = []
    try:
        result = runner(
            _osascript_args(script),
            capture_output=True,
            text=True,
            timeout=ACTIVE_VISION_DOCK_ITEM_ENUMERATION_TIMEOUT_SEC,
            check=False,
        )
    except Exception as exc:
        errors.append(f"System Events Dock item enumeration failed: {exc}")
        return ([], errors) if include_errors else []
    if getattr(result, "returncode", 1) != 0:
        detail = _clean_vision_text(getattr(result, "stderr", "") or getattr(result, "stdout", ""), max_length=180)
        errors.append(f"System Events Dock item enumeration failed: {detail or 'osascript failed'}")
        return ([], errors) if include_errors else []
    candidates = _parse_macos_dock_item_candidates(str(getattr(result, "stdout", "") or ""))
    return (candidates, errors) if include_errors else candidates


_ACTIVE_VISION_SYSTEM_PROCESS_NAMES = {
    "airportd",
    "backupd",
    "accessibilityuiserver",
    "accessoryupdaterd",
    "amfid",
    "cfprefsd",
    "controlcenter",
    "configd",
    "coreaudiod",
    "corelocationagent",
    "coreservicesuiagent",
    "corespeechd_system",
    "dasd",
    "diskarbitrationd",
    "distnoted",
    "dock",
    "duetexpertd",
    "endpointsecurityd",
    "fseventsd",
    "iomfb_bics_daemon",
    "keybagd",
    "kernel_task",
    "launchd",
    "liquiddetectiond",
    "logd",
    "loginwindow",
    "lsd",
    "mediaremoted",
    "mds",
    "mdworker",
    "notifyd",
    "osascript",
    "powerd",
    "reportcrash",
    "remoted",
    "runningboardd",
    "secd",
    "smd",
    "software update",
    "softwareupdated",
    "sociallayerd",
    "systemstats",
    "systemuiserver",
    "syslogd",
    "tccd",
    "trustd",
    "uarpassetmanagerd",
    "usereventagent",
    "usbmuxd",
    "wallpaperagent",
    "windowserver",
    "windowmanager",
    "xprotect",
}

_ACTIVE_VISION_LOW_PRIORITY_APP_NAMES = {
    "activity monitor",
    "codex",
    "console",
    "finder",
    "system settings",
    "terminal",
}


def _is_active_vision_system_process_name(name: str) -> bool:
    cleaned = _clean_vision_text(name, max_length=120)
    if not cleaned or cleaned.startswith("."):
        return True
    return cleaned.lower() in _ACTIVE_VISION_SYSTEM_PROCESS_NAMES


def _active_vision_name_key(name: str) -> str:
    return _active_targets.active_vision_name_key(name)


def _app_bundle_info_from_process_path(process_path: str) -> dict[str, str]:
    raw_path = str(process_path or "").strip()
    if not raw_path:
        return {}
    parts = Path(raw_path).parts
    lowered_parts = [part.lower() for part in parts]
    blocked_roots = {"privateframeworks"}
    for index, part in enumerate(parts):
        if part.lower().endswith(".app") and len(part) > 4:
            ancestors = set(lowered_parts[:index])
            if ancestors & blocked_roots:
                return {}
            app_root = lowered_parts[index - 1] if index > 0 else ""
            is_system_app = index >= 2 and lowered_parts[index - 2 : index] == ["system", "applications"]
            is_core_services_app = index >= 3 and lowered_parts[index - 3 : index] == [
                "system",
                "library",
                "coreservices",
            ]
            is_user_or_global_app = app_root == "applications" and not is_system_app
            if not (is_user_or_global_app or is_system_app or is_core_services_app):
                return {}
            location = "user_app" if is_user_or_global_app else "system_app"
            if is_core_services_app:
                location = "core_services_app"
            return {
                "name": _clean_vision_text(part[:-4], max_length=120),
                "location": location,
            }
    return {}


def _app_bundle_name_from_process_path(process_path: str) -> str:
    return _app_bundle_info_from_process_path(process_path).get("name", "")


def _running_app_process_path_score(name: str, location: str) -> float:
    base = _running_app_candidate_score(name, from_process_path=True)
    if location == "user_app":
        return base + 8.0
    if location == "core_services_app":
        return base - 18.0
    if location == "system_app":
        return base - 24.0
    return base


def _running_app_candidate_score(name: str, *, frontmost: bool = False, from_process_path: bool = False) -> float:
    if _active_vision_name_key(name) in _ACTIVE_VISION_LOW_PRIORITY_APP_NAMES:
        return 45.0 if frontmost else 35.0
    if frontmost:
        return 75.0
    return 65.0 if from_process_path else 70.0


def _parse_system_events_running_app_candidates(
    output: str,
    *,
    start_index: int = 0,
    limit: int = ACTIVE_VISION_MAX_CANDIDATES,
) -> list[dict]:
    result_limit = max(1, min(256, int(limit)))
    candidates: list[dict] = []
    for line in str(output or "").splitlines():
        parts = line.split("\t")
        name = _clean_vision_text(parts[0] if parts else "", max_length=120)
        if _is_active_vision_system_process_name(name):
            continue
        bundle_id = _clean_vision_text(parts[1] if len(parts) > 1 else "", max_length=160)
        pid = _clean_vision_text(parts[2] if len(parts) > 2 else "", max_length=80)
        frontmost = str(parts[3] if len(parts) > 3 else "").strip().lower() == "true"
        candidate = {
            "target_id": _active_candidate_id("running_app", bundle_id or name, name, start_index + len(candidates)),
            "source": "running_app",
            "app": name,
            "title": name,
            "frontmost": frontmost,
            "focusable": True,
            "score": _running_app_candidate_score(name, frontmost=frontmost),
        }
        if bundle_id:
            candidate["bundle_id"] = bundle_id
        if pid:
            candidate["pid"] = pid
        candidates.append(_sanitize_active_candidate(candidate, index=start_index + len(candidates), source="running_app"))
        if len(candidates) >= result_limit:
            break
    return candidates


def _system_events_running_app_candidates(
    *,
    runner=subprocess.run,
    limit: int = ACTIVE_VISION_MAX_CANDIDATES,
) -> tuple[list[dict], list[str]]:
    script = r"""
tell application "System Events"
    set appRows to {}
    repeat with proc in (application processes whose background only is false)
        try
            set appName to name of proc as text
            set bundleId to ""
            set unixId to ""
            set isFront to frontmost of proc
            try
                set bundleId to bundle identifier of proc as text
            end try
            try
                set unixId to unix id of proc as text
            end try
            set rowText to appName & tab & bundleId & tab & unixId & tab & (isFront as text)
            set end of appRows to rowText
        end try
    end repeat
    set AppleScript's text item delimiters to linefeed
    set joinedRows to appRows as text
    set AppleScript's text item delimiters to ""
    return joinedRows
end tell
""".strip()
    try:
        result = runner(
            _osascript_args(script),
            capture_output=True,
            text=True,
            timeout=ACTIVE_VISION_RUNNING_APP_FALLBACK_TIMEOUT_SEC,
            check=False,
        )
    except Exception as exc:
        return [], [f"System Events running app fallback failed: {exc}"]
    if getattr(result, "returncode", 1) != 0:
        detail = _clean_vision_text(getattr(result, "stderr", "") or getattr(result, "stdout", ""), max_length=180)
        return [], [f"System Events running app fallback failed: {detail or 'osascript failed'}"]
    return _parse_system_events_running_app_candidates(
        str(getattr(result, "stdout", "") or ""),
        limit=limit,
    ), []


def enumerate_active_vision_running_app_candidates(
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    include_errors: bool = False,
    limit: int = ACTIVE_VISION_MAX_CANDIDATES,
    include_hidden: bool = False,
) -> list[dict] | tuple[list[dict], list[str]]:
    if not _is_macos(platform_name):
        return ([], []) if include_errors else []
    result_limit = max(1, min(256, int(limit)))
    candidates: list[dict] = []
    errors: list[str] = []
    try:
        import AppKit  # type: ignore

        workspace = AppKit.NSWorkspace.sharedWorkspace()
        for index, app in enumerate(list(workspace.runningApplications() or [])):
            try:
                name = _clean_vision_text(app.localizedName(), max_length=120)
                bundle_id = _clean_vision_text(app.bundleIdentifier(), max_length=160)
                pid = str(int(app.processIdentifier()))
                frontmost = bool(app.isActive())
                hidden = bool(app.isHidden())
                activation_policy = int(app.activationPolicy())
            except Exception:
                continue
            if not name or (hidden and not include_hidden) or activation_policy != 0:
                continue
            candidates.append(
                _sanitize_active_candidate(
                    {
                        "target_id": _active_candidate_id("running_app", bundle_id or name, name, index),
                        "source": "running_app",
                        "app": name,
                        "title": name,
                        "bundle_id": bundle_id,
                        "pid": pid,
                        "frontmost": frontmost,
                        "focusable": True,
                        "score": _running_app_candidate_score(name, frontmost=frontmost),
                    },
                    index=index,
                    source="running_app",
                )
            )
            if len(candidates) >= result_limit:
                return (candidates, errors) if include_errors else candidates
    except Exception as exc:
        errors.append(f"AppKit running app enumeration unavailable: {exc}")
    if not candidates:
        system_events_candidates, system_events_errors = _system_events_running_app_candidates(
            runner=runner,
            limit=result_limit,
        )
        errors.extend(system_events_errors)
        if system_events_candidates:
            capped = system_events_candidates[:result_limit]
            return (capped, errors) if include_errors else capped
    try:
        result = runner(["/bin/ps", "-axo", "comm="], capture_output=True, text=True, timeout=0.8, check=False)
    except Exception:
        capped = candidates[:result_limit]
        return (capped, errors) if include_errors else capped
    if getattr(result, "returncode", 1) != 0:
        detail = _clean_vision_text(getattr(result, "stderr", "") or getattr(result, "stdout", ""), max_length=180)
        errors.append(f"running process fallback failed: {detail or 'ps failed'}")
        capped = candidates[:result_limit]
        return (capped, errors) if include_errors else capped
    seen: set[str] = {_active_vision_name_key(str(item.get("app") or "")) for item in candidates}
    ps_lines = str(getattr(result, "stdout", "") or "").splitlines()
    app_bundle_candidates: list[dict] = []
    for line in ps_lines:
        app_info = _app_bundle_info_from_process_path(line)
        app_name = app_info.get("name", "")
        key = _active_vision_name_key(app_name)
        if not app_name or key in seen:
            continue
        if _is_active_vision_system_process_name(app_name):
            continue
        seen.add(key)
        app_bundle_candidates.append(
            _sanitize_active_candidate(
                {
                    "target_id": _active_candidate_id("running_app", app_name, app_name, len(candidates) + len(app_bundle_candidates)),
                    "source": "running_app",
                    "app": app_name,
                    "title": app_name,
                    "focusable": True,
                    "score": _running_app_process_path_score(app_name, app_info.get("location", "")),
                },
                source="running_app",
            )
        )
        if len(app_bundle_candidates) >= max(80, result_limit):
            break
    if app_bundle_candidates:
        combined = sorted(
            candidates + app_bundle_candidates,
            key=lambda value: float(value.get("score") or 0.0),
            reverse=True,
        )[:result_limit]
        return (combined, errors) if include_errors else combined
    for line in str(getattr(result, "stdout", "") or "").splitlines():
        name = Path(line.strip()).name
        key = _active_vision_name_key(name)
        if not name or key in seen or name.startswith("."):
            continue
        if _is_active_vision_system_process_name(name):
            continue
        seen.add(key)
        candidates.append(
            _sanitize_active_candidate(
                {
                    "target_id": _active_candidate_id("running_process", name, name, len(candidates)),
                    "source": "running_process",
                    "app": name,
                    "title": name,
                    "focusable": False,
                    "score": 25,
                },
                source="running_process",
            )
        )
        if len(candidates) >= result_limit:
            break
    capped = candidates[:result_limit]
    return (capped, errors) if include_errors else capped
