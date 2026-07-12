from __future__ import annotations

import ast
import importlib
import inspect
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]


def _parse_source(relative_path: str) -> ast.Module:
    return ast.parse((ROOT_DIR / relative_path).read_text(encoding="utf-8"))


class DesktopActionWiringTests(unittest.TestCase):
    def test_wiring_import_does_not_import_main(self) -> None:
        code = (
            "import sys\n"
            "sys.modules.pop('main', None)\n"
            "import app.desktop_action_wiring\n"
            "raise SystemExit(1 if 'main' in sys.modules else 0)\n"
        )

        result = subprocess.run([sys.executable, "-c", code], check=False)

        self.assertEqual(result.returncode, 0)

    def test_entries_delegate_to_bridge_and_keep_default_event_clicker_identity(self) -> None:
        from app import desktop_action_wiring as wiring

        bridge = mock.Mock()
        bridge.screen_coordinate.return_value = 12
        bridge.cg_point_type = object
        bridge.execute_human_ops_click.return_value = {"clicked": True}
        bridge.execute_human_ops_type_text.return_value = {"typed": True}
        bridge.execute_human_ops_launch_app.return_value = {"launched": True}
        bridge.execute_human_ops_key_press.return_value = {"pressed": True}
        bridge.applescript_string.return_value = '"hello"'
        current_default_clicker = {"value": None}

        entries = wiring.create_desktop_action_entries(
            lambda: bridge,
            default_event_clicker_provider=lambda: current_default_clicker["value"],
            module_name="main",
        )
        current_default_clicker["value"] = mock.Mock(name="patched_default_clicker")
        runner = mock.Mock()

        self.assertEqual(entries["_screen_coordinate"]("12.2", fallback=1), 12)
        self.assertIs(entries["_CGPoint"], object)
        entries["_post_core_graphics_click"](12, 34)
        click_result = entries["execute_human_ops_click"]({"x": 12}, platform_name="darwin", runner=runner)
        type_result = entries["execute_human_ops_type_text"]({"text": "hi"}, platform_name="darwin", runner=runner)
        launch_result = entries["execute_human_ops_launch_app"]({"app": "WeChat"}, platform_name="darwin", runner=runner)
        key_result = entries["execute_human_ops_key_press"]({"key": "enter"}, platform_name="darwin", runner=runner)
        self.assertEqual(entries["_applescript_string"]("hello"), '"hello"')
        entries["_process_pending_qt_events"]()
        entries["_hide_window_for_desktop_click"]("window")
        entries["_restore_window_after_desktop_click"]("window", True)

        self.assertEqual(click_result, {"clicked": True})
        self.assertEqual(type_result, {"typed": True})
        self.assertEqual(launch_result, {"launched": True})
        self.assertEqual(key_result, {"pressed": True})
        self.assertEqual(entries["execute_human_ops_click"].__module__, "main")
        default_event_clicker = inspect.signature(entries["execute_human_ops_click"]).parameters["event_clicker"].default
        self.assertIs(default_event_clicker, entries["_post_core_graphics_click"])

        bridge.screen_coordinate.assert_called_once_with("12.2", fallback=1)
        bridge.post_core_graphics_click.assert_called_once_with(12, 34)
        bridge.execute_human_ops_click.assert_called_once_with(
            {"x": 12},
            platform_name="darwin",
            runner=runner,
            event_clicker=current_default_clicker["value"],
        )
        bridge.execute_human_ops_type_text.assert_called_once_with(
            {"text": "hi"},
            platform_name="darwin",
            runner=runner,
        )
        bridge.execute_human_ops_launch_app.assert_called_once_with(
            {"app": "WeChat"},
            platform_name="darwin",
            runner=runner,
        )
        bridge.execute_human_ops_key_press.assert_called_once_with(
            {"key": "enter"},
            platform_name="darwin",
            runner=runner,
        )
        bridge.process_pending_qt_events.assert_called_once_with()
        bridge.hide_window_for_desktop_click.assert_called_once_with("window")
        bridge.restore_window_after_desktop_click.assert_called_once_with("window", True)

    def test_main_installs_wiring_entries_without_importing_bridge_directly(self) -> None:
        tree = _parse_source("main.py")

        imports_wiring = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "app"
            and any(alias.name == "desktop_action_wiring" for alias in node.names)
            for node in ast.walk(tree)
        )
        direct_bridge_import = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "app.desktop_action_bridge"
            and any(alias.name == "create_desktop_action_bridge" for alias in node.names)
            for node in ast.walk(tree)
        )

        self.assertTrue(imports_wiring)
        self.assertFalse(direct_bridge_import)

    def test_main_entries_keep_patch_points_and_router_dependency_identity(self) -> None:
        import main

        bridge = mock.Mock()
        bridge.execute_human_ops_click.return_value = {"clicked": True}
        patched_clicker = mock.Mock(name="patched_clicker")
        runner = mock.Mock()

        with (
            mock.patch.object(main, "_desktop_action_bridge", return_value=bridge),
            mock.patch.object(main, "_post_core_graphics_click", patched_clicker),
        ):
            result = main.execute_human_ops_click({"x": 12}, platform_name="darwin", runner=runner)

        self.assertEqual(result, {"clicked": True})
        self.assertEqual(main.execute_human_ops_click.__module__, "main")
        bridge.execute_human_ops_click.assert_called_once_with(
            {"x": 12},
            platform_name="darwin",
            runner=runner,
            event_clicker=patched_clicker,
        )

        deps = main._build_desktop_command_router_dependencies()
        self.assertIs(deps.execute_human_ops_click, main.execute_human_ops_click)
        self.assertIs(deps.execute_human_ops_type_text, main.execute_human_ops_type_text)
        self.assertIs(deps.execute_human_ops_launch_app, main.execute_human_ops_launch_app)
        self.assertIs(deps.execute_human_ops_key_press, main.execute_human_ops_key_press)
        self.assertIs(deps.hide_window_for_desktop_click, main._hide_window_for_desktop_click)
        self.assertIs(deps.restore_window_after_desktop_click, main._restore_window_after_desktop_click)


if __name__ == "__main__":
    unittest.main()
