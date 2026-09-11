from __future__ import annotations

import ctypes
import logging
import platform
import sys


_PREPARE_HIDE_SCRIPT = "window.PET_APP?.prepareForDesktopHide?.();"
_RESTORE_SCRIPT = "window.PET_APP?.restoreFromDesktopHide?.();"
_NONACTIVATING_PANEL = 1 << 7  # NSWindowStyleMaskNonactivatingPanel


def _desktop_space_behavior(current: int, original: int, *, enabled: bool, modern_macos: bool) -> int:
    # NSWindowCollectionBehavior: Spaces and full-screen modes are mutually exclusive.
    mask = (1 << 0) | (1 << 1) | (1 << 7) | (1 << 8) | (1 << 9)
    follow = (1 << 0) | (1 << 8)  # CanJoinAllSpaces, FullScreenAuxiliary
    if modern_macos:
        mask |= (1 << 16) | (1 << 17) | (1 << 18)
        follow |= 1 << 18  # CanJoinAllApplications (macOS 13+)
    return (current & ~mask) | (follow if enabled else original & mask)


def apply_desktop_follow(owner) -> bool:
    """Apply on the Qt GUI thread after the native window exists."""
    if sys.platform != "darwin":
        return False
    try:
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        selector = objc.sel_registerName
        selector.restype = ctypes.c_void_p
        selector.argtypes = (ctypes.c_char_p,)
        # Separate signatures avoid changing objc_msgSend shared with other native helpers.
        get_pointer = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", objc)
        )
        get_behavior = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", objc)
        )
        set_behavior = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(
            ("objc_msgSend", objc)
        )
        # Qt's macOS WId is an NSView*, not an NSWindow*.
        native_window = get_pointer(int(owner.winId()), selector(b"window"))
        if not native_window:
            return False
        current = get_behavior(native_window, selector(b"collectionBehavior"))
        current_style = get_behavior(native_window, selector(b"styleMask"))
        saved = getattr(owner, "_desktop_space_behavior", None)
        if saved is None or saved[0] != native_window:
            saved = (native_window, current, current_style)
            owner._desktop_space_behavior = saved
        enabled = owner.config.get("window", {}).get("follow_desktop", True) is not False
        desired = _desktop_space_behavior(
            current, saved[1], enabled=enabled,
            modern_macos=int(platform.mac_ver()[0].split(".")[0]) >= 13,
        )
        desired_style = (current_style & ~_NONACTIVATING_PANEL) | (
            _NONACTIVATING_PANEL if enabled else saved[2] & _NONACTIVATING_PANEL
        )
        if desired_style != current_style:
            set_behavior(native_window, selector(b"setStyleMask:"), desired_style)
        # AppKit's Space tags can be stale even when collectionBehavior reads back
        # correctly. Register them after style changes, through the neutral mode.
        # Reapply on show as Qt can update the native style during that lifecycle.
        if enabled or desired != current or desired_style != current_style:
            set_behavior(native_window, selector(b"setCollectionBehavior:"), 0)
            set_behavior(native_window, selector(b"setCollectionBehavior:"), desired)
        return (
            get_behavior(native_window, selector(b"collectionBehavior")) == desired
            and get_behavior(native_window, selector(b"styleMask")) == desired_style
        )
    except (AttributeError, OSError, RuntimeError) as exc:
        logging.getLogger(__name__).warning("无法应用桌宠跟随桌面设置: %s", exc)
        return False


def deactivate_macos_application(platform_name: str | None = None) -> bool:
    """Make the next Dock click a real application activation event."""
    if (platform_name or sys.platform) != "darwin":
        return False
    try:
        ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = (ctypes.c_char_p,)
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = (ctypes.c_char_p,)
        send_message = objc.objc_msgSend
        send_message.restype = ctypes.c_void_p
        send_message.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        application_class = objc.objc_getClass(b"NSApplication")
        shared_application = send_message(application_class, objc.sel_registerName(b"sharedApplication"))
        send_message(shared_application, objc.sel_registerName(b"deactivate"))
        return True
    except (AttributeError, OSError):
        return False


class DesktopPetVisibilityController:
    """Own the explicit hidden/restored lifecycle of the desktop pet."""

    def __init__(self, owner, *, deactivate_application=deactivate_macos_application) -> None:
        self.owner = owner
        self.deactivate_application = deactivate_application
        self.hidden = False

    def hide(self) -> bool:
        if self.hidden:
            return False
        self.hidden = True
        self._run_javascript(_PREPARE_HIDE_SCRIPT)
        self.owner.vision_controller.stop()
        self.owner.environment_controller.stop()
        self.owner.hide()
        self.deactivate_application()
        return True

    def restore(self) -> bool:
        if not self.hidden:
            return False
        self.hidden = False
        self.owner.show()
        self.owner.raise_()
        self.owner.activateWindow()
        self.owner.vision_controller.apply_config(self.owner.config)
        self.owner.environment_controller.apply_config(self.owner.config)
        self._run_javascript(_RESTORE_SCRIPT)
        return True

    def on_application_activated(self) -> bool:
        return self.restore()

    def _run_javascript(self, script: str) -> None:
        browser = getattr(self.owner, "browser", None)
        page = browser.page() if browser is not None else None
        if page is not None:
            page.runJavaScript(script)
