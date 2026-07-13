from __future__ import annotations

import ast
import importlib
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]


def _desktop_actions_module():
    try:
        return importlib.import_module("human_ops.desktop_actions")
    except ImportError as exc:
        raise AssertionError("human_ops.desktop_actions module should be importable") from exc


class DesktopActionsModuleTests(unittest.TestCase):
    def test_module_does_not_import_main(self) -> None:
        module = _desktop_actions_module()
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)

        self.assertNotIn("main", imported_modules)

    def test_click_prefers_core_graphics_and_preserves_payload(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []

        def runner(args, **kwargs):
            calls.append(("system_events", args, kwargs))
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        def event_clicker(x, y):
            calls.append(("core_graphics", x, y))

        result = desktop_actions.execute_human_ops_click(
            {"x": "12.4", "y": "34.6", "label": "发送按钮"},
            platform_name="darwin",
            runner=runner,
            event_clicker=event_clicker,
        )

        self.assertEqual(result, {"clicked": True, "x": 12, "y": 35, "label": "发送按钮", "method": "core_graphics"})
        self.assertEqual(calls, [("core_graphics", 12, 35)])

    def test_click_falls_back_to_system_events_after_core_graphics_error(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []

        def runner(args, **kwargs):
            calls.append(("system_events", args, kwargs))
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        def event_clicker(x, y):
            calls.append(("core_graphics", x, y))
            raise RuntimeError("permission denied")

        result = desktop_actions.execute_human_ops_click(
            {"x": "12", "y": "34", "target": "发送按钮"},
            platform_name="darwin",
            runner=runner,
            event_clicker=event_clicker,
        )

        self.assertEqual(result, {"clicked": True, "x": 12, "y": 34, "label": "发送按钮", "method": "system_events"})
        self.assertEqual(calls[0], ("core_graphics", 12, 34))
        self.assertEqual(calls[1][0], "system_events")
        self.assertIn("click at {12, 34}", calls[1][1][-1])

    def test_click_reports_both_core_graphics_and_system_events_errors(self) -> None:
        desktop_actions = _desktop_actions_module()

        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="automation denied")

        def event_clicker(x, y):
            raise RuntimeError("permission denied")

        with self.assertRaisesRegex(
            RuntimeError,
            "CoreGraphics click failed: permission denied; System Events click failed: automation denied",
        ):
            desktop_actions.execute_human_ops_click(
                {"x": 12, "y": 34},
                platform_name="darwin",
                runner=runner,
                event_clicker=event_clicker,
            )

    def test_type_text_uses_core_graphics_unicode_for_url(self) -> None:
        desktop_actions = _desktop_actions_module()
        typed = []

        def runner(args, **kwargs):
            raise AssertionError(f"System Events fallback should not run: {args}")

        result = desktop_actions.execute_human_ops_type_text(
            {"text": "https://example.com/a:b", "label": "浏览器地址栏"},
            platform_name="darwin",
            runner=runner,
            event_typer=typed.append,
        )

        self.assertEqual(typed, ["https://example.com/a:b"])
        self.assertEqual(
            result,
            {
                "typed": True,
                "text": "https://example.com/a:b",
                "label": "浏览器地址栏",
                "method": "core_graphics_unicode",
            },
        )

    def test_type_text_falls_back_to_system_events_after_core_graphics_error(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        result = desktop_actions.execute_human_ops_type_text(
            {"text": '收到 "OK"', "label": "微信聊天输入框"},
            platform_name="darwin",
            runner=runner,
            event_typer=lambda _text: (_ for _ in ()).throw(RuntimeError("permission denied")),
        )

        self.assertEqual(result, {"typed": True, "text": '收到 "OK"', "label": "微信聊天输入框", "method": "system_events"})
        self.assertIn(r'keystroke "收到 \"OK\""', calls[0][0][-1])
        self.assertEqual(calls[0][1]["timeout"], 5)

    def test_launch_app_reads_macos_application_list_then_opens_resolved_bundle(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            if args[0] == "/usr/bin/mdfind":
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="/Applications/WeChat.app\n/Applications/Other.app/Contents/Helper.app\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        result = desktop_actions.execute_human_ops_launch_app(
            {"app": "WeChat"},
            platform_name="darwin",
            runner=runner,
        )

        self.assertEqual(calls[0][0][0], "/usr/bin/mdfind")
        self.assertEqual(calls[1][0], ["/usr/bin/open", "/Applications/WeChat.app"])
        self.assertEqual(result["app"], "WeChat")
        self.assertEqual(result["application_count"], 1)
        self.assertEqual(result["method"], "launch_services")

    def test_launch_app_resolves_netease_music_bundle_name(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            stdout = "/Applications/NeteaseMusic.app\n" if args[0] == "/usr/bin/mdfind" else ""
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

        result = desktop_actions.execute_human_ops_launch_app(
            {"app": "NeteaseMusic"},
            platform_name="darwin",
            runner=runner,
        )

        self.assertEqual(calls[1], ["/usr/bin/open", "/Applications/NeteaseMusic.app"])
        self.assertEqual(result["app"], "NeteaseMusic")

    def test_key_press_enter_and_return_use_key_code_36(self) -> None:
        desktop_actions = _desktop_actions_module()
        scripts = []

        def runner(args, **kwargs):
            scripts.append(args[-1])
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        enter_result = desktop_actions.execute_human_ops_key_press(
            {"key": "enter", "label": "发送消息"},
            platform_name="darwin",
            runner=runner,
        )
        return_result = desktop_actions.execute_human_ops_key_press(
            {"key": "return", "target": "发送消息"},
            platform_name="darwin",
            runner=runner,
        )

        self.assertEqual(enter_result, {"pressed": True, "key": "enter", "label": "发送消息", "method": "system_events"})
        self.assertEqual(return_result, {"pressed": True, "key": "enter", "label": "发送消息", "method": "system_events"})
        self.assertEqual(scripts, [
            'tell application "System Events" to key code 36',
            'tell application "System Events" to key code 36',
        ])

    def test_non_macos_actions_keep_macos_only_errors(self) -> None:
        desktop_actions = _desktop_actions_module()

        with self.assertRaisesRegex(RuntimeError, "Human Ops click is currently implemented through macOS"):
            desktop_actions.execute_human_ops_click({}, platform_name="linux", event_clicker=None)
        with self.assertRaisesRegex(RuntimeError, "Human Ops text input is currently implemented through macOS"):
            desktop_actions.execute_human_ops_type_text({}, platform_name="linux")
        with self.assertRaisesRegex(RuntimeError, "Human Ops key press is currently implemented through macOS"):
            desktop_actions.execute_human_ops_key_press({}, platform_name="linux")
        with self.assertRaisesRegex(RuntimeError, "Human Ops app launch is currently implemented through macOS"):
            desktop_actions.execute_human_ops_launch_app({"app": "WeChat"}, platform_name="linux")

    def test_focus_macos_application_waits_for_verified_frontmost_app(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []
        frontmost = iter(["Ipet", "Google Chrome"])

        def runner(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        result = desktop_actions.focus_macos_application(
            {"target_app": "Google Chrome"},
            platform_name="darwin",
            runner=runner,
            sleeper=lambda _seconds: None,
            frontmost_provider=lambda: next(frontmost),
        )

        self.assertEqual(calls, [["/usr/bin/open", "-a", "Google Chrome"]])
        self.assertEqual(result["frontmost_app"], "Google Chrome")
        self.assertTrue(result["focused"])

    def test_native_approval_dialog_returns_rejection_without_guessing(self) -> None:
        desktop_actions = _desktop_actions_module()

        def runner(args, **kwargs):
            self.assertIn('buttons {"拒绝", "批准"}', args[-1])
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="button returned:拒绝, gave up:false\n",
                stderr="",
            )

        result = desktop_actions.execute_human_ops_native_approval(
            {"message": "点击登录按钮", "timeout_sec": 60},
            platform_name="darwin",
            runner=runner,
        )

        self.assertFalse(result["approved"])
        self.assertEqual(result["method"], "macos_dialog")

    def test_hide_and_restore_window_use_injected_qapplication(self) -> None:
        desktop_actions = _desktop_actions_module()

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

        fake_app = FakeApp()
        fake_qapplication = mock.Mock()
        fake_qapplication.instance.return_value = fake_app
        window = FakeWindow()

        was_hidden = desktop_actions.hide_window_for_desktop_click(window, qapplication=fake_qapplication)
        desktop_actions.restore_window_after_desktop_click(window, was_hidden, qapplication=fake_qapplication)

        self.assertTrue(was_hidden)
        self.assertEqual(window.calls, ["hide", "show"])
        self.assertEqual(fake_app.process_count, 2)

    def test_importing_module_does_not_load_main(self) -> None:
        original_actions = sys.modules.get("human_ops.desktop_actions")
        original_main = sys.modules.get("main")
        sys.modules.pop("human_ops.desktop_actions", None)
        sys.modules.pop("main", None)

        try:
            try:
                importlib.import_module("human_ops.desktop_actions")
            except ImportError as exc:
                raise AssertionError("human_ops.desktop_actions module should be importable") from exc

            self.assertNotIn("main", sys.modules)
        finally:
            sys.modules.pop("human_ops.desktop_actions", None)
            if original_actions is not None:
                sys.modules["human_ops.desktop_actions"] = original_actions
            if original_main is not None:
                sys.modules["main"] = original_main
            else:
                sys.modules.pop("main", None)


if __name__ == "__main__":
    unittest.main()
