from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from brain.decisions import BrainDecision
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.environment import EnvironmentService
from backend.environment_routes import EnvironmentRouteDependencies, register_environment_routes
from backend.vision import VisionService


class _Clock:
    def __init__(self) -> None:
        self.value = 1_000.0

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class EnvironmentRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.config = {
            "environment": {
                "mode": "active",
                "intensity": "balanced",
                "metadata_enabled": True,
                "screen_context_enabled": True,
                "away_threshold_minutes": 1,
                "initiative_cooldown_minutes": 1,
                "minimum_confidence": 0.5,
                "quiet_hours_start": 22,
                "quiet_hours_end": 8,
            },
            "vision": {"enabled": False, "context_ttl_sec": 60},
            "brain": {
                "provider": "openai_compatible",
                "model_name": "friend",
                "web_search_enabled": True,
            },
            "human_ops": {"playwright_profile": "Personal"},
        }
        self.environment = EnvironmentService(
            self.config["environment"],
            now=self.clock.now,
            local_hour=lambda _: 12,
            id_factory=lambda: "candidate-1",
        )
        self.vision = VisionService(now=self.clock.now)
        self.brain_calls = []
        self.brain_configs = []

        async def run_brain(_config, **kwargs):
            self.brain_configs.append(dict(_config))
            self.brain_calls.append(kwargs)
            return BrainDecision.say("你回来啦。刚才休息得还好吗？")

        app = FastAPI()
        register_environment_routes(
            app,
            EnvironmentRouteDependencies(
                environment_service=self.environment,
                vision_service=self.vision,
                normalize_private_config=lambda: self.config,
                run_brain_turn=run_brain,
                decision_from_completion=lambda completion: completion,
                proactive_conversation_history=lambda _session, _opportunity: [
                    {"role": "system", "content": "用户不喜欢被催促。"}
                ],
            ),
        )
        self.client = TestClient(app)
        self.token_patch = mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "local-token"})
        self.token_patch.start()
        self.headers = {"X-Ipet-Local-Token": "local-token"}

    def tearDown(self) -> None:
        self.token_patch.stop()
        self.client.close()

    def _queue_return(self) -> None:
        first = self.client.post(
            "/api/environment/events",
            headers=self.headers,
            json={"foreground_app": "Editor", "idle_seconds": 180},
        )
        self.assertEqual(first.status_code, 200)
        self.clock.advance(5)
        second = self.client.post(
            "/api/environment/events",
            headers=self.headers,
            json={"foreground_app": "Editor", "idle_seconds": 0},
        )
        self.assertEqual(second.status_code, 200)

    def test_event_ingestion_requires_local_token(self) -> None:
        response = self.client.post("/api/environment/events", json={"foreground_app": "Editor"})
        self.assertEqual(response.status_code, 403)

    def test_status_rejects_cross_site_browser_origin(self) -> None:
        response = self.client.get("/api/environment/status", headers={"Origin": "https://example.com"})
        self.assertEqual(response.status_code, 403)

    def test_pulse_generates_safe_delivery_and_accepts_feedback(self) -> None:
        self._queue_return()
        response = self.client.post(
            "/api/environment/pulse",
            json={
                "session_id": "topic",
                "memory_mode": "persistent",
                "client_state": {"chat_busy": False},
            },
        )
        self.assertEqual(response.status_code, 200)
        delivery = response.json()["delivery"]
        self.assertEqual(delivery["id"], "candidate-1")
        self.assertIn("回来", delivery["text"])
        self.assertIn("不可信数据", self.brain_calls[0]["user_text"])
        self.assertEqual(self.brain_calls[0]["conversation_history"][0]["role"], "system")
        self.assertEqual(self.brain_calls[0]["prompt_profile"], "proactive")
        self.assertFalse(self.brain_configs[0]["web_search_enabled"])
        self.assertNotIn("playwright_profile", self.brain_configs[0])
        feedback = self.client.post(
            "/api/environment/feedback",
            json={"id": delivery["id"], "outcome": "delivered", "session_id": "topic"},
        )
        self.assertTrue(feedback.json()["ok"])

    def test_temporary_or_busy_client_does_not_claim(self) -> None:
        self._queue_return()
        temporary = self.client.post(
            "/api/environment/pulse",
            json={"session_id": "topic", "memory_mode": "temporary", "client_state": {}},
        )
        self.assertIsNone(temporary.json()["delivery"])
        busy = self.client.post(
            "/api/environment/pulse",
            json={"session_id": "topic", "memory_mode": "persistent", "client_state": {"asr_busy": True}},
        )
        self.assertIsNone(busy.json()["delivery"])
        self.assertEqual(self.brain_calls, [])

    def test_sensitive_frame_is_blocked_before_vision_storage(self) -> None:
        safe = self.client.post(
            "/api/vision/frame",
            headers=self.headers,
            json={
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,AA==",
                "foreground_app": "Editor",
            },
        )
        self.assertEqual(safe.status_code, 200)
        self.assertTrue(self.vision.context()["available"])
        blocked = self.client.post(
            "/api/vision/frame",
            headers=self.headers,
            json={
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,SECRET",
                "foreground_app": "1Password",
                "window_title": "Passwords",
            },
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertTrue(blocked.json()["privacy_blocked"])
        self.assertFalse(self.vision.context()["available"])

    def test_enabled_analyzer_enriches_frame_in_background(self) -> None:
        self.config["vision"]["analyzer"] = {"enabled": True, "provider": "macos_vision_ocr"}
        enriched = {
            "mime_type": "image/jpeg",
            "data_url": "data:image/jpeg;base64,AA==",
            "foreground_app": "Terminal",
            "observations": [
                {
                    "claim": "31 tests passed",
                    "evidence": "31 tests passed",
                    "region": "terminal",
                    "confidence": 0.9,
                    "source": "test",
                }
            ],
        }
        fake_analyzer = mock.Mock()
        fake_analyzer.enrich_payload.return_value = SimpleNamespace(
            payload=enriched,
            status={"enabled": True, "provider": "test", "status": "ok"},
        )
        with mock.patch("backend.environment_routes.VisionAnalyzer", return_value=fake_analyzer):
            response = self.client.post(
                "/api/vision/frame",
                headers=self.headers,
                json={
                    "mime_type": "image/jpeg",
                    "data_url": "data:image/jpeg;base64,AA==",
                    "foreground_app": "Terminal",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.vision.context()["observations"][0]["claim"], "31 tests passed")

    def test_vision_context_requires_token(self) -> None:
        self.assertEqual(self.client.get("/api/vision/context").status_code, 403)


if __name__ == "__main__":
    unittest.main()
