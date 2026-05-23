from __future__ import annotations

import unittest

from backend.active_vision import (
    build_active_observe_metadata,
    decide_active_observation,
    normalize_active_observation_config,
)


class ActiveVisionDecisionTests(unittest.TestCase):
    def test_invalid_decider_json_falls_back_to_visual_observation(self) -> None:
        decision = decide_active_observation(
            "浏览器打开的是什么网站？",
            {"enabled": True, "active_observation": {"enabled": True}},
            decider=lambda _text, _config: "not json",
        )

        self.assertTrue(decision["needs_observation"])
        self.assertEqual(decision["mode"], "desktop_survey")
        self.assertEqual(decision["target_hint"], "desktop_survey")
        self.assertEqual(decision["actions"], [])
        self.assertEqual(decision["source"], "fallback")
        self.assertIn("invalid", decision["reason"])

    def test_non_visual_chat_does_not_need_observation(self) -> None:
        decision = decide_active_observation("今天聊点轻松的", {"enabled": True})

        self.assertFalse(decision["needs_observation"])
        self.assertEqual(decision["actions"], [])

    def test_normalize_active_observation_restricts_actions_to_light(self) -> None:
        config = normalize_active_observation_config(
            {
                "enabled": True,
                "allowed_interaction": "full_control",
                "timeout_sec": 100,
                "settle_ms": 90000,
            }
        )

        self.assertTrue(config["enabled"])
        self.assertEqual(config["allowed_interaction"], "light")
        self.assertEqual(config["timeout_sec"], 20.0)
        self.assertEqual(config["settle_ms"], 2000)

    def test_desktop_survey_queues_window_targets_without_browser_special_cases(self) -> None:
        metadata = build_active_observe_metadata(
            {
                "text": "屏幕上视频里是什么？",
                "mode": "desktop_survey",
                "exclude_seen": [],
            },
            {
                "frame_hash": "hash-ipet",
                "desktop_context": {"foreground_app": "Ipet", "window_title": "Ipet chat"},
                "observations": [],
                "active_observation": {
                    "mode": "desktop_survey",
                    "desktop_targets": [
                        {"target_id": "target-safari", "app": "Safari", "title": "Video", "frontmost": False},
                        {"target_id": "target-preview", "app": "Preview", "title": "Image", "frontmost": False},
                    ],
                },
            },
            {},
        )

        self.assertEqual(metadata["attempt_index"], 1)
        self.assertTrue(metadata["self_occluded"])
        self.assertEqual(metadata["relevance_hint"], "not_relevant:self_occluded")
        self.assertEqual(metadata["available_next_targets"], ["target-safari", "target-preview"])
        self.assertNotIn("browser", metadata["available_next_targets"])
        self.assertNotIn("media_window", metadata["available_next_targets"])
        self.assertFalse(metadata["stop"])

    def test_exclude_seen_filters_target_ids_and_records_attempts(self) -> None:
        metadata = build_active_observe_metadata(
            {
                "mode": "focus_target",
                "target_id": "target-preview",
                "attempt_reason": "focus next desktop target",
                "exclude_seen": [
                    {"target_id": "target-safari", "foreground_app": "Safari", "window_title": "Docs", "frame_hash": "seen-1"},
                ],
            },
            {
                "frame_hash": "hash-new",
                "desktop_context": {"foreground_app": "Finder", "window_title": "Desktop"},
                "observations": [],
                "active_observation": {
                    "desktop_targets": [
                        {"target_id": "target-safari", "app": "Safari", "title": "Docs"},
                        {"target_id": "target-preview", "app": "Preview", "title": "Image"},
                        {"target_id": "target-code", "app": "Code", "title": "Editor"},
                    ],
                },
            },
            {},
        )

        self.assertEqual(metadata["attempt_index"], 2)
        self.assertIn("target-safari", metadata["attempted_targets"])
        self.assertIn("target-preview", metadata["attempted_targets"])
        self.assertNotIn("target-safari", metadata["available_next_targets"])
        self.assertNotIn("target-preview", metadata["available_next_targets"])
        self.assertEqual(metadata["available_next_targets"], ["target-code"])

    def test_repeated_frame_or_window_stops_without_next_targets(self) -> None:
        repeated_frame = build_active_observe_metadata(
            {"target_hint": "previous_app", "exclude_seen": [{"frame_hash": "same-frame"}]},
            {
                "frame_hash": "same-frame",
                "desktop_context": {"foreground_app": "Safari", "window_title": "Video"},
                "observations": [],
            },
            {},
        )
        repeated_window = build_active_observe_metadata(
            {"target_hint": "current_desktop", "exclude_seen": [{"foreground_app": "Safari", "window_title": "Video"}]},
            {
                "frame_hash": "other-frame",
                "desktop_context": {"foreground_app": "Safari", "window_title": "Video"},
                "observations": [],
            },
            {},
        )

        self.assertTrue(repeated_frame["stop"])
        self.assertEqual(repeated_frame["relevance_hint"], "stop:repeated_frame")
        self.assertEqual(repeated_frame["available_next_targets"], [])
        self.assertTrue(repeated_window["stop"])
        self.assertEqual(repeated_window["relevance_hint"], "stop:repeated_window")
        self.assertEqual(repeated_window["available_next_targets"], [])

    def test_attempt_index_at_limit_sets_stop_hint(self) -> None:
        metadata = build_active_observe_metadata(
            {
                "mode": "focus_target",
                "target_id": "target-preview",
                "exclude_seen": [
                    {"target_id": "desktop_survey", "frame_hash": "h1"},
                    {"target_id": "target-safari", "frame_hash": "h2"},
                ],
            },
            {
                "frame_hash": "h3",
                "desktop_context": {"foreground_app": "Preview", "window_title": "Image"},
                "observations": [],
                "active_observation": {
                    "desktop_targets": [
                        {"target_id": "target-safari", "app": "Safari"},
                        {"target_id": "target-preview", "app": "Preview"},
                    ]
                },
            },
            {},
        )

        self.assertEqual(metadata["attempt_index"], 3)
        self.assertTrue(metadata["stop"])
        self.assertEqual(metadata["relevance_hint"], "stop:max_attempts")
        self.assertEqual(metadata["available_next_targets"], [])

    def test_no_new_targets_sets_stop_hint(self) -> None:
        metadata = build_active_observe_metadata(
            {
                "mode": "focus_target",
                "target_id": "target-preview",
                "exclude_seen": [
                    {"target_id": "desktop_survey"},
                    {"target_id": "target-safari"},
                    {"target_id": "target-preview"},
                ],
                "attempt_index": 2,
            },
            {
                "frame_hash": "new-hash",
                "desktop_context": {"foreground_app": "Preview", "window_title": "Image"},
                "observations": [],
                "active_observation": {
                    "desktop_targets": [
                        {"target_id": "target-safari", "app": "Safari"},
                        {"target_id": "target-preview", "app": "Preview"},
                    ]
                },
            },
            {},
        )

        self.assertTrue(metadata["stop"])
        self.assertEqual(metadata["relevance_hint"], "stop:no_new_targets")

    def test_fallback_target_candidates_prevent_desktop_targets_empty_stop(self) -> None:
        metadata = build_active_observe_metadata(
            {
                "text": "屏幕上现在是什么？",
                "mode": "desktop_survey",
                "exclude_seen": [{"target_id": "desktop_survey", "frame_hash": "old-frame"}],
            },
            {
                "frame_hash": "new-frame",
                "desktop_context": {"foreground_app": "Ipet", "window_title": "Ipet chat"},
                "observations": [],
                "active_observation": {
                    "mode": "desktop_survey",
                    "desktop_targets": [],
                    "discovery_errors": ["System Events failed: -10827"],
                    "target_candidates": [
                        {
                            "target_id": "running:safari",
                            "source": "running_app",
                            "app": "Safari",
                            "title": "Safari",
                            "focusable": True,
                        },
                        {
                            "target_id": "screenshot:full_desktop",
                            "source": "screenshot_region",
                            "title": "full desktop screenshot",
                            "focusable": False,
                        },
                    ],
                },
            },
            {},
        )

        self.assertFalse(metadata["stop"])
        self.assertNotEqual(metadata["relevance_hint"], "stop:no_new_targets")
        self.assertEqual(metadata["available_next_targets"], ["running:safari"])
        self.assertEqual(metadata["selected_candidate"]["target_id"], "running:safari")
        self.assertEqual(metadata["selected_candidate"]["source"], "running_app")
        self.assertEqual(metadata["target_candidates"][0]["source"], "running_app")
        self.assertEqual(metadata["discovery_errors"], ["System Events failed: -10827"])

    def test_user_text_match_prioritizes_generic_app_candidate(self) -> None:
        metadata = build_active_observe_metadata(
            {
                "text": "我现在正在看的 FluxBoard 题单叫什么？",
                "mode": "desktop_survey",
                "exclude_seen": [{"target_id": "desktop_survey", "frame_hash": "old-frame"}],
            },
            {
                "frame_hash": "new-frame",
                "desktop_context": {"foreground_app": "Ipet", "window_title": "Ipet chat"},
                "observations": [],
                "active_observation": {
                    "desktop_targets": [],
                    "target_candidates": [
                        {
                            "target_id": "running:calendar",
                            "source": "running_app",
                            "app": "Calendar",
                            "title": "Calendar",
                            "focusable": True,
                            "score": 80,
                        },
                        {
                            "target_id": "running:fluxboard",
                            "source": "running_app",
                            "app": "FluxBoard",
                            "title": "Study list",
                            "focusable": True,
                            "score": 60,
                        },
                    ],
                },
            },
            {},
        )

        self.assertEqual(metadata["selected_candidate"]["target_id"], "running:fluxboard")
        self.assertEqual(metadata["available_next_targets"][0], "running:fluxboard")

    def test_current_frontmost_candidate_is_not_next_target(self) -> None:
        metadata = build_active_observe_metadata(
            {
                "text": "看看别的窗口里有没有题单",
                "mode": "desktop_survey",
                "exclude_seen": [{"target_id": "desktop_survey", "frame_hash": "old-frame"}],
            },
            {
                "frame_hash": "new-frame",
                "desktop_context": {"foreground_app": "WorkPad", "window_title": "Current Doc"},
                "observations": [],
                "active_observation": {
                    "desktop_targets": [],
                    "target_candidates": [
                        {
                            "target_id": "running:workpad",
                            "source": "running_app",
                            "app": "WorkPad",
                            "title": "Current Doc",
                            "frontmost": True,
                            "focusable": True,
                            "score": 95,
                        },
                        {
                            "target_id": "running:notes",
                            "source": "running_app",
                            "app": "NotesLab",
                            "title": "Problem set",
                            "focusable": True,
                            "score": 70,
                        },
                    ],
                },
            },
            {},
        )

        self.assertEqual(metadata["selected_candidate"]["target_id"], "running:notes")
        self.assertEqual(metadata["available_next_targets"], ["running:notes"])

    def test_passive_desktop_context_adds_focusable_next_target(self) -> None:
        metadata = build_active_observe_metadata(
            {
                "text": "我刚才看的 FluxDesk 里是什么？",
                "mode": "desktop_survey",
                "exclude_seen": [{"target_id": "desktop_survey", "frame_hash": "old-frame"}],
            },
            {
                "frame_hash": "new-frame",
                "desktop_context": {"foreground_app": "Ipet", "window_title": "Ipet chat"},
                "observations": [],
                "active_observation": {"desktop_targets": [], "target_candidates": []},
            },
            {},
            passive_context={
                "desktop_context": {
                    "foreground_app": "FluxDesk",
                    "frontmost_process": "FluxDesk",
                    "window_title": "Weekly Problem List",
                }
            },
        )

        desktop_context_candidates = [
            item for item in metadata["target_candidates"] if item.get("source") == "desktop_context"
        ]
        self.assertEqual(len(desktop_context_candidates), 1)
        self.assertTrue(desktop_context_candidates[0]["focusable"])
        self.assertIn(desktop_context_candidates[0]["target_id"], metadata["available_next_targets"])
        self.assertEqual(metadata["selected_candidate"]["target_id"], desktop_context_candidates[0]["target_id"])


if __name__ == "__main__":
    unittest.main()
