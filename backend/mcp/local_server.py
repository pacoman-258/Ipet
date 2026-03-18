from __future__ import annotations

import json
import sys
from typing import Any

from backend.tool_runtime import Tool, ToolRegistry, ToolResult
from backend.tooling import FileTools, SecurityPolicy


class LocalMCPServer:
    """
    Phase-1 local MCP-like tools provider.
    It can be called in-process by bridge, and also run as a simple stdio JSON loop.
    """

    TOOL_SPECS: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "create_file",
                "description": "Create a new text file under allowed paths. By default it fails if the file already exists, set overwrite=true to replace it.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "encoding": {"type": "string"},
                        "create_dirs": {"type": "boolean"},
                        "overwrite": {"type": "boolean"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file from allowed paths.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "encoding": {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write text content into a file under allowed paths. Default is append, set append=false to overwrite.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "encoding": {"type": "string"},
                        "create_dirs": {"type": "boolean"},
                        "append": {"type": "boolean"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "move_file",
                "description": "Move/rename a file inside allowed paths.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "src_path": {"type": "string"},
                        "dst_path": {"type": "string"},
                    },
                    "required": ["src_path", "dst_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "List files/directories from an allowed directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "recursive": {"type": "boolean"},
                        "max_entries": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
        },
    ]

    def __init__(self, file_allowlist: list[str], network_allow_domains: list[str] | None = None) -> None:
        policy = SecurityPolicy(file_allowlist=file_allowlist, network_allow_domains=network_allow_domains or [])
        self.tools = FileTools(policy)
        self.registry = ToolRegistry()
        self._register_builtin_tools()

    def _register_builtin_tools(self) -> None:
        for tool_spec in self.TOOL_SPECS:
            fn = tool_spec.get("function", {}) if isinstance(tool_spec, dict) else {}
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            self.registry.register(
                Tool(
                    name=name,
                    description=str(fn.get("description") or "").strip(),
                    input_schema=fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {"type": "object", "properties": {}},
                    invoke=lambda arguments, tool_name=name: self._call_builtin(tool_name, arguments),
                    source="local",
                )
            )

    def _call_builtin(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        arguments = arguments if isinstance(arguments, dict) else {}
        if tool_name == "create_file":
            return self.tools.create_file(**arguments)
        if tool_name == "read_file":
            return self.tools.read_file(**arguments)
        if tool_name == "write_file":
            return self.tools.write_file(**arguments)
        if tool_name == "move_file":
            return self.tools.move_file(**arguments)
        if tool_name == "list_dir":
            return self.tools.list_dir(**arguments)
        raise ValueError(f"unknown tool: {tool_name}")

    def get_tools(self) -> list[Tool]:
        return self.registry.list()

    def list_tools(self) -> list[dict[str, Any]]:
        return self.registry.to_llm_schemas()

    def call(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        return self.registry.invoke(tool_name, arguments)


def _run_stdio() -> int:
    allowlist = [str((__import__("pathlib").Path(__file__).resolve().parents[2]))]
    server = LocalMCPServer(file_allowlist=allowlist)
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            cmd = req.get("cmd")
            if cmd == "list_tools":
                out = {"ok": True, "tools": server.list_tools()}
            elif cmd == "call":
                name = str(req.get("tool_name") or "")
                args = req.get("arguments") or {}
                result = server.call(name, args)
                out = {
                    "ok": result.ok,
                    "result": result.structured_data,
                    "content": result.content,
                    "error": result.error,
                }
            else:
                out = {"ok": False, "error": "unsupported cmd"}
        except Exception as exc:
            out = {"ok": False, "error": str(exc)}
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_stdio())
