from __future__ import annotations

import json
from typing import Any


EXPR_PROTOCOL_PROMPT = (
    "Output must be strict NDJSON. One JSON object per line with no extra commentary. "
    "Only fields expr and text are allowed. expr is an expression name string (or empty), "
    'text is plain response content. Example: {"expr":"happy","text":"hello"}'
)

REACT_SKILL_VISIBILITY_NOTE = (
    "Use only capabilities exposed in the current runtime phase. "
    "If a required capability is not visible yet, search or transition first "
    "and keep hidden tool names abstract."
)


def _compact_text(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").replace("\r", "\n").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


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


def _tool_names(tools: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        fn = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = str(fn.get("name") or "").strip()
        if name:
            names.add(name)
    return names


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


def _capability_prompt_excerpt(value: Any, limit: int = 220) -> str:
    return _compact_text(str(value or "").replace("\r", "\n"), limit)


def _field(item: Any, key: str, default: Any = "") -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return json.dumps(str(value), ensure_ascii=False)


def tool_catalog_prompt(tools: list[dict[str, Any]]) -> str:
    lines = [
        "You are the decider-and-writer stage for a desktop pet assistant.",
        "Decide whether the user's request requires tools right now.",
        "Do not roleplay. Do not answer the user's question. Only return one JSON object.",
        "Do not reveal hidden reasoning. thought_summary must be short and user-facing.",
        "If tools are needed, action_message should describe the planned work in a friendly, concise way for user approval.",
        "If tools are needed, expected_effect should describe the concrete effect the next approved step is meant to achieve.",
        "When tools are not needed, set needs_tool to false and use an empty array for tool_calls.",
        "The user's full request must be completed before you stop asking for tools.",
        "Do not treat one successful tool call as task completion if the request clearly contains multiple unfinished steps.",
        "For browser automation or multi-step tasks, keep needs_tool=true until the requested sequence is actually completed or cannot be continued usefully.",
        "If part of the request is still unfinished, return only the next concrete tool step instead of jumping to a final answer.",
        "If a previous tool call failed or produced no useful progress, do not repeat it in the same form. Change the parameters materially or switch tools.",
        "If the user asked you to actually do something, such as open a page, click, search, save, inspect, or gather current runtime information, do not answer directly unless that action is already complete.",
        "If concrete non-system tools are already available for the current task, use them directly.",
        "The internal planner has already narrowed the available skills and MCP tools for this round when needed.",
        "If the user explicitly asks what skills, MCP tools, tools, or capabilities are available, rely on the planner-backed inventory result instead of guessing.",
        "Output JSON only with these keys:",
        '{"needs_tool":true,"thought_summary":"brief summary","action_message":"what you plan to do","expected_effect":"what this step should accomplish","tool_calls":[{"name":"read_file","arguments":{"path":"story.txt"}}]}',
        "Available tools:",
    ]
    for tool in tools:
        fn = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = str(fn.get("name") or "").strip()
        description = str(fn.get("description") or "").strip()
        if name:
            lines.append(f"- {name}: {description}")
    return "\n".join(lines)


def router_prompt_react(tools: list[dict[str, Any]], skill_summaries: list[dict[str, str]]) -> str:
    lines = [
        "You are the route-classifier stage for a desktop assistant.",
        "Classify the user's request before the execution model acts.",
        "Return JSON only.",
        'Allowed route_kind values: "skill_task", "complex_task", "simple_tool_task", "direct_answer".',
        'Return fields: {"route_kind":"","thought_summary":"","skill_ids":[],"tool_candidates":[],"tool_call":{"name":"","arguments":{}}}.',
        "Use skill_task when one or more visible skills are the clearest fit.",
        "Use complex_task when the request should fall back to the general agent loop.",
        "Use direct_answer only for pure conversational replies or simple explanations that do not require doing any work.",
        "If the user is asking you to perform a workflow, gather current information, search, save a file, follow a skill, or complete multiple steps, do not return direct_answer.",
        "When visible default skills are not enough, prefer complex_task so the execution model can use agent_loop and the internal planner.",
        "Do not select simple_tool_task in react mode unless a single explicit system tool call is truly required.",
    ]
    tool_names = sorted(_tool_names(tools))
    if tool_names:
        lines.append("Available system capabilities:")
        lines.extend([f"- {name}" for name in tool_names])
    skill_lines = _skill_summary_lines(skill_summaries)
    if skill_lines:
        lines.append("Visible default skills:")
        lines.extend(skill_lines)
    else:
        lines.append("Visible default skills: none")
    lines.append("Use skill_task for default skills. Use complex_task when hidden skills or later capability planning will likely be needed.")
    return "\n".join(lines)


def decider_prompt() -> str:
    return "\n".join(
        [
            "You are the decider stage for a desktop pet assistant.",
            "Decide whether the user's request can be completed right now without tools.",
            "Do not roleplay. Do not answer the user's question. Return JSON only.",
            'Return fields: {"needs_tool":true,"thought_summary":"brief user-facing summary","action_message":"what still needs to happen next"}.',
            "thought_summary must be short and user-facing.",
            "If the user asked you to actually do something, such as open a page, click, search, save, inspect, or gather current runtime information, set needs_tool=true unless that action is already complete.",
            "For explicit capability-availability questions like asking what skills, MCP tools, tools, or capabilities are available, set needs_tool=true so the planner can enumerate the current options.",
            "Do not include tool_calls in this stage.",
        ]
    )


def capability_plan_prompt(
    skill_catalog: list[dict[str, Any]],
    tool_catalog: list[dict[str, Any]],
    *,
    excluded_skill_ids: list[str],
    excluded_tool_names: list[str],
) -> str:
    lines = [
        "You are the capability-planning stage for a desktop assistant.",
        "Choose the next capability bundle for the main execution model.",
        "Return JSON only.",
        'Return fields: {"selection_kind":"skill|mcp|none","skill_ids":[],"tool_names":[],"query":"","thought_summary":"","reason":"","usage_notes":""}.',
        "The listed skills are the only planner-visible skills for this turn.",
        "Prefer skills first. Only return selection_kind=mcp when no listed skill is suitable.",
        "Return exactly one selection kind. Do not return both skill_ids and tool_names in the same response.",
        "When you choose a skill, return one or more skill_ids and an empty tool_names array.",
        "When you choose mcp, return one or more tool_names and an empty skill_ids array.",
        "If nothing suitable remains after exclusions, return selection_kind=none.",
        "thought_summary must be short and user-facing.",
        "reason should briefly explain why this bundle is the best next step.",
        "usage_notes should briefly explain how the writer should use the selected skill or MCP tools next.",
    ]
    if excluded_skill_ids:
        lines.append(f"Already excluded skill_ids: {', '.join(excluded_skill_ids)}")
    if excluded_tool_names:
        lines.append(f"Already excluded tool_names: {', '.join(excluded_tool_names)}")
    if skill_catalog:
        lines.append("Available planner-visible skills:")
        for item in skill_catalog:
            if not isinstance(item, dict):
                continue
            skill_id = str(item.get("skill_id") or "").strip()
            if not skill_id:
                continue
            display_name = str(item.get("display_name") or skill_id).strip()
            description = _capability_prompt_excerpt(item.get("description") or "", 160)
            tool_names = [
                str(tool_name or "").strip()
                for tool_name in (item.get("tool_names") or [])
                if str(tool_name or "").strip()
            ]
            prompt_excerpt = _capability_prompt_excerpt(item.get("prompt_excerpt") or "", 180)
            line = f"- {skill_id} ({display_name})"
            if description:
                line += f": {description}"
            if tool_names:
                line += f" | tools: {', '.join(tool_names[:8])}"
            if prompt_excerpt:
                line += f" | prompt: {prompt_excerpt}"
            lines.append(line)
    else:
        lines.append("Available planner-visible skills: none")
    if tool_catalog:
        lines.append("Available MCP tools:")
        for item in tool_catalog:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("name") or "").strip()
            if not tool_name:
                continue
            description = _capability_prompt_excerpt(item.get("description") or "", 160)
            source = str(item.get("source") or "").strip()
            line = f"- {tool_name}"
            if description:
                line += f": {description}"
            if source:
                line += f" | source: {source}"
            lines.append(line)
    else:
        lines.append("Available MCP tools: none")
    return "\n".join(lines)


def searcher_skill_match_prompt(skill_catalog: list[dict[str, Any]]) -> str:
    lines = [
        "You are the searcher stage for a desktop pet assistant.",
        "Look at the user's request and decide the next capability discovery path.",
        "Return JSON only.",
        'Return fields: {"mode":"skill|task_types|inventory","skill_id":"","task_types":[],"matched_tool_names":[],"reason":"","thought_summary":""}.',
        "Only choose mode=skill when one listed default-enabled skill is clearly the best fit.",
        "Use mode=task_types when the task should be handled through MCP tool domains.",
        "Use mode=inventory only when the user is explicitly asking what skills, MCP tools, tools, or capabilities are currently available.",
        'Allowed task_types: ["browser_automation","web_search","file_io","general_mcp"].',
        "Do not invent new task types.",
        "Do not return matched_tool_names in this stage unless mode=inventory.",
        "thought_summary must be short and user-facing.",
    ]
    if skill_catalog:
        lines.append("Default-enabled skills visible to the searcher:")
        for item in skill_catalog:
            if not isinstance(item, dict):
                continue
            skill_id = str(item.get("skill_id") or "").strip()
            if not skill_id:
                continue
            display_name = str(item.get("display_name") or skill_id).strip()
            description = _capability_prompt_excerpt(item.get("description") or "", 160)
            prompt_excerpt = _capability_prompt_excerpt(item.get("prompt_excerpt") or "", 180)
            line = f"- {skill_id} ({display_name})"
            if description:
                line += f": {description}"
            if prompt_excerpt:
                line += f" | prompt: {prompt_excerpt}"
            lines.append(line)
    else:
        lines.append("Default-enabled skills visible to the searcher: none")
    return "\n".join(lines)


def searcher_tool_type_prompt(task_types: list[str], tool_catalog: list[dict[str, Any]]) -> str:
    normalized_types = _normalize_name_list(task_types)
    lines = [
        "You are the searcher tool-filter stage for a desktop pet assistant.",
        "You already know the task types that match the user's request.",
        "Return JSON only.",
        'Return fields: {"mode":"task_types","skill_id":"","task_types":[],"matched_tool_names":[],"reason":"","thought_summary":""}.',
        "Return matched_tool_names from the listed MCP tools only.",
        "Prefer the smallest useful tool set that can start the task.",
        "Do not invent tool names.",
        "thought_summary must be short and user-facing.",
        f"Task types for this request: {', '.join(normalized_types) if normalized_types else 'general_mcp'}",
    ]
    if tool_catalog:
        lines.append("Available MCP tools after backend task-type classification:")
        for item in tool_catalog:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("name") or "").strip()
            if not tool_name:
                continue
            description = _capability_prompt_excerpt(item.get("description") or "", 160)
            task_type = str(item.get("task_type") or "").strip()
            line = f"- {tool_name}"
            if description:
                line += f": {description}"
            if task_type:
                line += f" | task_type: {task_type}"
            lines.append(line)
    else:
        lines.append("Available MCP tools after backend task-type classification: none")
    return "\n".join(lines)


