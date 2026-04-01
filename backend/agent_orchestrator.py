from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from .mcp_bridge import MCPBridge
from .ollama_client import chat_once, stream_chat
from .runtime_prompts import (
    build_approved_tool_message as _rt_build_approved_tool_message,
    build_inventory_result_message as _rt_build_inventory_result_message,
    build_rejected_tool_followup_message as _rt_build_rejected_tool_followup_message,
    build_rejected_tool_message as _rt_build_rejected_tool_message,
    capability_plan_prompt as _rt_capability_plan_prompt,
    decider_prompt as _rt_decider_prompt,
    leader_plan_assessment_prompt as _rt_leader_plan_assessment_prompt,
    planner_execution_plan_prompt as _rt_planner_execution_plan_prompt,
    router_prompt_chat as _rt_router_prompt_chat,
    router_prompt_react as _rt_router_prompt_react,
    router_prompt_skill as _rt_router_prompt_skill,
    searcher_skill_match_prompt as _rt_searcher_skill_match_prompt,
    searcher_tool_type_prompt as _rt_searcher_tool_type_prompt,
    tool_catalog_prompt as _rt_tool_catalog_prompt,
)
from .tool_runtime import ToolResult


MAX_PREVIEW_CHARS = 1000
MAX_STEP_TEXT_CHARS = 180


@dataclass
class ToolIntent:
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments}


@dataclass
class TurnDecision:
    needs_tool: bool
    thought_summary: str
    action_message: str
    tool_calls: list[ToolIntent]
    expected_effect: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "needs_tool": self.needs_tool,
            "thought_summary": self.thought_summary,
            "action_message": self.action_message,
            "tool_calls": [item.to_dict() for item in self.tool_calls],
            "expected_effect": self.expected_effect,
        }


@dataclass
class RouteDecision:
    route_kind: str = "direct_answer"
    thought_summary: str = ""
    skill_ids: list[str] = field(default_factory=list)
    tool_candidates: list[str] = field(default_factory=list)
    tool_call: ToolIntent | None = None
    search_needed: bool = False
    search_query: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_kind": self.route_kind,
            "thought_summary": self.thought_summary,
            "skill_ids": list(self.skill_ids),
            "tool_candidates": list(self.tool_candidates),
            "tool_call": self.tool_call.to_dict() if self.tool_call is not None else None,
            "search_needed": self.search_needed,
            "search_query": self.search_query,
        }


@dataclass
class CapabilityPlan:
    mode: str = "plan"
    selection_kind: str = "none"
    skill_ids: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    query: str = ""
    thought_summary: str = ""
    reason: str = ""
    usage_notes: str = ""
    inventory_scope: str = ""
    inventory_skill_ids: list[str] = field(default_factory=list)
    inventory_tool_names: list[str] = field(default_factory=list)
    inventory_summary: str = ""
    inventory_skills: list[dict[str, Any]] = field(default_factory=list)
    inventory_tools: list[dict[str, Any]] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "selection_kind": self.selection_kind,
            "skill_ids": list(self.skill_ids),
            "tool_names": list(self.tool_names),
            "query": self.query,
            "thought_summary": self.thought_summary,
            "reason": self.reason,
            "usage_notes": self.usage_notes,
            "inventory_scope": self.inventory_scope,
            "inventory_skill_ids": list(self.inventory_skill_ids),
            "inventory_tool_names": list(self.inventory_tool_names),
            "inventory_summary": self.inventory_summary,
            "inventory_skills": [dict(item) for item in self.inventory_skills if isinstance(item, dict)],
            "inventory_tools": [dict(item) for item in self.inventory_tools if isinstance(item, dict)],
            "debug": dict(self.debug or {}),
        }


@dataclass
class SearcherResult:
    mode: str = "task_types"
    skill_id: str = ""
    task_types: list[str] = field(default_factory=list)
    matched_tool_names: list[str] = field(default_factory=list)
    query: str = ""
    thought_summary: str = ""
    reason: str = ""
    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "skill_id": self.skill_id,
            "task_types": list(self.task_types),
            "matched_tool_names": list(self.matched_tool_names),
            "query": self.query,
            "thought_summary": self.thought_summary,
            "reason": self.reason,
            "debug": dict(self.debug or {}),
        }


@dataclass
class PlanStep:
    step_id: str
    title: str
    goal: str
    success_criteria: str
    call_mode: str = "single"
    candidate_tool_sets: list[list[str]] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "goal": self.goal,
            "success_criteria": self.success_criteria,
            "call_mode": self.call_mode,
            "candidate_tool_sets": [list(item) for item in self.candidate_tool_sets],
            "notes": self.notes,
        }


@dataclass
class ExecutionPlan:
    plan_id: str
    plan_summary: str
    reason: str
    steps: list[PlanStep] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "plan_summary": self.plan_summary,
            "reason": self.reason,
            "steps": [item.to_dict() for item in self.steps],
            "debug": dict(self.debug or {}),
        }


@dataclass
class ToolExecution:
    name: str
    arguments: dict[str, Any]
    ok: bool
    summary: str
    payload: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "ok": self.ok,
            "summary": self.summary,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class ProviderAdapter:
    provider: str = "ollama"
    base_url: str = ""
    api_key: str = ""

    async def chat_once(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return await chat_once(
            messages=messages,
            model=model,
            tools=tools,
            provider=self.provider,
            base_url=self.base_url,
            api_key=self.api_key,
        )

    async def stream_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
    ) -> AsyncGenerator[str, None]:
        async for delta in stream_chat(
            messages=messages,
            model=model,
            provider=self.provider,
            base_url=self.base_url,
            api_key=self.api_key,
        ):
            yield delta


def _event(event_type: str, **payload: Any) -> dict[str, Any]:
    data = {"type": event_type}
    data.update(payload)
    return data


def _chunks(text: str, step: int = 48) -> list[str]:
    if not text:
        return []
    size = max(1, int(step))
    return [text[i : i + size] for i in range(0, len(text), size)]


