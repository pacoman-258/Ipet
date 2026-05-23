from __future__ import annotations

import unittest

from backend.vision_state import VisionRoutingState, normalize_vision_routing_config


def _payload(
    token: str,
    *,
    visual_hash: str | None = None,
    foreground_app: str = "Code",
    window_title: str = "main.py",
    display_count: int = 1,
) -> dict:
    return {
        "mime_type": "image/jpeg",
        "data_url": f"data:image/jpeg;base64,{token}",
        "visual_hash": visual_hash or token,
        "capture_backend": "qt_fallback",
        "capture_scope": "visible_spaces_all_displays",
        "display_count": display_count,
        "desktop_context": {
            "foreground_app": foreground_app,
            "window_title": window_title,
        },
    }


class VisionRoutingStateTests(unittest.TestCase):
    def test_repeated_frame_reuses_existing_evidence_without_analyzer(self) -> None:
        ticks = [10.0]
        state = VisionRoutingState(now=lambda: ticks[0])
        config = normalize_vision_routing_config({"stable_after_change_ms": 0, "vlm_cooldown_sec": 10})

        first = state.evaluate(_payload("aaaa", visual_hash="0000"), config, analyzer_enabled=True)
        second = state.evaluate(_payload("aaaa", visual_hash="0000"), config, analyzer_enabled=True)

        self.assertTrue(first["should_analyze"])
        self.assertIn("initial_frame", first["events"])
        self.assertFalse(second["should_analyze"])
        self.assertTrue(second["reuse_evidence"])
        self.assertEqual(second["reason"], "no_change")

    def test_visual_hash_and_desktop_metadata_changes_trigger_analysis(self) -> None:
        ticks = [10.0]
        state = VisionRoutingState(now=lambda: ticks[0])
        config = normalize_vision_routing_config({"stable_after_change_ms": 0, "vlm_cooldown_sec": 0})

        state.evaluate(_payload("aaaa", visual_hash="0000"), config, analyzer_enabled=True)
        ticks[0] += 1.0
        visual = state.evaluate(_payload("bbbb", visual_hash="ffff"), config, analyzer_enabled=True)
        ticks[0] += 1.0
        metadata = state.evaluate(
            _payload("bbbb", visual_hash="ffff", foreground_app="Safari", window_title="Docs"),
            config,
            analyzer_enabled=True,
        )

        self.assertTrue(visual["should_analyze"])
        self.assertIn("visual_hash_changed", visual["events"])
        self.assertTrue(metadata["should_analyze"])
        self.assertIn("foreground_app_changed", metadata["events"])
        self.assertIn("window_title_changed", metadata["events"])
        self.assertIn("desktop_context_changed", metadata["events"])

    def test_cooldown_blocks_repeated_vlm_for_same_desktop_context(self) -> None:
        ticks = [20.0]
        state = VisionRoutingState(now=lambda: ticks[0])
        config = normalize_vision_routing_config({"stable_after_change_ms": 0, "vlm_cooldown_sec": 10})

        state.evaluate(_payload("aaaa", visual_hash="0000"), config, analyzer_enabled=True)
        ticks[0] += 2.0
        decision = state.evaluate(_payload("bbbb", visual_hash="ffff"), config, analyzer_enabled=True)

        self.assertFalse(decision["should_analyze"])
        self.assertEqual(decision["reason"], "cooldown")
        self.assertGreater(decision["cooldown_remaining_sec"], 0)

    def test_force_analyze_bypasses_cooldown_for_same_desktop_context(self) -> None:
        ticks = [20.0]
        state = VisionRoutingState(now=lambda: ticks[0])
        config = normalize_vision_routing_config({"stable_after_change_ms": 0, "vlm_cooldown_sec": 10})

        state.evaluate(_payload("aaaa", visual_hash="0000"), config, analyzer_enabled=True)
        ticks[0] += 2.0
        decision = state.evaluate(
            _payload("aaaa", visual_hash="0000"),
            config,
            analyzer_enabled=True,
            force_analyze=True,
        )

        self.assertTrue(decision["should_analyze"])
        self.assertEqual(decision["reason"], "force_analyze")

    def test_in_flight_analysis_defers_new_frame_without_second_start(self) -> None:
        state = VisionRoutingState(now=lambda: 30.0)
        config = normalize_vision_routing_config({"stable_after_change_ms": 0, "vlm_cooldown_sec": 0})

        state.evaluate(_payload("aaaa", visual_hash="0000"), config, analyzer_enabled=True)
        decision = state.evaluate(
            _payload("bbbb", visual_hash="ffff"),
            config,
            analyzer_enabled=True,
            analysis_in_flight=True,
        )

        self.assertFalse(decision["should_analyze"])
        self.assertEqual(decision["reason"], "analysis_in_flight")
        self.assertTrue(decision["pending_analysis"])
        self.assertTrue(state.status()["pending_analysis"])

    def test_in_flight_deferred_frame_is_analyzed_on_followup(self) -> None:
        ticks = [50.0]
        state = VisionRoutingState(now=lambda: ticks[0])
        config = normalize_vision_routing_config({"stable_after_change_ms": 0, "vlm_cooldown_sec": 0})

        state.evaluate(_payload("aaaa", visual_hash="0000"), config, analyzer_enabled=True)
        deferred = state.evaluate(
            _payload("bbbb", visual_hash="ffff"),
            config,
            analyzer_enabled=True,
            analysis_in_flight=True,
        )
        ticks[0] += 0.1
        followup = state.evaluate(_payload("bbbb", visual_hash="ffff"), config, analyzer_enabled=True)

        self.assertFalse(deferred["should_analyze"])
        self.assertEqual(deferred["reason"], "analysis_in_flight")
        self.assertTrue(followup["should_analyze"])
        self.assertIn("stable_after_change", followup["events"])

    def test_stable_after_change_event_waits_for_unchanged_followup(self) -> None:
        ticks = [40.0]
        state = VisionRoutingState(now=lambda: ticks[0])
        config = normalize_vision_routing_config({"stable_after_change_ms": 500, "vlm_cooldown_sec": 0})

        state.evaluate(_payload("aaaa", visual_hash="0000"), config, analyzer_enabled=True)
        ticks[0] += 0.1
        changing = state.evaluate(_payload("bbbb", visual_hash="ffff"), config, analyzer_enabled=True)
        ticks[0] += 0.6
        stable = state.evaluate(_payload("bbbb", visual_hash="ffff"), config, analyzer_enabled=True)

        self.assertFalse(changing["should_analyze"])
        self.assertEqual(changing["reason"], "stabilizing")
        self.assertTrue(changing["pending_analysis"])
        self.assertTrue(stable["should_analyze"])
        self.assertIn("stable_after_change", stable["events"])

    def test_stable_after_change_handles_zero_timestamp(self) -> None:
        ticks = [0.0]
        state = VisionRoutingState(now=lambda: ticks[0])
        config = normalize_vision_routing_config({"stable_after_change_ms": 500, "vlm_cooldown_sec": 0})

        state.evaluate(_payload("aaaa", visual_hash="0000"), config, analyzer_enabled=True)
        changing = state.evaluate(_payload("bbbb", visual_hash="ffff"), config, analyzer_enabled=True)
        ticks[0] = 0.7
        stable = state.evaluate(_payload("bbbb", visual_hash="ffff"), config, analyzer_enabled=True)

        self.assertEqual(changing["reason"], "stabilizing")
        self.assertTrue(stable["should_analyze"])
        self.assertIn("stable_after_change", stable["events"])


if __name__ == "__main__":
    unittest.main()
