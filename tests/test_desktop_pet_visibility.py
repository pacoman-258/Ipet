from __future__ import annotations

import ctypes
import unittest
from types import SimpleNamespace
from unittest import mock

from app.desktop_pet_visibility import (
    DesktopPetVisibilityController, deactivate_macos_application,
    _desktop_space_behavior, apply_desktop_follow,
)


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def stop(self) -> None:
        self.calls.append("stop")

    def apply_config(self, config) -> None:
        self.calls.append(("apply_config", config))


class _Page:
    def __init__(self, calls) -> None:
        self.calls = calls

    def runJavaScript(self, script: str) -> None:
        self.calls.append(("javascript", script))


class _Browser:
    def __init__(self, calls) -> None:
        self._page = _Page(calls)

    def page(self):
        return self._page


class _Owner:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.config = {"environment": {"mode": "active"}}
        self.vision_controller = _Recorder()
        self.environment_controller = _Recorder()
        self.browser = _Browser(self.calls)

    def hide(self) -> None:
        self.calls.append("hide")

    def show(self) -> None:
        self.calls.append("show")

    def raise_(self) -> None:
        self.calls.append("raise")

    def activateWindow(self) -> None:
        self.calls.append("activate")


class DesktopPetVisibilityTests(unittest.TestCase):
    def test_native_follow_sets_nonactivating_style_and_restores_owned_flags(self) -> None:
        for original_style in (0xE, 0x8E):
            with self.subTest(original_style=original_style):
                native = {b"collectionBehavior": 0x102, b"styleMask": original_style}
                library = SimpleNamespace(sel_registerName=mock.Mock(side_effect=lambda name: name))
                owner = SimpleNamespace(winId=lambda: 77, config={"window": {"follow_desktop": True}})
                writes = []

                def write(handle, selector, value):
                    key = {b"setCollectionBehavior:": b"collectionBehavior", b"setStyleMask:": b"styleMask"}[selector]
                    native[key] = value
                    writes.append((key, value))

                def signature(result_type, *arg_types):
                    if result_type is None:
                        return lambda symbol: write
                    if result_type is ctypes.c_void_p:
                        return lambda symbol: lambda handle, selector: 111
                    return lambda symbol: lambda handle, selector: native[selector]

                with (
                    mock.patch("app.desktop_pet_visibility.sys.platform", "darwin"),
                    mock.patch("app.desktop_pet_visibility.platform.mac_ver", return_value=("26.0", (), "")),
                    mock.patch("app.desktop_pet_visibility.ctypes.CDLL", return_value=library),
                    mock.patch("app.desktop_pet_visibility.ctypes.CFUNCTYPE", side_effect=signature),
                ):
                    self.assertTrue(apply_desktop_follow(owner))
                    self.assertEqual(native[b"styleMask"], original_style | 0x80)
                    self.assertEqual(native[b"collectionBehavior"], 0x40101)
                    self.assertEqual(writes[-2:], [
                        (b"collectionBehavior", 0),
                        (b"collectionBehavior", 0x40101),
                    ])
                    self.assertTrue(apply_desktop_follow(owner))
                    self.assertEqual(native[b"styleMask"], original_style | 0x80)
                    self.assertEqual(native[b"collectionBehavior"], 0x40101)
                    # A later unrelated style update must survive disabling follow.
                    native[b"styleMask"] |= 0x1000
                    owner.config["window"]["follow_desktop"] = False
                    self.assertTrue(apply_desktop_follow(owner))
                    self.assertEqual(native[b"styleMask"], original_style | 0x1000)
                    self.assertEqual(native[b"collectionBehavior"], 0x102)

    def test_follow_desktop_resolves_conflicting_modes_and_restores_original(self) -> None:
        unrelated = (1 << 3) | (1 << 6)
        original = unrelated | (1 << 1) | (1 << 9) | (1 << 17)
        enabled = _desktop_space_behavior(original, original, enabled=True, modern_macos=True)
        self.assertEqual(enabled, unrelated | 1 | (1 << 8) | (1 << 18))
        self.assertEqual(
            _desktop_space_behavior(enabled, original, enabled=True, modern_macos=True), enabled,
        )
        # Restore the owned flags without discarding an unrelated later window change.
        self.assertEqual(
            _desktop_space_behavior(enabled | (1 << 4), original, enabled=False, modern_macos=True),
            original | (1 << 4),
        )

    def test_older_macos_uses_only_supported_collection_modes(self) -> None:
        self.assertEqual(
            _desktop_space_behavior(1 << 1, 1 << 1, enabled=True, modern_macos=False),
            1 | (1 << 8),
        )

    def test_follow_desktop_does_not_load_native_api_on_other_platforms(self) -> None:
        with (
            mock.patch("app.desktop_pet_visibility.sys.platform", "win32"),
            mock.patch("app.desktop_pet_visibility.ctypes.CDLL") as library,
        ):
            self.assertFalse(apply_desktop_follow(object()))
            library.assert_not_called()

    def test_hide_pauses_presence_and_closes_frontend_chat(self) -> None:
        owner = _Owner()
        deactivations: list[str] = []
        controller = DesktopPetVisibilityController(
            owner,
            deactivate_application=lambda: deactivations.append("deactivate"),
        )

        self.assertTrue(controller.hide())
        self.assertTrue(controller.hidden)
        self.assertEqual(owner.vision_controller.calls, ["stop"])
        self.assertEqual(owner.environment_controller.calls, ["stop"])
        self.assertEqual(owner.calls[-1], "hide")
        self.assertIn("prepareForDesktopHide", owner.calls[0][1])
        self.assertEqual(deactivations, ["deactivate"])
        self.assertFalse(controller.hide())

    def test_non_macos_deactivation_is_a_noop(self) -> None:
        self.assertFalse(deactivate_macos_application("linux"))

    def test_application_activation_restores_only_the_pet_and_presence(self) -> None:
        owner = _Owner()
        controller = DesktopPetVisibilityController(owner)
        controller.hide()
        owner.calls.clear()

        self.assertTrue(controller.on_application_activated())
        self.assertFalse(controller.hidden)
        self.assertEqual(owner.calls[:3], ["show", "raise", "activate"])
        self.assertIn("restoreFromDesktopHide", owner.calls[-1][1])
        expected = [("apply_config", owner.config)]
        self.assertEqual(owner.vision_controller.calls[-1:], expected)
        self.assertEqual(owner.environment_controller.calls[-1:], expected)
        self.assertFalse(controller.on_application_activated())


if __name__ == "__main__":
    unittest.main()
