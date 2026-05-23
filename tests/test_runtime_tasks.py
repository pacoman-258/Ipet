from __future__ import annotations

import unittest

from backend.runtime_tasks import cleanup_runtime_session, temporary_runtime_session_id


class RuntimeTemporarySessionTests(unittest.IsolatedAsyncioTestCase):
    def test_temporary_runtime_session_id_prefixes_normalized_turn_id(self) -> None:
        self.assertEqual(temporary_runtime_session_id("abc"), "ipet-temp-abc")

    def test_temporary_runtime_session_id_replaces_path_separators(self) -> None:
        self.assertEqual(temporary_runtime_session_id("bad/session id"), "ipet-temp-bad-session-id")

    async def test_cleanup_runtime_session_deletes_chat_topic(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls = []

            async def request_json(self, method, path, *, json_payload=None):
                self.calls.append((method, path, json_payload))
                return {"ok": True}

        client = FakeClient()

        payload = await cleanup_runtime_session(client, "ipet-temp-abc")

        self.assertEqual(payload, {"ok": True, "payload": {"ok": True}, "error": ""})
        self.assertEqual(client.calls, [("DELETE", "/api/chat/topics/ipet-temp-abc", None)])

    async def test_cleanup_runtime_session_skips_empty_session_id(self) -> None:
        class FakeClient:
            async def request_json(self, method, path, *, json_payload=None):
                raise AssertionError("empty session cleanup should not call runtime")

        payload = await cleanup_runtime_session(FakeClient(), "")

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["skipped"])
        self.assertEqual(payload["payload"], {})

    async def test_cleanup_runtime_session_prefers_explicit_delete_method(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.delete_calls = []

            async def delete_runtime_session(self, session_id):
                self.delete_calls.append(session_id)
                return {"ok": True, "deleted_topic_id": session_id}

            async def request_json(self, method, path, *, json_payload=None):
                raise AssertionError("fallback request_json should not be called")

        client = FakeClient()

        payload = await cleanup_runtime_session(client, "qq:FriendMessage:123")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["error"], "")
        self.assertEqual(payload["payload"]["deleted_topic_id"], "qq:FriendMessage:123")
        self.assertEqual(client.delete_calls, ["qq:FriendMessage:123"])

    async def test_cleanup_runtime_session_quotes_fallback_path(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls = []

            async def request_json(self, method, path, *, json_payload=None):
                self.calls.append((method, path, json_payload))
                return {"ok": True}

        client = FakeClient()

        await cleanup_runtime_session(client, "qq:FriendMessage:123/with space")

        self.assertEqual(
            client.calls,
            [("DELETE", "/api/chat/topics/qq%3AFriendMessage%3A123%2Fwith%20space", None)],
        )

    async def test_cleanup_runtime_session_returns_error_payload_when_delete_fails(self) -> None:
        class FakeClient:
            async def request_json(self, method, path, *, json_payload=None):
                raise RuntimeError("delete failed")

        payload = await cleanup_runtime_session(FakeClient(), "ipet-temp-abc")

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["payload"], {})
        self.assertIn("delete failed", payload["error"])
