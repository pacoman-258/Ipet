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
        fake_manifest = {
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
            return_value=[fake_manifest],
        ), mock.patch(
            "backend.app.ThirdPartyMCPManager.load_manifest",
            return_value=fake_manifest,
        ), mock.patch.object(
            backend_app,
            "_get_mcp_bridge",
            side_effect=AssertionError("servers endpoint should stay lightweight"),
        ):
            resp = self.client.get("/api/mcp/servers")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["servers"]), 1)
        self.assertEqual(data["servers"][0]["name"], "playwright_mcp")
        self.assertEqual(data["servers"][0]["health_status"], "not_loaded")


if __name__ == "__main__":
    unittest.main()
