from __future__ import annotations

import json
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
    deserialize_tool_calls,
    _default_expected_effect,
    serialize_tool_calls,
)
from .runtime_prompts import (
    build_approved_tool_message,
    build_inventory_result_message,
    build_rejected_tool_followup_message,
    build_rejected_tool_message,
    action_recheck_prompt as _rt_action_recheck_prompt,
    continuation_recheck_prompt as _rt_continuation_recheck_prompt,
    final_tool_results_prompt as _rt_final_tool_results_prompt,
    leader_assessment_prompt as _rt_leader_assessment_prompt,
    loop_round_limit_prompt as _rt_loop_round_limit_prompt,
    no_progress_stop_prompt as _rt_no_progress_stop_prompt,
    planner_none_observation_text as _rt_planner_none_observation_text,
    planner_blocked_final_prompt as _rt_planner_blocked_final_prompt,
    planner_selected_skill_followup_prompt as _rt_planner_selected_skill_followup_prompt,
    planner_selected_skill_round_prompt as _rt_planner_selected_skill_round_prompt,
    post_meta_tool_guidance_prompt as _rt_post_meta_tool_guidance_prompt,
    tool_reflection_prompt as _rt_tool_reflection_prompt,
    tool_step_limit_prompt as _rt_tool_step_limit_prompt,
    writer_bundle_blocked_prompt as _rt_writer_bundle_blocked_prompt,
    writer_step_prompt as _rt_writer_step_prompt,
    writer_step_template_prompt as _rt_writer_step_template_prompt,
    writer_tool_call_template_prompt as _rt_writer_tool_call_template_prompt,
    writer_prompt_from_planner as _rt_writer_prompt_from_planner,
)

SYSTEM_TOOL_AGENT_LOOP = "system.agent_loop"


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
    settings_config: dict[str, Any]
    tooling_config: dict[str, Any]
    active_skill_ids: list[str]
    skill_prompt_text: str
    execution_phase: str
    selection_origin: str
    selected_skill_ids: list[str]
    selected_tool_names: list[str]
    planner_excluded_skill_ids: list[str]
    planner_excluded_tool_names: list[str]
    planner_retry_used: bool
    current_searcher_result: dict[str, Any]
    current_execution_plan: dict[str, Any]
    current_step_index: int
    current_step_attempt: int
    current_candidate_tool_set_index: int
    current_candidate_no_progress: int
    current_batch_id: str
    latest_batch_results: list[dict[str, Any]]
    completed_steps: list[str]
    blocked_steps: list[str]
    step_history: list[dict[str, Any]]
    writer_loop_active: bool
    loop_round: int
    last_expected_effect: str
    last_assessment: str
    last_inventory_result: dict[str, Any]
    rejected_call_signatures: list[str]
    no_progress_streak: int
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
    phase_events: list[dict[str, Any]]
    trace: list[dict[str, Any]]


@dataclass(frozen=True)
class ApprovalDecision:
    turn_id: str
    approved: bool
    user_text: str = ""

    @classmethod
    def from_resume(cls, turn_id: str, raw: Any) -> "ApprovalDecision":
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, dict):
            return cls(
                turn_id=turn_id,
                approved=bool(raw.get("approved")),
                user_text=str(raw.get("user_text") or ""),
            )
        return cls(turn_id=turn_id, approved=bool(raw))

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "approved": self.approved,
            "user_text": self.user_text,
        }


@dataclass
class GraphTurnOutcome:
    turn_id: str
    state: AgentRunState
    thought_summary: str = ""
    approval_request: dict[str, Any] | None = None
    final_messages: list[dict[str, Any]] = field(default_factory=list)
    phase_events: list[dict[str, Any]] = field(default_factory=list)
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
    search_capabilities: Callable[..., Awaitable[dict[str, Any]]] | None = None
    build_execution_plan: Callable[..., Awaitable[dict[str, Any]]] | None = None
    plan_capabilities: Callable[..., Awaitable[dict[str, Any]]] | None = None
    resolve_skill_prompt_text: Callable[[list[str], AgentRunState], str] | None = None
    decide_need_for_tools: Callable[..., Awaitable[TurnDecision]] | None = None


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


def _all_tool_calls_are_system(tool_calls: list[ToolIntent]) -> bool:
    if not tool_calls:
        return False
    return all(str(item.name or "").strip().startswith("system.") for item in tool_calls)


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
    return _rt_final_tool_results_prompt(executions, user_text=user_text)


def _continuation_recheck_prompt(user_text: str, remaining_steps: int) -> str:
    return _rt_continuation_recheck_prompt(user_text, remaining_steps)


def _has_successful_non_system_execution(executions: list[ToolExecution]) -> bool:
    return any(item.ok and not str(item.name or "").strip().startswith("system.") for item in executions)


def _successful_meta_tool_names(executions: list[ToolExecution]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for item in executions:
        name = str(item.name or "").strip()
        if not item.ok or name in seen:
            continue
        if name != SYSTEM_TOOL_AGENT_LOOP:
            continue
        seen.add(name)
        names.append(name)
    return names


def _post_meta_tool_guidance_prompt(
    state: AgentRunState,
    executions: list[ToolExecution],
    *,
    remaining_steps: int,
    strict: bool = False,
) -> str:
    successful_meta = _successful_meta_tool_names(executions)
    if not successful_meta or _has_successful_non_system_execution(executions):
        return ""

    selected_tools = _normalize_name_list(state.get("selected_tool_names") or [])
    selected_skills = _normalize_name_list(state.get("selected_skill_ids") or [])
    return _rt_post_meta_tool_guidance_prompt(
        successful_meta=successful_meta,
        selected_skills=selected_skills,
        selected_tools=selected_tools,
        remaining_steps=remaining_steps,
        strict=strict,
        system_tool_agent_loop=SYSTEM_TOOL_AGENT_LOOP,
    )


def _must_continue_after_meta_tools(state: AgentRunState, executions: list[ToolExecution]) -> bool:
    if _has_successful_non_system_execution(executions):
        return False
    successful_meta = set(_successful_meta_tool_names(executions))
    return SYSTEM_TOOL_AGENT_LOOP in successful_meta


def _merge_user_followup_text(original_text: str, followup_text: str) -> str:
    original = str(original_text or "").strip()
    followup = str(followup_text or "").strip()
    if not original:
        return followup
    if not followup:
        return original
    return f"{original}\n\nAdditional user guidance after rejecting a planned tool step: {followup}"


def _result_is_effectively_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set)):
        return len(value) == 0 or all(_result_is_effectively_empty(item) for item in value)
    if isinstance(value, dict):
        if not value:
            return True
        meaningful = {key: item for key, item in value.items() if key not in {"ok", "kind", "source", "source_type"}}
        if not meaningful:
            return True
        return all(_result_is_effectively_empty(item) for item in meaningful.values())
    return False


def _execution_had_no_effect(execution: ToolExecution) -> bool:
    if not execution.ok:
        return True
    payload = _parse_execution_payload(execution)
    if payload.get("ok") is False:
        return True
    if "result" in payload and _result_is_effectively_empty(payload.get("result")):
        return True
    if "result_preview" in payload and not str(payload.get("result_preview") or "").strip():
        return True
    summary = str(execution.summary or "").strip().lower()
    if not summary:
        return True
    return any(marker in summary for marker in ("no result", "no results", "no match", "no matches", "not found", "empty"))


def _tool_reflection_prompt(executions: list[ToolExecution]) -> str:
    ineffective: list[dict[str, Any]] = []
    seen: set[str] = set()
    for execution in executions:
        if not _execution_had_no_effect(execution):
            continue
        signature = _tool_call_signature(execution.name, execution.arguments)
        if signature in seen:
            continue
        seen.add(signature)
        ineffective.append(
            {
                "name": execution.name,
                "arguments": execution.arguments,
                "ok": execution.ok,
                "summary": execution.summary,
            }
        )
    return _rt_tool_reflection_prompt(ineffective)


def _append_trace(state: AgentRunState, event: str, **payload: Any) -> list[dict[str, Any]]:
    trace = list(state.get("trace") or [])
    trace.append({"event": event, "ts": time.time(), **payload})
    return trace


def _append_phase_event(
    events: list[dict[str, Any]] | None,
    phase: str,
    text: str,
    *,
    loop_round: int | None = None,
    thought_role: str = "",
    speaker: str = "pet",
    **extra: Any,
) -> list[dict[str, Any]]:
    clean_text = str(text or "").strip()
    if not clean_text:
        return list(events or [])
    payload: dict[str, Any] = {
        "phase": str(phase or "").strip().lower(),
        "text": clean_text,
        "speaker": str(speaker or "pet"),
    }
    if loop_round is not None:
        payload["loop_round"] = max(1, int(loop_round))
    if thought_role:
        payload["thought_role"] = str(thought_role or "").strip().lower()
    for key, value in extra.items():
        if value is None:
            continue
        payload[str(key)] = value
    return list(events or []) + [payload]


def _approval_request_text(decision: TurnDecision) -> str:
    action_message = str(decision.action_message or "").strip()
    expected_effect = str(decision.expected_effect or "").strip()
    if action_message and expected_effect:
        return f"{action_message}\nExpected effect: {expected_effect}"
    return action_message


def _approval_request_text_for_step(state: AgentRunState, step: dict[str, Any], decision: TurnDecision) -> str:
    steps = dict(state.get("current_execution_plan") or {}).get("steps")
    total_steps = len(steps) if isinstance(steps, list) else 0
    step_label = f"Step {max(0, int(state.get('current_step_index') or 0)) + 1}/{max(1, total_steps)}"
    title = str(step.get("title") or "").strip()
    batch_size = len(list(decision.tool_calls or []))
    header = step_label if not title else f"{step_label}: {title}"
    body = _approval_request_text(decision)
    batch_line = f"Batch: {batch_size} tool call{'s' if batch_size != 1 else ''}"
    return "\n".join([line for line in [header, batch_line, body] if line])


