from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from .mcp_bridge import MCPBridge
from .ollama_client import chat_once, stream_chat


def _chunks(text: str, step: int = 48) -> list[str]:
    if not text:
        return []
    n = max(1, int(step))
    return [text[i : i + n] for i in range(0, len(text), n)]


def _normalize_tool_calls(msg: dict[str, Any]) -> list[dict[str, Any]]:
    tc = msg.get("tool_calls", [])
    if isinstance(tc, list):
        return [x for x in tc if isinstance(x, dict)]
    fc = msg.get("function_call")
    if isinstance(fc, dict):
        return [
            {
                "id": "function_call_0",
                "type": "function",
                "function": {
                    "name": str(fc.get("name") or "").strip(),
                    "arguments": fc.get("arguments", {}),
                },
            }
        ]
    return []


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _tool_catalog_prompt(tools: list[dict[str, Any]]) -> str:
    lines = [
        "Tool use is available in this chat.",
        "Do not say you cannot access files or tools.",
        "If a task requires file operations, you must use the available tools instead of refusing.",
        "Prefer native function calling when the model/runtime supports it.",
        "If native function calling is unavailable, output exactly one JSON object and nothing else:",
        '{"tool_calls":[{"name":"read_file","arguments":{"path":"relative/or/absolute/path"}}]}',
        "After tool results are returned, continue the task and produce the final answer normally.",
        "Available tools:",
    ]
    for item in tools:
        fn = item.get("function", {}) if isinstance(item, dict) else {}
        name = str(fn.get("name") or "").strip()
        desc = str(fn.get("description") or "").strip()
        if name:
            lines.append(f"- {name}: {desc}")
    return "\n".join(lines)


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
    return list(dict.fromkeys([c for c in candidates if c]))


def _extract_fallback_tool_calls(content: str) -> list[dict[str, Any]]:
    for candidate in _extract_json_candidates(content):
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if isinstance(data, dict):
            tool_calls = data.get("tool_calls")
            if isinstance(tool_calls, list):
                normalized: list[dict[str, Any]] = []
                for idx, item in enumerate(tool_calls):
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    args = item.get("arguments", {})
                    if not name:
                        continue
                    normalized.append(
                        {
                            "id": f"fallback_call_{idx}",
                            "type": "function",
                            "function": {"name": name, "arguments": args},
                        }
                    )
                if normalized:
                    return normalized
            name = str(data.get("name") or "").strip()
            if name:
                return [
                    {
                        "id": "fallback_call_0",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": data.get("arguments", {}),
                        },
                    }
                ]
    return []


async def stream_reply(
    *,
    messages: list[dict[str, Any]],
    model: str,
    tools_enabled: bool,
    mcp_bridge: MCPBridge | None,
    max_tool_calls: int = 6,
    llm_provider: str = "ollama",
    api_base_url: str = "",
    api_key: str = "",
) -> AsyncGenerator[str, None]:
    if not tools_enabled or mcp_bridge is None:
        async for delta in stream_chat(
            messages=messages,
            model=model,
            provider=llm_provider,
            base_url=api_base_url,
            api_key=api_key,
        ):
            yield delta
        return

    work_msgs = list(messages)
    tools = mcp_bridge.list_tools()
    work_msgs = [{"role": "system", "content": _tool_catalog_prompt(tools)}] + work_msgs
    call_count = 0

    while True:
        assistant_msg = await chat_once(
            messages=work_msgs,
            model=model,
            tools=tools,
            provider=llm_provider,
            base_url=api_base_url,
            api_key=api_key,
        )
        content = str(assistant_msg.get("content") or "")
        tool_calls = _normalize_tool_calls(assistant_msg)
        fallback_mode = False
        if not tool_calls:
            tool_calls = _extract_fallback_tool_calls(content)
            fallback_mode = bool(tool_calls)

        if not tool_calls:
            for c in _chunks(content):
                yield c
            return

        assistant_record: dict[str, Any] = {"role": "assistant", "content": content}
        if not fallback_mode:
            assistant_record["tool_calls"] = tool_calls
        work_msgs.append(assistant_record)

        for call in tool_calls:
            if call_count >= max_tool_calls:
                yield "Tool call limit reached for this turn."
                return
            fn = call.get("function", {}) if isinstance(call.get("function"), dict) else {}
            name = str(fn.get("name") or "").strip()
            args = _parse_args(fn.get("arguments"))
            call_id = str(call.get("id") or f"call_{call_count}")
            call_count += 1

            try:
                result = mcp_bridge.call_tool(name, args)
                result_payload = json.dumps({"ok": True, "result": result}, ensure_ascii=False)
            except Exception as exc:
                result_payload = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

            if fallback_mode:
                work_msgs.append(
                    {
                        "role": "system",
                        "content": f"Tool result for {name}: {result_payload}",
                    }
                )
            else:
                work_msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": result_payload,
                    }
                )
