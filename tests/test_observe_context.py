from __future__ import annotations

import unittest

from brain import BrainDecision
from backend import observe_context


class ObserveContextTests(unittest.TestCase):
    def test_coordinate_context_includes_image_screen_and_scale(self) -> None:
        frame = {
            "image_width": 1920,
            "image_height": 1249,
            "display_layout": [{"x": 0, "y": 0, "width": 1470, "height": 956}],
        }

        context = observe_context.observe_coordinate_context_from_frame(frame)

        self.assertIn("Attached image pixels: 1920x1249", context)
        self.assertIn("macOS screen bounds: origin=(0, 0), size=1470x956 points", context)
        self.assertIn("image-to-screen scale: x=0.765625, y=0.765412", context)
        self.assertIn("screen-to-image scale: x=1.306122, y=1.306485", context)

    def test_goal_missing_coordinates_narrows_observe_prompt(self) -> None:
        decision = BrainDecision.observe(
            "screen",
            goal={
                "objective": "打开微信",
                "status": "in_progress",
                "stage": "launch_app",
                "missing": "微信图标中心点坐标",
                "next": "click_to_open_wechat",
            },
        )

        prompt = observe_context.observe_prompt_from_decision(decision, "打开微信")

        self.assertIn("请只定位", prompt)
        self.assertIn("微信图标", prompt)
        self.assertIn("x 和 y", prompt)
        self.assertIn("不要描述其他内容", prompt)

    def test_frame_with_observe_prompt_preserves_goal_objective_for_screen_target(self) -> None:
        decision = BrainDecision.observe(
            "screen",
            goal={
                "objective": "打开微信并回复张三",
                "status": "in_progress",
                "stage": "launch_app",
                "missing": "微信图标的坐标",
                "next": "click_to_open_wechat",
            },
        )
        frame = {
            "image_width": 1920,
            "image_height": 1249,
            "display_layout": [{"x": 0, "y": 0, "width": 1470, "height": 956}],
            "active_observation": {"target_hint": "screen"},
        }

        enriched = observe_context.frame_with_observe_prompt(frame, decision, "screen")

        active = enriched["active_observation"]
        self.assertEqual(active["target_hint"], "打开微信并回复张三")
        self.assertIn("微信图标", active["observe_prompt"])
        self.assertEqual(active["coordinate_space"], "macos_screen_points")

    def test_observe_model_analyzer_config_clamps_and_defaults_timeout(self) -> None:
        defaulted = observe_context.observe_model_analyzer_config(
            {"observe_model": {"enabled": True, "provider": "openai_compatible"}}
        )
        low = observe_context.observe_model_analyzer_config(
            {"observe_model": {"enabled": True, "provider": "openai_compatible", "timeout_sec": 1}}
        )
        high = observe_context.observe_model_analyzer_config(
            {"observe_model": {"enabled": True, "provider": "openai_compatible", "timeout_sec": 999}}
        )
        invalid = observe_context.observe_model_analyzer_config(
            {"observe_model": {"enabled": True, "provider": "openai_compatible", "timeout_sec": "slow"}}
        )

        self.assertEqual(defaulted["timeout_sec"], 90.0)
        self.assertEqual(low["timeout_sec"], 5.0)
        self.assertEqual(high["timeout_sec"], 300.0)
        self.assertEqual(invalid["timeout_sec"], 90.0)


if __name__ == "__main__":
    unittest.main()
