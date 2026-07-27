from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import deque
from contextlib import contextmanager
from functools import wraps
from threading import Condition, Lock
from typing import Any

from body import active_vision_macos as _active_macos
from body.macos_accessibility_cache import (
    AX_SEARCH_DEFAULT_LIMIT,
    AX_STORE_MAX_ELEMENTS,
    AXSnapshotCache,
    MACOS_AX_SNAPSHOT_CACHE,
    snapshot_window_signatures,
)


AX_FULL_TREE_MAX_ELEMENTS = 20_000
AX_FULL_TREE_MAX_DEPTH = 64
AX_FULL_TREE_TIMEOUT_SEC = 15.0
AX_BACKGROUND_REFRESH_TIMEOUT_SEC = 5.0
AX_NATIVE_FOREGROUND_WAIT_TIMEOUT_SEC = 16.0
AX_NATIVE_BACKGROUND_WAIT_TIMEOUT_SEC = 5.0
AX_REFRESH_MAX_APPS = 128
AX_SELF_PROCESS_REASON = "Ipet refuses to inspect or modify its own Accessibility process tree"
_AX_INDEX_REFRESH_LOCK = Lock()
_AX_NATIVE_OPERATION_CONDITION = Condition()
_AX_NATIVE_OPERATION_ACTIVE = False
_AX_NATIVE_FOREGROUND_WAITERS = 0


@contextmanager
def _ax_native_operation(*, foreground: bool):
    global _AX_NATIVE_OPERATION_ACTIVE, _AX_NATIVE_FOREGROUND_WAITERS
    with _AX_NATIVE_OPERATION_CONDITION:
        if foreground:
            _AX_NATIVE_FOREGROUND_WAITERS += 1
        wait_timeout = (
            AX_NATIVE_FOREGROUND_WAIT_TIMEOUT_SEC
            if foreground
            else AX_NATIVE_BACKGROUND_WAIT_TIMEOUT_SEC
        )
        wait_deadline = time.monotonic() + wait_timeout
        try:
            while _AX_NATIVE_OPERATION_ACTIVE or (
                not foreground and _AX_NATIVE_FOREGROUND_WAITERS
            ):
                remaining = wait_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "timed out waiting for serialized macOS "
                        "Accessibility access"
                    )
                _AX_NATIVE_OPERATION_CONDITION.wait(timeout=remaining)
            _AX_NATIVE_OPERATION_ACTIVE = True
        except Exception:
            if foreground:
                _AX_NATIVE_FOREGROUND_WAITERS -= 1
                _AX_NATIVE_OPERATION_CONDITION.notify_all()
            raise
    try:
        yield
    finally:
        with _AX_NATIVE_OPERATION_CONDITION:
            _AX_NATIVE_OPERATION_ACTIVE = False
            if foreground:
                _AX_NATIVE_FOREGROUND_WAITERS -= 1
            _AX_NATIVE_OPERATION_CONDITION.notify_all()


def _serialize_ax_native_call(*, background_flag: str = ""):
    def decorate(function):
        @wraps(function)
        def serialized(*args, **kwargs):
            foreground = not (
                background_flag and bool(kwargs.get(background_flag))
            )
            with _ax_native_operation(foreground=foreground):
                return function(*args, **kwargs)

        return serialized

    return decorate


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
_HIT_TEST_ROLES = {"AXGroup", "AXStaticText", "AXImage", "AXWebArea"}
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
    "AXOpen": "open",
    "AXConfirm": "confirm",
    "AXCancel": "cancel",
    "AXShowMenu": "show_menu",
    "AXIncrement": "increment",
    "AXDecrement": "decrement",
    "AXRaise": "raise",
}
_LIVE_STATE_QUERY_TERMS = (
    "当前",
    "现在",
    "正在",
    "是否",
    "可见",
    "标题",
    "焦点",
    "聚焦",
    "选中状态",
    "已选中",
    "前台",
    "哪些",
    "所有",
    "列出",
    "列表",
    "多少",
    "有什么",
    "current",
    "active",
    "visible",
    "focused",
    "selected",
    "frontmost",
    "list all",
    "how many",
    "what are",
    "搜索",
    "查找",
    "输入框",
    "编辑框",
    "打开",
    "进入",
    "选择",
    "点击",
    "按下",
    "search",
    "find",
    "input",
    "editor",
    "open",
    "select",
    "click",
    "press",
)


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


def _host_process_tree_pids(
    *,
    current_pid_provider=os.getpid,
    runner=subprocess.run,
) -> set[int]:
    """Return the host PID and every currently running descendant.

    QtWebEngine helpers are regular GUI processes on macOS.  Treating one as
    an external AX target can synchronously call back into Chromium from our
    worker thread and abort the whole host.  Process-tree discovery is
    therefore a safety boundary, not merely an enumeration optimization.
    """

    root_pid = _current_process_pid(current_pid_provider)
    if root_pid <= 0:
        return set()
    protected = {root_pid}
    try:
        result = runner(
            ["/bin/ps", "-axo", "pid=,ppid="],
            capture_output=True,
            text=True,
            timeout=0.8,
            check=False,
        )
    except Exception:
        return protected
    if getattr(result, "returncode", 1) != 0:
        return protected
    children_by_parent: dict[int, set[int]] = {}
    for line in str(getattr(result, "stdout", "") or "").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        pid = _positive_pid(parts[0])
        parent_pid = _positive_pid(parts[1])
        if pid <= 0 or parent_pid <= 0:
            continue
        children_by_parent.setdefault(parent_pid, set()).add(pid)
    pending = [root_pid]
    while pending:
        parent_pid = pending.pop()
        for child_pid in children_by_parent.get(parent_pid, ()):
            if child_pid in protected:
                continue
            protected.add(child_pid)
            pending.append(child_pid)
    return protected


def _is_host_process(
    pid: object,
    *,
    host_process_pids: set[int],
) -> bool:
    resolved_pid = _positive_pid(pid)
    return bool(resolved_pid and resolved_pid in host_process_pids)


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


def _query_requires_live_state(query: object) -> bool:
    intent = _clean_text(query, max_length=240).casefold()
    return bool(intent and any(term in intent for term in _LIVE_STATE_QUERY_TERMS))


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


def _running_bundle_pid(profile: dict[str, Any]) -> int:
    bundle_id = _clean_text(profile.get("bundle_id"), max_length=200)
    if not bundle_id:
        return 0
    try:
        import AppKit  # type: ignore

        candidates: list[tuple[int, int, int]] = []
        for application in (
            AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(
                bundle_id
            )
            or []
        ):
            pid = _positive_pid(application.processIdentifier())
            if pid <= 0 or bool(application.isTerminated()):
                continue
            try:
                regular = int(application.activationPolicy()) == 0
            except Exception:
                regular = True
            candidates.append(
                (
                    0 if regular else 1,
                    0 if bool(application.isActive()) else 1,
                    pid,
                )
            )
        if candidates:
            candidates.sort()
            return candidates[0][2]
    except Exception:
        return 0
    return 0


