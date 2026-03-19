from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from backend.mcp_bridge import MCPBridge


class _FakeClient:
    def __init__(self, command, cwd, timeout_sec=180, env=None, protocol="content_length") -> None:
        self.command = command
        self.cwd = cwd
        self.timeout_sec = timeout_sec
        self.env = env
        self.protocol = protocol

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def is_healthy(self) -> bool:
        return True

    def list_tools(self):
        return [
            {
                "name": "echo",
                "description": "Echo from MCP",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
            }
        ]

    def call_tool(self, name, arguments):
        return {"tool": name, "arguments": arguments}


class MCPBridgeToolTests(unittest.TestCase):
    def test_bridge_registers_cached_tools_without_eager_start(self) -> None:
        fake_manifest = {
            "name": "playwright_mcp",
            "version": "0.1.0",
            "enabled": True,
            "runtime": "node",
            "transport": "stdio",
            "server_dir": str(Path.cwd()),
            "workdir_path": str(Path.cwd()),
            "manifest_path": str(Path.cwd() / "manifest.json"),
            "entry": {"command": "npx", "args": ["@playwright/mcp@latest"]},
            "install": {"type": "none"},
        }
        fake_server_cfg = {
            "name": "playwright_mcp",
            "enabled": True,
            "manifest_path": str(Path.cwd() / "manifest.json"),
            "source_type": "git",
            "source": "https://github.com/microsoft/playwright-mcp",
        }

        with mock.patch("backend.mcp_bridge.StdioMCPClient", _FakeClient), mock.patch.object(
            MCPBridge, "_iter_registered_servers", return_value=[fake_server_cfg]
        ), mock.patch("backend.mcp_bridge.ThirdPartyMCPManager.load_manifest", return_value=fake_manifest), mock.patch(
            "backend.mcp_bridge.ThirdPartyMCPManager.load_tool_cache",
            return_value=[
                {
                    "name": "echo",
                    "description": "Echo from MCP",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                    },
                }
            ],
        ), mock.patch(
            "backend.mcp_bridge.ThirdPartyMCPManager.ensure_installed",
            return_value={"install_status": "ready", "runtime": "node"},
        ), mock.patch(
            "backend.mcp_bridge.ThirdPartyMCPManager.build_command",
            return_value=["npx", "@playwright/mcp@latest"],
        ) as build_command_mock, mock.patch(
            "backend.mcp_bridge.ThirdPartyMCPManager.save_tool_cache",
        ):
            bridge = MCPBridge(
                {
                    "enabled": True,
                    "file_allowlist": [str(Path.cwd())],
                    "tool_timeout_sec": 30,
                    "third_party": {"enabled": True, "servers": [fake_server_cfg]},
                },
                Path.cwd(),
            )
            names = [tool.name for tool in bridge.list_registered_tools()]
            self.assertIn("read_file", names)
            self.assertIn("playwright_mcp.echo", names)

            schemas = bridge.list_tools()
            schema_names = [item["function"]["name"] for item in schemas]
            self.assertIn("playwright_mcp.echo", schema_names)
            build_command_mock.assert_not_called()

            result = bridge.call_tool("playwright_mcp.echo", {"text": "hello"})
            self.assertTrue(result.ok)
            self.assertEqual(
                result.structured_data,
                {"tool": "echo", "arguments": {"text": "hello"}},
            )
            build_command_mock.assert_called_once()
            bridge.stop()


if __name__ == "__main__":
    unittest.main()
