from __future__ import annotations

import asyncio
import inspect
import unittest
from unittest import mock

from brain.decisions import BrainDecision
from backend import app as backend_app
from backend.human_ops_observe import HumanOpsObserveDependencies, perform_human_ops_observe


def _deps(**overrides):
    async def send_desktop_command(_command_type, _payload=None, *, timeout_sec=8.0):
        raise AssertionError("send_desktop_command should not be called")

    defaults = {
        "send_desktop_command": send_desktop_command,
        "observe_target_hint_from_decision": lambda _decision, fallback="screen": fallback,
        "frame_with_observe_prompt": lambda frame, _decision, _user_text: dict(frame),
        "observe_coordinate_context_from_frame": lambda _frame: "coordinate context",
        "observe_model_analyzer_config": lambda _config: {"enabled": False},
        "looks_like_click_request": lambda _text: False,
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
    def test_module_does_not_import_backend_app(self) -> None:
        import backend.human_ops_observe as observe_module

        source = inspect.getsource(observe_module)
        self.assertNotIn("backend.app", source)
        self.assertNotIn("from . import app", source)

    def test_observe_screen_disabled_returns_permission_message_without_capture(self) -> None:
        result = asyncio.run(
            perform_human_ops_observe(
                BrainDecision.observe("screen"),
                {"observe_screen": False},
                deps=_deps(),
            )
        )

        self.assertIn("观察权限", result["text"])
        self.assertEqual(result["observations"], [])
        self.assertIn("observe_screen disabled", result["unknowns"])

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
                self.assertEqual(payload["accessibility_result_limit"], 10)

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
                    looks_like_click_request=lambda text: "点击" in text,
                ),
            )
        )

        self.assertIn("由 Brain 模型直接观察", result["text"])
        self.assertEqual(result["analysis_route"], "brain")
        self.assertEqual(result["unknowns"], [])

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