def _target_app_pid(profile: dict[str, Any], runtime: "_AXRuntime", *, runner=subprocess.run) -> int:
    focused_pid, focused_name = runtime.focused_application()
    if focused_pid > 0 and _profile_matches_app_name(profile, focused_name):
        return focused_pid
    profile_pid = _positive_pid(profile.get("pid"))
    if profile_pid > 0:
        return profile_pid
    bundle_pid = _running_bundle_pid(profile)
    if bundle_pid > 0:
        return bundle_pid
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
        self._application_elements: set[int] = set()
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
        self.core.CFEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.core.CFEqual.restype = ctypes.c_bool
        self.core.CFHash.argtypes = [ctypes.c_void_p]
        self.core.CFHash.restype = ctypes.c_ulong
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
        self.core.CFArrayCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_long,
            ctypes.c_void_p,
        ]
        self.core.CFArrayCreate.restype = ctypes.c_void_p
        self.core.CFURLGetTypeID.restype = ctypes.c_ulong
        self.core.CFURLGetString.argtypes = [ctypes.c_void_p]
        self.core.CFURLGetString.restype = ctypes.c_void_p

        self.ax.AXIsProcessTrusted.argtypes = []
        self.ax.AXIsProcessTrusted.restype = ctypes.c_bool
        self.ax.AXUIElementCreateSystemWide.argtypes = []
        self.ax.AXUIElementCreateSystemWide.restype = ctypes.c_void_p
        self.ax.AXUIElementCreateApplication.argtypes = [ctypes.c_int]
        self.ax.AXUIElementCreateApplication.restype = ctypes.c_void_p
        self.ax.AXUIElementGetPid.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        self.ax.AXUIElementGetPid.restype = ctypes.c_int
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
        copy_multiple = getattr(
            self.ax,
            "AXUIElementCopyMultipleAttributeValues",
            None,
        )
        if copy_multiple is not None:
            copy_multiple.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            copy_multiple.restype = ctypes.c_int
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
        self._application_elements.clear()

    def trusted(self) -> bool:
        return bool(self.ax.AXIsProcessTrusted())

    def application(self, pid: int) -> int:
        resolved_pid = _positive_pid(pid)
        if resolved_pid <= 0:
            return 0
        if resolved_pid == _positive_pid(os.getpid()):
            raise RuntimeError(AX_SELF_PROCESS_REASON)
        value = self.ax.AXUIElementCreateApplication(resolved_pid)
        application = int(value or 0)
        if application:
            self._application_elements.add(application)
        return application

    def focused_application(self) -> tuple[int, str]:
        try:
            import AppKit  # type: ignore

            application = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if application is None:
                return 0, ""
            pid = _positive_pid(application.processIdentifier())
            name = _clean_text(application.localizedName(), max_length=120)
        except Exception:
            system_wide = int(self.ax.AXUIElementCreateSystemWide() or 0)
            focused = ctypes.c_void_p()
            if not system_wide:
                return 0, ""
            try:
                error = self.ax.AXUIElementCopyAttributeValue(
                    self._pointer(system_wide),
                    self._pointer(self._cf_string("AXFocusedApplication")),
                    ctypes.byref(focused),
                )
                if error != 0 or not focused.value:
                    return 0, ""
                pid_value = ctypes.c_int()
                if (
                    self.ax.AXUIElementGetPid(
                        focused,
                        ctypes.byref(pid_value),
                    )
                    != 0
                ):
                    return 0, ""
                pid = _positive_pid(pid_value.value)
                name = _clean_text(
                    self.attribute(focused, "AXTitle")
                    or self.attribute(focused, "AXDescription"),
                    max_length=120,
                )
            finally:
                if focused.value:
                    self.release(focused)
                self.release(system_wide)
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

    def element_identity(self, value: object) -> int:
        pointer = self._pointer(value)
        return int(self.core.CFHash(pointer)) if pointer.value else 0

    def elements_equal(self, left: object, right: object) -> bool:
        left_pointer = self._pointer(left)
        right_pointer = self._pointer(right)
        return bool(
            left_pointer.value
            and right_pointer.value
            and self.core.CFEqual(left_pointer, right_pointer)
        )

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
        if type_id == int(self.core.CFURLGetTypeID()):
            string_pointer = self.core.CFURLGetString(pointer)
            return self._string_value(string_pointer) if string_pointer else ""
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

    def attributes(
        self,
        element: object,
        names: tuple[str, ...],
    ) -> dict[str, object]:
        clean_names = tuple(str(name or "").strip() for name in names if str(name or "").strip())
        if not clean_names:
            return {}
        copy_multiple = getattr(
            self.ax,
            "AXUIElementCopyMultipleAttributeValues",
            None,
        )
        if copy_multiple is None:
            return {name: self.attribute(element, name) for name in clean_names}
        pointers = (ctypes.c_void_p * len(clean_names))(
            *(self._cf_string(name) for name in clean_names)
        )
        attributes_array = self.core.CFArrayCreate(
            None,
            pointers,
            len(clean_names),
            None,
        )
        if not attributes_array:
            return {name: self.attribute(element, name) for name in clean_names}
        output = ctypes.c_void_p()
        try:
            error = copy_multiple(
                self._pointer(element),
                self._pointer(attributes_array),
                0,
                ctypes.byref(output),
            )
            if error != 0 or not output.value:
                return {name: self.attribute(element, name) for name in clean_names}
            if int(self.core.CFGetTypeID(output)) != int(self.core.CFArrayGetTypeID()):
                return {name: self.attribute(element, name) for name in clean_names}
            count = int(self.core.CFArrayGetCount(output))
            if count != len(clean_names):
                return {name: self.attribute(element, name) for name in clean_names}
            return {
                name: self._python_value(
                    self.core.CFArrayGetValueAtIndex(output, index)
                )
                for index, name in enumerate(clean_names)
            }
        finally:
            if output.value:
                self.release(output)
            self.release(attributes_array)

    def _attribute_elements(
        self,
        element: object,
        attribute_name: str,
        *,
        limit: int,
    ) -> tuple[list[int], bool]:
        attribute = self._pointer(self._cf_string(attribute_name))
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

    def _attribute_element(
        self,
        element: object,
        attribute_name: str,
    ) -> int:
        output = ctypes.c_void_p()
        error = self.ax.AXUIElementCopyAttributeValue(
            self._pointer(element),
            self._pointer(self._cf_string(attribute_name)),
            ctypes.byref(output),
        )
        if error != 0 or not output.value:
            return 0
        if int(self.core.CFGetTypeID(output)) == int(
            self.ax.AXUIElementGetTypeID()
        ):
            return int(output.value)
        self.release(output)
        return 0

    def focused_ui_element(self, application: object) -> int:
        """Return a retained reference to the application's focused AX element."""
        return self._attribute_element(application, "AXFocusedUIElement")

    def _element_identity_signature(self, element: object) -> tuple[object, ...]:
        values = self.attributes(
            element,
            (
                "AXRole",
                "AXIdentifier",
                "AXTitle",
                "AXDescription",
                "AXPosition",
                "AXSize",
            ),
        )
        position = values.get("AXPosition") if isinstance(values.get("AXPosition"), dict) else {}
        size = values.get("AXSize") if isinstance(values.get("AXSize"), dict) else {}
        return (
            _clean_text(values.get("AXRole"), max_length=80),
            _clean_text(values.get("AXIdentifier"), max_length=160),
            _clean_text(values.get("AXTitle"), max_length=160),
            _clean_text(values.get("AXDescription"), max_length=160),
            _rounded_number(position.get("x")),
            _rounded_number(position.get("y")),
            _rounded_number(size.get("width")),
            _rounded_number(size.get("height")),
        )

    def children(self, element: object, *, limit: int) -> tuple[list[int], bool]:
        requested_limit = max(0, int(limit))
        children, truncated = self._attribute_elements(
            element,
            "AXChildren",
            limit=requested_limit,
        )
        element_pointer = int(self._pointer(element).value or 0)
        if (
            element_pointer not in self._application_elements
            or len(children) >= requested_limit
        ):
            return children, truncated
        windows, windows_truncated = self._attribute_elements(
            element,
            "AXWindows",
            limit=requested_limit - len(children),
        )
        child_signatures: set[tuple[object, ...]] = set()
        for child in children:
            signature = self._element_identity_signature(child)
            if any(signature[1:]):
                child_signatures.add(signature)

        def append_window(window: int) -> None:
            signature = self._element_identity_signature(window)
            if (
                window in children
                or (any(signature[1:]) and signature in child_signatures)
            ):
                self.release(window)
                return
            if any(signature[1:]):
                child_signatures.add(signature)
            children.append(window)

        for window in windows:
            append_window(window)
        for attribute_name in ("AXFocusedWindow", "AXMainWindow"):
            if len(children) >= requested_limit:
                break
            window = self._attribute_element(element, attribute_name)
            if window:
                append_window(window)
        return children, truncated or windows_truncated

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

    def attribute_settable(self, element: object, name: str) -> bool:
        settable = ctypes.c_bool(False)
        error = self.ax.AXUIElementIsAttributeSettable(
            self._pointer(element),
            self._pointer(self._cf_string(name)),
            ctypes.byref(settable),
        )
        return bool(error == 0 and settable.value)

    def set_boolean_attribute(self, element: object, name: str, value: bool) -> None:
        attribute = self._pointer(self._cf_string(name))
        if not self.attribute_settable(element, name):
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

    def enable_manual_accessibility(self, application: object) -> bool:
        """Ask Chromium/Electron apps to expose their web accessibility tree."""
        boolean = ctypes.c_void_p.in_dll(self.core, "kCFBooleanTrue").value
        error = self.ax.AXUIElementSetAttributeValue(
            self._pointer(application),
            self._pointer(self._cf_string("AXManualAccessibility")),
            self._pointer(boolean),
        )
        return error == 0


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


def _ancestor_signature(
    ancestors: tuple[dict[str, Any], ...],
) -> list[dict[str, str]]:
    return [
        {
            key: str(item.get(key) or "")
            for key in ("role", "label")
            if item.get(key)
        }
        for item in ancestors[-3:]
        if item.get("role") or item.get("label")
    ]