def _writer_phase_text(decision: TurnDecision) -> str:
    lines: list[str] = []
    action_message = str(decision.action_message or "").strip()
    if action_message:
        lines.append(action_message)
    tool_calls = list(decision.tool_calls or [])
    if tool_calls:
        lines.append("Planned calls:")
        for intent in tool_calls[:4]:
            arguments = intent.arguments if isinstance(intent.arguments, dict) else {}
            if arguments:
                try:
                    args_text = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
                except Exception:
                    args_text = json.dumps(str(arguments), ensure_ascii=False)
                lines.append(f"- {intent.name} {args_text}")
            else:
                lines.append(f"- {intent.name}")
        if len(tool_calls) > 4:
            lines.append(f"- ... and {len(tool_calls) - 4} more call(s)")
    expected_effect = str(decision.expected_effect or "").strip()
    if expected_effect:
        lines.append(f"Expected effect: {expected_effect}")
    return "\n".join(lines) if lines else "I have organized the next concrete tool step for approval."


def _latest_inventory_result(executions: list[ToolExecution]) -> dict[str, Any]:
    for item in reversed(executions):
        payload = _parse_execution_payload(item)
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        if str(result.get("mode") or "").strip().lower() == "inventory":
            return dict(result)
    return {}


def _latest_non_system_execution(executions: list[ToolExecution]) -> ToolExecution | None:
    for item in reversed(executions):
        name = str(item.name or "").strip()
        if not name.startswith("system."):
            return item
    return None


def _latest_progress_was_effective(executions: list[ToolExecution]) -> bool:
    latest = _latest_non_system_execution(executions)
    if latest is None:
        return False
    return not _execution_had_no_effect(latest)


def _batch_execution_progress(executions: list[ToolExecution]) -> dict[str, Any]:
    any_ok = any(item.ok for item in executions)
    effective_ok = any(item.ok and not _execution_had_no_effect(item) for item in executions)
    completed = False
    blocked = False
    for item in executions:
        payload = _parse_execution_payload(item)
        if payload.get("plan_completed") is True or payload.get("step_completed") is True:
            completed = True
        status = str(payload.get("step_status") or payload.get("status") or "").strip().lower()
        if status in {"completed", "done", "success"}:
            completed = True
        if payload.get("step_blocked") is True or payload.get("blocked") is True:
            blocked = True
        if status in {"blocked", "failed"}:
            blocked = True
    return {
        "completed": completed,
        "blocked": blocked,
        "effective_progress": effective_ok,
        "any_ok": any_ok,
    }


def _planner_phase_text(plan_result: dict[str, Any]) -> str:
    mode = str(plan_result.get("mode") or "plan").strip().lower()
    if mode == "inventory":
        return str(
            plan_result.get("inventory_summary")
            or plan_result.get("thought_summary")
            or "I listed the currently available skills and tools."
        ).strip()
    selection_kind = str(plan_result.get("selection_kind") or "none").strip().lower()
    names = _normalize_name_list(
        (plan_result.get("skill_ids") or []) if selection_kind == "skill" else (plan_result.get("tool_names") or [])
    )
    if selection_kind == "skill" and names:
        return f"I checked the default-enabled skills and chose: {', '.join(names)}."
    if selection_kind == "mcp" and names:
        return f"I checked the MCP tools and chose: {', '.join(names)}."
    if selection_kind == "none":
        observation = _rt_planner_none_observation_text(plan_result)
        if observation:
            return observation
    return str(plan_result.get("thought_summary") or plan_result.get("reason") or "I did not find a usable next capability.").strip()


def _planner_state_updates(plan_result: dict[str, Any]) -> dict[str, Any]:
    mode = str(plan_result.get("mode") or "plan").strip().lower()
    if mode == "inventory":
        return {
            "selection_origin": "none",
            "selected_skill_ids": [],
            "selected_tool_names": [],
            "execution_phase": "agent_loop",
            "last_inventory_result": dict(plan_result),
        }
    selection_kind = str(plan_result.get("selection_kind") or "none").strip().lower()
    if selection_kind == "skill":
        return {
            "selection_origin": "planner",
            "selected_skill_ids": _normalize_name_list(plan_result.get("skill_ids") or []),
            "selected_tool_names": [],
            "execution_phase": "skill_execution",
            "last_inventory_result": {},
        }
    if selection_kind == "mcp":
        return {
            "selection_origin": "planner",
            "selected_skill_ids": [],
            "selected_tool_names": _normalize_name_list(plan_result.get("tool_names") or []),
            "execution_phase": "agent_loop",
            "last_inventory_result": {},
        }
    return {
        "selection_origin": "none",
        "selected_skill_ids": [],
        "selected_tool_names": [],
        "execution_phase": "agent_loop",
        "last_inventory_result": {},
    }


def _writer_prompt_from_planner(state: AgentRunState, plan_result: dict[str, Any], *, strict: bool = False) -> str:
    return _rt_writer_prompt_from_planner(state, plan_result, strict=strict)


def _writer_tool_call_template_prompt(writer_tools: list[dict[str, Any]], plan_result: dict[str, Any]) -> str:
    return _rt_writer_tool_call_template_prompt(writer_tools, plan_result)


def _planner_blocked_final_prompt(plan_result: dict[str, Any], *, user_text: str) -> str:
    return _rt_planner_blocked_final_prompt(plan_result, user_text=user_text)


def _writer_blocked_phase_text(writer_tools: list[dict[str, Any]], plan_result: dict[str, Any]) -> str:
    tool_names: list[str] = []
    for schema in writer_tools or []:
        if not isinstance(schema, dict):
            continue
        function = schema.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if name and name not in tool_names:
            tool_names.append(name)
    selected_names = _normalize_name_list(plan_result.get("tool_names") or [])
    visible_names = tool_names or selected_names
    if visible_names:
        preview = ", ".join(visible_names[:5])
        suffix = "..." if len(visible_names) > 5 else ""
        return (
            f"I saw {len(visible_names)} visible tool(s) in the current planner bundle, "
            f"but the writer did not emit a concrete tool call yet: {preview}{suffix}."
        )
    return "I entered the writer stage, but no visible tools were exposed for a concrete tool call."


def _single_step_plan_from_capability_plan(plan_result: dict[str, Any]) -> dict[str, Any]:
    selection_kind = str(plan_result.get("selection_kind") or "none").strip().lower()
    if selection_kind == "skill":
        skill_ids = _normalize_name_list(plan_result.get("skill_ids") or [])
        title = "Use the selected skill workflow"
        notes = str(plan_result.get("usage_notes") or "").strip()
        return {
            "plan_id": "plan-1",
            "plan_summary": str(plan_result.get("thought_summary") or "Use the selected skill workflow.").strip(),
            "reason": str(plan_result.get("reason") or "").strip(),
            "steps": [
                {
                    "step_id": "step-1",
                    "title": title,
                    "goal": title,
                    "success_criteria": "The selected skill completes the next concrete workflow step.",
                    "call_mode": "single",
                    "candidate_tool_sets": [[]],
                    "notes": notes,
                }
            ],
        }
    tool_names = _normalize_name_list(plan_result.get("tool_names") or [])
    if tool_names:
        call_mode = "batch" if len(tool_names) > 1 else "single"
        return {
            "plan_id": "plan-1",
            "plan_summary": str(plan_result.get("thought_summary") or "Use the selected MCP tools.").strip(),
            "reason": str(plan_result.get("reason") or "").strip(),
            "steps": [
                {
                    "step_id": "step-1",
                    "title": "Use the selected MCP tools",
                    "goal": "Take the next concrete tool step for the user's request.",
                    "success_criteria": "The selected MCP tools make concrete progress on the user request.",
                    "call_mode": call_mode,
                    "candidate_tool_sets": [tool_names],
                    "notes": str(plan_result.get("usage_notes") or "").strip(),
                }
            ],
        }
    return {
        "plan_id": "plan-1",
        "plan_summary": str(plan_result.get("thought_summary") or "No plan was created.").strip(),
        "reason": str(plan_result.get("reason") or "").strip(),
        "steps": [],
    }


def _searcher_phase_text(searcher_result: dict[str, Any]) -> str:
    mode = str(searcher_result.get("mode") or "task_types").strip().lower() or "task_types"
    if mode == "inventory":
        return str(searcher_result.get("inventory_summary") or "I listed the currently available capabilities.").strip()
    if mode == "skill":
        skill_id = str(searcher_result.get("skill_id") or "").strip()
        if skill_id:
            return f"I matched the request to the default-enabled skill: {skill_id}."
        return "I matched the request to a default-enabled skill."
    task_types = _normalize_name_list(searcher_result.get("task_types") or [])
    matched_tools = _normalize_name_list(searcher_result.get("matched_tool_names") or [])
    if matched_tools:
        return f"I mapped the request to {', '.join(task_types or ['general_mcp'])} and narrowed the MCP tools to: {', '.join(matched_tools[:4])}."
    if task_types:
        return f"I mapped the request to the tool domains: {', '.join(task_types)}."
    return str(searcher_result.get("thought_summary") or "I checked the next capability path for this task.").strip()


def _planner_plan_phase_text(plan: dict[str, Any]) -> str:
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    summary = str(plan.get("plan_summary") or "").strip()
    if summary and steps:
        return f"{summary} Planned steps: {len(steps)}."
    if summary:
        return summary
    if steps:
        return f"I built a {len(steps)}-step execution plan."
    return "I built the next execution plan."


def _current_plan_step(state: AgentRunState) -> tuple[dict[str, Any], int, int]:
    plan = dict(state.get("current_execution_plan") or {})
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    step_index = max(0, int(state.get("current_step_index") or 0))
    if step_index >= len(steps):
        step_index = max(0, len(steps) - 1)
    step = dict(steps[step_index]) if steps else {}
    return plan, step_index, step


def _current_candidate_tool_set(state: AgentRunState, step: dict[str, Any]) -> list[str]:
    candidate_sets = step.get("candidate_tool_sets") if isinstance(step.get("candidate_tool_sets"), list) else []
    candidate_index = max(0, int(state.get("current_candidate_tool_set_index") or 0))
    if candidate_index >= len(candidate_sets):
        candidate_index = max(0, len(candidate_sets) - 1)
    candidate = candidate_sets[candidate_index] if candidate_sets else []
    if isinstance(candidate, list):
        return _normalize_name_list(candidate)
    return []


def _writer_step_prompt(state: AgentRunState, plan: dict[str, Any], step: dict[str, Any], candidate_tool_set: list[str], *, strict: bool = False) -> str:
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    return _rt_writer_step_prompt(
        user_text=str(state.get("user_text") or ""),
        plan=plan,
        step=step,
        candidate_tool_set=candidate_tool_set,
        step_index=max(0, int(state.get("current_step_index") or 0)),
        step_count=max(1, len(steps)),
        attempt=max(1, int(state.get("current_step_attempt") or 1)),
        strict=strict,
    )


