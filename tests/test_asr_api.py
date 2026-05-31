from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

import backend.app as backend_app


class ASRApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_asr_warmup_reports_disabled_minimal_backend(self) -> None:
        resp = self.client.post("/api/asr/warmup")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["available"])
        self.assertIn("disabled", payload["detail"].lower())

    def test_asr_websocket_returns_disabled_error_and_closes(self) -> None:
        with self.client.websocket_connect("/api/asr/stream") as websocket:
            payload = websocket.receive_json()

        self.assertEqual(payload["type"], "error")
        self.assertIn("disabled", payload["detail"].lower())


if __name__ == "__main__":
    unittest.main()
