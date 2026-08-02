from __future__ import annotations

import asyncio
import inspect
import unittest
from unittest import mock

from brain.decisions import BrainDecision
from backend import app as backend_app
from backend.human_ops_observe import HumanOpsObserveDependencies, perform_human_ops_observe
from backend.observe_result import build_local_ocr_search


def _deps(**overrides):
    async def send_desktop_command(_command_type, _payload=None, *, timeout_sec=8.0):
        raise AssertionError("send_desktop_command should not be called")

    defaults = {
        "send_desktop_command": send_desktop_command,
        "observe_target_hint_from_decision": lambda _decision, fallback="screen": fallback,
        "frame_with_observe_prompt": lambda frame, _decision, _user_text: dict(frame),
        "observe_coordinate_context_from_frame": lambda _frame: "coordinate context",
        "observe_model_analyzer_config": lambda _config: {"enabled": False},
        "infer_computer_use_context": lambda _text, _frame=None, _target_hint="": {
            "surface": {"kind": "unknown"},
            "affordances": [],
        },
        "enrich_observation_frame_with_model": lambda frame, _config: dict(frame),
        "observation_text_from_result": lambda result: str(result.get("frame", {}).get("observe_answer") or ""),
        "observe_decision_requests_click": lambda _decision: False,
        "observe_click_coordinate_status": lambda _text, *, require_coordinates=False: {},
        "goal_requests_click_coordinate_followup": lambda _decision: False,
        "click_coordinate_observe_failure_text": lambda text: text,
    }
    defaults.update(overrides)
    return HumanOpsObserveDependencies(**defaults)


