from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend.conversation_store import ConversationStore
from backend.ipet_memory_store import IpetMemoryStore


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
        self._ipet_store_tmp = tempfile.TemporaryDirectory()
        root = Path(self._ipet_store_tmp.name)
        self._conversation_store = ConversationStore(root / "ipet_conversations")
        self._memory_store = IpetMemoryStore(root / "ipet_memory")
        self._ipet_store_patchers = [
            mock.patch.object(backend_app, "_get_conversation_store", return_value=self._conversation_store),
            mock.patch.object(backend_app, "_get_ipet_memory_store", return_value=self._memory_store),
        ]
        for patcher in self._ipet_store_patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(getattr(self, "_ipet_store_patchers", [])):
            patcher.stop()
        if hasattr(self, "_ipet_store_tmp"):
            self._ipet_store_tmp.cleanup()
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
        config = {
            "runtime": {"active": "hermes", "adapters": {"hermes": {"enabled": False}}},
            "hermes": {"enabled": False},
        }
        with mock.patch.object(backend_app, "_load_full_config", return_value=config):
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

    def test_chat_topics_use_ipet_local_store_with_hermes_runtime(self) -> None:
        fake = FakeHermesClient(payload={"ok": True, "topics": [{"topic_id": "work"}]})
        self._conversation_store.append_turn(
            "work",
            turn_id="turn-1",
            user_text="hello",
            assistant_text="world",
            runtime="hermes",
            runtime_session_id="ipet-temp-turn-1",
        )
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_get_hermes_client", return_value=fake
        ):
            list_resp = self.client.get("/api/chat/topics")
            create_resp = self.client.post("/api/chat/topics", json={"session_id": "new-work"})
            detail_resp = self.client.get("/api/chat/topics/work")
            delete_resp = self.client.delete("/api/chat/topics/work")

        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(create_resp.status_code, 200)
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(delete_resp.status_code, 200)
        self.assertEqual(list_resp.json()["runtime"], "ipet")
        self.assertEqual(list_resp.json()["topics"][0]["topic_id"], "work")
        self.assertEqual(create_resp.json()["runtime"], "ipet")
        self.assertEqual(detail_resp.json()["messages"][0]["content"], "hello")
        self.assertEqual(delete_resp.json()["deleted_topic_id"], "work")
        self.assertEqual(fake.requests, [])


if __name__ == "__main__":
    unittest.main()
