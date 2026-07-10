from __future__ import annotations

import inspect
import subprocess
import sys
import unittest
from unittest import mock


class DesktopScreenCaptureWiringTests(unittest.TestCase):
    def test_wiring_import_does_not_import_main(self) -> None:
        code = (
            "import sys\n"
            "sys.modules.pop('main', None)\n"
            "import app.desktop_screen_capture_wiring\n"
            "raise SystemExit(1 if 'main' in sys.modules else 0)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], check=False)
        self.assertEqual(result.returncode, 0)

    def test_module_wrappers_pass_default_provider_identities_to_bridge(self) -> None:
        from app import desktop_screen_capture_wiring as wiring

        bridge = mock.Mock()
        bridge.capture_qt_screen_frame_payload.return_value = {"backend": "qt"}
        bridge.capture_screen_frame_payload.return_value = {"backend": "screen"}

        with mock.patch.object(wiring, "screen_capture_bridge", return_value=bridge):
            qt_payload = wiring.capture_qt_screen_frame_payload("screen", {"quality": 75})
            screen_payload = wiring.capture_screen_frame_payload("screen", {"enabled": True})

        self.assertEqual(qt_payload, {"backend": "qt"})
        self.assertEqual(screen_payload, {"backend": "screen"})

        self.assertIs(
            bridge.capture_qt_screen_frame_payload.call_args.kwargs["default_pixmap_composer"],
            wiring.compose_qt_screens_pixmap,
        )
        self.assertIs(
            bridge.capture_qt_screen_frame_payload.call_args.kwargs["default_pixmap_encoder"],
            wiring._encode_pixmap_frame,
        )
        self.assertIs(
            bridge.capture_screen_frame_payload.call_args.kwargs["default_macos_capture"],
            wiring.capture_macos_screencapture_payload,
        )
        self.assertIs(
            bridge.capture_screen_frame_payload.call_args.kwargs["default_qt_capture"],
            wiring.capture_qt_screen_frame_payload,
        )

    def test_main_installs_wiring_owned_compat_exports(self) -> None:
        import main
        from app import desktop_screen_capture_wiring as wiring

        main_source = inspect.getsource(main)
        self.assertIn("create_screen_capture_compat_exports", main_source)
        self.assertNotIn("def capture_qt_screen_frame_payload(", main_source)
        self.assertNotIn("def capture_screen_frame_payload(", main_source)
        self.assertTrue(callable(wiring.create_screen_capture_compat_exports))
        for name in (
            "compose_qt_screens_pixmap",
            "_encode_pixmap_frame",
            "capture_qt_screen_frame_payload",
            "capture_macos_screencapture_payload",
            "capture_screen_frame_payload",
        ):
            self.assertEqual(getattr(main, name).__module__, "main")

        bridge = mock.Mock()
        bridge.capture_qt_screen_frame_payload.return_value = {"backend": "qt"}
        bridge.capture_screen_frame_payload.return_value = {"backend": "screen"}

        with mock.patch.object(main, "_screen_capture_bridge", return_value=bridge):
            qt_payload = main.capture_qt_screen_frame_payload("screen", {"quality": 75})
            screen_payload = main.capture_screen_frame_payload("screen", {"enabled": True})

        self.assertEqual(qt_payload, {"backend": "qt"})
        self.assertEqual(screen_payload, {"backend": "screen"})
        self.assertIs(
            bridge.capture_qt_screen_frame_payload.call_args.kwargs["default_pixmap_composer"],
            main.compose_qt_screens_pixmap,
        )
        self.assertIs(
            bridge.capture_qt_screen_frame_payload.call_args.kwargs["default_pixmap_encoder"],
            main._encode_pixmap_frame,
        )
        self.assertIs(
            bridge.capture_screen_frame_payload.call_args.kwargs["default_macos_capture"],
            main.capture_macos_screencapture_payload,
        )
        self.assertIs(
            bridge.capture_screen_frame_payload.call_args.kwargs["default_qt_capture"],
            main.capture_qt_screen_frame_payload,
        )

    def test_main_exports_keep_legacy_defaults_and_dynamic_patch_markers(self) -> None:
        import main

        qt_signature = inspect.signature(main.capture_qt_screen_frame_payload)
        screen_signature = inspect.signature(main.capture_screen_frame_payload)
        original_composer = main.compose_qt_screens_pixmap
        original_encoder = main._encode_pixmap_frame
        original_macos_capture = main.capture_macos_screencapture_payload
        original_qt_capture = main.capture_qt_screen_frame_payload

        self.assertIs(qt_signature.parameters["pixmap_composer"].default, original_composer)
        self.assertIs(qt_signature.parameters["pixmap_encoder"].default, original_encoder)
        self.assertIs(screen_signature.parameters["macos_capture"].default, original_macos_capture)
        self.assertIs(screen_signature.parameters["qt_capture"].default, original_qt_capture)

        bridge = mock.Mock()
        bridge.capture_qt_screen_frame_payload.return_value = {"backend": "qt"}
        bridge.capture_screen_frame_payload.return_value = {"backend": "screen"}
        patched_composer = mock.Mock(name="patched_composer")
        patched_encoder = mock.Mock(name="patched_encoder")
        patched_macos_capture = mock.Mock(name="patched_macos_capture")
        patched_qt_capture = mock.Mock(name="patched_qt_capture")

        with (
            mock.patch.object(main, "_screen_capture_bridge", return_value=bridge),
            mock.patch.object(main, "compose_qt_screens_pixmap", patched_composer),
            mock.patch.object(main, "_encode_pixmap_frame", patched_encoder),
        ):
            main.capture_qt_screen_frame_payload("screen", {})

        qt_kwargs = bridge.capture_qt_screen_frame_payload.call_args.kwargs
        self.assertIs(qt_kwargs["pixmap_composer"], original_composer)
        self.assertIs(qt_kwargs["pixmap_encoder"], original_encoder)
        self.assertIs(qt_kwargs["default_pixmap_composer"], patched_composer)
        self.assertIs(qt_kwargs["default_pixmap_encoder"], patched_encoder)

        with (
            mock.patch.object(main, "_screen_capture_bridge", return_value=bridge),
            mock.patch.object(main, "capture_macos_screencapture_payload", patched_macos_capture),
            mock.patch.object(main, "capture_qt_screen_frame_payload", patched_qt_capture),
        ):
            main.capture_screen_frame_payload("screen", {})

        screen_kwargs = bridge.capture_screen_frame_payload.call_args.kwargs
        self.assertIs(screen_kwargs["macos_capture"], original_macos_capture)
        self.assertIs(screen_kwargs["qt_capture"], original_qt_capture)
        self.assertIs(screen_kwargs["default_macos_capture"], patched_macos_capture)
        self.assertIs(screen_kwargs["default_qt_capture"], patched_qt_capture)

        with mock.patch.object(main, "capture_screen_frame_payload", return_value={"patched": True}):
            self.assertEqual(main.capture_screen_frame_payload(None, {}), {"patched": True})


if __name__ == "__main__":
    unittest.main()
