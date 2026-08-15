from __future__ import annotations

import ctypes
import sys


_PREPARE_HIDE_SCRIPT = "window.PET_APP?.prepareForDesktopHide?.();"
_RESTORE_SCRIPT = "window.PET_APP?.restoreFromDesktopHide?.();"


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
