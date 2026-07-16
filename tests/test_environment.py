from __future__ import annotations

import unittest
from types import SimpleNamespace

from backend.environment import (
    EnvironmentService,
    classify_screen_activity,
    normalize_environment_config,
)
from backend.proactive_context import build_proactive_conversation_history


class _Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class EnvironmentServiceTests(unittest.TestCase):
    def config(self, **overrides):
        return {
            "mode": "active",
            "intensity": "balanced",
            "away_threshold_minutes": 1,
            "focus_minutes": 5,
            "initiative_cooldown_minutes": 1,
            "minimum_confidence": 0.5,
            "quiet_hours_start": 22,
            "quiet_hours_end": 8,
            **overrides,
        }

    def test_config_normalization_is_privacy_conservative(self) -> None:
        config = normalize_environment_config(
            {
                "mode": "unknown",
                "screen_context_enabled": "yes",
                "include_window_titles": 1,
                "sensor_interval_sec": 0,
                "blocked_apps": ["Vault", "vault", ""],
            }
        )
        self.assertEqual(config["mode"], "off")
        self.assertFalse(config["screen_context_enabled"])
        self.assertFalse(config["include_window_titles"])
        self.assertEqual(config["sensor_interval_sec"], 2)
        self.assertEqual(config["blocked_apps"], ["Vault"])

    def test_sensitive_context_is_redacted_before_presence_state(self) -> None:
        service = EnvironmentService(self.config(include_window_titles=True), local_hour=lambda _: 12)
        status = service.ingest_event(
            {"foreground_app": "Browser", "window_title": "My Bank - payment", "idle_seconds": 0}
        )
        self.assertTrue(status["presence"]["privacy_blocked"])
        self.assertEqual(status["presence"]["foreground_app"], "")
        self.assertEqual(status["presence"]["window_title"], "")
        self.assertEqual(status["recent_audit"][-1]["action"], "privacy_blocked")

    def test_shadow_mode_records_candidate_without_queueing_delivery(self) -> None:
        clock = _Clock()
        service = EnvironmentService(
            self.config(mode="shadow"), now=clock.now, local_hour=lambda _: 12, id_factory=lambda: "candidate-1"
        )
        service.ingest_event({"foreground_app": "Editor", "idle_seconds": 120})
        clock.advance(5)
        status = service.ingest_event({"foreground_app": "Editor", "idle_seconds": 0})
        self.assertEqual(status["pending_count"], 0)
        self.assertTrue(any(item["action"] == "would_speak" for item in status["recent_audit"]))
        self.assertIsNone(service.claim_opportunity(session_id="topic"))

    def test_active_return_candidate_delivers_and_becomes_reply_context_once(self) -> None:
        clock = _Clock()
        service = EnvironmentService(
            self.config(), now=clock.now, local_hour=lambda _: 12, id_factory=lambda: "candidate-1"
        )
        service.ingest_event({"foreground_app": "Editor", "idle_seconds": 180})
        clock.advance(5)
        service.ingest_event({"foreground_app": "Editor", "idle_seconds": 0})
        candidate = service.claim_opportunity(session_id="topic")
        self.assertEqual(candidate["kind"], "user_returned")
        generated = service.complete_generation(candidate["id"], "你回来啦，刚才休息得怎么样？")
        self.assertEqual(generated["status"], "awaiting_delivery")
        self.assertTrue(service.feedback(candidate["id"], "delivered", session_id="topic"))
        context = service.consume_user_reply_context("topic")
        self.assertIn("你回来啦", context)
        self.assertEqual(service.consume_user_reply_context("topic"), "")

    def test_unanswered_delivery_uses_backoff(self) -> None:
        clock = _Clock()
        ids = iter(["one", "two"])
        service = EnvironmentService(
            self.config(unanswered_backoff_minutes=10, screen_context_enabled=True),
            now=clock.now,
            local_hour=lambda _: 12,
            id_factory=lambda: next(ids),
        )
        service.ingest_event({"foreground_app": "Editor", "idle_seconds": 120})
        clock.advance(5)
        service.ingest_event({"foreground_app": "Editor", "idle_seconds": 0})
        first = service.claim_opportunity(session_id="topic")
        service.complete_generation(first["id"], "回来啦？")
        service.feedback(first["id"], "delivered", session_id="topic")
        clock.advance(61)
        service.ingest_screen_activity("tests failed", foreground_app="Editor")
        self.assertIsNone(service.claim_opportunity(session_id="topic"))
        clock.advance(600)
        self.assertIsNotNone(service.claim_opportunity(session_id="topic"))

    def test_focus_milestone_and_screen_activity_are_semantic_only(self) -> None:
        clock = _Clock()
        ids = iter(["focus", "test"])
        service = EnvironmentService(
            self.config(focus_minutes=5), now=clock.now, local_hour=lambda _: 12, id_factory=lambda: next(ids)
        )
        service.ingest_event({"foreground_app": "Editor", "idle_seconds": 0})
        clock.advance(301)
        status = service.ingest_event({"foreground_app": "Editor", "idle_seconds": 0})
        self.assertTrue(any(item["kind"] == "focus_milestone" for item in status["recent_audit"]))
        self.assertEqual(classify_screen_activity("31 tests passed in 0.4s"), "test_passed")
        self.assertEqual(classify_screen_activity("用户正在写一封信"), "")

    def test_quiet_hours_suppress_candidate(self) -> None:
        clock = _Clock()
        service = EnvironmentService(self.config(), now=clock.now, local_hour=lambda _: 23)
        service.ingest_event({"foreground_app": "Editor", "idle_seconds": 120})
        clock.advance(5)
        status = service.ingest_event({"foreground_app": "Editor", "idle_seconds": 0})
        self.assertEqual(status["pending_count"], 0)
        self.assertEqual(status["recent_audit"][-1]["reason"], "quiet_hours")

    def test_presence_expires_and_mode_change_cancels_pending_delivery(self) -> None:
        clock = _Clock()
        service = EnvironmentService(
            self.config(event_ttl_sec=60), now=clock.now, local_hour=lambda _: 12, id_factory=lambda: "candidate"
        )
        service.ingest_event({"foreground_app": "Editor", "idle_seconds": 120})
        clock.advance(5)
        service.ingest_event({"foreground_app": "Editor", "idle_seconds": 0})
        self.assertEqual(service.status()["pending_count"], 1)
        service.configure(self.config(mode="shadow", event_ttl_sec=60))
        self.assertEqual(service.status()["pending_count"], 0)
        service.configure(self.config(event_ttl_sec=60))
        self.assertIsNone(service.claim_opportunity(session_id="topic"))
        clock.advance(61)
        self.assertEqual(service.status()["presence"]["foreground_app"], "")

    def test_screen_activity_remains_independent_when_metadata_is_disabled(self) -> None:
        service = EnvironmentService(
            self.config(metadata_enabled=False, screen_context_enabled=True),
            local_hour=lambda _: 12,
            id_factory=lambda: "screen-candidate",
        )
        activity = service.ingest_screen_activity("31 tests passed", foreground_app="Terminal", confidence=0.9)
        self.assertEqual(activity, "test_passed")
        candidate = service.claim_opportunity(session_id="topic")
        self.assertEqual(candidate["kind"], "test_passed")
        self.assertEqual(candidate["facts"]["foreground_app"], "")

    def test_generated_proactive_text_is_hard_limited(self) -> None:
        clock = _Clock()
        service = EnvironmentService(
            self.config(), now=clock.now, local_hour=lambda _: 12, id_factory=lambda: "candidate"
        )
        service.ingest_event({"foreground_app": "Editor", "idle_seconds": 120})
        clock.advance(5)
        service.ingest_event({"foreground_app": "Editor", "idle_seconds": 0})
        candidate = service.claim_opportunity(session_id="topic")
        generated = service.complete_generation(candidate["id"], "很" * 200)
        self.assertEqual(len(generated["generated_text"]), 80)

    def test_claim_survives_a_slow_but_valid_brain_turn(self) -> None:
        clock = _Clock()
        service = EnvironmentService(
            self.config(), now=clock.now, local_hour=lambda _: 12, id_factory=lambda: "candidate"
        )
        service.ingest_event({"foreground_app": "Editor", "idle_seconds": 120})
        clock.advance(5)
        service.ingest_event({"foreground_app": "Editor", "idle_seconds": 0})
        candidate = service.claim_opportunity(session_id="topic")
        clock.advance(90)
        service.status()
        generated = service.complete_generation(candidate["id"], "慢一点也没关系。")
        self.assertEqual(generated["status"], "awaiting_delivery")

    def test_proactive_context_uses_recent_topic_and_relationship_memory(self) -> None:
        messages = [{"role": "user", "content": f"message-{index}"} for index in range(15)]
        topic_store = SimpleNamespace(
            get_context_snapshot=lambda _session_id: SimpleNamespace(model_messages=messages)
        )
        memory_queries = []

        def relationship_context(query, config):
            memory_queries.append((query, config))
            return "用户不喜欢被催促。", []

        history = build_proactive_conversation_history(
            topic_store=topic_store,
            session_id="topic",
            opportunity={"kind": "test_failed", "facts": {"foreground_app": "Terminal"}},
            private_config={"memory": {"long_term_enabled": True}},
            relationship_memory_context=relationship_context,
        )

        self.assertEqual(history[0], {"role": "system", "content": "用户不喜欢被催促。"})
        self.assertEqual(history[1]["content"], "message-3")
        self.assertEqual(len(history), 13)
        self.assertEqual(memory_queries[0][0], "test_failed Terminal")


if __name__ == "__main__":
    unittest.main()
