from __future__ import annotations

import unittest

from backend.game import GameService, normalize_game_config


class _Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class GameServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        ids = (f"reaction-{index}" for index in range(20))
        self.service = GameService(now=self.clock.now, id_factory=lambda: next(ids))
        self.service.heartbeat(
            {
                "session_id": "session-1",
                "run_id": "run-1",
                "game_version": "beta",
                "adapter_version": "0.1.0",
                "compatible": True,
                "local_player_identified": True,
                "run_summary": {"act": 1, "floor": 1, "hp": 70, "max_hp": 80},
            }
        )

    def event(self, sequence: int, kind: str, *, facts=None, snapshot=None, age: float = 0.0):
        return {
            "session_id": "session-1",
            "run_id": "run-1",
            "events": [
                {
                    "sequence": sequence,
                    "occurred_at_ms": int((self.clock.now() - age) * 1000),
                    "kind": kind,
                    "facts": facts or {},
                    "snapshot": snapshot or {},
                }
            ],
        }

    def test_config_is_bounded_and_categories_default_on(self) -> None:
        config = normalize_game_config(
            {
                "enabled": 0,
                "adapter": "invented",
                "min_reaction_interval_sec": 999,
                "max_reactions_per_minute": 0,
                "reaction_instruction": "好" * 600,
                "categories": {"combat": False},
            }
        )
        self.assertTrue(config["enabled"])
        self.assertEqual(config["adapter"], "slay_the_spire_2")
        self.assertEqual(config["min_reaction_interval_sec"], 60)
        self.assertEqual(config["max_reactions_per_minute"], 1)
        self.assertEqual(len(config["reaction_instruction"]), 500)
        self.assertFalse(config["categories"]["combat"])
        self.assertTrue(config["categories"]["outcome"])

    def test_normal_events_coalesce_after_750ms(self) -> None:
        self.service.ingest_events(self.event(1, "combat_started"))
        self.clock.advance(0.2)
        self.service.ingest_events(self.event(2, "enemy_defeated", facts={"enemy_id": "slime"}))
        self.assertIsNone(self.service.claim_opportunity())
        self.clock.advance(0.8)
        candidate = self.service.claim_opportunity()
        self.assertEqual(candidate["kind"], "enemy_defeated")
        self.assertEqual(self.service.status()["pending_count"], 1)

    def test_thresholds_and_context_events_do_not_queue(self) -> None:
        snapshot = {"player": {"hp": 60, "max_hp": 80}}
        self.service.ingest_events(self.event(1, "turn_started", snapshot=snapshot))
        self.service.ingest_events(
            self.event(2, "large_damage_taken", facts={"damage": 9, "max_hp": 80}, snapshot=snapshot)
        )
        self.service.ingest_events(
            self.event(3, "heal_received", facts={"healed": 15, "max_hp": 80}, snapshot=snapshot)
        )
        self.clock.advance(1)
        self.assertIsNone(self.service.claim_opportunity())
        self.assertEqual(self.service.status()["last_event"]["kind"], "heal_received")

    def test_heartbeat_preserves_latest_event_for_same_run(self) -> None:
        self.service.ingest_events(self.event(1, "room_entered"))
        self.service.heartbeat(
            {
                "session_id": "session-1",
                "run_id": "run-1",
                "adapter_version": "0.1.0",
                "compatible": True,
                "local_player_identified": True,
                "run_summary": {"act": 1, "floor": 2},
            }
        )

        self.assertEqual(self.service.status()["last_event"]["kind"], "room_entered")

    def test_low_hp_is_latched_until_recovery(self) -> None:
        low = {"player": {"hp": 20, "max_hp": 100}}
        self.service.ingest_events(self.event(1, "low_hp_entered", snapshot=low))
        first = self.service.claim_opportunity(client_state={"speech_busy": True})
        self.assertTrue(first["priority"] == "important")
        self.service.feedback(first["id"], "delivered")
        self.service.ingest_events(self.event(2, "low_hp_entered", snapshot=low))
        self.assertIsNone(self.service.claim_opportunity())
        recovered = {"player": {"hp": 50, "max_hp": 100}}
        self.service.ingest_events(self.event(3, "turn_started", snapshot=recovered))
        self.service.ingest_events(self.event(4, "low_hp_entered", snapshot=low))
        second = self.service.claim_opportunity()
        self.assertEqual(second["kind"], "low_hp_entered")

    def test_duplicate_stale_pause_and_disconnect_fail_closed(self) -> None:
        self.service.ingest_events(self.event(1, "combat_started"))
        duplicate = self.service.ingest_events(self.event(1, "combat_won"))
        self.assertEqual(duplicate["ignored"], 1)
        stale = self.service.ingest_events(self.event(2, "combat_won", age=20))
        self.assertEqual(stale["ignored"], 1)
        paused = self.service.pause(True, session_id="session-1")
        self.assertEqual(paused["state"], "paused")
        blocked = self.service.ingest_events(self.event(3, "run_won"))
        self.assertEqual(blocked["reason"], "paused")
        self.clock.advance(12)
        self.assertEqual(self.service.status()["state"], "waiting")
        self.assertEqual(self.service.status()["pending_count"], 0)

    def test_new_run_resets_temporary_pause(self) -> None:
        self.service.pause(True, session_id="session-1")
        status = self.service.heartbeat(
            {
                "session_id": "session-1",
                "run_id": "run-2",
                "adapter_version": "0.1.0",
                "compatible": True,
                "local_player_identified": True,
                "run_summary": {"act": 1, "floor": 0},
            }
        )
        self.assertEqual(status["state"], "active")
        self.assertFalse(status["paused"])

    def test_important_event_replaces_normal_and_can_interrupt_speech(self) -> None:
        self.service.ingest_events(self.event(1, "combat_started"))
        self.clock.advance(0.1)
        self.service.ingest_events(self.event(2, "run_won"))

        candidate = self.service.claim_opportunity(client_state={"speech_busy": True})

        self.assertEqual(candidate["kind"], "run_won")
        self.assertEqual(candidate["priority"], "important")
        replaced = [item for item in self.service.status()["recent_audit"] if item["kind"] == "combat_started"]
        self.assertTrue(replaced)

    def test_new_normal_event_replaces_claimed_unplayed_normal_response(self) -> None:
        self.service.ingest_events(self.event(1, "combat_started"))
        self.clock.advance(0.8)
        claimed = self.service.claim_opportunity()
        self.assertEqual(claimed["kind"], "combat_started")

        self.clock.advance(1)
        self.service.ingest_events(self.event(2, "enemy_defeated", facts={"enemy_id": "slime"}))

        self.assertIsNone(self.service.complete_generation(claimed["id"], "开打了。"))
        self.clock.advance(0.8)
        replacement = self.service.claim_opportunity()
        self.assertEqual(replacement["kind"], "enemy_defeated")

    def test_generated_reaction_has_bounded_synthesis_and_playback_grace(self) -> None:
        self.service.ingest_events(self.event(1, "combat_started"))
        self.clock.advance(0.8)
        claimed = self.service.claim_opportunity()
        self.clock.advance(10)
        generated = self.service.complete_generation(claimed["id"], "开打了。")
        self.assertIsNotNone(generated)

        for advance in (0, 10, 10):
            self.clock.advance(advance)
            self.service.heartbeat(
                {
                    "session_id": "session-1",
                    "run_id": "run-1",
                    "adapter_version": "0.1.0",
                    "compatible": True,
                    "local_player_identified": True,
                    "run_summary": {"act": 1, "floor": 1},
                }
            )
        self.assertEqual(self.service.status()["pending_count"], 1)
        self.assertTrue(self.service.feedback(claimed["id"], "delivered"))

    def test_minimum_interval_starts_when_reaction_enters_speech_pipeline(self) -> None:
        self.service.ingest_events(self.event(1, "combat_started"))
        self.clock.advance(0.8)
        first = self.service.claim_opportunity()
        self.assertIsNotNone(self.service.complete_generation(first["id"], "开打了。"))

        # Playback finishes 4.2 seconds later. The remaining 0.8 seconds of
        # the configured five-second spacing window should be enough; the
        # full interval must not restart from this feedback timestamp.
        self.clock.advance(4.2)
        self.assertTrue(self.service.feedback(first["id"], "delivered"))
        self.service.ingest_events(self.event(2, "combat_won"))
        self.clock.advance(0.8)

        self.assertEqual(self.service.claim_opportunity()["kind"], "combat_won")

    def test_category_gate_expiry_and_per_minute_limit(self) -> None:
        self.service.configure({"categories": {"combat": False}})
        self.service.ingest_events(self.event(1, "combat_started"))
        self.clock.advance(1)
        self.assertIsNone(self.service.claim_opportunity())

        self.service.configure({"max_reactions_per_minute": 2})
        for sequence, kind in ((2, "run_won"), (3, "run_lost")):
            self.service.ingest_events(self.event(sequence, kind))
            candidate = self.service.claim_opportunity()
            self.assertIsNotNone(candidate)
            self.assertTrue(self.service.feedback(candidate["id"], "delivered"))
        self.service.ingest_events(self.event(4, "run_abandoned"))
        self.assertIsNone(self.service.claim_opportunity())

        self.clock.advance(61)
        self.service.heartbeat(
            {
                "session_id": "session-1",
                "run_id": "run-1",
                "adapter_version": "0.1.0",
                "compatible": True,
                "local_player_identified": True,
                "run_summary": {},
            }
        )
        # The old queued event cannot survive its 30 second important TTL.
        self.assertIsNone(self.service.claim_opportunity())

    def test_first_run_id_after_menu_heartbeat_resets_pause(self) -> None:
        service = GameService(now=self.clock.now)
        service.heartbeat(
            {
                "session_id": "menu-session",
                "run_id": "",
                "adapter_version": "0.1.0",
                "compatible": True,
                "local_player_identified": True,
                "run_summary": {},
            }
        )
        self.assertEqual(service.status()["state"], "connected")
        service.pause(True, session_id="menu-session")
        status = service.heartbeat(
            {
                "session_id": "menu-session",
                "run_id": "first-run",
                "adapter_version": "0.1.0",
                "compatible": True,
                "local_player_identified": True,
                "run_summary": {},
            }
        )
        self.assertEqual(status["state"], "active")
        self.assertFalse(status["paused"])

    def test_run_end_keeps_only_outcome_reaction_while_connected(self) -> None:
        self.service.ingest_events(self.event(1, "combat_started"))
        self.service.ingest_events(self.event(2, "run_won"))
        status = self.service.heartbeat(
            {
                "session_id": "session-1",
                "run_id": "",
                "adapter_version": "0.1.0",
                "compatible": True,
                "local_player_identified": True,
                "run_summary": {},
            }
        )

        self.assertEqual(status["state"], "connected")
        candidate = self.service.claim_opportunity(client_state={"speech_busy": True})
        self.assertEqual(candidate["kind"], "run_won")

    def test_expired_event_does_not_pollute_last_sequence(self) -> None:
        # Event with sequence 10 is expired (age 60s > TTL)
        stale = self.service.ingest_events(self.event(10, "combat_started", age=60.0))
        self.assertTrue(stale["ok"])
        self.assertEqual(stale["accepted"], 0)
        self.assertEqual(stale["ignored"], 1)
        self.assertEqual(self.service.status()["accepted_sequence"], 0)

        # Fresh event with sequence 5 should not be rejected as out-of-order
        fresh = self.service.ingest_events(self.event(5, "combat_started", age=0.0))
        self.assertTrue(fresh["ok"])
        self.assertEqual(fresh["accepted"], 1)
        self.assertEqual(fresh["ignored"], 0)
        self.assertEqual(self.service.status()["accepted_sequence"], 5)


if __name__ == "__main__":
    unittest.main()
