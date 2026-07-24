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
        self.assertTrue(context_helpers._looks_like_chat_reply_request("在 QQ 回复小明"))
        self.assertTrue(context_helpers._looks_like_desktop_action_request("暂停音乐"))
        self.assertTrue(context_helpers._looks_like_desktop_action_request("在访达选择文件"))
        self.assertFalse(context_helpers._looks_like_click_request("帮我打开微信"))
        localized_app_names = {
            "打开QQ": "QQ",
            "打开音乐": "Music",
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

    def test_generic_web_chat_is_not_coerced_to_wechat(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "B站私信聊天界面中可见联系人小明、搜索框和消息输入框。",
            {"desktop_context": {"foreground_app": "Google Chrome"}},
            "打开 B 站并查看私信",
        )

        self.assertEqual(context["surface"]["kind"], "chat_gui")
        self.assertEqual(context["surface"]["app"], "Google Chrome")
        self.assertNotEqual(context["surface"]["kind"], "wechat_gui")
        search = next(item for item in context["affordances"] if item["kind"] == "search_field")
        self.assertEqual(search["label"], "搜索框")

    def test_accessibility_elements_become_semantic_affordances(self) -> None:
        ax_ref = {
            "app_id": "wechat",
            "role": "AXTextArea",
            "path": [0, 2],
            "fingerprint": "copied",
        }
        context = context_helpers._infer_computer_use_context(
            "AX 读取成功",
            {
                "accessibility": {
                    "usable": True,
                    "app": {
                        "app_id": "wechat",
                        "name": "微信",
                        "bundle_id": "com.tencent.xinWeChat",
                        "surface": "wechat_gui",
                    },
                    "elements": [
                        {
                            "role": "AXTextArea",
                            "label": "消息输入框",
                            "identifier": "message-input",
                            "supports": ["focus", "type_text"],
                            "focused": True,
                            "ax_ref": ax_ref,
                        }
                    ],
                }
            },
            "回复消息",
        )

        self.assertEqual(context["surface"]["kind"], "wechat_gui")
        self.assertEqual(context["surface"]["source"], "macos_accessibility")
        self.assertEqual(context["affordances"][0]["kind"], "chat_input")
        self.assertEqual(context["affordances"][0]["supports"], ["click", "type_text", "key_press"])
        self.assertEqual(context["affordances"][0]["ax_ref"], ax_ref)
        self.assertTrue(context["chat_context"]["input_focused"])
        self.assertTrue(context_helpers._observation_has_reviewable_click_affordance(context))

    def test_accessibility_affordance_preserves_hierarchy_path_and_search_quality(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "AX 读取成功",
            {
                "accessibility": {
                    "usable": True,
                    "app": {
                        "app_id": "music",
                        "name": "音乐",
                        "surface": "music_gui",
                    },
                    "elements": [
                        {
                            "role": "AXRow",
                            "label": "专辑",
                            "label_source": "descendant",
                            "semantic_path": "资料库 > 专辑",
                            "supports": ["select"],
                            "ax_ref": {"fingerprint": "album-row"},
                        }
                    ],
                    "search": {
                        "query": "打开专辑",
                        "matched_count": 2,
                        "actionable_match_count": 1,
                        "visible_match_count": 1,
                        "contextual_count": 1,
                        "sufficient": True,
                    },
                }
            },
            "打开专辑",
        )

        self.assertEqual(context["affordances"][0]["label"], "专辑")
        self.assertEqual(context["affordances"][0]["semantic_path"], "资料库 > 专辑")
        self.assertTrue(context["ax_search"]["sufficient"])
        self.assertEqual(context["ax_search"]["actionable_match_count"], 1)

    def test_hidden_menu_item_is_not_a_page_affordance_without_menu_query(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "AX 搜索不足",
            {
                "accessibility": {
                    "usable": True,
                    "app": {"app_id": "qq", "name": "QQ", "surface": "qq_gui"},
                    "elements": [
                        {
                            "role": "AXMenuItem",
                            "label": "搜索",
                            "supports": ["press"],
                            "semantic_path": "编辑 > 搜索",
                            "ax_ref": {"fingerprint": "hidden-search"},
                        }
                    ],
                    "search": {
                        "query": "搜索",
                        "sufficient": False,
                        "insufficiency_reason": "only_hidden_menu_matches",
                    },
                }
            },
            "找到搜索框",
        )

        self.assertEqual(context["affordances"], [])
        self.assertFalse(context["ax_search"]["sufficient"])

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

    def test_accessibility_context_exposes_cache_index_without_full_tree(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "AX 搜索完成",
            {
                "accessibility": {
                    "usable": True,
                    "snapshot_id": "axc-123",
                    "total_element_count": 420,
                    "app": {
                        "app_id": "qq",
                        "name": "QQ",
                        "bundle_id": "com.tencent.qq",
                        "surface": "qq_gui",
                    },
                    "search": {
                        "query": "张三",
                        "cache_hit": True,
                        "persistent": True,
                        "source": "disk",
                        "stale": True,
                        "stale_reason": "process_changed",
                        "age_ms": 1250,
                        "returned_count": 4,
                        "matched_count": 1,
                        "search_truncated": True,
                        "index": {
                            "roles": {"AXRow": 400, "AXButton": 20},
                            "supports": {"select": 400, "press": 20},
                            "actionable": 420,
                        },
                    },
                    "elements": [],
                }
            },
            "查找张三",
        )

        self.assertEqual(context["ax_search"]["tool"], "observe.ax_query")
        self.assertEqual(context["ax_search"]["total_element_count"], 420)
        self.assertEqual(context["ax_search"]["returned_count"], 4)
        self.assertTrue(context["ax_search"]["persistent"])
        self.assertEqual(context["ax_search"]["source"], "disk")
        self.assertTrue(context["ax_search"]["stale"])
        self.assertEqual(context["ax_search"]["stale_reason"], "process_changed")
        self.assertEqual(context["ax_search"]["age_ms"], 1250)
        context_text = context_helpers._computer_use_context_text(context)
        self.assertIn('"ax_search"', context_text)
        self.assertNotIn("联系人399", context_text)

    def test_legacy_large_ax_frame_is_still_capped_before_brain_context(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "AX 读取成功",
            {
                "accessibility": {
                    "usable": True,
                    "app": {
                        "app_id": "qq",
                        "name": "QQ",
                        "surface": "qq_gui",
                    },
                    "elements": [
                        {
                            "role": "AXButton",
                            "label": f"按钮{index}",
                            "supports": ["press"],
                            "ax_ref": {"fingerprint": f"ref-{index}"},
                        }
                        for index in range(100)
                    ],
                }
            },
            "按钮",
        )

        self.assertEqual(len(context["affordances"]), 16)


if __name__ == "__main__":
    unittest.main()
