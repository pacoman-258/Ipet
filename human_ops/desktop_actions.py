from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from body import macos_accessibility as _macos_accessibility


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


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
    bundle_id = str(ax_profile.get("bundle_id") or "").strip()
    if getattr(result, "returncode", 1) != 0 and bundle_id:
        command = ["/usr/bin/open", "-b", bundle_id]
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
            geometry_clicker=event_clicker,
        )
        if not bool(result.get("already_satisfied")):
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


def _chat_app_id(target_app: object) -> str:
    profile = _macos_accessibility.resolve_macos_ax_app(target_app)
    app_id = str(profile.get("app_id") or "") if isinstance(profile, dict) else ""
    return app_id if app_id in {"qq", "wechat"} else ""


def _is_signed_chat_search_input(ax_ref: object) -> bool:
    reference = ax_ref if isinstance(ax_ref, dict) else {}
    return str(reference.get("input_kind") or "").strip() == "search_field"


def _core_graphics_event_api():
    app_services = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    preflight = getattr(app_services, "CGPreflightPostEventAccess", None)
    if preflight is not None:
        preflight.argtypes = []
        preflight.restype = ctypes.c_bool
        if not bool(preflight()):
            raise RuntimeError("macOS event posting permission is not granted.")
    app_services.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    app_services.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    app_services.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    post_to_pid = getattr(app_services, "CGEventPostToPid", None)
    if post_to_pid is not None:
        post_to_pid.argtypes = [ctypes.c_int, ctypes.c_void_p]
    app_services.CFRelease.argtypes = [ctypes.c_void_p]
    return app_services, post_to_pid


def _post_keyboard_events(
    app_services,
    key_down: object,
    key_up: object,
    *,
    target_pid: int = 0,
) -> None:
    post_to_pid = getattr(app_services, "CGEventPostToPid", None)
    if target_pid > 0:
        if post_to_pid is None:
            raise RuntimeError("Targeted macOS keyboard delivery is unavailable.")
        post_to_pid(int(target_pid), key_down)
        post_to_pid(int(target_pid), key_up)
        return
    app_services.CGEventPost(0, key_down)
    app_services.CGEventPost(0, key_up)


def _post_core_graphics_text(text: str, *, target_pid: int = 0) -> None:
    value = str(text or "")
    if not value:
        return
    app_services, _post_to_pid = _core_graphics_event_api()

    encoded = value.encode("utf-16-le")
    unit_count = len(encoded) // 2
    units = (ctypes.c_uint16 * unit_count).from_buffer_copy(encoded)
    app_services.CGEventKeyboardSetUnicodeString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_uint16),
    ]
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
        _post_keyboard_events(
            app_services,
            key_down,
            key_up,
            target_pid=target_pid,
        )
    finally:
        app_services.CFRelease(key_down)
        app_services.CFRelease(key_up)


def _post_core_graphics_key(
    key_code: int,
    *,
    target_pid: int,
    flags: int = 0,
) -> None:
    if target_pid <= 0:
        raise RuntimeError("Targeted keyboard delivery requires a verified process.")
    app_services, _post_to_pid = _core_graphics_event_api()
    set_flags = getattr(app_services, "CGEventSetFlags", None)
    if set_flags is not None:
        set_flags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    key_down = app_services.CGEventCreateKeyboardEvent(None, int(key_code), True)
    key_up = app_services.CGEventCreateKeyboardEvent(None, int(key_code), False)
    if not key_down or not key_up:
        if key_down:
            app_services.CFRelease(key_down)
        if key_up:
            app_services.CFRelease(key_up)
        raise RuntimeError("CoreGraphics could not create a keyboard event.")
    try:
        if flags:
            if set_flags is None:
                raise RuntimeError("Targeted keyboard modifiers are unavailable.")
            set_flags(key_down, int(flags))
            set_flags(key_up, int(flags))
        _post_keyboard_events(
            app_services,
            key_down,
            key_up,
            target_pid=target_pid,
        )
    finally:
        app_services.CFRelease(key_down)
        app_services.CFRelease(key_up)


