from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

import backend.app as backend_app
from backend.conversation_store import ConversationStore
from backend.ipet_memory_store import IpetMemoryStore
from backend.runtime_adapters import (
    AstrBotRuntimeAdapter,
    HermesRuntimeAdapter,
    _adapt_astrbot_event,
    _normalize_stream_delta,
    create_runtime_adapter,
)
from backend.runtime_config import (
    DEFAULT_RUNTIME_CONFIG,
    RUNTIME_ASTRBOT,
    RUNTIME_HERMES,
    mirror_runtime_compat,
    normalize_runtime_config,
    normalize_runtime_id,
    runtime_sidecar_config,
)


class _FakeRuntimeClient:
    runtime_id = "astrbot"

    def __init__(self, events=None, payload=None, status=None):
        self.events = events or []
        self.payload = payload or {}
        self.status_payload = status or {
            "ok": True,
            "available": True,
            "configured": True,
            "runtime": "astrbot",
            "detail": "",
        }
        self.requests = []

    async def status(self):
        return dict(self.status_payload)

    async def request_json(self, method, path, *, json_payload=None):
        self.requests.append((method, path, json_payload))
        return dict(self.payload)

    async def stream_sse(self, path, payload):
        self.requests.append(("POST", path, payload))
        for event, data in self.events:
            if event == "sleep":
                await asyncio.sleep(float(data))
                continue
            yield event, data


def _parse_sse_events(raw_text: str):
    events = []
    for chunk in str(raw_text or "").split("\n\n"):
        event_name = ""
        data_lines = []
        for line in chunk.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if event_name and data_lines:
            events.append((event_name, json.loads("".join(data_lines))))
    return events


class RuntimeDefaultsTests(unittest.TestCase):
    def test_default_runtime_config_uses_astrbot(self) -> None:
        self.assertEqual(DEFAULT_RUNTIME_CONFIG["active"], RUNTIME_ASTRBOT)

    def test_normalize_runtime_id_falls_back_to_astrbot(self) -> None:
        self.assertEqual(normalize_runtime_id(None), RUNTIME_ASTRBOT)
        self.assertEqual(normalize_runtime_id("unknown-runtime"), RUNTIME_ASTRBOT)

    def test_legacy_top_level_hermes_customization_keeps_hermes_active(self) -> None:
        config = normalize_runtime_config(
            {},
            legacy_hermes={
                "enabled": True,
                "base_url": "http://127.0.0.1:9120",
            },
        )
        self.assertEqual(config["active"], RUNTIME_HERMES)
        self.assertTrue(config["adapters"][RUNTIME_HERMES]["enabled"])
        self.assertEqual(config["adapters"][RUNTIME_HERMES]["base_url"], "http://127.0.0.1:9120")

    def test_legacy_default_hermes_config_does_not_keep_hermes_active(self) -> None:
        config = mirror_runtime_compat({"hermes": {}})
        self.assertEqual(config["runtime"]["active"], RUNTIME_ASTRBOT)

    def test_create_runtime_adapter_defaults_to_astrbot_but_respects_explicit_hermes(self) -> None:
        self.assertIsInstance(create_runtime_adapter({}), AstrBotRuntimeAdapter)
        self.assertIsInstance(create_runtime_adapter({}, runtime_id=RUNTIME_HERMES), HermesRuntimeAdapter)

    def test_runtime_sidecar_config_defaults_to_astrbot_adapter(self) -> None:
        runtime_id, adapter_config = runtime_sidecar_config({})
        self.assertEqual(runtime_id, RUNTIME_ASTRBOT)
        self.assertEqual(adapter_config["base_url"], "http://127.0.0.1:6185")


class RuntimeProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(backend_app.app)
        self._ipet_store_tmp = tempfile.TemporaryDirectory()
        root = Path(self._ipet_store_tmp.name)
        self._ipet_store_patchers = [
            mock.patch.object(
                backend_app,
                "_get_conversation_store",
                return_value=ConversationStore(root / "ipet_conversations"),
            ),
            mock.patch.object(
                backend_app,
                "_get_ipet_memory_store",
                return_value=IpetMemoryStore(root / "ipet_memory"),
            ),
        ]
        for patcher in self._ipet_store_patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(getattr(self, "_ipet_store_patchers", [])):
            patcher.stop()
        if hasattr(self, "_ipet_store_tmp"):
            self._ipet_store_tmp.cleanup()
        self.client.close()

    def test_settings_config_redacts_astrbot_api_key(self) -> None:
        config = {
            "runtime": {
                "active": "astrbot",
                "adapters": {
                    "astrbot": {
                        "enabled": True,
                        "base_url": "http://127.0.0.1:6185",
                        "health_path": "/",
                        "api_key": "abk_secret_123456",
                        "username": "ipet",
                    }
                },
            },
            "chat": {"backend_url": "http://127.0.0.1:8008", "model": "gpt-5.4", "session_id": "default"},
        }
        with mock.patch.object(backend_app, "_load_full_config", return_value=config):
            resp = self.client.get("/api/settings/config")
        self.assertEqual(resp.status_code, 200)
        astrobot = resp.json()["config"]["runtime"]["adapters"]["astrbot"]
        self.assertEqual(astrobot["api_key"], "")
        self.assertTrue(astrobot["api_key_set"])
        self.assertEqual(astrobot["api_key_action"], "keep")
        self.assertIn("...", astrobot["api_key_preview"])

    def test_settings_config_redacts_astrbot_dashboard_password(self) -> None:
        config = {
            "runtime": {
                "active": "astrbot",
                "adapters": {
                    "astrbot": {
                        "enabled": True,
                        "base_url": "http://127.0.0.1:6185",
                        "dashboard_username": "ipet",
                        "dashboard_password": "dashboard-secret-123456",
                    }
                },
            },
        }
        with mock.patch.object(backend_app, "_load_full_config", return_value=config):
            resp = self.client.get("/api/settings/config")
        self.assertEqual(resp.status_code, 200)
        astrobot = resp.json()["config"]["runtime"]["adapters"]["astrbot"]
        self.assertEqual(astrobot["dashboard_password"], "")
        self.assertEqual(astrobot["dashboard_password_action"], "keep")
        self.assertTrue(astrobot["dashboard_password_set"])
        self.assertIn("...", astrobot["dashboard_password_preview"])

    def test_settings_config_redacts_vision_analyzer_api_key(self) -> None:
        config = {
            "vision": {
                "enabled": True,
                "analyzer": {
                    "enabled": True,
                    "provider": "openai_compatible_vlm",
                    "base_url": "https://vlm.example/v1",
                    "model": "demo-vlm",
                    "api_key": "vsk_secret_123456",
                },
            }
        }
        with mock.patch.object(backend_app, "_load_full_config", return_value=config):
            resp = self.client.get("/api/settings/config")
        self.assertEqual(resp.status_code, 200)
        analyzer = resp.json()["config"]["vision"]["analyzer"]
        self.assertEqual(analyzer["api_key"], "")
        self.assertTrue(analyzer["api_key_set"])
        self.assertEqual(analyzer["api_key_action"], "keep")
        self.assertIn("...", analyzer["api_key_preview"])

    def test_put_settings_config_keeps_or_replaces_vision_analyzer_api_key(self) -> None:
        saved = []
        previous = {
            "vision": {
                "enabled": True,
                "analyzer": {
                    "enabled": True,
                    "provider": "openai_compatible_vlm",
                    "base_url": "https://vlm.example/v1",
                    "model": "demo-vlm",
                    "api_key": "old-secret",
                },
            }
        }

        with mock.patch.object(backend_app, "_load_full_config", return_value=previous), mock.patch.object(
            backend_app, "_save_full_config", side_effect=lambda config: saved.append(config)
        ):
            keep_resp = self.client.put(
                "/api/settings/config",
                json={"config": {"vision": {"analyzer": {"api_key": "", "api_key_action": "keep"}}}},
            )
        self.assertEqual(keep_resp.status_code, 200)
        self.assertEqual(saved[-1]["vision"]["analyzer"]["api_key"], "old-secret")

        with mock.patch.object(backend_app, "_load_full_config", return_value=previous), mock.patch.object(
            backend_app, "_save_full_config", side_effect=lambda config: saved.append(config)
        ):
            replace_resp = self.client.put(
                "/api/settings/config",
                json={"config": {"vision": {"analyzer": {"api_key": "new-secret", "api_key_action": "replace"}}}},
            )
        self.assertEqual(replace_resp.status_code, 200)
        self.assertEqual(saved[-1]["vision"]["analyzer"]["api_key"], "new-secret")

    def test_vision_models_endpoint_uses_user_supplied_vlm_key(self) -> None:
        with mock.patch("backend.app.provider_list_models", new_callable=mock.AsyncMock) as list_models_mock:
            list_models_mock.return_value = ["vision-a", "vision-b"]
            resp = self.client.post(
                "/api/vision/models",
                json={
                    "provider": "openai_compatible_vlm",
                    "base_url": "https://vlm.example/v1",
                    "api_key": "typed-key",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["models"], ["vision-a", "vision-b"])
        list_models_mock.assert_awaited_once_with(
            provider="openai_compat",
            base_url="https://vlm.example/v1",
            api_key="typed-key",
        )

    def test_vision_models_endpoint_reuses_saved_vlm_key_when_input_blank(self) -> None:
        config = {
            "vision": {
                "analyzer": {
                    "provider": "openai_compatible_vlm",
                    "base_url": "https://saved-vlm.example/v1",
                    "api_key": "saved-key",
                },
            }
        }
        with mock.patch.object(backend_app, "_load_full_config", return_value=config), mock.patch(
            "backend.app.provider_list_models", new_callable=mock.AsyncMock
        ) as list_models_mock:
            list_models_mock.return_value = ["saved-vision"]
            resp = self.client.post(
                "/api/vision/models",
                json={
                    "base_url": "https://saved-vlm.example/v1",
                    "api_key": "",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["models"], ["saved-vision"])
        list_models_mock.assert_awaited_once_with(
            provider="openai_compat",
            base_url="https://saved-vlm.example/v1",
            api_key="saved-key",
        )

    def test_runtime_status_uses_active_runtime_client(self) -> None:
        fake = _FakeRuntimeClient(status={"ok": True, "available": True, "runtime": "astrbot"})
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake):
            resp = self.client.get("/api/runtime/status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["runtime"], "astrbot")
        self.assertTrue(resp.json()["available"])

    def test_chat_stream_proxies_active_runtime_sse_shape(self) -> None:
        fake = _FakeRuntimeClient(
            events=[
                ("meta", {"runtime": "astrbot", "topic_id": "default"}),
                ("token", {"delta": "hello"}),
                ("done", {"text": "hello", "topic_id": "default"}),
            ]
        )
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_get_agent_graph_runtime", side_effect=AssertionError("legacy runtime should not be used")
        ):
            resp = self.client.post("/api/chat/stream", json={"text": "hi", "expression_mode": False})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("event: meta", resp.text)
        self.assertIn("event: token", resp.text)
        self.assertIn("event: done", resp.text)
        self.assertEqual(fake.requests[0][1], "/api/chat/stream")

    def test_chat_stream_emits_inferred_worklog_prelude_before_tokens(self) -> None:
        fake = _FakeRuntimeClient(
            events=[
                ("meta", {"runtime": "astrbot", "topic_id": "default"}),
                ("token", {"delta": "hello"}),
                ("done", {"text": "hello", "topic_id": "default"}),
            ]
        )
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake):
            resp = self.client.post("/api/chat/stream", json={"text": "search docs", "expression_mode": False})
        self.assertEqual(resp.status_code, 200)
        events = _parse_sse_events(resp.text)
        self.assertEqual(events[0][0], "meta")
        self.assertEqual(events[1][0], "phase")
        self.assertEqual(events[1][1]["source"], "ipet_inferred")
        self.assertEqual(events[1][1]["render"], "worklog")
        self.assertTrue(events[1][1]["transient"])
        self.assertIn("status_id", events[1][1])
        self.assertEqual(events[-2][0], "token")
        self.assertEqual(events[-1][0], "done")

    def test_chat_stream_marks_runtime_phase_source(self) -> None:
        fake = _FakeRuntimeClient(
            events=[
                ("meta", {"runtime": "astrbot", "topic_id": "default"}),
                ("phase", {"phase": "tool", "text": "AstrBot is running a tool"}),
                ("token", {"delta": "hello"}),
                ("done", {"text": "hello", "topic_id": "default"}),
            ]
        )
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake):
            resp = self.client.post("/api/chat/stream", json={"text": "run a tool", "expression_mode": False})
        events = _parse_sse_events(resp.text)
        runtime_phases = [
            payload
            for event, payload in events
            if event == "phase" and payload.get("text") == "AstrBot is running a tool"
        ]
        self.assertEqual(len(runtime_phases), 1)
        self.assertIn("source", runtime_phases[0])
        self.assertEqual(runtime_phases[0]["source"], "runtime")
        self.assertEqual(runtime_phases[0]["render"], "worklog")

    def test_chat_stream_stops_inferred_heartbeats_after_first_token(self) -> None:
        fake = _FakeRuntimeClient(
            events=[
                ("meta", {"runtime": "astrbot", "topic_id": "default"}),
                ("sleep", 0.02),
                ("token", {"delta": "hello"}),
                ("sleep", 0.03),
                ("done", {"text": "hello", "topic_id": "default"}),
            ]
        )
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_REALTIME_HEARTBEAT_AFTER_SEC", 0.001, create=True
        ), mock.patch.object(backend_app, "_REALTIME_HEARTBEAT_INTERVAL_SEC", 0.001, create=True):
            resp = self.client.post("/api/chat/stream", json={"text": "hi", "expression_mode": False})
        events = _parse_sse_events(resp.text)
        token_index = next(index for index, item in enumerate(events) if item[0] == "token")
        before_token = [payload for event, payload in events[:token_index] if event == "phase" and payload.get("source") == "ipet_inferred"]
        after_token = [payload for event, payload in events[token_index + 1 :] if event == "phase" and payload.get("source") == "ipet_inferred"]
        self.assertTrue(before_token)
        self.assertEqual(after_token, [])

    def test_chat_stream_error_finishes_without_late_heartbeat(self) -> None:
        fake = _FakeRuntimeClient(
            events=[
                ("meta", {"runtime": "astrbot", "topic_id": "default"}),
                ("sleep", 0.02),
                ("error", {"message": "boom", "runtime": "astrbot"}),
            ]
        )
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_REALTIME_HEARTBEAT_AFTER_SEC", 0.001, create=True
        ), mock.patch.object(backend_app, "_REALTIME_HEARTBEAT_INTERVAL_SEC", 0.001, create=True):
            resp = self.client.post("/api/chat/stream", json={"text": "hi", "expression_mode": False})
        events = _parse_sse_events(resp.text)
        self.assertEqual(events[-1][0], "error")
        self.assertTrue(any(event == "phase" and payload.get("source") == "ipet_inferred" for event, payload in events))
        error_index = len(events) - 1
        late_inferred = [
            payload
            for event, payload in events[error_index + 1 :]
            if event == "phase" and payload.get("source") == "ipet_inferred"
        ]
        self.assertEqual(late_inferred, [])

    def test_chat_topics_use_ipet_local_store(self) -> None:
        fake = _FakeRuntimeClient(payload={"ok": True, "runtime": "astrbot", "topics": [{"topic_id": "qq"}]})
        with tempfile.TemporaryDirectory() as root:
            store = ConversationStore(Path(root) / "ipet_conversations")
            store.append_turn(
                "local-topic",
                turn_id="turn-1",
                user_text="hello",
                assistant_text="world",
                runtime="astrbot",
                runtime_session_id="ipet-temp-turn-1",
            )
            with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
                backend_app, "_get_conversation_store", return_value=store
            ):
                resp = self.client.get("/api/chat/topics")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["runtime"], "ipet")
        self.assertEqual(resp.json()["topics"][0]["topic_id"], "local-topic")
        self.assertEqual(fake.requests, [])

    def test_chat_topic_detail_uses_ipet_local_store(self) -> None:
        fake = _FakeRuntimeClient(payload={"ok": True, "runtime": "astrbot", "messages": []})
        with tempfile.TemporaryDirectory() as root:
            store = ConversationStore(Path(root) / "ipet_conversations")
            store.append_turn(
                "qq-FriendMessage-123",
                turn_id="turn-1",
                user_text="hello",
                assistant_text="world",
                runtime="astrbot",
                runtime_session_id="ipet-temp-turn-1",
            )
            with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
                backend_app, "_get_conversation_store", return_value=store
            ):
                resp = self.client.get("/api/chat/topics/qq%3AFriendMessage%3A123")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["runtime"], "ipet")
        self.assertEqual(resp.json()["topic"]["topic_id"], "qq-FriendMessage-123")
        self.assertEqual(fake.requests, [])

    def test_astrbot_cumulative_content_is_trimmed_to_delta(self) -> None:
        self.assertEqual(_normalize_stream_delta("", "你好", cumulative=True), "你好")
        self.assertEqual(_normalize_stream_delta("你好", "你好，很高兴", cumulative=True), "，很高兴")
        self.assertEqual(_normalize_stream_delta("你好，很高兴", "你好，很高兴", cumulative=True), "")

    def test_astrbot_empty_progress_event_can_be_filtered(self) -> None:
        event, payload = _adapt_astrbot_event("message", {"type": "progress"}, session_id="default")
        self.assertEqual(event, "phase")
        self.assertEqual(payload["text"], "")

    def test_astrbot_unknown_textual_event_marks_runtime_phase_source(self) -> None:
        event, payload = _adapt_astrbot_event("custom_progress", {"message": "using AstrBot tool"}, session_id="default")
        self.assertEqual(event, "phase")
        self.assertEqual(payload["source"], "runtime")
        self.assertEqual(payload["render"], "worklog")
        self.assertEqual(payload["text"], "using AstrBot tool")


