from __future__ import annotations

import asyncio
import shutil
import unittest
from pathlib import Path
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

    def test_create_topic_endpoint_returns_unsaved_draft_until_first_exchange(self) -> None:
        settings = {"chat": {"topic_history": {"enabled": True, "summary_interval_assistant_turns": 10}}}
        with mock.patch.object(backend_app, "_load_settings_config", return_value=settings):
            create_resp = self.client.post("/api/chat/topics", json={})
            self.assertEqual(create_resp.status_code, 200)
            payload = create_resp.json()
            topic_id = payload["topic_id"]
            self.assertFalse(payload["persisted"])
            self.assertEqual(payload["topic"]["title"], DEFAULT_TOPIC_TITLE)

            list_resp = self.client.get("/api/chat/topics")
            detail_resp = self.client.get(f"/api/chat/topics/{topic_id}")

        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list_resp.json()["topics"], [])
        self.assertEqual(detail_resp.status_code, 404)
        self.assertFalse((self.tmp_root / "chat_topics" / topic_id).exists())

    def test_create_topic_returns_ephemeral_topic_when_history_disabled(self) -> None:
        settings = {"chat": {"topic_history": {"enabled": False, "summary_interval_assistant_turns": 10}}}
        with mock.patch.object(backend_app, "_load_settings_config", return_value=settings):
            resp = self.client.post("/api/chat/topics", json={})

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertFalse(payload["persisted"])
        self.assertFalse((self.tmp_root / "chat_topics" / payload["topic_id"]).exists())

    def test_delete_topic_endpoint_removes_persisted_topic(self) -> None:
        self.store.create_topic(topic_id="demo-topic")
        self.store.append_exchange("demo-topic", user_text="hello", assistant_text="world")
        backend_app.SESSION_STORE["demo-topic"] = self.store.load_full_messages("demo-topic")

        resp = self.client.delete("/api/chat/topics/demo-topic")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["deleted"])
        self.assertEqual(payload["topics"], [])
        self.assertNotIn("demo-topic", backend_app.SESSION_STORE)
        self.assertFalse((self.tmp_root / "chat_topics" / "demo-topic").exists())
        self.assertEqual(self.client.get("/api/chat/topics/demo-topic").status_code, 404)

    def test_delete_topic_post_fallback_endpoint_removes_persisted_topic(self) -> None:
        self.store.create_topic(topic_id="demo-topic")
        self.store.append_exchange("demo-topic", user_text="hello", assistant_text="world")

        resp = self.client.post("/api/chat/topics/demo-topic/delete")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["deleted"])
        self.assertEqual(payload["topics"], [])
        self.assertFalse((self.tmp_root / "chat_topics" / "demo-topic").exists())

    def test_chat_stream_persists_draft_topic_after_first_exchange(self) -> None:
        runtime = _FakeRuntime()
        settings = {
            "chat": {
                "skills": {"enabled": True, "default_active_ids": []},
                "topic_history": {"enabled": True, "summary_interval_assistant_turns": 10},
            }
        }

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "fresh answer"}

        with mock.patch.object(backend_app, "_load_settings_config", return_value=settings), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_agent_graph_runtime", return_value=runtime), mock.patch.object(
            backend_app,
            "stream_final_reply",
            fake_stream_final_reply,
        ):
            create_resp = self.client.post("/api/chat/topics", json={})
            topic_id = create_resp.json()["topic_id"]
            self.assertFalse((self.tmp_root / "chat_topics" / topic_id).exists())

            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "session_id": topic_id,
                    "text": "first turn",
                    "model": "demo",
                    "expression_mode": False,
                    "router_enabled": False,
                },
            )
            topics_payload = self.client.get("/api/chat/topics").json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(topics_payload["topics"]), 1)
        detail = self.store.get_topic_detail(topic_id) or {}
        self.assertEqual([item["content"] for item in detail["messages"]], ["first turn", "fresh answer"])

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

    def test_build_chat_request_context_prefers_topic_history_over_long_term_memory(self) -> None:
        topic_id = "demo-topic"
        self.store.create_topic(topic_id=topic_id)
        self.store.append_exchange(topic_id, user_text="stored user 1", assistant_text="stored assistant 1")
        self.store.append_exchange(topic_id, user_text="stored user 2", assistant_text="stored assistant 2")
        raw_config = {
            "chat": {
                "skills": {"enabled": True, "default_active_ids": []},
                "topic_history": {"enabled": True, "summary_interval_assistant_turns": 10},
                "long_term_memory": {
                    "enabled": True,
                    "project": "ipet-default",
                    "read_enabled": True,
                    "write_enabled": True,
                    "ask_before_save": True,
                    "prefer_topic_history": True,
                    "save_from_major_summary": True,
                    "save_on_explicit_request": True,
                },
            }
        }
        req = backend_app.ChatStreamRequest(
            session_id=topic_id,
            text="你还记得我以前说过什么吗？",
            model="demo",
            expression_mode=False,
            router_enabled=False,
        )

        with mock.patch.object(backend_app, "_load_full_config", return_value=raw_config), mock.patch.object(
            backend_app, "_call_basic_memory_tool"
        ) as memory_tool:
            context = backend_app._build_chat_request_context(req, backend_app.PerfTracker())

        self.assertEqual(memory_tool.call_count, 0)
        self.assertEqual(
            [item["content"] for item in context.history_messages],
            ["stored user 1", "stored assistant 1", "stored user 2", "stored assistant 2"],
        )
        self.assertFalse(any(item["content"].startswith("[Long-term Memory]") for item in context.history_messages))

    def test_build_chat_request_context_adds_long_term_memory_message_when_topic_is_short(self) -> None:
        topic_id = "demo-topic"
        self.store.create_topic(topic_id=topic_id)
        self.store.append_exchange(topic_id, user_text="stored user", assistant_text="stored assistant")
        raw_config = {
            "chat": {
                "skills": {"enabled": True, "default_active_ids": []},
                "topic_history": {"enabled": True, "summary_interval_assistant_turns": 10},
                "long_term_memory": {
                    "enabled": True,
                    "project": "ipet-default",
                    "read_enabled": True,
                    "write_enabled": True,
                    "ask_before_save": True,
                    "prefer_topic_history": True,
                    "save_from_major_summary": True,
                    "save_on_explicit_request": True,
                },
            }
        }
        req = backend_app.ChatStreamRequest(
            session_id=topic_id,
            text="你还记得我以前说过什么吗？",
            model="demo",
            expression_mode=False,
            router_enabled=False,
        )
        memory_tool = mock.Mock(
            side_effect=[
                backend_app.ToolResult(
                    ok=True,
                    structured_data={"results": [{"identifier": "note-1", "title": "偏好记录", "excerpt": "喜欢红茶"}]},
                ),
                backend_app.ToolResult(
                    ok=True,
                    structured_data={"content": "# Summary\n喜欢红茶，早上喝。"},
                ),
            ]
        )

        with mock.patch.object(backend_app, "_load_full_config", return_value=raw_config), mock.patch.object(
            backend_app, "_call_basic_memory_tool", memory_tool
        ):
            context = backend_app._build_chat_request_context(req, backend_app.PerfTracker())

        self.assertEqual(memory_tool.call_count, 2)
        self.assertTrue(context.history_messages[-1]["content"].startswith("[Long-term Memory]"))
        self.assertIn("偏好记录", context.history_messages[-1]["content"])
        self.assertIn("喜欢红茶", context.history_messages[-1]["content"])
        self.assertEqual(context.working_messages[-1]["content"], "你还记得我以前说过什么吗？")

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

    def test_finalize_chat_exchange_returns_memory_save_suggestion_for_major_summary(self) -> None:
        settings = {
            "chat": {
                "topic_history": {"enabled": True, "summary_interval_assistant_turns": 10},
                "long_term_memory": {
                    "enabled": True,
                    "project": "ipet-default",
                    "read_enabled": True,
                    "write_enabled": True,
                    "ask_before_save": True,
                    "prefer_topic_history": True,
                    "save_from_major_summary": True,
                    "save_on_explicit_request": True,
                },
            }
        }

        async def fake_generate_summary(*, kind: str, start_assistant_turn: int, end_assistant_turn: int, **_kwargs):
            return f"{kind}:{start_assistant_turn}-{end_assistant_turn}"

        result: dict[str, object] = {}
        with mock.patch.object(backend_app, "_generate_topic_summary_text", side_effect=fake_generate_summary):
            for index in range(1, 31):
                result = asyncio.run(
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

        candidate = result.get("memory_save_suggestion")
        self.assertIsInstance(candidate, dict)
        self.assertEqual(candidate["marker"], "major:30")
        self.assertEqual(candidate["source_kind"], "major")
        self.assertIn("## Source", candidate["content"])
        self.assertIn("major:30", candidate["title"])

    def test_finalize_chat_exchange_returns_memory_save_suggestion_for_explicit_request(self) -> None:
        settings = {
            "chat": {
                "topic_history": {"enabled": True, "summary_interval_assistant_turns": 10},
                "long_term_memory": {
                    "enabled": True,
                    "project": "ipet-default",
                    "read_enabled": True,
                    "write_enabled": True,
                    "ask_before_save": True,
                    "prefer_topic_history": True,
                    "save_from_major_summary": True,
                    "save_on_explicit_request": True,
                },
            }
        }

        result = asyncio.run(
            backend_app._finalize_chat_exchange(
                session_id="demo-topic",
                user_text="请记住这个：我更喜欢红茶，不喜欢太甜。",
                assistant_text="好的，我会记住这点。",
                working_messages=[],
                memory_window=10,
                settings_config=settings,
                llm_provider="main-provider",
                api_base_url="http://main.local",
                api_key="main-key",
                model="main-model",
            )
        )

        candidate = result.get("memory_save_suggestion")
        self.assertIsInstance(candidate, dict)
        self.assertEqual(candidate["marker"], "explicit:1")
        self.assertEqual(candidate["source_kind"], "explicit")
        self.assertIn("我更喜欢红茶", candidate["content"])
        self.assertIn("Preferences / Decisions", candidate["content"])

    def test_memory_decision_dismiss_updates_topic_marker(self) -> None:
        self.store.create_topic(topic_id="demo-topic")
        self.store.append_exchange("demo-topic", user_text="hello", assistant_text="world")

        resp = self.client.post(
            "/api/chat/memory/decision",
            json={
                "topic_id": "demo-topic",
                "action": "dismiss",
                "candidate": {
                    "marker": "major:30",
                    "title": "Demo Topic | major:30",
                    "content": "# Summary\nHello",
                    "source_kind": "major",
                    "start_turn": 1,
                    "end_turn": 30,
                },
            },
        )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["action"], "dismiss")
        meta = self.store.load_meta("demo-topic") or {}
        self.assertEqual(meta["last_long_term_memory_dismissed_marker"], "major:30")

    def test_memory_decision_save_writes_note_and_updates_topic_marker(self) -> None:
        self.store.create_topic(topic_id="demo-topic")
        self.store.append_exchange("demo-topic", user_text="hello", assistant_text="world")
        settings = {
            "chat": {
                "long_term_memory": {
                    "enabled": True,
                    "project": "ipet-default",
                    "read_enabled": True,
                    "write_enabled": True,
                    "ask_before_save": True,
                    "prefer_topic_history": True,
                    "save_from_major_summary": True,
                    "save_on_explicit_request": True,
                }
            }
        }
        memory_tool = mock.Mock(
            side_effect=[
                backend_app.ToolResult(ok=True, structured_data={"results": []}),
                backend_app.ToolResult(ok=True, structured_data={"identifier": "note-1"}),
            ]
        )

        with mock.patch.object(backend_app, "_load_settings_config", return_value=settings), mock.patch.object(
            backend_app, "_call_basic_memory_tool", memory_tool
        ):
            resp = self.client.post(
                "/api/chat/memory/decision",
                json={
                    "topic_id": "demo-topic",
                    "action": "save",
                    "candidate": {
                        "marker": "explicit:1",
                        "title": "Demo Topic | explicit:1",
                        "content": "# Summary\nUser prefers red tea",
                        "source_kind": "explicit",
                        "start_turn": 1,
                        "end_turn": 1,
                    },
                },
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["action"], "save")
        self.assertEqual(memory_tool.call_count, 2)
        self.assertEqual(memory_tool.call_args_list[0].args[0], backend_app.LONG_TERM_MEMORY_TOOL_SEARCH)
        self.assertEqual(memory_tool.call_args_list[1].args[0], backend_app.LONG_TERM_MEMORY_TOOL_WRITE)
        meta = self.store.load_meta("demo-topic") or {}
        self.assertEqual(meta["last_long_term_memory_saved_marker"], "explicit:1")

    def test_chat_stream_emits_memory_save_suggestion_before_done(self) -> None:
        runtime = _FakeRuntime()
        settings = {
            "chat": {
                "skills": {"enabled": True, "default_active_ids": []},
                "topic_history": {"enabled": True, "summary_interval_assistant_turns": 10},
            }
        }

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "fresh answer"}

        suggestion = {
            "marker": "major:30",
            "title": "Demo Topic | major:30",
            "content": "# Summary\nLong-term memory suggestion",
            "source_kind": "major",
            "start_turn": 1,
            "end_turn": 30,
        }

        with mock.patch.object(backend_app, "_load_settings_config", return_value=settings), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_agent_graph_runtime", return_value=runtime), mock.patch.object(
            backend_app,
            "stream_final_reply",
            fake_stream_final_reply,
        ), mock.patch.object(
            backend_app,
            "_finalize_chat_exchange",
            new=mock.AsyncMock(return_value={"memory_save_suggestion": suggestion}),
        ):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "session_id": "demo-topic",
                    "text": "first turn",
                    "model": "demo",
                    "expression_mode": False,
                    "router_enabled": False,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("event: memory_save_suggestion", resp.text)
        self.assertLess(resp.text.index("event: memory_save_suggestion"), resp.text.index("event: done"))


if __name__ == "__main__":
    unittest.main()