def _writer_step_template_prompt(writer_tools: list[dict[str, Any]], step: dict[str, Any], candidate_tool_set: list[str]) -> str:
    return _rt_writer_step_template_prompt(writer_tools, step, candidate_tool_set)


def _step_phase_extra(
    state: AgentRunState,
    step: dict[str, Any],
    *,
    step_status: str = "",
    batch_id: str = "",
    batch_size: int = 0,
) -> dict[str, Any]:
    plan = dict(state.get("current_execution_plan") or {})
    return {
        "plan_id": str(plan.get("plan_id") or "").strip(),
        "step_id": str(step.get("step_id") or "").strip(),
        "step_index": max(0, int(state.get("current_step_index") or 0)) + 1,
        "step_title": str(step.get("title") or "").strip(),
        "step_status": str(step_status or "").strip(),
        "batch_id": str(batch_id or "").strip(),
        "batch_size": max(0, int(batch_size or 0)),
    }


def _append_step_phase_event(
    events: list[dict[str, Any]] | None,
    phase: str,
    text: str,
    *,
    state: AgentRunState,
    step: dict[str, Any],
    loop_round: int,
    thought_role: str = "",
    speaker: str = "pet",
    step_status: str = "",
    batch_id: str = "",
    batch_size: int = 0,
) -> list[dict[str, Any]]:
    return _append_phase_event(
        events,
        phase,
        text,
        loop_round=loop_round,
        thought_role=thought_role,
        speaker=speaker,
        **_step_phase_extra(
            state,
            step,
            step_status=step_status,
            batch_id=batch_id,
            batch_size=batch_size,
        ),
    )


def _append_trace_event_with_step(
    state: AgentRunState,
    event: str,
    *,
    step: dict[str, Any] | None = None,
    tool_names: list[str] | None = None,
    status: str = "",
    **payload: Any,
) -> list[dict[str, Any]]:
    plan = dict(state.get("current_execution_plan") or {})
    step_payload = dict(step or {})
    return _append_trace(
        state,
        event,
        plan_id=str(plan.get("plan_id") or "").strip(),
        step_id=str(step_payload.get("step_id") or "").strip(),
        step_index=max(0, int(state.get("current_step_index") or 0)) + 1,
        step_title=str(step_payload.get("title") or "").strip(),
        call_mode=str(step_payload.get("call_mode") or "").strip(),
        attempt=max(1, int(state.get("current_step_attempt") or 1)),
        candidate_tool_set_index=max(0, int(state.get("current_candidate_tool_set_index") or 0)),
        tool_names=list(tool_names or []),
        status=str(status or "").strip(),
        **payload,
    )


def _next_batch_id(state: AgentRunState) -> str:
    plan = dict(state.get("current_execution_plan") or {})
    plan_id = str(plan.get("plan_id") or "plan").strip() or "plan"
    step_id = str((_current_plan_step(state)[2] or {}).get("step_id") or "step").strip() or "step"
    attempt = max(1, int(state.get("current_step_attempt") or 1))
    candidate_index = max(0, int(state.get("current_candidate_tool_set_index") or 0))
    return f"{plan_id}:{step_id}:a{attempt}:c{candidate_index + 1}"


