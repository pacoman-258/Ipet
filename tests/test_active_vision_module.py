from __future__ import annotations

import importlib
import unittest
from unittest import mock


def _active_vision_module():
    try:
        return importlib.import_module("body.active_vision")
    except ImportError as exc:
        raise AssertionError("body.active_vision module should be importable") from exc


def _active_vision_targets_module():
    try:
        return importlib.import_module("body.active_vision_targets")
    except ImportError as exc:
        raise AssertionError("body.active_vision_targets module should be importable") from exc


class ActiveVisionModuleTests(unittest.TestCase):
    def test_target_candidate_helpers_are_pure_and_cap_boosted_candidates(self) -> None:
        active_targets = _active_vision_targets_module()
        candidates = [
            {"target_id": f"candidate-{index}", "source": "running_app", "app": f"App {index}", "score": 70}
            for index in range(active_targets.ACTIVE_VISION_MAX_CANDIDATES + 2)
        ]
        candidates.append({"target_id": "candidate-wechat", "source": "dock_item", "app": "微信", "score": 50})

        boosted = active_targets.boost_candidates_for_target_hint(candidates, "打开微信回复消息")
        merged = active_targets.merge_active_target_candidates(boosted, [{"target_id": "candidate-1", "app": "Duplicate"}])

        self.assertLessEqual(len(merged), active_targets.ACTIVE_VISION_MAX_CANDIDATES)
        self.assertEqual(merged[0]["target_id"], "candidate-wechat")
        self.assertEqual(merged[0]["reason"], "target_hint_match")
        self.assertEqual(merged[0]["score"], 96.0)
        self.assertEqual(sum(1 for item in merged if item["target_id"] == "candidate-1"), 1)

    def test_screenshot_candidate_uses_display_union_without_focus(self) -> None:
        active_targets = _active_vision_targets_module()

        candidate = active_targets.active_screenshot_candidate(
            [
                {"x": -1280, "y": 0, "width": 1280, "height": 720},
                {"x": 0, "y": -100, "width": 1920, "height": 1080},
            ]
        )

        self.assertEqual(candidate["target_id"], "screenshot:full_desktop")
        self.assertEqual(candidate["source"], "screenshot_region")
        self.assertEqual(candidate["bounds"], {"x": -1280, "y": -100, "width": 3200, "height": 1080})
        self.assertFalse(candidate["focusable"])

    def test_target_id_and_focus_point_helpers_are_pure_and_delegated(self) -> None:
        active_targets = _active_vision_targets_module()
        active_vision = _active_vision_module()
        bounds = {"x": 10, "y": 30, "width": 900, "height": 600}

        self.assertEqual(active_targets.safe_focus_point(bounds), {"x": 190, "y": 42})
        self.assertEqual(active_vision._safe_focus_point(bounds), active_targets.safe_focus_point(bounds))
        self.assertEqual(
            active_vision._active_target_id("Safari", "Docs", bounds, 2),
            active_targets.active_target_id("Safari", "Docs", bounds, 2),
        )

    def test_active_vision_private_wrappers_delegate_to_target_module(self) -> None:
        active_vision = _active_vision_module()
        sentinel = {"target_id": "chosen"}

        with mock.patch.object(active_vision._active_targets, "find_active_target", return_value=sentinel) as find_target:
            result = active_vision._find_active_target([{"target_id": "window"}], [{"target_id": "candidate"}], "candidate")

        self.assertEqual(result, sentinel)
        find_target.assert_called_once_with([{"target_id": "window"}], [{"target_id": "candidate"}], "candidate")

    def test_discovery_boosts_matching_dock_item_past_candidate_cap(self) -> None:
        active_vision = _active_vision_module()
        dock_items = [
            {"target_id": f"dock:other-{index}", "source": "dock_item", "app": f"App {index}", "score": 88}
            for index in range(active_vision.ACTIVE_VISION_MAX_CANDIDATES + 3)
        ]
        dock_items.append(
            {
                "target_id": "dock:wechat",
                "source": "dock_item",
                "app": "微信",
                "focus_point": {"x": 963, "y": 908},
                "score": 88,
            }
        )

        discovery = active_vision.discover_active_vision_target_candidates(
            desktop_targets_provider=lambda **kwargs: [],
            dock_items_provider=lambda **kwargs: dock_items,
            running_apps_provider=lambda **kwargs: [],
            target_hint="打开微信并根据张三聊天信息回复张三",
            platform_name="darwin",
        )

        self.assertLessEqual(len(discovery["target_candidates"]), active_vision.ACTIVE_VISION_MAX_CANDIDATES)
        self.assertEqual(discovery["target_candidates"][0]["app"], "微信")
        self.assertEqual(discovery["target_candidates"][0]["source"], "dock_item")
        self.assertEqual(discovery["target_candidates"][0]["reason"], "target_hint_match")

    def test_focus_interaction_uses_injected_runner_and_blocks_non_focus_actions(self) -> None:
        active_vision = _active_vision_module()
        scripts: list[str] = []

        def runner(argv, **kwargs):
            scripts.append(" ".join(str(part) for part in argv))
            completed = mock.Mock()
            completed.returncode = 0
            completed.stdout = "accessibility_raise=success\nwindow_focus_click=success\n"
            completed.stderr = ""
            return completed

        trace = active_vision.run_active_vision_light_interaction(
            mode="focus_target",
            target_id="target-safari",
            desktop_targets=[
                {
                    "target_id": "target-safari",
                    "app": "Safari",
                    "title": "Video",
                    "bounds": {"x": 10, "y": 20, "width": 900, "height": 600},
                    "focus_point": {"x": 120, "y": 32},
                }
            ],
            actions=["focus_target", "type_text", "delete_file"],
            platform_name="darwin",
            runner=runner,
        )

        self.assertIn("System Events", "\n".join(scripts))
        self.assertEqual(trace["actions"], ["focus_target"])
        self.assertEqual(trace["click_policy"], "window_focus_only")
        self.assertEqual(trace["focused_target"]["target_id"], "target-safari")
        self.assertEqual(trace["click_point"], {"x": 120, "y": 32})
        self.assertIn("type_text", trace["blocked_actions"])
        self.assertIn("delete_file", trace["blocked_actions"])


if __name__ == "__main__":
    unittest.main()