def _ax_ref_payload(
    profile: dict[str, Any],
    node: dict[str, Any],
    *,
    path: tuple[int, ...],
    ancestors: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "app_id": str(profile.get("app_id") or ""),
        "role": str(node.get("role") or ""),
        "path": list(path),
    }
    for key in (
        "subrole",
        "identifier",
        "title",
        "description",
        "placeholder",
        "url",
    ):
        value = _clean_text(
            node.get(key),
            max_length=600 if key == "url" else 160,
        )
        if value:
            payload[key] = value
    if not any(payload.get(key) for key in ("identifier", "title", "description", "url")):
        value = _clean_text(node.get("value"), max_length=160)
        if value and str(node.get("role") or "") not in _EDITABLE_ROLES:
            payload["value"] = value
    ancestry = _ancestor_signature(ancestors)
    if ancestry:
        payload["ancestors"] = ancestry
    bounds = node.get("bounds") if isinstance(node.get("bounds"), dict) else {}
    if bounds:
        payload["bounds"] = dict(bounds)
    role = str(node.get("role") or "")
    supports = {
        str(item or "")
        for item in (
            node.get("supports")
            if isinstance(node.get("supports"), list)
            else []
        )
    }
    if role in _EDITABLE_ROLES and "type_text" in supports:
        editor_semantics = " ".join(
            _clean_text(node.get(key), max_length=180)
            for key in (
                "label",
                "title",
                "description",
                "placeholder",
                "identifier",
            )
        ).casefold()
        bounds = (
            node.get("bounds")
            if isinstance(node.get("bounds"), dict)
            else {}
        )
        window_bounds = next(
            (
                item.get("bounds")
                for item in reversed(ancestors)
                if str(item.get("role") or "") in {"AXWindow", "AXSheet"}
                and isinstance(item.get("bounds"), dict)
            ),
            {},
        )
        editor_width = _rounded_number(bounds.get("width"))
        editor_height = _rounded_number(bounds.get("height"))
        editor_center_y = (
            _rounded_number(bounds.get("y"))
            + editor_height // 2
        )
        window_width = _rounded_number(window_bounds.get("width"))
        window_height = _rounded_number(window_bounds.get("height"))
        window_top = _rounded_number(window_bounds.get("y"))
        compact_chat_search_shape = bool(
            str(profile.get("app_id") or "") in {"qq", "wechat"}
            and role in {"AXTextField", "AXSearchField", "AXComboBox"}
            and window_width >= 400
            and window_height >= 240
            and 40 <= editor_width <= min(420, int(window_width * 0.55))
            and 12 <= editor_height <= 64
            and window_top
            <= editor_center_y
            <= window_top + min(140, int(window_height * 0.30))
        )
        if (
            role == "AXSearchField"
            or compact_chat_search_shape
            or any(
                term in editor_semantics
                for term in (
                    "搜索",
                    "查找",
                    "筛选",
                    "过滤",
                    "search",
                    "find",
                    "filter",
                )
            )
        ):
            payload["input_kind"] = "search_field"
        elif str(profile.get("app_id") or "") in {"qq", "wechat"}:
            # Fail closed for chat applications: only an explicitly
            # identified and signed search field bypasses recipient binding.
            payload["input_kind"] = "chat_message"
        else:
            payload["input_kind"] = "input"
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
        value.get(key) for key in ("identifier", "title", "description", "value", "url")
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
    ancestors: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    read_attributes = getattr(runtime, "attributes", None)

    def read_many(names: tuple[str, ...]) -> dict[str, object]:
        if callable(read_attributes):
            values = read_attributes(element, names)
            if isinstance(values, dict):
                return values
        return {name: runtime.attribute(element, name) for name in names}

    identity = read_many(("AXRole", "AXSubrole", "AXProtectedContent"))
    role = _clean_text(identity.get("AXRole"), max_length=80)
    subrole = _clean_text(identity.get("AXSubrole"), max_length=80)
    protected = bool(identity.get("AXProtectedContent")) or subrole == "AXSecureTextField"
    detail_names = (
        "AXTitle",
        "AXDescription",
        "AXIdentifier",
        "AXPlaceholderValue",
        "AXEnabled",
        "AXFocused",
        "AXSelected",
        "AXURL",
        "AXPosition",
        "AXSize",
        *((("AXValue",) if not protected else ())),
    )
    details = read_many(detail_names)
    title = _clean_text(details.get("AXTitle"))
    description = _clean_text(details.get("AXDescription"))
    identifier = _clean_text(details.get("AXIdentifier"), max_length=160)
    placeholder = _clean_text(
        details.get("AXPlaceholderValue"),
        max_length=160,
    )
    value = "" if protected else _clean_text(details.get("AXValue"), max_length=360)
    url = _clean_text(details.get("AXURL"), max_length=600)
    enabled_value = details.get("AXEnabled")
    focused = bool(details.get("AXFocused"))
    selected = bool(details.get("AXSelected"))
    bounds = _bounds(details.get("AXPosition"), details.get("AXSize"))
    actions = runtime.actions(element)
    supports = [_ACTION_NAMES[action] for action in actions if action in _ACTION_NAMES]
    file_backed_item = role == "AXTextField" and url.casefold().startswith("file:")
    settable_provider = getattr(runtime, "attribute_settable", None)
    focus_settable = (
        bool(settable_provider(element, "AXFocused"))
        if callable(settable_provider)
        else True
    )
    if (
        role in _EDITABLE_ROLES
        and not file_backed_item
        and enabled_value is not False
        and focus_settable
    ):
        for support in ("focus", "type_text"):
            if support not in supports:
                supports.append(support)
    if role in _SELECTABLE_ROLES and enabled_value is not False and "select" not in supports:
        supports.append("select")
    label = (
        title
        or description
        or placeholder
        or (value if role not in _EDITABLE_ROLES or file_backed_item else "")
        or identifier
    )
    node: dict[str, Any] = {
        "role": role,
        "subrole": subrole,
        "tree_path": list(path),
        "identifier": identifier,
        "title": title,
        "description": description,
        "placeholder": placeholder,
        "value": value,
        "url": url,
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
    root = runtime.retain(application)
    if not root:
        return [], 0, False
    identity_provider = getattr(runtime, "element_identity", None)

    def element_identity(value: object) -> int | None:
        if callable(identity_provider):
            try:
                # CFHash may legally be zero.  None means that identity
                # extraction failed; zero is still a valid collision bucket
                # and must participate in CFEqual cycle detection.
                return int(identity_provider(value))
            except (TypeError, ValueError):
                return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    equality_provider = getattr(runtime, "elements_equal", None)

    def elements_equal(left: object, right: object) -> bool:
        if callable(equality_provider):
            try:
                return bool(equality_provider(left, right))
            except Exception:
                return False
        return int(left or 0) == int(right or 0)

    root_identity = element_identity(root)
    discovered: dict[int, list[int]] = (
        {root_identity: [root]}
        if root_identity is not None
        else {}
    )
    owned_elements: list[int] = [root]
    queue: deque[tuple[int, tuple[int, ...], tuple[dict[str, Any], ...]]] = deque(
        [(root, (), ())]
    )
    elements: list[dict[str, Any]] = []
    visited = 0
    truncated = False
    deadline = time.monotonic() + max(0.25, float(timeout_sec))
    try:
        while queue and visited < max_elements and time.monotonic() < deadline:
            element, path, ancestors = queue.popleft()
            node = _read_node(runtime, element, profile, path=path, ancestors=ancestors)
            visited += 1
            if _should_include_node(node):
                elements.append(_serializable_node(node))
            if len(path) >= max_depth:
                depth_children, depth_truncated = runtime.children(
                    element,
                    limit=1,
                )
                if depth_children or depth_truncated:
                    truncated = True
                for child in depth_children:
                    runtime.release(child)
                continue
            signature = {
                "role": _clean_text(node.get("role"), max_length=80),
                "label": _clean_text(node.get("label"), max_length=120),
            }
            if (
                signature["role"] in {"AXWindow", "AXSheet"}
                and isinstance(node.get("bounds"), dict)
                and node.get("bounds")
            ):
                signature["bounds"] = dict(node["bounds"])
            remaining = max(0, max_elements - visited - len(queue))
            children, children_truncated = runtime.children(element, limit=remaining)
            truncated = truncated or children_truncated
            next_ancestors = (*ancestors, signature)
            for index, child in enumerate(children):
                child_identity = element_identity(child)
                if child_identity is not None:
                    if any(
                        elements_equal(existing, child)
                        for existing in discovered.get(child_identity, ())
                    ):
                        runtime.release(child)
                        continue
                    discovered.setdefault(child_identity, []).append(child)
                owned_elements.append(child)
                queue.append((child, (*path, index), next_ancestors))
        if queue or visited >= max_elements or time.monotonic() >= deadline:
            truncated = True
    finally:
        for element in reversed(owned_elements):
            runtime.release(element)
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
    collection_total = max(0, int(search.get("collection_item_total_count") or 0))
    collection_returned = max(0, int(search.get("collection_item_returned_count") or 0))
    current_view_title = _clean_text(
        search.get("current_view_title"),
        max_length=120,
    )
    if current_view_title:
        lines.append(
            f"- 当前页面/视图标题已由主内容集合确认：“{current_view_title}”。"
        )
    if collection_total:
        collection_label = _clean_text(search.get("collection_label"), max_length=120) or "当前集合"
        completeness = "完整" if search.get("collection_complete") else "有界"
        lines.append(
            f"- 集合“{collection_label}”返回 {collection_returned}/{collection_total} 个逻辑条目（{completeness}）。"
        )
    if search.get("active_chat_identity_verified"):
        chat_label = (
            _clean_text(search.get("active_chat_label"), max_length=160)
            or "当前会话"
        )
        lines.append(
            f"- 已用同一窗口内的会话标题与消息输入框验证当前会话“{chat_label}”。"
        )
    for element in elements:
        label = _clean_text(element.get("label") or element.get("value"), max_length=180)
        role = _clean_text(element.get("role"), max_length=60) or (
            "AXCollectionItem"
            if element.get("context_relation") == "collection_item"
            else "AXElement"
        )
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
        activation_effect = _clean_text(
            element.get("activation_effect"),
            max_length=80,
        )
        if activation_effect:
            state.append("effect=" + activation_effect)
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
        next_step = (
            "需要视觉证据兜底"
            if search.get("visual_fallback_required") is not False
            else "结构证据足以提出澄清，但不足以执行动作"
        )
        lines.append(
            f"- AX 搜索结果不足以独立回答或定位动作（{reason}），{next_step}。"
        )
        if reason == "action_intent_mismatch":
            lines.append(
                "- 当前 AX 动作的真实效果与请求不一致；例如媒体条目的 AXPress 可能直接开始播放，不能当作“打开详情”。"
            )
        elif reason == "ambiguous_actionable_matches":
            lines.append(
                "- 当前查询命中了多个可执行目标，已移除它们的可执行引用；请用集合名称、AXRole 或 semantic_path 缩小 ax_query 后重新观察。"
            )
        near_matches = (
            search.get("near_match_labels")
            if isinstance(search.get("near_match_labels"), list)
            else []
        )
        if near_matches:
            labels = "、".join(
                _clean_text(item, max_length=80)
                for item in near_matches[:5]
                if _clean_text(item, max_length=80)
            )
            if labels:
                lines.append(
                    f"- 找到近似可见名称：{labels}；涉及动作时必须先让用户确认，不能自动替换目标。"
                )
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


def _raise_macos_accessibility_windows(
    runtime: _AXRuntime,
    application: int,
) -> int:
    children_provider = getattr(runtime, "children", None)
    if not callable(children_provider):
        return 0
    children, _truncated = children_provider(application, limit=96)
    raised = 0
    try:
        for child in children:
            if str(runtime.attribute(child, "AXRole") or "") not in {
                "AXWindow",
                "AXSheet",
                "AXDialog",
            }:
                continue
            try:
                if bool(runtime.attribute(child, "AXMinimized")):
                    runtime.set_boolean_attribute(
                        child,
                        "AXMinimized",
                        False,
                    )
            except Exception:
                pass
            try:
                actions = runtime.actions(child)
                if "AXRaise" in actions:
                    runtime.perform(child, "AXRaise")
            except Exception:
                pass
            raised += 1
    finally:
        for child in children:
            runtime.release(child)
    return raised


@_serialize_ax_native_call()
def activate_macos_accessibility_application(
    target_app: object,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    runtime_factory=_AXRuntime,
    running_apps_provider=_active_macos.enumerate_active_vision_running_app_candidates,
    current_pid_provider=os.getpid,
) -> bool:
    profile = resolve_macos_ax_app(target_app)
    if not profile or _platform_name(platform_name) != "darwin":
        return False
    host_process_pids = _host_process_tree_pids(
        current_pid_provider=current_pid_provider,
        runner=runner,
    )
    profile = _resolve_running_profile(
        profile,
        platform_name=platform_name or sys.platform,
        runner=runner,
        running_apps_provider=running_apps_provider,
    )
    if _is_host_process(
        profile.get("pid"),
        host_process_pids=host_process_pids,
    ):
        return False
    runtime = runtime_factory()
    application = 0
    try:
        if not runtime.trusted():
            return False
        pid = _target_app_pid(profile, runtime, runner=runner)
        if pid <= 0 or _is_host_process(
            pid,
            host_process_pids=host_process_pids,
        ):
            return False
        application = runtime.application(pid)
        if not application:
            return False
        runtime.set_timeout(application, 0.5)
        runtime.set_boolean_attribute(application, "AXFrontmost", True)
        _raise_macos_accessibility_windows(runtime, application)
        return bool(runtime.attribute(application, "AXFrontmost"))
    except Exception:
        return False
    finally:
        if application:
            runtime.release(application)
        runtime.close()


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


@_serialize_ax_native_call(background_flag="preserve_windowed_snapshot")
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
    preserve_windowed_snapshot: bool = False,
    _protected_host_pids: set[int] | None = None,
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
    host_process_pids = (
        {
            _positive_pid(pid)
            for pid in _protected_host_pids
            if _positive_pid(pid)
        }
        if _protected_host_pids is not None
        else _host_process_tree_pids(
            current_pid_provider=current_pid_provider,
            runner=runner,
        )
    )
    profile = _resolve_running_profile(
        profile,
        platform_name=platform_name or sys.platform,
        runner=runner,
        running_apps_provider=running_apps_provider,
    )
    base = base_payload()
    profile_pid = _positive_pid(profile.get("pid"))
    if _is_host_process(
        profile_pid,
        host_process_pids=host_process_pids,
    ):
        return {
            **base,
            "status": "blocked",
            "pid": profile_pid,
            "reason": AX_SELF_PROCESS_REASON,
        }
    runtime = runtime_factory()
    application = 0
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
        if _is_host_process(
            pid,
            host_process_pids=host_process_pids,
        ):
            return {
                **base,
                "status": "blocked",
                "pid": pid,
                "reason": AX_SELF_PROCESS_REASON,
            }
        if (
            str(cache_mode or "").strip().lower() == "prefer_cache"
            and not _query_requires_live_state(query)
        ):
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
        manual_accessibility_enabled = False
        manual_accessibility = getattr(
            runtime,
            "enable_manual_accessibility",
            None,
        )
        if callable(manual_accessibility):
            try:
                manual_accessibility_enabled = bool(
                    manual_accessibility(application)
                )
            except Exception:
                manual_accessibility_enabled = False
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
            if preserve_windowed_snapshot:
                cached_app_id = _cached_app_id(profile, cache)
                existing = cache.search(
                    cached_app_id,
                    pid=pid,
                    limit=result_limit,
                    query=_clean_text(query, max_length=240),
                )
                existing_index = (
                    existing.get("index")
                    if isinstance(existing.get("index"), dict)
                    else {}
                )
                existing_roles = (
                    existing_index.get("roles")
                    if isinstance(existing_index.get("roles"), dict)
                    else {}
                )
                new_roles: dict[str, int] = {}
                for element in elements:
                    role = str(element.get("role") or "")
                    if role:
                        new_roles[role] = new_roles.get(role, 0) + 1
                page_roles = {
                    "AXCell",
                    "AXGrid",
                    "AXList",
                    "AXListItem",
                    "AXOutline",
                    "AXOutlineRow",
                    "AXRow",
                    "AXScrollArea",
                    "AXSearchField",
                    "AXTable",
                    "AXTextArea",
                    "AXTextField",
                    "AXToolbar",
                    "AXWebArea",
                }
                existing_windows = int(
                    existing_roles.get("AXWindow") or 0
                )
                new_windows = int(new_roles.get("AXWindow") or 0)
                existing_page_nodes = sum(
                    int(existing_roles.get(role) or 0)
                    for role in page_roles
                )
                new_page_nodes = sum(
                    int(new_roles.get(role) or 0)
                    for role in page_roles
                )
                existing_total = max(
                    0,
                    int(existing.get("total_element_count") or 0),
                )
                new_total = len(elements)
                existing_window_signatures = {
                    str(item)
                    for item in (
                        existing_index.get("window_signatures")
                        if isinstance(
                            existing_index.get("window_signatures"),
                            list,
                        )
                        else []
                    )
                    if str(item)
                }
                new_window_signatures = set(
                    snapshot_window_signatures(elements)
                )
                window_identity_changed = bool(
                    existing_window_signatures
                    and new_window_signatures
                    and not (
                        existing_window_signatures
                        & new_window_signatures
                    )
                )
                materially_smaller = bool(
                    existing_total
                    >= new_total + max(24, int(new_total * 0.25))
                    and existing_page_nodes
                    >= new_page_nodes + max(4, int(new_page_nodes * 0.20))
                )
                lower_fidelity = bool(
                    existing_windows > 0
                    and (
                        new_windows < existing_windows
                        or materially_smaller
                        or window_identity_changed
                    )
                )
                if (
                    existing.get("status") == "hit"
                    and not bool(existing.get("process_changed"))
                    and lower_fidelity
                ):
                    preserved = _capture_result_from_search(
                        base,
                        profile,
                        existing,
                        pid=pid,
                        cache_hit=True,
                    )
                    preserved["snapshot_preserved"] = True
                    preserved["reason"] = (
                        "background refresh exposed a different or "
                        "lower-fidelity window tree; the main snapshot was retained"
                    )
                    if isinstance(preserved.get("search"), dict):
                        preserved["search"]["refresh_preserved"] = True
                    return preserved
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
                result = _capture_result_from_search(
                    base,
                    profile,
                    search,
                    pid=pid,
                    cache_hit=False,
                )
                result["manual_accessibility_enabled"] = (
                    manual_accessibility_enabled
                )
                return result
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
        if application:
            runtime.release(application)
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
    for key, weight in (
        ("subrole", 4),
        ("identifier", 60),
        ("title", 35),
        ("description", 25),
        ("placeholder", 25),
        ("value", 20),
        ("url", 70),
    ):
        max_length = 600 if key == "url" else 160
        expected = _clean_text(ax_ref.get(key), max_length=max_length)
        if not expected:
            continue
        actual = _clean_text(node.get(key), max_length=max_length)
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


def _resolve_element_at_reviewed_path(
    runtime: _AXRuntime,
    application: int,
    profile: dict[str, Any],
    ax_ref: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    expected_path = tuple(ax_ref.get("path") or ())
    current = runtime.retain(application)
    path: tuple[int, ...] = ()
    ancestors: tuple[dict[str, str], ...] = ()
    if not current:
        return 0, {}
    try:
        for child_index in expected_path:
            node = _read_node(
                runtime,
                current,
                profile,
                path=path,
                ancestors=ancestors,
            )
            signature = {
                "role": _clean_text(node.get("role"), max_length=80),
                "label": _clean_text(node.get("label"), max_length=120),
            }
            children, _truncated = runtime.children(
                current,
                limit=child_index + 1,
            )
            if child_index >= len(children):
                for child in children:
                    runtime.release(child)
                return 0, {}
            selected_child = children[child_index]
            for index, child in enumerate(children):
                if index != child_index:
                    runtime.release(child)
            runtime.release(current)
            current = selected_child
            path = (*path, child_index)
            ancestors = (*ancestors, signature)
        node = _read_node(
            runtime,
            current,
            profile,
            path=path,
            ancestors=ancestors,
        )
        if _match_score(ax_ref, node) < 0:
            return 0, {}
        expected_bounds = (
            ax_ref.get("bounds")
            if isinstance(ax_ref.get("bounds"), dict)
            else {}
        )
        actual_bounds = (
            node.get("bounds")
            if isinstance(node.get("bounds"), dict)
            else {}
        )
        if expected_bounds:
            if not actual_bounds:
                return 0, {}
            bounds_delta = sum(
                abs(
                    _rounded_number(expected_bounds.get(key))
                    - _rounded_number(actual_bounds.get(key))
                )
                for key in ("x", "y", "width", "height")
            )
            if bounds_delta > 8:
                return 0, {}
        resolved = current
        current = 0
        return resolved, node
    finally:
        if current:
            runtime.release(current)


def _resolve_element(
    runtime: _AXRuntime,
    application: int,
    profile: dict[str, Any],
    ax_ref: dict[str, Any],
    *,
    max_elements: int = AX_FULL_TREE_MAX_ELEMENTS,
    timeout_sec: float = 8.0,
) -> tuple[int, dict[str, Any]]:
    direct_element, direct_node = _resolve_element_at_reviewed_path(
        runtime,
        application,
        profile,
        ax_ref,
    )
    if direct_element:
        return direct_element, direct_node
    root = runtime.retain(application)
    queue: deque[tuple[int, tuple[int, ...], tuple[dict[str, str], ...]]] = deque([(root, (), ())])
    matches: list[tuple[int, int, dict[str, Any]]] = []
    visited = 0
    role_candidates = 0
    deadline = time.monotonic() + max(0.5, float(timeout_sec))
    try:
        while queue and visited < max_elements and time.monotonic() < deadline:
            element, path, ancestors = queue.popleft()
            try:
                node = _read_node(runtime, element, profile, path=path, ancestors=ancestors)
                visited += 1
                if str(node.get("role") or "") == str(ax_ref.get("role") or ""):
                    role_candidates += 1
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
        raise RuntimeError(
            "The approved AX target is stale or no longer present "
            f"(visited={visited}, role_candidates={role_candidates})."
        )
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


def _chat_header_matches_input_window(
    elements: list[dict[str, Any]],
    input_node: dict[str, Any],
    expected_chat: str,
) -> bool:
    expected_key = _app_key(expected_chat)
    input_path = tuple(input_node.get("tree_path") or ())
    input_bounds = (
        input_node.get("bounds")
        if isinstance(input_node.get("bounds"), dict)
        else {}
    )
    if not expected_key or not input_path or not input_bounds:
        return False
    input_x = _rounded_number(input_bounds.get("x"))
    input_y = _rounded_number(input_bounds.get("y"))
    input_width = _rounded_number(input_bounds.get("width"))
    if input_x <= 0 or input_y <= 0 or input_width <= 0:
        return False
    window = next(
        (
            item
            for item in elements
            if str(item.get("role") or "") == "AXWindow"
            and tuple(item.get("tree_path") or ()) == input_path[:1]
        ),
        None,
    )
    window_bounds = (
        window.get("bounds")
        if isinstance(window, dict)
        and isinstance(window.get("bounds"), dict)
        else {}
    )
    window_y = _rounded_number(window_bounds.get("y"))
    window_height = _rounded_number(window_bounds.get("height"))
    if window_height <= 0:
        return False
    header_bottom = window_y + max(80, min(220, int(window_height * 0.35)))
    header_roles = {
        "AXButton",
        "AXHeading",
        "AXStaticText",
        "AXTitleUIElement",
    }
    content_roles = {
        "AXBrowser",
        "AXCell",
        "AXCollection",
        "AXList",
        "AXOutline",
        "AXOutlineRow",
        "AXRow",
        "AXScrollArea",
        "AXTable",
        "AXWebArea",
    }
    path_roles = {
        tuple(item.get("tree_path") or ()): str(item.get("role") or "")
        for item in elements
        if isinstance(item, dict) and item.get("tree_path") is not None
    }
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    input_parent_path = input_path[:-1]
    input_parent_role = path_roles.get(input_parent_path, "")

    def shared_path_depth(left: tuple[int, ...], right: tuple[int, ...]) -> int:
        depth = 0
        for left_part, right_part in zip(left, right):
            if left_part != right_part:
                break
            depth += 1
        return depth

    for candidate in elements:
        candidate_path = tuple(candidate.get("tree_path") or ())
        if (
            str(candidate.get("role") or "") not in header_roles
            or candidate_path[:1] != input_path[:1]
            or candidate_path[: len(input_path)] == input_path
            or input_path[: len(candidate_path)] == candidate_path
        ):
            continue
        # Chromium-based chat apps commonly expose the whole window through
        # one AXWebArea.  A shared collection/web ancestor is therefore not
        # evidence that a node belongs to message content.  Reject only a
        # content container on the candidate's own branch.
        if any(
            path_roles.get(candidate_path[:depth]) in content_roles
            and input_path[:depth] != candidate_path[:depth]
            for depth in range(1, len(candidate_path))
        ):
            continue
        bounds = (
            candidate.get("bounds")
            if isinstance(candidate.get("bounds"), dict)
            else {}
        )
        width = _rounded_number(bounds.get("width"))
        height = _rounded_number(bounds.get("height"))
        x = _rounded_number(bounds.get("x"))
        y = _rounded_number(bounds.get("y"))
        if not (
            width > 0
            and height > 0
            and x + width // 2 >= input_x
            and x <= input_x + min(360, max(120, int(input_width * 0.6)))
            and window_y <= y < min(input_y, header_bottom)
        ):
            continue
        common_depth = shared_path_depth(candidate_path, input_path)
        candidates.append((common_depth, y, candidate))
    if not candidates:
        return False

    # A button/static label beside the editor inside its immediate composer
    # group is normally a send/tool affordance or quoted message text, not the
    # conversation title.  Keep flat AXWindow/AXWebArea trees as a fallback:
    # several Electron builds expose every useful control as direct siblings.
    branch_candidates = candidates
    if input_parent_role not in {"", "AXApplication", "AXWebArea", "AXWindow"}:
        outside_composer = [
            item
            for item in candidates
            if item[0] < len(input_parent_path)
        ]
        if outside_composer:
            branch_candidates = outside_composer

    # QQ exposes the active recipient again as the editor description.  When
    # that independent native identity agrees with the approved recipient, a
    # higher app-toolbar row must not hide the matching conversation header.
    # Editable values are never used here, so message text cannot satisfy it.
    input_identity_keys = {
        _app_key(input_node.get(key))
        for key in ("title", "description")
        if _app_key(input_node.get(key))
    }
    if expected_key in input_identity_keys:
        return any(
            _app_key(candidate.get("label")) == expected_key
            for _depth, _y, candidate in branch_candidates
        )

    # Identify the title slot before comparing its text.  Searching directly
    # for the expected contact lets a same-name message in the content pane
    # masquerade as the active conversation.  Nested Electron trees reveal
    # the pane boundary structurally: the real title shares a deeper ancestor
    # with the editor than an application-level toolbar does.  Within that
    # pane, use the highest plausible row.  Role priority is deliberately not
    # used because call/menu buttons often share the title row.
    deepest_shared = max(item[0] for item in branch_candidates)
    same_pane = [item for item in branch_candidates if item[0] == deepest_shared]
    top_y = min(item[1] for item in same_pane)
    title_row = [
        item[2]
        for item in same_pane
        if abs(item[1] - top_y) <= 12
    ]
    return any(_app_key(item.get("label")) == expected_key for item in title_row)


@_serialize_ax_native_call()
def verify_macos_accessibility_chat_context(
    target_app: object,
    *,
    intended_chat: object,
    input_ax_ref: object,
    expected_text: object = "",
    require_input_focused: bool = False,
    platform_name: str | None = None,
    runner=subprocess.run,
    runtime_factory=_AXRuntime,
    running_apps_provider=_active_macos.enumerate_active_vision_running_app_candidates,
    current_pid_provider=os.getpid,
) -> dict[str, Any]:
    profile = resolve_macos_ax_app(target_app)
    if not profile or str(profile.get("app_id") or "") not in {"qq", "wechat"}:
        raise RuntimeError("Chat context verification requires QQ or WeChat.")
    chat_label = _clean_text(intended_chat, max_length=160)
    reference = dict(input_ax_ref) if isinstance(input_ax_ref, dict) else {}
    if not chat_label:
        raise RuntimeError("The chat action is missing its intended conversation.")
    if not _valid_ax_ref(reference):
        raise RuntimeError("The chat action is missing a valid reviewed input reference.")
    if _platform_name(platform_name) != "darwin":
        raise RuntimeError("Chat context verification is only available on macOS.")
    host_process_pids = _host_process_tree_pids(
        current_pid_provider=current_pid_provider,
        runner=runner,
    )
    profile = _resolve_running_profile(
        profile,
        platform_name=platform_name or sys.platform,
        runner=runner,
        running_apps_provider=running_apps_provider,
    )
    if str(reference.get("app_id") or "") != str(profile.get("app_id") or ""):
        raise RuntimeError("The reviewed chat input belongs to a different application.")
    if _is_host_process(
        profile.get("pid"),
        host_process_pids=host_process_pids,
    ):
        raise RuntimeError(AX_SELF_PROCESS_REASON)

    runtime = runtime_factory()
    application = 0
    try:
        if not runtime.trusted():
            raise RuntimeError("Accessibility permission is not granted to Ipet.")
        pid = _target_app_pid(profile, runtime, runner=runner)
        if pid <= 0 or _is_host_process(
            pid,
            host_process_pids=host_process_pids,
        ):
            raise RuntimeError("The target chat application process could not be resolved.")
        frontmost_pid, frontmost_name = runtime.focused_application()
        if frontmost_pid != pid:
            raise RuntimeError(
                "The target chat application is not frontmost at verification time."
            )
        application = runtime.application(pid)
        if not application:
            raise RuntimeError("AXUIElementCreateApplication failed.")
        runtime.set_timeout(application, 0.5)
        elements, _visited, truncated = _capture_tree(
            runtime,
            application,
            profile,
            max_elements=4_000,
            max_depth=64,
            timeout_sec=4.0,
        )
        if truncated:
            raise RuntimeError("The live chat hierarchy was incomplete.")
        matches = [
            (_match_score(reference, element), element)
            for element in elements
            if str(element.get("role") or "") in _EDITABLE_ROLES
            and _match_score(reference, element) >= 0
        ]
        if not matches:
            raise RuntimeError("The reviewed chat input is stale or no longer present.")
        best_score = max(score for score, _element in matches)
        best_matches = [
            element
            for score, element in matches
            if score == best_score
        ]
        if len(best_matches) != 1:
            raise RuntimeError("The reviewed chat input is ambiguous in the current interface.")
        input_node = best_matches[0]
        if not _chat_header_matches_input_window(elements, input_node, chat_label):
            raise RuntimeError(
                "The active conversation does not match the approved intended chat."
            )
        if require_input_focused and not bool(input_node.get("focused")):
            raise RuntimeError("The reviewed chat input is not focused.")
        expected = "".join(str(expected_text or "").casefold().split())
        value = "".join(str(input_node.get("value") or "").casefold().split())
        if expected and (bool(input_node.get("protected")) or expected != value):
            raise RuntimeError(
                "The expected draft does not exactly match the approved chat input."
            )
        return {
            "chat_identity_verified": True,
            "chat_input_verified": True,
            "chat_draft_verified": bool(expected),
            "ax_app_id": str(profile.get("app_id") or ""),
            "target_pid": pid,
            "frontmost_app": _clean_text(frontmost_name, max_length=120),
            "frontmost_verified": True,
            "chat_input_focused": bool(input_node.get("focused")),
            "method": "macos_accessibility_chat_context",
        }
    finally:
        if application:
            runtime.release(application)
        runtime.close()


def _live_descendant_evidence(
    runtime: _AXRuntime,
    element: int,
    *,
    max_depth: int = 4,
    max_elements: int = 96,
) -> tuple[set[str], set[str]]:
    labels: set[str] = set()
    urls: set[str] = set()
    children, _ = runtime.children(element, limit=max_elements)
    queue: deque[tuple[int, int]] = deque((child, 1) for child in children)
    visited = 0
    try:
        while queue and visited < max_elements:
            child, depth = queue.popleft()
            try:
                visited += 1
                subrole = _clean_text(
                    runtime.attribute(child, "AXSubrole"),
                    max_length=80,
                )
                protected = bool(
                    runtime.attribute(child, "AXProtectedContent")
                ) or subrole == "AXSecureTextField"
                if not protected:
                    for attribute in (
                        "AXTitle",
                        "AXDescription",
                        "AXValue",
                        "AXIdentifier",
                    ):
                        label = _clean_text(
                            runtime.attribute(child, attribute),
                            max_length=160,
                        )
                        if label:
                            labels.add(label)
                    url = _clean_text(
                        runtime.attribute(child, "AXURL"),
                        max_length=600,
                    )
                    if url:
                        urls.add(url)
                if depth < max_depth:
                    remaining = max(0, max_elements - visited - len(queue))
                    descendants, _ = runtime.children(child, limit=remaining)
                    queue.extend((descendant, depth + 1) for descendant in descendants)
            finally:
                runtime.release(child)
    finally:
        while queue:
            runtime.release(queue.popleft()[0])
    return labels, urls


def _verify_live_descendant_identity(
    runtime: _AXRuntime,
    element: int,
    node: dict[str, Any],
    reference: dict[str, Any],
) -> None:
    expected_label = _clean_text(
        reference.get("descendant_label"),
        max_length=160,
    )
    expected_url = _clean_text(
        reference.get("descendant_url"),
        max_length=600,
    )
    if not expected_label and not expected_url:
        return
    live_labels = {
        _clean_text(node.get(key), max_length=160)
        for key in ("label", "title", "description", "value", "identifier")
        if _clean_text(node.get(key), max_length=160)
    }
    descendant_labels, descendant_urls = _live_descendant_evidence(
        runtime,
        element,
    )
    live_labels.update(descendant_labels)
    expected_key = _app_key(expected_label)
    if expected_label and (
        not expected_key
        or expected_key
        not in {
            _app_key(label)
            for label in live_labels
            if _app_key(label)
        }
    ):
        raise RuntimeError(
            "The approved AX target no longer contains the reviewed descendant label."
        )
    if expected_url and expected_url not in descendant_urls:
        raise RuntimeError(
            "The approved AX target no longer contains the reviewed file URL."
        )


def _hit_test_point(
    runtime: _AXRuntime,
    element: int,
    node: dict[str, Any],
    reference: dict[str, Any],
) -> tuple[int, int]:
    role = str(node.get("role") or "")
    if role not in _HIT_TEST_ROLES or node.get("enabled") is False:
        raise RuntimeError("The approved AX geometry target is not a safe hit-test container.")
    if not _clean_text(reference.get("descendant_label"), max_length=160):
        raise RuntimeError("The approved AX geometry target has no descendant label.")
    _verify_live_descendant_identity(runtime, element, node, reference)
    bounds = node.get("bounds") if isinstance(node.get("bounds"), dict) else {}
    x = _rounded_number(bounds.get("x"))
    y = _rounded_number(bounds.get("y"))
    width = _rounded_number(bounds.get("width"))
    height = _rounded_number(bounds.get("height"))
    if width <= 2 or height <= 2:
        raise RuntimeError("The approved AX geometry target has no usable live bounds.")
    return x + width // 2, y + height // 2


def _editable_focus_point(
    node: dict[str, Any],
    reference: dict[str, Any],
) -> tuple[int, int]:
    role = str(node.get("role") or "")
    if role not in _EDITABLE_ROLES or node.get("enabled") is False:
        raise RuntimeError("The approved AX target is not an enabled editable element.")
    if _match_score(reference, node) < 0:
        raise RuntimeError("The approved AX input no longer matches its live element.")
    bounds = node.get("bounds") if isinstance(node.get("bounds"), dict) else {}
    x = _rounded_number(bounds.get("x"))
    y = _rounded_number(bounds.get("y"))
    width = _rounded_number(bounds.get("width"))
    height = _rounded_number(bounds.get("height"))
    if width <= 2 or height <= 2:
        raise RuntimeError("The approved AX input has no usable live bounds.")
    return x + width // 2, y + height // 2


def _editable_element_has_focus(
    runtime: _AXRuntime,
    application: int,
    element: int,
) -> bool:
    if bool(runtime.attribute(element, "AXFocused")):
        return True
    focused_provider = getattr(runtime, "focused_ui_element", None)
    equal_provider = getattr(runtime, "elements_equal", None)
    if not callable(focused_provider) or not callable(equal_provider):
        return False
    focused = focused_provider(application)
    if not focused:
        return False
    try:
        return bool(equal_provider(element, focused))
    finally:
        runtime.release(focused)


def _focus_editable_element(
    runtime: _AXRuntime,
    application: int,
    element: int,
    node: dict[str, Any],
    reference: dict[str, Any],
    *,
    geometry_clicker=None,
    sleeper=time.sleep,
) -> tuple[str, tuple[int, int] | None]:
    runtime.set_boolean_attribute(element, "AXFocused", True)
    for delay in (0.0, 0.04, 0.08, 0.12, 0.16):
        if delay:
            sleeper(delay)
        if _editable_element_has_focus(runtime, application, element):
            return "AXFocused", None
    if geometry_clicker is not None:
        click_point = _editable_focus_point(node, reference)
        geometry_clicker(*click_point)
        for delay in (0.04, 0.08, 0.12, 0.16, 0.2):
            sleeper(delay)
            if _editable_element_has_focus(runtime, application, element):
                return "AXGeometryFocus", click_point
    raise RuntimeError("macOS Accessibility could not verify input focus.")


def _qq_chat_thread_already_active(
    runtime: _AXRuntime,
    application: int,
    profile: dict[str, Any],
    node: dict[str, Any],
    reference: dict[str, Any],
) -> bool:
    if (
        str(profile.get("app_id") or "") != "qq"
        or str(reference.get("semantic_kind") or "") != "chat_thread"
    ):
        return False
    target_label = _clean_text(
        reference.get("descendant_label") or node.get("label"),
        max_length=160,
    )
    target_key = _app_key(target_label)
    target_bounds = node.get("bounds") if isinstance(node.get("bounds"), dict) else {}
    target_path = tuple(reference.get("path") or ())
    if not target_key or not target_bounds or not target_path:
        return False
    content_left = (
        _rounded_number(target_bounds.get("x"))
        + _rounded_number(target_bounds.get("width"))
        - 4
    )
    try:
        elements, _visited, _truncated = _capture_tree(
            runtime,
            application,
            profile,
            max_elements=3_000,
            max_depth=48,
            timeout_sec=2.5,
        )
    except Exception:
        return False
    editable_visible = False
    matching_header = False
    for candidate in elements:
        bounds = candidate.get("bounds") if isinstance(candidate.get("bounds"), dict) else {}
        if (
            not bounds
            or _rounded_number(bounds.get("width")) <= 2
            or _rounded_number(bounds.get("height")) <= 2
            or _rounded_number(bounds.get("x")) < content_left
        ):
            continue
        role = str(candidate.get("role") or "")
        if role in _EDITABLE_ROLES:
            editable_visible = True
            continue
        candidate_path = tuple(candidate.get("tree_path") or ())
        if (
            role == "AXButton"
            and _app_key(candidate.get("label")) == target_key
            and candidate_path[: len(target_path)] != target_path
            and bool(candidate.get("actions"))
        ):
            matching_header = True
    return editable_visible and matching_header


@_serialize_ax_native_call()
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
    geometry_clicker=None,
    sleeper=time.sleep,
) -> dict[str, Any]:
    profile = resolve_macos_ax_app(target_app)
    if not profile:
        raise RuntimeError("The target application is required for macOS Accessibility.")
    if _platform_name(platform_name) != "darwin":
        raise RuntimeError("macOS Accessibility actions are only available on macOS.")
    host_process_pids = _host_process_tree_pids(
        current_pid_provider=current_pid_provider,
        runner=runner,
    )
    reference = dict(ax_ref) if isinstance(ax_ref, dict) else {}
    if not _valid_ax_ref(reference):
        raise RuntimeError("The approved AX target reference is invalid.")
    profile = _resolve_running_profile(
        profile,
        platform_name=platform_name or sys.platform,
        runner=runner,
        running_apps_provider=running_apps_provider,
    )
    if _is_host_process(
        profile.get("pid"),
        host_process_pids=host_process_pids,
    ):
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
        if _is_host_process(
            pid,
            host_process_pids=host_process_pids,
        ):
            raise RuntimeError(AX_SELF_PROCESS_REASON)
        application = runtime.application(pid)
        if not application:
            raise RuntimeError("AXUIElementCreateApplication failed.")
        runtime.set_timeout(application, 0.5)
        for attempt in range(3):
            try:
                element, node = _resolve_element(
                    runtime,
                    application,
                    profile,
                    reference,
                )
                break
            except RuntimeError as exc:
                if (
                    "stale or no longer present" not in str(exc)
                    or attempt >= 2
                ):
                    raise
                sleeper(0.12 * (attempt + 1))
        _verify_live_descendant_identity(
            runtime,
            element,
            node,
            reference,
        )
        normalized_operation = str(operation or "press").strip().lower()
        role = str(node.get("role") or "")
        actions = node.get("actions") if isinstance(node.get("actions"), list) else []
        activation = str(reference.get("activation") or "").strip().lower()
        if (
            normalized_operation == "press"
            and activation == "hit_test"
            and _qq_chat_thread_already_active(
                runtime,
                application,
                profile,
                node,
                reference,
            )
        ):
            return {
                "clicked": False,
                "already_satisfied": True,
                "postcondition_verified": True,
                "ax_target_verified": True,
                "ax_action_performed": False,
                "ax_action": "AXNoOpAlreadyActive",
                "ax_app_id": profile["app_id"],
                "ax_role": role,
                "ax_label": _clean_text(
                    reference.get("descendant_label") or node.get("label"),
                    max_length=160,
                ),
                "target_pid": pid,
                "method": "macos_accessibility",
            }
        ax_action = ""
        action_point: tuple[int, int] | None = None
        if normalized_operation == "focus":
            if role not in _EDITABLE_ROLES:
                raise RuntimeError("The approved AX target is not an editable element.")
            ax_action, action_point = _focus_editable_element(
                runtime,
                application,
                element,
                node,
                reference,
                geometry_clicker=geometry_clicker,
                sleeper=sleeper,
            )
        elif activation == "open":
            if "AXOpen" not in actions:
                raise RuntimeError(
                    "The approved AX target no longer exposes its reviewed open action."
                )
            runtime.perform(element, "AXOpen")
            ax_action = "AXOpen"
        elif activation == "show_menu":
            if "AXShowMenu" not in actions:
                raise RuntimeError(
                    "The approved AX target no longer exposes its reviewed context-menu action."
                )
            runtime.perform(element, "AXShowMenu")
            ax_action = "AXShowMenu"
        elif activation == "hit_test":
            click_x, click_y = _hit_test_point(
                runtime,
                element,
                node,
                reference,
            )
            if geometry_clicker is None:
                raise RuntimeError(
                    "The approved AX geometry target has no Human Ops click dispatcher."
                )
            geometry_clicker(click_x, click_y)
            ax_action = "AXGeometryHitTest"
        elif "AXPress" in actions:
            runtime.perform(element, "AXPress")
            ax_action = "AXPress"
        elif role in _EDITABLE_ROLES:
            ax_action, action_point = _focus_editable_element(
                runtime,
                application,
                element,
                node,
                reference,
                geometry_clicker=geometry_clicker,
                sleeper=sleeper,
            )
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
            "target_pid": pid,
            "method": "macos_accessibility",
        }
        if ax_action == "AXGeometryHitTest":
            result.update(
                {
                    "x": click_x,
                    "y": click_y,
                    "method": "macos_accessibility+core_graphics",
                }
            )
        elif ax_action == "AXGeometryFocus" and action_point is not None:
            result.update(
                {
                    "x": action_point[0],
                    "y": action_point[1],
                    "method": "macos_accessibility+core_graphics",
                }
            )
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


