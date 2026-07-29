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
    def setUp(self) -> None:
        patcher = mock.patch(
            "body.macos_accessibility.invalidate_macos_accessibility_cache"
        )
        self.cache_invalidator = patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_click_uses_approved_ax_reference_without_coordinate_fallback(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []
        ax_ref = {"app_id": "wechat", "role": "AXButton", "path": [0], "fingerprint": "copied"}

        def accessibility_actioner(target_app, reference, **kwargs):
            calls.append((target_app, reference, kwargs))
            return {
                "ax_target_verified": True,
                "ax_action_performed": True,
                "method": "macos_accessibility",
            }

        result = desktop_actions.execute_human_ops_click(
            {"target_app": "WeChat", "ax_ref": ax_ref, "label": "发送"},
            platform_name="darwin",
            runner=lambda *_args, **_kwargs: self.fail("runner should not be used"),
            event_clicker=lambda *_args: self.fail("coordinate click should not run"),
            accessibility_actioner=accessibility_actioner,
        )

        self.assertEqual(calls[0][0:2], ("WeChat", ax_ref))
        self.assertEqual(calls[0][2]["operation"], "press")
        self.assertTrue(result["clicked"])
        self.assertTrue(result["ax_target_verified"])
        self.assertNotIn("x", result)

    def test_click_noop_keeps_already_active_chat_state_and_cache(self) -> None:
        desktop_actions = _desktop_actions_module()
        result = desktop_actions.execute_human_ops_click(
            {
                "target_app": "QQ",
                "ax_ref": {
                    "app_id": "qq",
                    "role": "AXGroup",
                    "path": [0, 3],
                    "fingerprint": "copied",
                },
                "label": "当前会话",
            },
            platform_name="darwin",
            event_clicker=lambda *_args: self.fail("no coordinate click should run"),
            accessibility_actioner=lambda *_args, **_kwargs: {
                "clicked": False,
                "already_satisfied": True,
                "postcondition_verified": True,
                "ax_target_verified": True,
                "ax_action_performed": False,
                "ax_action": "AXNoOpAlreadyActive",
            },
        )

        self.assertFalse(result["clicked"])
        self.assertTrue(result["already_satisfied"])
        self.cache_invalidator.assert_not_called()

    def test_click_allows_verified_ax_geometry_to_use_human_ops_dispatcher(self) -> None:
        desktop_actions = _desktop_actions_module()
        clicks = []
        ax_ref = {
            "app_id": "qq",
            "role": "AXGroup",
            "path": [0, 3],
            "activation": "hit_test",
            "fingerprint": "copied",
        }

        def event_clicker(x, y):
            clicks.append((x, y))

        def accessibility_actioner(_target_app, _reference, **kwargs):
            kwargs["geometry_clicker"](356, 414)
            return {
                "ax_target_verified": True,
                "ax_action_performed": True,
                "ax_action": "AXGeometryHitTest",
                "x": 356,
                "y": 414,
                "method": "macos_accessibility+core_graphics",
            }

        result = desktop_actions.execute_human_ops_click(
            {
                "target_app": "QQ",
                "ax_ref": ax_ref,
                "label": "测试联系人",
            },
            platform_name="darwin",
            event_clicker=event_clicker,
            accessibility_actioner=accessibility_actioner,
        )

        self.assertEqual(clicks, [(356, 414)])
        self.assertEqual(result["ax_action"], "AXGeometryHitTest")
        self.assertEqual(
            result["method"],
            "macos_accessibility+core_graphics",
        )

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

    def test_type_text_can_focus_an_approved_ax_input_before_typing(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []
        focus_kwargs = []
        ax_ref = {"app_id": "qq", "role": "AXTextArea", "path": [2], "fingerprint": "copied"}
        clicker = lambda _x, _y: None

        def accessibility_actioner(target_app, reference, **kwargs):
            calls.append(("ax", target_app, reference, kwargs["operation"]))
            focus_kwargs.append(kwargs)
            return {
                "ax_target_verified": True,
                "ax_action_performed": True,
                "ax_action": "AXFocused",
                "method": "macos_accessibility",
            }

        result = desktop_actions.execute_human_ops_type_text(
            {
                "target_app": "QQ",
                "ax_ref": ax_ref,
                "intended_chat": "目标会话",
                "text": "你好",
                "label": "消息输入框",
            },
            platform_name="darwin",
            event_typer=lambda text: calls.append(("type", text)),
            event_clicker=clicker,
            accessibility_actioner=accessibility_actioner,
            input_value_verifier=lambda _target_app, **kwargs: calls.append(
                ("verify", kwargs["expected_text"])
            )
            or {
                "input_value_verified": True,
                "postcondition_verified": True,
                "method": "macos_accessibility",
            },
            chat_context_verifier=lambda target_app, **kwargs: calls.append(
                (
                    "chat",
                    target_app,
                    kwargs["intended_chat"],
                    kwargs["input_ax_ref"],
                )
            )
            or {
                "chat_identity_verified": True,
                "chat_input_verified": True,
            },
        )

        self.assertEqual(calls[0], ("ax", "QQ", ax_ref, "focus"))
        self.assertIs(focus_kwargs[0]["geometry_clicker"], clicker)
        self.assertEqual(calls[1], ("chat", "QQ", "目标会话", ax_ref))
        self.assertEqual(calls[2], ("type", "你好"))
        self.assertEqual(calls[3], ("verify", "你好"))
        self.assertEqual(calls[4], ("chat", "QQ", "目标会话", ax_ref))
        self.assertTrue(result["ax_target_verified"])
        self.assertTrue(result["chat_identity_verified"])
        self.assertTrue(result["postcondition_verified"])
        self.assertEqual(result["verification_method"], "macos_accessibility")
        self.assertEqual(result["method"], "macos_accessibility_focus+core_graphics_unicode")

    def test_chat_search_field_can_type_without_message_recipient_binding(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []
        search_ref = {
            "app_id": "qq",
            "role": "AXTextField",
            "path": [0, 1],
            "input_kind": "search_field",
            "fingerprint": "copied",
        }

        result = desktop_actions.execute_human_ops_type_text(
            {
                "target_app": "QQ",
                "ax_ref": search_ref,
                "text": "莉霖澪",
                "label": "搜索输入框",
            },
            platform_name="darwin",
            event_typer=lambda text: calls.append(("type", text)),
            accessibility_actioner=lambda target_app, reference, **kwargs: (
                calls.append(
                    (
                        "focus",
                        target_app,
                        reference,
                        kwargs["operation"],
                    )
                )
                or {
                    "ax_target_verified": True,
                    "ax_action_performed": True,
                    "target_pid": 777,
                    "method": "macos_accessibility",
                }
            ),
            input_value_verifier=lambda _target_app, **_kwargs: {
                "input_value_verified": True,
                "postcondition_verified": True,
            },
            chat_context_verifier=lambda *_args, **_kwargs: self.fail(
                "search input must not run chat recipient verification"
            ),
        )

        self.assertEqual(
            calls,
            [
                ("focus", "QQ", search_ref, "focus"),
                ("type", "莉霖澪"),
            ],
        )
        self.assertEqual(result["target_pid"], 777)
        self.assertTrue(result["typed"])
        self.assertTrue(result["postcondition_verified"])

    def test_signed_search_field_can_be_cleared_with_targeted_replace(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []
        search_ref = {
            "app_id": "qq",
            "role": "AXTextField",
            "path": [0, 1],
            "input_kind": "search_field",
            "fingerprint": "copied",
        }

        def accessibility_actioner(_app, _reference, **kwargs):
            if kwargs["operation"] == "set_value":
                raise RuntimeError("AXValue is not settable")
            return {
                "ax_target_verified": True,
                "target_pid": 777,
            }

        result = desktop_actions.execute_human_ops_type_text(
            {
                "target_app": "QQ",
                "ax_ref": search_ref,
                "text": "",
                "replace_existing": True,
                "label": "搜索输入框",
            },
            platform_name="darwin",
            event_typer=lambda text: calls.append(("type", text)),
            event_key_presser=lambda key_code, **kwargs: calls.append(
                ("key", key_code, kwargs)
            ),
            accessibility_actioner=accessibility_actioner,
            input_value_verifier=lambda _target_app, **kwargs: {
                "input_value_verified": kwargs["expected_text"] == "",
                "postcondition_verified": kwargs["expected_text"] == "",
            },
            chat_context_verifier=lambda *_args, **_kwargs: self.fail(
                "search input must not run chat recipient verification"
            ),
        )

        self.assertEqual(
            calls,
            [
                ("key", 0, {"target_pid": 777, "flags": 1 << 20}),
                ("key", 51, {"target_pid": 777}),
                ("type", ""),
            ],
        )
        self.assertTrue(result["replace_existing"])
        self.assertEqual(result["target_pid"], 777)
        self.assertTrue(result["postcondition_verified"])
        self.assertEqual(
            result["semantic_value_fallback_reason"],
            "AXValue is not settable",
        )

    def test_replace_text_prefers_verified_ax_value_assignment(self) -> None:
        desktop_actions = _desktop_actions_module()
        operations = []
        search_ref = {
            "app_id": "qq",
            "role": "AXTextField",
            "path": [0, 1],
            "input_kind": "search_field",
            "fingerprint": "copied",
        }

        def accessibility_actioner(_app, _reference, **kwargs):
            operations.append((kwargs["operation"], kwargs.get("value")))
            return {
                "ax_target_verified": True,
                "target_pid": 777,
                **(
                    {"input_value_set": True}
                    if kwargs["operation"] == "set_value"
                    else {}
                ),
            }

        result = desktop_actions.execute_human_ops_type_text(
            {
                "target_app": "QQ",
                "ax_ref": search_ref,
                "text": "",
                "replace_existing": True,
                "label": "搜索输入框",
            },
            platform_name="darwin",
            event_typer=lambda _text: self.fail("AXValue set must avoid typing"),
            event_key_presser=lambda *_args, **_kwargs: self.fail(
                "AXValue set must avoid keyboard shortcuts"
            ),
            accessibility_actioner=accessibility_actioner,
            input_value_verifier=lambda _target_app, **kwargs: {
                "input_value_verified": kwargs["expected_text"] == "",
            },
        )

        self.assertEqual(operations, [("set_value", "")])
        self.assertTrue(result["input_value_set"])
        self.assertTrue(result["postcondition_verified"])
        self.assertEqual(result["method"], "macos_accessibility_set_value")

    def test_accepted_async_ax_value_is_not_redelivered_by_keyboard(self) -> None:
        desktop_actions = _desktop_actions_module()
        search_ref = {
            "app_id": "qq",
            "role": "AXTextField",
            "path": [0, 1],
            "input_kind": "search_field",
            "fingerprint": "copied",
        }

        def accessibility_actioner(_app, _reference, **kwargs):
            return {
                "ax_target_verified": True,
                "target_pid": 777,
                **(
                    {"input_value_set": True}
                    if kwargs["operation"] == "set_value"
                    else {}
                ),
            }

        result = desktop_actions.execute_human_ops_type_text(
            {
                "target_app": "QQ",
                "ax_ref": search_ref,
                "text": "async draft",
                "replace_existing": True,
            },
            platform_name="darwin",
            event_typer=lambda _text: self.fail(
                "accepted AXValue must not be delivered again"
            ),
            event_key_presser=lambda *_args, **_kwargs: self.fail(
                "accepted AXValue must not fall back to a keyboard shortcut"
            ),
            accessibility_actioner=accessibility_actioner,
            input_value_verifier=lambda *_args, **_kwargs: {
                "input_value_verified": False,
            },
        )

        self.assertTrue(result["input_value_set"])
        self.assertFalse(result["postcondition_verified"])
        self.assertEqual(result["method"], "macos_accessibility_set_value")

    def test_type_text_postcondition_failure_does_not_redeliver_text(self) -> None:
        desktop_actions = _desktop_actions_module()
        typed: list[str] = []
        ax_ref = {
            "app_id": "qq",
            "role": "AXTextField",
            "path": [0, 1],
            "bounds": {"x": 100, "y": 100, "width": 180, "height": 28},
            "input_kind": "search_field",
            "fingerprint": "copied",
        }

        result = desktop_actions.execute_human_ops_type_text(
            {
                "target_app": "QQ",
                "ax_ref": ax_ref,
                "text": "只输入一次",
                "label": "搜索输入框",
            },
            platform_name="darwin",
            event_typer=lambda text: typed.append(text),
            accessibility_actioner=lambda *_args, **_kwargs: {
                "ax_target_verified": True,
                "ax_action_performed": True,
                "target_pid": 777,
            },
            input_value_verifier=lambda *_args, **_kwargs: (
                (_ for _ in ()).throw(RuntimeError("value not observable"))
            ),
            runner=lambda *_args, **_kwargs: self.fail(
                "postcondition failure must not fall back and type again"
            ),
        )

        self.assertEqual(typed, ["只输入一次"])
        self.assertTrue(result["typed"])
        self.assertFalse(result["postcondition_verified"])
        self.assertEqual(result["postcondition_reason"], "value not observable")

    def test_replace_text_requires_reviewed_input_reference(self) -> None:
        desktop_actions = _desktop_actions_module()

        with self.assertRaisesRegex(
            RuntimeError,
            "reviewed AX input reference",
        ):
            desktop_actions.execute_human_ops_type_text(
                {
                    "target_app": "Music",
                    "text": "",
                    "replace_existing": True,
                },
                platform_name="darwin",
                event_typer=lambda _text: None,
            )

    def test_chat_send_revalidates_recipient_and_draft_before_enter(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []
        input_ref = {
            "app_id": "wechat",
            "role": "AXTextArea",
            "path": [0, 2],
            "fingerprint": "copied",
        }

        result = desktop_actions.execute_human_ops_key_press(
            {
                "target_app": "WeChat",
                "key": "enter",
                "intended_chat": "目标会话",
                "expected_text": "准备发送的草稿",
                "input_ax_ref": input_ref,
            },
            platform_name="darwin",
            event_presser=lambda key_code, **kwargs: calls.append(
                ("key", key_code, kwargs)
            ),
            chat_context_verifier=lambda target_app, **kwargs: calls.append(
                ("chat", target_app, kwargs)
            )
            or {
                "chat_identity_verified": True,
                "chat_input_verified": True,
                "chat_draft_verified": True,
                "target_pid": 888,
            },
        )

        self.assertEqual(calls[0][0], "chat")
        self.assertEqual(calls[0][2]["intended_chat"], "目标会话")
        self.assertEqual(calls[0][2]["expected_text"], "准备发送的草稿")
        self.assertEqual(calls[0][2]["input_ax_ref"], input_ref)
        self.assertTrue(calls[0][2]["require_input_focused"])
        self.assertEqual(calls[1][0], "key")
        self.assertEqual(calls[1][1], 36)
        self.assertEqual(calls[1][2]["target_pid"], 888)
        self.assertTrue(result["chat_identity_verified"])
        self.assertTrue(result["chat_draft_verified"])
        self.assertEqual(result["method"], "targeted_core_graphics")

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

    def test_launch_app_normalizes_localized_ax_allowlist_name(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            stdout = "/System/Applications/Music.app\n" if args[0] == "/usr/bin/mdfind" else ""
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

        result = desktop_actions.execute_human_ops_launch_app(
            {"app": "音乐"},
            platform_name="darwin",
            runner=runner,
        )

        self.assertEqual(calls[1], ["/usr/bin/open", "/System/Applications/Music.app"])
        self.assertEqual(result["app"], "Music")

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

    def test_launch_app_falls_back_to_bundle_identifier(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            if args[:3] == ["/usr/bin/mdfind", "-onlyin", "/Applications"]:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                args=args,
                returncode=0 if args[:2] == ["/usr/bin/open", "-b"] else 1,
                stdout="",
                stderr="not found",
            )

        result = desktop_actions.execute_human_ops_launch_app(
            {"app": "WeChat"},
            platform_name="darwin",
            runner=runner,
        )

        self.assertTrue(result["launched"])
        self.assertIn(
            ["/usr/bin/open", "-b", "com.tencent.xinWeChat"],
            calls,
        )

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
        self.assertEqual(result["previous_frontmost_app"], "Ipet")
        self.assertTrue(result["focused"])

    def test_focus_requires_a_window_on_the_current_space(self) -> None:
        desktop_actions = _desktop_actions_module()
        surfaces = iter([False, True])
        activations = []

        result = desktop_actions.focus_macos_application(
            {"target_app": "QQ"},
            platform_name="darwin",
            sleeper=lambda _seconds: None,
            frontmost_provider=lambda: "QQ",
            native_activator=lambda profile: activations.append(
                profile["bundle_id"]
            )
            or "launch_services",
            visible_window_provider=lambda _profile: next(surfaces),
        )

        self.assertEqual(
            activations,
            ["com.tencent.qq"],
        )
        self.assertTrue(result["focused"])
        self.assertTrue(result["surface_visible"])

    def test_ax_window_metadata_can_verify_surface_without_reading_content(self) -> None:
        desktop_actions = _desktop_actions_module()
        runtime = mock.Mock()
        runtime.trusted.return_value = True
        runtime.focused_application.return_value = (4321, "QQ")
        runtime.application.return_value = 100
        runtime._attribute_elements.return_value = ([200], False)
        runtime.attributes.return_value = {
            "AXMinimized": False,
            "AXSize": {"width": 980, "height": 720},
        }

        with mock.patch.object(
            desktop_actions._macos_accessibility,
            "_AXRuntime",
            return_value=runtime,
        ):
            visible = desktop_actions._visible_macos_accessibility_window(
                desktop_actions._macos_accessibility.resolve_macos_ax_app("QQ")
            )

        self.assertTrue(visible)
        runtime.attributes.assert_called_once_with(
            200,
            ("AXMinimized", "AXSize"),
        )
        self.assertEqual(
            [call.args[0] for call in runtime.release.call_args_list],
            [200, 100],
        )
        runtime.close.assert_called_once_with()

    def test_frontmost_application_prefers_native_accessibility_query(self) -> None:
        desktop_actions = _desktop_actions_module()
        runtime = mock.Mock()
        runtime.trusted.return_value = True
        runtime.focused_application.return_value = (1234, "QQ")
        runner = mock.Mock()

        with mock.patch.object(
            desktop_actions._macos_accessibility,
            "_AXRuntime",
            return_value=runtime,
        ):
            result = desktop_actions.frontmost_macos_application(runner=runner)

        self.assertEqual(result, "QQ")
        runtime.close.assert_called_once_with()
        runner.assert_not_called()

    def test_frontmost_application_tolerates_system_events_timeout(self) -> None:
        desktop_actions = _desktop_actions_module()
        runtime = mock.Mock()
        runtime.trusted.return_value = False
        runner = mock.Mock(side_effect=subprocess.TimeoutExpired("osascript", 1.0))

        with mock.patch.object(
            desktop_actions._macos_accessibility,
            "_AXRuntime",
            return_value=runtime,
        ):
            result = desktop_actions.frontmost_macos_application(runner=runner)

        self.assertEqual(result, "")
        runtime.close.assert_called_once_with()

    def test_focus_macos_application_normalizes_localized_allowlisted_name(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        result = desktop_actions.focus_macos_application(
            {"target_app": "微信"},
            platform_name="darwin",
            runner=runner,
            sleeper=lambda _seconds: None,
            frontmost_provider=lambda: "WeChat",
        )

        self.assertEqual(calls, [["/usr/bin/open", "-a", "WeChat"]])
        self.assertTrue(result["focused"])

    def test_focus_macos_application_prefers_native_running_app_activation(self) -> None:
        desktop_actions = _desktop_actions_module()
        runner = mock.Mock()
        activations = []
        frontmost = iter(["ChatGPT", "WeChat"])

        result = desktop_actions.focus_macos_application(
            {"target_app": "WeChat"},
            platform_name="darwin",
            runner=runner,
            sleeper=lambda _seconds: None,
            frontmost_provider=lambda: next(frontmost),
            native_activator=lambda profile: activations.append(profile["bundle_id"]) or True,
        )

        runner.assert_not_called()
        self.assertEqual(activations, ["com.tencent.xinWeChat"])
        self.assertEqual(result["method"], "native")
        self.assertEqual(result["frontmost_app"], "WeChat")
        self.assertEqual(result["previous_frontmost_app"], "ChatGPT")

    def test_restore_focus_only_when_observed_target_is_still_frontmost(self) -> None:
        desktop_actions = _desktop_actions_module()
        activations = []

        result = desktop_actions.restore_macos_application_focus(
            {
                "target_app": "WeChat",
                "previous_frontmost_app": "ChatGPT",
            },
            platform_name="darwin",
            frontmost_provider=lambda: "WeChat",
            native_activator=lambda profile: activations.append(
                profile["display_name"]
            )
            or "appkit",
        )

        self.assertTrue(result["restored"])
        self.assertEqual(result["target_app"], "ChatGPT")
        self.assertEqual(activations, ["ChatGPT"])

    def test_restore_focus_preserves_user_foreground_change(self) -> None:
        desktop_actions = _desktop_actions_module()
        activator = mock.Mock()

        result = desktop_actions.restore_macos_application_focus(
            {
                "target_app": "WeChat",
                "previous_frontmost_app": "ChatGPT",
            },
            platform_name="darwin",
            frontmost_provider=lambda: "Finder",
            native_activator=activator,
        )

        self.assertFalse(result["restored"])
        self.assertEqual(result["reason"], "foreground_changed_by_user")
        activator.assert_not_called()

    def test_native_activation_uses_accessibility_before_system_events(self) -> None:
        desktop_actions = _desktop_actions_module()
        with mock.patch.object(
            desktop_actions,
            "_activate_running_macos_application",
            return_value=False,
        ), mock.patch.object(
            desktop_actions,
            "_activate_macos_application_with_launch_services",
            return_value=False,
        ), mock.patch.object(
            desktop_actions._macos_accessibility,
            "activate_macos_accessibility_application",
            return_value=True,
        ) as activate_ax, mock.patch.object(
            desktop_actions,
            "_activate_running_macos_application_with_system_events",
        ) as activate_system_events:
            method = desktop_actions._activate_macos_application(
                {"app_id": "wechat", "display_name": "微信"},
                runner=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(method, "accessibility")
        activate_ax.assert_called_once()
        activate_system_events.assert_not_called()

    def test_native_activation_prefers_launch_services_before_accessibility(self) -> None:
        desktop_actions = _desktop_actions_module()
        with mock.patch.object(
            desktop_actions,
            "_activate_running_macos_application",
            return_value=False,
        ), mock.patch.object(
            desktop_actions,
            "_activate_macos_application_with_launch_services",
            return_value=True,
        ) as activate_launch_services, mock.patch.object(
            desktop_actions._macos_accessibility,
            "activate_macos_accessibility_application",
        ) as activate_ax:
            method = desktop_actions._activate_macos_application(
                {
                    "app_id": "qq",
                    "display_name": "QQ",
                    "launch_name": "QQ",
                    "bundle_id": "com.tencent.qq",
                },
                runner=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(method, "launch_services")
        activate_launch_services.assert_called_once()
        activate_ax.assert_not_called()

    def test_system_events_activation_targets_running_bundle_without_launch_services(self) -> None:
        desktop_actions = _desktop_actions_module()
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="微信\n",
                stderr="",
            )

        activated = desktop_actions._activate_running_macos_application_with_system_events(
            {
                "bundle_id": "com.tencent.xinWeChat",
                "display_name": "微信",
                "launch_name": "WeChat",
                "aliases": ("微信", "WeChat"),
            },
            runner=runner,
        )

        self.assertTrue(activated)
        self.assertEqual(calls[0][0][:2], ["/usr/bin/osascript", "-e"])
        self.assertIn("com.tencent.xinWeChat", calls[0][0][-1])
        self.assertIn("set frontmost of proc to true", calls[0][0][-1])

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

    def test_full_authorization_notice_is_noninteractive(self) -> None:
        desktop_actions = _desktop_actions_module()

        def runner(args, **kwargs):
            self.assertIn("display notification", args[-1])
            self.assertIn("with title", args[-1])
            self.assertNotIn("buttons", args[-1])
            self.assertEqual(kwargs["timeout"], 5)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        result = desktop_actions.execute_human_ops_native_approval(
            {
                "notice_only": True,
                "title": "Ipet 完全授权操作",
                "message": "即将点击。",
                "task_id": "task-notice",
            },
            platform_name="darwin",
            runner=runner,
        )

        self.assertTrue(result["notified"])
        self.assertEqual(result["method"], "macos_notification")
        self.assertEqual(result["task_id"], "task-notice")

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
