from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app


class HealthEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_health_does_not_initialize_mcp_bridge_when_absent(self) -> None:
        backend_app._MCP_BRIDGE = None
        with mock.patch.object(backend_app, "is_ollama_alive", return_value=True), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "third_party": {"enabled": True}},
        ), mock.patch.object(backend_app, "_get_mcp_bridge", side_effect=AssertionError("should not initialize bridge")):
            resp = self.client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["third_party_mcp"]["servers"], [])
        self.assertEqual(data["third_party_mcp"]["online"], 0)


if __name__ == "__main__":
    unittest.main()