@_serialize_ax_native_call()
def verify_macos_accessibility_input_value(
    target_app: object,
    *,
    input_ax_ref: object,
    expected_text: object,
    require_input_focused: bool = True,
    platform_name: str | None = None,
    runner=subprocess.run,
    runtime_factory=_AXRuntime,
    running_apps_provider=_active_macos.enumerate_active_vision_running_app_candidates,
    current_pid_provider=os.getpid,
    sleeper=time.sleep,
) -> dict[str, Any]:
    profile = resolve_macos_ax_app(target_app)
    reference = dict(input_ax_ref) if isinstance(input_ax_ref, dict) else {}
    if not profile:
        raise RuntimeError("The target application is required for AX input verification.")
    if not _valid_ax_ref(reference):
        raise RuntimeError("AX input verification requires a valid reviewed input reference.")
    if _platform_name(platform_name) != "darwin":
        raise RuntimeError("AX input verification is only available on macOS.")
    host_process_pids = _host_process_tree_pids(
        current_pid_provider=current_pid_provider,
        runner=runner,
    )
    profile = _resolve_running_profile(
        profile,
        platform_name=platform_name or sys.platform,
        runner=runner,
        running_apps_provider=running_apps_provider,
    )
    if str(reference.get("app_id") or "") != str(profile.get("app_id") or ""):
        raise RuntimeError("The reviewed AX input belongs to a different application.")
    if _is_host_process(
        profile.get("pid"),
        host_process_pids=host_process_pids,
    ):
        raise RuntimeError(AX_SELF_PROCESS_REASON)

    runtime = runtime_factory()
    application = 0
    element = 0
    try:
        if not runtime.trusted():
            raise RuntimeError("Accessibility permission is not granted to Ipet.")
        pid = _target_app_pid(profile, runtime, runner=runner)
        if pid <= 0 or _is_host_process(
            pid,
            host_process_pids=host_process_pids,
        ):
            raise RuntimeError("The target input application process could not be resolved.")
        frontmost_pid, _frontmost_name = runtime.focused_application()
        if frontmost_pid != pid:
            raise RuntimeError("The target input application is not frontmost at verification time.")
        application = runtime.application(pid)
        if not application:
            raise RuntimeError("AXUIElementCreateApplication failed.")
        runtime.set_timeout(application, 0.5)
        element, node = _resolve_element(
            runtime,
            application,
            profile,
            reference,
            max_elements=4_000,
            timeout_sec=4.0,
        )
        _verify_live_descendant_identity(
            runtime,
            element,
            node,
            reference,
        )
        if str(node.get("role") or "") not in _EDITABLE_ROLES:
            raise RuntimeError("The reviewed AX target is no longer an editable input.")
        if bool(node.get("protected")):
            raise RuntimeError("Protected AX input values cannot be verified.")
        expected = str(expected_text or "")
        last_focused = False
        value_available = False
        for delay in (0.0, 0.04, 0.08, 0.12, 0.16, 0.2):
            if delay:
                sleeper(delay)
            value = runtime.attribute(element, "AXValue")
            value_available = value is not None
            last_focused = _editable_element_has_focus(
                runtime,
                application,
                element,
            )
            if value_available and str(value) == expected and (
                last_focused or not require_input_focused
            ):
                return {
                    "input_value_verified": True,
                    "input_focus_verified": last_focused,
                    "postcondition_verified": True,
                    "target_pid": pid,
                    "method": "macos_accessibility",
                }
        if not value_available:
            raise RuntimeError("The reviewed AX input does not expose a readable value.")
        if require_input_focused and not last_focused:
            raise RuntimeError("The reviewed AX input is not focused after text delivery.")
        raise RuntimeError("The reviewed AX input value does not match the delivered text.")
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
        host_process_pids = _host_process_tree_pids(
            current_pid_provider=current_pid_provider,
            runner=runner,
        )
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
                if app_id and _is_host_process(
                    pid,
                    host_process_pids=host_process_pids,
                ):
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
                if app_id and _is_host_process(
                    pid,
                    host_process_pids=host_process_pids,
                ):
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

        # Refresh discovery can take long enough for QtWebEngine to spawn a
        # new renderer after the first process-tree snapshot.  Re-read the
        # tree after enumeration and fail closed before any native AX call.
        host_process_pids.update(
            _host_process_tree_pids(
                current_pid_provider=current_pid_provider,
                runner=runner,
            )
        )
        external_profiles: list[dict[str, Any]] = []
        for profile in profiles:
            app_id = str(profile.get("app_id") or "")
            pid = _positive_pid(profile.get("pid"))
            if app_id and _is_host_process(
                pid,
                host_process_pids=host_process_pids,
            ):
                if app_id not in self_excluded_ids:
                    self_excluded_apps.append(
                        {
                            "app_id": app_id,
                            "name": str(
                                profile.get("display_name")
                                or app_id
                            ),
                            "pid": pid,
                            "reason": AX_SELF_PROCESS_REASON,
                        }
                    )
                    self_excluded_ids.add(app_id)
                continue
            external_profiles.append(profile)
        profiles = external_profiles

        updates: list[dict[str, Any]] = []
        running_app_ids = {str(profile.get("app_id") or "") for profile in profiles}
        discovery_authoritative = bool(
            target_apps is not None
            or not enumeration_errors
        )
        for item in stored_before:
            if not isinstance(item, dict):
                continue
            app_id = str(item.get("app_id") or "")
            if not app_id or app_id in running_app_ids:
                continue
            self_excluded = app_id in self_excluded_ids
            if not self_excluded and not discovery_authoritative:
                updates.append(
                    {
                        "app_id": app_id,
                        "name": str(item.get("app_name") or app_id),
                        "status": "unverified",
                        "updated": False,
                        "element_count": max(
                            0,
                            int(item.get("element_count") or 0),
                        ),
                        "visited_count": max(
                            0,
                            int(item.get("visited_count") or 0),
                        ),
                        "capture_truncated": bool(
                            item.get("capture_truncated")
                        ),
                        "reason": (
                            "running application discovery failed; "
                            "the previous snapshot was retained"
                        ),
                    }
                )
                continue
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
                timeout_sec=AX_BACKGROUND_REFRESH_TIMEOUT_SEC,
                query="",
                cache_mode="refresh",
                result_limit=1,
                cache=cache,
                running_apps_provider=running_apps_provider,
                current_pid_provider=current_pid_provider,
                preserve_windowed_snapshot=True,
                _protected_host_pids=host_process_pids,
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
                    "updated": (
                        result_status == "success"
                        and not bool(result.get("snapshot_preserved"))
                    ),
                    "snapshot_preserved": bool(
                        result.get("snapshot_preserved")
                    ),
                    "element_count": max(0, int(result.get("total_element_count") or 0)),
                    "visited_count": max(0, int(result.get("visited_count") or 0)),
                    "capture_truncated": bool(result.get("truncated")),
                    "reason": _clean_text(result.get("reason"), max_length=240),
                }
            )
        status = macos_accessibility_index_status(cache=cache)
        updated_count = sum(bool(item.get("updated")) for item in updates)
        skipped_count = sum(not bool(item.get("updated")) for item in updates)
        attempted_updates = [
            item
            for item in updates
            if str(item.get("app_id") or "") in running_app_ids
        ]
        attempted_success_count = sum(
            str(item.get("status") or "") == "success"
            for item in attempted_updates
        )
        attempted_failed_count = len(attempted_updates) - attempted_success_count
        if target_apps and not attempted_updates:
            overall_status = "error"
        elif attempted_updates and attempted_failed_count == 0:
            overall_status = "success"
        elif attempted_success_count:
            overall_status = "partial"
        elif attempted_updates or enumeration_errors:
            overall_status = "error"
        else:
            overall_status = "success"
        return {
            **status,
            "status": overall_status,
            "reason": (
                ""
                if overall_status == "success"
                else (
                    "some macOS Accessibility snapshots could not be refreshed"
                    if overall_status == "partial"
                    else "macOS Accessibility snapshots could not be refreshed"
                )
            ),
            "discovered_count": len(profiles),
            "discovery_truncated": discovery_truncated,
            "discovery_authoritative": discovery_authoritative,
            "enumeration_errors": enumeration_errors,
            "self_excluded_count": len(self_excluded_apps),
            "self_excluded_apps": self_excluded_apps,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            "updates": updates,
        }
    finally:
        _AX_INDEX_REFRESH_LOCK.release()