def execute_human_ops_type_text(
    payload: dict | None,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    event_typer=None,
    event_key_presser=None,
    event_clicker=_post_core_graphics_click,
    accessibility_actioner=_macos_accessibility.perform_macos_accessibility_action,
    input_value_verifier=_macos_accessibility.verify_macos_accessibility_input_value,
    chat_context_verifier=_macos_accessibility.verify_macos_accessibility_chat_context,
) -> dict[str, object]:
    if not _is_macos(platform_name):
        raise RuntimeError("Human Ops text input is currently implemented through macOS desktop event APIs.")
    data = payload if isinstance(payload, dict) else {}
    text = str(data.get("text") or "")
    label = str(data.get("label") or data.get("target") or "输入位置").strip() or "输入位置"
    ax_ref = data.get("ax_ref") if isinstance(data.get("ax_ref"), dict) else {}
    target_app = str(data.get("target_app") or "").strip()
    intended_chat = str(data.get("intended_chat") or "").strip()
    replace_existing = bool(data.get("replace_existing"))
    chat_verification: dict[str, object] = {}
    chat_target = bool(_chat_app_id(target_app))
    chat_message_target = bool(
        chat_target
        and not _is_signed_chat_search_input(ax_ref)
    )
    if chat_target:
        if not ax_ref:
            raise RuntimeError("Chat text input requires a reviewed AX input reference.")
    if replace_existing and not ax_ref:
        raise RuntimeError("Replacing text requires a reviewed AX input reference.")
    if chat_message_target and not intended_chat:
        raise RuntimeError("Chat text input requires intended_chat.")
    accessibility_result: dict[str, object] = {}
    if ax_ref and (not replace_existing or chat_message_target):
        if not target_app:
            raise RuntimeError("Human Ops semantic text input requires target_app.")
        accessibility_result = accessibility_actioner(
            target_app,
            ax_ref,
            operation="focus",
            platform_name=platform_name,
            runner=runner,
            geometry_clicker=event_clicker,
        )
    if chat_message_target:
        chat_verification = chat_context_verifier(
            target_app,
            intended_chat=intended_chat,
            input_ax_ref=ax_ref,
            require_input_focused=True,
            platform_name=platform_name,
            runner=runner,
        )
    target_pid = int(
        chat_verification.get("target_pid")
        or accessibility_result.get("target_pid")
        or 0
    )

    def verify_delivered_text() -> dict[str, object]:
        if not ax_ref:
            return {}
        try:
            postcondition = dict(
                input_value_verifier(
                    target_app,
                    input_ax_ref=ax_ref,
                    expected_text=text,
                    require_input_focused=True,
                    platform_name=platform_name,
                    runner=runner,
                )
            )
            verification_method = str(
                postcondition.pop("method", "")
            ).strip()
            if verification_method:
                postcondition["verification_method"] = verification_method
            if chat_message_target:
                verified_chat = chat_context_verifier(
                    target_app,
                    intended_chat=intended_chat,
                    input_ax_ref=ax_ref,
                    expected_text=text,
                    require_input_focused=True,
                    platform_name=platform_name,
                    runner=runner,
                )
                postcondition.update(verified_chat)
                postcondition["postcondition_verified"] = bool(
                    postcondition.get("input_value_verified")
                    and postcondition.get("chat_identity_verified")
                    and postcondition.get("chat_input_verified")
                )
            else:
                postcondition["postcondition_verified"] = bool(
                    postcondition.get("input_value_verified")
                )
            return postcondition
        except Exception as exc:
            return {
                "postcondition_verified": False,
                "postcondition_reason": str(exc).strip()
                or "AX input postcondition could not be verified.",
            }

    native_typer = event_typer
    event_error = ""
    semantic_value_error = ""
    if replace_existing:
        try:
            value_result = accessibility_actioner(
                target_app,
                ax_ref,
                operation="set_value",
                value=text,
                platform_name=platform_name,
                runner=runner,
                geometry_clicker=event_clicker,
            )
        except Exception as exc:
            value_result = {}
            semantic_value_error = str(exc).strip()[:240]
            if not accessibility_result:
                # `set_value` already focuses the reviewed editor. Only
                # resolve it separately after AXValue fails, so Electron does
                # not get a chance to rebuild its tree between two otherwise
                # redundant resolutions.
                accessibility_result = accessibility_actioner(
                    target_app,
                    ax_ref,
                    operation="focus",
                    platform_name=platform_name,
                    runner=runner,
                    geometry_clicker=event_clicker,
                )
                target_pid = int(
                    accessibility_result.get("target_pid") or 0
                )
        else:
            accessibility_result = dict(value_result)
            postcondition = verify_delivered_text()
            _macos_accessibility.invalidate_macos_accessibility_cache(target_app)
            return {
                "typed": True,
                "text": text,
                "label": label,
                "replace_existing": True,
                **chat_verification,
                **accessibility_result,
                **postcondition,
                "method": "macos_accessibility_set_value",
            }
    try:
        if replace_existing:
            if target_pid <= 0:
                raise RuntimeError(
                    "Replacing text requires a verified target process."
                )
            native_key_presser = event_key_presser or _post_core_graphics_key
            # kCGEventFlagMaskCommand with the hardware A key selects the
            # contents of the freshly re-verified editor in the target PID.
            native_key_presser(
                0,
                target_pid=target_pid,
                flags=1 << 20,
            )
            if not text:
                native_key_presser(51, target_pid=target_pid)
        if native_typer is not None:
            native_typer(text)
        elif target_pid > 0:
            _post_core_graphics_text(text, target_pid=target_pid)
        else:
            _post_core_graphics_text(text)
    except Exception as exc:
        event_error = str(exc).strip()
    else:
        postcondition = verify_delivered_text()
        method = "macos_accessibility_focus+core_graphics_unicode" if accessibility_result else "core_graphics_unicode"
        _macos_accessibility.invalidate_macos_accessibility_cache(target_app)
        return {
            "typed": True,
            "text": text,
            "label": label,
            **({"replace_existing": True} if replace_existing else {}),
            **chat_verification,
            **accessibility_result,
            **postcondition,
            **(
                {"semantic_value_fallback_reason": semantic_value_error}
                if semantic_value_error
                else {}
            ),
            "method": method,
        }
    if chat_message_target or replace_existing:
        raise RuntimeError(
            f"Targeted CoreGraphics text input failed: {event_error}"
        )
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
    postcondition = verify_delivered_text()
    _macos_accessibility.invalidate_macos_accessibility_cache(target_app)
    return {
        "typed": True,
        "text": text,
        "label": label,
        **({"replace_existing": True} if replace_existing else {}),
        **chat_verification,
        **accessibility_result,
        **postcondition,
        "method": method,
    }