class AstrBotTopicAdapterTests(unittest.IsolatedAsyncioTestCase):
    def _adapter(self) -> AstrBotRuntimeAdapter:
        return AstrBotRuntimeAdapter(
            {
                "enabled": True,
                "base_url": "http://127.0.0.1:6185",
                "api_key": "abk_test",
                "username": "ipet",
                "dashboard_username": "ipet",
                "dashboard_password": "dashboard-secret",
            }
        )

    async def test_astrbot_topic_list_maps_sessions_and_marks_runtime_capabilities(self) -> None:
        adapter = self._adapter()
        with mock.patch.object(
            adapter,
            "_request_json",
            new=mock.AsyncMock(
                return_value={
                    "data": {
                        "sessions": [
                            {
                                "session_id": "qq:FriendMessage:123",
                                "display_name": "桌宠测试群",
                                "updated_at": "2026-05-15T10:00:00+08:00",
                            }
                        ]
                    }
                }
            ),
        ):
            payload = await adapter.request_json("GET", "/api/chat/topics")

        topic = payload["topics"][0]
        self.assertEqual(topic["topic_id"], "qq:FriendMessage:123")
        self.assertEqual(topic["title"], "桌宠测试群")
        self.assertEqual(topic["runtime"], "astrbot")
        self.assertTrue(topic["supports_history_detail"])
        self.assertTrue(topic["supports_delete"])

    async def test_astrbot_topic_list_keeps_ipet_sessions_when_dashboard_creator_differs(self) -> None:
        adapter = self._adapter()
        with mock.patch.object(
            adapter,
            "_request_json",
            new=mock.AsyncMock(
                return_value={
                    "data": {
                        "sessions": [
                            {
                                "session_id": "webchat:mismatch",
                                "display_name": "Ipet 会话",
                                "platform_id": "webchat",
                                "creator": "other-dashboard-user",
                            },
                            {
                                "session_id": "qq:FriendMessage:123",
                                "display_name": "QQ 会话",
                                "platform_id": "aiocqhttp",
                                "creator": "ipet",
                            },
                        ]
                    }
                }
            ),
        ) as request_json:
            payload = await adapter.request_json("GET", "/api/chat/topics")

        self.assertEqual(request_json.await_args.kwargs["params"], {"username": "ipet"})
        self.assertEqual([topic["topic_id"] for topic in payload["topics"]], ["webchat:mismatch"])
        self.assertTrue(payload["topics"][0]["supports_history_detail"])
        self.assertFalse(payload["topics"][0]["supports_delete"])

    async def test_astrbot_topic_list_enriches_round_counts_from_dashboard_history(self) -> None:
        adapter = self._adapter()
        sessions_payload = {
            "data": {
                "sessions": [
                    {
                        "session_id": "webchat:rounds",
                        "display_name": "有历史的会话",
                        "platform_id": "webchat",
                        "creator": "ipet",
                    }
                ]
            }
        }
        dashboard_payload = {
            "data": {
                "history": [
                    {"content": {"type": "user", "message": [{"type": "plain", "text": "第一问"}]}},
                    {"content": {"type": "bot", "message": [{"type": "plain", "text": "第一答"}]}},
                    {"content": {"type": "user", "message": [{"type": "plain", "text": "第二问"}]}},
                    {"content": {"type": "bot", "message": [{"type": "plain", "text": "第二答"}]}},
                ]
            }
        }
        with mock.patch.object(adapter, "_request_json", new=mock.AsyncMock(return_value=sessions_payload)), mock.patch.object(
            adapter, "_dashboard_json", new=mock.AsyncMock(return_value=dashboard_payload)
        ) as dashboard_json:
            payload = await adapter.request_json("GET", "/api/chat/topics")

        topic = payload["topics"][0]
        self.assertEqual(topic["message_count"], 4)
        self.assertEqual(topic["assistant_turn_count"], 2)
        self.assertEqual(topic["preview"], "第二答")
        dashboard_json.assert_awaited_once_with(
            "GET",
            "/api/chat/get_session",
            params={"session_id": "webchat:rounds"},
            dashboard_username="ipet",
        )

    async def test_astrbot_topic_detail_fetches_dashboard_history_and_maps_parts(self) -> None:
        adapter = self._adapter()
        sessions_payload = {
            "data": {
                "sessions": [
                    {
                        "session_id": "qq:FriendMessage:123",
                        "display_name": "桌宠测试群",
                        "platform_id": "webchat",
                        "creator": "ipet",
                        "created_at": "2026-05-15T09:00:00+08:00",
                    }
                ]
            }
        }
        dashboard_payload = {
            "data": {
                "history": [
                    {"content": {"type": "user", "message": [{"type": "plain", "text": "你好"}, {"type": "image"}]}},
                    {"content": {"type": "bot", "message": [{"type": "plain", "text": "收到"}, {"type": "file", "name": "a.txt"}]}},
                ]
            }
        }
        with mock.patch.object(adapter, "_request_json", new=mock.AsyncMock(return_value=sessions_payload)), mock.patch.object(
            adapter, "_dashboard_json", new=mock.AsyncMock(return_value=dashboard_payload)
        ) as dashboard_json:
            payload = await adapter.request_json("GET", "/api/chat/topics/qq%3AFriendMessage%3A123")

        self.assertEqual(payload["meta"]["topic_id"], "qq:FriendMessage:123")
        self.assertEqual(payload["meta"]["title"], "桌宠测试群")
        self.assertFalse(payload.get("history_unavailable", False))
        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertEqual(payload["messages"][0]["content"], "你好\n[图片]")
        self.assertEqual(payload["messages"][1]["role"], "assistant")
        self.assertEqual(payload["messages"][1]["content"], "收到\n[文件: a.txt]")
        dashboard_json.assert_awaited_once_with(
            "GET",
            "/api/chat/get_session",
            params={"session_id": "qq:FriendMessage:123"},
            dashboard_username="ipet",
        )

    async def test_astrbot_topic_list_can_use_local_dashboard_jwt_secret_without_password(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root) / "data"
            data_dir.mkdir()
            (data_dir / "cmd_config.json").write_text(
                "\ufeff" + json.dumps({"dashboard": {"jwt_secret": "local-secret"}}),
                encoding="utf-8",
            )
            adapter = AstrBotRuntimeAdapter(
                {
                    "enabled": True,
                    "base_url": "http://127.0.0.1:6185",
                    "api_key": "abk_test",
                    "username": "ipet",
                    "dashboard_username": "pacoman",
                    "cwd": root,
                }
            )
            sessions_payload = {
                "data": {
                    "sessions": [
                        {
                            "session_id": "webchat:local",
                            "display_name": "本地历史",
                            "platform_id": "webchat",
                            "creator": "ipet",
                        }
                    ]
                }
            }
            dashboard_payload = {
                "data": {
                    "history": [
                        {"content": {"type": "user", "message": [{"type": "plain", "text": "问"}]}},
                        {"content": {"type": "bot", "message": [{"type": "plain", "text": "答"}]}},
                    ]
                }
            }
            with mock.patch.object(adapter, "_request_json", new=mock.AsyncMock(return_value=sessions_payload)), mock.patch.object(
                adapter, "_dashboard_json", new=mock.AsyncMock(return_value=dashboard_payload)
            ) as dashboard_json:
                payload = await adapter.request_json("GET", "/api/chat/topics")

        topic = payload["topics"][0]
        self.assertTrue(topic["supports_history_detail"])
        self.assertTrue(topic["supports_delete"])
        self.assertEqual(topic["assistant_turn_count"], 1)
        dashboard_json.assert_awaited_once_with(
            "GET",
            "/api/chat/get_session",
            params={"session_id": "webchat:local"},
            dashboard_username="ipet",
        )

    async def test_astrbot_topic_detail_keeps_delete_disabled_when_creator_mismatches(self) -> None:
        adapter = self._adapter()
        sessions_payload = {
            "data": {
                "sessions": [
                    {
                        "session_id": "webchat:mismatch",
                        "display_name": "Ipet 会话",
                        "platform_id": "webchat",
                        "creator": "other-dashboard-user",
                    }
                ]
            }
        }
        dashboard_payload = {"data": {"history": [{"content": {"type": "bot", "message": [{"type": "plain", "text": "历史"}]}}]}}
        with mock.patch.object(adapter, "_request_json", new=mock.AsyncMock(return_value=sessions_payload)), mock.patch.object(
            adapter, "_dashboard_json", new=mock.AsyncMock(return_value=dashboard_payload)
        ):
            payload = await adapter.request_json("GET", "/api/chat/topics/webchat%3Amismatch")

        self.assertTrue(payload["supports_history_detail"])
        self.assertFalse(payload["supports_delete"])
        self.assertFalse(payload["topic"]["supports_delete"])
        self.assertEqual(payload["messages"][0]["content"], "历史")

    async def test_astrbot_create_topic_returns_unique_session_hint(self) -> None:
        adapter = self._adapter()
        first = await adapter.request_json("POST", "/api/chat/topics", json_payload={})
        second = await adapter.request_json("POST", "/api/chat/topics", json_payload={})

        self.assertNotEqual(first["topic"]["topic_id"], "default")
        self.assertNotEqual(second["topic"]["topic_id"], "default")
        self.assertNotEqual(first["topic"]["topic_id"], second["topic"]["topic_id"])

    async def test_astrbot_topic_delete_calls_dashboard_and_returns_refreshed_topics(self) -> None:
        adapter = self._adapter()
        calls = []
        list_payloads = [
            {
                "data": {
                    "sessions": [
                        {
                            "session_id": "qq:FriendMessage:123",
                            "display_name": "待删会话",
                            "platform_id": "webchat",
                            "creator": "ipet",
                        }
                    ]
                }
            },
            {"data": {"sessions": []}},
        ]

        async def fake_request_json(method, path, *, params=None, json_payload=None):
            calls.append(("openapi", method, path, params, json_payload))
            return list_payloads.pop(0)

        async def fake_dashboard_json(method, path, *, params=None, json_payload=None, dashboard_username=None):
            calls.append(("dashboard", method, path, params, json_payload, dashboard_username))
            return {"ok": True}

        with mock.patch.object(adapter, "_request_json", new=fake_request_json), mock.patch.object(
            adapter, "_dashboard_json", new=fake_dashboard_json
        ):
            payload = await adapter.request_json("DELETE", "/api/chat/topics/qq%3AFriendMessage%3A123")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["topics"], [])
        self.assertIn(("dashboard", "GET", "/api/chat/delete_session", {"session_id": "qq:FriendMessage:123"}, None, "ipet"), calls)

    async def test_astrbot_topic_delete_uses_local_jwt_creator_identity(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root) / "data"
            data_dir.mkdir()
            (data_dir / "cmd_config.json").write_text(
                "\ufeff" + json.dumps({"dashboard": {"jwt_secret": "local-secret"}}),
                encoding="utf-8",
            )
            adapter = AstrBotRuntimeAdapter(
                {
                    "enabled": True,
                    "base_url": "http://127.0.0.1:6185",
                    "api_key": "abk_test",
                    "username": "ipet",
                    "dashboard_username": "pacoman",
                    "cwd": root,
                }
            )
            calls = []
            session = {
                "session_id": "webchat:delete-local",
                "display_name": "本地删除",
                "platform_id": "webchat",
                "creator": "ipet",
            }
            list_payloads = [{"data": {"sessions": [session]}}, {"data": {"sessions": []}}]

            async def fake_request_json(method, path, *, params=None, json_payload=None):
                return list_payloads.pop(0)

            async def fake_dashboard_json(method, path, *, params=None, json_payload=None, dashboard_username=None):
                calls.append((method, path, params, dashboard_username))
                return {"status": "ok", "data": {"history": []}}

            with mock.patch.object(adapter, "_request_json", new=fake_request_json), mock.patch.object(
                adapter, "_dashboard_json", new=fake_dashboard_json
            ):
                payload = await adapter.request_json("DELETE", "/api/chat/topics/webchat%3Adelete-local")

        self.assertTrue(payload["ok"])
        self.assertIn(("GET", "/api/chat/delete_session", {"session_id": "webchat:delete-local"}, "ipet"), calls)

    async def test_astrbot_topic_delete_reports_failure_when_refresh_still_contains_session(self) -> None:
        adapter = self._adapter()
        session = {
            "session_id": "qq:FriendMessage:123",
            "display_name": "删不掉会话",
            "platform_id": "webchat",
            "creator": "ipet",
        }
        list_payloads = [
            {"data": {"sessions": [session]}},
            {"data": {"sessions": [session]}},
        ]

        async def fake_request_json(method, path, *, params=None, json_payload=None):
            return list_payloads.pop(0)

        with mock.patch.object(adapter, "_request_json", new=fake_request_json), mock.patch.object(
            adapter, "_dashboard_json", new=mock.AsyncMock(return_value={"status": "ok", "data": {}})
        ):
            payload = await adapter.request_json("DELETE", "/api/chat/topics/qq%3AFriendMessage%3A123")

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["kind"], "chat_topic_delete_not_confirmed")
        self.assertEqual([topic["topic_id"] for topic in payload["topics"]], ["qq:FriendMessage:123"])

    async def test_astrbot_topic_delete_rejects_username_mismatch(self) -> None:
        adapter = self._adapter()
        with mock.patch.object(
            adapter,
            "_request_json",
            new=mock.AsyncMock(
                return_value={
                    "data": {
                        "sessions": [
                            {
                                "session_id": "qq:FriendMessage:123",
                                "display_name": "别人创建",
                                "platform_id": "webchat",
                                "creator": "other-user",
                            }
                        ]
                    }
                }
            ),
        ), mock.patch.object(adapter, "_dashboard_json", new=mock.AsyncMock()) as dashboard_json:
            payload = await adapter.request_json("DELETE", "/api/chat/topics/qq%3AFriendMessage%3A123")

        self.assertFalse(payload["ok"])
        self.assertFalse(payload["supports_delete"])
        self.assertIn("creator", payload["detail"])
        dashboard_json.assert_not_awaited()

    async def test_astrbot_dashboard_status_error_payload_is_unavailable(self) -> None:
        adapter = self._adapter()

        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"status": "error", "detail": "session not found"}

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def request(self, method, url, **kwargs):
                return FakeResponse()

        adapter._dashboard_tokens["dashboard:ipet"] = "cached-token"
        with mock.patch("backend.runtime_adapters.httpx.AsyncClient", FakeAsyncClient):
            with self.assertRaisesRegex(Exception, "session not found"):
                await adapter._dashboard_json("GET", "/api/chat/get_session", params={"session_id": "missing"})


class AstrBotVisionAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_astrbot_chat_stream_rewrites_reserved_vision_identity(self) -> None:
        calls = []

        class FakeStreamResponse:
            status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def aread(self):
                return b""

            async def aiter_lines(self):
                yield 'event: token'
                yield 'data: {"delta":"你好"}'
                yield ''
                yield 'event: done'
                yield 'data: {"text":"你好"}'
                yield ''

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, method, url, **kwargs):
                calls.append((method, url, kwargs))
                return FakeStreamResponse()

        adapter = AstrBotRuntimeAdapter(
            {
                "enabled": True,
                "base_url": "http://127.0.0.1:6185",
                "api_key": "abk_test",
                "username": "ipet-vision-analysis",
            }
        )

        with self.assertLogs("backend.runtime_adapters", level="WARNING") as logs, mock.patch(
            "backend.runtime_adapters.httpx.AsyncClient", FakeAsyncClient
        ):
            events = [
                item
                async for item in adapter.stream_sse(
                    "/api/chat/stream",
                    {
                        "text": "普通聊天不要污染视觉历史",
                        "session_id": "ipet-vision-analysis",
                    },
                )
            ]

        meta_event, meta_payload = events[0]
        self.assertEqual(meta_event, "meta")
        self.assertTrue(any("Rewriting reserved AstrBot vision identity" in item for item in logs.output))
        self.assertEqual(meta_payload["session_id"], "ipet-chat-default")
        self.assertEqual(meta_payload["topic_id"], "ipet-chat-default")
        self.assertEqual(meta_payload["runtime_guard"]["kind"], "reserved_astrbot_vision_identity")
        self.assertEqual(calls[0][2]["json"]["session_id"], "ipet-chat-default")
        self.assertEqual(calls[0][2]["json"]["username"], "ipet")
        self.assertNotEqual(calls[0][2]["json"]["session_id"], "ipet-vision-analysis")
        self.assertNotEqual(calls[0][2]["json"]["username"], "ipet-vision-analysis")

    async def test_astrbot_chat_stream_sends_inline_vision_frame_as_same_message(self) -> None:
        calls = []

        class FakeStreamResponse:
            status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def aread(self):
                return b""

            async def aiter_lines(self):
                yield 'event: token'
                yield 'data: {"delta":"看到了"}'
                yield ''
                yield 'event: done'
                yield 'data: {"text":"看到了"}'
                yield ''

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, **kwargs):
                calls.append(("post", url, kwargs, self.kwargs))
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json={"data": {"attachment_id": "att-inline-1"}},
                )

            def stream(self, method, url, **kwargs):
                calls.append(("stream", url, kwargs, self.kwargs))
                return FakeStreamResponse()

        adapter = AstrBotRuntimeAdapter(
            {
                "enabled": True,
                "base_url": "http://127.0.0.1:6185",
                "api_key": "abk_test",
                "username": "ipet",
            }
        )

        with mock.patch("backend.runtime_adapters.httpx.AsyncClient", FakeAsyncClient):
            events = [
                item
                async for item in adapter.stream_sse(
                    "/api/chat/stream",
                    {
                        "text": "请看图回答",
                        "session_id": "default",
                        "vision_frame": {
                            "mime_type": "image/png",
                            "data_url": "data:image/png;base64,iVBORw0KGgo=",
                        },
                    },
                )
            ]

        self.assertEqual(events[-1], ("done", {"text": "看到了", "topic_id": "default", "runtime": "astrbot"}))
        self.assertEqual(calls[0][0], "post")
        self.assertEqual(calls[0][1], "http://127.0.0.1:6185/api/v1/file")
        self.assertEqual(calls[1][0], "stream")
        message = calls[1][2]["json"]["message"]
        self.assertEqual(message[0], {"type": "plain", "text": "请看图回答"})
        self.assertEqual(message[1], {"type": "image", "attachment_id": "att-inline-1"})

    async def test_astrbot_chat_stream_sends_multiple_inline_vision_frames(self) -> None:
        calls = []

        class FakeStreamResponse:
            status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def aread(self):
                return b""

            async def aiter_lines(self):
                yield 'event: done'
                yield 'data: {"text":"看到了"}'
                yield ''

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, **kwargs):
                calls.append(("post", url, kwargs, self.kwargs))
                upload_index = len([item for item in calls if item[0] == "post"])
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json={"data": {"attachment_id": f"att-inline-{upload_index}"}},
                )

            def stream(self, method, url, **kwargs):
                calls.append(("stream", url, kwargs, self.kwargs))
                return FakeStreamResponse()

        adapter = AstrBotRuntimeAdapter(
            {
                "enabled": True,
                "base_url": "http://127.0.0.1:6185",
                "api_key": "abk_test",
                "username": "ipet",
            }
        )

        with mock.patch("backend.runtime_adapters.httpx.AsyncClient", FakeAsyncClient):
            events = [
                item
                async for item in adapter.stream_sse(
                    "/api/chat/stream",
                    {
                        "text": "请先看全图再看局部",
                        "session_id": "default",
                        "vision_frames": [
                            {
                                "mime_type": "image/png",
                                "data_url": "data:image/png;base64,iVBORw0KGgo=",
                                "frame_id": "active-main",
                            },
                            {
                                "mime_type": "image/png",
                                "data_url": "data:image/png;base64,aVBPUkRldGFpbA==",
                                "frame_id": "active-detail",
                            },
                        ],
                    },
                )
            ]

        self.assertEqual(events[-1], ("done", {"text": "看到了", "topic_id": "default", "runtime": "astrbot"}))
        self.assertEqual([item[0] for item in calls[:2]], ["post", "post"])
        message = calls[2][2]["json"]["message"]
        self.assertEqual(message[0], {"type": "plain", "text": "请先看全图再看局部"})
        self.assertEqual(message[1], {"type": "image", "attachment_id": "att-inline-1"})
        self.assertEqual(message[2], {"type": "image", "attachment_id": "att-inline-2"})

    async def test_astrbot_vision_analysis_fallback_is_disabled_without_astrbot_chat(self) -> None:
        calls = []

        class FakeAsyncClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, **kwargs):
                calls.append((url, kwargs, self.kwargs))
                raise AssertionError("legacy AstrBot chat-based vision fallback must not make HTTP calls")

        adapter = AstrBotRuntimeAdapter(
            {
                "enabled": True,
                "base_url": "http://127.0.0.1:6185",
                "api_key": "abk_test",
                "username": "ipet",
            }
        )

        with self.assertLogs("backend.runtime_adapters", level="WARNING") as logs:
            result = await adapter.analyze_vision_frame(
                {"mime_type": "image/png", "data_url": "data:image/png;base64,iVBORw0KGgo=", "frame_id": "frame-vision-1"},
                {"timeout_sec": 1.0},
                http_client_factory=FakeAsyncClient,
            )

        self.assertEqual(result.status["status"], "error")
        self.assertEqual(result.status["provider"], "active_runtime_vlm")
        self.assertTrue(any("chat-based vision fallback is disabled" in item for item in logs.output))
        self.assertIn("disabled", result.status["last_error"])
        self.assertIn("Ipet-owned", result.status["last_error"])
        self.assertEqual(calls, [])


