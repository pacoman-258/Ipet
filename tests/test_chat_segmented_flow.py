from __future__ import annotations

import json
import unittest
from pathlib import Path
import shutil
from uuid import uuid4
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend.agent_orchestrator import ToolExecution, ToolIntent, TurnDecision
from backend.chat_topics import TopicStore


class ChatSegmentedFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        backend_app.SESSION_STORE.clear()
        backend_app.PENDING_CHAT_TURNS.clear()
        backend_app._reset_agent_graph_runtime()
        self.topic_tmp = Path(__file__).resolve().parent / ".tmp_chat_segmented_flow" / f"tmp_{uuid4().hex}"
        self.topic_tmp.mkdir(parents=True, exist_ok=True)
        backend_app._CHAT_TOPIC_STORE = TopicStore(self.topic_tmp / "chat_topics")
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()
        backend_app.SESSION_STORE.clear()
        backend_app.PENDING_CHAT_TURNS.clear()
        backend_app._reset_agent_graph_runtime()
        shutil.rmtree(self.topic_tmp, ignore_errors=True)

    def _pending_turn_id(self) -> str:
        runtime = backend_app._get_agent_graph_runtime()
        pending = runtime.pending_turn_ids()
        self.assertEqual(len(pending), 1)
        return pending[0]

    def test_chat_stream_pauses_for_tool_approval(self) -> None:
        async def fake_decide_turn(**_kwargs):
            return TurnDecision(
                needs_tool=True,
                thought_summary="Need a tool first",
                action_message="I should read test.txt first",
                tool_calls=[ToolIntent(name="read_file", arguments={"path": "test.txt"})],
            )

        fake_bridge = mock.Mock()
        fake_bridge.list_tools.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]

        with mock.patch.object(backend_app, "decide_turn", fake_decide_turn), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "Read test.txt",
                    "model": "demo",
                    "expression_mode": False,
                    "react_enabled": True,
                    "router_enabled": False,
                    "system_prompt": '{"character":{"name_cn":"Demo"}}',
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("event: phase", body)
        self.assertIn('"phase": "thought"', body)
        self.assertIn("event: approval_required", body)
        self.assertNotIn("event: done", body)
        self.assertEqual(len(backend_app._get_agent_graph_runtime().pending_turn_ids()), 1)

    def test_chat_approval_continues_and_finishes(self) -> None:
        async def fake_decide_turn(**_kwargs):
            return TurnDecision(
                needs_tool=True,
                thought_summary="Need a tool first",
                action_message="I should read test.txt first",
                tool_calls=[ToolIntent(name="read_file", arguments={"path": "test.txt"})],
            )

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "Final answer ready."}

        fake_bridge = mock.Mock()
        fake_bridge.list_tools.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]

        with mock.patch.object(backend_app, "decide_turn", fake_decide_turn), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge):
            self.client.post(
                "/api/chat/stream",
                json={
                    "text": "Read test.txt",
                    "model": "demo",
                    "expression_mode": False,
                    "react_enabled": True,
                    "max_reasoning_steps": 1,
                    "router_enabled": False,
                    "system_prompt": '{"character":{"name_cn":"Demo"}}',
                },
            )

        turn_id = self._pending_turn_id()
        with mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=mock.Mock()), mock.patch.object(
            backend_app,
            "execute_tool_calls",
            return_value=[
                ToolExecution(
                    name="read_file",
                    arguments={"path": "test.txt"},
                    ok=True,
                    summary="read ok",
                    payload=json.dumps({"ok": True, "result_preview": "demo"}, ensure_ascii=False),
                )
            ],
        ), mock.patch.object(backend_app, "stream_final_reply", fake_stream_final_reply):
            resp = self.client.post("/api/chat/approval", json={"turn_id": turn_id, "approved": True})

        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("event: phase", body)
        self.assertIn('"phase": "action"', body)
        self.assertIn("event: token", body)
        self.assertIn("event: done", body)
        self.assertIn("Final answer ready.", body)
        self.assertIn("default", backend_app.SESSION_STORE)
        self.assertEqual(backend_app._get_agent_graph_runtime().pending_turn_ids(), [])

    def test_chat_approval_can_pause_again_for_second_tool_round(self) -> None:
        async def fake_first_decide_turn(**_kwargs):
            return TurnDecision(
                needs_tool=True,
                thought_summary="Open the page first",
                action_message="I should open the page first",
                tool_calls=[
                    ToolIntent(
                        name="playwright_mcp.browser_navigate",
                        arguments={"url": "https://www.bilibili.com"},
                    )
                ],
            )

        async def fake_second_decide_turn(**_kwargs):
            return TurnDecision(
                needs_tool=True,
                thought_summary="Now type the query",
                action_message="I should type the search text next",
                tool_calls=[ToolIntent(name="playwright_mcp.browser_type", arguments={"text": "genshin"})],
            )

        fake_bridge = mock.Mock()
        fake_bridge.list_tools.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "playwright_mcp.browser_navigate",
                    "description": "Open a page",
                    "parameters": {"type": "object", "properties": {"url": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "playwright_mcp.browser_type",
                    "description": "Type text",
                    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
                },
            },
        ]

        with mock.patch.object(backend_app, "decide_turn", fake_first_decide_turn), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge):
            self.client.post(
                "/api/chat/stream",
                json={
                    "text": "Open Bilibili and search",
                    "model": "demo",
                    "expression_mode": False,
                    "react_enabled": True,
                    "max_reasoning_steps": 3,
                    "router_enabled": False,
                    "system_prompt": '{"character":{"name_cn":"Demo"}}',
                },
            )

        turn_id = self._pending_turn_id()
        with mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge), mock.patch.object(
            backend_app,
            "execute_tool_calls",
            return_value=[
                ToolExecution(
                    name="playwright_mcp.browser_navigate",
                    arguments={"url": "https://www.bilibili.com"},
                    ok=True,
                    summary="page opened",
                    payload=json.dumps({"ok": True, "result_preview": "opened"}, ensure_ascii=False),
                )
            ],
        ), mock.patch.object(backend_app, "decide_turn", fake_second_decide_turn):
            resp = self.client.post("/api/chat/approval", json={"turn_id": turn_id, "approved": True})

        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("event: phase", body)
        self.assertIn('"phase": "thought"', body)
        self.assertIn("event: approval_required", body)
        self.assertNotIn("event: done", body)
        runtime = backend_app._get_agent_graph_runtime()
        self.assertEqual(runtime.pending_turn_ids(), [turn_id])
        pending_state = runtime.get_state_values(turn_id) or {}
        self.assertEqual(int(pending_state.get("reasoning_step") or 0), 2)
        self.assertEqual(
            pending_state.get("decision", {}).get("tool_calls", [{}])[0].get("name"),
            "playwright_mcp.browser_type",
        )

    def test_chat_approval_rechecks_if_model_prematurely_stops(self) -> None:
        async def fake_first_decide_turn(**_kwargs):
            return TurnDecision(
                needs_tool=True,
                thought_summary="Open the search page first",
                action_message="I should open the search page first",
                tool_calls=[
                    ToolIntent(
                        name="playwright.browser_navigate",
                        arguments={"url": "https://search.bilibili.com/upuser?keyword=test"},
                    )
                ],
            )

        decide_responses = [
            TurnDecision(
                needs_tool=False,
                thought_summary="Maybe it is already done",
                action_message="",
                tool_calls=[],
            ),
            TurnDecision(
                needs_tool=True,
                thought_summary="No, click into the profile next",
                action_message="I should click into the profile next",
                tool_calls=[ToolIntent(name="playwright.browser_click", arguments={"selector": "a.user-link"})],
            ),
        ]

        async def fake_second_decide_turn(**_kwargs):
            return decide_responses.pop(0)

        fake_bridge = mock.Mock()
        fake_bridge.list_tools.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "playwright.browser_navigate",
                    "description": "Open a page",
                    "parameters": {"type": "object", "properties": {"url": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "playwright.browser_click",
                    "description": "Click an element",
                    "parameters": {"type": "object", "properties": {"selector": {"type": "string"}}},
                },
            },
        ]

        with mock.patch.object(backend_app, "decide_turn", fake_first_decide_turn), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge):
            self.client.post(
                "/api/chat/stream",
                json={
                    "text": "Open Bilibili search",
                    "model": "demo",
                    "expression_mode": False,
                    "react_enabled": True,
                    "max_reasoning_steps": 3,
                    "router_enabled": False,
                    "system_prompt": '{"character":{"name_cn":"Demo"}}',
                },
            )

        turn_id = self._pending_turn_id()
        with mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge), mock.patch.object(
            backend_app,
            "execute_tool_calls",
            return_value=[
                ToolExecution(
                    name="playwright.browser_navigate",
                    arguments={"url": "https://search.bilibili.com/upuser?keyword=test"},
                    ok=True,
                    summary="search opened",
                    payload=json.dumps({"ok": True, "result_preview": "opened"}, ensure_ascii=False),
                )
            ],
        ), mock.patch.object(backend_app, "decide_turn", fake_second_decide_turn):
            resp = self.client.post("/api/chat/approval", json={"turn_id": turn_id, "approved": True})

        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("event: approval_required", body)
        self.assertNotIn("event: done", body)
        pending_state = backend_app._get_agent_graph_runtime().get_state_values(turn_id) or {}
        self.assertEqual(
            pending_state.get("decision", {}).get("tool_calls", [{}])[0].get("name"),
            "playwright.browser_click",
        )

    def test_chat_approval_rejected_skips_tools_and_finishes(self) -> None:
        async def fake_decide_turn(**_kwargs):
            return TurnDecision(
                needs_tool=True,
                thought_summary="Need a tool first",
                action_message="I should read test.txt first",
                tool_calls=[ToolIntent(name="read_file", arguments={"path": "test.txt"})],
            )

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "Answering without tools."}

        fake_bridge = mock.Mock()
        fake_bridge.list_tools.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]
        execute_mock = mock.Mock(return_value=[])

        with mock.patch.object(backend_app, "decide_turn", fake_decide_turn), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge):
            self.client.post(
                "/api/chat/stream",
                json={
                    "text": "Read test.txt",
                    "model": "demo",
                    "expression_mode": False,
                    "react_enabled": True,
                    "router_enabled": False,
                    "system_prompt": '{"character":{"name_cn":"Demo"}}',
                },
            )

        turn_id = self._pending_turn_id()
        with mock.patch.object(backend_app, "execute_tool_calls", execute_mock), mock.patch.object(
            backend_app, "stream_final_reply", fake_stream_final_reply
        ):
            resp = self.client.post("/api/chat/approval", json={"turn_id": turn_id, "approved": False})

        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("event: done", body)
        self.assertIn("Answering without tools.", body)
        execute_mock.assert_not_called()
        self.assertEqual(backend_app._get_agent_graph_runtime().pending_turn_ids(), [])

    def test_chat_stream_without_tools_goes_directly_to_done(self) -> None:
        async def fake_decide_turn(**_kwargs):
            return TurnDecision(
                needs_tool=False,
                thought_summary="No tools needed",
                action_message="",
                tool_calls=[],
            )

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": "This is a direct answer."}

        with mock.patch.object(backend_app, "decide_turn", fake_decide_turn), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "stream_final_reply", fake_stream_final_reply):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "Hello",
                    "model": "demo",
                    "expression_mode": False,
                    "react_enabled": True,
                    "router_enabled": False,
                    "system_prompt": '{"character":{"name_cn":"Demo"}}',
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("event: phase", body)
        self.assertIn("event: token", body)
        self.assertIn("event: done", body)
        self.assertNotIn("event: approval_required", body)

    def test_chat_stream_approval_contract_matches_provider_variants(self) -> None:
        async def fake_decide_turn(**_kwargs):
            return TurnDecision(
                needs_tool=True,
                thought_summary="Need a tool first",
                action_message="I should read test.txt first",
                tool_calls=[ToolIntent(name="read_file", arguments={"path": "test.txt"})],
            )

        fake_bridge = mock.Mock()
        fake_bridge.list_tools.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]

        for provider in ("ollama", "openai_compat"):
            backend_app._reset_agent_graph_runtime()
            with self.subTest(provider=provider):
                with mock.patch.object(backend_app, "decide_turn", fake_decide_turn), mock.patch.object(
                    backend_app,
                    "_load_runtime_tooling_config",
                    return_value={"enabled": True, "max_tool_calls_per_turn": 6},
                ), mock.patch.object(backend_app, "_get_mcp_bridge", return_value=fake_bridge):
                    resp = self.client.post(
                        "/api/chat/stream",
                        json={
                            "text": "Read test.txt",
                            "model": "demo",
                            "llm_provider": provider,
                    "expression_mode": False,
                    "react_enabled": True,
                    "router_enabled": False,
                },
                    )

                self.assertEqual(resp.status_code, 200)
                body = resp.text
                self.assertIn("event: approval_required", body)
                self.assertIn(f'"llm_provider": "{provider}"', body)


if __name__ == "__main__":
    unittest.main()
