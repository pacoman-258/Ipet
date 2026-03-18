from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from .mcp_bridge import MCPBridge
from .ollama_client import chat_once, stream_chat
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "needs_tool": self.needs_tool,
            "thought_summary": self.thought_summary,
            "action_message": self.action_message,
            "tool_calls": [item.to_dict() for item in self.tool_calls],
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
    lines = [
        "You are the tool-decision stage for a desktop pet assistant.",
        "Decide whether the user's request requires tools right now.",
        "Do not roleplay. Do not answer the user's question. Only return one JSON object.",
        "Do not reveal hidden reasoning. thought_summary must be short and user-facing.",
        "If tools are needed, action_message should describe the planned work in a friendly, concise way for user approval.",
        "When tools are not needed, set needs_tool to false and use an empty array for tool_calls.",
        "The user's full request must be completed before you stop asking for tools.",
        "Do not treat one successful tool call as task completion if the request clearly contains multiple unfinished steps.",
        "For browser automation or multi-step tasks, keep needs_tool=true until the requested sequence is actually completed or cannot be continued usefully.",
        "If part of the request is still unfinished, return only the next concrete tool step instead of jumping to a final answer.",
        "Output JSON only with these keys:",
        '{"needs_tool":true,"thought_summary":"brief summary","action_message":"what you plan to do","tool_calls":[{"name":"read_file","arguments":{"path":"story.txt"}}]}',
        "Available tools:",
    ]
    for tool in tools:
        fn = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = str(fn.get("name") or "").strip()
        description = str(fn.get("description") or "").strip()
        if name:
            lines.append(f"- {name}: {description}")
    return "\n".join(lines)


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
    return TurnDecision(
        needs_tool=needs_tool,
        thought_summary=thought_summary,
        action_message=action_message,
        tool_calls=tool_calls,
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
    decision_messages = [{"role": "system", "content": _tool_catalog_prompt(tools)}] + list(messages)
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
    return _decision_from_assistant_message(assistant_msg, allowed_names)


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
    if remaining_steps is not None:
        if remaining_steps > 0:
            lines.append(f"You may request up to {remaining_steps} more approved tool step(s) in this turn if necessary.")
        else:
            lines.append("Do not request more tools in this turn. Give the best possible final answer with the current results.")
    for item in executions:
        lines.append(
            json.dumps(
                {
                    "name": item.name,
                    "arguments": item.arguments,
                    "ok": item.ok,
                    "summary": item.summary,
                    "payload": item.payload,
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


def build_rejected_tool_message(decision: TurnDecision) -> str:
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
