from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend import asr_disabled_routes, chat_topics_routes


class BackendChatTopicsRouteSplitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_skills_route_delegates_to_split_module(self) -> None:
        with mock.patch.object(
            chat_topics_routes,
            "list_skills",
            return_value={"skills": ["delegated"], "recipes": [], "items": []},
        ) as delegated:
            resp = self.client.get("/api/skills")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"skills": ["delegated"], "recipes": [], "items": []})
        delegated.assert_called_once_with()

    def test_create_topic_route_delegates_with_store_dependencies(self) -> None:
        with mock.patch.object(
            chat_topics_routes,
            "create_chat_topic",
            new=mock.AsyncMock(return_value={"ok": True, "topic_id": "delegated"}),
        ) as delegated:
            resp = self.client.post("/api/chat/topics", json={"topic_id": "demo-topic"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "topic_id": "delegated"})
        args, kwargs = delegated.await_args
        self.assertEqual(args[0], {"topic_id": "demo-topic"})
        self.assertIs(kwargs["deps"].topic_store, backend_app.TOPIC_STORE)
        self.assertEqual(kwargs["deps"].default_topic_title, backend_app.DEFAULT_TOPIC_TITLE)

    def test_list_topic_route_delegates_with_store_dependencies(self) -> None:
        with mock.patch.object(
            chat_topics_routes,
            "list_chat_topics",
            new=mock.AsyncMock(return_value={"ok": True, "topics": [{"topic_id": "delegated"}]}),
        ) as delegated:
            resp = self.client.get("/api/chat/topics")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "topics": [{"topic_id": "delegated"}]})
        args, kwargs = delegated.await_args
        self.assertEqual(args, ())
        self.assertIs(kwargs["deps"].topic_store, backend_app.TOPIC_STORE)

    def test_topic_detail_route_delegates_with_store_dependencies(self) -> None:
        with mock.patch.object(
            chat_topics_routes,
            "get_chat_topic",
            new=mock.AsyncMock(return_value={"ok": True, "topic_id": "delegated"}),
        ) as delegated:
            resp = self.client.get("/api/chat/topics/demo-topic")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "topic_id": "delegated"})
        args, kwargs = delegated.await_args
        self.assertEqual(args, ("demo-topic",))
        self.assertIs(kwargs["deps"].topic_store, backend_app.TOPIC_STORE)

    def test_delete_topic_routes_delegate_with_store_dependencies(self) -> None:
        with mock.patch.object(
            chat_topics_routes,
            "delete_chat_topic",
            new=mock.AsyncMock(return_value={"ok": True, "deleted": True, "topics": []}),
        ) as delegated:
            resp = self.client.delete("/api/chat/topics/demo-topic")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "deleted": True, "topics": []})
        args, kwargs = delegated.await_args
        self.assertEqual(args, ("demo-topic",))
        self.assertIs(kwargs["deps"].topic_store, backend_app.TOPIC_STORE)

        with mock.patch.object(
            chat_topics_routes,
            "delete_chat_topic_post",
            new=mock.AsyncMock(return_value={"ok": True, "deleted": True, "topics": []}),
        ) as delegated_post:
            resp = self.client.post("/api/chat/topics/demo-topic/delete")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "deleted": True, "topics": []})
        args, kwargs = delegated_post.await_args
        self.assertEqual(args, ("demo-topic",))
        self.assertIs(kwargs["deps"].topic_store, backend_app.TOPIC_STORE)

    def test_asr_warmup_route_delegates_to_disabled_shell_module(self) -> None:
        with mock.patch.object(
            asr_disabled_routes,
            "asr_warmup",
            new=mock.AsyncMock(return_value={"ok": False, "delegated": True}),
        ) as delegated:
            resp = self.client.post("/api/asr/warmup")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": False, "delegated": True})
        delegated.assert_awaited_once_with()

    def test_asr_stream_route_delegates_to_disabled_shell_module(self) -> None:
        async def delegated_stream(websocket) -> None:
            await websocket.accept()
            await websocket.send_json({"type": "delegated"})
            await websocket.close(code=1000)

        with mock.patch.object(
            asr_disabled_routes,
            "asr_stream",
            new=mock.AsyncMock(side_effect=delegated_stream),
        ) as delegated:
            with self.client.websocket_connect("/api/asr/stream") as websocket:
                self.assertEqual(websocket.receive_json(), {"type": "delegated"})

        args, kwargs = delegated.await_args
        self.assertEqual(len(args), 1)
        self.assertEqual(kwargs, {})

    def test_app_source_registers_chat_topics_router_instead_of_inline_decorators(self) -> None:
        source = Path(backend_app.__file__).read_text(encoding="utf-8")

        self.assertIn("chat_topics_routes", source)
        self.assertIn("register_chat_topics_routes(app, _chat_topics_route_deps())", source)
        self.assertNotIn('@app.get("/api/skills")', source)
        self.assertNotIn('@app.post("/api/chat/topics")', source)
        self.assertNotIn('@app.get("/api/chat/topics")', source)
        self.assertNotIn('@app.get("/api/chat/topics/{topic_id}")', source)
        self.assertNotIn('@app.delete("/api/chat/topics/{topic_id}")', source)
        self.assertNotIn('@app.post("/api/chat/topics/{topic_id}/delete")', source)

    def test_split_module_source_declares_chat_topics_router_decorators(self) -> None:
        source = Path(chat_topics_routes.__file__).read_text(encoding="utf-8")

        self.assertIn('@router.get("/api/skills")', source)
        self.assertIn('@router.post("/api/chat/topics")', source)
        self.assertIn('@router.get("/api/chat/topics")', source)
        self.assertIn('@router.get("/api/chat/topics/{topic_id}")', source)
        self.assertIn('@router.delete("/api/chat/topics/{topic_id}")', source)
        self.assertIn('@router.post("/api/chat/topics/{topic_id}/delete")', source)


if __name__ == "__main__":
    unittest.main()
