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
        fake_client = mock.AsyncMock()
        fake_client.request_json.return_value = {
            "runtime": "hermes",
            "storage": {"runtime": "Hermes", "proxied": True},
            "servers": [{"name": "playwright_mcp", "health_status": "ready"}],
        }
        with mock.patch.object(
            backend_app, "_get_hermes_client", return_value=fake_client
        ), mock.patch.object(
            backend_app,
            "_get_mcp_bridge",
            side_effect=AssertionError("servers endpoint should stay lightweight"),
        ):
            resp = self.client.get("/api/mcp/servers")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["runtime"], "hermes")
        self.assertEqual(data["storage"]["runtime"], "Hermes")
        self.assertEqual(data["servers"][0]["name"], "playwright_mcp")
        fake_client.request_json.assert_awaited_once_with("GET", "/api/mcp/servers")

    def test_mcp_health_proxies_hermes_status(self) -> None:
        fake_client = mock.AsyncMock()
        fake_client.request_json.return_value = {
            "runtime": "hermes",
            "enabled": True,
            "servers": [
                {
                    "name": "playwright_mcp",
                    "enabled": True,
                    "health_status": "ready",
                }
            ],
            "online": 1,
        }
        with mock.patch.object(backend_app, "_get_hermes_client", return_value=fake_client):
            resp = self.client.get("/api/mcp/health")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["runtime"], "hermes")
        self.assertEqual(data["servers"][0]["name"], "playwright_mcp")
        self.assertEqual(data["online"], 1)
        fake_client.request_json.assert_awaited_once_with("GET", "/api/mcp/health")


if __name__ == "__main__":
    unittest.main()