class AstrBotVisionContextReferenceHelperTests(unittest.TestCase):
    def test_passive_timeline_reference_helper_returns_compact_timeline(self) -> None:
        from integrations.astrbot.ipet_vision_context import plugin_skeleton

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "timeline": [
                            {
                                "frame_id": "frame-1",
                                "change_summary": "切到设置页",
                                "important_objects": ["设置页"],
                                "visible_text": ["自动视觉"],
                                "confidence": 0.8,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ).encode("utf-8")

        with mock.patch.dict("os.environ", {"IPET_LOCAL_API_TOKEN": "plugin-token"}), mock.patch(
            "integrations.astrbot.ipet_vision_context.plugin_skeleton.urllib.request.urlopen",
            return_value=FakeResponse(),
        ) as urlopen_mock:
            text = plugin_skeleton.ipet_passive_vision_timeline()

        self.assertIn("最近屏幕变化：", text)
        self.assertIn("1. 切到设置页", text)
        request = urlopen_mock.call_args.args[0]
        self.assertIn("lane=passive", request.full_url)

    def test_active_observe_reference_helper_requests_legacy_image_and_injects_next_request_image_url(self) -> None:
        from integrations.astrbot.ipet_vision_context import plugin_skeleton

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "ok": True,
                        "context": {
                            "observations": [{"claim": "浏览器页面可见", "confidence": 0.9}],
                        },
                        "image_urls": ["base64://active-shot"],
                    },
                    ensure_ascii=False,
                ).encode("utf-8")

        request = type("Request", (), {"extra_user_content_parts": []})()
        with mock.patch.dict("os.environ", {"IPET_LOCAL_API_TOKEN": "plugin-token"}), mock.patch(
            "integrations.astrbot.ipet_vision_context.plugin_skeleton.urllib.request.urlopen",
            return_value=FakeResponse(),
        ) as urlopen_mock:
            result = plugin_skeleton.ipet_active_observe("浏览器打开的是什么？")
            plugin_skeleton.inject_active_observe_result_into_next_request(request, result)

        self.assertTrue(result["ok"])
        sent_request = urlopen_mock.call_args.args[0]
        self.assertIn("lane=active", sent_request.full_url)
        self.assertIn("include_image=true", sent_request.full_url)
        self.assertEqual(request.extra_user_content_parts[-1], {"type": "image_url", "image_url": "base64://active-shot"})

    def test_active_observe_reference_helper_sends_attempt_reason_and_exclude_seen(self) -> None:
        from integrations.astrbot.ipet_vision_context import plugin_skeleton

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"ok": True, "image_urls": []}).encode("utf-8")

        with mock.patch.dict("os.environ", {"IPET_LOCAL_API_TOKEN": "plugin-token"}), mock.patch(
            "integrations.astrbot.ipet_vision_context.plugin_skeleton.urllib.request.urlopen",
            return_value=FakeResponse(),
        ) as urlopen_mock:
            plugin_skeleton.ipet_active_observe(
                "用户正在看的页面是什么？",
                target_hint="browser",
                attempt_reason="当前问题需要读取用户正在看的网页",
                exclude_seen=[{"target_hint": "frontmost", "frame_hash": "seen-frame"}],
            )

        sent_request = urlopen_mock.call_args.args[0]
        body = json.loads(sent_request.data.decode("utf-8"))
        self.assertEqual(body["text"], "用户正在看的页面是什么？")
        self.assertEqual(body["target_hint"], "browser")
        self.assertEqual(body["attempt_reason"], "当前问题需要读取用户正在看的网页")
        self.assertEqual(body["exclude_seen"][0]["frame_hash"], "seen-frame")

    def test_reference_protocol_prompt_marks_deprecated_broker_owned_path(self) -> None:
        from integrations.astrbot.ipet_vision_context import plugin_skeleton

        protocol = plugin_skeleton.build_vision_tool_protocol_context()

        self.assertIn("Deprecated Ipet Vision Reference Helper", protocol)
        self.assertIn("Ipet Vision Broker", protocol)
        self.assertIn("/api/chat/stream", protocol)
        self.assertIn("runtime 默认只接收 bounded text evidence", protocol)
        self.assertIn("不要把它作为推荐插件安装路径", protocol)
        self.assertIn("不要让 AstrBot/model 接管 retry loop", protocol)
        self.assertIn("passive timeline", protocol)
        self.assertIn("active broker live evidence", protocol)

    def test_deprecated_hook_reference_injects_protocol_before_passive_timeline(self) -> None:
        from integrations.astrbot.ipet_vision_context import plugin_skeleton

        request = type("Request", (), {"extra_user_content_parts": []})()
        with mock.patch.object(plugin_skeleton, "ipet_passive_vision_timeline", return_value="最近屏幕变化：\n1. 切到浏览器"):
            asyncio.run(plugin_skeleton.on_before_llm_request(request))

        self.assertGreaterEqual(len(request.extra_user_content_parts), 2)
        self.assertIn("Observe-Reason-Retry", request.extra_user_content_parts[0]["text"])
        self.assertIn("最近屏幕变化：", request.extra_user_content_parts[1]["text"])


if __name__ == "__main__":
    unittest.main()
