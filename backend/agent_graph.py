from __future__ import annotations

import pickle
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .agent_orchestrator import (
    ToolExecution,
    ToolIntent,
    TurnDecision,
    approval_tool_items,
    build_approved_tool_message,
    build_rejected_tool_message,
    deserialize_tool_calls,
    serialize_tool_calls,
)


class AgentRunState(TypedDict, total=False):
    turn_id: str
    session_id: str
    chat_mode: str
    router_used: bool
    route_kind: str
    route_thought_summary: str
    route_skill_ids: list[str]
    route_tool_candidates: list[str]
    route_tool_call: dict[str, Any]
    route_search_needed: bool
    route_search_query: str
    model: str
    llm_provider: str
    api_base_url: str
    api_key: str
    memory_window: int
    system_prompt: str
    active_skill_ids: list[str]
    skill_prompt_text: str
    expression_mode: bool
    expression_output_format: str
    available_expressions: list[str]
    tools_enabled: bool
    react_enabled: bool
    max_reasoning_steps: int
    reasoning_step: int
    user_text: str
    pet_display_name: str
    working_messages: list[dict[str, Any]]
    decision_messages: list[dict[str, Any]]
    prompt_messages: list[dict[str, Any]]
    followup_messages: list[dict[str, Any]]
    decision: dict[str, Any]
    tool_plan: list[dict[str, Any]]
    thought_summary: str
    approval_request: dict[str, Any]
    approval_decision: dict[str, Any]
    tool_results: list[dict[str, Any]]
    executed_call_signatures: list[str]
    needs_additional_approval: bool
    final_messages: list[dict[str, Any]]
    final_answer: str
    trace: list[dict[str, Any]]


@dataclass(frozen=True)
class ApprovalDecision:
    turn_id: str
    approved: bool

    @classmethod
    def from_resume(cls, turn_id: str, raw: Any) -> "ApprovalDecision":
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, dict):
            return cls(turn_id=turn_id, approved=bool(raw.get("approved")))
        return cls(turn_id=turn_id, approved=bool(raw))

    def to_dict(self) -> dict[str, Any]:
        return {"turn_id": self.turn_id, "approved": self.approved}


@dataclass
class GraphTurnOutcome:
    turn_id: str
    state: AgentRunState
    thought_summary: str = ""
    approval_request: dict[str, Any] | None = None
    final_messages: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_pending(self) -> bool:
        return self.approval_request is not None


@dataclass(frozen=True)
class GraphDependencies:
    decide_turn: Callable[..., Awaitable[TurnDecision]]
    execute_tool_calls: Callable[..., list[ToolExecution]]
    get_mcp_bridge: Callable[[], Any]
    load_tooling_config: Callable[[], dict[str, Any]]
    build_tool_bridge: Callable[[AgentRunState], Any] | None = None


