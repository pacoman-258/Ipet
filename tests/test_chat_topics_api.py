from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend.chat_topics import DEFAULT_TOPIC_TITLE, TopicStore


def _sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name = "message"
    data_lines: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            if data_lines:
                events.append((event_name, json.loads("\n".join(data_lines))))
            event_name = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
    if data_lines:
        events.append((event_name, json.loads("\n".join(data_lines))))
    return events


class ChatTopicsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(__file__).resolve().parent / ".tmp_chat_topics_api" / f"tmp_{uuid4().hex}"
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self.store = TopicStore(self.tmp_root / "chat_topics")
        self.topic_patch = mock.patch.object(backend_app, "TOPIC_STORE", self.store)
        self.topic_patch.start()
        self.config_patch = mock.patch.object(backend_app, "CONFIG_PATH", self.tmp_root / "pet_config.json")
        self.config_patch.start()
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()
        self.config_patch.stop()
        self.topic_patch.stop()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_create_list_detail_and_delete_topic_use_local_store(self) -> None:
        create_resp = self.client.post("/api/chat/topics", json={"topic_id": "demo-topic"})

        self.assertEqual(create_resp.status_code, 200)
        created = create_resp.json()
        self.assertEqual(created["topic_id"], "demo-topic")
        self.assertEqual(created["topic"]["title"], DEFAULT_TOPIC_TITLE)
        self.assertEqual(self.client.get("/api/chat/topics").json()["topics"], [])

        self.store.append_exchange("demo-topic", user_text="hello", assistant_text="world")
        list_resp = self.client.get("/api/chat/topics")
        detail_resp = self.client.get("/api/chat/topics/demo-topic")

        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual([item["topic_id"] for item in list_resp.json()["topics"]], ["demo-topic"])
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual([item["content"] for item in detail_resp.json()["messages"]], ["hello", "world"])

        delete_resp = self.client.delete("/api/chat/topics/demo-topic")

        self.assertEqual(delete_resp.status_code, 200)
        self.assertTrue(delete_resp.json()["deleted"])
        self.assertEqual(self.client.get("/api/chat/topics/demo-topic").status_code, 404)

    def test_create_topic_can_return_ephemeral_draft(self) -> None:
        resp = self.client.post("/api/chat/topics", json={"topic_id": "draft-topic", "persisted": False})

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertFalse(payload["persisted"])
        self.assertFalse((self.tmp_root / "chat_topics" / "draft-topic").exists())
        self.assertEqual(self.client.get("/api/chat/topics/draft-topic").status_code, 404)

    def test_chat_stream_persists_first_exchange_to_topic(self) -> None:
        completion = SimpleNamespace(text="fresh answer", provider="openai_compatible", model="neo-model")
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(return_value=completion)):
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"session_id": "demo-topic", "text": "first turn"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        events = _sse_events(body)
        self.assertIn("done", [name for name, _ in events])
        detail = self.store.get_topic_detail("demo-topic") or {}
        self.assertEqual([item["content"] for item in detail["messages"]], ["first turn", "fresh answer"])
        self.assertEqual([item["topic_id"] for item in self.client.get("/api/chat/topics").json()["topics"]], ["demo-topic"])

    def test_legacy_memory_decision_endpoint_is_not_registered(self) -> None:
        self.store.create_topic(topic_id="demo-topic")
        self.store.append_exchange("demo-topic", user_text="hello", assistant_text="world")

        resp = self.client.post(
            "/api/chat/memory/decision",
            json={
                "topic_id": "demo-topic",
                "action": "save",
                "candidate": {
                    "marker": "explicit:1",
                    "title": "Demo",
                    "content": "old memory note",
                },
            },
        )

        self.assertEqual(resp.status_code, 404)
        meta = self.store.load_meta("demo-topic") or {}
        self.assertIsNone(meta["last_long_term_memory_saved_marker"])
        self.assertIsNone(meta["last_long_term_memory_dismissed_marker"])


if __name__ == "__main__":
    unittest.main()
