from __future__ import annotations

import unittest
from pathlib import Path

from backend.agent_graph import AgentGraphRuntime, ApprovalDecision, GraphDependencies
from backend.agent_orchestrator import ToolExecution, ToolIntent, TurnDecision


class _FakeBridge:
    def list_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]


class AgentGraphRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_continuation_decision_uses_decision_messages_but_final_reply_keeps_prompt_messages(self) -> None:
        decide_calls: list[list[dict[str, object]]] = []

        async def fake_decide_turn(**kwargs):
            messages = list(kwargs.get("messages") or [])
            decide_calls.append(messages)
            if len(decide_calls) == 1:
                return TurnDecision(
                    needs_tool=True,
                    thought_summary="Need a tool first",
                    action_message="I should read the file first",
                    tool_calls=[ToolIntent(name="read_file", arguments={"path": "story.txt"})],
                )
            return TurnDecision(
                needs_tool=False,
                thought_summary="Enough info now",
                action_message="",
                tool_calls=[],
            )

        def fake_execute_tool_calls(**_kwargs):
            return [
                ToolExecution(
                    name="read_file",
                    arguments={"path": "story.txt"},
                    ok=True,
                    summary="read ok",
                    payload='{"ok": true, "result_preview": "demo"}',
                )
            ]

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=fake_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
        )

        checkpoint_path = Path.cwd() / "test_agent_graph_state_context.pkl"
        try:
            checkpoint_path.unlink()
        except FileNotFoundError:
            pass
        try:
            runtime = AgentGraphRuntime(
                dependency_provider=lambda: deps,
                checkpoint_path=checkpoint_path,
            )
            outcome = await runtime.start_turn(
                {
                    "turn_id": "turn-context",
                    "session_id": "default",
                    "memory_window": 10,
                    "model": "demo",
                    "llm_provider": "ollama",
                    "api_base_url": "",
                    "api_key": "",
                    "system_prompt": "",
                    "expression_mode": False,
                    "expression_output_format": "ndjson_v1",
                    "available_expressions": [],
                    "tools_enabled": True,
                    "react_enabled": True,
                    "max_reasoning_steps": 2,
                    "reasoning_step": 1,
                    "user_text": "read story.txt and then answer",
                    "working_messages": [{"role": "user", "content": "read story.txt and then answer"}],
                    "decision_messages": [{"role": "user", "content": "read story.txt and then answer"}],
                    "prompt_messages": [
                        {"role": "system", "content": "ROLEPLAY_PROMPT"},
                        {"role": "user", "content": "read story.txt and then answer"},
                    ],
                }
            )

            self.assertTrue(outcome.is_pending)
            resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-context", approved=True))

            self.assertFalse(resumed.is_pending)
            self.assertGreaterEqual(len(decide_calls), 2)
            second_messages = decide_calls[-1]
            self.assertEqual(second_messages[0]["role"], "user")
            self.assertNotIn(
                "ROLEPLAY_PROMPT",
                "\n".join(str(item.get("content") or "") for item in second_messages),
            )
            self.assertEqual(resumed.final_messages[0]["content"], "ROLEPLAY_PROMPT")
            self.assertIn(
                "read_file",
                "\n".join(str(item.get("content") or "") for item in resumed.final_messages),
            )
        finally:
            try:
                checkpoint_path.unlink()
            except FileNotFoundError:
                pass

    async def test_interrupt_state_persists_across_runtime_recreation(self) -> None:
        decide_calls: list[str] = []

        async def fake_decide_turn(**_kwargs):
            decide_calls.append("decide")
            return TurnDecision(
                needs_tool=True,
                thought_summary="Need a tool first",
                action_message="I should read the file first",
                tool_calls=[ToolIntent(name="read_file", arguments={"path": "story.txt"})],
            )

        def fake_execute_tool_calls(**_kwargs):
            return [
                ToolExecution(
                    name="read_file",
                    arguments={"path": "story.txt"},
                    ok=True,
                    summary="read ok",
                    payload='{"ok": true, "result_preview": "demo"}',
                )
            ]

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=fake_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
        )

        checkpoint_path = Path.cwd() / "test_agent_graph_state.pkl"
        try:
            checkpoint_path.unlink()
        except FileNotFoundError:
            pass
        try:
            runtime = AgentGraphRuntime(
                dependency_provider=lambda: deps,
                checkpoint_path=checkpoint_path,
            )
            outcome = await runtime.start_turn(
                {
                    "turn_id": "turn-persist",
                    "session_id": "default",
                    "memory_window": 10,
                    "model": "demo",
                    "llm_provider": "ollama",
                    "api_base_url": "",
                    "api_key": "",
                    "system_prompt": "",
                    "expression_mode": False,
                    "expression_output_format": "ndjson_v1",
                    "available_expressions": [],
                    "tools_enabled": True,
                    "react_enabled": True,
                    "max_reasoning_steps": 1,
                    "reasoning_step": 1,
                    "user_text": "read story.txt",
                    "working_messages": [{"role": "user", "content": "read story.txt"}],
                    "prompt_messages": [{"role": "user", "content": "read story.txt"}],
                }
            )

            self.assertTrue(outcome.is_pending)
            self.assertEqual(runtime.pending_turn_ids(), ["turn-persist"])
            self.assertTrue(checkpoint_path.exists())

            runtime = AgentGraphRuntime(
                dependency_provider=lambda: deps,
                checkpoint_path=checkpoint_path,
            )
            resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-persist", approved=True))

            self.assertFalse(resumed.is_pending)
            self.assertTrue(resumed.final_messages)
            self.assertEqual(decide_calls, ["decide"])
        finally:
            try:
                checkpoint_path.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    unittest.main()