def planner_execution_plan_prompt(
    *,
    user_text: str,
    searcher_result: dict[str, Any],
    resolved_skill_prompt: str = "",
    tool_catalog: list[dict[str, Any]] | None = None,
) -> str:
    mode = str(searcher_result.get("mode") or "task_types").strip().lower() or "task_types"
    task_types = _normalize_name_list(searcher_result.get("task_types") or [])
    matched_tool_names = _normalize_name_list(searcher_result.get("matched_tool_names") or [])
    lines = [
        "You are the planner stage for a desktop pet assistant.",
        "Convert the current request into a structured multi-step execution plan.",
        "Return JSON only.",
        'Return fields: {"plan_id":"","plan_summary":"","reason":"","steps":[{"step_id":"","title":"","goal":"","success_criteria":"","call_mode":"single|batch","candidate_tool_sets":[["tool.name"]],"notes":""}]}.',
        "Always return at least one step.",
        "Each step must contain one or more candidate_tool_sets.",
        "Each candidate_tool_set is an ordered group of tool names that the writer may submit together for one approval batch.",
        'Use call_mode="single" when the step should submit exactly one tool call in the next batch.',
        'Use call_mode="batch" when the step needs two or more tool calls in the same approval batch.',
        "Do not invent tool names outside the provided skill or MCP context.",
        "Do not fill concrete tool arguments. The writer will do that later.",
    ]
    if user_text:
        lines.append(f"Original user request: {user_text}")
    if mode == "skill":
        lines.append("Searcher result: use the selected default-enabled skill workflow.")
        if searcher_result.get("skill_id"):
            lines.append(f"Selected skill_id: {searcher_result.get('skill_id')}")
        if resolved_skill_prompt:
            lines.append("Resolved skill prompt:")
            lines.append(str(resolved_skill_prompt or "").strip())
    else:
        lines.append("Searcher result: use MCP tools filtered by task type.")
        if task_types:
            lines.append(f"Task types: {', '.join(task_types)}")
        if matched_tool_names:
            lines.append(f"Matched tool names: {', '.join(matched_tool_names)}")
        catalog = list(tool_catalog or [])
        if catalog:
            lines.append("Filtered MCP tools visible to the planner:")
            for item in catalog:
                if not isinstance(item, dict):
                    continue
                tool_name = str(item.get("name") or "").strip()
                if not tool_name:
                    continue
                description = _capability_prompt_excerpt(item.get("description") or "", 160)
                task_type = str(item.get("task_type") or "").strip()
                line = f"- {tool_name}"
                if description:
                    line += f": {description}"
                if task_type:
                    line += f" | task_type: {task_type}"
                lines.append(line)
        else:
            lines.append("Filtered MCP tools visible to the planner: none")
    return "\n".join(lines)