def execute_human_ops_key_press(
    payload: dict | None,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    event_presser=None,
    chat_context_verifier=_macos_accessibility.verify_macos_accessibility_chat_context,
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
    chat_verification: dict[str, object] = {}
    chat_target = bool(_chat_app_id(target_app))
    if chat_target:
        intended_chat = str(data.get("intended_chat") or "").strip()
        expected_text = str(data.get("expected_text") or "").strip()
        input_ax_ref = (
            data.get("input_ax_ref")
            if isinstance(data.get("input_ax_ref"), dict)
            else {}
        )
        if not intended_chat:
            raise RuntimeError("Chat send requires intended_chat.")
        if not expected_text:
            raise RuntimeError("Chat send requires expected_text.")
        if not input_ax_ref:
            raise RuntimeError("Chat send requires the reviewed input_ax_ref.")
        chat_verification = chat_context_verifier(
            target_app,
            intended_chat=intended_chat,
            input_ax_ref=input_ax_ref,
            expected_text=expected_text,
            require_input_focused=True,
            platform_name=platform_name,
            runner=runner,
        )
        target_pid = int(chat_verification.get("target_pid") or 0)
        if target_pid <= 0:
            raise RuntimeError("Chat send lacks a verified target process.")
        native_presser = event_presser or _post_core_graphics_key
        native_presser(key_codes[raw_key], target_pid=target_pid)
        normalized_key = "enter" if raw_key == "return" else raw_key
        _macos_accessibility.invalidate_macos_accessibility_cache(target_app)
        return {
            "pressed": True,
            "key": normalized_key,
            "label": label,
            **chat_verification,
            "method": "targeted_core_graphics",
        }
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
    return {
        "pressed": True,
        "key": normalized_key,
        "label": label,
        **chat_verification,
        "method": "system_events",
    }


def frontmost_macos_application(*, runner=subprocess.run) -> str:
    try:
        import AppKit  # type: ignore

        application = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        name = str(application.localizedName() or "").strip() if application else ""
        if name:
            return name
    except Exception:
        pass
    runtime = None
    try:
        runtime = _macos_accessibility._AXRuntime()
        if runtime.trusted():
            pid, name = runtime.focused_application()
            if pid > 0 and name:
                return name
    except Exception:
        pass
    finally:
        if runtime is not None:
            runtime.close()
    script = 'tell application "System Events" to get name of first application process whose frontmost is true'
    try:
        result = runner(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
    except Exception:
        return ""
    if getattr(result, "returncode", 1) != 0:
        return ""
    return str(getattr(result, "stdout", "") or "").strip()


def _visible_macos_accessibility_window(profile: dict) -> bool | None:
    runtime = None
    application = 0
    windows: list[int] = []
    try:
        runtime = _macos_accessibility._AXRuntime()
        if not runtime.trusted():
            return None
        pid, name = runtime.focused_application()
        target_keys = {
            _application_name_key(value)
            for value in (
                profile.get("display_name"),
                profile.get("launch_name"),
                *(profile.get("aliases") or ()),
                *(profile.get("process_names") or ()),
            )
            if _application_name_key(value)
        }
        name_key = _application_name_key(name)
        if pid <= 0 or not name_key or not any(
            key == name_key or key in name_key or name_key in key
            for key in target_keys
        ):
            return False
        application = runtime.application(pid)
        if not application:
            return None
        windows, _truncated = runtime._attribute_elements(
            application,
            "AXWindows",
            limit=16,
        )
        for window in windows:
            values = runtime.attributes(
                window,
                ("AXMinimized", "AXSize"),
            )
            size = (
                values.get("AXSize")
                if isinstance(values.get("AXSize"), dict)
                else {}
            )
            if (
                values.get("AXMinimized") is not True
                and float(size.get("width") or 0) >= 32
                and float(size.get("height") or 0) >= 32
            ):
                return True
        return str(profile.get("app_id") or "").strip().casefold() == "finder"
    except Exception:
        return None
    finally:
        if runtime is not None:
            for window in windows:
                runtime.release(window)
            if application:
                runtime.release(application)
            runtime.close()


def _visible_macos_application_window(profile: dict) -> bool | None:
    """Return whether this application owns a normal window on the current Space."""

    try:
        core = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        graphics = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        core.CFRelease.argtypes = [ctypes.c_void_p]
        core.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        core.CFArrayGetCount.restype = ctypes.c_long
        core.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        core.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        core.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        core.CFDictionaryGetValue.restype = ctypes.c_void_p
        core.CFStringGetLength.argtypes = [ctypes.c_void_p]
        core.CFStringGetLength.restype = ctypes.c_long
        core.CFStringGetMaximumSizeForEncoding.argtypes = [
            ctypes.c_long,
            ctypes.c_uint32,
        ]
        core.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
        core.CFStringGetCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_long,
            ctypes.c_uint32,
        ]
        core.CFStringGetCString.restype = ctypes.c_bool
        core.CFNumberGetValue.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        core.CFNumberGetValue.restype = ctypes.c_bool
        graphics.CGWindowListCopyWindowInfo.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        graphics.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
        graphics.CGRectMakeWithDictionaryRepresentation.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_CGRect),
        ]
        graphics.CGRectMakeWithDictionaryRepresentation.restype = ctypes.c_bool
        owner_name_key = ctypes.c_void_p.in_dll(
            graphics,
            "kCGWindowOwnerName",
        ).value
        layer_key = ctypes.c_void_p.in_dll(
            graphics,
            "kCGWindowLayer",
        ).value
        alpha_key = ctypes.c_void_p.in_dll(
            graphics,
            "kCGWindowAlpha",
        ).value
        bounds_key = ctypes.c_void_p.in_dll(
            graphics,
            "kCGWindowBounds",
        ).value
    except Exception:
        return _visible_macos_accessibility_window(profile)

    target_keys = {
        _application_name_key(value)
        for value in (
            profile.get("display_name"),
            profile.get("launch_name"),
            *(profile.get("aliases") or ()),
            *(profile.get("process_names") or ()),
        )
        if _application_name_key(value)
    }
    if not target_keys:
        return None

    utf8_encoding = 0x08000100
    number_sint32 = 3
    number_double = 13

    def dictionary_value(dictionary: object, key: object) -> int:
        return int(
            core.CFDictionaryGetValue(
                ctypes.c_void_p(int(dictionary or 0)),
                ctypes.c_void_p(int(key or 0)),
            )
            or 0
        )

    def string_value(value: object) -> str:
        pointer = ctypes.c_void_p(int(value or 0))
        if not pointer.value:
            return ""
        length = int(core.CFStringGetLength(pointer))
        capacity = int(
            core.CFStringGetMaximumSizeForEncoding(length, utf8_encoding)
        ) + 1
        if capacity <= 1:
            return ""
        buffer = ctypes.create_string_buffer(capacity)
        if not core.CFStringGetCString(
            pointer,
            buffer,
            capacity,
            utf8_encoding,
        ):
            return ""
        return buffer.value.decode("utf-8", errors="replace")

    def integer_value(value: object) -> int:
        output = ctypes.c_int32()
        if value and core.CFNumberGetValue(
            ctypes.c_void_p(int(value)),
            number_sint32,
            ctypes.byref(output),
        ):
            return int(output.value)
        return 0

    def double_value(value: object, fallback: float) -> float:
        output = ctypes.c_double()
        if value and core.CFNumberGetValue(
            ctypes.c_void_p(int(value)),
            number_double,
            ctypes.byref(output),
        ):
            return float(output.value)
        return fallback

    # On-screen only (1) plus exclude desktop elements (16). Unlike process
    # frontmost state, this list is scoped to the currently active Spaces.
    windows = int(graphics.CGWindowListCopyWindowInfo(1 | 16, 0) or 0)
    if not windows:
        return _visible_macos_accessibility_window(profile)
    try:
        count = int(core.CFArrayGetCount(ctypes.c_void_p(windows)))
        for index in range(max(0, count)):
            dictionary = int(
                core.CFArrayGetValueAtIndex(
                    ctypes.c_void_p(windows),
                    index,
                )
                or 0
            )
            owner = string_value(dictionary_value(dictionary, owner_name_key))
            owner_key = _application_name_key(owner)
            if not owner_key or not any(
                key == owner_key or key in owner_key or owner_key in key
                for key in target_keys
            ):
                continue
            layer = integer_value(dictionary_value(dictionary, layer_key))
            alpha = double_value(
                dictionary_value(dictionary, alpha_key),
                1.0,
            )
            bounds_value = dictionary_value(dictionary, bounds_key)
            bounds = _CGRect()
            has_bounds = bool(
                bounds_value
                and graphics.CGRectMakeWithDictionaryRepresentation(
                    ctypes.c_void_p(bounds_value),
                    ctypes.byref(bounds),
                )
            )
            if (
                layer == 0
                and alpha > 0.01
                and has_bounds
                and bounds.size.width >= 32
                and bounds.size.height >= 32
            ):
                return True
        # Finder's Desktop is a valid application surface even though its
        # WindowServer-owned desktop layer is intentionally excluded above.
        return str(profile.get("app_id") or "").strip().casefold() == "finder"
    except Exception:
        return _visible_macos_accessibility_window(profile)
    finally:
        core.CFRelease(ctypes.c_void_p(windows))


