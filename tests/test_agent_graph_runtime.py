from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.agent_graph import AgentGraphRuntime, ApprovalDecision, GraphDependencies
from backend.agent_orchestrator import ToolExecution, ToolIntent, TurnDecision, _route_from_assistant_message


def _react_state(
    *,
    turn_id: str,
    user_text: str,
    route_kind: str = "complex_task",
    execution_phase: str = "agent_loop",
    selection_origin: str = "none",
    selected_skill_ids: list[str] | None = None,
    selected_tool_names: list[str] | None = None,
    planner_excluded_skill_ids: list[str] | None = None,
    planner_excluded_tool_names: list[str] | None = None,
    planner_retry_used: bool = False,
    max_reasoning_steps: int = 2,
    loop_round: int = 1,
) -> dict[str, object]:
    return {
        "turn_id": turn_id,
        "session_id": "default",
        "chat_mode": "react",
        "route_kind": route_kind,
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
        "max_reasoning_steps": max_reasoning_steps,
        "reasoning_step": 1,
        "user_text": user_text,
        "working_messages": [{"role": "user", "content": user_text}],
        "decision_messages": [{"role": "user", "content": user_text}],
        "prompt_messages": [{"role": "user", "content": user_text}],
        "execution_phase": execution_phase,
        "selection_origin": selection_origin,
        "selected_skill_ids": list(selected_skill_ids or []),
        "selected_tool_names": list(selected_tool_names or []),
        "planner_excluded_skill_ids": list(planner_excluded_skill_ids or []),
        "planner_excluded_tool_names": list(planner_excluded_tool_names or []),
        "planner_retry_used": planner_retry_used,
        "loop_round": loop_round,
        "last_expected_effect": "",
        "last_assessment": "",
        "last_inventory_result": {},
        "rejected_call_signatures": [],
        "no_progress_streak": 0,
        "phase_events": [],
    }


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


class _PhaseBridge:
    def __init__(self, names: list[str]) -> None:
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


class _StateAwareBridge:
    def __init__(self, state: dict[str, object]) -> None:
        self._state = dict(state)

    def _tool_names(self) -> list[str]:
        phase = str(self._state.get("execution_phase") or "")
        selection_origin = str(self._state.get("selection_origin") or "none")
        planner_retry_used = bool(self._state.get("planner_retry_used", False))
        active_skill_ids = [str(item) for item in (self._state.get("active_skill_ids") or []) if str(item)]
        selected_skill_ids = [str(item) for item in (self._state.get("selected_skill_ids") or []) if str(item)]
        selected_tool_names = [str(item) for item in (self._state.get("selected_tool_names") or []) if str(item)]

        if phase == "skill_selection":
            return [f"skill.{skill_id}.run_task" for skill_id in active_skill_ids] + ["system.agent_loop"]
        if phase == "skill_execution":
            names = [f"skill.{skill_id}.run_task" for skill_id in (selected_skill_ids or active_skill_ids)]
            if selection_origin == "planner" and not planner_retry_used:
                names.append("system.capability_search")
            return names
        if phase == "agent_loop":
            if selected_tool_names:
                names = list(selected_tool_names)
                if selection_origin == "planner" and not planner_retry_used:
                    names.insert(0, "system.capability_search")
                return names
            return ["system.capability_search"]
        return []

    def list_tools(self):
        return _PhaseBridge(self._tool_names()).list_tools()

    def call_tool(self, tool_name: str, arguments: dict[str, object]):
        if tool_name == "system.capability_search":
            query = str(arguments.get("query") or arguments.get("task") or arguments.get("keyword") or "").strip()
            normalized = {
                "kind": "capability_search",
                "task": query,
                "query": query,
                "reason": str(arguments.get("reason") or "").strip(),
                "exclude_skill_ids": [str(item) for item in (arguments.get("exclude_skill_ids") or []) if str(item)],
                "exclude_tool_names": [str(item) for item in (arguments.get("exclude_tool_names") or []) if str(item)],
            }
            return SimpleNamespace(
                ok=True,
                content=json.dumps(normalized, ensure_ascii=False),
                structured_data=normalized,
                error="",
            )
        return SimpleNamespace(ok=True, content=tool_name, structured_data=dict(arguments or {}), error="")


def _successful_execute_tool_calls(**kwargs):
    tool_calls = list(kwargs.get("tool_calls") or [])
    return [
        ToolExecution(
            intent.name,
            dict(intent.arguments or {}),
            True,
            f"ran {intent.name}",
            json.dumps({"ok": True, "result": {"tool_name": intent.name}}, ensure_ascii=False),
        )
        for intent in tool_calls
    ]


class AgentGraphRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_continuation_decision_uses_decision_messages_but_final_reply_keeps_prompt_messages(self) -> None:
        decide_calls: list[list[dict[str, object]]] = []

        async def fake_decide_turn(**kwargs):
            messages = list(kwargs.get("messages") or [])
            decide_calls.append(messages)
            if len(decide_calls) == 1:
                return TurnDecision(True, "Need a tool first", "I should read the file first", [ToolIntent("read_file", {"path": "story.txt"})])
            return TurnDecision(False, "Enough info now", "", [])

        def fake_execute_tool_calls(**_kwargs):
            return [ToolExecution("read_file", {"path": "story.txt"}, True, "read ok", '{"ok": true, "result_preview": "demo"}')]

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
            runtime = AgentGraphRuntime(dependency_provider=lambda: deps, checkpoint_path=checkpoint_path)
            state = _react_state(turn_id="turn-context", user_text="read story.txt and then answer")
            state["prompt_messages"] = [
                {"role": "system", "content": "ROLEPLAY_PROMPT"},
                {"role": "user", "content": "read story.txt and then answer"},
            ]
            outcome = await runtime.start_turn(state)
            self.assertTrue(outcome.is_pending)
            resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-context", approved=True))
            self.assertFalse(resumed.is_pending)
            second_messages = decide_calls[-1]
            self.assertEqual(second_messages[0]["role"], "user")
            self.assertNotIn("ROLEPLAY_PROMPT", "\n".join(str(item.get("content") or "") for item in second_messages))
            self.assertEqual(resumed.final_messages[0]["content"], "ROLEPLAY_PROMPT")
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
                return TurnDecision(True, "Need a tool first", "I should inspect the file first", [ToolIntent("read_file", {"path": "missing.txt"})])
            return TurnDecision(False, "Need a different approach", "", [])

        def fake_execute_tool_calls(**_kwargs):
            return [ToolExecution("read_file", {"path": "missing.txt"}, False, "not found", '{"ok": false, "error": "not found"}')]

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
            runtime = AgentGraphRuntime(dependency_provider=lambda: deps, checkpoint_path=checkpoint_path)
            outcome = await runtime.start_turn(_react_state(turn_id="turn-reflection", user_text="read the missing file and help me continue"))
            self.assertTrue(outcome.is_pending)
            resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-reflection", approved=True))
            self.assertFalse(resumed.is_pending)
            combined = "\n".join(str(item.get("content") or "") for item in decide_calls[-1] if isinstance(item, dict))
            self.assertIn("Do not repeat an ineffective tool call in the same form", combined)
            self.assertIn("missing.txt", combined)
        finally:
            try:
                checkpoint_path.unlink()
            except FileNotFoundError:
                pass

    async def test_writer_phase_event_includes_concrete_tool_call_summary(self) -> None:
        async def fake_decide_need_for_tools(**_kwargs):
            return TurnDecision(True, "Need a browser tool first", "Open YouTube in the browser", [])

        async def fake_plan_capabilities(_state, _arguments):
            return {
                "selection_kind": "mcp",
                "skill_ids": [],
                "tool_names": ["playwright.browser_navigate"],
                "query": "open youtube",
                "thought_summary": "Use browser navigate.",
                "reason": "A browser tool is required.",
                "usage_notes": "Navigate directly to YouTube.",
            }

        async def fake_decide_turn(**_kwargs):
            return TurnDecision(
                True,
                "Need a browser tool first",
                "Open YouTube in the browser",
                [ToolIntent("playwright.browser_navigate", {"url": "https://www.youtube.com"})],
                "Navigate to the YouTube homepage in the browser.",
            )

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda _state: _PhaseBridge(["playwright.browser_navigate"]),
            plan_capabilities=fake_plan_capabilities,
            decide_need_for_tools=fake_decide_need_for_tools,
        )

        runtime = self._build_runtime(deps, "test_agent_graph_writer_phase.pkl")
        outcome = await runtime.start_turn(
            _react_state(
                turn_id="turn-writer-phase",
                user_text="open youtube",
                execution_phase="agent_loop",
            )
        )

        self.assertTrue(outcome.is_pending)
        writer_events = [item for item in outcome.phase_events if item.get("thought_role") == "writer"]
        self.assertEqual(len(writer_events), 1)
        self.assertIn("Planned calls:", writer_events[0]["text"])
        self.assertIn("playwright.browser_navigate", writer_events[0]["text"])
        self.assertIn("https://www.youtube.com", writer_events[0]["text"])

    async def test_internal_planner_selects_mcp_bundle_before_writer(self) -> None:
        writer_tool_names: list[list[str]] = []
        writer_messages: list[list[dict[str, object]]] = []

        async def fake_decide_need_for_tools(**_kwargs):
            return TurnDecision(True, "Need a browser tool", "Open the target page", [])

        async def fake_decide_turn(**kwargs):
            writer_messages.append(list(kwargs.get("messages") or []))
            tool_names = [str(item.get("function", {}).get("name") or "") for item in (kwargs.get("tools") or [])]
            writer_tool_names.append(tool_names)
            return TurnDecision(
                True,
                "Use the planner-selected browser tool",
                "Open the page now",
                [ToolIntent("playwright.browser_navigate", {"url": "https://example.com"})],
                "Browser reaches https://example.com.",
            )

        async def fake_plan_capabilities(_state, arguments):
            return {
                "selection_kind": "mcp",
                "skill_ids": [],
                "tool_names": ["playwright.browser_navigate"],
                "query": str(arguments.get("query") or ""),
                "thought_summary": "Use browser navigate.",
                "reason": "A concrete browser MCP tool is the best fit.",
                "usage_notes": "Navigate directly to the requested URL first.",
            }

        def build_tool_bridge(state):
            selected = [str(item) for item in (state.get("selected_tool_names") or []) if str(item)]
            return _PhaseBridge(selected or ["read_file", "playwright.browser_navigate"])

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=build_tool_bridge,
            plan_capabilities=fake_plan_capabilities,
            decide_need_for_tools=fake_decide_need_for_tools,
        )

        runtime = self._build_runtime(deps, "test_agent_graph_internal_planner_mcp.pkl")
        outcome = await runtime.start_turn(_react_state(turn_id="turn-planner-mcp", user_text="open the site"))

        self.assertTrue(outcome.is_pending)
        self.assertEqual(writer_tool_names[0], ["playwright.browser_navigate"])
        self.assertEqual(writer_messages[0][0]["role"], "system")
        self.assertIn("Planner-selected MCP tools: playwright.browser_navigate", str(writer_messages[0][0]["content"] or ""))
        self.assertEqual(writer_messages[0][1]["role"], "system")
        self.assertIn("Tool-call JSON template for the writer stage.", str(writer_messages[0][1]["content"] or ""))
        self.assertIn('"name": "playwright.browser_navigate"', str(writer_messages[0][1]["content"] or ""))
        self.assertEqual(outcome.state["selection_origin"], "planner")
        self.assertEqual(outcome.state["selected_tool_names"], ["playwright.browser_navigate"])
        self.assertTrue(any(item.get("thought_role") == "planner" for item in outcome.phase_events))
        self.assertTrue(any(item.get("thought_role") == "writer" for item in outcome.phase_events))

    async def test_internal_planner_selects_skill_bundle_before_writer(self) -> None:
        writer_tool_names: list[list[str]] = []
        writer_messages: list[list[dict[str, object]]] = []

        async def fake_decide_need_for_tools(**_kwargs):
            return TurnDecision(True, "Need the selected skill workflow", "Use the selected skill", [])

        async def fake_decide_turn(**kwargs):
            writer_messages.append(list(kwargs.get("messages") or []))
            tool_names = [str(item.get("function", {}).get("name") or "") for item in (kwargs.get("tools") or [])]
            writer_tool_names.append(tool_names)
            return TurnDecision(
                True,
                "Use the planner-selected skill",
                "Run the selected skill now",
                [ToolIntent("skill.extra-skill.run_task", {"task": "finish it"})],
                "The selected skill handles the requested workflow.",
            )

        async def fake_plan_capabilities(_state, arguments):
            return {
                "selection_kind": "skill",
                "skill_ids": ["extra-skill"],
                "tool_names": [],
                "query": str(arguments.get("query") or ""),
                "thought_summary": "Use the extra skill.",
                "reason": "The default-enabled skill matches the task.",
                "usage_notes": "Start by running the skill entrypoint for this task.",
            }

        def build_tool_bridge(state):
            selected_skill_ids = [str(item) for item in (state.get("selected_skill_ids") or []) if str(item)]
            if selected_skill_ids:
                return _PhaseBridge([f"skill.{selected_skill_ids[0]}.run_task"])
            return _PhaseBridge(["read_file"])

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=build_tool_bridge,
            plan_capabilities=fake_plan_capabilities,
            resolve_skill_prompt_text=lambda skill_ids, _state: f"Detailed skill prompt for {','.join(skill_ids)}",
            decide_need_for_tools=fake_decide_need_for_tools,
        )

        runtime = self._build_runtime(deps, "test_agent_graph_internal_planner_skill.pkl")
        outcome = await runtime.start_turn(_react_state(turn_id="turn-planner-skill", user_text="use the extra skill"))

        self.assertTrue(outcome.is_pending)
        self.assertEqual(writer_tool_names[0], ["skill.extra-skill.run_task"])
        combined_messages = "\n".join(str(item.get("content") or "") for item in writer_messages[0])
        self.assertIn("Detailed skill prompt for extra-skill", combined_messages)
        self.assertEqual(outcome.state["selection_origin"], "planner")
        self.assertEqual(outcome.state["selected_skill_ids"], ["extra-skill"])

    async def test_internal_planner_inventory_finishes_without_tool_approval(self) -> None:
        async def fake_decide_need_for_tools(**_kwargs):
            return TurnDecision(True, "Check available MCP tools first", "List the current MCP tools", [])

        async def fake_decide_turn(**_kwargs):
            raise AssertionError("writer should not run for planner inventory mode")

        async def fake_plan_capabilities(_state, arguments):
            return {
                "mode": "inventory",
                "selection_kind": "none",
                "skill_ids": [],
                "tool_names": [],
                "query": str(arguments.get("query") or ""),
                "thought_summary": "I listed the current MCP tools.",
                "reason": "The user explicitly asked for available tools.",
                "usage_notes": "",
                "inventory_scope": "mcp",
                "inventory_skill_ids": [],
                "inventory_tool_names": ["playwright.browser_navigate"],
                "inventory_summary": "I found 1 runtime-available non-skill MCP tool.",
                "inventory_skills": [],
                "inventory_tools": [
                    {
                        "name": "playwright.browser_navigate",
                        "description": "Navigate a page in the browser",
                        "source": "mcp",
                    }
                ],
            }

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda state: _PhaseBridge([str(item) for item in (state.get("selected_tool_names") or []) if str(item)]),
            plan_capabilities=fake_plan_capabilities,
            decide_need_for_tools=fake_decide_need_for_tools,
        )

        runtime = self._build_runtime(deps, "test_agent_graph_internal_planner_inventory.pkl")
        outcome = await runtime.start_turn(_react_state(turn_id="turn-planner-inventory", user_text="搜有没有可用的 mcp"))

        self.assertFalse(outcome.is_pending)
        self.assertEqual(outcome.state["selection_origin"], "none")
        self.assertEqual(outcome.state["last_inventory_result"]["inventory_scope"], "mcp")
        combined = "\n".join(str(item.get("content") or "") for item in outcome.final_messages)
        self.assertIn("playwright.browser_navigate", combined)
        self.assertTrue(any(item.get("thought_role") == "planner" for item in outcome.phase_events))

    async def test_planner_none_adds_trace_and_observability_summary(self) -> None:
        async def fake_decide_need_for_tools(**_kwargs):
            return TurnDecision(True, "Need a browser tool", "Open the target page", [])

        async def fake_decide_turn(**_kwargs):
            raise AssertionError("writer should not run when planner returns none")

        async def fake_plan_capabilities(_state, arguments):
            return {
                "mode": "plan",
                "selection_kind": "none",
                "skill_ids": [],
                "tool_names": [],
                "query": str(arguments.get("query") or ""),
                "thought_summary": "No bundle selected.",
                "reason": "The planner response did not provide tool names from the visible MCP list.",
                "usage_notes": "",
                "debug": {
                    "parse_status": "invalid_tool_names",
                    "visible_skill_count": 2,
                    "visible_tool_count": 44,
                    "raw_content_excerpt": '{"selection_kind":"mcp","tool_names":["missing.browser"]}',
                },
            }

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda state: _PhaseBridge([str(item) for item in (state.get("selected_tool_names") or []) if str(item)]),
            plan_capabilities=fake_plan_capabilities,
            decide_need_for_tools=fake_decide_need_for_tools,
        )

        runtime = self._build_runtime(deps, "test_agent_graph_planner_none_trace.pkl")
        outcome = await runtime.start_turn(_react_state(turn_id="turn-planner-none", user_text="open the site"))

        self.assertFalse(outcome.is_pending)
        planner_event = next(item for item in (outcome.trace or []) if item.get("event") == "planner_result")
        self.assertEqual(planner_event.get("selection_kind"), "none")
        self.assertEqual(planner_event.get("debug.parse_status"), "invalid_tool_names")
        self.assertEqual(planner_event.get("debug.visible_skill_count"), 2)
        self.assertEqual(planner_event.get("debug.visible_tool_count"), 44)
        self.assertIn("missing.browser", str(planner_event.get("debug.raw_content_excerpt") or ""))
        planner_phase = next(item for item in outcome.phase_events if item.get("thought_role") == "planner")
        self.assertIn("Checked 2 default-enabled skill(s) and 44 MCP tool(s).", planner_phase.get("text") or "")
        self.assertIn("outside the visible MCP tool list", planner_phase.get("text") or "")
        self.assertFalse(any(item.get("thought_role") == "writer" for item in outcome.phase_events))

    async def test_writer_failure_still_emits_writer_observation(self) -> None:
        async def fake_decide_need_for_tools(**_kwargs):
            return TurnDecision(True, "Need a browser tool", "Open the target page", [])

        writer_calls = 0

        async def fake_decide_turn(**_kwargs):
            nonlocal writer_calls
            writer_calls += 1
            return TurnDecision(False, "I could answer directly", "", [])

        async def fake_plan_capabilities(_state, arguments):
            return {
                "selection_kind": "mcp",
                "skill_ids": [],
                "tool_names": ["playwright_mcp.browser_navigate", "playwright_mcp.browser_type"],
                "query": str(arguments.get("query") or ""),
                "thought_summary": "Use browser tools.",
                "reason": "A browser MCP bundle is the best fit.",
                "usage_notes": "Navigate first, then type into the page if needed.",
            }

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda state: _PhaseBridge([str(item) for item in (state.get("selected_tool_names") or []) if str(item)]),
            plan_capabilities=fake_plan_capabilities,
            decide_need_for_tools=fake_decide_need_for_tools,
        )

        runtime = self._build_runtime(deps, "test_agent_graph_writer_failure_observation.pkl")
        outcome = await runtime.start_turn(_react_state(turn_id="turn-writer-failure", user_text="open bilibili and search"))

        self.assertFalse(outcome.is_pending)
        self.assertEqual(writer_calls, 2)
        writer_phase = next(item for item in outcome.phase_events if item.get("thought_role") == "writer")
        self.assertIn("did not emit a concrete tool call", writer_phase.get("text") or "")
        self.assertIn("playwright_mcp.browser_navigate", writer_phase.get("text") or "")

    async def test_intermediate_tool_success_keeps_writer_step_in_progress(self) -> None:
        writer_calls = 0

        async def fake_decide_need_for_tools(**_kwargs):
            return TurnDecision(True, "Need a file tool", "Inspect the file first", [])

        async def fake_decide_turn(**_kwargs):
            nonlocal writer_calls
            writer_calls += 1
            if writer_calls == 1:
                return TurnDecision(
                    True,
                    "Read the first file",
                    "Read story.txt first",
                    [ToolIntent("read_file", {"path": "story.txt"})],
                    "Inspect the file contents before deciding the next step.",
                )
            return TurnDecision(
                True,
                "Need one more read",
                "Read story-2.txt next",
                [ToolIntent("read_file", {"path": "story-2.txt"})],
                "Gather the remaining details for this step.",
            )

        async def fake_search_capabilities(_state, arguments):
            return {
                "mode": "task_types",
                "skill_id": "",
                "task_types": ["file_io"],
                "matched_tool_names": ["read_file"],
                "query": str(arguments.get("query") or ""),
                "thought_summary": "Use file tools.",
                "reason": "The task needs file access.",
            }

        async def fake_build_execution_plan(_state, _searcher_result):
            return {
                "plan_id": "plan-1",
                "plan_summary": "Inspect the file and continue if needed.",
                "reason": "The first read is only intermediate progress.",
                "steps": [
                    {
                        "step_id": "step-1",
                        "title": "Inspect file contents",
                        "goal": "Read the file and decide whether the step still needs more tool work.",
                        "success_criteria": "Only complete after enough file details have been gathered.",
                        "call_mode": "single",
                        "candidate_tool_sets": [["read_file"]],
                        "notes": "Intermediate reads may require another batch.",
                    }
                ],
            }

        def fake_execute_tool_calls(**_kwargs):
            return [
                ToolExecution(
                    "read_file",
                    {"path": "story.txt"},
                    True,
                    "read ok",
                    json.dumps({"ok": True, "result": {"partial": True}}, ensure_ascii=False),
                )
            ]

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=fake_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda _state: _PhaseBridge(["read_file"]),
            search_capabilities=fake_search_capabilities,
            build_execution_plan=fake_build_execution_plan,
            decide_need_for_tools=fake_decide_need_for_tools,
        )

        runtime = self._build_runtime(deps, "test_agent_graph_writer_step_progress.pkl")
        outcome = await runtime.start_turn(_react_state(turn_id="turn-step-progress", user_text="read the file and continue if needed"))

        self.assertTrue(outcome.is_pending)
        resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-step-progress", approved=True))

        self.assertTrue(resumed.is_pending)
        self.assertEqual(writer_calls, 2)
        self.assertEqual(resumed.state.get("completed_steps"), [])
        self.assertEqual(resumed.state.get("current_step_index"), 0)
        self.assertEqual(resumed.state.get("current_step_attempt"), 2)
        self.assertTrue(bool(resumed.state.get("writer_loop_active")))
        approval_text = str((resumed.approval_request or {}).get("text") or "")
        self.assertIn("Step 1/1", approval_text)
        self.assertIn("story-2.txt", json.dumps((resumed.approval_request or {}).get("tools") or [], ensure_ascii=False))

    async def test_interrupt_state_persists_across_runtime_recreation(self) -> None:
        async def fake_decide_turn(**_kwargs):
            return TurnDecision(True, "Need a tool first", "I should read the file first", [ToolIntent("read_file", {"path": "story.txt"})])

        def fake_execute_tool_calls(**_kwargs):
            return [ToolExecution("read_file", {"path": "story.txt"}, True, "read ok", '{"ok": true, "result_preview": "demo"}')]

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
            runtime = AgentGraphRuntime(dependency_provider=lambda: deps, checkpoint_path=checkpoint_path)
            outcome = await runtime.start_turn(_react_state(turn_id="turn-persist", user_text="read story.txt"))
            self.assertTrue(outcome.is_pending)
            self.assertTrue(checkpoint_path.exists())

            runtime = AgentGraphRuntime(dependency_provider=lambda: deps, checkpoint_path=checkpoint_path)
            resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-persist", approved=True))
            self.assertFalse(resumed.is_pending)
            self.assertTrue(resumed.final_messages)
        finally:
            try:
                checkpoint_path.unlink()
            except FileNotFoundError:
                pass

    def _build_runtime(self, deps: GraphDependencies, checkpoint_name: str) -> AgentGraphRuntime:
        checkpoint_path = Path.cwd() / checkpoint_name
        try:
            checkpoint_path.unlink()
        except FileNotFoundError:
            pass
        self.addCleanup(lambda path=checkpoint_path: path.unlink(missing_ok=True))
        return AgentGraphRuntime(dependency_provider=lambda: deps, checkpoint_path=checkpoint_path)

    @unittest.skip("legacy capability_search flow removed")
    async def test_capability_search_skill_selection_updates_turn_state_and_next_tools(self) -> None:
        decide_tool_names: list[list[str]] = []
        plan_calls: list[dict[str, object]] = []

        async def fake_decide_turn(**kwargs):
            tool_names = [str(item.get("function", {}).get("name") or "") for item in (kwargs.get("tools") or [])]
            decide_tool_names.append(tool_names)
            if len(decide_tool_names) == 1:
                return TurnDecision(
                    True,
                    "Need a hidden skill",
                    "Search for a better capability bundle",
                    [ToolIntent("system.capability_search", {"task": "find a hidden skill"})],
                )
            return TurnDecision(
                True,
                "Use the selected skill",
                "Run the selected skill next",
                [ToolIntent("skill.extra-skill.run_task", {"task": "finish it"})],
            )

        async def fake_plan_capabilities(_state, arguments):
            plan_calls.append(dict(arguments))
            return {
                "selection_kind": "skill",
                "skill_ids": ["extra-skill"],
                "tool_names": [],
                "query": str(arguments.get("query") or ""),
                "thought_summary": "Use hidden skill",
                "reason": "Hidden skill fits best",
            }

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda state: _StateAwareBridge(state),
            plan_capabilities=fake_plan_capabilities,
            resolve_skill_prompt_text=lambda skill_ids, _state: f"Detailed hidden skill prompt for {','.join(skill_ids)}",
        )

        runtime = self._build_runtime(deps, "test_agent_graph_capability_skill.pkl")
        state = _react_state(turn_id="turn-capability-skill", user_text="use a hidden skill")
        outcome = await runtime.start_turn(state)
        self.assertTrue(outcome.is_pending)

        resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-capability-skill", approved=True))
        self.assertTrue(resumed.is_pending)
        self.assertEqual(plan_calls[0]["query"], "find a hidden skill")
        self.assertEqual(resumed.state["selection_origin"], "planner")
        self.assertEqual(resumed.state["execution_phase"], "skill_execution")
        self.assertEqual(resumed.state["selected_skill_ids"], ["extra-skill"])
        self.assertEqual(resumed.state["loop_round"], 2)
        self.assertIn("skill.extra-skill.run_task", decide_tool_names[1])
        self.assertIn("system.capability_search", decide_tool_names[1])
        combined_followup = "\n".join(str(item.get("content") or "") for item in resumed.state.get("followup_messages") or [])
        self.assertIn("Detailed hidden skill prompt for extra-skill", combined_followup)

    @unittest.skip("legacy capability_search flow removed")
    async def test_agent_loop_transition_exposes_capability_search_before_next_decision(self) -> None:
        decide_tool_names: list[list[str]] = []

        async def fake_decide_turn(**kwargs):
            tool_names = [str(item.get("function", {}).get("name") or "") for item in (kwargs.get("tools") or [])]
            decide_tool_names.append(tool_names)
            if len(decide_tool_names) == 1:
                return TurnDecision(
                    True,
                    "Visible skill is not enough",
                    "Enter agent loop",
                    [ToolIntent("system.agent_loop", {"task": "fallback"})],
                )
            return TurnDecision(False, "Now I can answer", "", [])

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda state: _StateAwareBridge(state),
        )

        runtime = self._build_runtime(deps, "test_agent_graph_agent_loop.pkl")
        state = _react_state(
            turn_id="turn-agent-loop",
            user_text="start from the visible skill, then fall back",
            execution_phase="skill_selection",
        )
        state["active_skill_ids"] = ["visible-skill"]
        outcome = await runtime.start_turn(state)
        self.assertTrue(outcome.is_pending)

        resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-agent-loop", approved=True))
        self.assertFalse(resumed.is_pending)
        self.assertIn("system.agent_loop", decide_tool_names[0])
        self.assertEqual(decide_tool_names[1], ["system.capability_search"])
        self.assertEqual(resumed.state["selection_origin"], "none")
        self.assertEqual(resumed.state["execution_phase"], "agent_loop")

    @unittest.skip("legacy capability_search flow removed")
    async def test_capability_search_mcp_handoff_requires_concrete_tool_followup(self) -> None:
        decide_tool_names: list[list[str]] = []
        decide_messages: list[list[dict[str, object]]] = []

        async def fake_decide_turn(**kwargs):
            decide_messages.append(list(kwargs.get("messages") or []))
            tool_names = [str(item.get("function", {}).get("name") or "") for item in (kwargs.get("tools") or [])]
            decide_tool_names.append(tool_names)
            if len(decide_tool_names) == 1:
                return TurnDecision(
                    True,
                    "Need a browser tool",
                    "Search capabilities",
                    [ToolIntent("system.capability_search", {"task": "open the page"})],
                )
            return TurnDecision(
                True,
                "Use the narrowed MCP tool",
                "Open the page now",
                [ToolIntent("playwright.browser_navigate", {"url": "https://example.com"})],
            )

        async def fake_plan_capabilities(_state, arguments):
            return {
                "selection_kind": "mcp",
                "skill_ids": [],
                "tool_names": ["playwright.browser_navigate"],
                "query": str(arguments.get("query") or ""),
                "thought_summary": "Use browser tool",
                "reason": "No skill fits",
            }

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda state: _StateAwareBridge(state),
            plan_capabilities=fake_plan_capabilities,
        )

        runtime = self._build_runtime(deps, "test_agent_graph_capability_mcp.pkl")
        outcome = await runtime.start_turn(_react_state(turn_id="turn-capability-mcp", user_text="open the site"))
        self.assertTrue(outcome.is_pending)

        resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-capability-mcp", approved=True))
        self.assertTrue(resumed.is_pending)
        self.assertEqual(resumed.state["selection_origin"], "planner")
        self.assertEqual(resumed.state["selected_tool_names"], ["playwright.browser_navigate"])
        self.assertEqual(resumed.state["loop_round"], 2)
        self.assertEqual(decide_tool_names[1], ["system.capability_search", "playwright.browser_navigate"])
        handoff_prompt = "\n".join(str(item.get("content") or "") for item in decide_messages[1])
        self.assertIn("capability-planner handoff stage", handoff_prompt)

    @unittest.skip("legacy capability_search flow removed")
    async def test_capability_search_exclusion_retry_replans_once_and_hides_search_after_retry(self) -> None:
        decide_tool_names: list[list[str]] = []
        plan_calls: list[dict[str, object]] = []

        async def fake_decide_turn(**kwargs):
            tool_names = [str(item.get("function", {}).get("name") or "") for item in (kwargs.get("tools") or [])]
            decide_tool_names.append(tool_names)
            if len(decide_tool_names) == 1:
                return TurnDecision(
                    True,
                    "Need capabilities",
                    "Search capabilities",
                    [ToolIntent("system.capability_search", {"task": "open the site"})],
                )
            if len(decide_tool_names) == 2:
                return TurnDecision(
                    True,
                    "This bundle is unsuitable",
                    "Retry with exclusions",
                    [
                        ToolIntent(
                            "system.capability_search",
                            {
                                "task": "open the site",
                                "exclude_tool_names": ["playwright.browser_navigate"],
                                "reason": "navigation tool failed the requirement",
                            },
                        )
                    ],
                )
            return TurnDecision(
                True,
                "Use the retry result",
                "Open a tab with the replacement tool",
                [ToolIntent("browser.open_tab", {"url": "https://example.com"})],
            )

        async def fake_plan_capabilities(_state, arguments):
            plan_calls.append(dict(arguments))
            if arguments.get("exclude_tool_names"):
                return {
                    "selection_kind": "mcp",
                    "skill_ids": [],
                    "tool_names": ["browser.open_tab"],
                    "query": str(arguments.get("query") or ""),
                    "thought_summary": "Use fallback browser tool",
                    "reason": "Excluded the first MCP tool",
                }
            return {
                "selection_kind": "mcp",
                "skill_ids": [],
                "tool_names": ["playwright.browser_navigate"],
                "query": str(arguments.get("query") or ""),
                "thought_summary": "Use browser navigate",
                "reason": "First MCP candidate",
            }

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda state: _StateAwareBridge(state),
            plan_capabilities=fake_plan_capabilities,
        )

        runtime = self._build_runtime(deps, "test_agent_graph_capability_retry.pkl")
        first = await runtime.start_turn(_react_state(turn_id="turn-capability-retry", user_text="open the site"))
        self.assertTrue(first.is_pending)

        second = await runtime.resume_turn(ApprovalDecision(turn_id="turn-capability-retry", approved=True))
        self.assertTrue(second.is_pending)
        third = await runtime.resume_turn(ApprovalDecision(turn_id="turn-capability-retry", approved=True))
        self.assertTrue(third.is_pending)

        self.assertEqual(plan_calls[1]["exclude_tool_names"], ["playwright.browser_navigate"])
        self.assertEqual(decide_tool_names[2], ["browser.open_tab"])
        self.assertEqual(third.state["selected_tool_names"], ["browser.open_tab"])
        self.assertEqual(third.state["planner_excluded_tool_names"], ["playwright.browser_navigate"])
        self.assertTrue(third.state["planner_retry_used"])

    @unittest.skip("legacy capability_search flow removed")
    async def test_capability_search_none_clears_selected_bundle(self) -> None:
        decide_tool_names: list[list[str]] = []

        async def fake_decide_turn(**kwargs):
            tool_names = [str(item.get("function", {}).get("name") or "") for item in (kwargs.get("tools") or [])]
            decide_tool_names.append(tool_names)
            if len(decide_tool_names) == 1:
                return TurnDecision(
                    True,
                    "Current bundle is not usable",
                    "Search again with exclusions",
                    [
                        ToolIntent(
                            "system.capability_search",
                            {
                                "task": "find another option",
                                "exclude_tool_names": ["playwright.browser_navigate"],
                            },
                        )
                    ],
                )
            return TurnDecision(False, "No more tools needed", "", [])

        async def fake_plan_capabilities(_state, arguments):
            return {
                "selection_kind": "none",
                "skill_ids": [],
                "tool_names": [],
                "query": str(arguments.get("query") or ""),
                "thought_summary": "No suitable bundle remains",
                "reason": "Everything useful was excluded",
            }

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda state: _StateAwareBridge(state),
            plan_capabilities=fake_plan_capabilities,
        )

        runtime = self._build_runtime(deps, "test_agent_graph_capability_none.pkl")
        state = _react_state(
            turn_id="turn-capability-none",
            user_text="find another option",
            execution_phase="agent_loop",
            selection_origin="planner",
            selected_tool_names=["playwright.browser_navigate"],
        )
        outcome = await runtime.start_turn(state)
        self.assertTrue(outcome.is_pending)

        resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-capability-none", approved=True))
        self.assertFalse(resumed.is_pending)
        self.assertEqual(resumed.state["selection_origin"], "planner")
        self.assertEqual(resumed.state["selected_skill_ids"], [])
        self.assertEqual(resumed.state["selected_tool_names"], [])
        self.assertEqual(decide_tool_names[1], ["system.capability_search"])

    @unittest.skip("legacy capability_search flow removed")
    async def test_explicit_capability_inventory_finishes_with_fact_based_final_reply(self) -> None:
        async def fake_decide_turn(**kwargs):
            return TurnDecision(
                True,
                "Check available MCP tools first",
                "List the currently available MCP tools",
                [ToolIntent("system.capability_search", {"task": "搜有没有可用的 mcp"})],
                "Enumerate the runtime-available MCP tools before doing anything else.",
            )

        async def fake_plan_capabilities(_state, arguments):
            return {
                "mode": "inventory",
                "selection_kind": "none",
                "skill_ids": [],
                "tool_names": [],
                "query": str(arguments.get("query") or ""),
                "thought_summary": "I found the currently available MCP tools.",
                "reason": "The user explicitly asked for an availability check.",
                "inventory_scope": "mcp",
                "inventory_skill_ids": [],
                "inventory_tool_names": ["playwright.browser_navigate"],
                "inventory_summary": "I found 1 runtime-available non-skill MCP tool.",
                "inventory_skills": [],
                "inventory_tools": [
                    {
                        "name": "playwright.browser_navigate",
                        "description": "Navigate a page in the browser",
                        "source": "mcp",
                    }
                ],
            }

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda state: _StateAwareBridge(state),
            plan_capabilities=fake_plan_capabilities,
        )

        runtime = self._build_runtime(deps, "test_agent_graph_inventory.pkl")
        outcome = await runtime.start_turn(_react_state(turn_id="turn-inventory", user_text="搜有没有可用的 mcp"))
        self.assertTrue(outcome.is_pending)

        resumed = await runtime.resume_turn(ApprovalDecision(turn_id="turn-inventory", approved=True))
        self.assertFalse(resumed.is_pending)
        self.assertEqual(resumed.state["selection_origin"], "none")
        self.assertEqual(resumed.state["selected_skill_ids"], [])
        self.assertEqual(resumed.state["selected_tool_names"], [])
        self.assertEqual(resumed.state["last_inventory_result"]["inventory_scope"], "mcp")
        self.assertEqual(resumed.state["last_inventory_result"]["inventory_tool_names"], ["playwright.browser_navigate"])
        combined = "\n".join(str(item.get("content") or "") for item in resumed.final_messages)
        self.assertIn("Runtime-available non-skill MCP tools found", combined)
        self.assertIn("playwright.browser_navigate", combined)
        self.assertTrue(any(item.get("thought_role") == "planner" for item in resumed.phase_events))

    async def test_rejected_approval_with_user_input_replans_same_turn(self) -> None:
        decide_messages: list[list[dict[str, object]]] = []

        async def fake_decide_turn(**kwargs):
            messages = list(kwargs.get("messages") or [])
            decide_messages.append(messages)
            if len(decide_messages) == 1:
                return TurnDecision(
                    True,
                    "Need a file read",
                    "Read the draft first",
                    [ToolIntent("read_file", {"path": "draft.txt"})],
                )
            return TurnDecision(False, "Use the user's follow-up guidance", "", [])

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda _state: _PhaseBridge(["read_file"]),
        )

        runtime = self._build_runtime(deps, "test_agent_graph_reject_followup.pkl")
        outcome = await runtime.start_turn(_react_state(turn_id="turn-reject-followup", user_text="summarize the draft"))
        self.assertTrue(outcome.is_pending)

        resumed = await runtime.resume_turn(
            ApprovalDecision(
                turn_id="turn-reject-followup",
                approved=False,
                user_text="don't read files, just answer from memory",
            )
        )
        self.assertFalse(resumed.is_pending)
        combined = "\n".join(str(item.get("content") or "") for item in decide_messages[1])
        self.assertIn("don't read files, just answer from memory", combined)
        self.assertIn("did not approve the previously planned tool usage", combined)

    async def test_rejected_call_signature_blocks_identical_reproposal_in_next_round(self) -> None:
        decide_calls = 0

        async def fake_decide_turn(**_kwargs):
            nonlocal decide_calls
            decide_calls += 1
            if decide_calls == 1:
                return TurnDecision(
                    True,
                    "Need a file read",
                    "Read the draft first",
                    [ToolIntent("read_file", {"path": "draft.txt"})],
                    "Inspect draft.txt before answering.",
                )
            return TurnDecision(
                True,
                "Still trying the same thing",
                "Read the draft first",
                [ToolIntent("read_file", {"path": "draft.txt"})],
                "Inspect draft.txt before answering.",
            )

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda _state: _PhaseBridge(["read_file"]),
        )

        runtime = self._build_runtime(deps, "test_agent_graph_reject_blacklist.pkl")
        outcome = await runtime.start_turn(_react_state(turn_id="turn-reject-blacklist", user_text="summarize the draft"))
        self.assertTrue(outcome.is_pending)

        resumed = await runtime.resume_turn(
            ApprovalDecision(
                turn_id="turn-reject-blacklist",
                approved=False,
                user_text="不要读文件，直接给我结论",
            )
        )
        self.assertFalse(resumed.is_pending)
        self.assertEqual(resumed.state["loop_round"], 2)
        self.assertEqual(
            resumed.state["rejected_call_signatures"],
            ['read_file:{"path": "draft.txt"}'],
        )

    async def test_react_direct_answer_route_still_runs_decision_stage(self) -> None:
        decide_tool_names: list[list[str]] = []

        async def fake_decide_turn(**kwargs):
            tool_names = [str(item.get("function", {}).get("name") or "") for item in (kwargs.get("tools") or [])]
            decide_tool_names.append(tool_names)
            return TurnDecision(
                True,
                "React direct_answer still gets a decision pass",
                "Enter the agent loop",
                [ToolIntent("system.agent_loop", {"task": "fallback"})],
            )

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda state: _StateAwareBridge(state),
        )

        runtime = self._build_runtime(deps, "test_agent_graph_direct_answer.pkl")
        outcome = await runtime.start_turn(
            _react_state(
                turn_id="turn-direct-answer",
                user_text="help me think",
                route_kind="direct_answer",
                execution_phase="skill_selection",
            )
        )
        self.assertTrue(outcome.is_pending)
        self.assertEqual(decide_tool_names[0], [])

    async def test_route_skill_task_still_uses_direct_skill_execution(self) -> None:
        decide_tool_names: list[list[str]] = []

        async def fake_decide_turn(**kwargs):
            tool_names = [str(item.get("function", {}).get("name") or "") for item in (kwargs.get("tools") or [])]
            decide_tool_names.append(tool_names)
            return TurnDecision(
                True,
                "Use the routed visible skill",
                "Run the visible skill",
                [ToolIntent("skill.visible-skill.run_task", {"task": "finish it"})],
            )

        deps = GraphDependencies(
            decide_turn=fake_decide_turn,
            execute_tool_calls=_successful_execute_tool_calls,
            get_mcp_bridge=lambda: _FakeBridge(),
            load_tooling_config=lambda: {"max_tool_calls_per_turn": 6},
            build_tool_bridge=lambda state: _StateAwareBridge(state),
        )

        runtime = self._build_runtime(deps, "test_agent_graph_skill_route.pkl")
        state = _react_state(
            turn_id="turn-skill-route",
            user_text="use the visible skill",
            route_kind="skill_task",
            execution_phase="skill_execution",
            selection_origin="route",
            selected_skill_ids=["visible-skill"],
        )
        outcome = await runtime.start_turn(state)
        self.assertTrue(outcome.is_pending)
        self.assertEqual(decide_tool_names[0], [])

    def test_react_empty_skill_task_falls_back_to_complex_task(self) -> None:
        route = _route_from_assistant_message(
            "react",
            {
                "content": json.dumps(
                    {
                        "route_kind": "skill_task",
                        "thought_summary": "try skill first",
                        "skill_ids": [],
                    },
                    ensure_ascii=False,
                )
            },
            allowed_skill_ids=set(),
            allowed_tool_names={"system.capability_search", "system.agent_loop"},
        )

        self.assertEqual(route.route_kind, "complex_task")
        self.assertEqual(route.skill_ids, [])