def writer_step_prompt(
    *,
    user_text: str,
    plan: dict[str, Any],
    step: dict[str, Any],
    candidate_tool_set: list[str],
    step_index: int,
    step_count: int,
    attempt: int,
    strict: bool = False,
) -> str:
    lines = [
        "You are the writer stage for a desktop pet assistant.",
        "The decider, searcher, and planner have already finished their work for this round.",
        "Write the next concrete approval batch for the current step only.",
        "Return JSON only in the normal decision format.",
        "Do not answer the user directly.",
        "Use only the visible tools for the current candidate tool set.",
        "Do not reassess whether tools are available. The visible tools are the allowed tools for this batch.",
        "Do not skip ahead to later steps.",
    ]
    if user_text:
        lines.append(f"Original user request: {user_text}")
    lines.append(f"Plan summary: {str(plan.get('plan_summary') or '').strip()}")
    lines.append(f"Current step: {step_index + 1}/{max(1, int(step_count))}")
    lines.append(f"Step title: {str(step.get('title') or '').strip()}")
    lines.append(f"Step goal: {str(step.get('goal') or '').strip()}")
    lines.append(f"Step success criteria: {str(step.get('success_criteria') or '').strip()}")
    lines.append(f"Current step attempt: {max(1, int(attempt))}")
    lines.append(f"Current candidate tool set: {', '.join(candidate_tool_set) if candidate_tool_set else 'none'}")
    call_mode = str(step.get("call_mode") or "single").strip().lower() or "single"
    if candidate_tool_set:
        lines.append(f"Planner-selected MCP tools: {', '.join(candidate_tool_set)}")
    if call_mode == "batch":
        lines.append("This step uses call_mode=batch. You must emit at least 2 tool_calls from the current candidate tool set.")
    else:
        lines.append("This step uses call_mode=single. You must emit exactly 1 tool_call from the current candidate tool set.")
    notes = str(step.get("notes") or "").strip()
    if notes:
        lines.append(f"Planner notes: {notes}")
    if strict:
        lines.append("Returning needs_tool=false is invalid while the current step still has visible tools and unfinished work.")
        lines.append("If the visible tools can plausibly advance the current step, you must emit a valid tool_calls batch now.")
    return "\n".join(lines)


