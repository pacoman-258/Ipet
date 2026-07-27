from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from body import macos_accessibility as _macos_accessibility


def _platform_name(platform_name: str | None = None) -> str:
    return str(platform_name or sys.platform).strip().lower()


def _is_macos(platform_name: str | None = None) -> bool:
    return _platform_name(platform_name) == "darwin"


def _screen_coordinate(value: Any, fallback: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


def _application_name_key(value: object) -> str:
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def _installed_macos_applications(*, runner=subprocess.run) -> list[dict[str, str]]:
    try:
        result = runner(
            ["/usr/bin/mdfind", "kMDItemContentType == 'com.apple.application-bundle'"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except Exception:
        return []
    if getattr(result, "returncode", 1) != 0:
        return []
    applications: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_path in str(getattr(result, "stdout", "") or "").splitlines():
        path = Path(raw_path.strip())
        if path.suffix.casefold() != ".app" or any(parent.suffix.casefold() == ".app" for parent in path.parents):
            continue
        key = str(path).casefold()
        if not _application_name_key(path.stem) or key in seen:
            continue
        seen.add(key)
        applications.append({"name": path.stem, "path": str(path)})
    return sorted(
        applications,
        key=lambda item: (
            0 if str(item["path"]).startswith("/Applications/") else 1,
            str(item["path"]).casefold(),
        ),
    )


def _resolve_installed_application(app_name: str, applications: list[dict[str, str]]) -> dict[str, str]:
    target_key = _application_name_key(app_name)
    exact = [item for item in applications if _application_name_key(item.get("name")) == target_key]
    return exact[0] if exact else {}


def execute_human_ops_launch_app(
    payload: dict | None,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
) -> dict[str, object]:
    if not _is_macos(platform_name):
        raise RuntimeError("Human Ops app launch is currently implemented through macOS Launch Services.")
    data = payload if isinstance(payload, dict) else {}
    app_name = str(data.get("app") or data.get("name") or data.get("label") or "").strip()
    if not app_name:
        raise RuntimeError("Human Ops app launch requires an application name.")
    ax_profile = _macos_accessibility.resolve_macos_ax_app(app_name)
    launch_name = str(ax_profile.get("launch_name") or ax_profile.get("display_name") or app_name).strip()
    applications = _installed_macos_applications(runner=runner)
    resolved = _resolve_installed_application(launch_name, applications)
    command = ["/usr/bin/open", resolved["path"]] if resolved else ["/usr/bin/open", "-a", launch_name]
    result = runner(command, capture_output=True, text=True, timeout=5, check=False)
    if getattr(result, "returncode", 1) != 0:
        detail = str(getattr(result, "stderr", "") or getattr(result, "stdout", "") or "open failed").strip()
        raise RuntimeError(f"macOS could not open {app_name}: {detail}")
    _macos_accessibility.invalidate_macos_accessibility_cache(app_name)
    return {
        "launched": True,
        "app": str(resolved.get("name") or launch_name),
        "application_path": str(resolved.get("path") or ""),
        "application_count": len(applications),
        "method": "launch_services",
    }


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


def _post_core_graphics_click(x: int, y: int) -> None:
    app_services = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    preflight = getattr(app_services, "CGPreflightPostEventAccess", None)
    if preflight is not None:
        preflight.argtypes = []
        preflight.restype = ctypes.c_bool
        if not bool(preflight()):
            raise RuntimeError("macOS event posting permission is not granted.")

    app_services.CGEventCreateMouseEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        _CGPoint,
        ctypes.c_uint32,
    ]
    app_services.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    app_services.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    app_services.CFRelease.argtypes = [ctypes.c_void_p]

    point = _CGPoint(float(x), float(y))
    for event_type in (5, 1, 2):  # moved, left mouse down, left mouse up
        event = app_services.CGEventCreateMouseEvent(None, event_type, point, 0)
        if not event:
            raise RuntimeError("CoreGraphics could not create a mouse event.")
        try:
            app_services.CGEventPost(0, event)
        finally:
            app_services.CFRelease(event)
        if event_type == 1:
            time.sleep(0.03)


def execute_human_ops_click(
    payload: dict | None,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    event_clicker=_post_core_graphics_click,
    accessibility_actioner=_macos_accessibility.perform_macos_accessibility_action,
) -> dict[str, object]:
    if not _is_macos(platform_name):
        raise RuntimeError("Human Ops click is currently implemented through macOS desktop event APIs.")
    data = payload if isinstance(payload, dict) else {}
    ax_ref = data.get("ax_ref") if isinstance(data.get("ax_ref"), dict) else {}
    target_app = str(data.get("target_app") or "").strip()
    label = str(data.get("label") or data.get("target") or "目标位置").strip() or "目标位置"
    if ax_ref:
        if not target_app:
            raise RuntimeError("Human Ops semantic click requires target_app.")
        result = accessibility_actioner(
            target_app,
            ax_ref,
            operation="press",
            platform_name=platform_name,
            runner=runner,
        )
        _macos_accessibility.invalidate_macos_accessibility_cache(target_app)
        return {"clicked": True, "label": label, **result}
    x = _screen_coordinate(data.get("x"))
    y = _screen_coordinate(data.get("y"))
    event_error = ""
    if event_clicker is not None:
        try:
            event_clicker(x, y)
            _macos_accessibility.invalidate_macos_accessibility_cache(target_app)
            return {"clicked": True, "x": x, "y": y, "label": label, "method": "core_graphics"}
        except Exception as exc:
            event_error = str(exc).strip()

    script = f'tell application "System Events" to click at {{{x}, {y}}}'
    result = runner(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if getattr(result, "returncode", 1) != 0:
        detail = str(getattr(result, "stderr", "") or getattr(result, "stdout", "") or "osascript failed").strip()
        if event_error:
            detail = f"CoreGraphics click failed: {event_error}; System Events click failed: {detail}"
        raise RuntimeError(detail)
    _macos_accessibility.invalidate_macos_accessibility_cache(target_app)
    return {"clicked": True, "x": x, "y": y, "label": label, "method": "system_events"}


def _applescript_string(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _post_core_graphics_text(text: str) -> None:
    value = str(text or "")
    if not value:
        return
    app_services = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    preflight = getattr(app_services, "CGPreflightPostEventAccess", None)
    if preflight is not None:
        preflight.argtypes = []
        preflight.restype = ctypes.c_bool
        if not bool(preflight()):
            raise RuntimeError("macOS event posting permission is not granted.")

    encoded = value.encode("utf-16-le")
    unit_count = len(encoded) // 2
    units = (ctypes.c_uint16 * unit_count).from_buffer_copy(encoded)
    app_services.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    app_services.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    app_services.CGEventKeyboardSetUnicodeString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_uint16),
    ]
    app_services.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    app_services.CFRelease.argtypes = [ctypes.c_void_p]

    key_down = app_services.CGEventCreateKeyboardEvent(None, 0, True)
    key_up = app_services.CGEventCreateKeyboardEvent(None, 0, False)
    if not key_down or not key_up:
        if key_down:
            app_services.CFRelease(key_down)
        if key_up:
            app_services.CFRelease(key_up)
        raise RuntimeError("CoreGraphics could not create a keyboard event.")
    try:
        app_services.CGEventKeyboardSetUnicodeString(key_down, unit_count, units)
        app_services.CGEventPost(0, key_down)
        app_services.CGEventPost(0, key_up)
    finally:
        app_services.CFRelease(key_down)
        app_services.CFRelease(key_up)


def execute_human_ops_type_text(
    payload: dict | None,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    event_typer=None,
    accessibility_actioner=_macos_accessibility.perform_macos_accessibility_action,
) -> dict[str, object]:
    if not _is_macos(platform_name):
        raise RuntimeError("Human Ops text input is currently implemented through macOS desktop event APIs.")
    data = payload if isinstance(payload, dict) else {}
    text = str(data.get("text") or "")
    label = str(data.get("label") or data.get("target") or "输入位置").strip() or "输入位置"
    ax_ref = data.get("ax_ref") if isinstance(data.get("ax_ref"), dict) else {}
    target_app = str(data.get("target_app") or "").strip()
    accessibility_result: dict[str, object] = {}
    if ax_ref:
        if not target_app:
            raise RuntimeError("Human Ops semantic text input requires target_app.")
        accessibility_result = accessibility_actioner(
            target_app,
            ax_ref,
            operation="focus",
            platform_name=platform_name,
            runner=runner,
        )
    native_typer = event_typer or _post_core_graphics_text
    try:
        native_typer(text)
        method = "macos_accessibility_focus+core_graphics_unicode" if accessibility_result else "core_graphics_unicode"
        _macos_accessibility.invalidate_macos_accessibility_cache(target_app)
        return {
            "typed": True,
            "text": text,
            "label": label,
            **accessibility_result,
            "method": method,
        }
    except Exception as exc:
        event_error = str(exc).strip()
    script = f'tell application "System Events" to keystroke {_applescript_string(text)}'
    result = runner(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if getattr(result, "returncode", 1) != 0:
        detail = str(getattr(result, "stderr", "") or getattr(result, "stdout", "") or "osascript failed").strip()
        if event_error:
            detail = f"CoreGraphics text input failed: {event_error}; System Events input failed: {detail}"
        raise RuntimeError(detail)
    method = "macos_accessibility_focus+system_events" if accessibility_result else "system_events"
    _macos_accessibility.invalidate_macos_accessibility_cache(target_app)
    return {
        "typed": True,
        "text": text,
        "label": label,
        **accessibility_result,
        "method": method,
    }


def execute_human_ops_key_press(
    payload: dict | None,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
) -> dict[str, object]:
    if not _is_macos(platform_name):
        raise RuntimeError("Human Ops key press is currently implemented through macOS desktop event APIs.")
    data = payload if isinstance(payload, dict) else {}
    raw_key = str(data.get("key") or "enter").strip().lower() or "enter"
    label = str(data.get("label") or data.get("target") or "当前焦点").strip() or "当前焦点"
    target_app = str(data.get("target_app") or "").strip()
    key_codes = {
        "enter": 36,
        "return": 36,
    }
    if raw_key not in key_codes:
        raise RuntimeError(f"unsupported key press: {raw_key}")
    script = f'tell application "System Events" to key code {key_codes[raw_key]}'
    result = runner(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if getattr(result, "returncode", 1) != 0:
        detail = str(getattr(result, "stderr", "") or getattr(result, "stdout", "") or "osascript failed").strip()
        raise RuntimeError(detail)
    normalized_key = "enter" if raw_key == "return" else raw_key
    _macos_accessibility.invalidate_macos_accessibility_cache(target_app)
    return {"pressed": True, "key": normalized_key, "label": label, "method": "system_events"}


def frontmost_macos_application(*, runner=subprocess.run) -> str:
    script = 'tell application "System Events" to get name of first application process whose frontmost is true'
    result = runner(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=1.0,
        check=False,
    )
    if getattr(result, "returncode", 1) != 0:
        return ""
    return str(getattr(result, "stdout", "") or "").strip()


def _application_names_match(left: object, right: object) -> bool:
    left_key = _application_name_key(left)
    right_key = _application_name_key(right)
    return bool(
        left_key
        and right_key
        and (
            left_key == right_key
            or left_key in right_key
            or right_key in left_key
        )
    )


def focus_macos_application(
    payload: dict | None,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    sleeper=time.sleep,
    frontmost_provider=None,
    timeout_sec: float = 4.0,
) -> dict[str, object]:
    if not _is_macos(platform_name):
        raise RuntimeError("Target application focus is currently implemented through macOS Launch Services.")
    data = payload if isinstance(payload, dict) else {}
    target_app = str(data.get("target_app") or data.get("app") or data.get("name") or "").strip()
    if not target_app:
        raise RuntimeError("Human Ops action requires target_app before changing application focus.")
    ax_profile = _macos_accessibility.resolve_macos_ax_app(target_app)
    launch_name = str(ax_profile.get("launch_name") or ax_profile.get("display_name") or target_app).strip()
    get_frontmost = frontmost_provider or (lambda: frontmost_macos_application(runner=runner))
    previous_frontmost = str(get_frontmost() or "").strip()
    result = runner(
        ["/usr/bin/open", "-a", launch_name],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if getattr(result, "returncode", 1) != 0:
        detail = str(getattr(result, "stderr", "") or getattr(result, "stdout", "") or "open failed").strip()
        raise RuntimeError(f"macOS could not focus {target_app}: {detail}")
    deadline = time.monotonic() + max(0.2, float(timeout_sec))
    target_keys = {
        _application_name_key(value)
        for value in (
            target_app,
            launch_name,
            ax_profile.get("display_name"),
            *(ax_profile.get("aliases") or ()),
        )
        if _application_name_key(value)
    }
    last_frontmost = ""
    while time.monotonic() < deadline:
        last_frontmost = str(get_frontmost() or "").strip()
        frontmost_key = _application_name_key(last_frontmost)
        if frontmost_key and any(
            target_key == frontmost_key or target_key in frontmost_key or frontmost_key in target_key
            for target_key in target_keys
        ):
            result = {
                "focused": True,
                "target_app": target_app,
                "frontmost_app": last_frontmost,
                "method": "launch_services",
            }
            if (
                previous_frontmost
                and not _application_names_match(
                    previous_frontmost,
                    target_app,
                )
            ):
                result["previous_frontmost_app"] = previous_frontmost
            return result
        sleeper(0.08)
    raise RuntimeError(
        f"macOS foreground verification failed: expected {target_app}, got {last_frontmost or 'unknown'}"
    )


def restore_macos_application_focus(
    focus_result: dict | None,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    frontmost_provider=None,
) -> dict[str, object]:
    if not _is_macos(platform_name):
        return {"restored": False, "reason": "unsupported_platform"}
    data = focus_result if isinstance(focus_result, dict) else {}
    target_app = str(data.get("target_app") or "").strip()
    previous_app = str(data.get("previous_frontmost_app") or "").strip()
    if not target_app or not previous_app:
        return {"restored": False, "reason": "no_previous_application"}
    get_frontmost = frontmost_provider or (
        lambda: frontmost_macos_application(runner=runner)
    )
    current_app = str(get_frontmost() or "").strip()
    if not _application_names_match(current_app, target_app):
        return {
            "restored": False,
            "reason": "foreground_changed_by_user",
            "frontmost_app": current_app,
        }
    profile = _macos_accessibility.resolve_macos_ax_app(previous_app)
    launch_name = str(
        profile.get("launch_name")
        or profile.get("display_name")
        or previous_app
    ).strip()
    result = runner(
        ["/usr/bin/open", "-a", launch_name],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    restored = getattr(result, "returncode", 1) == 0
    return {
        "restored": restored,
        "reason": "restored" if restored else "activation_failed",
        "target_app": previous_app,
        "method": "launch_services" if restored else "",
    }


def execute_human_ops_native_approval(
    payload: dict | None,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
) -> dict[str, object]:
    if not _is_macos(platform_name):
        raise RuntimeError("Native Human Ops approval is currently implemented through macOS dialogs.")
    data = payload if isinstance(payload, dict) else {}
    title = str(data.get("title") or "Ipet 需要你的批准").strip()[:120]
    message = str(data.get("message") or data.get("summary") or "是否批准这一步操作？").strip()[:800]
    if data.get("notice_only") is True:
        script = (
            f"display notification {_applescript_string(message)} "
            f"with title {_applescript_string(title)} sound name \"default\""
        )
        result = runner(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        output = str(getattr(result, "stdout", "") or "").strip()
        error = str(getattr(result, "stderr", "") or "").strip()
        if getattr(result, "returncode", 1) != 0:
            raise RuntimeError(error or output or "macOS action notification failed")
        return {
            "notified": True,
            "method": "macos_notification",
            "task_id": str(data.get("task_id") or "").strip(),
        }
    timeout_sec = max(30, min(600, _screen_coordinate(data.get("timeout_sec"), fallback=300)))
    script = (
        f"display dialog {_applescript_string(message)} "
        f"with title {_applescript_string(title)} "
        f'buttons {{"拒绝", "批准"}} default button "批准" with icon caution giving up after {timeout_sec}'
    )
    result = runner(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout_sec + 5,
        check=False,
    )
    output = str(getattr(result, "stdout", "") or "").strip()
    error = str(getattr(result, "stderr", "") or "").strip()
    if getattr(result, "returncode", 1) != 0:
        if "-128" in error or "canceled" in error.lower() or "cancelled" in error.lower():
            return {"approved": False, "reason": "cancelled", "method": "macos_dialog"}
        raise RuntimeError(error or output or "macOS approval dialog failed")
    approved = "button returned:批准" in output and "gave up:true" not in output.lower()
    return {
        "approved": approved,
        "reason": "approved" if approved else "rejected_or_timed_out",
        "method": "macos_dialog",
    }


def process_pending_qt_events(*, qapplication=None) -> None:
    if qapplication is None:
        return
    try:
        app = qapplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass


def hide_window_for_desktop_click(window, *, qapplication=None) -> bool:
    try:
        was_visible = bool(window.isVisible()) if hasattr(window, "isVisible") else False
    except Exception:
        was_visible = False
    if not was_visible or not hasattr(window, "hide"):
        return False
    try:
        window.hide()
        process_pending_qt_events(qapplication=qapplication)
        return True
    except Exception:
        return False


def restore_window_after_desktop_click(window, was_hidden: bool, *, qapplication=None) -> None:
    if not was_hidden or not hasattr(window, "show"):
        return
    try:
        window.show()
        process_pending_qt_events(qapplication=qapplication)
    except Exception:
        pass
