from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app


class FakeHermesClient:
    def __init__(self, events=None, payload=None):
        self.events = events or []
        self.payload = payload or {}
        self.requests = []

    async def status(self):
        return {"ok": True, "available": True, "configured": True, "detail": "", "config": {}}

    async def request_json(self, method, path, *, json_payload=None):
        self.requests.append((method, path, json_payload))
        return dict(self.payload)

    async def stream_sse(self, path, payload):
        self.requests.append(("POST", path, payload))
        for event, data in self.events:
            yield event, data


class HermesProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_hermes_status_reports_disabled_without_network(self) -> None:
        with mock.patch.object(backend_app, "_load_full_config", return_value={"hermes": {"enabled": False}}):
            resp = self.client.get("/api/hermes/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["ok"])
        self.assertFalse(data["configured"])
        self.assertIn("disabled", data["detail"])

    def test_chat_stream_proxies_fake_hermes_sse_shape(self) -> None:
        fake = FakeHermesClient(
            events=[
                ("meta", {"runtime": "hermes", "topic_id": "default"}),
                ("phase", {"phase": "thought", "text": "thinking", "speaker": "pet"}),
                ("token", {"delta": "hello"}),
                ("done", {"text": "hello", "topic_id": "default"}),
            ]
        )
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_get_hermes_client", return_value=fake
        ), mock.patch.object(
            backend_app, "_get_agent_graph_runtime", side_effect=AssertionError("legacy runtime should not be used")
        ):
            resp = self.client.post("/api/chat/stream", json={"text": "hi", "expression_mode": False})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("event: meta", resp.text)
        self.assertIn("event: phase", resp.text)
        self.assertIn("event: token", resp.text)
        self.assertIn("event: done", resp.text)
        self.assertEqual(fake.requests[0][1], "/api/chat/stream")

    def test_chat_stream_returns_clear_error_when_hermes_disabled(self) -> None:
        with mock.patch.object(backend_app, "_load_full_config", return_value={"hermes": {"enabled": False}}):
            resp = self.client.post("/api/chat/stream", json={"text": "hi", "expression_mode": False})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("event: error", resp.text)
        self.assertIn("Hermes Agent is disabled", resp.text)

    def test_approval_proxies_fake_hermes(self) -> None:
        fake = FakeHermesClient(events=[("phase", {"phase": "action", "text": "approved"}), ("done", {"text": "ok"})])
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_get_hermes_client", return_value=fake
        ), mock.patch.object(
            backend_app, "_get_agent_graph_runtime", side_effect=AssertionError("legacy runtime should not be used")
        ):
            resp = self.client.post("/api/chat/approval", json={"turn_id": "turn-1", "approved": True})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("event: phase", resp.text)
        self.assertIn("event: done", resp.text)
        self.assertEqual(fake.requests[0][1], "/api/chat/approval")

    def test_skills_and_mcp_lists_do_not_read_legacy_when_hermes_unavailable(self) -> None:
        with mock.patch.object(backend_app, "_get_skill_manager", side_effect=AssertionError("legacy skills read")), mock.patch.object(
            backend_app, "_get_mcp_manager", side_effect=AssertionError("legacy mcp read")
        ), mock.patch.object(backend_app, "_load_full_config", return_value={"hermes": {"enabled": False}}):
            skills_resp = self.client.get("/api/skills")
            mcp_resp = self.client.get("/api/mcp/servers")
        self.assertEqual(skills_resp.status_code, 200)
        self.assertEqual(mcp_resp.status_code, 200)
        self.assertFalse(skills_resp.json()["ok"])
        self.assertFalse(mcp_resp.json()["ok"])
        self.assertEqual(skills_resp.json()["skills"], [])
        self.assertEqual(mcp_resp.json()["servers"], [])

    def test_chat_topics_proxy_to_hermes_sessions(self) -> None:
        fake = FakeHermesClient(payload={"ok": True, "topics": [{"topic_id": "work"}]})
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_get_hermes_client", return_value=fake
        ), mock.patch.object(
            backend_app, "_get_chat_topic_store", side_effect=AssertionError("local topic store should not be used")
        ):
            list_resp = self.client.get("/api/chat/topics")
            create_resp = self.client.post("/api/chat/topics", json={"session_id": "work"})
            detail_resp = self.client.get("/api/chat/topics/work")
            delete_resp = self.client.delete("/api/chat/topics/work")

        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(create_resp.status_code, 200)
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(delete_resp.status_code, 200)
        self.assertEqual(fake.requests[0], ("GET", "/api/chat/topics", None))
        self.assertEqual(fake.requests[1], ("POST", "/api/chat/topics", {"session_id": "work"}))
        self.assertEqual(fake.requests[2], ("GET", "/api/chat/topics/work", None))
        self.assertEqual(fake.requests[3], ("DELETE", "/api/chat/topics/work", None))


if __name__ == "__main__":
    unittest.main()
