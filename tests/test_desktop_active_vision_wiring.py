from __future__ import annotations

import subprocess
import sys
import unittest
from unittest import mock


class DesktopActiveVisionWiringTests(unittest.TestCase):
    def test_wiring_import_does_not_import_main(self) -> None:
        code = (
            "import sys\n"
            "sys.modules.pop('main', None)\n"
            "import app.desktop_active_vision_wiring\n"
            "raise SystemExit(1 if 'main' in sys.modules else 0)\n"
        )

        result = subprocess.run([sys.executable, "-c", code], check=False)

        self.assertEqual(result.returncode, 0)

    def test_compat_exports_delegate_to_body_active_vision(self) -> None:
        from app import desktop_active_vision_wiring as wiring
        from body import active_vision

        exports = wiring.create_active_vision_compat_exports(module_name="main")

        self.assertIs(exports["ACTIVE_VISION_ALLOWED_ACTIONS"], active_vision.ACTIVE_VISION_ALLOWED_ACTIONS)
        self.assertEqual(exports["_safe_focus_point"].__module__, "main")
        self.assertEqual(
            exports["_safe_focus_point"]({"x": 10, "y": 20, "width": 100, "height": 80}),
            active_vision._safe_focus_point({"x": 10, "y": 20, "width": 100, "height": 80}),
        )

    def test_main_entries_delegate_through_wiring_and_keep_default_provider_identity(self) -> None:
        import main

        bridge = mock.Mock()
        bridge.discover_active_vision_target_candidates.return_value = {"discovered": True}
        bridge.capture_active_vision_frame_payload.return_value = {"captured": True}

        with mock.patch.object(main, "_active_vision_bridge", return_value=bridge):
            discovery = main.discover_active_vision_target_candidates(platform_name="darwin")
            capture = main.capture_active_vision_frame_payload(
                "window",
                {"mode": "desktop_survey"},
                {"enabled": True},
                platform_name="darwin",
            )

        self.assertEqual(discovery, {"discovered": True})
        self.assertEqual(capture, {"captured": True})
        self.assertEqual(main.discover_active_vision_target_candidates.__module__, "main")
        self.assertEqual(main.capture_active_vision_frame_payload.__module__, "main")

        discovery_kwargs = bridge.discover_active_vision_target_candidates.call_args.kwargs
        self.assertIs(discovery_kwargs["default_desktop_targets_provider"], main.enumerate_active_vision_desktop_targets)
        self.assertIs(discovery_kwargs["default_dock_items_provider"], main.enumerate_macos_dock_item_candidates)
        self.assertIs(discovery_kwargs["default_running_apps_provider"], main.enumerate_active_vision_running_app_candidates)

        capture_kwargs = bridge.capture_active_vision_frame_payload.call_args.kwargs
        self.assertIs(capture_kwargs["default_frame_encoder"], main.capture_screen_frame_payload)
        self.assertIs(capture_kwargs["default_observation_provider"], main.collect_screen_observations)
        self.assertIs(capture_kwargs["default_desktop_targets_provider"], main.enumerate_active_vision_desktop_targets)
        self.assertIs(capture_kwargs["default_dock_items_provider"], main.enumerate_macos_dock_item_candidates)
        self.assertIs(capture_kwargs["default_running_apps_provider"], main.enumerate_active_vision_running_app_candidates)
        self.assertIs(capture_kwargs["default_interaction_runner"], main.run_active_vision_light_interaction)


if __name__ == "__main__":
    unittest.main()
