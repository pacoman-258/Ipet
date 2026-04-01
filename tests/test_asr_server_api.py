from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.asr_server as asr_server
from backend.asr import ASRService


class _FakeRuntime:
    def available(self) -> bool:
        return True

    def availability_message(self) -> str:
        return ""

    def start_session(self, *, language: str, punctuation: bool):
        return {"language": language, "punctuation": punctuation, "chunks": []}

    def push_audio(self, runtime_state, pcm16_chunk: bytes) -> str:
        runtime_state["chunks"].append(bytes(pcm16_chunk))
        return "partial-ok"

    def stop_session(self, runtime_state) -> str:
        return "final-ok"

    def discard_session(self, runtime_state) -> None:
        runtime_state["discarded"] = True


class ASRServerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(asr_server.app)
        self.original_asr_service = asr_server._ASR_SERVICE
        asr_server._ASR_SERVICE = ASRService(runtime=_FakeRuntime())

    def tearDown(self) -> None:
        self.client.close()
        asr_server._ASR_SERVICE = self.original_asr_service

    def test_health_reports_asr_available(self) -> None:
        settings = {"chat": {"asr": {"enabled": True, "provider": "funasr", "push_to_talk_key": "Alt", "interim_results": True}}}
        with mock.patch.object(asr_server, "_load_settings_config", return_value=settings):
            resp = self.client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["asr"], True)
        self.assertEqual(resp.json()["message"], "")

    def test_health_reports_macos_disabled_message_by_default(self) -> None:
        with mock.patch.object(asr_server, "_is_macos", return_value=True), mock.patch.object(
            asr_server,
            "_load_settings_config",
            return_value={},
        ):
            resp = self.client.get("/api/health")

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["asr"])
        self.assertIn("macOS", resp.json()["message"])

    def test_asr_server_websocket_streams_ready_partial_and_final(self) -> None:
        settings = {"chat": {"asr": {"enabled": True, "provider": "funasr", "push_to_talk_key": "Alt", "interim_results": True}}}
        with mock.patch.object(asr_server, "_load_settings_config", return_value=settings):
            with self.client.websocket_connect("/api/asr/stream") as websocket:
                websocket.send_json({"type": "start", "key": "Alt", "language": "zh", "punctuation": True})
                ready = websocket.receive_json()
                websocket.send_bytes(b"\x00\x00")
                partial = websocket.receive_json()
                websocket.send_json({"type": "stop"})
                final = websocket.receive_json()

        self.assertEqual(ready["type"], "ready")
        self.assertEqual(partial, {"type": "partial", "text": "partial-ok"})
        self.assertEqual(final, {"type": "final", "text": "final-ok"})


if __name__ == "__main__":
    unittest.main()
