from __future__ import annotations

import unittest
from unittest import mock

from backend import computer_use_context as context_helpers


class ComputerUseContextTests(unittest.TestCase):
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
                    "require_coordinates": True,
                    "screen_bounds": {"x": 0, "y": 0, "width": 1470, "height": 956},
                }
            },
            "打开微信",
        )

        affordances = {item["kind"]: item for item in context["affordances"]}
        self.assertIn("screen_edge", affordances)
        self.assertEqual(affordances["screen_edge"]["purpose"], "reveal_hidden_dock")
        self.assertEqual(affordances["screen_edge"]["location"], {"x": 735, "y": 954})

    def test_hidden_dock_words_do_not_select_coordinate_route(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "当前浏览器窗口占满屏幕，未显示 Dock 栏，也没有看到微信图标。",
            {
                "active_observation": {
                    "target_hint": "请点击 Dock 里的微信",
                    "screen_bounds": {"x": 0, "y": 0, "width": 1470, "height": 956},
                }
            },
            "请点击 Dock 里的微信",
        )

        self.assertNotIn("screen_edge", {item["kind"] for item in context["affordances"]})

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
        self.assertEqual(
            context["affordances"][0]["supports"],
            ["click", "type_text", "key_press"],
        )
        self.assertEqual(context["affordances"][0]["ax_ref"], ax_ref)
        self.assertTrue(context["chat_context"]["input_focused"])
        self.assertTrue(
            context_helpers._observation_has_reviewable_click_affordance(
                context
            )
        )

    def test_local_ocr_match_augments_insufficient_accessibility_context(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "本机 OCR 找到唯一关键词匹配。",
            {
                "accessibility": {
                    "usable": True,
                    "app": {
                        "app_id": "wechat",
                        "name": "微信",
                        "surface": "wechat_gui",
                    },
                    "elements": [],
                    "search": {
                        "query": "点击目标会话",
                        "sufficient": False,
                        "insufficiency_reason": "no_semantic_match",
                    },
                },
                "local_ocr_search": {
                    "available": True,
                    "query": "点击目标会话",
                    "matched_count": 1,
                    "best_match_count": 1,
                    "sufficient": True,
                    "actionable": True,
                    "matches": [
                        {
                            "label": "目标会话",
                            "kind": "chat_thread",
                            "supports": ["click"],
                            "confidence": 0.91,
                            "location": {
                                "x": 191,
                                "y": 301,
                            },
                        }
                    ],
                },
            },
            "点击目标会话",
        )

        self.assertEqual(context["surface"]["app_id"], "wechat")
        self.assertEqual(context["affordances"][0]["label"], "目标会话")
        self.assertEqual(
            context["affordances"][0]["location"]["coordinate_space"],
            "macos_screen_points",
        )
        self.assertTrue(context["visual_search"]["sufficient"])

    def test_local_ocr_match_ignores_malformed_confidence(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "本机 OCR 找到唯一关键词匹配。",
            {
                "local_ocr_search": {
                    "available": True,
                    "query": "点击目标会话",
                    "matched_count": 1,
                    "best_match_count": 1,
                    "sufficient": True,
                    "actionable": True,
                    "matches": [
                        {
                            "label": "目标会话",
                            "kind": "chat_thread",
                            "supports": ["click"],
                            "confidence": "unknown",
                            "location": {"x": 191, "y": 301},
                        }
                    ],
                }
            },
            "点击目标会话",
        )

        self.assertEqual(context["affordances"][0]["confidence"], 0.0)

    def test_verified_geometry_collection_item_becomes_click_affordance(self) -> None:
        ax_ref = {
            "app_id": "qq",
            "role": "AXGroup",
            "path": [0, 3],
            "bounds": {"x": 231, "y": 382, "width": 250, "height": 64},
            "activation": "hit_test",
            "descendant_label": "测试联系人",
            "fingerprint": "copied",
        }
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
                            "role": "AXGroup",
                            "label": "测试联系人",
                            "semantic_path": "会话列表 > 测试联系人",
                            "supports": ["show_menu", "hit_test"],
                            "activation": "hit_test",
                            "ax_ref": ax_ref,
                        }
                    ],
                    "search": {
                        "query": "测试联系人 会话列表",
                        "collection_label": "会话列表",
                        "collection_item_total_count": 8,
                        "collection_item_returned_count": 8,
                        "collection_complete": True,
                        "sufficient": True,
                    },
                }
            },
            "打开测试联系人的会话",
        )

        affordance = context["affordances"][0]
        self.assertEqual(affordance["supports"], ["click"])
        self.assertEqual(affordance["activation"], "verified_geometry")
        self.assertEqual(affordance["ax_ref"], ax_ref)
        self.assertIn("unique AX collection item geometry", affordance["evidence"])
        self.assertEqual(context["ax_search"]["collection_label"], "会话列表")
        self.assertTrue(context["ax_search"]["collection_complete"])

    def test_read_only_collection_labels_reach_brain_context_without_ax_refs(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "已通过 AX 读取专辑列表",
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
                            "label": "专辑",
                            "context_relation": "collection_anchor",
                        },
                        {
                            "label": "Album A",
                            "context_relation": "collection_item",
                        },
                        {
                            "label": "Album B",
                            "context_relation": "collection_item",
                        },
                    ],
                    "search": {
                        "query": "列出所有专辑",
                        "current_view_title": "专辑",
                        "collection_label": "专辑",
                        "collection_item_total_count": 2,
                        "collection_item_returned_count": 2,
                        "collection_complete": True,
                        "sufficient": True,
                    },
                }
            },
            "读取专辑名称",
        )

        self.assertEqual(context["affordances"], [])
        self.assertEqual(
            context["ax_search"]["collection_items"],
            ["Album A", "Album B"],
        )
        self.assertEqual(
            context["ax_search"]["current_view_title"],
            "专辑",
        )
        context_text = context_helpers._computer_use_context_text(context)
        self.assertIn("Album A", context_text)
        self.assertIn("Album B", context_text)

    def test_playlist_noun_does_not_enable_music_playback_affordance(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "AX 搜索成功",
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
                            "role": "AXGroup",
                            "label": "ONE OF US, Afterglow",
                            "supports": ["press"],
                            "activation_effect": "media_playback",
                            "ax_ref": {"fingerprint": "album"},
                        }
                    ],
                    "search": {
                        "query": "查看播放列表里的 ONE OF US, Afterglow 专辑",
                        "sufficient": True,
                    },
                }
            },
            "查看播放列表里的 ONE OF US, Afterglow 专辑",
        )

        self.assertEqual(context["affordances"], [])
        for query in (
            "查看播放列表",
            "查看播放清单",
            "查看播放队列",
            "view play queue",
        ):
            with self.subTest(query=query):
                self.assertFalse(
                    context_helpers._media_playback_requested(query)
                )
        self.assertTrue(
            context_helpers._media_playback_requested("播放这张专辑")
        )

    def test_unique_compact_qq_search_editor_is_not_chat_input(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "AX 搜索成功",
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
                            "role": "AXMenuItem",
                            "label": "搜索",
                            "supports": ["press"],
                            "ax_ref": {"fingerprint": "hidden-menu"},
                        },
                        {
                            "role": "AXTextField",
                            "supports": ["focus", "type_text"],
                            "bounds": {
                                "x": 151,
                                "y": 127,
                                "width": 162,
                                "height": 20,
                            },
                            "ax_ref": {"fingerprint": "qq-search"},
                        },
                    ],
                    "search": {
                        "query": "搜索",
                        "sufficient": True,
                        "actionable_match_count": 1,
                        "visible_match_count": 1,
                    },
                }
            },
            "搜索联系人",
        )

        self.assertEqual(len(context["affordances"]), 1)
        self.assertEqual(context["affordances"][0]["kind"], "search_field")
        self.assertEqual(context["affordances"][0]["label"], "搜索输入框")
        self.assertNotIn("chat_context", context)

    def test_compact_qq_text_area_uses_search_intent_not_chat_role_default(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "AX 搜索成功",
            {
                "accessibility": {
                    "usable": True,
                    "app": {"app_id": "qq", "name": "QQ", "surface": "qq_gui"},
                    "elements": [
                        {
                            "role": "AXTextArea",
                            "supports": ["focus", "type_text"],
                            "bounds": {
                                "x": 151,
                                "y": 127,
                                "width": 162,
                                "height": 20,
                            },
                            "ax_ref": {"fingerprint": "qq-search-text-area"},
                        }
                    ],
                    "search": {
                        "query": "搜索",
                        "sufficient": True,
                        "actionable_match_count": 1,
                        "visible_match_count": 1,
                    },
                }
            },
            "搜索联系人",
        )

        self.assertEqual(context["affordances"][0]["kind"], "search_field")
        self.assertNotIn("chat_context", context)

    def test_compact_qq_editor_uses_target_hint_when_free_query_omits_search_word(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "AX 搜索成功",
            {
                "accessibility": {
                    "usable": True,
                    "app": {"app_id": "qq", "name": "QQ", "surface": "qq_gui"},
                    "elements": [
                        {
                            "role": "AXTextField",
                            "supports": ["focus", "type_text"],
                            "bounds": {
                                "x": 151,
                                "y": 127,
                                "width": 162,
                                "height": 20,
                            },
                            "ax_ref": {"fingerprint": "qq-free-query-search"},
                        }
                    ],
                    "search": {
                        "query": "联系人 Alice",
                        "sufficient": True,
                        "actionable_match_count": 1,
                        "visible_match_count": 1,
                    },
                }
            },
            "搜索联系人",
        )

        self.assertEqual(context["affordances"][0]["kind"], "search_field")
        self.assertNotIn("chat_context", context)

    def test_search_intent_does_not_reclassify_explicit_chat_editor(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "AX 搜索成功",
            {
                "accessibility": {
                    "usable": True,
                    "app": {"app_id": "qq", "name": "QQ", "surface": "qq_gui"},
                    "elements": [
                        {
                            "role": "AXTextArea",
                            "label": "消息输入框",
                            "identifier": "message-input",
                            "focused": True,
                            "supports": ["focus", "type_text"],
                            "bounds": {
                                "x": 500,
                                "y": 600,
                                "width": 320,
                                "height": 60,
                            },
                            "ax_ref": {"fingerprint": "qq-message-input"},
                        }
                    ],
                    "search": {
                        "query": "搜索当前聊天内容",
                        "sufficient": True,
                        "actionable_match_count": 1,
                        "visible_match_count": 1,
                    },
                }
            },
            "搜索当前聊天内容",
        )

        self.assertEqual(context["affordances"][0]["kind"], "chat_input")
        self.assertTrue(context["chat_context"]["input_ready"])
        self.assertTrue(context["chat_context"]["input_focused"])

    def test_native_show_menu_is_a_reviewable_click_affordance(self) -> None:
        ax_ref = {
            "app_id": "music",
            "role": "AXGroup",
            "path": [1, 0, 0],
            "activation": "show_menu",
            "fingerprint": "menu-ref",
        }
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
                            "role": "AXGroup",
                            "label": "测试专辑",
                            "supports": ["press", "show_menu"],
                            "activation": "show_menu",
                            "activation_effect": "context_menu",
                            "ax_ref": ax_ref,
                        }
                    ],
                    "search": {
                        "query": "显示测试专辑的上下文菜单",
                        "sufficient": True,
                    },
                }
            },
            "显示测试专辑的上下文菜单",
        )

        affordance = context["affordances"][0]
        self.assertEqual(affordance["supports"], ["click"])
        self.assertEqual(affordance["activation"], "show_menu")
        self.assertEqual(affordance["ax_ref"], ax_ref)
        self.assertIn("native AXShowMenu action", affordance["evidence"])

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

    def test_insufficient_ax_search_withholds_all_executable_affordances(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "AX 不能独立验证当前会话",
            {
                "accessibility": {
                    "usable": True,
                    "app": {"app_id": "qq", "name": "QQ", "surface": "qq_gui"},
                    "elements": [
                        {
                            "role": "AXTextArea",
                            "label": "消息输入框",
                            "supports": ["focus", "type_text"],
                            "focused": True,
                            "ax_ref": {"fingerprint": "chat-input"},
                        }
                    ],
                    "search": {
                        "query": "验证当前会话标题和输入框",
                        "sufficient": False,
                        "insufficiency_reason": "active_chat_identity_unverified",
                    },
                }
            },
            "验证当前会话",
        )

        self.assertEqual(context["affordances"], [])
        self.assertNotIn("chat_context", context)
        self.assertTrue(context["ax_search"]["executable_refs_withheld"])

    def test_stale_ax_search_never_exposes_executable_affordances(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "AX 缓存已过期",
            {
                "accessibility": {
                    "usable": True,
                    "app": {
                        "app_id": "finder",
                        "name": "访达",
                        "surface": "finder_gui",
                    },
                    "elements": [
                        {
                            "role": "AXRow",
                            "label": "Downloads",
                            "supports": ["select", "open"],
                            "ax_ref": {"fingerprint": "old-file-row"},
                        }
                    ],
                    "search": {
                        "query": "打开 Downloads",
                        "sufficient": True,
                        "stale": True,
                        "stale_reason": "snapshot_expired",
                    },
                }
            },
            "打开 Downloads",
        )

        self.assertEqual(context["affordances"], [])
        self.assertTrue(context["ax_search"]["stale"])
        self.assertTrue(context["ax_search"]["executable_refs_withheld"])

    def test_media_playback_affordance_requires_explicit_play_intent(self) -> None:
        element = {
            "role": "AXGroup",
            "label": "ONE OF US, Afterglow",
            "supports": ["press"],
            "activation_effect": "media_playback",
            "ax_ref": {"fingerprint": "album-card"},
        }

        open_context = context_helpers._infer_computer_use_context(
            "AX 搜索不足",
            {
                "accessibility": {
                    "usable": True,
                    "app": {"app_id": "music", "name": "音乐", "surface": "music_gui"},
                    "elements": [element],
                    "search": {
                        "query": "打开 ONE OF US 专辑详情",
                        "sufficient": False,
                        "insufficiency_reason": "action_intent_mismatch",
                    },
                }
            },
            "打开 ONE OF US 专辑详情",
        )
        play_context = context_helpers._infer_computer_use_context(
            "AX 搜索成功",
            {
                "accessibility": {
                    "usable": True,
                    "app": {"app_id": "music", "name": "音乐", "surface": "music_gui"},
                    "elements": [element],
                    "search": {
                        "query": "播放 ONE OF US 专辑",
                        "sufficient": True,
                    },
                }
            },
            "播放 ONE OF US 专辑",
        )

        self.assertEqual(open_context["affordances"], [])
        self.assertFalse(open_context["ax_search"]["sufficient"])
        self.assertEqual(play_context["affordances"][0]["supports"], ["click"])
        self.assertEqual(
            play_context["affordances"][0]["activation_effect"],
            "media_playback",
        )

    def test_music_track_info_effect_is_preserved_for_brain(self) -> None:
        context = context_helpers._infer_computer_use_context(
            "AX 搜索成功",
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
                            "role": "AXMenuItem",
                            "label": "显示简介",
                            "supports": ["press"],
                            "activation_effect": "track_info_dialog",
                            "ax_ref": {"fingerprint": "get-info"},
                        }
                    ],
                    "search": {
                        "query": "点击显示简介菜单项 AXMenuItem",
                        "sufficient": True,
                        "requested_roles": ["axmenuitem"],
                    },
                }
            },
            "显示所选歌曲的信息",
        )

        target = context["affordances"][0]
        self.assertEqual(target["activation_effect"], "track_info_dialog")
        self.assertTrue(
            any("not verified album details" in item for item in target["evidence"])
        )

    def test_finder_open_and_selection_keep_distinct_file_item_semantics(self) -> None:
        def context_for(element: dict[str, object], query: str) -> dict[str, object]:
            return context_helpers._infer_computer_use_context(
                "AX 搜索成功",
                {
                    "accessibility": {
                        "usable": True,
                        "app": {
                            "app_id": "finder",
                            "name": "访达",
                            "surface": "finder_gui",
                        },
                        "elements": [element],
                        "search": {"query": query, "sufficient": True},
                    }
                },
                query,
            )

        open_context = context_for(
            {
                "role": "AXTextField",
                "label": "Downloads",
                "url": "file:///Users/test/Downloads",
                "supports": ["open"],
                "activation": "open",
                "context_relation": "collection_item",
                "ax_ref": {"fingerprint": "finder-open"},
            },
            "打开 Downloads 文件夹",
        )
        select_context = context_for(
            {
                "role": "AXGroup",
                "label": "Downloads",
                "supports": ["hit_test"],
                "context_relation": "collection_item",
                "ax_ref": {"fingerprint": "finder-select"},
            },
            "选择 Downloads 文件夹",
        )

        open_item = open_context["affordances"][0]
        select_item = select_context["affordances"][0]
        self.assertEqual(open_item["kind"], "file_item")
        self.assertEqual(open_item["activation"], "open")
        self.assertIn("native AXOpen action", open_item["evidence"])
        self.assertEqual(select_item["kind"], "file_item")
        self.assertEqual(select_item["activation"], "verified_geometry")

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

    def test_computer_use_context_text_includes_route_learning_feedback(self) -> None:
        context_text = context_helpers._computer_use_context_text(
            {
                "route_decision": {
                    "selected": "brain_vision",
                    "reason": "screenshot_requires_brain_vision",
                    "outcome": "pending_model",
                },
                "route_history": [
                    {
                        "selected": "ax",
                        "reason": "no_semantic_match",
                        "outcome": "needs_narrowing",
                        "repeat_count": 2,
                    }
                ],
            }
        )

        self.assertIn('"route_decision"', context_text)
        self.assertIn('"route_history"', context_text)
        self.assertIn('"repeat_count": 2', context_text)

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
