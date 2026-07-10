from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from typing import Any


def _platform_name(platform_name: str | None = None) -> str:
    return str(platform_name or sys.platform).strip().lower()


def _is_macos(platform_name: str | None = None) -> bool:
    return _platform_name(platform_name) == "darwin"


def _screen_coordinate(value: Any, fallback: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


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
) -> dict[str, object]:
    if not _is_macos(platform_name):
        raise RuntimeError("Human Ops click is currently implemented through macOS desktop event APIs.")
    data = payload if isinstance(payload, dict) else {}
    x = _screen_coordinate(data.get("x"))
    y = _screen_coordinate(data.get("y"))
    label = str(data.get("label") or data.get("target") or "目标位置").strip() or "目标位置"
    event_error = ""
    if event_clicker is not None:
        try:
            event_clicker(x, y)
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
    return {"clicked": True, "x": x, "y": y, "label": label, "method": "system_events"}


def _applescript_string(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def execute_human_ops_type_text(
    payload: dict | None,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
) -> dict[str, object]:
    if not _is_macos(platform_name):
        raise RuntimeError("Human Ops text input is currently implemented through macOS desktop event APIs.")
    data = payload if isinstance(payload, dict) else {}
    text = str(data.get("text") or "")
    label = str(data.get("label") or data.get("target") or "输入位置").strip() or "输入位置"
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
        raise RuntimeError(detail)
    return {"typed": True, "text": text, "label": label, "method": "system_events"}


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
    return {"pressed": True, "key": normalized_key, "label": label, "method": "system_events"}


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
