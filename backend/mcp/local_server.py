from __future__ import annotations

import json
import sys
from typing import Any

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

    def list_tools(self) -> list[dict[str, Any]]:
        return list(self.TOOL_SPECS)

    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
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
                out = {"ok": True, "result": server.call(name, args)}
            else:
                out = {"ok": False, "error": "unsupported cmd"}
        except Exception as exc:
            out = {"ok": False, "error": str(exc)}
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_stdio())