class HumanOpsObserveSplitTests(unittest.TestCase):
    def test_local_ocr_search_returns_only_query_matching_screen_coordinates(self) -> None:
        result = build_local_ocr_search(
            {
                "display_layout": [
                    {
                        "x": 15,
                        "y": 125,
                        "width": 880,
                        "height": 640,
                    }
                ],
                "local_ocr": {
                    "items": [
                        {
                            "text": "目标会话",
                            "confidence": 0.91,
                            "box": {
                                "x": 0.1,
                                "y": 0.7,
                                "width": 0.2,
                                "height": 0.05,
                            },
                        },
                        {
                            "text": "不相关的私人文字",
                            "confidence": 0.99,
                            "box": {
                                "x": 0.5,
                                "y": 0.4,
                                "width": 0.2,
                                "height": 0.05,
                            },
                        },
                    ]
                },
            },
            "点击目标会话的会话入口",
        )

        self.assertTrue(result["sufficient"])
        self.assertTrue(result["actionable"])
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["matches"][0]["label"], "目标会话")
        self.assertEqual(
            result["matches"][0]["location"],
            {
                "x": 191,
                "y": 301,
                "coordinate_space": "macos_screen_points",
            },
        )
        self.assertNotIn("不相关", str(result))

    def test_local_ocr_search_with_duplicate_best_labels_is_not_actionable(self) -> None:
        result = build_local_ocr_search(
            {
                "display_layout": [
                    {"x": 0, "y": 0, "width": 800, "height": 600}
                ],
                "local_ocr": {
                    "items": [
                        {
                            "text": "目标会话",
                            "confidence": 0.9,
                            "box": {
                                "x": 0.1,
                                "y": 0.7,
                                "width": 0.2,
                                "height": 0.05,
                            },
                        },
                        {
                            "text": "目标会话",
                            "confidence": 0.88,
                            "box": {
                                "x": 0.5,
                                "y": 0.7,
                                "width": 0.2,
                                "height": 0.05,
                            },
                        },
                    ]
                },
            },
            "点击目标会话",
        )

        self.assertFalse(result["sufficient"])
        self.assertFalse(result["actionable"])
        self.assertEqual(result["best_match_count"], 2)
        self.assertTrue(
            all(not match.get("supports") for match in result["matches"])
        )
        self.assertTrue(
            all("location" not in match for match in result["matches"])
        )

    def test_local_ocr_search_rejects_instruction_words_and_weak_matches(self) -> None:
        instruction_only = build_local_ocr_search(
            {
                "display_layout": [
                    {"x": 0, "y": 0, "width": 800, "height": 600}
                ],
                "local_ocr": {
                    "items": [
                        {
                            "text": "打开",
                            "confidence": 0.99,
                            "box": {
                                "x": 0.1,
                                "y": 0.7,
                                "width": 0.2,
                                "height": 0.05,
                            },
                        }
                    ]
                },
            },
            "打开目标会话",
        )
        weak_target = build_local_ocr_search(
            {
                "display_layout": [
                    {"x": 0, "y": 0, "width": 800, "height": 600}
                ],
                "local_ocr": {
                    "items": [
                        {
                            "text": "目标会话",
                            "confidence": 0.2,
                            "box": {
                                "x": 0.1,
                                "y": 0.7,
                                "width": 0.2,
                                "height": 0.05,
                            },
                        }
                    ]
                },
            },
            "打开目标会话",
        )

        self.assertEqual(instruction_only["matched_count"], 0)
        self.assertFalse(instruction_only["actionable"])
        self.assertEqual(weak_target["matched_count"], 1)
        self.assertFalse(weak_target["sufficient"])
        self.assertFalse(weak_target["actionable"])
        self.assertNotIn("supports", weak_target["matches"][0])
        self.assertNotIn("location", weak_target["matches"][0])

    def test_module_does_not_import_backend_app(self) -> None:
        import backend.human_ops_observe as observe_module

        source = inspect.getsource(observe_module)
        self.assertNotIn("backend.app", source)
        self.assertNotIn("from . import app", source)

    def test_all_observation_permissions_disabled_returns_without_capture(self) -> None:
        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe("screen"),
                {"observe_screen": False, "accessibility": False},
                deps=_deps(),
            )
        )

        self.assertIn("均已关闭", result["text"])
        self.assertEqual(result["observations"], [])
        self.assertIn("screen capture and accessibility disabled", result["unknowns"])

    def test_screen_capture_disabled_still_allows_application_accessibility(self) -> None:
        send_mock = mock.AsyncMock(
            return_value={
                "frame": {
                    "capture_backend": "macos_accessibility",
                    "accessibility": {
                        "usable": True,
                        "text": "已读取目标应用结构",
                        "search": {"sufficient": True},
                    },
                    "observations": [],
                }
            }
        )

        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe("读取按钮", target_app="QQ", ax_query="AXButton"),
                {"observe_screen": False, "accessibility": True},
                deps=_deps(send_desktop_command=send_mock),
            )
        )

        payload = send_mock.await_args.args[1]
        self.assertFalse(payload["screen_capture_enabled"])
        self.assertTrue(payload["accessibility_enabled"])
        self.assertEqual(result["analysis_route"], "structured")
        self.assertEqual(result["route_decision"]["selected"], "ax")

    def test_screen_capture_disabled_requires_app_for_accessibility(self) -> None:
        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe("screen"),
                {"observe_screen": False, "accessibility": True},
                deps=_deps(),
            )
        )

        self.assertIn("需要指定一个明确的目标应用", result["text"])
        self.assertIn("accessibility observation requires target_app", result["unknowns"])

    def test_sends_active_vision_capture_with_target_hint_and_long_timeout(self) -> None:
        send_mock = mock.AsyncMock(
            return_value={
                "frame": {
                    "capture_backend": "macos_screencapture",
                    "observations": [{"claim": "captured", "source": "test"}],
                },
                "trace": {"capture": "ok"},
            }
        )

        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe("打开微信"),
                {"observe_screen": True, "observe_model": {"enabled": False}},
                deps=_deps(
                    send_desktop_command=send_mock,
                    observe_target_hint_from_decision=lambda _decision, fallback="screen": "Dock 微信",
                ),
            )
        )

        send_mock.assert_awaited_once_with(
            "active_vision_capture",
            {
                "mode": "desktop_survey",
                "target_hint": "Dock 微信",
                "target": "Dock 微信",
                "target_app": "",
                "capture_scope": "desktop",
                "accessibility_enabled": True,
            },
            timeout_sec=14,
        )
        self.assertEqual(result["trace"], {"capture": "ok"})

    def test_application_observe_carries_explicit_target_app(self) -> None:
        send_mock = mock.AsyncMock(return_value={"frame": {"capture_backend": "macos_screencapture"}})

        asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe("screen", target_app="Google Chrome"),
                {"observe_screen": True, "observe_model": {"enabled": False}},
                deps=_deps(send_desktop_command=send_mock),
            )
        )

        payload = send_mock.await_args.args[1]
        self.assertEqual(payload["target_app"], "Google Chrome")
        self.assertEqual(payload["capture_scope"], "application")

    def test_desktop_surface_name_is_not_treated_as_an_application(self) -> None:
        send_mock = mock.AsyncMock(
            return_value={"frame": {"capture_backend": "macos_screencapture"}}
        )

        asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe(
                    "列出当前前台应用及其可见窗口",
                    target_app="当前桌面",
                ),
                {"observe_screen": True, "observe_model": {"enabled": False}},
                deps=_deps(send_desktop_command=send_mock),
            )
        )

        payload = send_mock.await_args.args[1]
        self.assertEqual(payload["target_app"], "")
        self.assertEqual(payload["capture_scope"], "desktop")
        self.assertNotIn("accessibility_query", payload)

    def test_model_ax_query_drives_bounded_search_for_each_supported_app(self) -> None:
        send_mock = mock.AsyncMock(
            return_value={
                "frame": {
                    "capture_backend": "macos_accessibility",
                    "accessibility": {
                        "usable": True,
                        "text": "返回张三会话",
                        "elements": [],
                    },
                }
            }
        )

        cases = (
            ("QQ", "张三 AXRow"),
            ("微信", "文件传输助手 AXRow"),
            ("音乐", "播放 AXButton"),
            ("访达", "Downloads AXRow"),
        )
        for target_app, ax_query in cases:
            with self.subTest(target_app=target_app):
                asyncio.run(
                    perform_human_ops_observe(
                        BrainDecision.observe(
                            "screen",
                            target_app=target_app,
                            ax_query=ax_query,
                        ),
                        {"observe_screen": True, "observe_model": {"enabled": False}},
                        deps=_deps(send_desktop_command=send_mock),
                    )
                )

                payload = send_mock.await_args.args[1]
                self.assertEqual(payload["target_app"], target_app)
                self.assertEqual(payload["accessibility_query"], ax_query)
                self.assertEqual(payload["accessibility_cache_mode"], "prefer_cache")
                self.assertEqual(payload["accessibility_result_limit"], 16)
                self.assertEqual(payload["accessibility_timeout_sec"], 6.0)
                self.assertEqual(send_mock.await_args.kwargs["timeout_sec"], 18)

    def test_question_is_only_a_compatibility_fallback_for_ax_search(self) -> None:
        send_mock = mock.AsyncMock(return_value={"frame": {"capture_backend": "macos_screencapture"}})

        asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe(
                    "screen",
                    observe_prompt="找到张三联系人",
                    target_app="QQ",
                ),
                {"observe_screen": True, "observe_model": {"enabled": False}},
                deps=_deps(send_desktop_command=send_mock),
            )
        )

        payload = send_mock.await_args.args[1]
        self.assertEqual(payload["accessibility_query"], "找到张三联系人")
        self.assertEqual(payload["accessibility_cache_mode"], "refresh")

    def test_observe_model_disabled_routes_captured_image_to_brain(self) -> None:
        async def send_desktop_command(_command_type, _payload=None, *, timeout_sec=8.0):
            return {
                "frame": {
                    "capture_backend": "macos_screencapture",
                    "data_url": "data:image/png;base64,AA==",
                    "active_observation": {"target_hint": "点击 Dock 栏里的设置"},
                }
            }

        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe("点击 Dock 栏里的设置"),
                {"observe_screen": True, "observe_model": {"enabled": False}},
                deps=_deps(
                    send_desktop_command=send_desktop_command,
                    observe_target_hint_from_decision=lambda _decision, fallback="screen": "点击 Dock 栏里的设置",
                ),
            )
        )

        self.assertIn("由 Brain 模型直接观察", result["text"])
        self.assertEqual(result["analysis_route"], "brain")
        self.assertEqual(result["unknowns"], [])
        self.assertEqual(result["route_decision"]["selected"], "brain_vision")
        self.assertEqual(
            result["route_decision"]["attempted"],
            ["screenshot", "brain_vision"],
        )

    def test_structured_accessibility_observation_skips_image_analyzer(self) -> None:
        send_mock = mock.AsyncMock(
            return_value={
                "frame": {
                    "capture_backend": "macos_accessibility",
                    "accessibility": {
                        "usable": True,
                        "text": "AX 读取成功",
                        "elements": [],
                    },
                    "observe_answer": "AX 读取成功",
                    "observations": [{"claim": "发送按钮可用", "source": "macos_accessibility"}],
                },
                "trace": {"status": "success"},
            }
        )
        analyzer = mock.Mock(side_effect=AssertionError("image analyzer should not run"))

        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe("screen", target_app="WeChat"),
                {"observe_screen": True, "observe_model": {"enabled": True}},
                deps=_deps(
                    send_desktop_command=send_mock,
                    observe_model_analyzer_config=lambda _config: {"enabled": True},
                    enrich_observation_frame_with_model=analyzer,
                    observation_text_from_result=lambda _result: "AX 读取成功",
                    infer_computer_use_context=lambda _text, _frame, _target: {
                        "surface": {"kind": "wechat_gui"},
                        "affordances": [{"kind": "button", "ax_ref": {"fingerprint": "abc"}}],
                    },
                ),
            )
        )

        self.assertEqual(result["analysis_route"], "structured")
        self.assertEqual(result["surface"]["kind"], "wechat_gui")
        self.assertEqual(result["text"], "AX 读取成功")
        self.assertEqual(result["route_decision"]["selected"], "ax")
        self.assertEqual(result["route_decision"]["outcome"], "usable")
        analyzer.assert_not_called()

    def test_insufficient_ax_search_keeps_structure_and_falls_back_to_screenshot(self) -> None:
        send_mock = mock.AsyncMock(
            side_effect=[
                {
                    "frame": {
                        "capture_backend": "macos_accessibility",
                        "accessibility": {
                            "usable": True,
                            "snapshot_id": "axs-music",
                            "text": "没有找到足够相关的 AX 结果",
                            "elements": [],
                            "search": {
                                "query": "读取专辑名称",
                                "matched_count": 0,
                                "sufficient": False,
                                "insufficiency_reason": "no_semantic_match",
                            },
                        },
                    },
                    "trace": {"ax": "insufficient"},
                },
                {
                    "frame": {
                        "capture_backend": "macos_screencapture",
                        "data_url": "data:image/png;base64,AA==",
                    },
                    "trace": {"capture": "ok"},
                },
            ]
        )

        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe(
                    "screen",
                    target_app="Music",
                    ax_query="读取专辑名称",
                ),
                {"observe_screen": True, "observe_model": {"enabled": False}},
                deps=_deps(send_desktop_command=send_mock),
            )
        )

        self.assertEqual(send_mock.await_count, 2)
        self.assertTrue(send_mock.await_args_list[0].args[1]["accessibility_enabled"])
        self.assertFalse(send_mock.await_args_list[1].args[1]["accessibility_enabled"])
        self.assertEqual(result["analysis_route"], "brain")
        self.assertEqual(result["frame"]["accessibility"]["snapshot_id"], "axs-music")
        self.assertEqual(
            result["frame"]["accessibility_fallback"]["reason"],
            "no_semantic_match",
        )

    def test_unique_local_ocr_match_becomes_structured_without_brain_image(self) -> None:
        send_mock = mock.AsyncMock(
            return_value={
                "frame": {
                    "capture_backend": "macos_screencapture",
                    "data_url": "data:image/png;base64,AA==",
                    "display_layout": [
                        {"x": 15, "y": 125, "width": 880, "height": 640}
                    ],
                    "focus_verification": {
                        "focused": True,
                        "surface_visible": True,
                        "target_app": "微信",
                    },
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
                            "visual_fallback_required": True,
                            "insufficiency_reason": "no_semantic_match",
                        },
                    },
                }
            }
        )
        local_calls: list[str] = []

        def enrich_local(frame: dict, query: str) -> dict:
            local_calls.append(query)
            return {
                **frame,
                "local_ocr_search": {
                    "available": True,
                    "query": query,
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
                                "coordinate_space": "macos_screen_points",
                            },
                        }
                    ],
                },
            }

        def infer_context(_text, frame, _target):
            match = frame["local_ocr_search"]["matches"][0]
            return {
                "surface": {
                    "kind": "wechat_gui",
                    "app": "微信",
                    "app_id": "wechat",
                },
                "affordances": [match],
                "ax_search": {
                    "sufficient": False,
                    "insufficiency_reason": "no_semantic_match",
                },
                "visual_search": {
                    "sufficient": True,
                    "matched_count": 1,
                },
            }

        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe(
                    "screen",
                    target_app="微信",
                    ax_query="点击目标会话",
                ),
                {
                    "observe_screen": True,
                    "accessibility": True,
                    "observe_model": {"enabled": False},
                },
                deps=_deps(
                    send_desktop_command=send_mock,
                    enrich_observation_frame_with_local_ocr=enrich_local,
                    infer_computer_use_context=infer_context,
                ),
            )
        )

        self.assertEqual(local_calls, ["点击目标会话"])
        self.assertEqual(result["analysis_route"], "structured")
        self.assertEqual(result["visual_search"]["matched_count"], 1)
        self.assertEqual(result["affordances"][0]["location"]["x"], 191)
        self.assertTrue(
            str(result["frame"].get("data_url") or "").startswith(
                "data:image/"
            )
        )
        self.assertEqual(result["route_decision"]["selected"], "local_ocr")
        self.assertEqual(result["route_decision"]["outcome"], "usable")

    def test_non_actionable_local_ocr_does_not_short_circuit_click_vision(self) -> None:
        send_mock = mock.AsyncMock(
            return_value={
                "frame": {
                    "capture_backend": "macos_screencapture",
                    "data_url": "data:image/png;base64,AA==",
                    "accessibility": {
                        "usable": True,
                        "search": {
                            "sufficient": False,
                            "visual_fallback_required": True,
                            "insufficiency_reason": "no_reviewable_actionable_match",
                        },
                    },
                }
            }
        )

        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe(
                    "screen",
                    target_app="Music",
                    ax_query="当前歌曲名称",
                ),
                {"observe_screen": True, "observe_model": {"enabled": False}},
                deps=_deps(
                    send_desktop_command=send_mock,
                    observe_decision_requests_click=lambda _decision: True,
                    enrich_observation_frame_with_local_ocr=lambda frame, _query: {
                        **frame,
                        "local_ocr_analysis": {
                            "provider": "macos_vision_ocr",
                            "status": "ok",
                        },
                        "local_ocr_search": {
                            "available": True,
                            "matched_count": 1,
                            "best_match_count": 1,
                            "sufficient": True,
                            "actionable": False,
                            "matches": [{"label": "当前歌曲"}],
                        },
                    },
                ),
            )
        )

        self.assertEqual(result["analysis_route"], "brain")
        self.assertEqual(result["route_decision"]["selected"], "brain_vision")
        self.assertEqual(
            result["route_decision"]["attempted"],
            ["ax", "screenshot", "local_ocr", "brain_vision"],
        )
        self.assertFalse(result["route_decision"]["ocr_actionable"])

    def test_near_match_ambiguity_stays_structured_without_screenshot(self) -> None:
        send_mock = mock.AsyncMock(
            return_value={
                "frame": {
                    "capture_backend": "macos_accessibility",
                    "accessibility": {
                        "usable": True,
                        "snapshot_id": "axs-qq",
                        "text": "找到近似可见名称：测试联系人；必须由用户确认。",
                        "elements": [],
                        "search": {
                            "query": "测试联系入 会话列表",
                            "sufficient": False,
                            "insufficiency_reason": "near_match_requires_confirmation",
                            "near_match_labels": ["测试联系人"],
                            "visual_fallback_required": False,
                        },
                    },
                },
                "trace": {"ax": "ambiguous"},
            }
        )

        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe(
                    "screen",
                    target_app="QQ",
                    ax_query="测试联系入 会话列表",
                ),
                {"observe_screen": True, "observe_model": {"enabled": False}},
                deps=_deps(send_desktop_command=send_mock),
            )
        )

        self.assertEqual(send_mock.await_count, 1)
        self.assertEqual(result["analysis_route"], "structured")
        self.assertNotIn("accessibility_fallback", result["frame"])
        self.assertEqual(
            result["frame"]["accessibility"]["search"]["insufficiency_reason"],
            "near_match_requires_confirmation",
        )

    def test_verified_focus_failure_does_not_start_a_second_visual_command(self) -> None:
        send_mock = mock.AsyncMock(
            return_value={
                "frame": {
                    "capture_backend": "macos_accessibility",
                    "focus_verification": {
                        "focused": False,
                        "surface_visible": False,
                        "error": "target window is not on the current Space",
                    },
                    "accessibility": {
                        "usable": True,
                        "elements": [],
                        "search": {
                            "sufficient": False,
                            "insufficiency_reason": "requested_role_not_found",
                        },
                    },
                },
                "trace": {"status": "partial"},
            }
        )

        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe(
                    "screen",
                    target_app="QQ",
                    ax_query="conversation input",
                ),
                {
                    "observe_screen": True,
                    "observe_model": {"enabled": False},
                },
                deps=_deps(send_desktop_command=send_mock),
            )
        )

        self.assertEqual(send_mock.await_count, 1)
        self.assertEqual(
            result["frame"]["focus_verification"]["surface_visible"],
            False,
        )

    def test_observe_model_disabled_reports_missing_capture_data(self) -> None:
        async def send_desktop_command(_command_type, _payload=None, *, timeout_sec=8.0):
            return {"frame": {"capture_backend": "macos_screencapture"}}

        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe("screen"),
                {"observe_screen": True, "observe_model": {"enabled": False}},
                deps=_deps(send_desktop_command=send_desktop_command),
            )
        )

        self.assertEqual(result["analysis_route"], "unavailable")
        self.assertIn("截图数据不可用", result["text"])
        self.assertIn("captured image unavailable", result["unknowns"])

    def test_enabled_observe_marks_incomplete_coordinate_unknowns(self) -> None:
        async def send_desktop_command(_command_type, _payload=None, *, timeout_sec=8.0):
            return {"frame": {"unknowns": ["preexisting"], "observations": [{"claim": "微信可见", "source": "fake"}]}}

        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe(
                    "screen",
                    goal={
                        "objective": "打开微信",
                        "status": "in_progress",
                        "missing": "微信图标的中心点坐标",
                    },
                ),
                {"observe_screen": True, "observe_model": {"enabled": True}},
                deps=_deps(
                    send_desktop_command=send_desktop_command,
                    observe_model_analyzer_config=lambda _config: {"enabled": True},
                    enrich_observation_frame_with_model=lambda frame, _config: {
                        **frame,
                        "observe_answer": "Dock 中可见微信图标，但没有完整坐标。",
                    },
                    observe_decision_requests_click=lambda _decision: True,
                    observe_click_coordinate_status=lambda _text, *, require_coordinates=False: {
                        "status": "incomplete",
                        "reason": "missing x/y",
                    },
                    goal_requests_click_coordinate_followup=lambda _decision: True,
                    click_coordinate_observe_failure_text=lambda text: f"{text}\n缺少完整 x 和 y。",
                ),
            )
        )

        self.assertEqual(result["coordinate_status"]["status"], "incomplete")
        self.assertIn("observe_coordinate_incomplete", result["unknowns"])
        self.assertIn("preexisting", result["unknowns"])
        self.assertIn("缺少完整 x 和 y", result["text"])

    def test_app_wrapper_uses_patched_app_level_send_desktop_command(self) -> None:
        send_mock = mock.AsyncMock(
            return_value={
                "frame": {
                    "capture_backend": "macos_screencapture",
                    "observations": [],
                }
            }
        )

        with mock.patch.object(backend_app, "_send_desktop_command", new=send_mock):
            result = asyncio.run(
                backend_app._perform_human_ops_observe(
                    BrainDecision.observe("打开微信"),
                    {"observe_screen": True, "observe_model": {"enabled": False}},
                )
            )

        send_mock.assert_awaited_once()
        self.assertEqual(send_mock.await_args.args[0], "active_vision_capture")
        self.assertEqual(send_mock.await_args.kwargs["timeout_sec"], 14)
        self.assertIn("captured image unavailable", result["unknowns"])


if __name__ == "__main__":
    unittest.main()
