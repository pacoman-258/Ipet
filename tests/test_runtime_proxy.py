from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend.runtime_adapters import _adapt_astrbot_event, _normalize_stream_delta


class _FakeRuntimeClient:
    runtime_id = "astrbot"

    def __init__(self, events=None, payload=None, status=None):
        self.events = events or []
        self.payload = payload or {}
        self.status_payload = status or {
            "ok": True,
            "available": True,
            "configured": True,
            "runtime": "astrbot",
            "detail": "",
        }
        self.requests = []

    async def status(self):
        return dict(self.status_payload)

    async def request_json(self, method, path, *, json_payload=None):
        self.requests.append((method, path, json_payload))
        return dict(self.payload)

    async def stream_sse(self, path, payload):
        self.requests.append(("POST", path, payload))
        for event, data in self.events:
            yield event, data


class RuntimeProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_settings_config_redacts_astrbot_api_key(self) -> None:
        config = {
            "runtime": {
                "active": "astrbot",
                "adapters": {
                    "astrbot": {
                        "enabled": True,
                        "base_url": "http://127.0.0.1:6185",
                        "health_path": "/",
                        "api_key": "abk_secret_123456",
                        "username": "ipet",
                    }
                },
            },
            "chat": {"backend_url": "http://127.0.0.1:8008", "model": "gpt-5.4", "session_id": "default"},
        }
        with mock.patch.object(backend_app, "_load_full_config", return_value=config):
            resp = self.client.get("/api/settings/config")
        self.assertEqual(resp.status_code, 200)
        astrobot = resp.json()["config"]["runtime"]["adapters"]["astrbot"]
        self.assertEqual(astrobot["api_key"], "")
        self.assertTrue(astrobot["api_key_set"])
        self.assertEqual(astrobot["api_key_action"], "keep")
        self.assertIn("...", astrobot["api_key_preview"])

    def test_runtime_status_uses_active_runtime_client(self) -> None:
        fake = _FakeRuntimeClient(status={"ok": True, "available": True, "runtime": "astrbot"})
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake):
            resp = self.client.get("/api/runtime/status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["runtime"], "astrbot")
        self.assertTrue(resp.json()["available"])

    def test_chat_stream_proxies_active_runtime_sse_shape(self) -> None:
        fake = _FakeRuntimeClient(
            events=[
                ("meta", {"runtime": "astrbot", "topic_id": "default"}),
                ("token", {"delta": "hello"}),
                ("done", {"text": "hello", "topic_id": "default"}),
            ]
        )
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_get_agent_graph_runtime", side_effect=AssertionError("legacy runtime should not be used")
        ):
            resp = self.client.post("/api/chat/stream", json={"text": "hi", "expression_mode": False})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("event: meta", resp.text)
        self.assertIn("event: token", resp.text)
        self.assertIn("event: done", resp.text)
        self.assertEqual(fake.requests[0][1], "/api/chat/stream")

    def test_astrbot_topics_proxy_uses_active_runtime(self) -> None:
        fake = _FakeRuntimeClient(payload={"ok": True, "runtime": "astrbot", "topics": [{"topic_id": "qq"}]})
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_get_chat_topic_store", side_effect=AssertionError("local topic store should not be used")
        ):
            resp = self.client.get("/api/chat/topics")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["runtime"], "astrbot")
        self.assertEqual(fake.requests[0], ("GET", "/api/chat/topics", None))

    def test_astrbot_cumulative_content_is_trimmed_to_delta(self) -> None:
        self.assertEqual(_normalize_stream_delta("", "你好", cumulative=True), "你好")
        self.assertEqual(_normalize_stream_delta("你好", "你好，很高兴", cumulative=True), "，很高兴")
        self.assertEqual(_normalize_stream_delta("你好，很高兴", "你好，很高兴", cumulative=True), "")

    def test_astrbot_empty_progress_event_can_be_filtered(self) -> None:
        event, payload = _adapt_astrbot_event("message", {"type": "progress"}, session_id="default")
        self.assertEqual(event, "phase")
        self.assertEqual(payload["text"], "")


if __name__ == "__main__":
    unittest.main()
