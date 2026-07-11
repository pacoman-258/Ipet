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

    def test_create_topic_respects_disabled_conversation_saving(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps({"memory": {"conversation_saving": False}}),
            encoding="utf-8",
        )

        resp = self.client.post("/api/chat/topics", json={"topic_id": "privacy-topic", "persisted": True})

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["persisted"])
        self.assertFalse((self.tmp_root / "chat_topics" / "privacy-topic").exists())
        self.assertEqual(self.client.get("/api/chat/topics/privacy-topic").status_code, 404)

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

    def test_chat_stream_passes_persisted_topic_history_to_second_turn(self) -> None:
        completions = [
            SimpleNamespace(text="first answer", provider="openai_compatible", model="neo-model"),
            SimpleNamespace(text="second answer", provider="openai_compatible", model="neo-model"),
        ]
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

        run_mock = mock.AsyncMock(side_effect=completions)
        with mock.patch.object(backend_app, "run_brain_turn", new=run_mock):
            for text in ("first turn", "second turn"):
                with self.client.stream(
                    "POST",
                    "/api/chat/stream",
                    json={"session_id": "history-topic", "text": text, "memory_mode": "persistent"},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    resp.read()

        self.assertNotIn("conversation_history", run_mock.call_args_list[0].kwargs)
        self.assertEqual(
            run_mock.call_args_list[1].kwargs["conversation_history"],
            [
                {"role": "user", "content": "first turn"},
                {"role": "assistant", "content": "first answer"},
            ],
        )

    def test_chat_stream_temporary_mode_keeps_context_without_writing_topic(self) -> None:
        session_id = f"temporary-{uuid4().hex}"
        completions = [
            SimpleNamespace(text="temporary one", provider="openai_compatible", model="neo-model"),
            SimpleNamespace(text="temporary two", provider="openai_compatible", model="neo-model"),
        ]
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

        run_mock = mock.AsyncMock(side_effect=completions)
        last_events: list[tuple[str, dict]] = []
        with mock.patch.object(backend_app, "run_brain_turn", new=run_mock):
            for text in ("temporary first", "temporary second"):
                with self.client.stream(
                    "POST",
                    "/api/chat/stream",
                    json={"session_id": session_id, "text": text, "memory_mode": "temporary"},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    last_events = _sse_events(resp.read().decode("utf-8"))

        self.assertIsNone(self.store.get_topic_detail(session_id))
        self.assertFalse((self.tmp_root / "chat_topics" / session_id).exists())
        self.assertEqual(
            run_mock.call_args_list[1].kwargs["conversation_history"],
            [
                {"role": "user", "content": "temporary first"},
                {"role": "assistant", "content": "temporary one"},
            ],
        )
        meta = next(payload for event, payload in last_events if event == "meta")
        done = next(payload for event, payload in last_events if event == "done")
        self.assertEqual(meta["memory_mode"], "temporary")
        self.assertEqual(done["memory_mode"], "temporary")

    def test_disabling_conversation_saving_forces_server_side_temporary_mode(self) -> None:
        session_id = f"saving-disabled-{uuid4().hex}"
        completion = SimpleNamespace(text="not persisted", provider="openai_compatible", model="neo-model")
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    },
                    "memory": {"conversation_saving": False},
                }
            ),
            encoding="utf-8",
        )

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(return_value=completion)):
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"session_id": session_id, "text": "do not save", "memory_mode": "persistent"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                events = _sse_events(resp.read().decode("utf-8"))

        self.assertIsNone(self.store.get_topic_detail(session_id))
        self.assertEqual(next(payload for event, payload in events if event == "meta")["memory_mode"], "temporary")
        self.assertEqual(next(payload for event, payload in events if event == "done")["memory_mode"], "temporary")

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
