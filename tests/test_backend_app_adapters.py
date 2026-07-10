from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from brain.decisions import BrainDecision
from backend import app as backend_app


class BackendAppAdaptersSplitTests(unittest.TestCase):
    def test_backend_app_reexports_adapter_compatibility_names(self) -> None:
        from backend import app_adapters

        expected_names = (
            "_looks_like_desktop_observe_request",
            "_looks_like_desktop_action_request",
            "_looks_like_click_request",
            "_default_observe_prompt_for_request",
            "_observe_prompt_from_decision",
            "_frame_with_observe_prompt",
            "_infer_computer_use_context",
            "_computer_use_context_text",
            "_observation_text_from_result",
            "_observe_model_analyzer_config",
            "_perform_human_ops_observe",
        )

        for name in expected_names:
            with self.subTest(name=name):
                self.assertIs(getattr(backend_app, name), getattr(app_adapters, name))

    def test_computer_use_intent_adapter_delegates_to_context_module(self) -> None:
        from backend import app_adapters
        from backend import computer_use_context

        with mock.patch.object(
            computer_use_context,
            "_looks_like_desktop_action_request",
            return_value=True,
        ) as delegated:
            self.assertTrue(app_adapters._looks_like_desktop_action_request("打开微信"))

        delegated.assert_called_once_with("打开微信")

    def test_observe_prompt_and_result_adapters_keep_representative_behavior(self) -> None:
        from backend import app_adapters

        decision = BrainDecision.observe(
            "screen",
            goal={
                "objective": "打开微信",
                "status": "in_progress",
                "missing": "微信图标中心点坐标",
            },
        )
        prompt = app_adapters._observe_prompt_from_decision(decision, "打开微信")
        frame = app_adapters._frame_with_observe_prompt(
            {"image_width": 1000, "image_height": 800, "display_layout": [{"x": 0, "y": 0, "width": 500, "height": 400}]},
            decision,
            "screen",
        )
        text = app_adapters._observation_text_from_result({"frame": {"observe_answer": "Dock 中可见微信图标。"}})

        self.assertIn("微信图标", prompt)
        self.assertEqual(frame["active_observation"]["target_hint"], "打开微信")
        self.assertEqual(text, "Dock 中可见微信图标。")

    def test_human_ops_observe_adapter_uses_patched_backend_app_send_command(self) -> None:
        from backend import app_adapters

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
                app_adapters._perform_human_ops_observe(
                    BrainDecision.observe("打开微信"),
                    {"observe_screen": True, "observe_model": {"enabled": False}},
                )
            )

        send_mock.assert_awaited_once()
        self.assertEqual(send_mock.await_args.args[0], "active_vision_capture")
        self.assertEqual(send_mock.await_args.kwargs["timeout_sec"], 14)
        self.assertIn("observe_model disabled", result["unknowns"])


if __name__ == "__main__":
    unittest.main()