class PersistentInMemorySaver(InMemorySaver):
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        super().__init__()
        self._load_from_disk()

    def _restore_storage(
        self,
        raw: dict[str, dict[str, dict[str, tuple[tuple[str, bytes], tuple[str, bytes], str | None]]]],
    ) -> defaultdict[str, dict[str, dict[str, tuple[tuple[str, bytes], tuple[str, bytes], str | None]]]]:
        storage: defaultdict[str, dict[str, dict[str, tuple[tuple[str, bytes], tuple[str, bytes], str | None]]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        for thread_id, ns_map in raw.items():
            nested = defaultdict(dict)
            for checkpoint_ns, checkpoint_map in ns_map.items():
                nested[checkpoint_ns] = dict(checkpoint_map)
            storage[thread_id] = nested
        return storage

    def _plain_storage(self) -> dict[str, dict[str, dict[str, tuple[tuple[str, bytes], tuple[str, bytes], str | None]]]]:
        out: dict[str, dict[str, dict[str, tuple[tuple[str, bytes], tuple[str, bytes], str | None]]]] = {}
        for thread_id, ns_map in self.storage.items():
            out[thread_id] = {}
            for checkpoint_ns, checkpoint_map in ns_map.items():
                out[thread_id][checkpoint_ns] = dict(checkpoint_map)
        return out

    def _plain_writes(
        self,
    ) -> dict[tuple[str, str, str], dict[tuple[str, int], tuple[str, str, tuple[str, bytes], str]]]:
        return {tuple(key): dict(value) for key, value in self.writes.items()}

    def _load_from_disk(self) -> None:
        if not self.path.exists():
            return
        with self._lock:
            try:
                data = pickle.loads(self.path.read_bytes())
            except Exception:
                return
            storage = data.get("storage", {})
            writes = data.get("writes", {})
            blobs = data.get("blobs", {})
            if isinstance(storage, dict):
                self.storage = self._restore_storage(storage)
            if isinstance(writes, dict):
                self.writes = defaultdict(dict)
                for key, value in writes.items():
                    self.writes[tuple(key)] = dict(value)
            if isinstance(blobs, dict):
                self.blobs = dict(blobs)

    def _persist(self) -> None:
        with self._lock:
            payload = {
                "storage": self._plain_storage(),
                "writes": self._plain_writes(),
                "blobs": dict(self.blobs),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(pickle.dumps(payload))

    def put(self, config, checkpoint, metadata, new_versions):
        result = super().put(config, checkpoint, metadata, new_versions)
        self._persist()
        return result

    def put_writes(self, config, writes, task_id, task_path=""):
        super().put_writes(config, writes, task_id, task_path)
        self._persist()

    def delete_thread(self, thread_id: str) -> None:
        super().delete_thread(thread_id)
        self._persist()

    async def aput(self, config, checkpoint, metadata, new_versions):
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        self.delete_thread(thread_id)


def _tool_call_signature(name: str, arguments: dict[str, Any] | None) -> str:
    import json

    try:
        args_text = json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True)
    except Exception:
        args_text = json.dumps(str(arguments or {}), ensure_ascii=False)
    return f"{str(name or '').strip()}:{args_text}"


def _filter_repeated_tool_calls(
    tool_calls: list[ToolIntent],
    seen_signatures: set[str],
) -> list[ToolIntent]:
    filtered: list[ToolIntent] = []
    local_seen: set[str] = set()
    for item in tool_calls:
        signature = _tool_call_signature(item.name, item.arguments)
        if signature in seen_signatures or signature in local_seen:
            continue
        local_seen.add(signature)
        filtered.append(item)
    return filtered


def _deserialize_tool_executions(raw_items: list[dict[str, Any]] | None) -> list[ToolExecution]:
    items: list[ToolExecution] = []
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            continue
        arguments = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else {}
        items.append(
            ToolExecution(
                name=str(raw.get("name") or ""),
                arguments=arguments,
                ok=bool(raw.get("ok")),
                summary=str(raw.get("summary") or ""),
                payload=str(raw.get("payload") or ""),
            )
        )
    return items


def _final_tool_results_prompt(executions: list[ToolExecution], user_text: str = "") -> str:
    lines = [
        "The following tool steps have already been executed for this turn.",
        "Use these results to produce the best possible final answer.",
        "Do not claim that additional tools were executed, and do not request more tools in this final reply.",
    ]
    if user_text:
        lines.append(f"Original user request: {user_text}")
    for item in executions:
        lines.append(
            f"- {item.name} | ok={str(bool(item.ok)).lower()} | summary={item.summary} | payload={item.payload}"
        )
    return "\n".join(lines)


def _continuation_recheck_prompt(user_text: str, remaining_steps: int) -> str:
    clean_text = str(user_text or "").strip()
    lines = [
        "Re-check whether the user's original request is truly finished.",
        "Do not say the task is complete if any requested step is still unfinished.",
        "If the user asked for a sequence of actions, and only part of the sequence has been completed, you must request the next tool step.",
        f"Remaining approved tool steps available: {max(0, int(remaining_steps))}.",
        "Return JSON only in the normal decision format.",
    ]
    if clean_text:
        lines.append(f"Original user request: {clean_text}")
    return "\n".join(lines)


def _append_trace(state: AgentRunState, event: str, **payload: Any) -> list[dict[str, Any]]:
    trace = list(state.get("trace") or [])
    trace.append({"event": event, "ts": time.time(), **payload})
    return trace


def _route_tool_intent(state: AgentRunState) -> ToolIntent | None:
    payload = state.get("route_tool_call")
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name") or "").strip()
    if not name:
        return None
    arguments = payload.get("arguments")
    return ToolIntent(name=name, arguments=arguments if isinstance(arguments, dict) else {})


def _route_action_message(state: AgentRunState, intent: ToolIntent | None) -> str:
    payload = state.get("route_tool_call")
    if isinstance(payload, dict):
        text = str(payload.get("action_message") or "").strip()
        if text:
            return text
    if intent is None:
        return ""
    return f"I will call {intent.name} next."


class AgentGraphRuntime:
    def __init__(
        self,
        *,
        dependency_provider: Callable[[], GraphDependencies],
        checkpoint_path: Path,
    ) -> None:
        self._dependency_provider = dependency_provider
        self._checkpointer = PersistentInMemorySaver(checkpoint_path)
        self._graph = self._build_graph()

    def _deps(self) -> GraphDependencies:
        return self._dependency_provider()

    def _build_graph(self):
        builder = StateGraph(AgentRunState)
        builder.add_node("prepare_context", self._prepare_context)
        builder.add_node("decide_or_respond", self._decide_or_respond)
        builder.add_node("require_approval", self._require_approval)
        builder.add_node("execute_tools", self._execute_tools)
        builder.add_node("continuation_check", self._continuation_check)
        builder.add_node("final_reply", self._final_reply)
        builder.add_edge(START, "prepare_context")
        builder.add_edge("prepare_context", "decide_or_respond")
        builder.add_conditional_edges(
            "decide_or_respond",
            self._route_after_decide,
            {
                "require_approval": "require_approval",
                "final_reply": "final_reply",
            },
        )
        builder.add_conditional_edges(
            "require_approval",
            self._route_after_approval,
            {
                "execute_tools": "execute_tools",
                "final_reply": "final_reply",
            },
        )
        builder.add_edge("execute_tools", "continuation_check")
        builder.add_conditional_edges(
            "continuation_check",
            self._route_after_continuation,
            {
                "require_approval": "require_approval",
                "final_reply": "final_reply",
            },
        )
        builder.add_edge("final_reply", END)
        return builder.compile(checkpointer=self._checkpointer)

    async def start_turn(self, state: AgentRunState) -> GraphTurnOutcome:
        turn_id = str(state.get("turn_id") or "").strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        config = {"configurable": {"thread_id": turn_id}}
        await self._graph.ainvoke(state, config=config)
        return self._snapshot_to_outcome(turn_id, config)

    async def resume_turn(self, decision: ApprovalDecision) -> GraphTurnOutcome:
        turn_id = str(decision.turn_id or "").strip()
        if not turn_id:
            raise KeyError("missing turn_id")
        config = {"configurable": {"thread_id": turn_id}}
        snapshot = self._graph.get_state(config)
        if snapshot is None or not snapshot.interrupts:
            raise KeyError(turn_id)
        await self._graph.ainvoke(Command(resume=decision.to_dict()), config=config)
        return self._snapshot_to_outcome(turn_id, config)

    def get_pending_approval(self, turn_id: str) -> dict[str, Any] | None:
        config = {"configurable": {"thread_id": str(turn_id or "").strip()}}
        snapshot = self._graph.get_state(config)
        if snapshot is None or not snapshot.interrupts:
            return None
        interrupt_payload = snapshot.interrupts[0].value
        return interrupt_payload if isinstance(interrupt_payload, dict) else None

    def get_state_values(self, turn_id: str) -> AgentRunState | None:
        config = {"configurable": {"thread_id": str(turn_id or "").strip()}}
        snapshot = self._graph.get_state(config)
        if snapshot is None:
            return None
        values = snapshot.values or {}
        return values if isinstance(values, dict) else None

    def delete_turn(self, turn_id: str) -> None:
        target = str(turn_id or "").strip()
        if not target:
            return
        self._checkpointer.delete_thread(target)

    def pending_turn_ids(self) -> list[str]:
        pending: set[str] = set()
        for item in self._checkpointer.list(None):
            thread_id = str(item.config.get("configurable", {}).get("thread_id") or "").strip()
            if not thread_id or thread_id in pending:
                continue
            state = self.get_pending_approval(thread_id)
            if state is not None:
                pending.add(thread_id)
        return sorted(pending)

    def _snapshot_to_outcome(self, turn_id: str, config: dict[str, Any]) -> GraphTurnOutcome:
        snapshot = self._graph.get_state(config)
        values = snapshot.values or {}
        state = values if isinstance(values, dict) else {}
        approval_request: dict[str, Any] | None = None
        if snapshot.interrupts:
            raw = snapshot.interrupts[0].value
            approval_request = raw if isinstance(raw, dict) else None
        return GraphTurnOutcome(
            turn_id=turn_id,
            state=state,
            thought_summary=str(state.get("thought_summary") or ""),
            approval_request=approval_request,
            final_messages=list(state.get("final_messages") or []),
            trace=list(state.get("trace") or []),
        )

    async def _prepare_context(self, state: AgentRunState) -> AgentRunState:
        return {
            "reasoning_step": max(1, int(state.get("reasoning_step") or 1)),
            "followup_messages": list(state.get("followup_messages") or []),
            "tool_results": list(state.get("tool_results") or []),
            "executed_call_signatures": list(state.get("executed_call_signatures") or []),
            "approval_request": dict(state.get("approval_request") or {}),
            "approval_decision": dict(state.get("approval_decision") or {}),
            "final_messages": list(state.get("final_messages") or []),
            "needs_additional_approval": bool(state.get("needs_additional_approval", False)),
            "trace": _append_trace(
                state,
                "prepare_context",
                session_id=str(state.get("session_id") or ""),
                turn_id=str(state.get("turn_id") or ""),
            ),
        }

    async def _decide_or_respond(self, state: AgentRunState) -> AgentRunState:
        route_kind = str(state.get("route_kind") or "").strip()
        route_thought_summary = str(state.get("route_thought_summary") or "").strip()
        if route_kind == "direct_answer":
            return {
                "decision": {
                    "needs_tool": False,
                    "thought_summary": route_thought_summary,
                    "action_message": "",
                    "tool_calls": [],
                },
                "tool_plan": [],
                "thought_summary": route_thought_summary,
                "approval_request": {},
                "needs_additional_approval": False,
                "trace": _append_trace(state, "decide_or_respond", mode="route_direct_answer"),
            }

        if route_kind == "simple_tool_task":
            intent = _route_tool_intent(state)
            if intent is not None and bool(state.get("tools_enabled", True)):
                decision = TurnDecision(
                    needs_tool=True,
                    thought_summary=route_thought_summary,
                    action_message=_route_action_message(state, intent),
                    tool_calls=[intent],
                )
                return {
                    "decision": decision.to_dict(),
                    "tool_plan": serialize_tool_calls(decision.tool_calls),
                    "thought_summary": route_thought_summary,
                    "approval_request": {
                        "text": decision.action_message,
                        "tools": approval_tool_items(decision),
                        "speaker": "pet",
                    },
                    "needs_additional_approval": True,
                    "trace": _append_trace(state, "decide_or_respond", mode="route_simple_tool_task"),
                }

        if not bool(state.get("react_enabled", True)):
            return {
                "decision": {
                    "needs_tool": False,
                    "thought_summary": "",
                    "action_message": "",
                    "tool_calls": [],
                },
                "tool_plan": [],
                "thought_summary": "",
                "approval_request": {},
                "needs_additional_approval": False,
                "trace": _append_trace(state, "decide_or_respond", mode="react_disabled"),
            }

        deps = self._deps()
        tools_enabled = bool(state.get("tools_enabled", True))
        bridge = deps.build_tool_bridge(state) if tools_enabled and deps.build_tool_bridge else None
        if tools_enabled and bridge is None:
            bridge = deps.get_mcp_bridge()
        tools = bridge.list_tools() if bridge is not None else []
        decision = await deps.decide_turn(
            messages=list(state.get("decision_messages") or state.get("working_messages") or []),
            model=str(state.get("model") or ""),
            tools=tools,
            llm_provider=str(state.get("llm_provider") or "ollama"),
            api_base_url=str(state.get("api_base_url") or ""),
            api_key=str(state.get("api_key") or ""),
        )
        decision_payload = decision.to_dict()
        tool_plan = serialize_tool_calls(decision.tool_calls)
        approval_request = (
            {
                "text": decision.action_message,
                "tools": approval_tool_items(decision),
                "speaker": "pet",
            }
            if tools_enabled and decision.needs_tool and decision.tool_calls
            else {}
        )
        return {
            "decision": decision_payload,
            "tool_plan": tool_plan,
            "thought_summary": decision.thought_summary,
            "approval_request": approval_request,
            "needs_additional_approval": bool(approval_request),
            "trace": _append_trace(
                state,
                "decide_or_respond",
                needs_tool=bool(decision.needs_tool),
                tool_count=len(tool_plan),
            ),
        }

    def _route_after_decide(self, state: AgentRunState) -> str:
        decision = state.get("decision") or {}
        tool_plan = list(state.get("tool_plan") or [])
        if bool(state.get("tools_enabled", True)) and bool(decision.get("needs_tool")) and tool_plan:
            return "require_approval"
        return "final_reply"

    async def _require_approval(self, state: AgentRunState) -> AgentRunState:
        approval_request = dict(state.get("approval_request") or {})
        if not approval_request:
            decision = TurnDecision(
                needs_tool=bool((state.get("decision") or {}).get("needs_tool")),
                thought_summary=str((state.get("decision") or {}).get("thought_summary") or ""),
                action_message=str((state.get("decision") or {}).get("action_message") or ""),
                tool_calls=deserialize_tool_calls((state.get("decision") or {}).get("tool_calls")),
            )
            approval_request = {
                "text": decision.action_message,
                "tools": approval_tool_items(decision),
                "speaker": "pet",
            }
        resumed = interrupt(approval_request)
        decision = ApprovalDecision.from_resume(str(state.get("turn_id") or ""), resumed)
        return {
            "approval_decision": decision.to_dict(),
            "trace": _append_trace(
                state,
                "approval_resume",
                approved=decision.approved,
            ),
        }

    def _route_after_approval(self, state: AgentRunState) -> str:
        approval = state.get("approval_decision") or {}
        if bool(approval.get("approved")):
            return "execute_tools"
        return "final_reply"

    async def _execute_tools(self, state: AgentRunState) -> AgentRunState:
        deps = self._deps()
        tooling_cfg = deps.load_tooling_config()
        bridge = deps.build_tool_bridge(state) if deps.build_tool_bridge else None
        if bridge is None:
            bridge = deps.get_mcp_bridge()
        tool_calls = deserialize_tool_calls(list(state.get("tool_plan") or []))
        executions = deps.execute_tool_calls(
            tool_calls=tool_calls,
            mcp_bridge=bridge,
            max_tool_calls=int(tooling_cfg.get("max_tool_calls_per_turn", 6)),
        )
        executed_signatures = {
            str(item)
            for item in (state.get("executed_call_signatures") or [])
            if str(item or "").strip()
        }
        for item in tool_calls:
            executed_signatures.add(_tool_call_signature(item.name, item.arguments))
        current_results = [item.to_dict() for item in executions]
        all_tool_results = list(state.get("tool_results") or []) + current_results
        all_executions = _deserialize_tool_executions(all_tool_results)
        remaining_steps = max(0, int(state.get("max_reasoning_steps") or 1) - int(state.get("reasoning_step") or 1))
        followup_messages = list(state.get("decision_messages") or state.get("working_messages") or []) + [
            {
                "role": "system",
                "content": build_approved_tool_message(
                    all_executions,
                    remaining_steps=remaining_steps,
                    user_request=str(state.get("user_text") or ""),
                ),
            }
        ]
        return {
            "tool_results": all_tool_results,
            "followup_messages": followup_messages,
            "executed_call_signatures": sorted(executed_signatures),
            "approval_request": {},
            "needs_additional_approval": False,
            "trace": _append_trace(
                state,
                "execute_tools",
                tool_count=len(tool_calls),
                execution_count=len(executions),
            ),
        }

    async def _continuation_check(self, state: AgentRunState) -> AgentRunState:
        max_reasoning_steps = max(1, int(state.get("max_reasoning_steps") or 1))
        reasoning_step = max(1, int(state.get("reasoning_step") or 1))
        followup_messages = list(state.get("followup_messages") or [])
        cumulative_executions = _deserialize_tool_executions(list(state.get("tool_results") or []))
        final_messages = list(state.get("prompt_messages") or [])
        if cumulative_executions:
            final_messages = final_messages + [
                {
                    "role": "system",
                    "content": _final_tool_results_prompt(
                        cumulative_executions,
                        user_text=str(state.get("user_text") or ""),
                    ),
                }
            ]
        if str(state.get("route_kind") or "").strip() == "simple_tool_task":
            return {
                "final_messages": final_messages,
                "tool_plan": [],
                "approval_request": {},
                "needs_additional_approval": False,
                "trace": _append_trace(
                    state,
                    "continuation_check",
                    needs_tool=False,
                    tool_count=0,
                    reason="route_simple_tool",
                ),
            }
        if (
            bool(state.get("react_enabled", True))
            and bool(state.get("tools_enabled", True))
            and reasoning_step < max_reasoning_steps
        ):
            deps = self._deps()
            bridge = deps.build_tool_bridge(state) if deps.build_tool_bridge else None
            if bridge is None:
                bridge = deps.get_mcp_bridge()
            tools = bridge.list_tools() if bridge is not None else []
            executed_signatures = {
                str(item)
                for item in (state.get("executed_call_signatures") or [])
                if str(item or "").strip()
            }
            next_decision = await deps.decide_turn(
                messages=followup_messages,
                model=str(state.get("model") or ""),
                tools=tools,
                llm_provider=str(state.get("llm_provider") or "ollama"),
                api_base_url=str(state.get("api_base_url") or ""),
                api_key=str(state.get("api_key") or ""),
            )
            next_decision.tool_calls = _filter_repeated_tool_calls(next_decision.tool_calls, executed_signatures)
            next_decision.needs_tool = bool(next_decision.needs_tool and next_decision.tool_calls)
            remaining_steps = max(0, max_reasoning_steps - reasoning_step)
            if not next_decision.needs_tool and remaining_steps > 0:
                reconsider_messages = list(followup_messages) + [
                    {
                        "role": "system",
                        "content": _continuation_recheck_prompt(str(state.get("user_text") or ""), remaining_steps),
                    }
                ]
                reconsidered = await deps.decide_turn(
                    messages=reconsider_messages,
                    model=str(state.get("model") or ""),
                    tools=tools,
                    llm_provider=str(state.get("llm_provider") or "ollama"),
                    api_base_url=str(state.get("api_base_url") or ""),
                    api_key=str(state.get("api_key") or ""),
                )
                reconsidered.tool_calls = _filter_repeated_tool_calls(reconsidered.tool_calls, executed_signatures)
                reconsidered.needs_tool = bool(reconsidered.needs_tool and reconsidered.tool_calls)
                if reconsidered.needs_tool:
                    next_decision = reconsidered
            if next_decision.needs_tool and next_decision.tool_calls:
                return {
                    "decision": next_decision.to_dict(),
                    "tool_plan": serialize_tool_calls(next_decision.tool_calls),
                    "thought_summary": next_decision.thought_summary,
                    "approval_request": {
                        "text": next_decision.action_message,
                        "tools": approval_tool_items(next_decision),
                        "speaker": "pet",
                    },
                    "needs_additional_approval": True,
                    "reasoning_step": reasoning_step + 1,
                    "final_messages": [],
                    "trace": _append_trace(
                        state,
                        "continuation_check",
                        needs_tool=True,
                        tool_count=len(next_decision.tool_calls),
                    ),
                }
            return {
                "thought_summary": next_decision.thought_summary,
                "final_messages": final_messages,
                "tool_plan": [],
                "approval_request": {},
                "needs_additional_approval": False,
                "trace": _append_trace(
                    state,
                    "continuation_check",
                    needs_tool=False,
                    tool_count=0,
                ),
            }

        if reasoning_step >= max_reasoning_steps:
            final_messages = list(final_messages) + [
                {
                    "role": "system",
                    "content": (
                        "You have reached the maximum approved tool steps for this turn. "
                        "Do not request more tools. Provide the best possible final answer using the completed results only."
                    ),
                }
            ]
        return {
            "final_messages": final_messages,
            "tool_plan": [],
            "approval_request": {},
            "needs_additional_approval": False,
            "trace": _append_trace(
                state,
                "continuation_check",
                needs_tool=False,
                tool_count=0,
                reason="limit" if reasoning_step >= max_reasoning_steps else "done",
            ),
        }

    def _route_after_continuation(self, state: AgentRunState) -> str:
        if bool(state.get("needs_additional_approval")) and state.get("approval_request"):
            return "require_approval"
        return "final_reply"

    async def _final_reply(self, state: AgentRunState) -> AgentRunState:
        final_messages = list(state.get("final_messages") or [])
        if not final_messages:
            approval = state.get("approval_decision") or {}
            if approval and not bool(approval.get("approved")):
                decision_payload = state.get("decision") or {}
                decision = TurnDecision(
                    needs_tool=bool(decision_payload.get("needs_tool")),
                    thought_summary=str(decision_payload.get("thought_summary") or ""),
                    action_message=str(decision_payload.get("action_message") or ""),
                    tool_calls=deserialize_tool_calls(decision_payload.get("tool_calls")),
                )
                final_messages = list(state.get("prompt_messages") or []) + [
                    {"role": "system", "content": build_rejected_tool_message(decision)}
                ]
            elif state.get("followup_messages"):
                final_messages = list(state.get("followup_messages") or [])
            else:
                final_messages = list(state.get("prompt_messages") or [])
        return {
            "final_messages": final_messages,
            "needs_additional_approval": False,
            "trace": _append_trace(
                state,
                "final_reply",
                message_count=len(final_messages),
            ),
        }