def router_prompt_chat(tools: list[dict[str, Any]]) -> str:
    lines = [
        "You are the route-classifier stage for chat mode.",
        "Return JSON only.",
        'Return fields: {"search_needed":true,"search_query":"keywords","thought_summary":"brief user-facing summary"}.',
        "Only decide whether web search is needed and what to search for.",
        "Do not mention any other MCP, local tool, or skill.",
        "If search is not needed, set search_needed to false and search_query to an empty string.",
    ]
    tool_names = sorted(_tool_names(tools))
    if tool_names:
        lines.append("Available search tools:")
        lines.extend([f"- {name}" for name in tool_names])
    return "\n".join(lines)


def router_prompt_skill(skill_summaries: list[dict[str, str]]) -> str:
    lines = [
        "You are the route-classifier stage for skill mode.",
        "Return JSON only.",
        'Return fields: {"skill_ids":[],"thought_summary":"brief user-facing summary"}.',
        "Select the most relevant skill IDs for the user's request.",
        "Do not mention MCP or other tools.",
        "If no listed skill fits, return an empty array.",
    ]
    skill_lines = _skill_summary_lines(skill_summaries)
    if skill_lines:
        lines.append("Available skills:")
        lines.extend(skill_lines)
    return "\n".join(lines)


def build_expression_protocol_prompt(available_expressions: list[str] | None = None) -> str:
    allowed = [str(item).strip() for item in (available_expressions or []) if str(item).strip()]
    if allowed:
        expr_list = ", ".join(json.dumps(item, ensure_ascii=False) for item in allowed)
        return (
            f"{EXPR_PROTOCOL_PROMPT} "
            f"Available expr values are: {expr_list}. "
            'If none fits, use "" for expr.'
        )
    return f'{EXPR_PROTOCOL_PROMPT} If no matching expression exists, use "" for expr.'


def build_system_prompt(
    user_prompt: str,
    skill_prompt: str,
    expression_mode: bool,
    output_format: str,
    available_expressions: list[str] | None = None,
) -> str:
    parts = [str(user_prompt or "").strip(), str(skill_prompt or "").strip()]
    base = "\n\n".join([item for item in parts if item])
    if not expression_mode or output_format != "ndjson_v1":
        return base
    protocol_prompt = build_expression_protocol_prompt(available_expressions)
    return f"{base}\n\n{protocol_prompt}" if base else protocol_prompt


def build_decision_messages(base_messages: list[dict[str, Any]], skill_prompt: str) -> list[dict[str, Any]]:
    prompt = str(skill_prompt or "").strip()
    if not prompt:
        return list(base_messages)
    return [
        {
            "role": "system",
            "content": (
                "Follow the active skill instructions below while deciding the next tool step. "
                "Do not mark the task complete until that workflow is actually finished.\n\n"
                f"{prompt}"
            ),
        },
        *list(base_messages),
    ]


def continuation_recheck_prompt(user_text: str, remaining_steps: int) -> str:
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


def writer_prompt_from_planner(state: dict[str, Any], plan_result: dict[str, Any], *, strict: bool = False) -> str:
    lines = [
        "You are the writer stage for a desktop pet assistant.",
        "The decider has already determined that the task needs tools.",
        "The planner has already selected the capability bundle for this round.",
        "Use only the currently visible tools to produce the next concrete tool call.",
        "Do not reassess whether tools are available. The currently visible tools are the usable bundle for this round.",
        "Do not apologize for missing capabilities when planner-selected tools are visible.",
        "Return only the next concrete tool step, not the whole sequence.",
        "Do not answer the user directly.",
        "Return JSON only in the normal decision format.",
    ]
    user_text = str(state.get("user_text") or "").strip()
    if user_text:
        lines.append(f"Original user request: {user_text}")
    selection_kind = str(plan_result.get("selection_kind") or "none").strip().lower()
    skill_ids = _normalize_name_list(plan_result.get("skill_ids") or [])
    tool_names = _normalize_name_list(plan_result.get("tool_names") or [])
    if selection_kind == "skill" and skill_ids:
        lines.append(f"Planner-selected skills: {', '.join(skill_ids)}")
    if selection_kind == "mcp" and tool_names:
        lines.append(f"Planner-selected MCP tools: {', '.join(tool_names)}")
    reason = str(plan_result.get("reason") or "").strip()
    if reason:
        lines.append(f"Planner reason: {reason}")
    usage_notes = str(plan_result.get("usage_notes") or "").strip()
    if usage_notes:
        lines.append(f"Planner usage notes: {usage_notes}")
    if strict:
        lines.append("If any currently visible tool can plausibly advance the task, you must return needs_tool=true with at least one concrete tool_call.")
        lines.append("Returning needs_tool=false, apologizing, or giving a final answer is invalid while planner-selected tools are still visible.")
    return "\n".join(lines)


