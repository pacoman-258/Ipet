from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app
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
        return "??"

    def stop_session(self, runtime_state) -> str:
        return "????"

    def discard_session(self, runtime_state) -> None:
        runtime_state["discarded"] = True


class ASRApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(backend_app.app)
        self.original_asr_service = backend_app._ASR_SERVICE
        backend_app._ASR_SERVICE = ASRService(runtime=_FakeRuntime())

    def tearDown(self) -> None:
        self.client.close()
        backend_app._ASR_SERVICE = self.original_asr_service

    def test_asr_websocket_streams_ready_partial_and_final(self) -> None:
        settings = {"chat": {"backend_url": "http://127.0.0.1:8008", "asr": {"enabled": True, "provider": "funasr", "api_base_url": "http://127.0.0.1:8008", "push_to_talk_key": "Alt", "interim_results": True}}}
        with mock.patch.object(backend_app, "_load_settings_config", return_value=settings):
            with self.client.websocket_connect("/api/asr/stream") as websocket:
                websocket.send_json({"type": "start", "key": "Alt", "language": "zh", "punctuation": True})
                ready = websocket.receive_json()
                websocket.send_bytes(b"\x00\x00\x01\x00")
                partial = websocket.receive_json()
                websocket.send_json({"type": "stop"})
                final = websocket.receive_json()

        self.assertEqual(ready["type"], "ready")
        self.assertEqual(partial, {"type": "partial", "text": "??"})
        self.assertEqual(final, {"type": "final", "text": "????"})

    def test_asr_websocket_rejects_invalid_first_message(self) -> None:
        settings = {"chat": {"backend_url": "http://127.0.0.1:8008", "asr": {"enabled": True, "provider": "funasr", "api_base_url": "http://127.0.0.1:8008", "push_to_talk_key": "Alt", "interim_results": True}}}
        with mock.patch.object(backend_app, "_load_settings_config", return_value=settings):
            with self.client.websocket_connect("/api/asr/stream") as websocket:
                websocket.send_json({"type": "ping"})
                error = websocket.receive_json()

        self.assertEqual(error["type"], "error")
        self.assertIn("type=start", error["message"])

    def test_normalize_settings_config_includes_asr_defaults(self) -> None:
        with mock.patch.object(backend_app, "_is_macos", return_value=False):
            normalized = backend_app._normalize_settings_config({"chat": {"asr": {"push_to_talk_key": "BadKey"}}})

        self.assertTrue(normalized["chat"]["asr"]["enabled"])
        self.assertEqual(normalized["chat"]["asr"]["provider"], "funasr")
        self.assertEqual(normalized["chat"]["asr"]["api_base_url"], "http://127.0.0.1:8012")
        self.assertEqual(normalized["chat"]["asr"]["push_to_talk_key"], "Alt")
        self.assertTrue(normalized["chat"]["asr"]["interim_results"])

    def test_normalize_settings_config_disables_asr_by_default_on_macos(self) -> None:
        with mock.patch.object(backend_app, "_is_macos", return_value=True):
            normalized = backend_app._normalize_settings_config({"chat": {"asr": {"push_to_talk_key": "BadKey"}}})

        self.assertFalse(normalized["chat"]["asr"]["enabled"])
        self.assertEqual(normalized["chat"]["asr"]["push_to_talk_key"], "Alt")

    def test_asr_warmup_endpoint_starts_background_loading(self) -> None:
        settings = {"chat": {"backend_url": "http://127.0.0.1:8009", "asr": {"enabled": True, "provider": "funasr", "api_base_url": "http://127.0.0.1:8009", "push_to_talk_key": "Ctrl", "interim_results": True}}}
        fake_service = mock.Mock()
        fake_service.readiness_message.return_value = "ASR 正在加载模型，请稍后再试。"
        with mock.patch.object(backend_app, "_load_settings_config", return_value=settings), mock.patch.object(
            backend_app,
            "_ensure_internal_asr_warmup_started",
            return_value=(True, "ASR 正在加载模型，请稍后再试。"),
        ), mock.patch.object(backend_app, "_get_asr_service", return_value=fake_service):
            resp = self.client.post("/api/asr/warmup")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "ok": True,
                "started": True,
                "ready": False,
                "message": "ASR 正在加载模型，请稍后再试。",
            },
        )


    def test_asr_warmup_reports_macos_disabled_message(self) -> None:
        with mock.patch.object(backend_app, "_is_macos", return_value=True), mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={},
        ):
            resp = self.client.post("/api/asr/warmup")

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["ok"])
        self.assertIn("macOS", resp.json()["message"])


if __name__ == "__main__":
    unittest.main()
