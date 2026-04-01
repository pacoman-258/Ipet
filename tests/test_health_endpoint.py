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
        ), mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={"chat": {"backend_url": "http://127.0.0.1:8008", "asr": {"enabled": True, "provider": "funasr", "api_base_url": "http://127.0.0.1:8012", "push_to_talk_key": "Alt", "interim_results": True}}},
        ), mock.patch.object(backend_app, "_external_asr_health", new=mock.AsyncMock(return_value=True)), mock.patch.object(backend_app, "_get_mcp_bridge", side_effect=AssertionError("should not initialize bridge")):
            resp = self.client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("asr", data)
        self.assertEqual(data["third_party_mcp"]["servers"], [])
        self.assertEqual(data["third_party_mcp"]["online"], 0)

    def test_health_falls_back_to_internal_asr_when_external_unavailable(self) -> None:
        backend_app._MCP_BRIDGE = None
        fake_service = mock.Mock()
        fake_service.readiness_message.return_value = ""
        with mock.patch.object(backend_app, "is_ollama_alive", return_value=True), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "third_party": {"enabled": True}},
        ), mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={"chat": {"backend_url": "http://127.0.0.1:8009", "asr": {"enabled": True, "provider": "funasr", "api_base_url": "http://127.0.0.1:8012", "push_to_talk_key": "Ctrl", "interim_results": True}}},
        ), mock.patch.object(backend_app, "_external_asr_health", new=mock.AsyncMock(return_value=False)), mock.patch.object(
            backend_app,
            "_get_asr_service",
            return_value=fake_service,
        ):
            resp = self.client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["asr"])
        self.assertEqual(resp.json()["message"], "")
        fake_service.readiness_message.assert_called_once()

    def test_health_reports_internal_asr_message_when_not_ready(self) -> None:
        backend_app._MCP_BRIDGE = None
        fake_service = mock.Mock()
        fake_service.readiness_message.return_value = "ASR 正在加载模型，请稍后再试。"
        with mock.patch.object(backend_app, "is_ollama_alive", return_value=True), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "third_party": {"enabled": True}},
        ), mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={"chat": {"backend_url": "http://127.0.0.1:8009", "asr": {"enabled": True, "provider": "funasr", "api_base_url": "http://127.0.0.1:8009", "push_to_talk_key": "Ctrl", "interim_results": True}}},
        ), mock.patch.object(
            backend_app,
            "_get_asr_service",
            return_value=fake_service,
        ):
            resp = self.client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["asr"])
        self.assertEqual(resp.json()["message"], "ASR 正在加载模型，请稍后再试。")


if __name__ == "__main__":
    unittest.main()