def _needs_action_recheck(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    if not text:
        return False
    action_markers = (
        "打开",
        "open ",
        "打开网站",
        "click",
        "点击",
        "保存",
        "save ",
        "search ",
        "搜索",
        "搜 ",
        "inspect",
        "查看",
        "collect",
        "收集",
        "navigate",
        "访问",
    )
    return any(marker in text for marker in action_markers)


def _action_recheck_prompt(user_text: str) -> str:
    return _rt_action_recheck_prompt(user_text)


def _leader_assessment_prompt(user_text: str, expected_effect: str, loop_round: int) -> str:
    return _rt_leader_assessment_prompt(user_text, expected_effect, loop_round)


def _max_loop_rounds(state: AgentRunState) -> int:
    return max(3, int(state.get("max_reasoning_steps") or 1) + 1)


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


def _normalize_name_list(raw_items: Any) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for raw in raw_items or []:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        items.append(value)
    return items


def _default_execution_phase(state: AgentRunState) -> str:
    if str(state.get("chat_mode") or "").strip().lower() != "react":
        return ""
    if str(state.get("route_kind") or "").strip() == "complex_task":
        return "agent_loop"
    selected_skill_ids = _normalize_name_list(state.get("selected_skill_ids") or [])
    if selected_skill_ids:
        return "skill_execution"
    return "skill_selection"


def _parse_execution_payload(execution: ToolExecution) -> dict[str, Any]:
    try:
        payload = json.loads(str(execution.payload or ""))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _skill_id_from_tool_name(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    if not name.startswith("skill."):
        return ""
    parts = name.split(".")
    if len(parts) < 3:
        return ""
    return str(parts[1] or "").strip()


def _state_updates_from_executions(
    state: AgentRunState,
    tool_calls: list[ToolIntent],
    executions: list[ToolExecution],
) -> dict[str, Any]:
    execution_phase = str(state.get("execution_phase") or _default_execution_phase(state)).strip()
    selection_origin = str(state.get("selection_origin") or "none").strip().lower() or "none"
    selected_skill_ids = _normalize_name_list(state.get("selected_skill_ids") or [])
    selected_tool_names = _normalize_name_list(state.get("selected_tool_names") or [])
    last_inventory_result = dict(state.get("last_inventory_result") or {})
    no_progress_streak = max(0, int(state.get("no_progress_streak") or 0))

    for intent in tool_calls:
        skill_id = _skill_id_from_tool_name(intent.name)
        if skill_id and skill_id not in selected_skill_ids:
            selected_skill_ids.append(skill_id)

    for execution in executions:
        if not execution.ok:
            continue
        if execution.name == "system.agent_loop":
            execution_phase = "agent_loop"
            selection_origin = "none"
            selected_skill_ids = []
            selected_tool_names = []
            last_inventory_result = {}

    if execution_phase != "agent_loop" and selected_skill_ids:
        execution_phase = "skill_execution"

    meta_progress = any(
        item.ok
        and str(item.name or "").strip() == SYSTEM_TOOL_AGENT_LOOP
        for item in executions
    )
    if _latest_progress_was_effective(executions) or meta_progress:
        no_progress_streak = 0
    elif executions:
        no_progress_streak += 1

    return {
        "execution_phase": execution_phase,
        "selection_origin": selection_origin,
        "selected_skill_ids": selected_skill_ids,
        "selected_tool_names": selected_tool_names,
        "last_inventory_result": dict(last_inventory_result) if isinstance(last_inventory_result, dict) else {},
        "no_progress_streak": no_progress_streak,
    }


def _state_updates_from_rejected_followup(
    state: AgentRunState,
    decision: ApprovalDecision,
) -> dict[str, Any]:
    followup_text = str(decision.user_text or "").strip()
    if not followup_text:
        return {}
    decision_payload = state.get("decision") or {}
    turn_decision = TurnDecision(
        needs_tool=bool(decision_payload.get("needs_tool")),
        thought_summary=str(decision_payload.get("thought_summary") or ""),
        action_message=str(decision_payload.get("action_message") or ""),
        tool_calls=deserialize_tool_calls(decision_payload.get("tool_calls")),
        expected_effect=str(decision_payload.get("expected_effect") or ""),
    )
    rejection_prompt = build_rejected_tool_followup_message(turn_decision, followup_text)
    base_decision_messages = list(state.get("followup_messages") or state.get("decision_messages") or state.get("working_messages") or [])
    updated_user_text = _merge_user_followup_text(str(state.get("user_text") or ""), followup_text)
    rejected_signatures = {
        str(item)
        for item in (state.get("rejected_call_signatures") or [])
        if str(item or "").strip()
    }
    for intent in turn_decision.tool_calls:
        rejected_signatures.add(_tool_call_signature(intent.name, intent.arguments))
    return {
        "loop_round": max(1, int(state.get("loop_round") or 1)) + 1,
        "user_text": updated_user_text,
        "working_messages": list(state.get("working_messages") or []) + [{"role": "user", "content": followup_text}],
        "decision_messages": base_decision_messages
        + [
            {"role": "system", "content": rejection_prompt},
            {"role": "user", "content": followup_text},
        ],
        "prompt_messages": list(state.get("prompt_messages") or [])
        + [
            {"role": "system", "content": rejection_prompt},
            {"role": "user", "content": followup_text},
        ],
        "followup_messages": [],
        "approval_request": {},
        "needs_additional_approval": False,
        "final_messages": [],
        "rejected_call_signatures": sorted(rejected_signatures),
        "last_assessment": "The user rejected the previous tool plan and gave new guidance for the next round.",
        "last_expected_effect": "",
    }


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
                "decide_or_respond": "decide_or_respond",
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
            phase_events=list(state.get("phase_events") or []),
            trace=list(state.get("trace") or []),
        )

    async def _prepare_context(self, state: AgentRunState) -> AgentRunState:
        return {
            "reasoning_step": max(1, int(state.get("reasoning_step") or 1)),
            "loop_round": max(1, int(state.get("loop_round") or 1)),
            "followup_messages": list(state.get("followup_messages") or []),
            "tool_results": list(state.get("tool_results") or []),
            "executed_call_signatures": list(state.get("executed_call_signatures") or []),
            "rejected_call_signatures": list(state.get("rejected_call_signatures") or []),
            "approval_request": dict(state.get("approval_request") or {}),
            "approval_decision": dict(state.get("approval_decision") or {}),
            "final_messages": list(state.get("final_messages") or []),
            "needs_additional_approval": bool(state.get("needs_additional_approval", False)),
            "execution_phase": str(state.get("execution_phase") or _default_execution_phase(state)),
            "selection_origin": str(state.get("selection_origin") or "none").strip().lower() or "none",
            "selected_skill_ids": _normalize_name_list(state.get("selected_skill_ids") or []),
            "selected_tool_names": _normalize_name_list(state.get("selected_tool_names") or []),
            "planner_excluded_skill_ids": _normalize_name_list(state.get("planner_excluded_skill_ids") or []),
            "planner_excluded_tool_names": _normalize_name_list(state.get("planner_excluded_tool_names") or []),
            "planner_retry_used": bool(state.get("planner_retry_used", False)),
            "current_searcher_result": dict(state.get("current_searcher_result") or {}),
            "current_execution_plan": dict(state.get("current_execution_plan") or {}),
            "current_step_index": max(0, int(state.get("current_step_index") or 0)),
            "current_step_attempt": max(1, int(state.get("current_step_attempt") or 1)),
            "current_candidate_tool_set_index": max(0, int(state.get("current_candidate_tool_set_index") or 0)),
            "current_candidate_no_progress": max(0, int(state.get("current_candidate_no_progress") or 0)),
            "current_batch_id": str(state.get("current_batch_id") or ""),
            "latest_batch_results": list(state.get("latest_batch_results") or []),
            "completed_steps": _normalize_name_list(state.get("completed_steps") or []),
            "blocked_steps": _normalize_name_list(state.get("blocked_steps") or []),
            "step_history": list(state.get("step_history") or []),
            "writer_loop_active": bool(state.get("writer_loop_active", False)),
            "last_expected_effect": str(state.get("last_expected_effect") or ""),
            "last_assessment": str(state.get("last_assessment") or ""),
            "last_inventory_result": dict(state.get("last_inventory_result") or {}),
            "no_progress_streak": max(0, int(state.get("no_progress_streak") or 0)),
            "phase_events": [],
            "trace": _append_trace(
                state,
                "prepare_context",
                session_id=str(state.get("session_id") or ""),
                turn_id=str(state.get("turn_id") or ""),
                execution_phase=str(state.get("execution_phase") or _default_execution_phase(state)),
            ),
        }

    async def _run_internal_planner_round(
        self,
        state: AgentRunState,
        *,
        messages: list[dict[str, Any]],
        final_message_base: list[dict[str, Any]],
        loop_round: int,
        phase_events: list[dict[str, Any]],
    ) -> AgentRunState:
        deps = self._deps()
        tools_enabled = bool(state.get("tools_enabled", True))
        user_text = str(state.get("user_text") or "")
        decider_messages = list(messages)
        action_recheck_prompt = _action_recheck_prompt(user_text)
        if action_recheck_prompt:
            decider_messages.append({"role": "system", "content": action_recheck_prompt})
        common_kwargs = {
            "messages": decider_messages,
            "model": str(state.get("model") or ""),
            "llm_provider": str(state.get("llm_provider") or "ollama"),
            "api_base_url": str(state.get("api_base_url") or ""),
            "api_key": str(state.get("api_key") or ""),
        }
        if deps.decide_need_for_tools is not None:
            decider = await deps.decide_need_for_tools(**common_kwargs)
        else:
            decider = await deps.decide_turn(tools=[], **common_kwargs)
        phase_events = _append_phase_event(
            phase_events,
            "thought",
            decider.thought_summary or ("I should keep working on this task." if decider.needs_tool else "I can answer directly now."),
            loop_round=loop_round,
            thought_role="decider",
        )
        blocked_signatures = {
            str(item)
            for item in (state.get("executed_call_signatures") or [])
            if str(item or "").strip()
        }
        blocked_signatures.update(
            str(item)
            for item in (state.get("rejected_call_signatures") or [])
            if str(item or "").strip()
        )
        trace = list(state.get("trace") or [])
        decider.tool_calls = _filter_repeated_tool_calls(decider.tool_calls, blocked_signatures)
        decider.needs_tool = bool(decider.needs_tool and decider.tool_calls) if decider.tool_calls else bool(decider.needs_tool)
        if tools_enabled and decider.needs_tool and decider.tool_calls:
            phase_events = _append_phase_event(
                phase_events,
                "thought",
                _writer_phase_text(decider),
                loop_round=loop_round,
                thought_role="writer",
            )
            selected_skill_ids = []
            for intent in decider.tool_calls:
                skill_id = _skill_id_from_tool_name(intent.name)
                if skill_id and skill_id not in selected_skill_ids:
                    selected_skill_ids.append(skill_id)
            return {
                "decision": decider.to_dict(),
                "tool_plan": serialize_tool_calls(decider.tool_calls),
                "thought_summary": decider.thought_summary,
                "approval_request": {
                    "text": _approval_request_text(decider),
                    "tools": approval_tool_items(decider),
                    "speaker": "pet",
                },
                "needs_additional_approval": True,
                "last_expected_effect": str(decider.expected_effect or ""),
                "selection_origin": str(state.get("selection_origin") or "none"),
                "selected_skill_ids": selected_skill_ids or list(state.get("selected_skill_ids") or []),
                "selected_tool_names": list(state.get("selected_tool_names") or []),
                "execution_phase": str(state.get("execution_phase") or "agent_loop"),
                "last_inventory_result": dict(state.get("last_inventory_result") or {}),
                "phase_events": phase_events,
                "trace": trace,
            }
        if not tools_enabled or not decider.needs_tool:
            return {
                "decision": decider.to_dict(),
                "tool_plan": [],
                "thought_summary": decider.thought_summary,
                "approval_request": {},
                "needs_additional_approval": False,
                "last_expected_effect": "",
                "phase_events": phase_events,
                "trace": trace,
            }

        searcher_args = {
            "task": user_text,
            "query": user_text,
            "reason": str(decider.action_message or decider.thought_summary or "").strip(),
        }
        searcher_result: dict[str, Any]
        fallback_plan_result: dict[str, Any] | None = None
        if deps.search_capabilities is not None:
            searcher_result = await deps.search_capabilities(state, searcher_args)
        else:
            fallback_plan_result = (
                await deps.plan_capabilities(state, searcher_args)
                if deps.plan_capabilities is not None
                else {"selection_kind": "none", "reason": "No internal planner is configured."}
            )
            if str(fallback_plan_result.get("mode") or "").strip().lower() == "inventory":
                searcher_result = dict(fallback_plan_result)
            elif str(fallback_plan_result.get("selection_kind") or "").strip().lower() == "skill":
                searcher_result = {
                    "mode": "skill",
                    "skill_id": str((_normalize_name_list(fallback_plan_result.get("skill_ids") or []) or [""])[0]),
                    "task_types": [],
                    "matched_tool_names": [],
                    "query": str(fallback_plan_result.get("query") or user_text),
                    "thought_summary": str(fallback_plan_result.get("thought_summary") or "I matched the request to a skill workflow."),
                    "reason": str(fallback_plan_result.get("reason") or ""),
                }
            else:
                searcher_result = {
                    "mode": "task_types",
                    "skill_id": "",
                    "task_types": ["general_mcp"],
                    "matched_tool_names": _normalize_name_list(fallback_plan_result.get("tool_names") or []),
                    "query": str(fallback_plan_result.get("query") or user_text),
                    "thought_summary": str(fallback_plan_result.get("thought_summary") or "I narrowed the task to MCP tools."),
                    "reason": str(fallback_plan_result.get("reason") or ""),
                }
        trace = _append_trace(
            {**state, "trace": trace},
            "searcher_result",
            mode=str(searcher_result.get("mode") or "").strip(),
            skill_id=str(searcher_result.get("skill_id") or "").strip(),
            task_types=_normalize_name_list(searcher_result.get("task_types") or []),
            matched_tool_names=_normalize_name_list(searcher_result.get("matched_tool_names") or []),
        )
        phase_events = _append_phase_event(
            phase_events,
            "thought",
            _searcher_phase_text(searcher_result),
            loop_round=loop_round,
            thought_role="searcher",
        )
        if str(searcher_result.get("mode") or "").strip().lower() == "inventory":
            inventory_result = dict(searcher_result)
            trace = _append_trace(
                {**state, "trace": trace},
                "planner_result",
                mode="inventory",
                selection_kind="none",
                normalized_skill_ids=_normalize_name_list(inventory_result.get("inventory_skill_ids") or []),
                normalized_tool_names=_normalize_name_list(inventory_result.get("inventory_tool_names") or []),
            )
            phase_events = _append_phase_event(
                phase_events,
                "thought",
                str(inventory_result.get("inventory_summary") or inventory_result.get("thought_summary") or "I listed the currently available capabilities.").strip(),
                loop_round=loop_round,
                thought_role="planner",
            )
            return {
                "decision": decider.to_dict(),
                "tool_plan": [],
                "thought_summary": str(inventory_result.get("inventory_summary") or decider.thought_summary or ""),
                "approval_request": {},
                "needs_additional_approval": False,
                "last_expected_effect": "",
                "selection_origin": "none",
                "selected_skill_ids": [],
                "selected_tool_names": [],
                "execution_phase": "agent_loop",
                "current_searcher_result": inventory_result,
                "current_execution_plan": {},
                "writer_loop_active": False,
                "last_inventory_result": inventory_result,
                "final_messages": list(final_message_base)
                + [
                    {
                        "role": "system",
                        "content": build_inventory_result_message(
                            inventory_result,
                            user_request=user_text,
                        ),
                    }
                ],
                "phase_events": phase_events,
                "trace": trace,
            }

        execution_plan = (
            await deps.build_execution_plan(state, searcher_result)
            if deps.build_execution_plan is not None
            else _single_step_plan_from_capability_plan(fallback_plan_result or {})
        )
        plan_steps = execution_plan.get("steps") if isinstance(execution_plan.get("steps"), list) else []
        trace = _append_trace(
            {**state, "trace": trace},
            "planner_plan_created",
            plan_id=str(execution_plan.get("plan_id") or "").strip(),
            plan_summary=str(execution_plan.get("plan_summary") or "").strip(),
            step_count=len(plan_steps),
        )
        planner_debug = dict((fallback_plan_result or {}).get("debug") or {})
        trace = _append_trace(
            {**state, "trace": trace},
            "planner_result",
            mode="execution_plan",
            selection_kind=str((fallback_plan_result or {}).get("selection_kind") or "plan").strip() or "plan",
            plan_id=str(execution_plan.get("plan_id") or "").strip(),
            step_count=len(plan_steps),
            **(
                {
                    "debug.parse_status": str(planner_debug.get("parse_status") or "").strip(),
                    "debug.visible_skill_count": int(planner_debug.get("visible_skill_count") or 0),
                    "debug.visible_tool_count": int(planner_debug.get("visible_tool_count") or 0),
                    "debug.raw_content_excerpt": str(planner_debug.get("raw_content_excerpt") or "").strip(),
                }
                if planner_debug
                else {}
            ),
        )
        phase_events = _append_phase_event(
            phase_events,
            "thought",
            _planner_phase_text(fallback_plan_result)
            if fallback_plan_result is not None and not plan_steps
            else _planner_plan_phase_text(execution_plan),
            loop_round=loop_round,
            thought_role="planner",
        )
        if not plan_steps:
            return {
                "decision": decider.to_dict(),
                "tool_plan": [],
                "thought_summary": str(execution_plan.get("plan_summary") or decider.thought_summary or ""),
                "approval_request": {},
                "needs_additional_approval": False,
                "last_expected_effect": "",
                "selection_origin": "none",
                "selected_skill_ids": [],
                "selected_tool_names": [],
                "execution_phase": "agent_loop",
                "current_searcher_result": dict(searcher_result),
                "current_execution_plan": dict(execution_plan),
                "writer_loop_active": False,
                "last_inventory_result": {},
                "final_messages": list(final_message_base)
                + [
                    {
                        "role": "system",
                        "content": _rt_writer_bundle_blocked_prompt(),
                    }
                ],
                "phase_events": phase_events,
                "trace": trace,
            }

        selected_skill_ids = []
        if str(searcher_result.get("mode") or "").strip().lower() == "skill":
            skill_id = str(searcher_result.get("skill_id") or "").strip()
            if skill_id:
                selected_skill_ids = [skill_id]
        writer_base_state: AgentRunState = {
            **state,
            "selection_origin": "planner",
            "selected_skill_ids": selected_skill_ids,
            "selected_tool_names": [],
            "execution_phase": "skill_execution" if selected_skill_ids else "agent_loop",
            "current_searcher_result": dict(searcher_result),
            "current_execution_plan": dict(execution_plan),
            "current_step_index": 0,
            "current_step_attempt": 1,
            "current_candidate_tool_set_index": 0,
            "current_candidate_no_progress": 0,
            "completed_steps": [],
            "blocked_steps": [],
            "step_history": [],
            "writer_loop_active": True,
        }
        plan, _, step = _current_plan_step(writer_base_state)
        candidate_tool_set = _current_candidate_tool_set(writer_base_state, step)
        writer_state = {
            **writer_base_state,
            "selected_tool_names": candidate_tool_set if not selected_skill_ids else [],
        }
        bridge = deps.build_tool_bridge(writer_state) if deps.build_tool_bridge else None
        if bridge is None:
            bridge = deps.get_mcp_bridge()
        writer_tools = bridge.list_tools() if bridge is not None else []
        if not candidate_tool_set:
            candidate_tool_set = _normalize_name_list(
                [
                    str(item.get("function", {}).get("name") or "")
                    for item in writer_tools
                    if isinstance(item, dict)
                ]
            )
            if not selected_skill_ids:
                writer_state["selected_tool_names"] = list(candidate_tool_set)
        blocked_signatures = {
            str(item)
            for item in (state.get("executed_call_signatures") or [])
            if str(item or "").strip()
        }
        blocked_signatures.update(
            str(item)
            for item in (state.get("rejected_call_signatures") or [])
            if str(item or "").strip()
        )
        writer_system_messages = [
            {
                "role": "system",
                "content": _writer_step_prompt(writer_state, plan, step, candidate_tool_set),
            },
            {
                "role": "system",
                "content": _writer_step_template_prompt(writer_tools, step, candidate_tool_set),
            },
        ]
        if deps.resolve_skill_prompt_text is not None and selected_skill_ids:
            resolved_prompt = str(deps.resolve_skill_prompt_text(selected_skill_ids, writer_state) or "").strip()
            if resolved_prompt:
                writer_system_messages.append(
                    {
                        "role": "system",
                        "content": _rt_planner_selected_skill_round_prompt(resolved_prompt),
                    }
                )
        writer_messages = writer_system_messages + list(messages)
        writer_decision = await deps.decide_turn(
            messages=writer_messages,
            model=str(state.get("model") or ""),
            tools=writer_tools,
            llm_provider=str(state.get("llm_provider") or "ollama"),
            api_base_url=str(state.get("api_base_url") or ""),
            api_key=str(state.get("api_key") or ""),
        )
        writer_decision.tool_calls = _filter_repeated_tool_calls(writer_decision.tool_calls, blocked_signatures)
        allowed_step_tool_names = set(candidate_tool_set)
        if allowed_step_tool_names:
            writer_decision.tool_calls = [
                item for item in writer_decision.tool_calls if str(item.name or "").strip() in allowed_step_tool_names
            ]
        call_mode = str(step.get("call_mode") or "single").strip().lower() or "single"
        if call_mode == "batch":
            writer_decision.needs_tool = bool(writer_decision.needs_tool and len(writer_decision.tool_calls) >= 2)
        else:
            writer_decision.tool_calls = list(writer_decision.tool_calls[:1])
            writer_decision.needs_tool = bool(writer_decision.needs_tool and len(writer_decision.tool_calls) == 1)
        if not writer_decision.needs_tool and writer_tools:
            retry_messages = [
                {"role": "system", "content": _writer_step_prompt(writer_state, plan, step, candidate_tool_set, strict=True)},
                *writer_system_messages[1:],
                *list(messages),
            ]
            retry_decision = await deps.decide_turn(
                messages=retry_messages,
                model=str(state.get("model") or ""),
                tools=writer_tools,
                llm_provider=str(state.get("llm_provider") or "ollama"),
                api_base_url=str(state.get("api_base_url") or ""),
                api_key=str(state.get("api_key") or ""),
            )
            retry_decision.tool_calls = _filter_repeated_tool_calls(retry_decision.tool_calls, blocked_signatures)
            if allowed_step_tool_names:
                retry_decision.tool_calls = [
                    item for item in retry_decision.tool_calls if str(item.name or "").strip() in allowed_step_tool_names
                ]
            if call_mode == "batch":
                retry_decision.needs_tool = bool(retry_decision.needs_tool and len(retry_decision.tool_calls) >= 2)
            else:
                retry_decision.tool_calls = list(retry_decision.tool_calls[:1])
                retry_decision.needs_tool = bool(retry_decision.needs_tool and len(retry_decision.tool_calls) == 1)
            if retry_decision.needs_tool:
                writer_decision = retry_decision
        if not writer_decision.needs_tool or not writer_decision.tool_calls:
            phase_events = _append_step_phase_event(
                phase_events,
                "thought",
                _writer_blocked_phase_text(writer_tools, {"tool_names": candidate_tool_set}),
                state=writer_state,
                step=step,
                loop_round=loop_round,
                thought_role="writer",
                step_status="blocked",
            )
            return {
                "decision": writer_decision.to_dict(),
                "tool_plan": [],
                "thought_summary": str(execution_plan.get("plan_summary") or decider.thought_summary or ""),
                "approval_request": {},
                "needs_additional_approval": False,
                "last_expected_effect": "",
                "selection_origin": "planner",
                "selected_skill_ids": selected_skill_ids,
                "selected_tool_names": list(candidate_tool_set),
                "execution_phase": str(writer_state.get("execution_phase") or state.get("execution_phase") or "agent_loop"),
                "current_searcher_result": dict(searcher_result),
                "current_execution_plan": dict(execution_plan),
                "current_step_index": 0,
                "current_step_attempt": 1,
                "current_candidate_tool_set_index": 0,
                "current_candidate_no_progress": 0,
                "completed_steps": [],
                "blocked_steps": [str(step.get("step_id") or "").strip()] if str(step.get("step_id") or "").strip() else [],
                "step_history": [
                    {
                        "event": "writer_failed_to_emit",
                        "step_id": str(step.get("step_id") or "").strip(),
                        "tool_names": list(candidate_tool_set),
                    }
                ],
                "writer_loop_active": False,
                "last_inventory_result": {},
                "final_messages": list(final_message_base)
                + [
                    {
                        "role": "system",
                        "content": _rt_writer_bundle_blocked_prompt(),
                    }
                ],
                "phase_events": phase_events,
                "trace": _append_trace_event_with_step(
                    {**state, "trace": trace, "current_execution_plan": dict(execution_plan), "current_step_index": 0},
                    "writer_failed_to_emit",
                    step=step,
                    tool_names=list(candidate_tool_set),
                    status="blocked",
                ),
            }
        batch_id = _next_batch_id(writer_state)
        phase_events = _append_step_phase_event(
            phase_events,
            "thought",
            _writer_phase_text(writer_decision),
            state=writer_state,
            step=step,
            loop_round=loop_round,
            thought_role="writer",
            step_status="proposed",
            batch_id=batch_id,
            batch_size=len(writer_decision.tool_calls),
        )
        return {
            "decision": writer_decision.to_dict(),
            "tool_plan": serialize_tool_calls(writer_decision.tool_calls),
            "thought_summary": writer_decision.thought_summary,
            "approval_request": {
                "text": _approval_request_text_for_step(writer_state, step, writer_decision),
                "tools": approval_tool_items(writer_decision),
                "speaker": "pet",
                **_step_phase_extra(
                    writer_state,
                    step,
                    step_status="approval_required",
                    batch_id=batch_id,
                    batch_size=len(writer_decision.tool_calls),
                ),
            },
            "needs_additional_approval": True,
            "last_expected_effect": str(writer_decision.expected_effect or ""),
            "selection_origin": "planner",
            "selected_skill_ids": selected_skill_ids,
            "selected_tool_names": list(candidate_tool_set),
            "execution_phase": str(writer_state.get("execution_phase") or state.get("execution_phase") or "agent_loop"),
            "current_searcher_result": dict(searcher_result),
            "current_execution_plan": dict(execution_plan),
            "current_step_index": 0,
            "current_step_attempt": 1,
            "current_candidate_tool_set_index": 0,
            "current_candidate_no_progress": 0,
            "current_batch_id": batch_id,
            "completed_steps": [],
            "blocked_steps": [],
            "step_history": [],
            "writer_loop_active": True,
            "last_inventory_result": {},
            "phase_events": phase_events,
            "trace": _append_trace_event_with_step(
                {
                    **state,
                    "trace": trace,
                    "current_execution_plan": dict(execution_plan),
                    "current_step_index": 0,
                    "current_step_attempt": 1,
                    "current_candidate_tool_set_index": 0,
                },
                "writer_batch_proposed",
                step=step,
                tool_names=[item.name for item in writer_decision.tool_calls],
                status="proposed",
                batch_id=batch_id,
            ),
        }

    async def _continue_writer_loop(
        self,
        state: AgentRunState,
        *,
        messages: list[dict[str, Any]],
        loop_round: int,
        phase_events: list[dict[str, Any]],
        final_message_base: list[dict[str, Any]],
    ) -> AgentRunState:
        deps = self._deps()
        plan, _, step = _current_plan_step(state)
        if not step:
            return {
                "tool_plan": [],
                "approval_request": {},
                "needs_additional_approval": False,
                "writer_loop_active": False,
                "final_messages": list(final_message_base) + [{"role": "system", "content": _rt_writer_bundle_blocked_prompt()}],
                "trace": _append_trace(state, "plan_blocked", reason="missing_step"),
            }
        searcher_result = dict(state.get("current_searcher_result") or {})
        selected_skill_ids = _normalize_name_list(state.get("selected_skill_ids") or [])
        if not selected_skill_ids and str(searcher_result.get("mode") or "").strip().lower() == "skill":
            skill_id = str(searcher_result.get("skill_id") or "").strip()
            if skill_id:
                selected_skill_ids = [skill_id]
        candidate_tool_set = _current_candidate_tool_set(state, step)
        writer_state: AgentRunState = {
            **state,
            "selection_origin": "planner",
            "selected_skill_ids": selected_skill_ids,
            "selected_tool_names": [] if selected_skill_ids else list(candidate_tool_set),
            "execution_phase": "skill_execution" if selected_skill_ids else "agent_loop",
        }
        bridge = deps.build_tool_bridge(writer_state) if deps.build_tool_bridge else None
        if bridge is None:
            bridge = deps.get_mcp_bridge()
        writer_tools = bridge.list_tools() if bridge is not None else []
        if not candidate_tool_set:
            candidate_tool_set = _normalize_name_list(
                [
                    str(item.get("function", {}).get("name") or "")
                    for item in writer_tools
                    if isinstance(item, dict)
                ]
            )
            if not selected_skill_ids:
                writer_state["selected_tool_names"] = list(candidate_tool_set)
        blocked_signatures = {
            str(item)
            for item in (state.get("executed_call_signatures") or [])
            if str(item or "").strip()
        }
        blocked_signatures.update(
            str(item)
            for item in (state.get("rejected_call_signatures") or [])
            if str(item or "").strip()
        )
        writer_system_messages = [
            {"role": "system", "content": _writer_step_prompt(writer_state, plan, step, candidate_tool_set)},
            {"role": "system", "content": _writer_step_template_prompt(writer_tools, step, candidate_tool_set)},
        ]
        if deps.resolve_skill_prompt_text is not None and selected_skill_ids:
            resolved_prompt = str(deps.resolve_skill_prompt_text(selected_skill_ids, writer_state) or "").strip()
            if resolved_prompt:
                writer_system_messages.append({"role": "system", "content": _rt_planner_selected_skill_round_prompt(resolved_prompt)})
        writer_messages = writer_system_messages + list(messages)
        writer_decision = await deps.decide_turn(
            messages=writer_messages,
            model=str(state.get("model") or ""),
            tools=writer_tools,
            llm_provider=str(state.get("llm_provider") or "ollama"),
            api_base_url=str(state.get("api_base_url") or ""),
            api_key=str(state.get("api_key") or ""),
        )
        writer_decision.tool_calls = _filter_repeated_tool_calls(writer_decision.tool_calls, blocked_signatures)
        allowed_step_tool_names = set(candidate_tool_set)
        if allowed_step_tool_names:
            writer_decision.tool_calls = [
                item for item in writer_decision.tool_calls if str(item.name or "").strip() in allowed_step_tool_names
            ]
        call_mode = str(step.get("call_mode") or "single").strip().lower() or "single"
        if call_mode == "batch":
            writer_decision.needs_tool = bool(writer_decision.needs_tool and len(writer_decision.tool_calls) >= 2)
        else:
            writer_decision.tool_calls = list(writer_decision.tool_calls[:1])
            writer_decision.needs_tool = bool(writer_decision.needs_tool and len(writer_decision.tool_calls) == 1)
        if not writer_decision.needs_tool and writer_tools:
            retry_messages = [
                {"role": "system", "content": _writer_step_prompt(writer_state, plan, step, candidate_tool_set, strict=True)},
                *writer_system_messages[1:],
                *list(messages),
            ]
            retry_decision = await deps.decide_turn(
                messages=retry_messages,
                model=str(state.get("model") or ""),
                tools=writer_tools,
                llm_provider=str(state.get("llm_provider") or "ollama"),
                api_base_url=str(state.get("api_base_url") or ""),
                api_key=str(state.get("api_key") or ""),
            )
            retry_decision.tool_calls = _filter_repeated_tool_calls(retry_decision.tool_calls, blocked_signatures)
            if allowed_step_tool_names:
                retry_decision.tool_calls = [
                    item for item in retry_decision.tool_calls if str(item.name or "").strip() in allowed_step_tool_names
                ]
            if call_mode == "batch":
                retry_decision.needs_tool = bool(retry_decision.needs_tool and len(retry_decision.tool_calls) >= 2)
            else:
                retry_decision.tool_calls = list(retry_decision.tool_calls[:1])
                retry_decision.needs_tool = bool(retry_decision.needs_tool and len(retry_decision.tool_calls) == 1)
            if retry_decision.needs_tool:
                writer_decision = retry_decision
        if not writer_decision.needs_tool or not writer_decision.tool_calls:
            blocked_history = list(state.get("step_history") or []) + [
                {
                    "event": "writer_failed_to_emit",
                    "step_id": str(step.get("step_id") or "").strip(),
                    "tool_names": list(candidate_tool_set),
                    "attempt": max(1, int(state.get("current_step_attempt") or 1)),
                }
            ]
            phase_events = _append_step_phase_event(
                phase_events,
                "thought",
                _writer_blocked_phase_text(writer_tools, {"tool_names": candidate_tool_set}),
                state=writer_state,
                step=step,
                loop_round=loop_round,
                thought_role="writer",
                step_status="blocked",
            )
            return {
                "tool_plan": [],
                "approval_request": {},
                "needs_additional_approval": False,
                "writer_loop_active": False,
                "blocked_steps": _normalize_name_list(list(state.get("blocked_steps") or []) + [str(step.get("step_id") or "").strip()]),
                "step_history": blocked_history,
                "phase_events": phase_events,
                "final_messages": list(final_message_base) + [{"role": "system", "content": _rt_writer_bundle_blocked_prompt()}],
                "trace": _append_trace_event_with_step(state, "writer_failed_to_emit", step=step, tool_names=list(candidate_tool_set), status="blocked"),
            }
        batch_id = _next_batch_id(writer_state)
        phase_events = _append_step_phase_event(
            phase_events,
            "thought",
            _writer_phase_text(writer_decision),
            state=writer_state,
            step=step,
            loop_round=loop_round,
            thought_role="writer",
            step_status="proposed",
            batch_id=batch_id,
            batch_size=len(writer_decision.tool_calls),
        )
        return {
            "decision": writer_decision.to_dict(),
            "tool_plan": serialize_tool_calls(writer_decision.tool_calls),
            "thought_summary": writer_decision.thought_summary,
            "approval_request": {
                "text": _approval_request_text_for_step(writer_state, step, writer_decision),
                "tools": approval_tool_items(writer_decision),
                "speaker": "pet",
                **_step_phase_extra(
                    writer_state,
                    step,
                    step_status="approval_required",
                    batch_id=batch_id,
                    batch_size=len(writer_decision.tool_calls),
                ),
            },
            "needs_additional_approval": True,
            "selection_origin": "planner",
            "selected_skill_ids": selected_skill_ids,
            "selected_tool_names": list(candidate_tool_set),
            "execution_phase": str(writer_state.get("execution_phase") or "agent_loop"),
            "current_searcher_result": dict(searcher_result),
            "current_execution_plan": dict(plan),
            "current_step_index": max(0, int(state.get("current_step_index") or 0)),
            "current_step_attempt": max(1, int(state.get("current_step_attempt") or 1)),
            "current_candidate_tool_set_index": max(0, int(state.get("current_candidate_tool_set_index") or 0)),
            "current_candidate_no_progress": max(0, int(state.get("current_candidate_no_progress") or 0)),
            "current_batch_id": batch_id,
            "completed_steps": _normalize_name_list(state.get("completed_steps") or []),
            "blocked_steps": _normalize_name_list(state.get("blocked_steps") or []),
            "step_history": list(state.get("step_history") or []),
            "last_expected_effect": str(writer_decision.expected_effect or ""),
            "writer_loop_active": True,
            "phase_events": phase_events,
            "trace": _append_trace_event_with_step(state, "writer_batch_proposed", step=step, tool_names=[item.name for item in writer_decision.tool_calls], status="proposed", batch_id=batch_id),
        }

    async def _decide_or_respond(self, state: AgentRunState) -> AgentRunState:
        route_kind = str(state.get("route_kind") or "").strip()
        route_thought_summary = str(state.get("route_thought_summary") or "").strip()
        loop_round = max(1, int(state.get("loop_round") or 1))
        phase_events = list(state.get("phase_events") or [])
        if route_kind == "direct_answer" and str(state.get("chat_mode") or "").strip().lower() != "react":
            return {
                "decision": {
                    "needs_tool": False,
                    "thought_summary": route_thought_summary,
                    "action_message": "",
                    "tool_calls": [],
                    "expected_effect": "",
                },
                "tool_plan": [],
                "thought_summary": route_thought_summary,
                "approval_request": {},
                "needs_additional_approval": False,
                "last_expected_effect": "",
                "phase_events": _append_phase_event(
                    phase_events,
                    "thought",
                    route_thought_summary or "I can answer directly without using tools.",
                    loop_round=loop_round,
                    thought_role="decider",
                ),
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
                    expected_effect=_default_expected_effect([intent]),
                )
                writer_events = _append_phase_event(
                    _append_phase_event(
                        phase_events,
                        "thought",
                        decision.thought_summary or "I should take one direct tool step first.",
                        loop_round=loop_round,
                        thought_role="decider",
                    ),
                    "thought",
                    _writer_phase_text(decision),
                    loop_round=loop_round,
                    thought_role="writer",
                )
                return {
                    "decision": decision.to_dict(),
                    "tool_plan": serialize_tool_calls(decision.tool_calls),
                    "thought_summary": route_thought_summary,
                    "approval_request": {
                        "text": _approval_request_text(decision),
                        "tools": approval_tool_items(decision),
                        "speaker": "pet",
                    },
                    "needs_additional_approval": True,
                    "last_expected_effect": decision.expected_effect,
                    "phase_events": writer_events,
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
                "last_expected_effect": "",
                "trace": _append_trace(state, "decide_or_respond", mode="react_disabled"),
            }

        decision_messages = list(state.get("decision_messages") or state.get("working_messages") or [])
        round_result = await self._run_internal_planner_round(
            state,
            messages=decision_messages,
            final_message_base=list(state.get("prompt_messages") or []),
            loop_round=loop_round,
            phase_events=phase_events,
        )
        decision_payload = dict(round_result.get("decision") or {})
        tool_plan = list(round_result.get("tool_plan") or [])
        trace_state: AgentRunState = {**state, "trace": list(round_result.get("trace") or state.get("trace") or [])}
        return {
            **round_result,
            "trace": _append_trace(
                trace_state,
                "decide_or_respond",
                needs_tool=bool(decision_payload.get("needs_tool")),
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
        updates = (
            _state_updates_from_rejected_followup(state, decision)
            if (not decision.approved and str(decision.user_text or "").strip())
            else {}
        )
        return {
            **updates,
            "approval_decision": decision.to_dict(),
            "phase_events": [],
            "trace": _append_trace(
                state,
                "approval_resume",
                approved=decision.approved,
                has_user_text=bool(str(decision.user_text or "").strip()),
            ),
        }

    def _route_after_approval(self, state: AgentRunState) -> str:
        approval = state.get("approval_decision") or {}
        if bool(approval.get("approved")):
            return "execute_tools"
        if str(approval.get("user_text") or "").strip():
            return "decide_or_respond"
        return "final_reply"

    async def _execute_tools(self, state: AgentRunState) -> AgentRunState:
        deps = self._deps()
        loop_round = max(1, int(state.get("loop_round") or 1))
        phase_events = list(state.get("phase_events") or [])
        tooling_cfg = state.get("tooling_config") if isinstance(state.get("tooling_config"), dict) else deps.load_tooling_config()
        bridge = deps.build_tool_bridge(state) if deps.build_tool_bridge else None
        if bridge is None:
            bridge = deps.get_mcp_bridge()
        tool_calls = deserialize_tool_calls(list(state.get("tool_plan") or []))
        max_tool_calls = int(tooling_cfg.get("max_tool_calls_per_turn", 6))
        executions: list[ToolExecution] = []
        for index, intent in enumerate(tool_calls):
            if index >= max_tool_calls:
                executions.append(
                    ToolExecution(
                        name="tool_limit",
                        arguments={},
                        ok=False,
                        summary="tool call limit reached",
                        payload=json.dumps({"ok": False, "error": "tool call limit reached"}, ensure_ascii=False),
                    )
                )
                break
            executions.extend(
                deps.execute_tool_calls(
                    tool_calls=[intent],
                    mcp_bridge=bridge,
                    max_tool_calls=1,
                )
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
        state_updates = _state_updates_from_executions(state, tool_calls, executions)
        plan, _, step = _current_plan_step(state)
        batch_id = str(state.get("current_batch_id") or "").strip()
        remaining_steps = max(0, int(state.get("max_reasoning_steps") or 1) - int(state.get("reasoning_step") or 1))
        followup_messages = list(state.get("decision_messages") or state.get("working_messages") or []) + [
            {
                "role": "system",
                "content": build_approved_tool_message(
                    executions,
                    remaining_steps=remaining_steps,
                    user_request=str(state.get("user_text") or ""),
                    expected_effect=str(state.get("last_expected_effect") or ""),
                ),
            }
        ]
        if (
            deps.resolve_skill_prompt_text is not None
            and str(state_updates.get("selection_origin") or state.get("selection_origin") or "none").strip().lower() == "planner"
            and list(state_updates.get("selected_skill_ids") or [])
        ):
            resolved_prompt = str(
                deps.resolve_skill_prompt_text(
                    list(state_updates.get("selected_skill_ids") or []),
                    {
                        **state,
                        **state_updates,
                    },
                )
                or ""
            ).strip()
            if resolved_prompt:
                followup_messages.append(
                    {
                        "role": "system",
                        "content": _rt_planner_selected_skill_followup_prompt(resolved_prompt),
                    }
                )
        meta_guidance = _post_meta_tool_guidance_prompt(
            {
                **state,
                "execution_phase": str(state_updates.get("execution_phase") or state.get("execution_phase") or ""),
                "selection_origin": str(state_updates.get("selection_origin") or state.get("selection_origin") or "none"),
                "selected_skill_ids": list(state_updates.get("selected_skill_ids") or []),
                "selected_tool_names": list(state_updates.get("selected_tool_names") or []),
                "planner_excluded_skill_ids": list(state_updates.get("planner_excluded_skill_ids") or []),
                "planner_excluded_tool_names": list(state_updates.get("planner_excluded_tool_names") or []),
                "planner_retry_used": bool(state_updates.get("planner_retry_used", state.get("planner_retry_used", False))),
            },
            all_executions,
            remaining_steps=remaining_steps,
        )
        if meta_guidance:
            followup_messages.append({"role": "system", "content": meta_guidance})
        reflection_prompt = _tool_reflection_prompt(all_executions)
        if reflection_prompt:
            followup_messages.append({"role": "system", "content": reflection_prompt})
        trace = _append_trace(
            state,
            "approval_batch_resumed",
            plan_id=str(plan.get("plan_id") or "").strip(),
            step_id=str(step.get("step_id") or "").strip(),
            batch_id=batch_id,
            approved=True,
        )
        trace = _append_trace_event_with_step(
            {
                **state,
                "trace": trace,
            },
            "writer_batch_executed",
            step=step,
            tool_names=[item.name for item in tool_calls],
            status="executed",
            batch_id=batch_id,
            execution_count=len(executions),
        )
        return {
            "tool_results": all_tool_results,
            "latest_batch_results": current_results,
            "followup_messages": followup_messages,
            "executed_call_signatures": sorted(executed_signatures),
            "approval_request": {},
            "needs_additional_approval": False,
            "execution_phase": str(state_updates.get("execution_phase") or state.get("execution_phase") or ""),
            "selection_origin": str(state_updates.get("selection_origin") or state.get("selection_origin") or "none"),
            "selected_skill_ids": list(state_updates.get("selected_skill_ids") or []),
            "selected_tool_names": list(state_updates.get("selected_tool_names") or []),
            "planner_excluded_skill_ids": list(state_updates.get("planner_excluded_skill_ids") or []),
            "planner_excluded_tool_names": list(state_updates.get("planner_excluded_tool_names") or []),
            "planner_retry_used": bool(state_updates.get("planner_retry_used", state.get("planner_retry_used", False))),
            "last_inventory_result": dict(state_updates.get("last_inventory_result") or state.get("last_inventory_result") or {}),
            "no_progress_streak": int(state_updates.get("no_progress_streak") or state.get("no_progress_streak") or 0),
            "phase_events": phase_events,
            "trace": _append_trace(
                {
                    **state,
                    "trace": trace,
                },
                "execute_tools",
                tool_count=len(tool_calls),
                execution_count=len(executions),
                execution_phase=str(state_updates.get("execution_phase") or state.get("execution_phase") or ""),
            ),
        }

    async def _continuation_check(self, state: AgentRunState) -> AgentRunState:
        max_reasoning_steps = max(1, int(state.get("max_reasoning_steps") or 1))
        reasoning_step = max(1, int(state.get("reasoning_step") or 1))
        loop_round = max(1, int(state.get("loop_round") or 1))
        followup_messages = list(state.get("followup_messages") or [])
        cumulative_executions = _deserialize_tool_executions(list(state.get("tool_results") or []))
        latest_inventory_result = dict(state.get("last_inventory_result") or _latest_inventory_result(cumulative_executions) or {})
        phase_events = list(state.get("phase_events") or [])
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
                "last_assessment": "The routed single tool step has finished; I can now answer the user.",
                "trace": _append_trace(
                    state,
                    "continuation_check",
                    needs_tool=False,
                    tool_count=0,
                    reason="route_simple_tool",
                ),
            }
        if latest_inventory_result:
            inventory_messages = list(state.get("prompt_messages") or []) + [
                {
                    "role": "system",
                    "content": build_inventory_result_message(
                        latest_inventory_result,
                        user_request=str(state.get("user_text") or ""),
                    ),
                }
            ]
            return {
                "thought_summary": str(latest_inventory_result.get("inventory_summary") or state.get("thought_summary") or ""),
                "final_messages": inventory_messages,
                "tool_plan": [],
                "approval_request": {},
                "needs_additional_approval": False,
                "last_assessment": "The explicit capability inventory is complete, so I should report the enumerated result before doing anything else.",
                "trace": _append_trace(
                    state,
                    "continuation_check",
                    needs_tool=False,
                    tool_count=0,
                    reason="inventory",
                ),
            }

        if bool(state.get("writer_loop_active")) and dict(state.get("current_execution_plan") or {}):
            latest_batch = _deserialize_tool_executions(list(state.get("latest_batch_results") or []))
            plan, step_index, step = _current_plan_step(state)
            progress = _batch_execution_progress(latest_batch)
            step_id = str(step.get("step_id") or "").strip()
            step_title = str(step.get("title") or "").strip()
            step_history = list(state.get("step_history") or [])
            history_entry = {
                "event": "writer_batch_executed",
                "plan_id": str(plan.get("plan_id") or "").strip(),
                "step_id": step_id,
                "step_title": step_title,
                "attempt": max(1, int(state.get("current_step_attempt") or 1)),
                "candidate_tool_set_index": max(0, int(state.get("current_candidate_tool_set_index") or 0)),
                "batch_id": str(state.get("current_batch_id") or "").strip(),
                "tool_names": [item.name for item in latest_batch],
                "completed": bool(progress.get("completed")),
                "blocked": bool(progress.get("blocked")),
                "effective_progress": bool(progress.get("effective_progress")),
            }
            step_history.append(history_entry)
            if progress.get("completed"):
                completed_steps = _normalize_name_list(list(state.get("completed_steps") or []) + ([step_id] if step_id else []))
                phase_events = _append_step_phase_event(
                    phase_events,
                    "action",
                    f"Step {step_index + 1} completed: {step_title or step_id or 'current step'}.",
                    state=state,
                    step=step,
                    loop_round=loop_round,
                    step_status="completed",
                    batch_id=str(state.get("current_batch_id") or ""),
                    batch_size=len(latest_batch),
                )
                trace = _append_trace_event_with_step(state, "step_completed", step=step, tool_names=[item.name for item in latest_batch], status="completed")
                total_steps = len(plan.get("steps") if isinstance(plan.get("steps"), list) else [])
                if step_index + 1 >= total_steps:
                    return {
                        "final_messages": final_messages,
                        "tool_plan": [],
                        "approval_request": {},
                        "needs_additional_approval": False,
                        "writer_loop_active": False,
                        "completed_steps": completed_steps,
                        "step_history": step_history,
                        "last_assessment": "The current execution plan completed all planned steps.",
                        "phase_events": phase_events,
                        "trace": _append_trace(
                            {**state, "trace": trace},
                            "plan_completed",
                            plan_id=str(plan.get("plan_id") or "").strip(),
                            step_count=total_steps,
                        ),
                    }
                next_state: AgentRunState = {
                    **state,
                    "trace": trace,
                    "phase_events": phase_events,
                    "completed_steps": completed_steps,
                    "step_history": step_history,
                    "current_step_index": step_index + 1,
                    "current_step_attempt": 1,
                    "current_candidate_tool_set_index": 0,
                    "current_candidate_no_progress": 0,
                    "latest_batch_results": [],
                    "approval_request": {},
                    "needs_additional_approval": False,
                }
                next_result = await self._continue_writer_loop(
                    next_state,
                    messages=followup_messages,
                    loop_round=loop_round,
                    phase_events=phase_events,
                    final_message_base=final_messages,
                )
                if bool(next_result.get("needs_additional_approval")):
                    next_result["reasoning_step"] = reasoning_step + 1
                return next_result

            candidate_index = max(0, int(state.get("current_candidate_tool_set_index") or 0))
            current_candidate_no_progress = max(0, int(state.get("current_candidate_no_progress") or 0))
            step_attempt = max(1, int(state.get("current_step_attempt") or 1))
            if not progress.get("effective_progress"):
                current_candidate_no_progress += 1
            else:
                current_candidate_no_progress = 0
            step_attempt += 1
            candidate_sets = step.get("candidate_tool_sets") if isinstance(step.get("candidate_tool_sets"), list) else []
            should_switch_candidate = current_candidate_no_progress >= 2 and candidate_index + 1 < len(candidate_sets)
            if should_switch_candidate:
                candidate_index += 1
                current_candidate_no_progress = 0
            blocked_step = bool(progress.get("blocked")) or step_attempt > 3 or (
                current_candidate_no_progress >= 2 and candidate_index + 1 >= len(candidate_sets)
            )
            trace = _append_trace_event_with_step(
                state,
                "step_blocked" if blocked_step else "writer_batch_executed",
                step=step,
                tool_names=[item.name for item in latest_batch],
                status="blocked" if blocked_step else "in_progress",
            )
            if blocked_step:
                blocked_steps = _normalize_name_list(list(state.get("blocked_steps") or []) + ([step_id] if step_id else []))
                phase_events = _append_step_phase_event(
                    phase_events,
                    "action",
                    f"Step {step_index + 1} is blocked: {step_title or step_id or 'current step'}.",
                    state=state,
                    step=step,
                    loop_round=loop_round,
                    step_status="blocked",
                    batch_id=str(state.get("current_batch_id") or ""),
                    batch_size=len(latest_batch),
                )
                return {
                    "final_messages": list(final_messages) + [{"role": "system", "content": _rt_writer_bundle_blocked_prompt()}],
                    "tool_plan": [],
                    "approval_request": {},
                    "needs_additional_approval": False,
                    "writer_loop_active": False,
                    "blocked_steps": blocked_steps,
                    "step_history": step_history,
                    "last_assessment": "The current execution plan became blocked before all planned steps could complete.",
                    "phase_events": phase_events,
                    "trace": _append_trace(
                        {**state, "trace": trace},
                        "plan_blocked",
                        plan_id=str(plan.get("plan_id") or "").strip(),
                        blocked_step_id=step_id,
                    ),
                }

            phase_events = _append_step_phase_event(
                phase_events,
                "action",
                (
                    f"Step {step_index + 1} still needs work, switching to candidate set {candidate_index + 1}."
                    if should_switch_candidate
                    else f"Step {step_index + 1} still needs another batch."
                ),
                state=state,
                step=step,
                loop_round=loop_round,
                step_status="in_progress",
            )
            next_state = {
                **state,
                "trace": trace,
                "phase_events": phase_events,
                "step_history": step_history,
                "current_step_attempt": step_attempt,
                "current_candidate_tool_set_index": candidate_index,
                "current_candidate_no_progress": current_candidate_no_progress,
                "latest_batch_results": [],
                "approval_request": {},
                "needs_additional_approval": False,
            }
            next_result = await self._continue_writer_loop(
                next_state,
                messages=followup_messages,
                loop_round=loop_round,
                phase_events=phase_events,
                final_message_base=final_messages,
            )
            if bool(next_result.get("needs_additional_approval")):
                next_result["reasoning_step"] = reasoning_step + 1
            return next_result

        no_progress_streak = max(0, int(state.get("no_progress_streak") or 0))
        loop_limit_hit = loop_round >= _max_loop_rounds(state)
        if no_progress_streak >= 2:
            final_messages = list(final_messages) + [
                {
                    "role": "system",
                    "content": _rt_no_progress_stop_prompt(),
                }
            ]
            return {
                "final_messages": final_messages,
                "tool_plan": [],
                "approval_request": {},
                "needs_additional_approval": False,
                "last_assessment": "Recent tool attempts did not make useful progress, so I should stop looping and explain the blockage clearly.",
                "trace": _append_trace(
                    state,
                    "continuation_check",
                    needs_tool=False,
                    tool_count=0,
                    reason="no_progress",
                ),
            }

        if (
            bool(state.get("react_enabled", True))
            and bool(state.get("tools_enabled", True))
            and not loop_limit_hit
            and (
                reasoning_step < max_reasoning_steps
                or (
                    not _has_successful_non_system_execution(cumulative_executions)
                    and _must_continue_after_meta_tools(state, cumulative_executions)
                )
            )
        ):
            meta_handoff_grace = bool(
                reasoning_step >= max_reasoning_steps
                and not _has_successful_non_system_execution(cumulative_executions)
                and _must_continue_after_meta_tools(state, cumulative_executions)
            )
            remaining_steps = max(0, max_reasoning_steps - reasoning_step)
            if meta_handoff_grace and remaining_steps <= 0:
                remaining_steps = 1
            leader_messages = list(followup_messages) + [
                {
                    "role": "system",
                    "content": _leader_assessment_prompt(
                        str(state.get("user_text") or ""),
                        str(state.get("last_expected_effect") or ""),
                        loop_round,
                    ),
                }
            ]
            next_round = loop_round + 1
            candidate_result = await self._run_internal_planner_round(
                state,
                messages=leader_messages,
                final_message_base=final_messages,
                loop_round=next_round,
                phase_events=[],
            )
            next_decision_payload = dict(candidate_result.get("decision") or {})
            if bool(candidate_result.get("needs_additional_approval")) and candidate_result.get("approval_request"):
                preserve_step_budget = bool(
                    meta_handoff_grace
                    and not _all_tool_calls_are_system(deserialize_tool_calls(candidate_result.get("tool_plan")))
                )
                leader_text = f"Round {loop_round} has not met the expected effect yet, so I should continue with round {next_round}."
                continued_phase_events = _append_phase_event(
                    phase_events,
                    "action",
                    leader_text,
                )
                continued_phase_events.extend(list(candidate_result.get("phase_events") or []))
                return {
                    **candidate_result,
                    "reasoning_step": reasoning_step if preserve_step_budget else reasoning_step + 1,
                    "loop_round": next_round,
                    "last_assessment": leader_text,
                    "final_messages": [],
                    "phase_events": continued_phase_events,
                    "trace": _append_trace(
                        state,
                        "continuation_check",
                        needs_tool=True,
                        tool_count=len(list(candidate_result.get("tool_plan") or [])),
                    ),
                }
            return {
                "thought_summary": str(candidate_result.get("thought_summary") or next_decision_payload.get("thought_summary") or ""),
                "final_messages": list(candidate_result.get("final_messages") or final_messages),
                "tool_plan": [],
                "approval_request": {},
                "needs_additional_approval": False,
                "last_assessment": str(
                    candidate_result.get("last_assessment")
                    or "The current tool results are enough for me to finish the reply without another round."
                ),
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
                    "content": _rt_tool_step_limit_prompt(),
                }
            ]
        elif loop_limit_hit:
            final_messages = list(final_messages) + [
                {
                    "role": "system",
                    "content": _rt_loop_round_limit_prompt(),
                }
            ]
        return {
            "final_messages": final_messages,
            "tool_plan": [],
            "approval_request": {},
            "needs_additional_approval": False,
            "last_assessment": (
                "The turn hit the approved tool-step limit, so I should finish with the current results."
                if reasoning_step >= max_reasoning_steps
                else "The turn hit the agent-loop round limit, so I should stop and explain the current status."
                if loop_limit_hit
                else "The current results are enough to finish the answer."
            ),
            "trace": _append_trace(
                state,
                "continuation_check",
                needs_tool=False,
                tool_count=0,
                reason="limit" if reasoning_step >= max_reasoning_steps else "round_limit" if loop_limit_hit else "done",
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
                if str(approval.get("user_text") or "").strip():
                    final_messages = list(state.get("prompt_messages") or [])
                else:
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
        if final_messages and state.get("tool_results") and not state.get("final_messages") and not state.get("followup_messages"):
            cumulative_executions = _deserialize_tool_executions(list(state.get("tool_results") or []))
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
        return {
            "final_messages": final_messages,
            "needs_additional_approval": False,
            "trace": _append_trace(
                state,
                "final_reply",
                message_count=len(final_messages),
            ),
        }

