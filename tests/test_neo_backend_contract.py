from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app


def _sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name = "message"
    data_lines: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            if data_lines:
                events.append((event_name, json.loads("\n".join(data_lines))))
            event_name = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
    if data_lines:
        events.append((event_name, json.loads("\n".join(data_lines))))
    return events


class NeoBackendContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_dir.name)
        self.config_patch = mock.patch.object(backend_app, "CONFIG_PATH", temp_root / "pet_config.json")
        self.config_patch.start()
        self.topic_patch = None
        if hasattr(backend_app, "TOPIC_STORE"):
            self.topic_patch = mock.patch.object(backend_app, "TOPIC_STORE", backend_app.TopicStore(temp_root / "chat_topics"))
            self.topic_patch.start()
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()
        if self.topic_patch is not None:
            self.topic_patch.stop()
        self.config_patch.stop()
        self.temp_dir.cleanup()

    def test_health_reports_neo_backend_without_runtime_surfaces(self) -> None:
        resp = self.client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "ipet-neo-aspect-backend")
        self.assertIn("neo_aspect", payload)
        self.assertNotIn("third_party_mcp", payload)
        self.assertNotIn("runtime", payload)

    def test_settings_config_returns_neo_defaults_and_redacts_brain_secret(self) -> None:
        resp = self.client.get("/api/settings/config")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("config", payload)
        self.assertIn("defaults", payload)
        self.assertNotIn("mcp_server_presets", payload)

        config = payload["config"]
        self.assertEqual(
            set(config),
            {"model_path", "chat", "pet", "window", "brain", "human_ops", "memory", "skills", "diagnostics"},
        )
        self.assertNotIn("runtime", config)
        self.assertNotIn("hermes", config)
        self.assertNotIn("mcp", config)
        self.assertNotIn("api_key", config["brain"])
        self.assertEqual(config["brain"]["api_key_preview"], "")

    def test_settings_put_persists_public_neo_shape_without_api_key_echo(self) -> None:
        resp = self.client.put(
            "/api/settings/config",
            json={"config": {"brain": {"model_name": "neo-test", "api_key": "secret-value"}}},
        )

        self.assertEqual(resp.status_code, 200)
        config = resp.json()["config"]
        self.assertEqual(config["brain"]["model_name"], "neo-test")
        self.assertNotIn("api_key", config["brain"])
        self.assertEqual(config["brain"]["api_key_preview"], "se***ue")

    def test_settings_config_recovers_from_malformed_sections(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps({"brain": "legacy-string", "human_ops": ["bad"], "chat": {"asr": "bad"}}),
            encoding="utf-8",
        )

        resp = self.client.get("/api/settings/config")

        self.assertEqual(resp.status_code, 200)
        config = resp.json()["config"]
        self.assertIsInstance(config["brain"], dict)
        self.assertEqual(config["brain"]["model_name"], backend_app.NEO_DEFAULTS["brain"]["model_name"])
        self.assertIsInstance(config["human_ops"], dict)
        self.assertIsInstance(config["chat"]["asr"], dict)

    def test_chat_stream_returns_frontend_compatible_placeholder_sse(self) -> None:
        with self.client.stream("POST", "/api/chat/stream", json={"text": "你好", "session_id": "neo-contract"}) as resp:
            self.assertEqual(resp.status_code, 200)
            body = resp.read().decode("utf-8")

        events = _sse_events(body)
        event_names = [name for name, _ in events]
        self.assertIn("meta", event_names)
        self.assertIn("token", event_names)
        self.assertIn("done", event_names)
        self.assertNotIn("error", event_names)
        token_text = "".join(data.get("text", "") for name, data in events if name == "token")
        self.assertIn("Neo Brain", token_text)

    def test_skills_list_is_empty_compatibility_shell(self) -> None:
        resp = self.client.get("/api/skills")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["skills"], [])
        self.assertEqual(payload["recipes"], [])

    def test_tts_returns_existing_frontend_audio_url_contract(self) -> None:
        audio_path = Path("neo-test.mp3")
        result = SimpleNamespace(
            file_id="neo-test",
            path=audio_path,
            duration_ms=123,
            media_type="audio/mpeg",
        )

        with mock.patch.object(backend_app, "tts_available", return_value=True), mock.patch.object(
            backend_app, "cleanup_old_audio"
        ), mock.patch.object(backend_app, "synthesize_to_audio", new=mock.AsyncMock(return_value=result)):
            resp = self.client.post("/api/tts", json={"text": "hello"})

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["url"], "/api/audio/neo-test.mp3")
        self.assertEqual(payload["audio_url"], "/api/audio/neo-test.mp3")
        self.assertEqual(payload["duration_ms"], 123)

    def test_legacy_runtime_management_routes_are_gone(self) -> None:
        routes = [
            ("GET", "/api/runtime/status"),
            ("GET", "/api/hermes/status"),
            ("GET", "/api/mcp/servers"),
            ("GET", "/api/mcp/health"),
            ("POST", "/api/mcp/create-config"),
            ("POST", "/api/mcp/delete"),
            ("POST", "/api/mcp/toggle"),
            ("POST", "/api/mcp/reload"),
            ("POST", "/api/skills/import-local"),
            ("POST", "/api/skills/import-git"),
            ("POST", "/api/skills/delete"),
            ("POST", "/api/models"),
            ("POST", "/api/chat/approval"),
            ("POST", "/api/chat/memory/decision"),
        ]

        for method, path in routes:
            with self.subTest(path=path):
                resp = self.client.request(method, path, json={})
                self.assertEqual(resp.status_code, 410)
                self.assertIn("removed", resp.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