def _activate_running_macos_application(profile: dict) -> bool:
    try:
        import AppKit  # type: ignore

        candidates = []
        bundle_id = str(profile.get("bundle_id") or "").strip()
        if bundle_id:
            candidates.extend(
                list(
                    AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(
                        bundle_id
                    )
                    or []
                )
            )
        if not candidates:
            target_keys = {
                _application_name_key(value)
                for value in (
                    profile.get("display_name"),
                    profile.get("launch_name"),
                    *(profile.get("aliases") or ()),
                )
                if _application_name_key(value)
            }
            for application in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
                name_key = _application_name_key(application.localizedName())
                if name_key and any(
                    key == name_key or key in name_key or name_key in key
                    for key in target_keys
                ):
                    candidates.append(application)
        options = int(
            getattr(AppKit, "NSApplicationActivateIgnoringOtherApps", 1)
        ) | int(getattr(AppKit, "NSApplicationActivateAllWindows", 0))
        for application in candidates:
            try:
                application.unhide()
            except Exception:
                pass
            if bool(application.activateWithOptions_(options)):
                return True
    except Exception:
        return False
    return False


def _activate_running_macos_application_with_system_events(
    profile: dict,
    *,
    runner=subprocess.run,
) -> bool:
    bundle_id = str(profile.get("bundle_id") or "").strip()
    names = list(
        dict.fromkeys(
            str(value or "").strip()
            for value in (
                profile.get("display_name"),
                profile.get("launch_name"),
                *(profile.get("aliases") or ()),
            )
            if str(value or "").strip()
        )
    )
    if not bundle_id and not names:
        return False
    name_list = "{" + ", ".join(_applescript_string(name) for name in names) + "}"
    script = f"""
tell application "System Events"
    set targetBundle to {_applescript_string(bundle_id)}
    set targetNames to {name_list}
    repeat with proc in application processes
        try
            set procName to name of proc as text
            set procBundle to bundle identifier of proc as text
            if (targetBundle is not "" and procBundle is targetBundle) or procName is in targetNames then
                set frontmost of proc to true
                return procName
            end if
        end try
    end repeat
end tell
return ""
"""
    try:
        result = runner(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return False
    return bool(
        getattr(result, "returncode", 1) == 0
        and str(getattr(result, "stdout", "") or "").strip()
    )


def _activate_macos_application_with_launch_services(
    profile: dict,
    *,
    runner=subprocess.run,
) -> bool:
    launch_name = str(
        profile.get("launch_name")
        or profile.get("display_name")
        or profile.get("app")
        or ""
    ).strip()
    bundle_id = str(profile.get("bundle_id") or "").strip()
    if not launch_name and not bundle_id:
        return False
    result = None
    if launch_name:
        try:
            result = runner(
                ["/usr/bin/open", "-a", launch_name],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except Exception:
            result = None
        if result is not None and getattr(result, "returncode", 1) == 0:
            return True
    if bundle_id:
        try:
            result = runner(
                ["/usr/bin/open", "-b", bundle_id],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except Exception:
            return False
        return getattr(result, "returncode", 1) == 0
    return False


def _activate_macos_application(
    profile: dict,
    *,
    runner=subprocess.run,
) -> str:
    if _activate_running_macos_application(profile):
        return "appkit"
    if _activate_macos_application_with_launch_services(
        profile,
        runner=runner,
    ):
        return "launch_services"
    if _macos_accessibility.activate_macos_accessibility_application(
        profile,
        platform_name="darwin",
        runner=runner,
    ):
        return "accessibility"
    if _activate_running_macos_application_with_system_events(
        profile,
        runner=runner,
    ):
        return "system_events"
    return ""


def _activation_method(result: object) -> str:
    if isinstance(result, str):
        return result.strip()
    return "native" if result else ""


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
    native_activator=None,
    visible_window_provider=None,
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
    activate_native = native_activator
    if activate_native is None and frontmost_provider is None:
        activate_native = lambda profile: _activate_macos_application(
            profile,
            runner=runner,
        )
    activation_method = _activation_method(
        activate_native(ax_profile)
        if activate_native is not None
        else ""
    )
    if not activation_method:
        result = runner(
            ["/usr/bin/open", "-a", launch_name],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        bundle_id = str(ax_profile.get("bundle_id") or "").strip()
        if getattr(result, "returncode", 1) != 0 and bundle_id:
            result = runner(
                ["/usr/bin/open", "-b", bundle_id],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        if getattr(result, "returncode", 1) != 0:
            detail = str(getattr(result, "stderr", "") or getattr(result, "stdout", "") or "open failed").strip()
            raise RuntimeError(f"macOS could not focus {target_app}: {detail}")
        activation_method = activation_method or "launch_services"
    get_visible_window = visible_window_provider
    if get_visible_window is None and frontmost_provider is None:
        get_visible_window = _visible_macos_application_window
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
    last_surface_visible: bool | None = None
    while time.monotonic() < deadline:
        last_frontmost = str(get_frontmost() or "").strip()
        frontmost_key = _application_name_key(last_frontmost)
        if frontmost_key and any(
            target_key == frontmost_key or target_key in frontmost_key or frontmost_key in target_key
            for target_key in target_keys
        ):
            last_surface_visible = (
                get_visible_window(ax_profile)
                if get_visible_window is not None
                else None
            )
            if get_visible_window is None or last_surface_visible is True:
                result: dict[str, object] = {
                    "focused": True,
                    "target_app": target_app,
                    "frontmost_app": last_frontmost,
                    "method": activation_method,
                }
                if last_surface_visible is not None:
                    result["surface_visible"] = last_surface_visible
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
    if get_visible_window is not None and last_surface_visible is not True:
        detail = (
            "no normal application window is visible on the current Space"
            if last_surface_visible is False
            else "window visibility could not be verified"
        )
        raise RuntimeError(
            f"macOS foreground verification found {target_app}, but {detail}"
        )
    raise RuntimeError(
        f"macOS foreground verification failed: expected {target_app}, got {last_frontmost or 'unknown'}"
    )


def restore_macos_application_focus(
    focus_result: dict | None,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    frontmost_provider=None,
    native_activator=None,
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
    activate = native_activator or (
        lambda resolved: _activate_macos_application(
            resolved,
            runner=runner,
        )
    )
    method = _activation_method(activate(profile))
    return {
        "restored": bool(method),
        "reason": "restored" if method else "activation_failed",
        "target_app": previous_app,
        "method": method,
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
