from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import deque
from threading import Lock
from typing import Any

from body import active_vision_macos as _active_macos
from body.macos_accessibility_cache import (
    AX_SEARCH_DEFAULT_LIMIT,
    AX_STORE_MAX_ELEMENTS,
    AXSnapshotCache,
    MACOS_AX_SNAPSHOT_CACHE,
)


AX_FULL_TREE_MAX_ELEMENTS = 20_000
AX_FULL_TREE_MAX_DEPTH = 64
AX_FULL_TREE_TIMEOUT_SEC = 15.0
AX_REFRESH_MAX_APPS = 128
AX_SELF_PROCESS_REASON = "Ipet refuses to inspect or modify its own Accessibility process"
_AX_INDEX_REFRESH_LOCK = Lock()


MACOS_AX_APP_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "app_id": "qq",
        "display_name": "QQ",
        "bundle_id": "com.tencent.qq",
        "aliases": ("QQ", "腾讯QQ"),
        "process_names": ("QQ",),
        "surface": "qq_gui",
    },
    {
        "app_id": "wechat",
        "display_name": "微信",
        "launch_name": "WeChat",
        "bundle_id": "com.tencent.xinWeChat",
        "aliases": ("微信", "WeChat", "Weixin"),
        "process_names": ("WeChat",),
        "surface": "wechat_gui",
    },
    {
        "app_id": "music",
        "display_name": "音乐",
        "launch_name": "Music",
        "bundle_id": "com.apple.Music",
        "aliases": ("音乐", "Music", "Apple Music"),
        "process_names": ("Music",),
        "surface": "music_gui",
    },
    {
        "app_id": "finder",
        "display_name": "访达",
        "launch_name": "Finder",
        "bundle_id": "com.apple.finder",
        "aliases": ("访达", "Finder"),
        "process_names": ("Finder",),
        "surface": "finder_gui",
    },
)

_EDITABLE_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"}
_SELECTABLE_ROLES = {"AXRow", "AXCell", "AXOutlineRow", "AXTab", "AXListItem"}
_MEANINGFUL_ROLES = {
    "AXApplication",
    "AXWindow",
    "AXSheet",
    "AXButton",
    "AXCheckBox",
    "AXRadioButton",
    "AXPopUpButton",
    "AXMenuButton",
    "AXMenuItem",
    "AXLink",
    "AXTextField",
    "AXTextArea",
    "AXSearchField",
    "AXComboBox",
    "AXStaticText",
    "AXImage",
    "AXRow",
    "AXCell",
    "AXOutlineRow",
    "AXTab",
    "AXListItem",
    "AXTable",
    "AXOutline",
    "AXList",
    "AXToolbar",
    "AXSlider",
}
_ACTION_NAMES = {
    "AXPress": "press",
    "AXConfirm": "confirm",
    "AXCancel": "cancel",
    "AXShowMenu": "show_menu",
    "AXIncrement": "increment",
    "AXDecrement": "decrement",
    "AXRaise": "raise",
}


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


def _platform_name(platform_name: str | None = None) -> str:
    return str(platform_name or sys.platform).strip().lower()


