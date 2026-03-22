from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
import shutil
from uuid import uuid4
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend.agent_graph import GraphTurnOutcome
from backend.chat_topics import DEFAULT_TOPIC_TITLE, TopicStore


class _FakeRuntime:
    def __init__(self) -> None:
        self.start_state = None

    async def start_turn(self, state):
        self.start_state = state
        return GraphTurnOutcome(
            turn_id=str(state.get("turn_id") or ""),
            state=state,
            thought_summary="",
            approval_request=None,
            final_messages=list(state.get("prompt_messages") or []),
            trace=[],
        )

    def delete_turn(self, _turn_id: str) -> None:
        return None


class ChatTopicsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        backend_app.SESSION_STORE.clear()
        backend_app.PENDING_CHAT_TURNS.clear()
        backend_app._reset_agent_graph_runtime()
        self.tmp_root = Path(__file__).resolve().parent / ".tmp_chat_topics_api" / f"tmp_{uuid4().hex}"
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self.store = TopicStore(self.tmp_root / "chat_topics")
        self.original_store = backend_app._CHAT_TOPIC_STORE
        backend_app._CHAT_TOPIC_STORE = self.store
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()
        backend_app.SESSION_STORE.clear()
        backend_app.PENDING_CHAT_TURNS.clear()
        backend_app._reset_agent_graph_runtime()
        backend_app._CHAT_TOPIC_STORE = self.original_store
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_create_and_read_topic_endpoints(self) -> None:
        settings = {"chat": {"topic_history": {"enabled": True, "summary_interval_assistant_turns": 10}}}
        with mock.patch.object(backend_app, "_load_settings_config", return_value=settings):
            create_resp = self.client.post("/api/chat/topics", json={})
            self.assertEqual(create_resp.status_code, 200)
            payload = create_resp.json()
            topic_id = payload["topic_id"]
            self.assertTrue(payload["persisted"])
            self.assertEqual(payload["topic"]["title"], DEFAULT_TOPIC_TITLE)

            list_resp = self.client.get("/api/chat/topics")
            detail_resp = self.client.get(f"/api/chat/topics/{topic_id}")

        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(len(list_resp.json()["topics"]), 1)
        self.assertEqual(detail_resp.json()["meta"]["topic_id"], topic_id)
        self.assertEqual(detail_resp.json()["messages"], [])

    def test_create_topic_returns_ephemeral_topic_when_history_disabled(self) -> None:
        settings = {"chat": {"topic_history": {"enabled": False, "summary_interval_assistant_turns": 10}}}
        with mock.patch.object(backend_app, "_load_settings_config", return_value=settings):
            resp = self.client.post("/api/chat/topics", json={})

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertFalse(payload["persisted"])
        self.assertFalse((self.tmp_root / "chat_topics" / payload["topic_id"]).exists())

    def test_chat_stream_uses_topic_summary_context_instead_of_session_store(self) -> None:
        topic_id = "demo-topic"
        self.store.create_topic(topic_id=topic_id)
        self.store.append_exchange(topic_id, user_text="stored user", assistant_text="stored assistant")
        backend_app.SESSION_STORE[topic_id] = [{"role": "assistant", "content": "wrong memory"}]
        runtime = _FakeRuntime()
        settings = {
            "chat": {
                "skills": {"enabled": True, "default_active_ids": []},
                "topic_history": {"enabled": True, "summary_interval_assistant_turns": 10},
            }
        }

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "fresh answer"}

        with mock.patch.object(backend_app, "_get_agent_graph_runtime", return_value=runtime), mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value=settings,
        ), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(
            backend_app,
            "stream_final_reply",
            fake_stream_final_reply,
        ):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "session_id": topic_id,
                    "text": "next turn",
                    "model": "demo",
                    "expression_mode": False,
                    "router_enabled": False,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(runtime.start_state)
        history = runtime.start_state["working_messages"]
        self.assertEqual(history[0]["content"], "stored user")
        self.assertEqual(history[1]["content"], "stored assistant")
        self.assertNotIn("wrong memory", [item.get("content") for item in history])
        self.assertIn(f'"topic_id": "{topic_id}"', resp.text)
        detail = self.store.get_topic_detail(topic_id) or {}
        self.assertEqual([item["content"] for item in detail["messages"]], ["stored user", "stored assistant", "next turn", "fresh answer"])

    def test_resolve_summary_request_config_prefers_router_fields_with_main_fallback(self) -> None:
        settings = {
            "chat": {
                "llm_provider": "main-provider",
                "api_base_url": "http://main.local",
                "api_key": "main-key",
                "model": "main-model",
                "router_llm_provider": "router-provider",
                "router_api_base_url": "http://router.local",
                "router_api_key": "router-key",
                "router_model": "router-model",
            }
        }
        config = backend_app._resolve_summary_request_config(
            settings,
            llm_provider="",
            api_base_url="",
            api_key="",
            model="",
        )
        self.assertEqual(config["llm_provider"], "router-provider")
        self.assertEqual(config["api_base_url"], "http://router.local")
        self.assertEqual(config["api_key"], "router-key")
        self.assertEqual(config["model"], "router-model")

        fallback_settings = {
            "chat": {
                "llm_provider": "main-provider",
                "api_base_url": "http://main.local",
                "api_key": "main-key",
                "model": "main-model",
                "router_llm_provider": "",
                "router_api_base_url": "",
                "router_api_key": "",
                "router_model": "",
            }
        }
        fallback = backend_app._resolve_summary_request_config(
            fallback_settings,
            llm_provider="",
            api_base_url="",
            api_key="",
            model="",
        )
        self.assertEqual(fallback["llm_provider"], "main-provider")
        self.assertEqual(fallback["api_base_url"], "http://main.local")
        self.assertEqual(fallback["api_key"], "main-key")
        self.assertEqual(fallback["model"], "main-model")


    def test_finalize_chat_exchange_rolls_up_mini_and_major_summaries(self) -> None:
        settings = {
            "chat": {
                "topic_history": {"enabled": True, "summary_interval_assistant_turns": 10},
                "llm_provider": "main-provider",
                "api_base_url": "http://main.local",
                "api_key": "main-key",
                "model": "main-model",
                "router_llm_provider": "router-provider",
                "router_api_base_url": "http://router.local",
                "router_api_key": "router-key",
                "router_model": "router-model",
            }
        }

        async def fake_generate_summary(*, kind: str, start_assistant_turn: int, end_assistant_turn: int, **_kwargs):
            return f"{kind}:{start_assistant_turn}-{end_assistant_turn}"

        with mock.patch.object(backend_app, "_generate_topic_summary_text", side_effect=fake_generate_summary):
            for index in range(1, 31):
                asyncio.run(
                    backend_app._finalize_chat_exchange(
                        session_id="demo-topic",
                        user_text=f"user-{index}",
                        assistant_text=f"assistant-{index}",
                        working_messages=[],
                        memory_window=10,
                        settings_config=settings,
                        llm_provider="main-provider",
                        api_base_url="http://main.local",
                        api_key="main-key",
                        model="main-model",
                    )
                )

        detail = self.store.get_topic_detail("demo-topic") or {}
        blocks = detail["summary"]["blocks"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "major_summary")
        self.assertEqual(blocks[0]["content"], "major:1-30")
        self.assertEqual(detail["meta"]["assistant_turn_count"], 30)
        self.assertEqual(detail["meta"]["mini_summary_count"], 3)
        self.assertEqual(detail["meta"]["major_summary_count"], 1)
        model_messages = self.store.build_model_messages("demo-topic")
        self.assertEqual(model_messages[0]["role"], "system")
        self.assertIn("major:1-30", model_messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