def _compact_text(value: Any, limit: int = MAX_STEP_TEXT_CHARS) -> str:
    text = " ".join(str(value or "").replace("\r", "\n").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return json.dumps(str(value), ensure_ascii=False)


def _extract_json_candidates(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    candidates = [raw]
    if raw.startswith("```") and raw.endswith("```"):
        parts = raw.split("\n")
        if len(parts) >= 3:
            candidates.append("\n".join(parts[1:-1]).strip())
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(raw[start : end + 1].strip())
    return list(dict.fromkeys([item for item in candidates if item]))


def _extract_json_payload(content: str) -> dict[str, Any] | None:
    for candidate in _extract_json_candidates(content):
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return None


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _tool_catalog_prompt(tools: list[dict[str, Any]]) -> str:
    return _rt_tool_catalog_prompt(tools)


def _tool_names(tools: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        fn = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = str(fn.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _normalize_tool_intents(raw_items: Any, allowed_names: set[str]) -> list[ToolIntent]:
    intents: list[ToolIntent] = []
    if not isinstance(raw_items, list):
        return intents
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or (allowed_names and name not in allowed_names):
            continue
        intents.append(ToolIntent(name=name, arguments=_parse_args(item.get("arguments"))))
    return intents


def _normalize_native_tool_calls(raw_items: Any, allowed_names: set[str]) -> list[ToolIntent]:
    intents: list[ToolIntent] = []
    if not isinstance(raw_items, list):
        return intents
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        function_payload = item.get("function")
        if not isinstance(function_payload, dict):
            continue
        name = str(function_payload.get("name") or "").strip()
        if not name or (allowed_names and name not in allowed_names):
            continue
        intents.append(ToolIntent(name=name, arguments=_parse_args(function_payload.get("arguments"))))
    return intents


def _normalize_skill_ids(raw_items: Any, allowed_ids: set[str]) -> list[str]:
    values = raw_items if isinstance(raw_items, list) else []
    seen: set[str] = set()
    items: list[str] = []
    for item in values:
        skill_id = str(item or "").strip()
        if not skill_id or skill_id in seen:
            continue
        if allowed_ids and skill_id not in allowed_ids:
            continue
        seen.add(skill_id)
        items.append(skill_id)
    return items


def _coerce_string_list(raw_items: Any) -> list[str]:
    values = raw_items if isinstance(raw_items, list) else []
    seen: set[str] = set()
    items: list[str] = []
    for item in values:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        items.append(value)
    return items


def _resolve_allowed_tool_name(raw_name: Any, allowed_names: set[str]) -> str:
    value = str(raw_name or "").strip()
    if not value:
        return ""
    if not allowed_names:
        return value
    if value in allowed_names:
        return value

    lowered = value.lower()
    exact_lower_matches = [name for name in allowed_names if str(name or "").strip().lower() == lowered]
    if len(exact_lower_matches) == 1:
        return str(exact_lower_matches[0] or "").strip()

    leaf = lowered.split(".")[-1]
    leaf_matches = [name for name in allowed_names if str(name or "").strip().lower().split(".")[-1] == leaf]
    if len(leaf_matches) == 1:
        return str(leaf_matches[0] or "").strip()

    suffix_matches = [name for name in allowed_names if str(name or "").strip().lower().endswith(f".{lowered}")]
    if len(suffix_matches) == 1:
        return str(suffix_matches[0] or "").strip()
    return ""


def _normalize_tool_candidates(raw_items: Any, allowed_names: set[str]) -> list[str]:
    values = raw_items if isinstance(raw_items, list) else []
    seen: set[str] = set()
    items: list[str] = []
    for item in values:
        tool_name = _resolve_allowed_tool_name(item, allowed_names)
        if not tool_name or tool_name in seen:
            continue
        seen.add(tool_name)
        items.append(tool_name)
    return items


def _route_tool_call_payload(raw_item: Any, allowed_names: set[str]) -> ToolIntent | None:
    if not isinstance(raw_item, dict):
        return None
    name = str(raw_item.get("name") or "").strip()
    if not name or (allowed_names and name not in allowed_names):
        return None
    return ToolIntent(name=name, arguments=_parse_args(raw_item.get("arguments")))


def _skill_summary_lines(skill_summaries: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for item in skill_summaries:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("id") or "").strip()
        if not skill_id:
            continue
        name = str(item.get("name") or skill_id).strip()
        description = _compact_text(item.get("description") or "", 160)
        if description:
            lines.append(f"- {skill_id} ({name}): {description}")
        else:
            lines.append(f"- {skill_id} ({name})")
    return lines


def _router_prompt_react(tools: list[dict[str, Any]], skill_summaries: list[dict[str, str]]) -> str:
    return _rt_router_prompt_react(tools, skill_summaries)


def _decider_prompt() -> str:
    return _rt_decider_prompt()


def _decider_from_assistant_message(assistant_msg: dict[str, Any]) -> TurnDecision:
    payload = _extract_json_payload(str(assistant_msg.get("content") or "")) or {}
    needs_tool = bool(payload.get("needs_tool"))
    thought_summary = _compact_text(str(payload.get("thought_summary") or ""), MAX_STEP_TEXT_CHARS)
    if not thought_summary:
        thought_summary = _default_thought_summary(needs_tool)
    action_message = _compact_text(str(payload.get("action_message") or ""), MAX_PREVIEW_CHARS)
    return TurnDecision(
        needs_tool=needs_tool,
        thought_summary=thought_summary,
        action_message=action_message,
        tool_calls=[],
        expected_effect="",
    )


def _capability_prompt_excerpt(value: Any, limit: int = 220) -> str:
    return _compact_text(str(value or "").replace("\r", "\n"), limit)


def _capability_plan_prompt(
    skill_catalog: list[dict[str, Any]],
    tool_catalog: list[dict[str, Any]],
    *,
    excluded_skill_ids: list[str],
    excluded_tool_names: list[str],
) -> str:
    return _rt_capability_plan_prompt(
        skill_catalog,
        tool_catalog,
        excluded_skill_ids=excluded_skill_ids,
        excluded_tool_names=excluded_tool_names,
    )


SEARCHER_TASK_TYPES = (
    "browser_automation",
    "web_search",
    "file_io",
    "general_mcp",
)


def _searcher_skill_match_prompt(skill_catalog: list[dict[str, Any]]) -> str:
    return _rt_searcher_skill_match_prompt(skill_catalog)


def _searcher_tool_type_prompt(task_types: list[str], tool_catalog: list[dict[str, Any]]) -> str:
    return _rt_searcher_tool_type_prompt(task_types, tool_catalog)


def _planner_execution_plan_prompt(
    *,
    user_text: str,
    searcher_result: dict[str, Any],
    resolved_skill_prompt: str = "",
    tool_catalog: list[dict[str, Any]] | None = None,
) -> str:
    return _rt_planner_execution_plan_prompt(
        user_text=user_text,
        searcher_result=searcher_result,
        resolved_skill_prompt=resolved_skill_prompt,
        tool_catalog=tool_catalog or [],
    )


def _leader_plan_assessment_prompt(
    *,
    user_text: str,
    plan: dict[str, Any],
    plan_status: str,
    completed_steps: list[str],
    blocked_steps: list[str],
    loop_round: int,
) -> str:
    return _rt_leader_plan_assessment_prompt(
        user_text=user_text,
        plan=plan,
        plan_status=plan_status,
        completed_steps=completed_steps,
        blocked_steps=blocked_steps,
        loop_round=loop_round,
    )


def _router_prompt_chat(tools: list[dict[str, Any]]) -> str:
    return _rt_router_prompt_chat(tools)


def _router_prompt_skill(skill_summaries: list[dict[str, str]]) -> str:
    return _rt_router_prompt_skill(skill_summaries)


def _route_from_assistant_message(
    chat_mode: str,
    assistant_msg: dict[str, Any],
    allowed_skill_ids: set[str],
    allowed_tool_names: set[str],
) -> RouteDecision:
    payload = _extract_json_payload(str(assistant_msg.get("content") or "")) or {}
    thought_summary = _compact_text(str(payload.get("thought_summary") or ""), MAX_STEP_TEXT_CHARS)

    if chat_mode == "chat":
        search_needed = bool(payload.get("search_needed"))
        search_query = _compact_text(str(payload.get("search_query") or "").strip(), MAX_PREVIEW_CHARS)
        if search_needed and search_query:
            return RouteDecision(
                route_kind="simple_tool_task",
                thought_summary=thought_summary or "I should search the web first.",
                search_needed=True,
                search_query=search_query,
            )
        return RouteDecision(
            route_kind="direct_answer",
            thought_summary=thought_summary or "I can answer directly without web search.",
            search_needed=False,
            search_query="",
        )

    if chat_mode == "skill":
        skill_ids = _normalize_skill_ids(payload.get("skill_ids"), allowed_skill_ids)
        return RouteDecision(
            route_kind="skill_task" if skill_ids else "direct_answer",
            thought_summary=thought_summary or ("I found matching skills." if skill_ids else "No matching skill was selected."),
            skill_ids=skill_ids,
        )

    route_kind = str(payload.get("route_kind") or "direct_answer").strip().lower()
    if route_kind not in {"skill_task", "complex_task", "simple_tool_task", "direct_answer"}:
        route_kind = "direct_answer"
    skill_ids = _normalize_skill_ids(payload.get("skill_ids"), allowed_skill_ids)
    tool_candidates = _normalize_tool_candidates(payload.get("tool_candidates"), allowed_tool_names)
    tool_call = _route_tool_call_payload(payload.get("tool_call"), allowed_tool_names)
    if route_kind == "skill_task" and not skill_ids:
        route_kind = "complex_task" if chat_mode == "react" else "direct_answer"
    if route_kind == "simple_tool_task" and tool_call is None:
        route_kind = "direct_answer"
    return RouteDecision(
        route_kind=route_kind,
        thought_summary=thought_summary or _default_thought_summary(route_kind != "direct_answer"),
        skill_ids=skill_ids,
        tool_candidates=tool_candidates,
        tool_call=tool_call,
    )


def _capability_plan_from_assistant_message(
    assistant_msg: dict[str, Any],
    skill_catalog: list[dict[str, Any]],
    tool_catalog: list[dict[str, Any]],
) -> CapabilityPlan:
    raw_content = str(assistant_msg.get("content") or "")
    visible_skill_ids = [
        str(item.get("skill_id") or "").strip()
        for item in (skill_catalog or [])
        if isinstance(item, dict) and str(item.get("skill_id") or "").strip()
    ]
    visible_tool_names = [
        str(item.get("name") or "").strip()
        for item in (tool_catalog or [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    allowed_skill_ids = set(visible_skill_ids)
    allowed_tool_names = set(visible_tool_names)
    debug: dict[str, Any] = {
        "raw_content_excerpt": _compact_text(raw_content, MAX_PREVIEW_CHARS),
        "visible_skill_count": len(visible_skill_ids),
        "visible_tool_count": len(visible_tool_names),
        "visible_skill_ids": list(visible_skill_ids),
        "visible_tool_name_sample": list(visible_tool_names[:10]),
    }
    payload = _extract_json_payload(raw_content)
    if not isinstance(payload, dict):
        debug["parse_status"] = "json_invalid"
        return CapabilityPlan(
            selection_kind="none",
            thought_summary="I checked the visible capability lists, but the planner response was not valid JSON.",
            reason="The planner did not return a valid JSON capability selection.",
            usage_notes="",
            debug=debug,
        )

    raw_selection_kind = str(payload.get("selection_kind") or "none").strip().lower()
    debug["raw_selection_kind"] = raw_selection_kind
    raw_skill_ids = _coerce_string_list(payload.get("skill_ids"))
    raw_tool_names = _coerce_string_list(payload.get("tool_names"))
    debug["requested_skill_ids"] = list(raw_skill_ids)
    debug["requested_tool_names"] = list(raw_tool_names)
    selection_kind = raw_selection_kind
    if selection_kind not in {"skill", "mcp", "none"}:
        selection_kind = "none"
    skill_ids = _normalize_skill_ids(raw_skill_ids, allowed_skill_ids)
    tool_names = _normalize_tool_candidates(raw_tool_names, allowed_tool_names)
    debug["normalized_skill_ids"] = list(skill_ids)
    debug["normalized_tool_names"] = list(tool_names)
    debug["normalized_selection"] = {
        "selection_kind": selection_kind,
        "skill_ids": list(skill_ids),
        "tool_names": list(tool_names),
    }
    query = _compact_text(str(payload.get("query") or "").strip(), MAX_PREVIEW_CHARS)
    thought_summary = _compact_text(str(payload.get("thought_summary") or "").strip(), MAX_STEP_TEXT_CHARS)
    reason = _compact_text(str(payload.get("reason") or "").strip(), MAX_PREVIEW_CHARS)
    usage_notes = _compact_text(str(payload.get("usage_notes") or "").strip(), MAX_PREVIEW_CHARS)
    if selection_kind == "skill":
        if raw_tool_names:
            debug["parse_status"] = "mixed_selection"
            return CapabilityPlan(
                selection_kind="none",
                query=query,
                thought_summary=thought_summary or "I checked the visible skill bundles, but the planner mixed skill and MCP selections.",
                reason=reason or "The planner response mixed skill_ids and tool_names in the same response.",
                usage_notes="",
                debug=debug,
            )
        if raw_skill_ids and not skill_ids:
            debug["parse_status"] = "invalid_skill_ids"
            return CapabilityPlan(
                selection_kind="none",
                query=query,
                thought_summary=thought_summary or "I checked the visible skill bundles, but the planner only returned unavailable skill IDs.",
                reason=reason or "The planner response did not provide skill IDs from the visible default-enabled skill list.",
                usage_notes="",
                debug=debug,
            )
        if not skill_ids:
            debug["parse_status"] = "empty_selection"
            return CapabilityPlan(
                selection_kind="none",
                query=query,
                thought_summary=thought_summary or "I checked the visible skill bundles, but no usable skill IDs were returned.",
                reason=reason or "The planner response did not provide a valid skill-only selection.",
                usage_notes="",
                debug=debug,
            )
        debug["parse_status"] = "json_ok"
        return CapabilityPlan(
            selection_kind="skill",
            skill_ids=skill_ids,
            tool_names=[],
            query=query,
            thought_summary=thought_summary or "I found a skill workflow to try next.",
            reason=reason or "A matching skill workflow is the best next step.",
            usage_notes=usage_notes or "Use the selected skill workflow first, then let the writer turn it into the next concrete tool call.",
            debug=debug,
        )
    if selection_kind == "mcp":
        if raw_skill_ids:
            debug["parse_status"] = "mixed_selection"
            return CapabilityPlan(
                selection_kind="none",
                query=query,
                thought_summary=thought_summary or "I checked the visible MCP bundles, but the planner mixed skill and MCP selections.",
                reason=reason or "The planner response mixed skill_ids and tool_names in the same response.",
                usage_notes="",
                debug=debug,
            )
        if raw_tool_names and not tool_names:
            debug["parse_status"] = "invalid_tool_names"
            return CapabilityPlan(
                selection_kind="none",
                query=query,
                thought_summary=thought_summary or "I checked the visible MCP bundles, but the planner only returned unavailable tool names.",
                reason=reason or "The planner response did not provide tool names from the visible MCP list.",
                usage_notes="",
                debug=debug,
            )
        if not tool_names:
            debug["parse_status"] = "empty_selection"
            return CapabilityPlan(
                selection_kind="none",
                query=query,
                thought_summary=thought_summary or "I checked the visible MCP bundles, but no usable tool names were returned.",
                reason=reason or "The planner response did not provide a valid MCP-only selection.",
                usage_notes="",
                debug=debug,
            )
        debug["parse_status"] = "json_ok"
        return CapabilityPlan(
            selection_kind="mcp",
            skill_ids=[],
            tool_names=tool_names,
            query=query,
            thought_summary=thought_summary or "I should use a concrete MCP tool next.",
            reason=reason or "No skill fit, so a narrowed MCP tool bundle is the best fallback.",
            usage_notes=usage_notes or "Use the selected MCP tools directly for the next concrete step.",
            debug=debug,
        )
    debug["parse_status"] = "planner_selected_none" if raw_selection_kind == "none" else "normalized_to_none"
    return CapabilityPlan(
        selection_kind="none",
        skill_ids=[],
        tool_names=[],
        query=query,
        thought_summary=thought_summary or "I can continue without selecting another capability bundle.",
        reason=reason or "No suitable skill or MCP tool remained after the current exclusions.",
        usage_notes="",
        debug=debug,
    )


def _normalize_task_types(raw_items: Any) -> list[str]:
    normalized = []
    for value in _coerce_string_list(raw_items):
        if value in SEARCHER_TASK_TYPES and value not in normalized:
            normalized.append(value)
    return normalized


def _searcher_result_from_assistant_message(
    assistant_msg: dict[str, Any],
    *,
    skill_catalog: list[dict[str, Any]],
) -> SearcherResult:
    raw_content = str(assistant_msg.get("content") or "")
    payload = _extract_json_payload(raw_content) or {}
    allowed_skill_ids = {
        str(item.get("skill_id") or "").strip()
        for item in (skill_catalog or [])
        if isinstance(item, dict) and str(item.get("skill_id") or "").strip()
    }
    mode = str(payload.get("mode") or "task_types").strip().lower() or "task_types"
    if mode not in {"skill", "task_types", "inventory"}:
        mode = "task_types"
    skill_id = str(payload.get("skill_id") or "").strip()
    if skill_id and skill_id not in allowed_skill_ids:
        skill_id = ""
    task_types = _normalize_task_types(payload.get("task_types"))
    if mode == "skill" and not skill_id:
        mode = "task_types"
    if mode == "task_types" and not task_types:
        task_types = ["general_mcp"]
    return SearcherResult(
        mode=mode,
        skill_id=skill_id,
        task_types=task_types,
        matched_tool_names=[],
        query=_compact_text(str(payload.get("query") or ""), MAX_PREVIEW_CHARS),
        thought_summary=_compact_text(str(payload.get("thought_summary") or ""), MAX_STEP_TEXT_CHARS)
        or ("I found a matching default-enabled skill." if mode == "skill" else "I mapped the task to tool domains."),
        reason=_compact_text(str(payload.get("reason") or ""), MAX_PREVIEW_CHARS),
        debug={
            "raw_content_excerpt": _compact_text(raw_content, MAX_PREVIEW_CHARS),
            "visible_skill_count": len(allowed_skill_ids),
        },
    )


def _matched_tool_result_from_assistant_message(
    assistant_msg: dict[str, Any],
    *,
    allowed_tool_names: set[str],
    fallback_task_types: list[str],
    fallback_tool_names: list[str],
) -> SearcherResult:
    raw_content = str(assistant_msg.get("content") or "")
    payload = _extract_json_payload(raw_content) or {}
    task_types = _normalize_task_types(payload.get("task_types")) or list(fallback_task_types)
    matched_tool_names = _normalize_tool_candidates(payload.get("matched_tool_names"), allowed_tool_names)
    if not matched_tool_names:
        matched_tool_names = [name for name in fallback_tool_names if name in allowed_tool_names]
    return SearcherResult(
        mode="task_types",
        skill_id="",
        task_types=task_types,
        matched_tool_names=matched_tool_names,
        query=_compact_text(str(payload.get("query") or ""), MAX_PREVIEW_CHARS),
        thought_summary=_compact_text(str(payload.get("thought_summary") or ""), MAX_STEP_TEXT_CHARS)
        or "I filtered the MCP tools for the next planning step.",
        reason=_compact_text(str(payload.get("reason") or ""), MAX_PREVIEW_CHARS),
        debug={
            "raw_content_excerpt": _compact_text(raw_content, MAX_PREVIEW_CHARS),
            "visible_tool_count": len(allowed_tool_names),
        },
    )


def _normalize_candidate_tool_sets(raw_sets: Any, allowed_tool_names: set[str]) -> list[list[str]]:
    normalized: list[list[str]] = []
    if not isinstance(raw_sets, list):
        return normalized
    for raw_set in raw_sets:
        if not isinstance(raw_set, list):
            continue
        names = _normalize_tool_candidates(raw_set, allowed_tool_names)
        if names:
            normalized.append(names)
    return normalized


def _execution_plan_from_assistant_message(
    assistant_msg: dict[str, Any],
    *,
    allowed_tool_names: set[str],
    fallback_tool_names: list[str],
) -> ExecutionPlan:
    raw_content = str(assistant_msg.get("content") or "")
    payload = _extract_json_payload(raw_content) or {}
    raw_steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    steps: list[PlanStep] = []
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            continue
        title = _compact_text(str(raw_step.get("title") or f"Step {index + 1}"), 120)
        goal = _compact_text(str(raw_step.get("goal") or title), 220)
        success_criteria = _compact_text(str(raw_step.get("success_criteria") or goal), 220)
        call_mode = str(raw_step.get("call_mode") or "single").strip().lower() or "single"
        if call_mode not in {"single", "batch"}:
            call_mode = "single"
        candidate_tool_sets = _normalize_candidate_tool_sets(raw_step.get("candidate_tool_sets"), allowed_tool_names)
        if not candidate_tool_sets and fallback_tool_names:
            fallback_group = [name for name in fallback_tool_names if name in allowed_tool_names]
            if fallback_group:
                candidate_tool_sets = [fallback_group]
        if not candidate_tool_sets:
            continue
        if call_mode == "batch":
            candidate_tool_sets = [item for item in candidate_tool_sets if len(item) >= 2] or candidate_tool_sets
        else:
            candidate_tool_sets = [[item[0]] for item in candidate_tool_sets if item]
        if not candidate_tool_sets:
            continue
        steps.append(
            PlanStep(
                step_id=str(raw_step.get("step_id") or f"step-{index + 1}").strip() or f"step-{index + 1}",
                title=title,
                goal=goal,
                success_criteria=success_criteria,
                call_mode=call_mode,
                candidate_tool_sets=candidate_tool_sets,
                notes=_compact_text(str(raw_step.get("notes") or ""), 240),
            )
        )
    if not steps:
        fallback_group = [name for name in fallback_tool_names if name in allowed_tool_names]
        if not fallback_group and allowed_tool_names:
            fallback_group = [sorted(allowed_tool_names)[0]]
        if fallback_group:
            steps = [
                PlanStep(
                    step_id="step-1",
                    title="First concrete step",
                    goal="Take the next useful tool step for the user's request.",
                    success_criteria="The next concrete subtask is completed with a useful result.",
                    call_mode="single",
                    candidate_tool_sets=[[fallback_group[0]]],
                    notes="Fallback plan synthesized after invalid planner output.",
                )
            ]
    return ExecutionPlan(
        plan_id=str(payload.get("plan_id") or "plan-1").strip() or "plan-1",
        plan_summary=_compact_text(str(payload.get("plan_summary") or "Follow a multi-step execution plan."), 220),
        reason=_compact_text(str(payload.get("reason") or ""), MAX_PREVIEW_CHARS),
        steps=steps,
        debug={"raw_content_excerpt": _compact_text(raw_content, MAX_PREVIEW_CHARS)},
    )


def _decision_from_assistant_message(
    assistant_msg: dict[str, Any],
    allowed_names: set[str],
) -> TurnDecision:
    content = str(assistant_msg.get("content") or "")
    native_tool_calls = _normalize_native_tool_calls(assistant_msg.get("tool_calls"), allowed_names)
    payload = _extract_json_payload(content) or {}
    if native_tool_calls:
        tool_calls = native_tool_calls
        needs_tool = True
        thought_summary = _default_thought_summary(True)
        action_message = _compact_text(content, MAX_PREVIEW_CHARS)
        if not action_message:
            action_message = _default_action_message(tool_calls)
        expected_effect = _default_expected_effect(tool_calls)
    else:
        needs_tool = bool(payload.get("needs_tool"))
        tool_calls = _normalize_tool_intents(payload.get("tool_calls"), allowed_names)
        if not tool_calls:
            needs_tool = False
        thought_summary = _compact_text(str(payload.get("thought_summary") or ""), MAX_STEP_TEXT_CHARS)
        if not thought_summary:
            thought_summary = _default_thought_summary(needs_tool)
        action_message = _compact_text(str(payload.get("action_message") or ""), MAX_PREVIEW_CHARS)
        if needs_tool and not action_message:
            action_message = _default_action_message(tool_calls)
        expected_effect = _compact_text(str(payload.get("expected_effect") or ""), MAX_PREVIEW_CHARS)
        if needs_tool and not expected_effect:
            expected_effect = _default_expected_effect(tool_calls)
    return TurnDecision(
        needs_tool=needs_tool,
        thought_summary=thought_summary,
        action_message=action_message,
        tool_calls=tool_calls,
        expected_effect=expected_effect if needs_tool else "",
    )


def _default_thought_summary(needs_tool: bool) -> str:
    if needs_tool:
        return "我先判断了一下，这次需要借助工具来确认信息。"
    return "我先整理了一下问题，这次可以直接回答。"


def _default_action_message(tool_calls: list[ToolIntent]) -> str:
    names = [item.name for item in tool_calls if item.name]
    if not names:
        return "我打算先借助工具确认细节，再回来认真回答你。"
    joined = "、".join(dict.fromkeys(names))
    return f"我准备调用这些工具来帮你处理：{joined}。如果你同意，我就开始。"


def _default_expected_effect(tool_calls: list[ToolIntent]) -> str:
    names = [item.name for item in tool_calls if item.name]
    if not names:
        return ""
    if len(names) == 1:
        return f"Use {names[0]} to complete the next concrete step and bring back a useful result."
    joined = ", ".join(dict.fromkeys(names))
    return f"Use the planned tools ({joined}) to make concrete progress on the user's request."


def _tool_summary_list(tool_calls: list[ToolIntent]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for intent in tool_calls:
        items.append(
            {
                "name": intent.name,
                "summary": f"{intent.name}：{_compact_text(_stable_json(intent.arguments), 100)}" if intent.arguments else intent.name,
            }
        )
    return items


async def decide_turn(
    *,
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]],
    llm_provider: str = "ollama",
    api_base_url: str = "",
    api_key: str = "",
    provider_adapter: ProviderAdapter | None = None,
) -> TurnDecision:
    allowed_names = _tool_names(tools)
    if tools:
        decision_messages = [{"role": "system", "content": _tool_catalog_prompt(tools)}] + list(messages)
    else:
        decision_messages = [{"role": "system", "content": _decider_prompt()}] + list(messages)
    adapter = provider_adapter or ProviderAdapter(
        provider=llm_provider,
        base_url=api_base_url,
        api_key=api_key,
    )
    assistant_msg = await adapter.chat_once(
        messages=decision_messages,
        model=model,
        tools=tools or None,
    )
    if not tools:
        return _decider_from_assistant_message(assistant_msg)
    return _decision_from_assistant_message(assistant_msg, allowed_names)


async def decide_tool_need(
    *,
    messages: list[dict[str, Any]],
    model: str,
    llm_provider: str = "ollama",
    api_base_url: str = "",
    api_key: str = "",
    provider_adapter: ProviderAdapter | None = None,
) -> TurnDecision:
    decider_messages = [{"role": "system", "content": _decider_prompt()}] + list(messages)
    adapter = provider_adapter or ProviderAdapter(
        provider=llm_provider,
        base_url=api_base_url,
        api_key=api_key,
    )
    assistant_msg = await adapter.chat_once(
        messages=decider_messages,
        model=model,
        tools=None,
    )
    return _decider_from_assistant_message(assistant_msg)


async def plan_capabilities(
    *,
    messages: list[dict[str, Any]],
    model: str,
    skill_catalog: list[dict[str, Any]],
    tool_catalog: list[dict[str, Any]],
    exclude_skill_ids: list[str] | None = None,
    exclude_tool_names: list[str] | None = None,
    llm_provider: str = "ollama",
    api_base_url: str = "",
    api_key: str = "",
    provider_adapter: ProviderAdapter | None = None,
) -> CapabilityPlan:
    excluded_skill_ids = [str(item).strip() for item in (exclude_skill_ids or []) if str(item).strip()]
    excluded_tool_names = [str(item).strip() for item in (exclude_tool_names or []) if str(item).strip()]
    prompt = _capability_plan_prompt(
        list(skill_catalog or []),
        list(tool_catalog or []),
        excluded_skill_ids=excluded_skill_ids,
        excluded_tool_names=excluded_tool_names,
    )
    planner_messages = [{"role": "system", "content": prompt}] + list(messages)
    adapter = provider_adapter or ProviderAdapter(
        provider=llm_provider,
        base_url=api_base_url,
        api_key=api_key,
    )
    assistant_msg = await adapter.chat_once(
        messages=planner_messages,
        model=model,
        tools=None,
    )
    return _capability_plan_from_assistant_message(
        assistant_msg,
        list(skill_catalog or []),
        list(tool_catalog or []),
    )


def _tool_catalog_names_by_task_type(task_types: list[str], tool_catalog: list[dict[str, Any]]) -> list[str]:
    normalized_types = set(_normalize_task_types(task_types))
    if not normalized_types:
        normalized_types = {"general_mcp"}
    matched: list[str] = []
    seen: set[str] = set()
    for item in tool_catalog or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        task_type = str(item.get("task_type") or "general_mcp").strip() or "general_mcp"
        if not name or name in seen:
            continue
        if task_type in normalized_types or "general_mcp" in normalized_types:
            seen.add(name)
            matched.append(name)
    return matched


async def search_capabilities(
    *,
    messages: list[dict[str, Any]],
    model: str,
    skill_catalog: list[dict[str, Any]],
    tool_catalog: list[dict[str, Any]],
    llm_provider: str = "ollama",
    api_base_url: str = "",
    api_key: str = "",
    provider_adapter: ProviderAdapter | None = None,
) -> SearcherResult:
    adapter = provider_adapter or ProviderAdapter(
        provider=llm_provider,
        base_url=api_base_url,
        api_key=api_key,
    )
    first_messages = [{"role": "system", "content": _searcher_skill_match_prompt(skill_catalog)}] + list(messages)
    assistant_msg = await adapter.chat_once(
        messages=first_messages,
        model=model,
        tools=None,
    )
    first_result = _searcher_result_from_assistant_message(
        assistant_msg,
        skill_catalog=list(skill_catalog or []),
    )
    if first_result.mode in {"skill", "inventory"}:
        return first_result

    deterministic_tools = _tool_catalog_names_by_task_type(first_result.task_types, list(tool_catalog or []))
    second_messages = [{"role": "system", "content": _searcher_tool_type_prompt(first_result.task_types, tool_catalog)}] + list(messages)
    second_assistant_msg = await adapter.chat_once(
        messages=second_messages,
        model=model,
        tools=None,
    )
    second_result = _matched_tool_result_from_assistant_message(
        second_assistant_msg,
        allowed_tool_names={str(item.get("name") or "").strip() for item in (tool_catalog or []) if isinstance(item, dict)},
        fallback_task_types=first_result.task_types,
        fallback_tool_names=deterministic_tools,
    )
    if not second_result.thought_summary:
        second_result.thought_summary = first_result.thought_summary
    if not second_result.reason:
        second_result.reason = first_result.reason
    return second_result


async def build_execution_plan(
    *,
    messages: list[dict[str, Any]],
    model: str,
    searcher_result: dict[str, Any],
    resolved_skill_prompt: str = "",
    tool_catalog: list[dict[str, Any]] | None = None,
    llm_provider: str = "ollama",
    api_base_url: str = "",
    api_key: str = "",
    provider_adapter: ProviderAdapter | None = None,
) -> ExecutionPlan:
    adapter = provider_adapter or ProviderAdapter(
        provider=llm_provider,
        base_url=api_base_url,
        api_key=api_key,
    )
    matched_tool_names = _normalize_tool_candidates(
        searcher_result.get("matched_tool_names"),
        {
            str(item.get("name") or "").strip()
            for item in (tool_catalog or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        },
    )
    prompt = _planner_execution_plan_prompt(
        user_text=_compact_text(str((messages[-1] if messages else {}).get("content") or ""), 400),
        searcher_result=searcher_result,
        resolved_skill_prompt=resolved_skill_prompt,
        tool_catalog=tool_catalog or [],
    )
    planner_messages = [{"role": "system", "content": prompt}] + list(messages)
    assistant_msg = await adapter.chat_once(
        messages=planner_messages,
        model=model,
        tools=None,
    )
    return _execution_plan_from_assistant_message(
        assistant_msg,
        allowed_tool_names={
            str(item.get("name") or "").strip()
            for item in (tool_catalog or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        },
        fallback_tool_names=matched_tool_names,
    )


async def classify_route(
    *,
    chat_mode: str,
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]],
    skill_summaries: list[dict[str, str]] | None = None,
    llm_provider: str = "ollama",
    api_base_url: str = "",
    api_key: str = "",
    provider_adapter: ProviderAdapter | None = None,
) -> RouteDecision:
    skill_summaries = list(skill_summaries or [])
    normalized_mode = str(chat_mode or "react").strip().lower()
    if normalized_mode == "chat":
        prompt = _router_prompt_chat(tools)
    elif normalized_mode == "skill":
        prompt = _router_prompt_skill(skill_summaries)
    else:
        normalized_mode = "react"
        prompt = _router_prompt_react(tools, skill_summaries)
    router_messages = [{"role": "system", "content": prompt}] + list(messages)
    adapter = provider_adapter or ProviderAdapter(
        provider=llm_provider,
        base_url=api_base_url,
        api_key=api_key,
    )
    assistant_msg = await adapter.chat_once(
        messages=router_messages,
        model=model,
        tools=None,
    )
    return _route_from_assistant_message(
        normalized_mode,
        assistant_msg,
        {str(item.get("id") or "").strip() for item in skill_summaries if isinstance(item, dict)},
        _tool_names(tools),
    )


def execute_tool_calls(
    *,
    tool_calls: list[ToolIntent],
    mcp_bridge: MCPBridge | None,
    max_tool_calls: int = 6,
) -> list[ToolExecution]:
    if mcp_bridge is None:
        return [
            ToolExecution(
                name="tool_bridge",
                arguments={},
                ok=False,
                summary="工具桥接当前不可用，所以这次没法真正执行工具。",
                payload=json.dumps({"ok": False, "error": "tool bridge unavailable"}, ensure_ascii=False),
            )
        ]

    executions: list[ToolExecution] = []
    seen: set[str] = set()

    for index, intent in enumerate(tool_calls):
        if index >= max_tool_calls:
            executions.append(
                ToolExecution(
                    name="tool_limit",
                    arguments={},
                    ok=False,
                    summary="这轮允许调用的工具数量已经到上限了，后面的工具先不执行。",
                    payload=json.dumps({"ok": False, "error": "tool call limit reached"}, ensure_ascii=False),
                )
            )
            break

        dedupe_key = f"{intent.name}:{_stable_json(intent.arguments)}"
        if dedupe_key in seen:
            executions.append(
                ToolExecution(
                    name=intent.name,
                    arguments=intent.arguments,
                    ok=False,
                    summary=f"检测到重复工具调用 {intent.name}，为了避免重复执行，这一步已跳过。",
                    payload=json.dumps({"ok": False, "error": "duplicate tool call skipped"}, ensure_ascii=False),
                )
            )
            continue
        seen.add(dedupe_key)

        try:
            result = mcp_bridge.call_tool(intent.name, intent.arguments)
            if isinstance(result, ToolResult):
                if not result.ok:
                    summary = _compact_text(result.error, MAX_STEP_TEXT_CHARS)
                    executions.append(
                        ToolExecution(
                            name=intent.name,
                            arguments=intent.arguments,
                            ok=False,
                            summary=summary or "宸ュ叿鎵ц澶辫触銆?",
                            payload=json.dumps({"ok": False, "error": summary or "tool execution failed"}, ensure_ascii=False),
                        )
                    )
                    continue
                raw_value = result.structured_data if result.structured_data is not None else result.content
                summary = _compact_text(result.content or raw_value, MAX_PREVIEW_CHARS)
                payload = json.dumps({"ok": True, "result": raw_value}, ensure_ascii=False)
            else:
                summary = _compact_text(result, MAX_PREVIEW_CHARS)
                payload = json.dumps({"ok": True, "result": result}, ensure_ascii=False)
            if len(payload) > MAX_PREVIEW_CHARS:
                payload = json.dumps({"ok": True, "result_preview": summary}, ensure_ascii=False)
            executions.append(
                ToolExecution(
                    name=intent.name,
                    arguments=intent.arguments,
                    ok=True,
                    summary=summary or "工具执行完成，但没有返回可展示的摘要。",
                    payload=payload,
                )
            )
        except Exception as exc:
            summary = _compact_text(str(exc), MAX_STEP_TEXT_CHARS)
            executions.append(
                ToolExecution(
                    name=intent.name,
                    arguments=intent.arguments,
                    ok=False,
                    summary=summary or "工具执行失败。",
                    payload=json.dumps({"ok": False, "error": summary or "tool execution failed"}, ensure_ascii=False),
                )
            )
    return executions


def build_approved_tool_message(
    executions: list[ToolExecution],
    remaining_steps: int | None = None,
    user_request: str = "",
    expected_effect: str = "",
) -> str:
    return _rt_build_approved_tool_message(
        executions,
        remaining_steps=remaining_steps,
        user_request=user_request,
        expected_effect=expected_effect,
    )


def build_rejected_tool_message(decision: TurnDecision) -> str:
    return _rt_build_rejected_tool_message(decision)
    planned = [item.name for item in decision.tool_calls if item.name]
    joined = "、".join(dict.fromkeys(planned))
    if joined:
        return (
            "The user did not approve tool usage. "
            f"You planned to call: {joined}. "
            "Answer without using tools, without pretending the tools were used, and be honest about any remaining uncertainty."
        )
    return (
        "The user did not approve tool usage. "
        "Answer without using tools and do not claim that you executed anything."
    )


def build_inventory_result_message(inventory_result: dict[str, Any], user_request: str = "") -> str:
    return _rt_build_inventory_result_message(inventory_result, user_request=user_request)


def build_rejected_tool_followup_message(decision: TurnDecision, user_text: str) -> str:
    return _rt_build_rejected_tool_followup_message(decision, user_text)


def approval_tool_items(decision: TurnDecision) -> list[dict[str, Any]]:
    return _tool_summary_list(decision.tool_calls)


async def stream_reply(
    *,
    messages: list[dict[str, Any]],
    model: str,
    tools_enabled: bool = True,
    mcp_bridge: MCPBridge | None = None,
    react_enabled: bool = True,
    max_reasoning_steps: int = 10,
    llm_provider: str = "ollama",
    api_base_url: str = "",
    api_key: str = "",
    provider_adapter: ProviderAdapter | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    adapter = provider_adapter or ProviderAdapter(
        provider=llm_provider,
        base_url=api_base_url,
        api_key=api_key,
    )
    prompt_messages = list(messages)
    working_messages = list(messages)
    tool_bridge = mcp_bridge if tools_enabled else None
    tools = tool_bridge.list_tools() if tool_bridge is not None else []
    allowed_names = _tool_names(tools)
    max_steps = max(1, int(max_reasoning_steps or 1))
    executed_signatures: set[str] = set()
    final_emitted = False

    def _signature(intent: ToolIntent) -> str:
        return f"{intent.name}:{_stable_json(intent.arguments)}"

    for step in range(1, max_steps + 1):
        if not react_enabled or not tools_enabled or tool_bridge is None or not tools:
            break

        assistant_msg = await adapter.chat_once(
            messages=working_messages,
            model=model,
            tools=tools or None,
        )
        decision = _decision_from_assistant_message(assistant_msg, allowed_names)
        planned_calls = [item for item in decision.tool_calls if _signature(item) not in executed_signatures]
        if not decision.needs_tool or not planned_calls:
            for item in yield_text(str(assistant_msg.get("content") or "")):
                yield item
            final_emitted = True
            break

        action_text = decision.action_message or _default_action_message(planned_calls)
        yield _event("react_step", phase="action", text=action_text)

        executions = execute_tool_calls(
            tool_calls=planned_calls,
            mcp_bridge=tool_bridge,
        )
        for item in planned_calls:
            executed_signatures.add(_signature(item))

        summaries = [item.summary for item in executions if item.summary]
        observation_summary = "；".join(summaries)
        if step >= max_steps:
            observation_text = (
                f"{observation_summary}。工具步骤已达到上限，我会基于当前结果整理最终答复。"
                if observation_summary
                else "工具步骤已达到上限，我会基于当前结果整理最终答复。"
            )
        else:
            observation_text = (
                f"{observation_summary}。我正在整理最终答复。"
                if observation_summary
                else "工具结果已返回，我正在整理最终答复。"
            )
        yield _event("react_step", phase="observation", text=_compact_text(observation_text, MAX_PREVIEW_CHARS))

        working_messages = prompt_messages + [
            {
                "role": "system",
                "content": build_approved_tool_message(
                    executions,
                    remaining_steps=max(0, max_steps - step),
                    user_request=str(prompt_messages[-1].get("content") or "") if prompt_messages else "",
                ),
            }
        ]
    if not final_emitted:
        assistant_msg = await adapter.chat_once(
            messages=working_messages,
            model=model,
            tools=None,
        )
        for item in yield_text(str(assistant_msg.get("content") or "")):
            yield item


async def stream_final_reply(
    *,
    messages: list[dict[str, Any]],
    model: str,
    llm_provider: str = "ollama",
    api_base_url: str = "",
    api_key: str = "",
    provider_adapter: ProviderAdapter | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    adapter = provider_adapter or ProviderAdapter(
        provider=llm_provider,
        base_url=api_base_url,
        api_key=api_key,
    )
    async for delta in adapter.stream_chat(messages=messages, model=model):
        yield _event("final_delta", delta=delta)


def serialize_tool_calls(tool_calls: list[ToolIntent]) -> list[dict[str, Any]]:
    return [item.to_dict() for item in tool_calls]


def deserialize_tool_calls(raw_items: list[dict[str, Any]] | None) -> list[ToolIntent]:
    items: list[ToolIntent] = []
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        items.append(ToolIntent(name=name, arguments=_parse_args(raw.get("arguments"))))
    return items


def yield_text(text: str) -> list[dict[str, Any]]:
    return [_event("final_delta", delta=chunk) for chunk in _chunks(text)]