def _positive_pid(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _current_process_pid(provider=os.getpid) -> int:
    try:
        return _positive_pid(provider())
    except Exception:
        return _positive_pid(os.getpid())


def _is_self_process(pid: object, *, current_pid_provider=os.getpid) -> bool:
    resolved_pid = _positive_pid(pid)
    current_pid = _current_process_pid(current_pid_provider)
    return bool(resolved_pid and current_pid and resolved_pid == current_pid)


def _app_key(value: object) -> str:
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def _profile_identity_values(profile: dict[str, Any]) -> tuple[object, ...]:
    return (
        profile.get("app_id"),
        profile.get("display_name"),
        profile.get("launch_name"),
        profile.get("bundle_id"),
        *(profile.get("aliases") or ()),
        *(profile.get("process_names") or ()),
    )


def _known_macos_ax_profile(*identities: object) -> dict[str, Any]:
    keys = {_app_key(item) for item in identities if _app_key(item)}
    for profile in MACOS_AX_APP_PROFILES:
        if keys & {_app_key(item) for item in _profile_identity_values(profile) if _app_key(item)}:
            return dict(profile)
    return {}


def _dynamic_app_id(display_name: object, bundle_id: object = "") -> str:
    bundle = _clean_text(bundle_id, max_length=200)
    name = _clean_text(display_name, max_length=160)
    seed = bundle.casefold() or name.casefold()
    readable = "".join(
        char if char.isascii() and char.isalnum() else "-"
        for char in (bundle or name).casefold()
    )
    readable = "-".join(part for part in readable.split("-") if part)[:48] or "native"
    digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"app-{readable}-{digest}"


def _profile_from_running_app(
    app: dict[str, Any],
    *,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = app if isinstance(app, dict) else {}
    base = dict(fallback or {})
    display_name = _clean_text(
        data.get("display_name") or data.get("app") or data.get("name") or data.get("title")
        or base.get("display_name"),
        max_length=160,
    )
    bundle_id = _clean_text(data.get("bundle_id") or base.get("bundle_id"), max_length=200)
    known = (
        _known_macos_ax_profile(bundle_id)
        if bundle_id
        else _known_macos_ax_profile(
            data.get("app_id"),
            display_name,
            *(_profile_identity_values(base) if base else ()),
        )
    )
    profile = known or base
    aliases = tuple(
        dict.fromkeys(
            _clean_text(item, max_length=160)
            for item in (
                *(profile.get("aliases") or ()),
                *(data.get("aliases") if isinstance(data.get("aliases"), (list, tuple)) else ()),
                profile.get("display_name"),
                profile.get("launch_name"),
                display_name,
                data.get("title"),
            )
            if _clean_text(item, max_length=160)
        )
    )
    pid = _positive_pid(data.get("pid") or profile.get("pid"))
    resolved_display_name = str(known.get("display_name") or display_name)
    resolved_bundle_id = str(known.get("bundle_id") or bundle_id)
    return {
        **profile,
        "app_id": str(
            known.get("app_id")
            or _dynamic_app_id(resolved_display_name, resolved_bundle_id)
        ),
        "display_name": resolved_display_name,
        "launch_name": str(known.get("launch_name") or resolved_display_name),
        "bundle_id": resolved_bundle_id,
        "aliases": aliases,
        "process_names": tuple(
            dict.fromkeys(
                _clean_text(item, max_length=160)
                for item in (
                    *(profile.get("process_names") or ()),
                    *(data.get("process_names") if isinstance(data.get("process_names"), (list, tuple)) else ()),
                    data.get("process_name"),
                    display_name,
                )
                if _clean_text(item, max_length=160)
            )
        ),
        "surface": str(profile.get("surface") or "native_app_gui"),
        "pid": pid,
        "dynamic": not bool(known),
    }


def resolve_macos_ax_app(target_app: object) -> dict[str, Any]:
    if isinstance(target_app, dict):
        return _profile_from_running_app(target_app)
    target_name = _clean_text(target_app, max_length=160)
    if not target_name:
        return {}
    known = _known_macos_ax_profile(target_name)
    if known:
        return known
    bundle_id = target_name if "." in target_name and " " not in target_name else ""
    return _profile_from_running_app(
        {
            "app": target_name,
            "bundle_id": bundle_id,
        }
    )


def _clean_text(value: object, *, max_length: int = 240) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    return text[:max_length]


def _profile_matches_app_name(profile: dict[str, Any], app_name: object) -> bool:
    name_key = _app_key(app_name)
    names = _profile_identity_values(profile)
    return bool(name_key) and name_key in {_app_key(name) for name in names if name}


def _running_app_pid(profile: dict[str, Any], *, runner=subprocess.run) -> int:
    resolved_pid = _positive_pid(profile.get("pid"))
    if resolved_pid:
        return resolved_pid
    for process_name in profile.get("process_names") or ():
        try:
            result = runner(
                ["/usr/bin/pgrep", "-x", str(process_name)],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
        except Exception:
            continue
        if getattr(result, "returncode", 1) != 0:
            continue
        pids: list[int] = []
        for line in str(getattr(result, "stdout", "") or "").splitlines():
            try:
                pid = int(line.strip())
            except (TypeError, ValueError):
                continue
            if pid > 0:
                pids.append(pid)
        if pids:
            return min(pids)
    return 0


def _target_app_pid(profile: dict[str, Any], runtime: "_AXRuntime", *, runner=subprocess.run) -> int:
    focused_pid, focused_name = runtime.focused_application()
    if focused_pid > 0 and _profile_matches_app_name(profile, focused_name):
        return focused_pid
    return _running_app_pid(profile, runner=runner)


def _enumerate_running_ax_apps(
    *,
    platform_name: str | None,
    runner,
    provider,
) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        result = provider(
            platform_name=platform_name,
            runner=runner,
            include_errors=True,
            limit=AX_REFRESH_MAX_APPS,
            include_hidden=True,
        )
    except TypeError:
        result = provider(
            platform_name=platform_name,
            runner=runner,
            include_errors=True,
        )
    except Exception as exc:
        return [], [_clean_text(exc, max_length=240)]
    if isinstance(result, tuple):
        raw_apps = result[0] if isinstance(result[0], list) else []
        raw_errors = result[1] if len(result) > 1 and isinstance(result[1], list) else []
    else:
        raw_apps = result if isinstance(result, list) else []
        raw_errors = []
    apps = [
        dict(item)
        for item in raw_apps[:AX_REFRESH_MAX_APPS]
        if isinstance(item, dict)
        and _clean_text(item.get("app") or item.get("name"), max_length=160)
    ]
    return apps, [_clean_text(item, max_length=240) for item in raw_errors if _clean_text(item, max_length=240)]


def _resolve_running_profile(
    profile: dict[str, Any],
    *,
    platform_name: str | None,
    runner,
    running_apps_provider,
) -> dict[str, Any]:
    try:
        profile_pid = int(profile.get("pid") or 0)
    except (TypeError, ValueError):
        profile_pid = 0
    if not profile.get("dynamic") or profile_pid > 0:
        return dict(profile)
    apps, _errors = _enumerate_running_ax_apps(
        platform_name=platform_name,
        runner=runner,
        provider=running_apps_provider,
    )
    target_keys = {_app_key(item) for item in _profile_identity_values(profile) if _app_key(item)}
    matches = [
        item
        for item in apps
        if target_keys
        & {
            _app_key(item.get("app")),
            _app_key(item.get("name")),
            _app_key(item.get("title")),
            _app_key(item.get("bundle_id")),
        }
    ]
    if len(matches) == 1:
        return _profile_from_running_app(matches[0], fallback=profile)
    return dict(profile)


def _cached_app_id(profile: dict[str, Any], cache: AXSnapshotCache) -> str:
    stored = cache.find_app_id(*_profile_identity_values(profile))
    return stored or str(profile.get("app_id") or "")


class _AXRuntime:
    _UTF8 = 0x08000100
    _CF_NUMBER_DOUBLE = 13
    _AX_VALUE_POINT = 1
    _AX_VALUE_SIZE = 2
    _AX_VALUE_RECT = 3

    def __init__(self) -> None:
        self.core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        self._strings: dict[str, int] = {}
        self._configure()

    @staticmethod
    def _pointer(value: object) -> ctypes.c_void_p:
        if isinstance(value, ctypes.c_void_p):
            return value
        return ctypes.c_void_p(int(value or 0))

    def _configure(self) -> None:
        self.core.CFRelease.argtypes = [ctypes.c_void_p]
        self.core.CFRetain.argtypes = [ctypes.c_void_p]
        self.core.CFRetain.restype = ctypes.c_void_p
        self.core.CFGetTypeID.argtypes = [ctypes.c_void_p]
        self.core.CFGetTypeID.restype = ctypes.c_ulong
        self.core.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        self.core.CFStringCreateWithCString.restype = ctypes.c_void_p
        self.core.CFStringGetLength.argtypes = [ctypes.c_void_p]
        self.core.CFStringGetLength.restype = ctypes.c_long
        self.core.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, ctypes.c_uint32]
        self.core.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
        self.core.CFStringGetCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_long,
            ctypes.c_uint32,
        ]
        self.core.CFStringGetCString.restype = ctypes.c_bool
        self.core.CFStringGetTypeID.restype = ctypes.c_ulong
        self.core.CFBooleanGetTypeID.restype = ctypes.c_ulong
        self.core.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
        self.core.CFBooleanGetValue.restype = ctypes.c_bool
        self.core.CFNumberGetTypeID.restype = ctypes.c_ulong
        self.core.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        self.core.CFNumberGetValue.restype = ctypes.c_bool
        self.core.CFArrayGetTypeID.restype = ctypes.c_ulong
        self.core.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        self.core.CFArrayGetCount.restype = ctypes.c_long
        self.core.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        self.core.CFArrayGetValueAtIndex.restype = ctypes.c_void_p

        self.ax.AXIsProcessTrusted.argtypes = []
        self.ax.AXIsProcessTrusted.restype = ctypes.c_bool
        self.ax.AXUIElementCreateApplication.argtypes = [ctypes.c_int]
        self.ax.AXUIElementCreateApplication.restype = ctypes.c_void_p
        self.ax.AXUIElementGetTypeID.restype = ctypes.c_ulong
        self.ax.AXValueGetTypeID.restype = ctypes.c_ulong
        self.ax.AXValueGetType.argtypes = [ctypes.c_void_p]
        self.ax.AXValueGetType.restype = ctypes.c_int
        self.ax.AXValueGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        self.ax.AXValueGetValue.restype = ctypes.c_bool
        self.ax.AXUIElementCopyAttributeValue.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int
        self.ax.AXUIElementGetAttributeValueCount.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_long),
        ]
        self.ax.AXUIElementGetAttributeValueCount.restype = ctypes.c_int
        self.ax.AXUIElementCopyAttributeValues.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.ax.AXUIElementCopyAttributeValues.restype = ctypes.c_int
        self.ax.AXUIElementCopyActionNames.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.ax.AXUIElementCopyActionNames.restype = ctypes.c_int
        self.ax.AXUIElementPerformAction.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.ax.AXUIElementPerformAction.restype = ctypes.c_int
        self.ax.AXUIElementIsAttributeSettable.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_bool),
        ]
        self.ax.AXUIElementIsAttributeSettable.restype = ctypes.c_int
        self.ax.AXUIElementSetAttributeValue.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self.ax.AXUIElementSetAttributeValue.restype = ctypes.c_int
        messaging_timeout = getattr(self.ax, "AXUIElementSetMessagingTimeout", None)
        if messaging_timeout is not None:
            messaging_timeout.argtypes = [ctypes.c_void_p, ctypes.c_float]
            messaging_timeout.restype = ctypes.c_int

    def close(self) -> None:
        for value in self._strings.values():
            if value:
                self.core.CFRelease(self._pointer(value))
        self._strings.clear()

    def trusted(self) -> bool:
        return bool(self.ax.AXIsProcessTrusted())

    def application(self, pid: int) -> int:
        resolved_pid = _positive_pid(pid)
        if resolved_pid <= 0:
            return 0
        if resolved_pid == _positive_pid(os.getpid()):
            raise RuntimeError(AX_SELF_PROCESS_REASON)
        value = self.ax.AXUIElementCreateApplication(resolved_pid)
        return int(value or 0)

    def focused_application(self) -> tuple[int, str]:
        try:
            import AppKit  # type: ignore

            application = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if application is None:
                return 0, ""
            pid = _positive_pid(application.processIdentifier())
            name = _clean_text(application.localizedName(), max_length=120)
        except Exception:
            return 0, ""
        return (pid, name) if pid > 0 else (0, "")

    def release(self, value: object) -> None:
        pointer = self._pointer(value)
        if pointer.value:
            self.core.CFRelease(pointer)

    def retain(self, value: object) -> int:
        pointer = self._pointer(value)
        if not pointer.value:
            return 0
        return int(self.core.CFRetain(pointer) or 0)

    def set_timeout(self, element: object, seconds: float) -> None:
        setter = getattr(self.ax, "AXUIElementSetMessagingTimeout", None)
        if setter is not None:
            setter(self._pointer(element), ctypes.c_float(max(0.05, float(seconds))))

    def _cf_string(self, value: str) -> int:
        cached = self._strings.get(value)
        if cached:
            return cached
        pointer = self.core.CFStringCreateWithCString(None, value.encode("utf-8"), self._UTF8)
        if not pointer:
            raise RuntimeError(f"CoreFoundation could not encode {value}.")
        self._strings[value] = int(pointer)
        return int(pointer)

    def _string_value(self, value: object) -> str:
        pointer = self._pointer(value)
        length = int(self.core.CFStringGetLength(pointer))
        size = int(self.core.CFStringGetMaximumSizeForEncoding(length, self._UTF8)) + 1
        buffer = ctypes.create_string_buffer(max(1, size))
        if not self.core.CFStringGetCString(pointer, buffer, len(buffer), self._UTF8):
            return ""
        return buffer.value.decode("utf-8", errors="replace")

    def _python_value(self, value: object) -> object:
        pointer = self._pointer(value)
        if not pointer.value:
            return None
        type_id = int(self.core.CFGetTypeID(pointer))
        if type_id == int(self.core.CFStringGetTypeID()):
            return self._string_value(pointer)
        if type_id == int(self.core.CFBooleanGetTypeID()):
            return bool(self.core.CFBooleanGetValue(pointer))
        if type_id == int(self.core.CFNumberGetTypeID()):
            number = ctypes.c_double()
            if self.core.CFNumberGetValue(pointer, self._CF_NUMBER_DOUBLE, ctypes.byref(number)):
                return number.value
            return None
        if type_id == int(self.ax.AXValueGetTypeID()):
            value_type = int(self.ax.AXValueGetType(pointer))
            if value_type == self._AX_VALUE_POINT:
                point = _CGPoint()
                if self.ax.AXValueGetValue(pointer, value_type, ctypes.byref(point)):
                    return {"x": point.x, "y": point.y}
            elif value_type == self._AX_VALUE_SIZE:
                size = _CGSize()
                if self.ax.AXValueGetValue(pointer, value_type, ctypes.byref(size)):
                    return {"width": size.width, "height": size.height}
            elif value_type == self._AX_VALUE_RECT:
                rect = _CGRect()
                if self.ax.AXValueGetValue(pointer, value_type, ctypes.byref(rect)):
                    return {
                        "x": rect.origin.x,
                        "y": rect.origin.y,
                        "width": rect.size.width,
                        "height": rect.size.height,
                    }
        if type_id == int(self.core.CFArrayGetTypeID()):
            values: list[object] = []
            for index in range(min(80, int(self.core.CFArrayGetCount(pointer)))):
                item = self.core.CFArrayGetValueAtIndex(pointer, index)
                converted = self._python_value(item)
                if converted is not None:
                    values.append(converted)
            return values
        return None

    def attribute(self, element: object, name: str) -> object:
        output = ctypes.c_void_p()
        error = self.ax.AXUIElementCopyAttributeValue(
            self._pointer(element),
            self._pointer(self._cf_string(name)),
            ctypes.byref(output),
        )
        if error != 0 or not output.value:
            return None
        try:
            return self._python_value(output)
        finally:
            self.release(output)

    def children(self, element: object, *, limit: int) -> tuple[list[int], bool]:
        attribute = self._pointer(self._cf_string("AXChildren"))
        count = ctypes.c_long()
        error = self.ax.AXUIElementGetAttributeValueCount(
            self._pointer(element),
            attribute,
            ctypes.byref(count),
        )
        if error != 0 or count.value <= 0:
            return [], False
        requested = min(max(0, int(limit)), int(count.value))
        if requested <= 0:
            return [], bool(count.value)
        output = ctypes.c_void_p()
        error = self.ax.AXUIElementCopyAttributeValues(
            self._pointer(element),
            attribute,
            0,
            requested,
            ctypes.byref(output),
        )
        if error != 0 or not output.value:
            return [], False
        children: list[int] = []
        try:
            if int(self.core.CFGetTypeID(output)) != int(self.core.CFArrayGetTypeID()):
                return [], False
            array_count = int(self.core.CFArrayGetCount(output))
            ax_type = int(self.ax.AXUIElementGetTypeID())
            for index in range(array_count):
                child = self.core.CFArrayGetValueAtIndex(output, index)
                if child and int(self.core.CFGetTypeID(child)) == ax_type:
                    retained = self.retain(child)
                    if retained:
                        children.append(retained)
        finally:
            self.release(output)
        return children, int(count.value) > requested

    def actions(self, element: object) -> list[str]:
        output = ctypes.c_void_p()
        error = self.ax.AXUIElementCopyActionNames(self._pointer(element), ctypes.byref(output))
        if error != 0 or not output.value:
            return []
        try:
            value = self._python_value(output)
            return [str(item) for item in value] if isinstance(value, list) else []
        finally:
            self.release(output)

    def perform(self, element: object, action: str) -> None:
        error = self.ax.AXUIElementPerformAction(
            self._pointer(element),
            self._pointer(self._cf_string(action)),
        )
        if error != 0:
            raise RuntimeError(f"macOS Accessibility action {action} failed with AXError {error}.")

    def set_boolean_attribute(self, element: object, name: str, value: bool) -> None:
        settable = ctypes.c_bool(False)
        attribute = self._pointer(self._cf_string(name))
        error = self.ax.AXUIElementIsAttributeSettable(
            self._pointer(element),
            attribute,
            ctypes.byref(settable),
        )
        if error != 0 or not bool(settable.value):
            raise RuntimeError(f"macOS Accessibility attribute {name} is not settable.")
        symbol = "kCFBooleanTrue" if value else "kCFBooleanFalse"
        boolean = ctypes.c_void_p.in_dll(self.core, symbol).value
        error = self.ax.AXUIElementSetAttributeValue(
            self._pointer(element),
            attribute,
            self._pointer(boolean),
        )
        if error != 0:
            raise RuntimeError(f"macOS Accessibility could not set {name}; AXError {error}.")