def _sample_argument_value(name: str, schema: dict[str, Any]) -> Any:
    key = str(name or "").strip().lower()
    schema_type = str(schema.get("type") or "").strip().lower()
    if key == "url":
        return "https://example.com"
    if key in {"path", "file", "filepath", "file_path"}:
        return "example.txt"
    if key in {"text", "query", "keyword", "keywords", "value"}:
        return "example"
    if key in {"selector", "css", "xpath"}:
        return "body"
    if key in {"key", "key_name"}:
        return "Enter"
    if key in {"wait_ms", "timeout", "timeout_ms", "delay", "delay_ms"}:
        return 1000
    if schema_type == "boolean":
        return True
    if schema_type in {"integer", "number"}:
        return 1
    if schema_type == "array":
        return []
    if schema_type == "object":
        return {}
    return "example"


def _tool_call_example(tool_schema: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(tool_schema, dict):
        return None
    function = tool_schema.get("function")
    if not isinstance(function, dict):
        return None
    tool_name = str(function.get("name") or "").strip()
    parameters = function.get("parameters")
    if not tool_name or not isinstance(parameters, dict):
        return None
    props = parameters.get("properties")
    if not isinstance(props, dict):
        props = {}
    required = parameters.get("required")
    required_names = [str(item).strip() for item in (required if isinstance(required, list) else []) if str(item).strip()]
    argument_names = required_names or [str(name).strip() for name in props.keys() if str(name).strip()]
    arguments: dict[str, Any] = {}
    for arg_name in argument_names[:4]:
        arg_schema = props.get(arg_name) if isinstance(props.get(arg_name), dict) else {}
        arguments[arg_name] = _sample_argument_value(arg_name, arg_schema)
    return {
        "needs_tool": True,
        "thought_summary": "Use the selected tool for the next concrete step.",
        "action_message": f"Call {tool_name} for the next step.",
        "expected_effect": f"{tool_name} completes the next concrete step for the request.",
        "tool_calls": [{"name": tool_name, "arguments": arguments}],
    }


def writer_tool_call_template_prompt(tools: list[dict[str, Any]], plan_result: dict[str, Any]) -> str:
    visible_names = [
        str(item.get("name") or "").strip()
        for item in (plan_result.get("inventory_tools") or [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    if not visible_names:
        visible_names = _normalize_name_list(plan_result.get("tool_names") or [])
    lines = [
        "Tool-call JSON template for the writer stage.",
        "Copy this shape exactly and fill in the real arguments for the next step.",
        '{"needs_tool":true,"thought_summary":"brief summary","action_message":"what you plan to do next","expected_effect":"what this step should accomplish","tool_calls":[{"name":"TOOL_NAME","arguments":{"key":"value"}}]}',
    ]
    examples_added = 0
    for tool_schema in tools or []:
        example = _tool_call_example(tool_schema)
        if example is None:
            continue
        function = tool_schema.get("function") if isinstance(tool_schema, dict) else {}
        tool_name = str(function.get("name") or "").strip() if isinstance(function, dict) else ""
        if visible_names and tool_name and tool_name not in visible_names:
            continue
        lines.append(f"Example for {tool_name}:")
        lines.append(json.dumps(example, ensure_ascii=False))
        examples_added += 1
        if examples_added >= 3:
            break
    if visible_names:
        lines.append(f"Visible tool names for this round: {', '.join(visible_names)}")
    return "\n".join(lines)


def writer_step_template_prompt(
    tools: list[dict[str, Any]],
    step: dict[str, Any],
    candidate_tool_set: list[str],
) -> str:
    call_mode = str(step.get("call_mode") or "single").strip().lower() or "single"
    visible_names = [item for item in _normalize_name_list(candidate_tool_set) if item]
    lines = [
        "Tool-call JSON template for the writer stage.",
        "This template is scoped to the current writer step.",
        "Copy this shape exactly and fill in real arguments for the current step only.",
    ]
    if call_mode == "batch":
        lines.append(
            '{"needs_tool":true,"thought_summary":"brief summary","action_message":"what this batch will do","expected_effect":"what the whole batch should accomplish","tool_calls":[{"name":"TOOL_A","arguments":{"key":"value"}},{"name":"TOOL_B","arguments":{"key":"value"}}]}'
        )
    else:
        lines.append(
            '{"needs_tool":true,"thought_summary":"brief summary","action_message":"what this call will do","expected_effect":"what this call should accomplish","tool_calls":[{"name":"TOOL_NAME","arguments":{"key":"value"}}]}'
        )

    examples: list[dict[str, Any]] = []
    for tool_schema in tools or []:
        example = _tool_call_example(tool_schema)
        if example is None:
            continue
        function = tool_schema.get("function") if isinstance(tool_schema, dict) else {}
        tool_name = str(function.get("name") or "").strip() if isinstance(function, dict) else ""
        if visible_names and tool_name and tool_name not in visible_names:
            continue
        examples.append(example)
        if len(examples) >= 3:
            break
    if call_mode == "batch" and len(examples) >= 2:
        batch_example = {
            "needs_tool": True,
            "thought_summary": "Use the current candidate tool set for the current step.",
            "action_message": "Submit the current batch for approval.",
            "expected_effect": "This batch should satisfy the current step criteria.",
            "tool_calls": [examples[0]["tool_calls"][0], examples[1]["tool_calls"][0]],
        }
        lines.append("Batch example:")
        lines.append(json.dumps(batch_example, ensure_ascii=False))
    else:
        for example in examples:
            tool_name = str(example.get("tool_calls", [{}])[0].get("name") or "").strip()
            lines.append(f"Example for {tool_name}:")
            lines.append(json.dumps(example, ensure_ascii=False))
    if visible_names:
        lines.append(f"Visible tool names for the current candidate set: {', '.join(visible_names)}")
    return "\n".join(lines)


def leader_plan_assessment_prompt(
    *,
    user_text: str,
    plan: dict[str, Any],
    plan_status: str,
    completed_steps: list[str],
    blocked_steps: list[str],
    loop_round: int,
) -> str:
    lines = [
        "You are the leader stage for the current execution plan.",
        f"Current loop round: {max(1, int(loop_round))}.",
        "Decide whether the plan is completed, blocked, or needs a fresh replan.",
        'Return JSON only with fields: {"decision":"plan_completed|plan_blocked|needs_replan","thought_summary":"","reason":""}.',
        "Do not request concrete tool calls here.",
    ]
    if user_text:
        lines.append(f"Original user request: {user_text}")
    if plan:
        lines.append(f"Plan summary: {str(plan.get('plan_summary') or '').strip()}")
    lines.append(f"Observed plan status: {str(plan_status or '').strip() or 'unknown'}")
    if completed_steps:
        lines.append(f"Completed steps: {', '.join(_normalize_name_list(completed_steps))}")
    if blocked_steps:
        lines.append(f"Blocked steps: {', '.join(_normalize_name_list(blocked_steps))}")
    return "\n".join(lines)


def planner_none_observation_text(plan_result: dict[str, Any]) -> str:
    debug = plan_result.get("debug") if isinstance(plan_result.get("debug"), dict) else {}
    parse_status = str(debug.get("parse_status") or "").strip().lower() or "unknown"
    try:
        visible_skill_count = max(0, int(debug.get("visible_skill_count") or 0))
    except Exception:
        visible_skill_count = 0
    try:
        visible_tool_count = max(0, int(debug.get("visible_tool_count") or 0))
    except Exception:
        visible_tool_count = 0

    lines = [
        f"Checked {visible_skill_count} default-enabled skill(s) and {visible_tool_count} MCP tool(s).",
    ]
    if parse_status == "planner_selected_none":
        lines.append("Planner returned none for this round.")
    elif parse_status == "json_invalid":
        lines.append("Planner did not return valid JSON, so no usable selection was parsed.")
    elif parse_status == "invalid_tool_names":
        lines.append("Planner proposed tool names outside the visible MCP tool list.")
    elif parse_status == "invalid_skill_ids":
        lines.append("Planner proposed skill IDs outside the visible default-enabled skill list.")
    elif parse_status == "mixed_selection":
        lines.append("Planner mixed skill and MCP selections in one response, so the bundle was rejected.")
    elif parse_status == "empty_selection":
        lines.append("Planner did not provide any usable skill_ids or tool_names.")
    elif parse_status == "normalized_to_none":
        lines.append("Planner output was normalized to none after validation.")
    elif parse_status == "json_ok":
        lines.append("Planner returned a valid selection payload.")
    else:
        lines.append("Planner did not produce a usable capability selection.")

    reason = _compact_text(str(plan_result.get("reason") or ""), 220)
    if reason:
        lines.append(f"Reason: {reason}")
    return " ".join(line for line in lines if line)


def planner_blocked_final_prompt(plan_result: dict[str, Any], *, user_text: str) -> str:
    lines = [
        "The internal planner did not find a usable default-enabled skill or MCP tool for this turn.",
        "Do not pretend the task was completed.",
        "Explain clearly what remains blocked and why no concrete tool call was produced.",
    ]
    if user_text:
        lines.append(f"Original user request: {user_text}")
    reason = str(plan_result.get("reason") or "").strip()
    if reason:
        lines.append(f"Planner reason: {reason}")
    observation = planner_none_observation_text(plan_result)
    if observation:
        lines.append(f"Planner observation: {observation}")
    return "\n".join(lines)


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
        "按照skill",
        "follow skill",
        "用工具",
        "use tools",
    )
    return any(marker in text for marker in action_markers)


def action_recheck_prompt(user_text: str) -> str:
    if not _needs_action_recheck(user_text):
        return ""
    return (
        "The user is asking for an action, not only an explanation. "
        "Do not answer directly unless the requested action is already complete or impossible without tools."
    )


def leader_assessment_prompt(user_text: str, expected_effect: str, loop_round: int) -> str:
    lines = [
        "You are the leader stage for the current agent-loop round.",
        f"Current loop round: {max(1, int(loop_round))}.",
        "Compare the latest tool result against the expected effect of the previous approved step.",
        "A tool returning ok=true does not automatically mean the user's request is finished.",
        "If the expected effect was not met, continue with the next useful step instead of finalizing.",
    ]
    if user_text:
        lines.append(f"Original user request: {user_text}")
    if expected_effect:
        lines.append(f"Expected effect of the previous approved step: {expected_effect}")
    return "\n".join(lines)


def planner_selected_skill_round_prompt(resolved_prompt: str) -> str:
    return f"Planner-selected skill instructions for this round:\n\n{str(resolved_prompt or '').strip()}"


def planner_selected_skill_followup_prompt(resolved_prompt: str) -> str:
    return (
        "Follow the planner-selected skill instructions below while choosing the next concrete tool step. "
        "Do not mark the task complete until that workflow is actually finished.\n\n"
        f"{str(resolved_prompt or '').strip()}"
    )


def writer_bundle_blocked_prompt() -> str:
    return (
        "The planner selected a capability bundle, but the writer still failed to turn it into a concrete tool call. "
        "Do not claim the task is complete. Explain what remains blocked."
    )


def no_progress_stop_prompt() -> str:
    return (
        "Multiple recent rounds failed to make useful progress. "
        "Do not request more tools in this turn. Explain clearly what happened and what remains blocked."
    )


def tool_step_limit_prompt() -> str:
    return (
        "You have reached the maximum approved tool steps for this turn. "
        "Do not request more tools. Provide the best possible final answer using the completed results only."
    )


def loop_round_limit_prompt() -> str:
    return (
        "You have reached the maximum agent-loop rounds for this turn. "
        "Do not request more tools. Summarize the progress and any remaining blocker clearly."
    )


def build_approved_tool_message(
    executions: list[Any],
    remaining_steps: int | None = None,
    user_request: str = "",
    expected_effect: str = "",
) -> str:
    lines = [
        "The user approved the previous tool step.",
        "The tools below have already been executed in this turn.",
        "The original user request is still active until it is fully completed.",
        "Do not switch to a final answer just because one tool call succeeded.",
        "If any requested subtask is still unfinished, plan the next tool step based on these results instead of repeating the same tool call.",
    ]
    if user_request:
        lines.append(f"Original user request: {user_request}")
    if expected_effect:
        lines.append(f"Expected effect of the previous approved step: {expected_effect}")
        lines.append("Compare the actual tool result against that expected effect before deciding the task is done.")
    if remaining_steps is not None:
        if remaining_steps > 0:
            lines.append(f"You may request up to {remaining_steps} more approved tool step(s) in this turn if necessary.")
        else:
            lines.append("Do not request more tools in this turn. Give the best possible final answer with the current results.")
    for item in executions:
        lines.append(
            json.dumps(
                {
                    "name": _field(item, "name"),
                    "arguments": _field(item, "arguments", {}) or {},
                    "ok": bool(_field(item, "ok", False)),
                    "summary": str(_field(item, "summary") or ""),
                    "payload": str(_field(item, "payload") or ""),
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


def build_rejected_tool_message(decision: Any) -> str:
    tool_calls = list(_field(decision, "tool_calls", []) or [])
    planned = [str(_field(item, "name") or "").strip() for item in tool_calls if str(_field(item, "name") or "").strip()]
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
    scope = str(inventory_result.get("inventory_scope") or "both").strip().lower() or "both"
    summary = _compact_text(str(inventory_result.get("inventory_summary") or ""), 1000)
    skill_items = [item for item in (inventory_result.get("inventory_skills") or []) if isinstance(item, dict)]
    tool_items = [item for item in (inventory_result.get("inventory_tools") or []) if isinstance(item, dict)]
    lines = [
        "The user explicitly asked what capabilities are currently available.",
        "Answer using the deterministic inventory result below.",
        "Do not continue the broader task automatically after listing capabilities.",
        "If the inventory is empty, say so clearly instead of saying you cannot confirm it.",
    ]
    if user_request:
        lines.append(f"Original user request: {user_request}")
    if summary:
        lines.append(f"Inventory summary: {summary}")
    if scope in {"skill", "both"}:
        if skill_items:
            lines.append("Planner-visible skills found:")
            for item in skill_items:
                skill_id = str(item.get("skill_id") or "").strip()
                display_name = str(item.get("display_name") or skill_id).strip()
                description = _compact_text(str(item.get("description") or ""), 160)
                line = f"- {skill_id or display_name}"
                if display_name and display_name != skill_id:
                    line += f" ({display_name})"
                if description:
                    line += f": {description}"
                lines.append(line)
        else:
            lines.append("Planner-visible skills found: none")
    if scope in {"mcp", "both"}:
        if tool_items:
            lines.append("Runtime-available non-skill MCP tools found:")
            for item in tool_items:
                tool_name = str(item.get("name") or "").strip()
                description = _compact_text(str(item.get("description") or ""), 160)
                source = str(item.get("source") or "").strip()
                line = f"- {tool_name}"
                if description:
                    line += f": {description}"
                if source:
                    line += f" | source: {source}"
                lines.append(line)
        else:
            lines.append("Runtime-available non-skill MCP tools found: none")
    return "\n".join(lines)


def build_rejected_tool_followup_message(decision: Any, user_text: str) -> str:
    tool_calls = list(_field(decision, "tool_calls", []) or [])
    planned = [str(_field(item, "name") or "").strip() for item in tool_calls if str(_field(item, "name") or "").strip()]
    joined = ", ".join(dict.fromkeys(planned))
    lines = [
        "The user did not approve the previously planned tool usage.",
        "Continue the same turn using the user's updated guidance below.",
        "Do not claim that the rejected tool call was executed.",
        "Do not repeat the same rejected tool call unless the user's latest guidance materially changes what is needed.",
        "If tools are still needed, propose the next best step for approval.",
    ]
    if joined:
        lines.append(f"Rejected tool plan: {joined}")
    followup = str(user_text or "").strip()
    if followup:
        lines.append(f"Updated user guidance: {followup}")
    return "\n".join(lines)


def _final_result_preview(item: Any) -> str:
    payload_text = str(_field(item, "payload") or "").strip()
    if not payload_text:
        return ""
    try:
        payload = json.loads(payload_text)
    except Exception:
        return _compact_text(payload_text, 240)
    if not isinstance(payload, dict):
        return _compact_text(payload_text, 240)
    if str(payload.get("result_preview") or "").strip():
        return _compact_text(str(payload.get("result_preview") or "").strip(), 240)
    if str(payload.get("error") or "").strip():
        return _compact_text(str(payload.get("error") or "").strip(), 240)
    if "result" not in payload:
        return _compact_text(payload_text, 240)
    result = payload.get("result")
    if isinstance(result, str):
        return _compact_text(result, 240)
    return _compact_text(_stable_json(result), 240)


def final_tool_results_prompt(executions: list[Any], user_text: str = "") -> str:
    lines = [
        "The following tool steps have already been executed for this turn.",
        "Use these results to produce the best possible final answer.",
        "Do not claim that additional tools were executed, and do not request more tools in this final reply.",
    ]
    if user_text:
        lines.append(f"Original user request: {user_text}")
    for item in executions:
        result_preview = _final_result_preview(item)
        lines.append(
            f"- {str(_field(item, 'name') or '').strip()} | "
            f"ok={str(bool(_field(item, 'ok', False))).lower()} | "
            f"summary={str(_field(item, 'summary') or '')}"
        )
        if result_preview:
            lines.append(f"  result={result_preview}")
    return "\n".join(lines)


def post_meta_tool_guidance_prompt(
    *,
    successful_meta: list[str],
    selected_skills: list[str],
    selected_tools: list[str],
    remaining_steps: int,
    strict: bool = False,
    system_tool_agent_loop: str = "system.agent_loop",
) -> str:
    if not successful_meta:
        return ""
    lines: list[str] = []
    if system_tool_agent_loop in successful_meta:
        lines.extend(
            [
                "Entering agent_loop only changed which tools are available. It did not complete the user's task.",
                "Do not give a final answer yet if another concrete tool step is still required.",
            ]
        )
    if selected_skills:
        lines.append(f"Current planner-selected skill bundle: {', '.join(selected_skills)}.")
    elif selected_tools:
        lines.append(f"Current planner-selected MCP bundle: {', '.join(selected_tools)}.")

    if remaining_steps > 0:
        lines.append(f"Remaining approved tool steps available: {max(0, int(remaining_steps))}.")
    if strict:
        lines.append("Return JSON only in the normal decision format.")
        lines.append("If the current bundle can plausibly advance the task, you must request the next concrete tool step now.")
    return "\n".join(lines)


def tool_reflection_prompt(ineffective_attempts: list[dict[str, Any]]) -> str:
    if not ineffective_attempts:
        return ""
    lines = [
        "Some previous tool attempts did not produce useful progress.",
        "Do not repeat an ineffective tool call in the same form.",
        "If you use the same tool again, change the parameters materially and address why the previous attempt failed, or switch to a different tool.",
        "Ineffective attempts:",
    ]
    for item in ineffective_attempts[-5:]:
        lines.append(json.dumps(item, ensure_ascii=False))
    return "\n".join(lines)


def approval_tool_summary(name: str, arguments: dict[str, Any] | None = None) -> str:
    clean_name = str(name or "").strip()
    args = arguments if isinstance(arguments, dict) else {}
    if not args:
        return clean_name
    return f"{clean_name}: {_compact_text(_stable_json(args), 100)}"
