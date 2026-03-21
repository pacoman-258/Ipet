from __future__ import annotations

import unittest
from pathlib import Path

from backend.agent_graph import AgentGraphRuntime, ApprovalDecision, GraphDependencies
from backend.agent_orchestrator import ToolExecution, ToolIntent, TurnDecision, _route_from_assistant_message


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

    async def test_failed_tool_attempt_adds_reflection_prompt_for_next_decision(self) -> None:
        decide_calls: list[list[dict[str, object]]] = []

        async def fake_decide_turn(**kwargs):
            messages = list(kwargs.get("messages") or [])
            decide_calls.append(messages)
            if len(decide_calls) == 1:
                return TurnDecision(
                    needs_tool=True,
                    thought_summary="Need a tool first",
                    action_message="I should inspect the file first",
                    tool_calls=[ToolIntent(name="read_file", arguments={"path": "missing.txt"})],
                )
            return TurnDecision(needs_tool=False, thought_summary="Need a different approach", action_message="", tool_calls=[])

        def fake_execute_tool_calls(**_kwargs):
            return [
                ToolExecution(
                    name="read_file",
                    arguments={"path": "missing.txt"},
                    ok=False,
                    summary="not found",
                    payload='{"ok": false, "error": "not found"}',
                )
            ]

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=fake_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
        )

        checkpoint_path = Path.cwd() / "test_agent_graph_reflection.pkl"
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
                    "turn_id": "turn-reflection",
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
                    "user_text": "read the missing file and help me continue",
                    "working_messages": [{"role": "user", "content": "read the missing file and help me continue"}],
                    "decision_messages": [{"role": "user", "content": "read the missing file and help me continue"}],
                    "prompt_messages": [{"role": "user", "content": "read the missing file and help me continue"}],
                }
            )

            self.assertTrue(outcome.is_pending)
            resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-reflection", approved=True))

            self.assertFalse(resumed.is_pending)
            second_messages = decide_calls[-1]
            combined = "\n".join(str(item.get("content") or "") for item in second_messages if isinstance(item, dict))
            self.assertIn("Do not repeat an ineffective tool call in the same form", combined)
            self.assertIn("missing.txt", combined)
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

    async def test_system_skill_search_results_expand_turn_state(self) -> None:
        decide_calls: list[list[str]] = []
        bridge_states: list[dict[str, object]] = []

        class _PhaseBridge:
            def __init__(self, names):
                self._names = list(names)

            def list_tools(self):
                return [
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": name,
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                    for name in self._names
                ]

        def build_tool_bridge(state):
            bridge_states.append(
                {
                    "execution_phase": state.get("execution_phase"),
                    "discovered_skill_ids": list(state.get("discovered_skill_ids") or []),
                }
            )
            names = ["system.skill_search", "system.agent_loop"]
            if "extra-skill" in (state.get("discovered_skill_ids") or []):
                names.append("skill.extra-skill.run-task")
            return _PhaseBridge(names)

        async def fake_decide_turn(**kwargs):
            tools = [str(item.get("function", {}).get("name") or "") for item in (kwargs.get("tools") or [])]
            decide_calls.append(tools)
            if len(decide_calls) == 1:
                return TurnDecision(
                    needs_tool=True,
                    thought_summary="Search for another skill",
                    action_message="I should search for another skill",
                    tool_calls=[ToolIntent(name="system.skill_search", arguments={"query": "extra"})],
                )
            return TurnDecision(needs_tool=False, thought_summary="Enough info", action_message="", tool_calls=[])

        def fake_execute_tool_calls(**_kwargs):
            return [
                ToolExecution(
                    name="system.skill_search",
                    arguments={"query": "extra"},
                    ok=True,
                    summary="found extra skill",
                    payload='{"ok": true, "result": {"kind": "skill_search", "matches": [{"id": "extra-skill"}]}}',
                )
            ]

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=fake_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=build_tool_bridge,
        )

        checkpoint_path = Path.cwd() / "test_agent_graph_skill_search.pkl"
        try:
            checkpoint_path.unlink()
        except FileNotFoundError:
            pass
        try:
            runtime = AgentGraphRuntime(dependency_provider=lambda: deps, checkpoint_path=checkpoint_path)
            outcome = await runtime.start_turn(
                {
                    "turn_id": "turn-skill-search",
                    "session_id": "default",
                    "chat_mode": "react",
                    "route_kind": "skill_task",
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
                    "user_text": "find a better skill",
                    "working_messages": [{"role": "user", "content": "find a better skill"}],
                    "decision_messages": [{"role": "user", "content": "find a better skill"}],
                    "prompt_messages": [{"role": "user", "content": "find a better skill"}],
                    "execution_phase": "skill_selection",
                }
            )
            self.assertTrue(outcome.is_pending)
            resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-skill-search", approved=True))

            self.assertFalse(resumed.is_pending)
            self.assertIn("extra-skill", resumed.state.get("discovered_skill_ids") or [])
            self.assertIn("skill.extra-skill.run-task", decide_calls[-1])
            self.assertTrue(any("extra-skill" in (call.get("discovered_skill_ids") or []) for call in bridge_states))
        finally:
            try:
                checkpoint_path.unlink()
            except FileNotFoundError:
                pass

    async def test_agent_loop_tool_switch_updates_phase_before_next_decision(self) -> None:
        decide_calls: list[list[str]] = []

        class _PhaseBridge:
            def __init__(self, names):
                self._names = list(names)

            def list_tools(self):
                return [
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": name,
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                    for name in self._names
                ]

        def build_tool_bridge(state):
            phase = str(state.get("execution_phase") or "")
            if phase == "agent_loop":
                return _PhaseBridge(["system.tool_search"])
            return _PhaseBridge(["system.skill_search", "system.agent_loop"])

        async def fake_decide_turn(**kwargs):
            tools = [str(item.get("function", {}).get("name") or "") for item in (kwargs.get("tools") or [])]
            decide_calls.append(tools)
            if len(decide_calls) == 1:
                return TurnDecision(
                    needs_tool=True,
                    thought_summary="Need agent loop",
                    action_message="Switch to agent loop",
                    tool_calls=[ToolIntent(name="system.agent_loop", arguments={"task": "general fallback"})],
                )
            return TurnDecision(needs_tool=False, thought_summary="Done", action_message="", tool_calls=[])

        def fake_execute_tool_calls(**_kwargs):
            return [
                ToolExecution(
                    name="system.agent_loop",
                    arguments={"task": "general fallback"},
                    ok=True,
                    summary="entered agent loop",
                    payload='{"ok": true, "result": {"kind": "agent_loop", "task": "general fallback"}}',
                )
            ]

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=fake_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=build_tool_bridge,
        )

        checkpoint_path = Path.cwd() / "test_agent_graph_agent_loop.pkl"
        try:
            checkpoint_path.unlink()
        except FileNotFoundError:
            pass
        try:
            runtime = AgentGraphRuntime(dependency_provider=lambda: deps, checkpoint_path=checkpoint_path)
            outcome = await runtime.start_turn(
                {
                    "turn_id": "turn-agent-loop",
                    "session_id": "default",
                    "chat_mode": "react",
                    "route_kind": "skill_task",
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
                    "user_text": "fallback to agent loop",
                    "working_messages": [{"role": "user", "content": "fallback to agent loop"}],
                    "decision_messages": [{"role": "user", "content": "fallback to agent loop"}],
                    "prompt_messages": [{"role": "user", "content": "fallback to agent loop"}],
                    "execution_phase": "skill_selection",
                }
            )
            self.assertTrue(outcome.is_pending)
            resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-agent-loop", approved=True))

            self.assertFalse(resumed.is_pending)
            self.assertEqual(resumed.state.get("execution_phase"), "agent_loop")
            self.assertEqual(decide_calls[-1], ["system.tool_search"])
        finally:
            try:
                checkpoint_path.unlink()
            except FileNotFoundError:
                pass

    async def test_react_direct_answer_route_still_runs_decision_stage(self) -> None:
        decide_calls: list[list[str]] = []

        class _PhaseBridge:
            def list_tools(self):
                return [
                    {
                        "type": "function",
                        "function": {
                            "name": "system.skill_search",
                            "description": "search skills",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ]

        async def fake_decide_turn(**kwargs):
            tools = [str(item.get("function", {}).get("name") or "") for item in (kwargs.get("tools") or [])]
            decide_calls.append(tools)
            return TurnDecision(
                needs_tool=True,
                thought_summary="Need a skill search",
                action_message="I should search skills first",
                tool_calls=[ToolIntent(name="system.skill_search", arguments={"query": "hotspots"})],
            )

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=lambda **_kwargs: [],
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda _state: _PhaseBridge(),
        )

        checkpoint_path = Path.cwd() / "test_agent_graph_route_direct_answer.pkl"
        try:
            checkpoint_path.unlink()
        except FileNotFoundError:
            pass
        try:
            runtime = AgentGraphRuntime(dependency_provider=lambda: deps, checkpoint_path=checkpoint_path)
            outcome = await runtime.start_turn(
                {
                    "turn_id": "turn-react-direct-answer",
                    "session_id": "default",
                    "chat_mode": "react",
                    "route_kind": "direct_answer",
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
                    "user_text": "collect today's hotspots and save them",
                    "working_messages": [{"role": "user", "content": "collect today's hotspots and save them"}],
                    "decision_messages": [{"role": "user", "content": "collect today's hotspots and save them"}],
                    "prompt_messages": [{"role": "user", "content": "collect today's hotspots and save them"}],
                    "execution_phase": "skill_selection",
                }
            )

            self.assertTrue(outcome.is_pending)
            self.assertEqual(decide_calls[-1], ["system.skill_search"])
        finally:
            try:
                checkpoint_path.unlink()
            except FileNotFoundError:
                pass

    def test_react_empty_skill_task_falls_back_to_complex_task(self) -> None:
        route = _route_from_assistant_message(
            "react",
            {"content": '{"route_kind":"skill_task","skill_ids":[],"thought_summary":"Need a hidden skill"}'},
            allowed_skill_ids={"daily-hotspots"},
            allowed_tool_names={"system.skill_search", "system.agent_loop"},
        )

        self.assertEqual(route.route_kind, "complex_task")


if __name__ == "__main__":
    unittest.main()