def _rounded_number(value: object) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _bounds(position: object, size: object) -> dict[str, int]:
    point = position if isinstance(position, dict) else {}
    dimensions = size if isinstance(size, dict) else {}
    width = max(0, _rounded_number(dimensions.get("width")))
    height = max(0, _rounded_number(dimensions.get("height")))
    if width <= 0 or height <= 0:
        return {}
    return {
        "x": _rounded_number(point.get("x")),
        "y": _rounded_number(point.get("y")),
        "width": width,
        "height": height,
    }


def _ancestor_signature(ancestors: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
    return [
        {key: value for key, value in item.items() if value}
        for item in ancestors[-3:]
        if item.get("role") or item.get("label")
    ]


def _ax_ref_payload(
    profile: dict[str, Any],
    node: dict[str, Any],
    *,
    path: tuple[int, ...],
    ancestors: tuple[dict[str, str], ...],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "app_id": str(profile.get("app_id") or ""),
        "role": str(node.get("role") or ""),
        "path": list(path),
    }
    for key in ("subrole", "identifier", "title", "description"):
        value = _clean_text(node.get(key), max_length=160)
        if value:
            payload[key] = value
    if not any(payload.get(key) for key in ("identifier", "title", "description")):
        value = _clean_text(node.get("value"), max_length=160)
        if value and str(node.get("role") or "") not in _EDITABLE_ROLES:
            payload["value"] = value
    ancestry = _ancestor_signature(ancestors)
    if ancestry:
        payload["ancestors"] = ancestry
    bounds = node.get("bounds") if isinstance(node.get("bounds"), dict) else {}
    if bounds:
        payload["bounds"] = dict(bounds)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return payload


def _valid_ax_ref(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    payload = {key: item for key, item in value.items() if key != "fingerprint"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    path = value.get("path")
    bounds = value.get("bounds") if isinstance(value.get("bounds"), dict) else {}
    has_geometry = bool(
        bounds
        and _rounded_number(bounds.get("width")) > 0
        and _rounded_number(bounds.get("height")) > 0
    )
    has_semantic_identity = any(
        value.get(key) for key in ("identifier", "title", "description", "value")
    )
    return bool(
        value.get("app_id")
        and value.get("role")
        and value.get("fingerprint") == expected
        and isinstance(path, list)
        and len(path) <= 96
        and all(isinstance(item, int) and 0 <= item <= 10000 for item in path)
        # Composite AppKit/SwiftUI rows often keep their visible label in a
        # child AXStaticText. A non-root path plus screen geometry is still
        # strong, reviewable evidence and is re-resolved fail-closed at action
        # time; requiring an intrinsic title made those real controls vanish.
        and (has_semantic_identity or (bool(path) and has_geometry))
    )


def _read_node(
    runtime: _AXRuntime,
    element: object,
    profile: dict[str, Any],
    *,
    path: tuple[int, ...],
    ancestors: tuple[dict[str, str], ...],
) -> dict[str, Any]:
    role = _clean_text(runtime.attribute(element, "AXRole"), max_length=80)
    subrole = _clean_text(runtime.attribute(element, "AXSubrole"), max_length=80)
    title = _clean_text(runtime.attribute(element, "AXTitle"))
    description = _clean_text(runtime.attribute(element, "AXDescription"))
    identifier = _clean_text(runtime.attribute(element, "AXIdentifier"), max_length=160)
    protected = bool(runtime.attribute(element, "AXProtectedContent")) or subrole == "AXSecureTextField"
    value = "" if protected else _clean_text(runtime.attribute(element, "AXValue"), max_length=360)
    enabled_value = runtime.attribute(element, "AXEnabled")
    focused = bool(runtime.attribute(element, "AXFocused"))
    selected = bool(runtime.attribute(element, "AXSelected"))
    bounds = _bounds(runtime.attribute(element, "AXPosition"), runtime.attribute(element, "AXSize"))
    actions = runtime.actions(element)
    supports = [_ACTION_NAMES[action] for action in actions if action in _ACTION_NAMES]
    if role in _EDITABLE_ROLES and enabled_value is not False:
        for support in ("focus", "type_text"):
            if support not in supports:
                supports.append(support)
    if role in _SELECTABLE_ROLES and enabled_value is not False and "select" not in supports:
        supports.append("select")
    label = title or description or (value if role not in _EDITABLE_ROLES else "") or identifier
    node: dict[str, Any] = {
        "role": role,
        "subrole": subrole,
        "tree_path": list(path),
        "identifier": identifier,
        "title": title,
        "description": description,
        "value": value,
        "label": _clean_text(label),
        "enabled": enabled_value is not False,
        "focused": focused,
        "selected": selected,
        "protected": protected,
        "actions": actions,
        "supports": supports,
        "bounds": bounds,
    }
    actionable = bool(supports)
    if actionable:
        ax_ref = _ax_ref_payload(profile, node, path=path, ancestors=ancestors)
        if _valid_ax_ref(ax_ref):
            node["ax_ref"] = ax_ref
    return node


def _serializable_node(node: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: value
        for key, value in node.items()
        if value not in ("", False, [], {})
        or key in {"enabled", "focused", "selected", "protected", "tree_path"}
    }
    if not result.get("protected"):
        result.pop("protected", None)
    if not result.get("focused"):
        result.pop("focused", None)
    if not result.get("selected"):
        result.pop("selected", None)
    return result


def _should_include_node(node: dict[str, Any]) -> bool:
    return bool(node.get("role"))


def _capture_tree(
    runtime: _AXRuntime,
    application: int,
    profile: dict[str, Any],
    *,
    max_elements: int,
    max_depth: int,
    timeout_sec: float,
) -> tuple[list[dict[str, Any]], int, bool]:
    queue: deque[tuple[int, tuple[int, ...], tuple[dict[str, str], ...]]] = deque(
        [(application, (), ())]
    )
    elements: list[dict[str, Any]] = []
    visited = 0
    truncated = False
    deadline = time.monotonic() + max(0.25, float(timeout_sec))
    try:
        while queue and visited < max_elements and time.monotonic() < deadline:
            element, path, ancestors = queue.popleft()
            try:
                node = _read_node(runtime, element, profile, path=path, ancestors=ancestors)
                visited += 1
                if _should_include_node(node):
                    elements.append(_serializable_node(node))
                if len(path) >= max_depth:
                    continue
                signature = {
                    "role": _clean_text(node.get("role"), max_length=80),
                    "label": _clean_text(node.get("label"), max_length=120),
                }
                remaining = max(0, max_elements - visited - len(queue))
                children, children_truncated = runtime.children(element, limit=remaining)
                truncated = truncated or children_truncated
                next_ancestors = (*ancestors, signature)
                for index, child in enumerate(children):
                    queue.append((child, (*path, index), next_ancestors))
            finally:
                runtime.release(element)
        if queue or visited >= max_elements or time.monotonic() >= deadline:
            truncated = True
    finally:
        while queue:
            runtime.release(queue.popleft()[0])
    return elements, visited, truncated


def _snapshot_text(
    profile: dict[str, Any],
    elements: list[dict[str, Any]],
    *,
    search: dict[str, Any],
) -> str:
    query = _clean_text(search.get("query"), max_length=180)
    total = max(0, int(search.get("total_element_count") or 0))
    returned = max(0, int(search.get("returned_count") or len(elements)))
    lines = [
        f"已通过 macOS Accessibility 读取 {profile.get('display_name')} 当前页面，未使用截图。",
        (
            f"持久 AX 树共有 {total} 个元素；本次只返回"
            f"{f'与“{query}”相关的' if query else '优先级最高的'} {returned} 个结果。"
        ),
        "执行语义动作时必须原样复制 affordance 里的 ax_ref；需要其他元素时用 observe.ax_query 搜索当前应用索引：",
    ]
    for element in elements:
        label = _clean_text(element.get("label") or element.get("value"), max_length=180)
        role = _clean_text(element.get("role"), max_length=60)
        if not label or role in {"AXApplication"}:
            continue
        state: list[str] = []
        if element.get("focused"):
            state.append("focused")
        if element.get("selected"):
            state.append("selected")
        supports = element.get("supports") if isinstance(element.get("supports"), list) else []
        if supports:
            state.append("supports=" + ",".join(str(item) for item in supports))
        semantic_path = _clean_text(element.get("semantic_path"), max_length=260)
        if semantic_path and semantic_path != label:
            state.append("path=" + semantic_path)
        relation = _clean_text(element.get("context_relation"), max_length=60)
        anchor = _clean_text(element.get("context_anchor"), max_length=100)
        if relation:
            state.append(f"context={relation}{f' around {anchor}' if anchor else ''}")
        suffix = f" ({'; '.join(state)})" if state else ""
        lines.append(f"- {role}: {label}{suffix}")
    if search.get("sufficient") is False:
        reason = _clean_text(search.get("insufficiency_reason"), max_length=100) or "insufficient_search_result"
        lines.append(f"- AX 搜索结果不足以独立回答或定位动作（{reason}），需要视觉证据兜底。")
    if search.get("search_truncated") or search.get("capture_truncated"):
        lines.append("- 返回结果是有界搜索切片；未列出的元素不能视为不存在。")
    return "\n".join(lines)


def _has_usable_snapshot_content(elements: list[dict[str, Any]]) -> bool:
    container_roles = {"AXApplication", "AXWindow", "AXSheet", "AXGroup", "AXScrollArea", "AXSplitter"}
    return any(
        isinstance(element.get("ax_ref"), dict)
        or (
            str(element.get("role") or "") not in container_roles
            and bool(element.get("label") or element.get("value") or element.get("identifier"))
        )
        for element in elements
    )


def search_macos_accessibility_cache(
    target_app: object,
    query: object = "",
    *,
    pid: int = 0,
    limit: int = AX_SEARCH_DEFAULT_LIMIT,
    cache: AXSnapshotCache = MACOS_AX_SNAPSHOT_CACHE,
    current_pid_provider=os.getpid,
) -> dict[str, Any]:
    profile = resolve_macos_ax_app(target_app)
    if not profile:
        return {
            "status": "unsupported",
            "reason": "target application is required for macOS Accessibility search",
        }
    resolved_pid = _positive_pid(pid or profile.get("pid"))
    if _is_self_process(resolved_pid, current_pid_provider=current_pid_provider):
        return {
            "status": "blocked",
            "app_id": str(profile.get("app_id") or ""),
            "pid": resolved_pid,
            "reason": AX_SELF_PROCESS_REASON,
        }
    return cache.search(
        _cached_app_id(profile, cache),
        pid=resolved_pid,
        query=_clean_text(query, max_length=240),
        limit=limit,
    )


def invalidate_macos_accessibility_cache(
    target_app: object,
    *,
    reason: str = "ui_changed",
    cache: AXSnapshotCache = MACOS_AX_SNAPSHOT_CACHE,
) -> None:
    profile = resolve_macos_ax_app(target_app)
    if profile:
        cache.invalidate(_cached_app_id(profile, cache), reason=reason)


def _capture_result_from_search(
    base: dict[str, Any],
    profile: dict[str, Any],
    search: dict[str, Any],
    *,
    pid: int,
    cache_hit: bool,
) -> dict[str, Any]:
    elements = search.get("elements") if isinstance(search.get("elements"), list) else []
    search_meta = {
        key: value
        for key, value in search.items()
        if key not in {"elements", "status", "app_id", "pid"}
    }
    search_meta["cache_hit"] = bool(cache_hit)
    total = max(0, int(search.get("total_element_count") or len(elements)))
    observations = [
        {
            "claim": (
                f"macOS Accessibility 已索引 {profile['display_name']} 的应用结构，"
                f"从 {total} 个元素中返回 {len(elements)} 个相关结果"
            ),
            "evidence": "bounded persistent AX tree search",
            "source": "macos_accessibility",
            "confidence": 1.0,
        }
    ]
    return {
        **base,
        "status": "success",
        "usable": True,
        "pid": int(pid),
        "snapshot_id": str(search.get("snapshot_id") or ""),
        "element_count": len(elements),
        "total_element_count": total,
        "visited_count": max(0, int(search.get("visited_count") or 0)),
        "truncated": bool(search.get("capture_truncated")),
        "elements": [dict(item) for item in elements if isinstance(item, dict)],
        "search": search_meta,
        "observations": observations,
        "text": _snapshot_text(profile, elements, search=search),
        "reason": "",
    }


def capture_macos_accessibility(
    target_app: object,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    runtime_factory=_AXRuntime,
    max_elements: int = AX_FULL_TREE_MAX_ELEMENTS,
    max_depth: int = AX_FULL_TREE_MAX_DEPTH,
    timeout_sec: float = AX_FULL_TREE_TIMEOUT_SEC,
    query: object = "",
    cache_mode: str = "refresh",
    result_limit: int = AX_SEARCH_DEFAULT_LIMIT,
    cache: AXSnapshotCache = MACOS_AX_SNAPSHOT_CACHE,
    running_apps_provider=_active_macos.enumerate_active_vision_running_app_candidates,
    current_pid_provider=os.getpid,
) -> dict[str, Any]:
    profile = resolve_macos_ax_app(target_app)
    if not profile:
        return {
            "status": "unsupported",
            "supported": False,
            "usable": False,
            "reason": "target application is required for macOS Accessibility",
        }
    def base_payload() -> dict[str, Any]:
        return {
            "supported": True,
            "usable": False,
            "app": {
                "app_id": profile["app_id"],
                "name": profile["display_name"],
                "bundle_id": profile["bundle_id"],
                "surface": profile["surface"],
            },
        }

    base = base_payload()
    if _platform_name(platform_name) != "darwin":
        return {**base, "status": "unavailable", "reason": "macOS Accessibility is only available on macOS"}
    profile = _resolve_running_profile(
        profile,
        platform_name=platform_name or sys.platform,
        runner=runner,
        running_apps_provider=running_apps_provider,
    )
    base = base_payload()
    profile_pid = _positive_pid(profile.get("pid"))
    if _is_self_process(profile_pid, current_pid_provider=current_pid_provider):
        return {
            **base,
            "status": "blocked",
            "pid": profile_pid,
            "reason": AX_SELF_PROCESS_REASON,
        }
    runtime = runtime_factory()
    try:
        if not runtime.trusted():
            return {
                **base,
                "status": "permission_required",
                "reason": "Accessibility permission is not granted to Ipet",
            }
        pid = _target_app_pid(profile, runtime, runner=runner)
        if pid <= 0:
            return {
                **base,
                "status": "unavailable",
                "reason": "target application is not frontmost or its process could not be resolved",
            }
        if _is_self_process(pid, current_pid_provider=current_pid_provider):
            return {
                **base,
                "status": "blocked",
                "pid": pid,
                "reason": AX_SELF_PROCESS_REASON,
            }
        if str(cache_mode or "").strip().lower() == "prefer_cache":
            cached = cache.search(
                _cached_app_id(profile, cache),
                pid=pid,
                limit=result_limit,
                query=_clean_text(query, max_length=240),
            )
            if cached.get("status") == "hit" and not bool(cached.get("stale")):
                return _capture_result_from_search(
                    base,
                    profile,
                    cached,
                    pid=pid,
                    cache_hit=True,
                )
        application = runtime.application(pid)
        if not application:
            return {**base, "status": "unavailable", "reason": "AXUIElementCreateApplication failed"}
        runtime.set_timeout(application, min(0.6, max(0.1, timeout_sec / 5)))
        elements, visited, truncated = _capture_tree(
            runtime,
            application,
            profile,
            max_elements=max(20, min(AX_STORE_MAX_ELEMENTS, int(max_elements))),
            max_depth=max(2, min(96, int(max_depth))),
            timeout_sec=max(0.5, min(30.0, float(timeout_sec))),
        )
        usable = _has_usable_snapshot_content(elements)
        if usable:
            cache.put(
                str(profile.get("app_id") or ""),
                app_name=str(profile.get("display_name") or ""),
                bundle_id=str(profile.get("bundle_id") or ""),
                aliases=tuple(
                    _clean_text(item, max_length=160)
                    for item in _profile_identity_values(profile)
                    if _clean_text(item, max_length=160)
                ),
                pid=pid,
                elements=elements,
                visited_count=visited,
                capture_truncated=truncated,
            )
            search = cache.search(
                str(profile.get("app_id") or ""),
                pid=pid,
                limit=result_limit,
                query=_clean_text(query, max_length=240),
            )
            if search.get("status") == "hit":
                return _capture_result_from_search(
                    base,
                    profile,
                    search,
                    pid=pid,
                    cache_hit=False,
                )
        invalidate_macos_accessibility_cache(
            profile,
            reason="refresh_returned_no_usable_elements",
            cache=cache,
        )
        return {
            **base,
            "status": "empty",
            "usable": False,
            "pid": pid,
            "element_count": 0,
            "total_element_count": len(elements),
            "visited_count": visited,
            "truncated": truncated,
            "elements": [],
            "observations": [],
            "text": "",
            "reason": "the application exposed no meaningful AX elements",
        }
    except Exception as exc:
        return {**base, "status": "error", "reason": _clean_text(exc, max_length=300)}
    finally:
        runtime.close()


def _same_ancestors(expected: object, actual: object) -> bool:
    expected_items = expected if isinstance(expected, list) else []
    actual_items = actual if isinstance(actual, list) else []
    if not expected_items:
        return True
    if len(actual_items) < len(expected_items):
        return False
    tail = actual_items[-len(expected_items) :]
    return all(
        str(left.get("role") or "") == str(right.get("role") or "")
        and (
            not str(left.get("label") or "")
            or str(left.get("label") or "") == str(right.get("label") or "")
        )
        for left, right in zip(expected_items, tail)
        if isinstance(left, dict) and isinstance(right, dict)
    )


def _match_score(ax_ref: dict[str, Any], node: dict[str, Any]) -> int:
    if str(node.get("role") or "") != str(ax_ref.get("role") or ""):
        return -1
    score = 20
    for key, weight in (("subrole", 4), ("identifier", 60), ("title", 35), ("description", 25), ("value", 20)):
        expected = _clean_text(ax_ref.get(key), max_length=160)
        if not expected:
            continue
        actual = _clean_text(node.get(key), max_length=160)
        if actual != expected:
            return -1
        score += weight
    if not _same_ancestors(ax_ref.get("ancestors"), node.get("ax_ref", {}).get("ancestors")):
        return -1
    if list(node.get("ax_ref", {}).get("path") or []) == list(ax_ref.get("path") or []):
        score += 40
    expected_bounds = ax_ref.get("bounds") if isinstance(ax_ref.get("bounds"), dict) else {}
    actual_bounds = node.get("bounds") if isinstance(node.get("bounds"), dict) else {}
    if expected_bounds and actual_bounds:
        delta = sum(
            abs(_rounded_number(expected_bounds.get(key)) - _rounded_number(actual_bounds.get(key)))
            for key in ("x", "y", "width", "height")
        )
        if delta <= 8:
            score += 24
    return score


def _resolve_element(
    runtime: _AXRuntime,
    application: int,
    profile: dict[str, Any],
    ax_ref: dict[str, Any],
    *,
    max_elements: int = AX_FULL_TREE_MAX_ELEMENTS,
    timeout_sec: float = 8.0,
) -> tuple[int, dict[str, Any]]:
    root = runtime.retain(application)
    queue: deque[tuple[int, tuple[int, ...], tuple[dict[str, str], ...]]] = deque([(root, (), ())])
    matches: list[tuple[int, int, dict[str, Any]]] = []
    visited = 0
    deadline = time.monotonic() + max(0.5, float(timeout_sec))
    try:
        while queue and visited < max_elements and time.monotonic() < deadline:
            element, path, ancestors = queue.popleft()
            try:
                node = _read_node(runtime, element, profile, path=path, ancestors=ancestors)
                visited += 1
                score = _match_score(ax_ref, node)
                if score >= 0:
                    retained = runtime.retain(element)
                    if retained:
                        matches.append((score, retained, node))
                if len(path) < AX_FULL_TREE_MAX_DEPTH:
                    signature = {
                        "role": _clean_text(node.get("role"), max_length=80),
                        "label": _clean_text(node.get("label"), max_length=120),
                    }
                    remaining = max(0, max_elements - visited - len(queue))
                    children, _ = runtime.children(element, limit=remaining)
                    next_ancestors = (*ancestors, signature)
                    for index, child in enumerate(children):
                        queue.append((child, (*path, index), next_ancestors))
            finally:
                runtime.release(element)
    finally:
        while queue:
            runtime.release(queue.popleft()[0])
    if not matches:
        raise RuntimeError("The approved AX target is stale or no longer present.")
    selected = matches[0] if len(matches) == 1 else None
    if selected is None:
        expected_path = list(ax_ref.get("path") or [])
        expected_bounds = ax_ref.get("bounds") if isinstance(ax_ref.get("bounds"), dict) else {}
        contextual: list[tuple[int, int, dict[str, Any]]] = []
        for item in matches:
            node_ref = item[2].get("ax_ref") if isinstance(item[2].get("ax_ref"), dict) else {}
            path_matches = list(node_ref.get("path") or []) == expected_path
            actual_bounds = item[2].get("bounds") if isinstance(item[2].get("bounds"), dict) else {}
            bounds_match = bool(expected_bounds and actual_bounds) and sum(
                abs(_rounded_number(expected_bounds.get(key)) - _rounded_number(actual_bounds.get(key)))
                for key in ("x", "y", "width", "height")
            ) <= 8
            if path_matches and bounds_match:
                contextual.append(item)
        if len(contextual) == 1:
            selected = contextual[0]
    if selected is None:
        for item in matches:
            runtime.release(item[1])
        raise RuntimeError("The approved AX target is ambiguous in the current interface.")
    for item in matches:
        if item is not selected:
            runtime.release(item[1])
    return selected[1], selected[2]


def perform_macos_accessibility_action(
    target_app: object,
    ax_ref: object,
    *,
    operation: str = "press",
    platform_name: str | None = None,
    runner=subprocess.run,
    runtime_factory=_AXRuntime,
    cache: AXSnapshotCache = MACOS_AX_SNAPSHOT_CACHE,
    running_apps_provider=_active_macos.enumerate_active_vision_running_app_candidates,
    current_pid_provider=os.getpid,
) -> dict[str, Any]:
    profile = resolve_macos_ax_app(target_app)
    if not profile:
        raise RuntimeError("The target application is required for macOS Accessibility.")
    if _platform_name(platform_name) != "darwin":
        raise RuntimeError("macOS Accessibility actions are only available on macOS.")
    reference = dict(ax_ref) if isinstance(ax_ref, dict) else {}
    if not _valid_ax_ref(reference):
        raise RuntimeError("The approved AX target reference is invalid.")
    profile = _resolve_running_profile(
        profile,
        platform_name=platform_name or sys.platform,
        runner=runner,
        running_apps_provider=running_apps_provider,
    )
    if _is_self_process(profile.get("pid"), current_pid_provider=current_pid_provider):
        raise RuntimeError(AX_SELF_PROCESS_REASON)
    runtime = runtime_factory()
    application = 0
    element = 0
    try:
        if not runtime.trusted():
            raise RuntimeError("Accessibility permission is not granted to Ipet.")
        reference_app_id = str(reference.get("app_id") or "")
        if reference_app_id != str(profile.get("app_id") or ""):
            cached_app_id = _cached_app_id(profile, cache)
            if reference_app_id != cached_app_id:
                raise RuntimeError("The approved AX target belongs to a different application.")
            profile = {**profile, "app_id": cached_app_id}
        pid = _target_app_pid(profile, runtime, runner=runner)
        if pid <= 0:
            raise RuntimeError("The target application is not frontmost or its process could not be resolved.")
        if _is_self_process(pid, current_pid_provider=current_pid_provider):
            raise RuntimeError(AX_SELF_PROCESS_REASON)
        application = runtime.application(pid)
        if not application:
            raise RuntimeError("AXUIElementCreateApplication failed.")
        runtime.set_timeout(application, 0.5)
        element, node = _resolve_element(runtime, application, profile, reference)
        normalized_operation = str(operation or "press").strip().lower()
        role = str(node.get("role") or "")
        actions = node.get("actions") if isinstance(node.get("actions"), list) else []
        ax_action = ""
        if normalized_operation == "focus":
            if role not in _EDITABLE_ROLES:
                raise RuntimeError("The approved AX target is not an editable element.")
            runtime.set_boolean_attribute(element, "AXFocused", True)
            if not bool(runtime.attribute(element, "AXFocused")):
                raise RuntimeError("macOS Accessibility could not verify input focus.")
            ax_action = "AXFocused"
        elif "AXPress" in actions:
            runtime.perform(element, "AXPress")
            ax_action = "AXPress"
        elif role in _EDITABLE_ROLES:
            runtime.set_boolean_attribute(element, "AXFocused", True)
            if not bool(runtime.attribute(element, "AXFocused")):
                raise RuntimeError("macOS Accessibility could not verify input focus.")
            ax_action = "AXFocused"
        elif role in _SELECTABLE_ROLES:
            runtime.set_boolean_attribute(element, "AXSelected", True)
            if not bool(runtime.attribute(element, "AXSelected")):
                raise RuntimeError("macOS Accessibility could not verify element selection.")
            ax_action = "AXSelected"
        else:
            raise RuntimeError("The approved AX target exposes no safe semantic click action.")
        result = {
            "ax_target_verified": True,
            "ax_action_performed": True,
            "ax_action": ax_action,
            "ax_app_id": profile["app_id"],
            "ax_role": role,
            "ax_label": _clean_text(node.get("label"), max_length=160),
            "method": "macos_accessibility",
        }
        invalidate_macos_accessibility_cache(
            profile,
            reason="semantic_action_performed",
            cache=cache,
        )
        return result
    finally:
        if element:
            runtime.release(element)
        if application:
            runtime.release(application)
        runtime.close()


def macos_accessibility_index_status(
    *,
    cache: AXSnapshotCache = MACOS_AX_SNAPSHOT_CACHE,
) -> dict[str, Any]:
    raw = cache.status()
    apps = []
    for raw_item in raw.get("apps") if isinstance(raw.get("apps"), list) else []:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        known = _known_macos_ax_profile(
            item.get("app_id"),
            item.get("app_name"),
            item.get("bundle_id"),
            *(item.get("aliases") if isinstance(item.get("aliases"), list) else []),
        )
        apps.append(
            {
                **item,
                "name": str(
                    item.get("app_name")
                    or known.get("display_name")
                    or item.get("app_id")
                    or ""
                ),
                "bundle_id": str(item.get("bundle_id") or known.get("bundle_id") or ""),
                "stored": True,
            }
        )
    return {
        **raw,
        "scope": "running_macos_gui_applications",
        "supports_dynamic_apps": True,
        "known_alias_count": len(MACOS_AX_APP_PROFILES),
        "apps": apps,
    }


def refresh_macos_accessibility_index(
    *,
    target_apps: list[object] | tuple[object, ...] | None = None,
    platform_name: str | None = None,
    runner=subprocess.run,
    runtime_factory=_AXRuntime,
    cache: AXSnapshotCache = MACOS_AX_SNAPSHOT_CACHE,
    running_apps_provider=_active_macos.enumerate_active_vision_running_app_candidates,
    current_pid_provider=os.getpid,
) -> dict[str, Any]:
    if _platform_name(platform_name) != "darwin":
        return {
            "status": "unavailable",
            "reason": "macOS Accessibility is only available on macOS",
            **macos_accessibility_index_status(cache=cache),
        }
    if not _AX_INDEX_REFRESH_LOCK.acquire(blocking=False):
        return {
            "status": "busy",
            "reason": "an Accessibility index refresh is already running",
            **macos_accessibility_index_status(cache=cache),
        }
    try:
        profiles: list[dict[str, Any]] = []
        self_excluded_apps: list[dict[str, Any]] = []
        self_excluded_ids: set[str] = set()
        enumeration_errors: list[str] = []
        discovery_truncated = False
        stored_before = (
            cache.status().get("apps", [])
            if target_apps is None
            else []
        )
        if target_apps:
            seen: set[str] = set()
            for target_app in target_apps:
                profile = resolve_macos_ax_app(target_app)
                profile = _resolve_running_profile(
                    profile,
                    platform_name=platform_name or sys.platform,
                    runner=runner,
                    running_apps_provider=running_apps_provider,
                )
                app_id = str(profile.get("app_id") or "")
                pid = _positive_pid(profile.get("pid"))
                if app_id and _is_self_process(pid, current_pid_provider=current_pid_provider):
                    if app_id not in self_excluded_ids:
                        self_excluded_apps.append(
                            {
                                "app_id": app_id,
                                "name": str(profile.get("display_name") or app_id),
                                "pid": pid,
                                "reason": AX_SELF_PROCESS_REASON,
                            }
                        )
                        self_excluded_ids.add(app_id)
                    continue
                if profile and app_id not in seen:
                    profiles.append(profile)
                    seen.add(app_id)
        else:
            running_apps, enumeration_errors = _enumerate_running_ax_apps(
                platform_name=platform_name or sys.platform,
                runner=runner,
                provider=running_apps_provider,
            )
            discovery_truncated = len(running_apps) >= AX_REFRESH_MAX_APPS
            seen: set[str] = set()
            for app in running_apps:
                profile = resolve_macos_ax_app(app)
                app_id = str(profile.get("app_id") or "")
                pid = _positive_pid(profile.get("pid"))
                if pid <= 0:
                    pid = _running_app_pid(profile, runner=runner)
                    profile = {**profile, "pid": pid}
                if app_id and _is_self_process(pid, current_pid_provider=current_pid_provider):
                    if app_id not in self_excluded_ids:
                        self_excluded_apps.append(
                            {
                                "app_id": app_id,
                                "name": str(profile.get("display_name") or app_id),
                                "pid": pid,
                                "reason": AX_SELF_PROCESS_REASON,
                            }
                        )
                        self_excluded_ids.add(app_id)
                    continue
                if not app_id or pid <= 0 or app_id in seen:
                    continue
                profiles.append(profile)
                seen.add(app_id)

        updates: list[dict[str, Any]] = []
        running_app_ids = {str(profile.get("app_id") or "") for profile in profiles}
        for item in stored_before:
            if not isinstance(item, dict):
                continue
            app_id = str(item.get("app_id") or "")
            if not app_id or app_id in running_app_ids:
                continue
            self_excluded = app_id in self_excluded_ids
            stale_reason = "self_process_excluded" if self_excluded else "refresh_not_running"
            cache.invalidate(app_id, reason=stale_reason)
            updates.append(
                {
                    "app_id": app_id,
                    "name": str(item.get("app_name") or app_id),
                    "status": "blocked" if self_excluded else "unavailable",
                    "updated": False,
                    "element_count": max(0, int(item.get("element_count") or 0)),
                    "visited_count": max(0, int(item.get("visited_count") or 0)),
                    "capture_truncated": bool(item.get("capture_truncated")),
                    "reason": AX_SELF_PROCESS_REASON if self_excluded else "application is not currently running",
                }
            )
        for profile in profiles:
            result = capture_macos_accessibility(
                profile,
                platform_name=platform_name,
                runner=runner,
                runtime_factory=runtime_factory,
                max_elements=AX_FULL_TREE_MAX_ELEMENTS,
                max_depth=AX_FULL_TREE_MAX_DEPTH,
                timeout_sec=AX_FULL_TREE_TIMEOUT_SEC,
                query="",
                cache_mode="refresh",
                result_limit=1,
                cache=cache,
                running_apps_provider=running_apps_provider,
                current_pid_provider=current_pid_provider,
            )
            result_status = str(result.get("status") or "error")
            if result_status != "success":
                cache.invalidate(
                    str(profile.get("app_id") or ""),
                    reason=f"refresh_{result_status}",
                )
            updates.append(
                {
                    "app_id": str(profile.get("app_id") or ""),
                    "name": str(profile.get("display_name") or ""),
                    "status": result_status,
                    "updated": result_status == "success",
                    "element_count": max(0, int(result.get("total_element_count") or 0)),
                    "visited_count": max(0, int(result.get("visited_count") or 0)),
                    "capture_truncated": bool(result.get("truncated")),
                    "reason": _clean_text(result.get("reason"), max_length=240),
                }
            )
        status = macos_accessibility_index_status(cache=cache)
        overall_status = "success" if profiles or not enumeration_errors else "error"
        return {
            **status,
            "status": overall_status,
            "reason": (
                ""
                if overall_status == "success"
                else "running macOS GUI applications could not be enumerated"
            ),
            "discovered_count": len(profiles),
            "discovery_truncated": discovery_truncated,
            "enumeration_errors": enumeration_errors,
            "self_excluded_count": len(self_excluded_apps),
            "self_excluded_apps": self_excluded_apps,
            "updated_count": sum(bool(item.get("updated")) for item in updates),
            "skipped_count": sum(not bool(item.get("updated")) for item in updates),
            "updates": updates,
        }
    finally:
        _AX_INDEX_REFRESH_LOCK.release()
