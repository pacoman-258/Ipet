from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


ToolInvoker = Callable[[dict[str, Any]], Any]


def _safe_json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


@dataclass
class ToolResult:
    ok: bool
    content: str = ""
    structured_data: Any = None
    error: str = ""
    raw: Any = None

    @classmethod
    def from_value(cls, value: Any) -> "ToolResult":
        if isinstance(value, cls):
            return value
        return cls(
            ok=True,
            content=_safe_json_text(value),
            structured_data=value,
            raw=value,
        )

    @classmethod
    def from_error(cls, error: str, raw: Any = None) -> "ToolResult":
        return cls(ok=False, content="", structured_data=None, error=str(error or "tool execution failed"), raw=raw)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    invoke: ToolInvoker
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_llm_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema if isinstance(self.input_schema, dict) else {"type": "object", "properties": {}},
            },
        }

    def call(self, arguments: dict[str, Any] | None = None) -> ToolResult:
        try:
            result = self.invoke(arguments or {})
        except Exception as exc:
            return ToolResult.from_error(str(exc), raw=exc)
        return ToolResult.from_value(result)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, overwrite: bool = False) -> Tool:
        name = str(tool.name or "").strip()
        if not name:
            raise ValueError("tool name cannot be empty")
        if not overwrite and name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = tool
        return tool

    def get(self, name: str) -> Tool:
        target = str(name or "").strip()
        if target not in self._tools:
            raise KeyError(target)
        return self._tools[target]

    def list(self) -> list[Tool]:
        return [self._tools[name] for name in sorted(self._tools)]

    def to_llm_schemas(self) -> list[dict[str, Any]]:
        return [tool.to_llm_schema() for tool in self.list()]

    def invoke(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        return self.get(name).call(arguments or {})

