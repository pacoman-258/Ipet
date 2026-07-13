from __future__ import annotations

import importlib
import subprocess
import sys
import unittest
from unittest import mock


class DesktopActionBridgeTests(unittest.TestCase):
    def test_module_import_does_not_load_main(self) -> None:
        original_bridge = sys.modules.get("app.desktop_action_bridge")
        original_main = sys.modules.get("main")
        sys.modules.pop("app.desktop_action_bridge", None)
        sys.modules.pop("main", None)

        try:
            module = importlib.import_module("app.desktop_action_bridge")

            self.assertTrue(hasattr(module, "DesktopActionBridge"))
            self.assertNotIn("main", sys.modules)
        finally:
            sys.modules.pop("app.desktop_action_bridge", None)
            if original_bridge is not None:
                sys.modules["app.desktop_action_bridge"] = original_bridge
            if original_main is not None:
                sys.modules["main"] = original_main
            else:
                sys.modules.pop("main", None)

    def test_execute_actions_delegate_to_desktop_actions_module(self) -> None:
        module = importlib.import_module("app.desktop_action_bridge")
        desktop_actions = mock.Mock()
        desktop_actions._screen_coordinate.return_value = 42
        desktop_actions._CGPoint = object
        desktop_actions._post_core_graphics_click = mock.Mock()
        desktop_actions.execute_human_ops_click.return_value = {"clicked": True}
        desktop_actions.execute_human_ops_type_text.return_value = {"typed": True}
        desktop_actions.execute_human_ops_launch_app.return_value = {"launched": True}
        desktop_actions.execute_human_ops_key_press.return_value = {"pressed": True}
        clicker = mock.Mock()
        runner = mock.Mock()
        bridge = module.DesktopActionBridge(desktop_actions, qapplication=object())

        self.assertEqual(bridge.screen_coordinate("41.8", fallback=7), 42)
        self.assertIs(bridge.cg_point_type, object)
        bridge.post_core_graphics_click(12, 34)
        click_result = bridge.execute_human_ops_click(
            {"x": 12},
            platform_name="darwin",
            runner=runner,
            event_clicker=clicker,
        )
        type_result = bridge.execute_human_ops_type_text({"text": "hi"}, platform_name="darwin", runner=runner)
        launch_result = bridge.execute_human_ops_launch_app({"app": "WeChat"}, platform_name="darwin", runner=runner)
        key_result = bridge.execute_human_ops_key_press({"key": "enter"}, platform_name="darwin", runner=runner)

        self.assertEqual(click_result, {"clicked": True})
        self.assertEqual(type_result, {"typed": True})
        self.assertEqual(launch_result, {"launched": True})
        self.assertEqual(key_result, {"pressed": True})
        desktop_actions._screen_coordinate.assert_called_once_with("41.8", fallback=7)
        desktop_actions._post_core_graphics_click.assert_called_once_with(12, 34)
        desktop_actions.execute_human_ops_click.assert_called_once_with(
            {"x": 12},
            platform_name="darwin",
            runner=runner,
            event_clicker=clicker,
        )
        desktop_actions.execute_human_ops_type_text.assert_called_once_with(
            {"text": "hi"},
            platform_name="darwin",
            runner=runner,
        )
        desktop_actions.execute_human_ops_launch_app.assert_called_once_with(
            {"app": "WeChat"},
            platform_name="darwin",
            runner=runner,
        )
        desktop_actions.execute_human_ops_key_press.assert_called_once_with(
            {"key": "enter"},
            platform_name="darwin",
            runner=runner,
        )

    def test_real_actions_still_run_through_bridge(self) -> None:
        module = importlib.import_module("app.desktop_action_bridge")
        desktop_actions = importlib.import_module("human_ops.desktop_actions")
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        bridge = module.DesktopActionBridge(desktop_actions, qapplication=None)

        with mock.patch.object(desktop_actions, "_post_core_graphics_text") as event_typer:
            result = bridge.execute_human_ops_type_text(
                {"text": 'https://example.com', "label": "浏览器地址栏"},
                platform_name="darwin",
                runner=runner,
            )

        self.assertEqual(
            result,
            {"typed": True, "text": "https://example.com", "label": "浏览器地址栏", "method": "core_graphics_unicode"},
        )
        event_typer.assert_called_once_with("https://example.com")
        self.assertEqual(calls, [])

    def test_qt_event_helpers_use_injected_qapplication(self) -> None:
        module = importlib.import_module("app.desktop_action_bridge")

        class FakeWindow:
            def __init__(self) -> None:
                self.visible = True
                self.calls: list[str] = []

            def isVisible(self) -> bool:
                return self.visible

            def hide(self) -> None:
                self.calls.append("hide")
                self.visible = False

            def show(self) -> None:
                self.calls.append("show")
                self.visible = True

        class FakeApp:
            def __init__(self) -> None:
                self.process_count = 0

            def processEvents(self) -> None:
                self.process_count += 1

        desktop_actions = importlib.import_module("human_ops.desktop_actions")
        fake_app = FakeApp()
        fake_qapplication = mock.Mock()
        fake_qapplication.instance.return_value = fake_app
        bridge = module.DesktopActionBridge(desktop_actions, qapplication=fake_qapplication)
        window = FakeWindow()

        was_hidden = bridge.hide_window_for_desktop_click(window)
        bridge.restore_window_after_desktop_click(window, was_hidden)

        self.assertTrue(was_hidden)
        self.assertEqual(window.calls, ["hide", "show"])
        self.assertEqual(fake_app.process_count, 2)
        self.assertEqual(fake_qapplication.instance.call_count, 2)


if __name__ == "__main__":
    unittest.main()
