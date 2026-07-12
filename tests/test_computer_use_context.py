from __future__ import annotations

import unittest
from unittest import mock

import backend.app as backend_app
from backend import computer_use_context as context_helpers


class ComputerUseContextTests(unittest.TestCase):
    def test_intent_helpers_classify_observe_action_and_click_requests(self) -> None:
        for text in ("看看屏幕", "当前屏幕有什么", "帮我观察屏幕"):
            with self.subTest(text=text):
                self.assertTrue(context_helpers._looks_like_desktop_observe_request(text))

        for text in ("如何打开 app", "为什么打开微信"):
            with self.subTest(text=text):
                self.assertFalse(context_helpers._looks_like_desktop_action_request(text))
                self.assertFalse(context_helpers._looks_like_click_request(text))

        self.assertTrue(context_helpers._looks_like_desktop_action_request("帮我打开微信"))
        self.assertTrue(context_helpers._looks_like_app_launch_request("帮我打开微信"))
        self.assertEqual(context_helpers._app_launch_target("帮我打开微信"), "WeChat")
        self.assertFalse(context_helpers._looks_like_click_request("帮我打开微信"))
        localized_app_names = {
            "打开网易云音乐": "NeteaseMusic",
            "启动腾讯会议": "TencentMeeting",
            "打开库乐队": "GarageBand",
            "打开Keynote讲演": "Keynote",
            "打开Numbers表格": "Numbers",
            "打开Pages文稿": "Pages",
            "打开Safari浏览器": "Safari",
            "打开iMovie 剪辑": "iMovie",
        }
        for request, app_name in localized_app_names.items():
            with self.subTest(request=request):
                self.assertEqual(context_helpers._app_launch_target(request), app_name)

        self.assertTrue(context_helpers._looks_like_desktop_action_request("点击按钮"))
        self.assertTrue(context_helpers._looks_like_click_request("点击按钮"))

    def test_app_intent_wrappers_delegate_to_computer_use_context_helpers(self) -> None:
        with mock.patch.object(context_helpers, "_looks_like_desktop_observe_request", return_value=True) as helper:
            self.assertTrue(backend_app._looks_like_desktop_observe_request("看看屏幕"))
        helper.assert_called_once_with("看看屏幕")

        with mock.patch.object(context_helpers, "_looks_like_desktop_action_request", return_value=True) as helper:
            self.assertTrue(backend_app._looks_like_desktop_action_request("帮我打开微信"))
        helper.assert_called_once_with("帮我打开微信")

        with mock.patch.object(context_helpers, "_looks_like_click_request", return_value=True) as helper:
            self.assertTrue(backend_app._looks_like_click_request("点击按钮"))
        helper.assert_called_once_with("点击按钮")

    def test_negative_visibility_does_not_create_dock_app_icon(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "屏幕上目前没有显示微信图标，未显示 Dock 栏或桌面。",
            {"active_observation": {"target_hint": "打开微信"}},
            "打开微信",
        )

        affordances = {item["kind"]: item for item in context["affordances"]}
        self.assertNotEqual(context["surface"].get("region"), "dock")
        self.assertNotIn("app_icon", affordances)

    def test_hidden_dock_with_screen_bounds_creates_reveal_affordance(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "当前浏览器窗口占满屏幕，未显示 Dock 栏，也没有看到微信图标。",
            {
                "active_observation": {
                    "target_hint": "打开微信",
                    "screen_bounds": {"x": 0, "y": 0, "width": 1470, "height": 956},
                }
            },
            "打开微信",
        )

        affordances = {item["kind"]: item for item in context["affordances"]}
        self.assertIn("screen_edge", affordances)
        self.assertEqual(affordances["screen_edge"]["purpose"], "reveal_hidden_dock")
        self.assertEqual(affordances["screen_edge"]["location"], {"x": 735, "y": 954})

    def test_wechat_observation_creates_contact_input_and_chat_context(self) -> None:
        context = context_helpers._infer_computer_use_context(
            (
                "微信聊天窗口显示联系人张三。"
                "最近聊天内容：张三：下午3点记得带资料；我：收到。"
                "底部聊天输入框可见，发送入口可用。"
            ),
            {"active_observation": {"target_hint": "根据张三的聊天信息回复张三"}},
            "根据张三的聊天信息回复张三",
        )

        affordances = {item["kind"]: item for item in context["affordances"]}
        self.assertEqual(context["surface"]["kind"], "wechat_gui")
        self.assertIn("chat_input", affordances)
        self.assertEqual(context["chat_context"]["contact"], "张三")
        self.assertEqual(
            context["chat_context"]["recent_messages"],
            [
                {"speaker": "张三", "text": "下午3点记得带资料"},
                {"speaker": "我", "text": "收到"},
            ],
        )

    def test_computer_use_context_text_includes_chat_context(self) -> None:
        context_text = context_helpers._computer_use_context_text(
            {
                "surface": {"kind": "wechat_gui", "region": "chat", "confidence": 0.76},
                "affordances": [{"kind": "chat_input", "supports": ["type_text", "key_press"]}],
                "chat_context": {
                    "contact": "张三",
                    "recent_messages": [{"speaker": "张三", "text": "下午3点记得带资料"}],
                    "input_ready": True,
                    "send_ready": True,
                },
            }
        )

        self.assertIn("Structured computer-use context", context_text)
        self.assertIn("chat_context", context_text)
        self.assertIn("下午3点记得带资料", context_text)


if __name__ == "__main__":
    unittest.main()
