from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app


class MCPServersListingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()
        backend_app._MCP_BRIDGE = None

    def test_servers_endpoint_does_not_initialize_bridge(self) -> None:
        backend_app._MCP_BRIDGE = None
        fake_basic_memory_manifest = {
            "name": "basic_memory",
            "enabled": True,
            "runtime": "python",
            "version": "0.1.0",
            "manifest_path": "D:/tmp/basic_memory/manifest.json",
            "server_dir": "D:/tmp/basic_memory",
            "install": {"type": "none"},
        }
        fake_playwright_manifest = {
            "name": "playwright_mcp",
            "enabled": True,
            "runtime": "node",
            "version": "0.1.0",
            "manifest_path": "D:/tmp/playwright_mcp/manifest.json",
            "server_dir": "D:/tmp/playwright_mcp",
            "install": {"type": "none"},
        }
        with mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"third_party": {"enabled": True, "servers": []}},
        ), mock.patch(
            "backend.app.ThirdPartyMCPManager.list_manifests",
            return_value=[fake_basic_memory_manifest, fake_playwright_manifest],
        ), mock.patch(
            "backend.app.ThirdPartyMCPManager.load_manifest",
            side_effect=[fake_basic_memory_manifest, fake_playwright_manifest],
        ), mock.patch.object(
            backend_app,
            "_get_mcp_bridge",
            side_effect=AssertionError("servers endpoint should stay lightweight"),
        ):
            resp = self.client.get("/api/mcp/servers")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["servers"]), 3)
        self.assertEqual(data["servers"][0]["name"], "builtin_file_tools")
        self.assertEqual(data["servers"][0]["health_status"], "online")
        tool_names = [item["function"]["name"] for item in data["servers"][0]["tools"]]
        self.assertIn("read_file", tool_names)
        self.assertIn("list_dir", tool_names)
        self.assertEqual(data["servers"][1]["name"], "basic_memory")
        self.assertEqual(data["servers"][1]["health_status"], "not_loaded")
        self.assertEqual(data["servers"][2]["name"], "playwright_mcp")
        self.assertEqual(data["servers"][2]["health_status"], "not_loaded")

    def test_mcp_health_includes_builtin_local_tools_alongside_third_party_status(self) -> None:
        fake_bridge = mock.Mock()
        fake_bridge.health.return_value = {
            "enabled": True,
            "servers": [
                {
                    "name": "playwright_mcp",
                    "enabled": True,
                    "runtime": "node",
                    "source_type": "config",
                    "install_status": "ready",
                    "health_status": "ready",
                    "tools": [],
                    "error": "",
                }
            ],
            "online": 0,
        }
        with mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "file_allowlist": ["/repo"], "third_party": {"enabled": True, "servers": []}},
        ), mock.patch.object(
            backend_app,
            "_get_mcp_bridge_for_tooling",
            return_value=fake_bridge,
        ):
            resp = self.client.get("/api/mcp/health")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["builtin_enabled"])
        self.assertEqual(data["servers"][0]["name"], "builtin_file_tools")
        self.assertEqual(data["servers"][0]["allowlist"], ["/repo"])
        self.assertEqual(data["servers"][1]["name"], "playwright_mcp")
        builtin_tool_names = [item["function"]["name"] for item in data["servers"][0]["tools"]]
        self.assertIn("read_file", builtin_tool_names)
        self.assertEqual(data["online"], 1)


if __name__ == "__main__":
    unittest.main()
