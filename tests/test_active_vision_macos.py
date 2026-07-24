from __future__ import annotations

import builtins
import importlib
import subprocess
import sys
import unittest
from unittest import mock


def _fresh_import(name: str):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


class ActiveVisionMacOSModuleTests(unittest.TestCase):
    def test_macos_module_imports_without_main_or_qt_dependencies(self) -> None:
        original_import = builtins.__import__
        blocked_roots = {"main", "PySide6", "PyQt6"}

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if str(name).split(".", 1)[0] in blocked_roots:
                raise AssertionError(f"unexpected macOS discovery dependency: {name}")
            return original_import(name, globals, locals, fromlist, level)

        with mock.patch("builtins.__import__", side_effect=guarded_import):
            macos = _fresh_import("body.active_vision_macos")

        self.assertTrue(hasattr(macos, "discover_macos_active_vision_desktop_targets"))
        self.assertTrue(hasattr(macos, "enumerate_macos_dock_item_candidates"))
        self.assertTrue(hasattr(macos, "enumerate_active_vision_running_app_candidates"))

    def test_active_vision_wrappers_delegate_to_macos_module(self) -> None:
        active_vision = importlib.import_module("body.active_vision")
        sentinel_runner = mock.Mock()

        with mock.patch.object(
            active_vision._active_macos,
            "discover_macos_active_vision_desktop_targets",
            return_value=([{"target_id": "window-1"}], ["window warning"]),
        ) as discover:
            self.assertEqual(
                active_vision.discover_macos_active_vision_desktop_targets(
                    platform_name="darwin",
                    runner=sentinel_runner,
                ),
                ([{"target_id": "window-1"}], ["window warning"]),
            )
        discover.assert_called_once_with(platform_name="darwin", runner=sentinel_runner)

        with mock.patch.object(
            active_vision._active_macos,
            "enumerate_macos_dock_item_candidates",
            return_value=([{"target_id": "dock-1"}], []),
        ) as enumerate_dock:
            self.assertEqual(
                active_vision.enumerate_macos_dock_item_candidates(
                    platform_name="darwin",
                    runner=sentinel_runner,
                    include_errors=True,
                ),
                ([{"target_id": "dock-1"}], []),
            )
        enumerate_dock.assert_called_once_with(platform_name="darwin", runner=sentinel_runner, include_errors=True)

        with mock.patch.object(
            active_vision._active_macos,
            "enumerate_active_vision_running_app_candidates",
            return_value=([{"target_id": "running-1"}], []),
        ) as enumerate_running:
            self.assertEqual(
                active_vision.enumerate_active_vision_running_app_candidates(
                    platform_name="darwin",
                    runner=sentinel_runner,
                    include_errors=True,
                ),
                ([{"target_id": "running-1"}], []),
            )
        enumerate_running.assert_called_once_with(platform_name="darwin", runner=sentinel_runner, include_errors=True)

    def test_macos_module_parses_desktop_and_dock_targets(self) -> None:
        macos = importlib.import_module("body.active_vision_macos")

        desktop_targets = macos._parse_macos_desktop_targets(
            "Safari\tDocs\t10\t20\t900\t600\ttrue\tfalse\n"
            "Tiny\tSkip\t0\t0\t10\t10\tfalse\tfalse\n"
            "Broken\tSkip\tx\t0\t900\t600\tfalse\tfalse\n"
        )
        dock_candidates = macos._parse_macos_dock_item_candidates(
            "Music\t878\t872\t57\t73\n"
            "missing value\t935\t872\t57\t73\n"
            "TooSmall\t1\t2\t4\t4\n"
        )

        self.assertEqual(len(desktop_targets), 1)
        self.assertEqual(desktop_targets[0]["app"], "Safari")
        self.assertEqual(desktop_targets[0]["bounds"], {"x": 10, "y": 20, "width": 900, "height": 600})
        self.assertEqual(desktop_targets[0]["focus_point"], {"x": 190, "y": 32})
        self.assertTrue(desktop_targets[0]["frontmost"])
        self.assertFalse(desktop_targets[0]["minimized"])
        self.assertTrue(desktop_targets[0]["target_id"].startswith("macos:"))

        self.assertEqual(len(dock_candidates), 1)
        self.assertEqual(dock_candidates[0]["source"], "dock_item")
        self.assertEqual(dock_candidates[0]["app"], "Music")
        self.assertEqual(dock_candidates[0]["focus_point"], {"x": 906, "y": 908})
        self.assertTrue(dock_candidates[0]["focusable"])

    def test_running_app_fallback_stays_in_macos_module(self) -> None:
        macos = importlib.import_module("body.active_vision_macos")
        calls = []

        def runner(argv, **kwargs):
            calls.append(list(argv))
            if argv[0] == "osascript":
                return subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="-10827")
            if argv[:3] == ["/bin/ps", "-axo", "comm="]:
                return subprocess.CompletedProcess(
                    args=argv,
                    returncode=0,
                    stdout=(
                        "/usr/sbin/logd\n"
                        "/Applications/StudyBrowser.app/Contents/MacOS/StudyBrowser Helper\n"
                        "/System/Library/PrivateFrameworks/WeatherKit.framework/Versions/A/Resources/Weather.app/Contents/MacOS/Weather\n"
                        "/Applications/MediaBox.app/Contents/MacOS/MediaBox\n"
                    ),
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {argv}")

        with mock.patch.dict("sys.modules", {"AppKit": None}):
            candidates, errors = macos.enumerate_active_vision_running_app_candidates(
                platform_name="darwin",
                runner=runner,
                include_errors=True,
            )

        self.assertTrue(any("System Events running app fallback failed" in item for item in errors))
        self.assertEqual([call[0] for call in calls], ["osascript", "/bin/ps"])
        self.assertEqual([item["app"] for item in candidates], ["StudyBrowser", "MediaBox"])
        self.assertTrue(all(item["source"] == "running_app" for item in candidates))
        self.assertTrue(all(item["focusable"] for item in candidates))

    def test_ax_enumeration_can_include_hidden_apps_with_a_larger_limit(self) -> None:
        macos = importlib.import_module("body.active_vision_macos")
        apps = []
        for index, hidden in enumerate((True, False, False)):
            app = mock.Mock()
            app.localizedName.return_value = f"App {index}"
            app.bundleIdentifier.return_value = f"example.app{index}"
            app.processIdentifier.return_value = 700 + index
            app.isActive.return_value = index == 1
            app.isHidden.return_value = hidden
            app.activationPolicy.return_value = 0
            apps.append(app)
        appkit = mock.Mock()
        appkit.NSWorkspace.sharedWorkspace.return_value.runningApplications.return_value = apps

        with mock.patch.dict("sys.modules", {"AppKit": appkit}):
            candidates = macos.enumerate_active_vision_running_app_candidates(
                platform_name="darwin",
                include_hidden=True,
                limit=2,
            )

        self.assertEqual([item["app"] for item in candidates], ["App 0", "App 1"])
        self.assertEqual([item["pid"] for item in candidates], ["700", "701"])


if __name__ == "__main__":
    unittest.main()
