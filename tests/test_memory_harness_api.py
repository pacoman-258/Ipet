from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend.conversation_store import ConversationStore
from backend.ipet_memory_store import IpetMemoryStore


class _FakeRuntimeClient:
    runtime_id = "astrbot"

    def __init__(self, *, events=None) -> None:
        self.events = events or [("token", {"delta": "收到"}), ("done", {"text": "收到"})]
        self.stream_payloads: list[dict] = []
        self.delete_calls: list[str] = []
        self.request_json_calls: list[tuple[str, str, dict | None]] = []

    async def stream_sse(self, path, payload):
        self.stream_payloads.append({"path": path, "payload": dict(payload)})
        for event, data in self.events:
            if event == "sleep":
                await asyncio.sleep(float(data))
                continue
            yield event, dict(data)

    async def delete_runtime_session(self, session_id):
        self.delete_calls.append(session_id)
        return {"ok": True}

    async def request_json(self, method, path, *, json_payload=None):
        self.request_json_calls.append((method, path, json_payload))
        raise AssertionError("topic APIs should use Ipet local ConversationStore")


def _parse_sse_events(raw_text: str):
    events = []
    for chunk in str(raw_text or "").split("\n\n"):
        event_name = ""
        data_lines = []
        for line in chunk.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if event_name and data_lines:
            events.append((event_name, json.loads("".join(data_lines))))
    return events


class MemoryHarnessApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.conversations = ConversationStore(self.root / "ipet_conversations")
        self.memories = IpetMemoryStore(self.root / "ipet_memory")
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()
        self._tmp.cleanup()

    def test_persistent_stream_saves_local_conversation_and_deletes_runtime_session(self) -> None:
        runtime = _FakeRuntimeClient()
        with self._patched_app(runtime):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "session_id": "api-persistent",
                    "text": "请记住：我喜欢 unittest。",
                    "expression_mode": False,
                    "memory_mode": "persistent",
                },
            )

        self.assertEqual(resp.status_code, 200)
        events = _parse_sse_events(resp.text)
        self.assertEqual(events[-1][0], "done")
        meta_payloads = [payload for event, payload in events if event == "meta"]
        self.assertEqual(meta_payloads[0]["runtime"], "astrbot")
        self.assertEqual(events[-1][1]["topic_id"], "api-persistent")
        detail = self.conversations.get_topic_detail("api-persistent")
        self.assertIsNotNone(detail)
        self.assertEqual([item["content"] for item in detail["messages"]], ["请记住：我喜欢 unittest。", "收到"])
        self.assertTrue(self.memories.search("unittest"))
        self.assertEqual(runtime.delete_calls, [runtime.stream_payloads[0]["payload"]["session_id"]])
        self.assertTrue(runtime.stream_payloads[0]["payload"]["session_id"].startswith("ipet-temp-"))

    def test_temporary_stream_does_not_write_local_history_or_memory(self) -> None:
        runtime = _FakeRuntimeClient()
        with self._patched_app(runtime):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "session_id": "api-temp",
                    "text": "请记住：这只是临时偏好。",
                    "expression_mode": False,
                    "memory_mode": "temporary",
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self.conversations.get_topic_detail("api-temp"))
        self.assertEqual(self.conversations.list_topics(), [])
        self.assertEqual(self.memories.search("临时偏好"), [])
        self.assertEqual(len(runtime.delete_calls), 1)

    def test_topic_endpoints_use_local_store_without_runtime_request_json(self) -> None:
        runtime = _FakeRuntimeClient()
        self.conversations.append_turn(
            "local-topic",
            turn_id="turn-1",
            user_text="hello",
            assistant_text="world",
            runtime="astrbot",
            runtime_session_id="ipet-temp-turn-1",
        )

        with self._patched_app(runtime):
            create_resp = self.client.post("/api/chat/topics", json={"session_id": "new-local", "title": "新话题"})
            list_resp = self.client.get("/api/chat/topics")
            detail_resp = self.client.get("/api/chat/topics/local-topic")
            delete_resp = self.client.delete("/api/chat/topics/local-topic")

        self.assertEqual(create_resp.status_code, 200)
        self.assertEqual(create_resp.json()["runtime"], "ipet")
        self.assertEqual(create_resp.json()["topic"]["runtime"], "ipet")
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list_resp.json()["runtime"], "ipet")
        self.assertEqual(list_resp.json()["topics"][0]["topic_id"], "local-topic")
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.json()["runtime"], "ipet")
        self.assertEqual([item["content"] for item in detail_resp.json()["messages"]], ["hello", "world"])
        self.assertEqual(delete_resp.status_code, 200)
        self.assertEqual(delete_resp.json()["runtime"], "ipet")
        self.assertEqual(delete_resp.json()["deleted_topic_id"], "local-topic")
        self.assertEqual(runtime.request_json_calls, [])

    def _patched_app(self, runtime):
        return mock.patch.multiple(
            backend_app,
            _get_runtime_client=mock.Mock(return_value=runtime),
            _get_conversation_store=mock.Mock(return_value=self.conversations),
            _get_ipet_memory_store=mock.Mock(return_value=self.memories),
            _load_settings_config=mock.Mock(return_value={"vision": {"enabled": False}}),
        )
