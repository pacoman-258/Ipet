from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app
from brain import BrainDecision
from brain.llm import BrainLLMError


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
        self.assertEqual(config["brain"]["provider"], "openai_compatible")
        self.assertNotIn("api_key", config["human_ops"]["observe_model"])
        self.assertEqual(config["human_ops"]["observe_model"]["api_key_preview"], "")

    def test_settings_assets_disable_cache(self) -> None:
        for path in ("/settings", "/settings.css", "/settings.js"):
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200)
                cache_control = resp.headers.get("cache-control", "")
                self.assertIn("no-store", cache_control)
                self.assertEqual(resp.headers.get("pragma"), "no-cache")
                self.assertEqual(resp.headers.get("expires"), "0")

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

    def test_settings_put_persists_observe_model_without_key_echo(self) -> None:
        resp = self.client.put(
            "/api/settings/config",
            json={
                "config": {
                    "human_ops": {
                        "observe_model": {
                            "enabled": True,
                            "provider": "openai_compatible",
                            "model_endpoint": "https://observe.example/v1",
                            "model_name": "observe-fast",
                            "api_key": "observe-secret",
                        }
                    }
                }
            },
        )

        self.assertEqual(resp.status_code, 200)
        observe_model = resp.json()["config"]["human_ops"]["observe_model"]
        self.assertTrue(observe_model["enabled"])
        self.assertEqual(observe_model["model_name"], "observe-fast")
        self.assertNotIn("api_key", observe_model)
        self.assertEqual(observe_model["api_key_preview"], "ob***et")

    def test_brain_models_route_uses_unsaved_settings_without_key_echo(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://saved.example",
                        "api_key": "saved-secret",
                    }
                }
            ),
            encoding="utf-8",
        )
        models = [SimpleNamespace(id="neo-a", label="neo-a"), SimpleNamespace(id="neo-b", label="Neo B")]

        with mock.patch.object(backend_app, "list_provider_models", new=mock.AsyncMock(return_value=models)) as list_mock:
            resp = self.client.post(
                "/api/brain/models",
                json={
                    "provider": "openai_compatible",
                    "model_endpoint": "https://draft.example",
                    "api_key": "",
                },
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "openai_compatible")
        self.assertEqual(payload["models"], [{"id": "neo-a", "label": "neo-a"}, {"id": "neo-b", "label": "Neo B"}])
        self.assertNotIn("saved-secret", json.dumps(payload))
        args, _kwargs = list_mock.await_args
        self.assertEqual(args[0]["provider"], "openai_compatible")
        self.assertEqual(args[0]["model_endpoint"], "https://draft.example")
        self.assertEqual(args[0]["api_key"], "saved-secret")

    def test_brain_models_route_requires_endpoint(self) -> None:
        resp = self.client.post("/api/brain/models", json={"provider": "openai_compatible", "model_endpoint": ""})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("endpoint", resp.json()["detail"].lower())

    def test_brain_models_route_can_use_saved_observe_model_secret(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "human_ops": {
                        "observe_model": {
                            "enabled": True,
                            "provider": "openai_compatible",
                            "model_endpoint": "https://observe-saved.example",
                            "api_key": "observe-saved-secret",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        models = [SimpleNamespace(id="observe-a", label="Observe A")]

        with mock.patch.object(backend_app, "list_provider_models", new=mock.AsyncMock(return_value=models)) as list_mock:
            resp = self.client.post(
                "/api/brain/models",
                json={
                    "scope": "observe",
                    "provider": "openai_compatible",
                    "model_endpoint": "https://observe-draft.example",
                    "api_key": "",
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["models"], [{"id": "observe-a", "label": "Observe A"}])
        args, _kwargs = list_mock.await_args
        self.assertEqual(args[0]["model_endpoint"], "https://observe-draft.example")
        self.assertEqual(args[0]["api_key"], "observe-saved-secret")

    def test_brain_models_route_google_aistudio_needs_only_saved_key(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "google_aistudio",
                        "api_key": "gemini-saved-secret",
                    }
                }
            ),
            encoding="utf-8",
        )
        models = [SimpleNamespace(id="gemini-a", label="Gemini A")]

        with mock.patch.object(backend_app, "list_provider_models", new=mock.AsyncMock(return_value=models)) as list_mock:
            resp = self.client.post("/api/brain/models", json={"provider": "google_aistudio", "api_key": ""})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["models"], [{"id": "gemini-a", "label": "Gemini A"}])
        args, _kwargs = list_mock.await_args
        self.assertEqual(args[0]["provider"], "google_aistudio")
        self.assertEqual(args[0]["api_key"], "gemini-saved-secret")
        self.assertEqual(args[0]["model_endpoint"], "")

    def test_brain_models_route_google_aistudio_does_not_reuse_old_non_google_endpoint(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://saved.example/v1",
                        "api_key": "gemini-saved-secret",
                    }
                }
            ),
            encoding="utf-8",
        )
        models = [SimpleNamespace(id="gemini-a", label="Gemini A")]

        with mock.patch.object(backend_app, "list_provider_models", new=mock.AsyncMock(return_value=models)) as list_mock:
            resp = self.client.post(
                "/api/brain/models",
                json={
                    "provider": "google_aistudio",
                    "model_endpoint": "",
                    "api_key": "gemini-saved-secret",
                },
            )

        self.assertEqual(resp.status_code, 200)
        args, _kwargs = list_mock.await_args
        self.assertEqual(args[0]["provider"], "google_aistudio")
        self.assertEqual(args[0]["model_endpoint"], "")
        self.assertEqual(args[0]["api_key"], "gemini-saved-secret")

    def test_brain_models_route_google_aistudio_reports_remote_error(self) -> None:
        with mock.patch.object(
            backend_app,
            "list_provider_models",
            new=mock.AsyncMock(side_effect=backend_app.BrainLLMError("401 Unauthorized")),
        ):
            resp = self.client.post(
                "/api/brain/models",
                json={
                    "provider": "google_aistudio",
                    "model_endpoint": "",
                    "api_key": "gemini-saved-secret",
                },
            )

        self.assertEqual(resp.status_code, 502)
        self.assertIn("401 Unauthorized", resp.json()["detail"])

    def test_chat_stream_google_aistudio_uses_saved_key_without_endpoint(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "google_aistudio",
                        "model_name": "gemini-chat",
                        "api_key": "gemini-saved-secret",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(text="Google Brain 回复", provider="google_aistudio", model="gemini-chat")

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(return_value=completion)) as run_mock:
            with self.client.stream("POST", "/api/chat/stream", json={"text": "你好", "session_id": "google-brain"}) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        events = _sse_events(body)
        token_text = "".join(data.get("text", "") for name, data in events if name == "token")
        self.assertEqual(token_text, "Google Brain 回复")
        args, _kwargs = run_mock.await_args
        self.assertEqual(args[0]["provider"], "google_aistudio")
        self.assertEqual(args[0]["api_key"], "gemini-saved-secret")
        self.assertEqual(args[0].get("model_endpoint"), "")

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

    def test_chat_stream_uses_configured_brain_runner(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                        "api_key": "secret",
                        "persona": "你是 Ipet。",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(text="来自真实 Brain 的回复", provider="openai_compatible", model="neo-model")

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(return_value=completion)) as run_mock:
            with self.client.stream("POST", "/api/chat/stream", json={"text": "你好", "session_id": "neo-brain"}) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        events = _sse_events(body)
        self.assertEqual([name for name, _ in events[:2]], ["meta", "phase"])
        token_text = "".join(data.get("text", "") for name, data in events if name == "token")
        self.assertEqual(token_text, "来自真实 Brain 的回复")
        meta = [data for name, data in events if name == "meta"][-1]
        self.assertEqual(meta["provider"], "openai_compatible")
        self.assertEqual(meta["model"], "neo-model")
        done = [data for name, data in events if name == "done"][-1]
        self.assertEqual(done["text"], "来自真实 Brain 的回复")
        self.assertEqual(done["decision"]["kind"], "say")
        self.assertFalse(done["decision"]["requires_review"])
        run_mock.assert_awaited_once()
        args, kwargs = run_mock.await_args
        self.assertEqual(args[0]["model_name"], "neo-model")
        self.assertEqual(args[0]["api_key"], "secret")
        self.assertEqual(kwargs["user_text"], "你好")

    def test_chat_stream_emits_say_decision_without_json_shell(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(
            text="只聊天正常。",
            raw_text='{"kind":"say","text":"只聊天正常。"}',
            decision=BrainDecision.say("只聊天正常。"),
            provider="openai_compatible",
            model="neo-model",
        )

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(return_value=completion)):
            with self.client.stream("POST", "/api/chat/stream", json={"text": "只聊天", "session_id": "neo-say"}) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        events = _sse_events(body)
        token_text = "".join(data.get("text", "") for name, data in events if name == "token")
        self.assertEqual(token_text, "只聊天正常。")
        self.assertNotIn('"kind"', token_text)
        done = [data for name, data in events if name == "done"][-1]
        self.assertEqual(done["decision"]["kind"], "say")
        self.assertEqual(done["decision"]["payload"]["text"], "只聊天正常。")

    def test_chat_stream_observe_decision_runs_free_observation(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(
            text="Observe screen",
            decision=BrainDecision.observe("screen"),
            provider="openai_compatible",
            model="neo-model",
        )
        followup = SimpleNamespace(
            text="我看到当前屏幕上有 Codex 窗口。",
            decision=BrainDecision.say("我看到当前屏幕上有 Codex 窗口。"),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": "我看到当前屏幕上有 Codex 窗口。",
            "observations": [{"text": "Codex 窗口位于前台"}],
            "unknowns": [],
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[completion, followup])), mock.patch.object(
            backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
        ) as observe_mock:
            with self.client.stream("POST", "/api/chat/stream", json={"text": "看一下屏幕", "session_id": "neo-observe"}) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        observe_mock.assert_awaited_once()
        events = _sse_events(body)
        event_names = [name for name, _ in events]
        self.assertIn("display_segment", event_names)
        self.assertNotIn("approval_required", event_names)
        done = [data for name, data in events if name == "done"][-1]
        self.assertEqual(done["text"], "我看到当前屏幕上有 Codex 窗口。")
        self.assertEqual(done["decision"]["kind"], "say")
        self.assertEqual(done["observation"]["observations"][0]["text"], "Codex 窗口位于前台")
        topic = backend_app.TOPIC_STORE.get_topic_detail("neo-observe") or {}
        self.assertEqual(topic["messages"][-1]["content"], "我看到当前屏幕上有 Codex 窗口。")

    def test_chat_stream_react_continues_from_think_to_observe_to_review(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        think = SimpleNamespace(
            text="需要观察 Dock。",
            decision=BrainDecision.think(
                "需要观察 Dock。",
                goal={"objective": "打开 Dock 里的 Chrome", "status": "in_progress", "next": "observe"},
                next_kind="observe",
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        observe = SimpleNamespace(
            text="Observe Dock",
            decision=BrainDecision.observe(
                "Dock Chrome",
                observe_prompt="请找到 Dock 中 Chrome 图标的可点击中心点 macOS 屏幕坐标 x/y。",
                goal={"objective": "打开 Dock 里的 Chrome", "status": "in_progress", "next": "observe"},
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        propose = SimpleNamespace(
            text="Propose click",
            decision=BrainDecision.propose_act(
                "click",
                {"x": 382, "y": 930, "label": "Dock Chrome 图标"},
                goal={"objective": "打开 Dock 里的 Chrome", "status": "handoff_review", "next": "human_ops_review"},
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": "Dock 中可见 Chrome 图标，可点击中心点的 macOS 屏幕坐标为 x=382, y=930。",
            "observations": [{"claim": "Chrome 图标中心点 x=382, y=930"}],
            "unknowns": [],
            "coordinate_context": "Observe coordinate context: already macOS screen coordinates.",
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[think, observe, propose])) as run_mock, mock.patch.object(
            backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
        ) as observe_mock, mock.patch.object(
            backend_app, "_perform_human_ops_click", new=mock.AsyncMock(return_value={"ok": True})
        ) as click_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开 Dock 里的 Chrome", "session_id": "neo-react-think-observe"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 3)
        observe_mock.assert_awaited_once()
        click_mock.assert_not_awaited()
        events = _sse_events(body)
        phase_names = [data["name"] for name, data in events if name == "phase"]
        self.assertIn("brain_react", phase_names)
        self.assertIn("human_ops_observe", phase_names)
        self.assertIn("human_ops_review", phase_names)
        approvals = [data for name, data in events if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["preview"]["x"], 382)
        self.assertNotIn("done", [name for name, _ in events])

    def test_chat_stream_react_corrects_in_progress_say_for_operation_goal(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        unfinished_say = SimpleNamespace(
            text="Chrome 在 Dock 里。",
            decision=BrainDecision.say(
                "Chrome 在 Dock 里。",
                goal={"objective": "打开 Dock 里的 Chrome", "status": "in_progress", "next": "propose_act"},
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        propose = SimpleNamespace(
            text="Propose click",
            decision=BrainDecision.propose_act(
                "click",
                {"x": 382, "y": 930, "label": "Dock Chrome 图标"},
                goal={"objective": "打开 Dock 里的 Chrome", "status": "handoff_review", "next": "human_ops_review"},
            ),
            provider="openai_compatible",
            model="neo-model",
        )

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[unfinished_say, propose])) as run_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开 Dock 里的 Chrome", "session_id": "neo-react-correct-say"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 2)
        second_prompt = run_mock.await_args_list[1].kwargs["user_text"]
        self.assertIn("目标还没有完成", second_prompt)
        events = _sse_events(body)
        approvals = [data for name, data in events if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        token_text = "".join(data.get("text", "") for name, data in events if name == "token")
        self.assertNotIn("Chrome 在 Dock 里。", token_text)

    def test_chat_stream_react_budget_blocks_repeated_think_loop(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        repeated = SimpleNamespace(
            text="还需要继续想。",
            decision=BrainDecision.think(
                "还需要继续想。",
                goal={
                    "objective": "打开 Dock 里的 Chrome",
                    "status": "in_progress",
                    "missing": ["Chrome 图标中心点坐标"],
                    "next": "think",
                },
                next_kind="think",
            ),
            provider="openai_compatible",
            model="neo-model",
        )

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(return_value=repeated)) as run_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开 Dock 里的 Chrome", "session_id": "neo-react-budget"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 4)
        events = _sse_events(body)
        self.assertNotIn("approval_required", [name for name, _ in events])
        done = [data for name, data in events if name == "done"][-1]
        self.assertEqual(done["decision"]["kind"], "say")
        self.assertEqual(done["decision"]["payload"]["goal"]["status"], "blocked")
        self.assertIn("还缺少", done["text"])

    def test_open_app_request_is_desktop_action_and_observe_asks_for_clickable_entry(self) -> None:
        self.assertTrue(backend_app._looks_like_desktop_action_request("打开微信"))
        self.assertTrue(backend_app._looks_like_click_request("打开微信"))

        prompt = backend_app._default_observe_prompt_for_request("打开微信", "打开微信")

        self.assertIn("可点击", prompt)
        self.assertIn("x 和 y", prompt)
        self.assertIn("macOS", prompt)

    def test_wechat_reply_request_is_desktop_action_without_forcing_click_coordinates(self) -> None:
        user_text = "根据张三的微信聊天信息回复张三"

        self.assertTrue(backend_app._looks_like_desktop_action_request(user_text))
        self.assertFalse(backend_app._looks_like_click_request(user_text))

        prompt = backend_app._default_observe_prompt_for_request(user_text, user_text)

        self.assertIn("微信", prompt)
        self.assertIn("联系人", prompt)
        self.assertIn("聊天内容", prompt)
        self.assertIn("输入框", prompt)
        self.assertNotIn("可点击中心点", prompt)

    def test_observe_result_extracts_surface_and_affordance_for_dock_app_icon(self) -> None:
        frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,iVBORw0KGgo=",
            "capture_backend": "macos_screencapture",
            "display_layout": [{"x": 0, "y": 0, "width": 1470, "height": 956}],
            "active_observation": {"target_hint": "打开微信"},
        }

        class FakeAnalyzer:
            def __init__(self, config):
                self.config = config

            def enrich_payload(self, payload):
                return SimpleNamespace(
                    payload={
                        **payload,
                        "observe_answer": "Dock 中可见微信图标，可点击中心点的 macOS 屏幕坐标为 x=520, y=930。",
                        "observations": [
                            {
                                "claim": "Dock 中可见微信图标，可点击中心点的 macOS 屏幕坐标为 x=520, y=930。",
                                "source": "fake-vlm",
                            }
                        ],
                        "analysis": {"status": "ok"},
                    },
                    status={"status": "ok"},
                )

        with mock.patch.object(backend_app, "_send_desktop_command", new=mock.AsyncMock(return_value={"frame": frame})), mock.patch.object(
            backend_app, "VisionAnalyzer", new=FakeAnalyzer, create=True
        ):
            result = asyncio.run(
                backend_app._perform_human_ops_observe(
                    BrainDecision.observe(
                        "打开微信",
                        observe_prompt="请找到打开微信的可点击入口，并给出可点击中心点的 macOS 屏幕坐标 x 和 y。",
                    ),
                    {
                        "observe_screen": True,
                        "observe_model": {
                            "enabled": True,
                            "provider": "openai_compatible",
                            "model_endpoint": "https://observe.example",
                            "model_name": "vlm",
                        },
                    },
                )
            )

        self.assertEqual(result["surface"]["kind"], "desktop_gui")
        self.assertEqual(result["surface"]["region"], "dock")
        affordance = result["affordances"][0]
        self.assertEqual(affordance["kind"], "app_icon")
        self.assertEqual(affordance["label"], "微信")
        self.assertIn("click_to_open", affordance["supports"])
        self.assertEqual(affordance["location"], {"x": 520, "y": 930})

    def test_observe_prompt_uses_goal_missing_coordinates_for_narrow_click_followup(self) -> None:
        prompt = backend_app._observe_prompt_from_decision(
            BrainDecision.observe(
                "screen",
                goal={
                    "objective": "打开微信并回复张三",
                    "status": "in_progress",
                    "stage": "launch_app",
                    "evidence": "微信图标在 Dock 栏可见",
                    "missing": "微信图标的中心点坐标",
                    "next": "click_to_open_wechat",
                },
            ),
            "screen",
        )

        self.assertIn("微信图标", prompt)
        self.assertIn("可点击中心点", prompt)
        self.assertIn("x", prompt)
        self.assertIn("y", prompt)
        self.assertIn("不要描述其他内容", prompt)

    def test_human_ops_observe_sends_narrow_goal_coordinate_prompt_to_vlm(self) -> None:
        frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,iVBORw0KGgo=",
            "capture_backend": "macos_screencapture",
            "display_layout": [{"x": 0, "y": 0, "width": 1470, "height": 956}],
            "active_observation": {"target_hint": "screen"},
        }

        class FakeAnalyzer:
            frames: list[dict] = []

            def __init__(self, config):
                self.config = config

            def enrich_payload(self, payload):
                FakeAnalyzer.frames.append(payload)
                return SimpleNamespace(
                    payload={
                        **payload,
                        "observe_answer": "Dock 中可见微信图标，位于 Steam 图标和 QQ 图标之间。",
                        "observations": [
                            {
                                "claim": "Dock 中可见微信图标，位于 Steam 图标和 QQ 图标之间。",
                                "source": "fake-vlm",
                            }
                        ],
                        "analysis": {"status": "ok"},
                    },
                    status={"status": "ok"},
                )

        with mock.patch.object(backend_app, "_send_desktop_command", new=mock.AsyncMock(return_value={"frame": frame})), mock.patch.object(
            backend_app, "VisionAnalyzer", new=FakeAnalyzer, create=True
        ):
            result = asyncio.run(
                backend_app._perform_human_ops_observe(
                    BrainDecision.observe(
                        "screen",
                        goal={
                            "objective": "打开微信并回复张三",
                            "status": "in_progress",
                            "stage": "launch_app",
                            "evidence": "微信图标在 Dock 栏可见",
                            "missing": "微信图标的中心点坐标",
                            "next": "click_to_open_wechat",
                        },
                    ),
                    {
                        "observe_screen": True,
                        "observe_model": {
                            "enabled": True,
                            "provider": "openai_compatible",
                            "model_endpoint": "https://observe.example",
                            "model_name": "vlm",
                        },
                    },
                )
            )

        observe_prompt = FakeAnalyzer.frames[0]["active_observation"]["observe_prompt"]
        self.assertIn("微信图标", observe_prompt)
        self.assertIn("可点击中心点", observe_prompt)
        self.assertIn("不要描述其他内容", observe_prompt)
        self.assertEqual(result["coordinate_status"]["status"], "incomplete")
        self.assertIn("observe_coordinate_incomplete", result["unknowns"])

    def test_human_ops_observe_uses_longer_active_vision_capture_timeout(self) -> None:
        frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,iVBORw0KGgo=",
            "capture_backend": "macos_screencapture",
            "display_layout": [{"x": 0, "y": 0, "width": 1470, "height": 956}],
        }
        send_mock = mock.AsyncMock(return_value={"frame": frame})

        with mock.patch.object(backend_app, "_send_desktop_command", new=send_mock):
            asyncio.run(
                backend_app._perform_human_ops_observe(
                    BrainDecision.observe("打开微信"),
                    {
                        "observe_screen": True,
                        "observe_model": {"enabled": False},
                    },
                )
            )

        self.assertGreaterEqual(send_mock.await_args.kwargs["timeout_sec"], 12)

    def test_goal_objective_stays_target_hint_for_screen_observe_fallbacks(self) -> None:
        decision = BrainDecision.observe(
            "screen",
            goal={
                "objective": "打开微信并回复张三",
                "status": "in_progress",
                "stage": "launch_app",
                "missing": "微信图标的坐标",
                "next": "click_to_open_wechat",
            },
        )
        frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,iVBORw0KGgo=",
            "active_observation": {
                "target_hint": "screen",
                "screen_bounds": {"x": 0, "y": 0, "width": 1470, "height": 956},
                "verify_result": {"status": "captured", "method": "macos_screencapture"},
            },
        }

        enriched = backend_app._frame_with_observe_prompt(frame, decision, "screen")
        self.assertIn("微信", enriched["active_observation"]["target_hint"])
        context = backend_app._infer_computer_use_context(
            "我试着观察了屏幕，但还不能确认内容：VLM analyzer failed: The read operation timed out",
            enriched,
            "screen",
        )
        affordances = {item["kind"]: item for item in context["affordances"]}
        self.assertIn("screen_edge", affordances)

    def test_observe_result_does_not_fabricate_dock_icon_from_negative_visibility(self) -> None:
        context = backend_app._infer_computer_use_context(
            "屏幕上目前没有显示微信图标，因为当前整个屏幕被 VS Code 窗口占满，未显示 Dock 栏或桌面。",
            {"active_observation": {"target_hint": "打开微信"}},
            "打开微信",
        )

        affordances = {item["kind"]: item for item in context["affordances"]}
        self.assertNotEqual(context["surface"].get("region"), "dock")
        self.assertNotIn("app_icon", affordances)

    def test_dock_item_target_candidate_supplies_app_icon_location_when_vlm_omits_coordinates(self) -> None:
        context = backend_app._infer_computer_use_context(
            "Dock 栏上的微信图标可见，但没有给出坐标。",
            {
                "active_observation": {
                    "target_hint": "打开微信",
                    "target_candidates": [
                        {
                            "source": "dock_item",
                            "app": "微信",
                            "title": "微信",
                            "focus_point": {"x": 963, "y": 908},
                            "bounds": {"x": 935, "y": 872, "width": 57, "height": 73},
                        }
                    ],
                }
            },
            "打开微信",
        )

        affordances = {item["kind"]: item for item in context["affordances"]}
        self.assertEqual(affordances["app_icon"]["location"], {"x": 963, "y": 908})

    def test_dock_item_target_candidate_overrides_vlm_app_icon_coordinate(self) -> None:
        context = backend_app._infer_computer_use_context(
            "Dock 栏上的微信图标可见，可点击中心点的 macOS 屏幕坐标为 x=650, y=920。",
            {
                "active_observation": {
                    "target_hint": "打开微信",
                    "target_candidates": [
                        {
                            "source": "dock_item",
                            "app": "微信",
                            "title": "微信",
                            "focus_point": {"x": 963, "y": 908},
                            "bounds": {"x": 935, "y": 872, "width": 57, "height": 73},
                        }
                    ],
                }
            },
            "打开微信",
        )

        affordances = {item["kind"]: item for item in context["affordances"]}
        self.assertEqual(affordances["app_icon"]["location"], {"x": 963, "y": 908})

    def test_hidden_dock_observe_exposes_screen_edge_reveal_affordance(self) -> None:
        context = backend_app._infer_computer_use_context(
            "当前浏览器窗口占满屏幕，未显示 Dock 栏，也没有看到微信图标。",
            {
                "active_observation": {
                    "target_hint": "打开微信",
                    "screen_bounds": {"x": 0, "y": 0, "width": 1470, "height": 956},
                }
            },
            "打开微信",
        )

        affordances = {item["kind"]: item for item in context["affordances"]}
        self.assertIn("screen_edge", affordances)
        self.assertEqual(affordances["screen_edge"]["purpose"], "reveal_hidden_dock")
        self.assertIn("click", affordances["screen_edge"]["supports"])
        self.assertEqual(affordances["screen_edge"]["location"], {"x": 735, "y": 954})

    def test_vlm_timeout_still_exposes_screen_edge_reveal_affordance_for_app_launch(self) -> None:
        context = backend_app._infer_computer_use_context(
            "我试着观察了屏幕，但还不能确认内容：VLM analyzer failed: The read operation timed out",
            {
                "active_observation": {
                    "target_hint": "打开微信",
                    "screen_bounds": {"x": 0, "y": 0, "width": 1470, "height": 956},
                    "target_candidates": [
                        {"source": "running_app", "app": "WeChat", "title": "WeChat"},
                    ],
                    "verify_result": {"status": "captured", "method": "macos_screencapture"},
                }
            },
            "打开微信",
        )

        affordances = {item["kind"]: item for item in context["affordances"]}
        self.assertIn("screen_edge", affordances)
        self.assertEqual(affordances["screen_edge"]["purpose"], "reveal_hidden_dock")
        self.assertIn("click", affordances["screen_edge"]["supports"])
        self.assertEqual(affordances["screen_edge"]["location"], {"x": 735, "y": 954})

    def test_infers_wechat_contact_and_input_affordances_from_observe_text(self) -> None:
        context = backend_app._infer_computer_use_context(
            (
                "微信窗口已打开，左侧搜索框可输入联系人。"
                "联系人“张三”的聊天条目可点击，中心点的 macOS 屏幕坐标为 x=240, y=310。"
                "底部聊天输入框可见，中心点的 macOS 屏幕坐标为 x=760, y=905。"
            ),
            {"active_observation": {"target_hint": "根据张三的聊天信息回复张三"}},
            "根据张三的聊天信息回复张三",
        )

        self.assertEqual(context["surface"]["kind"], "wechat_gui")
        affordances = {item["kind"]: item for item in context["affordances"]}
        self.assertIn("search_field", affordances)
        self.assertIn("chat_thread", affordances)
        self.assertIn("chat_input", affordances)
        self.assertIn("type_text", affordances["search_field"]["supports"])
        self.assertIn("click", affordances["chat_thread"]["supports"])
        self.assertEqual(affordances["chat_thread"]["label"], "张三")
        self.assertEqual(affordances["chat_thread"]["location"], {"x": 240, "y": 310})
        self.assertIn("click", affordances["chat_input"]["supports"])
        self.assertIn("type_text", affordances["chat_input"]["supports"])
        self.assertIn("key_press", affordances["chat_input"]["supports"])
        self.assertEqual(affordances["chat_input"]["location"], {"x": 760, "y": 905})

    def test_wechat_search_field_does_not_fabricate_target_chat_thread(self) -> None:
        context = backend_app._infer_computer_use_context(
            "微信窗口已打开，左侧搜索框可输入联系人，搜索框中心点的 macOS 屏幕坐标为 x=180, y=120。",
            {"active_observation": {"target_hint": "打开微信并根据张三聊天信息回复张三"}},
            "打开微信并根据张三聊天信息回复张三",
        )

        affordances = {item["kind"]: item for item in context["affordances"]}
        self.assertIn("search_field", affordances)
        self.assertEqual(affordances["search_field"]["location"], {"x": 180, "y": 120})
        self.assertNotIn("chat_thread", affordances)

    def test_infers_wechat_chat_context_from_observe_text(self) -> None:
        context = backend_app._infer_computer_use_context(
            (
                "微信聊天窗口显示联系人张三。"
                "最近聊天内容：张三：下午3点记得带资料；我：收到。"
                "底部聊天输入框可见，发送入口可用。"
            ),
            {"active_observation": {"target_hint": "根据张三的聊天信息回复张三"}},
            "根据张三的聊天信息回复张三",
        )

        chat_context = context["chat_context"]
        self.assertEqual(chat_context["contact"], "张三")
        self.assertEqual(
            chat_context["recent_messages"],
            [
                {"speaker": "张三", "text": "下午3点记得带资料"},
                {"speaker": "我", "text": "收到"},
            ],
        )
        self.assertTrue(chat_context["input_ready"])
        self.assertFalse(chat_context["input_focused"])
        self.assertTrue(chat_context["send_ready"])

    def test_infers_wechat_chat_context_marks_focused_input_from_cursor_text(self) -> None:
        context = backend_app._infer_computer_use_context(
            (
                "微信聊天窗口显示联系人张三。"
                "底部聊天输入框已聚焦，里面有光标，可以直接输入回复。"
            ),
            {"active_observation": {"target_hint": "根据张三的聊天信息回复张三"}},
            "根据张三的聊天信息回复张三",
        )

        chat_context = context["chat_context"]
        self.assertTrue(chat_context["input_ready"])
        self.assertTrue(chat_context["input_focused"])

    def test_structured_computer_use_context_text_includes_chat_context(self) -> None:
        context_text = backend_app._computer_use_context_text(
            {
                "surface": {"kind": "wechat_gui", "region": "chat", "confidence": 0.76},
                "affordances": [{"kind": "chat_input", "supports": ["type_text", "key_press"]}],
                "chat_context": {
                    "contact": "张三",
                    "recent_messages": [{"speaker": "张三", "text": "下午3点记得带资料"}],
                    "input_ready": True,
                    "send_ready": True,
                },
            }
        )

        self.assertIn("Structured computer-use context", context_text)
        self.assertIn("chat_context", context_text)
        self.assertIn("下午3点记得带资料", context_text)

    def test_infers_terminal_prompt_affordance_from_observe_text(self) -> None:
        context = backend_app._infer_computer_use_context(
            "终端窗口处于前台，底部 shell prompt 等待输入命令。",
            {},
            "在终端输入命令并回车",
        )

        self.assertEqual(context["surface"]["kind"], "terminal_shell")
        affordance = context["affordances"][0]
        self.assertEqual(affordance["kind"], "terminal_prompt")
        self.assertIn("type_text", affordance["supports"])
        self.assertIn("key_press", affordance["supports"])

    def test_chat_stream_observe_followup_receives_surface_affordance_context(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(
            text="Observe WeChat",
            decision=BrainDecision.observe("打开微信"),
            provider="openai_compatible",
            model="neo-model",
        )
        followup = SimpleNamespace(
            text="Propose open WeChat",
            decision=BrainDecision.propose_act(
                "click",
                {"x": 520, "y": 930, "label": "Dock 微信图标"},
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": "Dock 中可见微信图标，可点击中心点的 macOS 屏幕坐标为 x=520, y=930。",
            "observations": [{"claim": "Dock 中可见微信图标，可点击中心点 x=520, y=930。"}],
            "unknowns": [],
            "surface": {"kind": "desktop_gui", "region": "dock", "confidence": 0.86},
            "affordances": [
                {
                    "kind": "app_icon",
                    "label": "微信",
                    "supports": ["click_to_open"],
                    "location": {"x": 520, "y": 930},
                    "confidence": 0.82,
                }
            ],
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[completion, followup])) as run_mock, mock.patch.object(
            backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
        ):
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开微信", "session_id": "neo-surface-affordance"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 2)
        followup_prompt = run_mock.await_args_list[1].kwargs["user_text"]
        self.assertIn("Structured computer-use context", followup_prompt)
        self.assertIn("desktop_gui", followup_prompt)
        self.assertIn("click_to_open", followup_prompt)
        approvals = [data for name, data in _sse_events(body) if name == "approval_required"]
        self.assertEqual(approvals[0]["preview"]["x"], 520)

    def test_chat_stream_can_review_hidden_dock_reveal_click_from_screen_edge_affordance(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(
            text="Observe hidden Dock",
            decision=BrainDecision.observe("打开微信"),
            provider="openai_compatible",
            model="neo-model",
        )
        followup = SimpleNamespace(
            text="Reveal hidden Dock",
            decision=BrainDecision.propose_act(
                "click",
                {
                    "x": 735,
                    "y": 954,
                    "label": "屏幕底边（显示隐藏 Dock）",
                    "continue_after_approval": True,
                },
                goal={
                    "objective": "打开微信",
                    "status": "handoff_review",
                    "stage": "reveal_dock",
                    "next": "launch_app",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": "当前浏览器窗口占满屏幕，未显示 Dock 栏，也没有看到微信图标。",
            "observations": [{"claim": "Dock 当前不可见"}],
            "unknowns": [],
            "surface": {"kind": "browser_page", "confidence": 0.7},
            "affordances": [
                {
                    "kind": "screen_edge",
                    "label": "屏幕底边（显示隐藏 Dock）",
                    "purpose": "reveal_hidden_dock",
                    "target_app": "微信",
                    "supports": ["click"],
                    "location": {"x": 735, "y": 954},
                    "confidence": 0.58,
                }
            ],
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[completion, followup])) as run_mock, mock.patch.object(
            backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
        ):
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开微信", "session_id": "neo-hidden-dock-reveal"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 2)
        followup_prompt = run_mock.await_args_list[1].kwargs["user_text"]
        self.assertIn("screen_edge", followup_prompt)
        self.assertIn("reveal_hidden_dock", followup_prompt)
        approvals = [data for name, data in _sse_events(body) if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["preview"]["x"], 735)
        self.assertEqual(approvals[0]["preview"]["y"], 954)
        self.assertIn("显示隐藏 Dock", approvals[0]["tools"][0]["summary"])

    def test_chat_stream_does_not_block_coordinate_incomplete_when_screen_edge_is_reviewable(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(
            text="Observe screen",
            decision=BrainDecision.observe(
                "screen",
                goal={
                    "objective": "打开微信并回复张三",
                    "status": "in_progress",
                    "stage": "launch_app",
                    "missing": "微信图标的坐标",
                    "next": "click_to_open_wechat",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        followup = SimpleNamespace(
            text="Reveal Dock",
            decision=BrainDecision.propose_act(
                "click",
                {"x": 735, "y": 954, "label": "屏幕底边（显示隐藏 Dock）", "continue_after_approval": True},
                goal={
                    "objective": "打开微信并回复张三",
                    "status": "handoff_review",
                    "stage": "reveal_dock",
                    "next": "launch_app",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": "我试着观察了屏幕，但还不能确认内容：VLM analyzer failed: The read operation timed out",
            "observations": [],
            "unknowns": ["VLM analyzer failed: The read operation timed out", "observe_coordinate_incomplete"],
            "coordinate_status": {"status": "incomplete", "reason": "missing complete x/y"},
            "surface": {"kind": "unknown", "confidence": 0.2},
            "affordances": [
                {
                    "kind": "screen_edge",
                    "label": "屏幕底边（显示隐藏 Dock）",
                    "purpose": "reveal_hidden_dock",
                    "supports": ["click"],
                    "location": {"x": 735, "y": 954},
                    "confidence": 0.58,
                }
            ],
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[completion, followup])) as run_mock, mock.patch.object(
            backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
        ):
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开微信并根据张三聊天信息回复张三", "session_id": "neo-screen-edge-coordinate-incomplete"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 2)
        approvals = [data for name, data in _sse_events(body) if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["preview"]["x"], 735)

    def test_observe_text_does_not_surface_metadata_timeout_as_screenshot_failure(self) -> None:
        text = backend_app._observation_text_from_result(
            {
                "frame": {
                    "mime_type": "image/jpeg",
                    "data_url": "data:image/jpeg;base64,abc",
                    "capture_backend": "macos_screencapture",
                    "unknowns": ["无法读取 macOS 前台应用元数据：Command '['osascript'] timed out after 0.8 seconds"],
                }
            }
        )

        self.assertIn("截取了当前屏幕", text)
        self.assertNotIn("osascript", text)
        self.assertNotIn("无法读取 macOS 前台应用元数据", text)

    def test_human_ops_observe_passes_natural_language_question_to_model(self) -> None:
        class FakeAnalyzer:
            instances: list["FakeAnalyzer"] = []
            frames: list[dict] = []

            def __init__(self, config):
                self.config = config
                FakeAnalyzer.instances.append(self)

            def enrich_payload(self, frame):
                FakeAnalyzer.frames.append(frame)
                enriched = {
                    **frame,
                    "observe_answer": "Dock 中可见系统设置图标，可点击中心点大约是 x=452, y=1187。",
                    "observations": [{"claim": "Dock 中可见系统设置图标，可点击中心点大约是 x=452, y=1187。", "source": "fake-vlm"}],
                    "analysis": {"status": "ok"},
                }
                return SimpleNamespace(payload=enriched, status={"status": "ok"})

        frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,iVBORw0KGgo=",
            "active_observation": {"target_hint": "点击 Dock 栏里的设置"},
        }
        observe_config = {
            "observe_screen": True,
            "observe_model": {
                "enabled": True,
                "provider": "openai_compatible",
                "model_endpoint": "https://observe.example/v1",
                "model_name": "fast-vlm",
                "api_key": "observe-secret",
            },
        }

        with mock.patch.object(backend_app, "_send_desktop_command", new=mock.AsyncMock(return_value={"frame": frame})), mock.patch.object(
            backend_app, "VisionAnalyzer", new=FakeAnalyzer, create=True
        ):
            decision = BrainDecision.observe(
                "点击 Dock 栏里的设置",
                observe_prompt="我需要找到 Dock 栏里的设置应用的位置，请观看屏幕图像并用自然语言告诉我可点击中心坐标。",
            )
            result = asyncio.run(backend_app._perform_human_ops_observe(decision, observe_config))

        self.assertEqual(FakeAnalyzer.instances[0].config["provider"], "openai_compatible_vlm")
        self.assertEqual(FakeAnalyzer.instances[0].config["base_url"], "https://observe.example/v1")
        self.assertEqual(FakeAnalyzer.instances[0].config["model"], "fast-vlm")
        self.assertEqual(FakeAnalyzer.instances[0].config["api_key"], "observe-secret")
        self.assertEqual(FakeAnalyzer.instances[0].config["timeout_sec"], 90.0)
        active = FakeAnalyzer.frames[0]["active_observation"]
        self.assertIn("设置应用的位置", active["observe_prompt"])
        self.assertIn("自然语言", active["observe_prompt"])
        self.assertIn("系统设置图标", result["text"])
        self.assertIn("x=452", result["text"])

    def test_observe_frame_adds_coordinate_contract_for_vlm(self) -> None:
        frame = {
            "mime_type": "image/jpeg",
            "data_url": "data:image/jpeg;base64,screen",
            "image_width": 3456,
            "image_height": 2234,
            "display_layout": [{"x": 0, "y": 0, "width": 1728, "height": 1117}],
            "active_observation": {"target_hint": "Dock 设置"},
        }

        enriched = backend_app._frame_with_observe_prompt(
            frame,
            BrainDecision.observe(
                "Dock 设置",
                observe_prompt="请找到 Dock 设置图标，并返回可点击中心坐标。",
            ),
            "点击 Dock 设置",
        )

        active = enriched["active_observation"]
        self.assertEqual(active["image_resolution"], {"width": 3456, "height": 2234})
        self.assertEqual(active["screen_resolution"], {"width": 1728, "height": 1117})
        self.assertEqual(active["screen_bounds"], {"x": 0, "y": 0, "width": 1728, "height": 1117})
        self.assertEqual(active["coordinate_space"], "macos_screen_points")
        self.assertAlmostEqual(active["coordinate_scale"]["image_to_screen_x"], 0.5)
        self.assertAlmostEqual(active["coordinate_scale"]["image_to_screen_y"], 0.5)
        self.assertAlmostEqual(active["coordinate_scale"]["screen_to_image_x"], 2.0)
        self.assertAlmostEqual(active["coordinate_scale"]["screen_to_image_y"], 2.0)

    def test_chat_stream_observe_followup_receives_coordinate_conversion_context(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    },
                    "human_ops": {
                        "observe_model": {
                            "enabled": True,
                            "provider": "openai_compatible",
                            "model_endpoint": "https://observe.example/v1",
                            "model_name": "fast-vlm",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        class FakeAnalyzer:
            def __init__(self, config):
                self.config = config

            def enrich_payload(self, frame):
                return SimpleNamespace(
                    payload={
                        **frame,
                        "observe_answer": "Dock 中可见系统设置图标；图像里中心点大约是 x=476, y=1185。",
                        "observations": [{"claim": "图像里中心点大约是 x=476, y=1185。", "source": "fake-vlm"}],
                        "analysis": {"status": "ok"},
                    },
                    status={"status": "ok"},
                )

        calls: list[str] = []

        async def fake_run_brain_turn(config, *, user_text, request_system_prompt=""):
            calls.append(user_text)
            if len(calls) == 1:
                return SimpleNamespace(
                    text="Observe Dock settings",
                    decision=BrainDecision.observe(
                        "Dock 设置",
                        observe_prompt="请找到 Dock 系统设置图标，并给出可点击中心坐标。",
                    ),
                    provider="openai_compatible",
                    model="neo-model",
                )
            return SimpleNamespace(
                text="Propose converted click",
                decision=BrainDecision.propose_act("click", {"x": 364, "y": 907, "label": "Dock 系统设置"}),
                provider="openai_compatible",
                model="neo-model",
            )

        frame = {
            "mime_type": "image/jpeg",
            "data_url": "data:image/jpeg;base64,screen",
            "image_width": 1920,
            "image_height": 1249,
            "display_layout": [{"x": 0, "y": 0, "width": 1470, "height": 956}],
            "active_observation": {"target_hint": "Dock 设置"},
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=fake_run_brain_turn), mock.patch.object(
            backend_app, "_send_desktop_command", new=mock.AsyncMock(return_value={"frame": frame})
        ), mock.patch.object(backend_app, "VisionAnalyzer", new=FakeAnalyzer, create=True):
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "点击 Dock 系统设置", "session_id": "neo-observe-coordinate-context"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        self.assertEqual(len(calls), 2)
        followup_prompt = calls[1]
        self.assertIn("Observe coordinate context", followup_prompt)
        self.assertIn("Attached image pixels: 1920x1249", followup_prompt)
        self.assertIn("macOS screen bounds: origin=(0, 0), size=1470x956 points", followup_prompt)
        self.assertIn("image-to-screen scale: x=0.765625, y=0.765412", followup_prompt)
        self.assertIn("If observe coordinates are screenshot/image pixels", followup_prompt)
        approvals = [data for name, data in _sse_events(body) if name == "approval_required"]
        self.assertEqual(approvals[0]["preview"]["x"], 364)
        self.assertEqual(approvals[0]["preview"]["y"], 907)

    def test_human_ops_observe_defaults_to_longer_model_timeout(self) -> None:
        config = backend_app._observe_model_analyzer_config(
            {
                "observe_model": {
                    "enabled": True,
                    "provider": "openai_compatible",
                    "model_endpoint": "https://observe.example/v1",
                    "model_name": "fast-vlm",
                }
            }
        )

        self.assertEqual(config["timeout_sec"], 90.0)

    def test_human_ops_observe_allows_slow_image_analysis_timeout(self) -> None:
        config = backend_app._observe_model_analyzer_config(
            {
                "observe_model": {
                    "enabled": True,
                    "provider": "openai_compatible",
                    "model_endpoint": "https://observe.example/v1",
                    "model_name": "fast-vlm",
                    "timeout_sec": 240,
                }
            }
        )

        self.assertEqual(config["timeout_sec"], 240.0)

    def test_human_ops_observe_preserves_custom_observe_timeout(self) -> None:
        class FakeAnalyzer:
            instances: list["FakeAnalyzer"] = []

            def __init__(self, config):
                self.config = config
                FakeAnalyzer.instances.append(self)

            def enrich_payload(self, frame):
                return SimpleNamespace(
                    payload={**frame, "observe_answer": "屏幕内容可见。", "analysis": {"status": "ok"}},
                    status={"status": "ok"},
                )

        frame = {"mime_type": "image/png", "data_url": "data:image/png;base64,iVBORw0KGgo="}
        observe_config = {
            "observe_screen": True,
            "observe_model": {
                "enabled": True,
                "provider": "openai_compatible",
                "model_endpoint": "https://observe.example/v1",
                "model_name": "fast-vlm",
                "timeout_sec": 80,
            },
        }

        with mock.patch.object(backend_app, "_send_desktop_command", new=mock.AsyncMock(return_value={"frame": frame})), mock.patch.object(
            backend_app, "VisionAnalyzer", new=FakeAnalyzer, create=True
        ):
            result = asyncio.run(backend_app._perform_human_ops_observe(BrainDecision.observe("screen"), observe_config))

        self.assertEqual(FakeAnalyzer.instances[0].config["timeout_sec"], 80.0)
        self.assertIn("屏幕内容可见", result["text"])

    def test_human_ops_observe_builds_non_click_natural_language_question(self) -> None:
        class FakeAnalyzer:
            frames: list[dict] = []

            def __init__(self, config):
                self.config = config

            def enrich_payload(self, frame):
                FakeAnalyzer.frames.append(frame)
                return SimpleNamespace(
                    payload={
                        **frame,
                        "observe_answer": "屏幕右侧可见聊天窗口。",
                        "observations": [{"claim": "屏幕右侧可见聊天窗口", "source": "fake-vlm"}],
                        "analysis": {"status": "ok"},
                    },
                    status={"status": "ok"},
                )

        frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,iVBORw0KGgo=",
            "display_layout": [{"x": 0, "y": 0, "width": 1728, "height": 1117}],
            "active_observation": {"target_hint": "看一下当前屏幕上有什么"},
        }
        observe_config = {
            "observe_screen": True,
            "observe_model": {
                "enabled": True,
                "provider": "openai_compatible",
                "model_endpoint": "https://observe.example/v1",
                "model_name": "fast-vlm",
            },
        }

        with mock.patch.object(backend_app, "_send_desktop_command", new=mock.AsyncMock(return_value={"frame": frame})), mock.patch.object(
            backend_app, "VisionAnalyzer", new=FakeAnalyzer, create=True
        ):
            decision = BrainDecision.observe(
                "看一下当前屏幕上有什么",
                observe_prompt="我需要了解当前屏幕主要内容，请概括可见窗口、文字和状态。",
            )
            result = asyncio.run(backend_app._perform_human_ops_observe(decision, observe_config))

        active = FakeAnalyzer.frames[0]["active_observation"]
        self.assertIn("概括可见窗口", active["observe_prompt"])
        self.assertEqual(active["screen_resolution"], {"width": 1728, "height": 1117})
        self.assertIn("聊天窗口", result["text"])

    def test_click_observe_without_observe_model_reports_model_requirement(self) -> None:
        frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,iVBORw0KGgo=",
            "capture_backend": "macos_screencapture",
            "active_observation": {"target_hint": "点击 Dock 栏里的设置"},
        }

        with mock.patch.object(backend_app, "_send_desktop_command", new=mock.AsyncMock(return_value={"frame": frame})):
            result = asyncio.run(
                backend_app._perform_human_ops_observe(
                    BrainDecision.observe(
                        "点击 Dock 栏里的设置",
                        observe_prompt="我需要找到 Dock 栏里的设置应用的位置，请用自然语言告诉我坐标。",
                    ),
                    {"observe_screen": True, "observe_model": {"enabled": False}},
                )
            )

        self.assertIn("需要配置 Human Ops observe 模型", result["text"])
        self.assertIn("observe_model disabled", result["unknowns"])

    def test_non_click_observe_without_observe_model_reports_model_requirement(self) -> None:
        frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,iVBORw0KGgo=",
            "capture_backend": "macos_screencapture",
            "active_observation": {"target_hint": "你能看到的屏幕内容有哪些"},
        }

        with mock.patch.object(backend_app, "_send_desktop_command", new=mock.AsyncMock(return_value={"frame": frame})):
            result = asyncio.run(
                backend_app._perform_human_ops_observe(
                    BrainDecision.observe(
                        "你能看到的屏幕内容有哪些",
                        observe_prompt="请观看截图并如实回答可见内容。",
                    ),
                    {"observe_screen": True, "observe_model": {"enabled": False}},
                )
            )

        self.assertIn("截图成功", result["text"])
        self.assertIn("Human Ops observe 模型未启用", result["text"])
        self.assertIn("observe_model disabled", result["unknowns"])

    def test_observe_text_reports_empty_observe_model_result(self) -> None:
        text = backend_app._observation_text_from_result(
            {
                "frame": {
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,iVBORw0KGgo=",
                    "capture_backend": "macos_screencapture",
                    "analysis": {
                        "enabled": True,
                        "provider": "openai_compatible_vlm",
                        "status": "ok",
                        "last_error": "",
                        "observations_added": 0,
                        "unknowns_added": 0,
                    },
                }
            }
        )

        self.assertIn("observe 模型", text)
        self.assertIn("没有返回可见内容", text)

    def test_click_observe_marks_incomplete_coordinate_answer(self) -> None:
        frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,iVBORw0KGgo=",
            "capture_backend": "macos_screencapture",
            "display_layout": [{"x": 0, "y": 0, "width": 1470, "height": 956}],
            "active_observation": {"target_hint": "点击 Dock 栏里的微信"},
        }

        class FakeAnalyzer:
            def __init__(self, config):
                self.config = config

            def enrich_payload(self, payload):
                return SimpleNamespace(
                    payload={
                        **payload,
                        "observe_answer": "Dock 中可见微信图标，可点击中心点的 macOS 屏幕坐标为 x=",
                        "observations": [
                            {
                                "claim": "Dock 中可见微信图标，可点击中心点的 macOS 屏幕坐标为 x=",
                                "source": "fake-vlm",
                            }
                        ],
                        "analysis": {"status": "ok"},
                    },
                    status={"status": "ok"},
                )

        with mock.patch.object(backend_app, "_send_desktop_command", new=mock.AsyncMock(return_value={"frame": frame})), mock.patch.object(
            backend_app, "VisionAnalyzer", new=FakeAnalyzer, create=True
        ):
            result = asyncio.run(
                backend_app._perform_human_ops_observe(
                    BrainDecision.observe(
                        "点击 Dock 栏里的微信",
                        observe_prompt="请给出微信图标可点击中心点的完整 x 和 y 坐标。",
                    ),
                    {
                        "observe_screen": True,
                        "observe_model": {
                            "enabled": True,
                            "provider": "openai_compatible",
                            "model_endpoint": "https://observe.example",
                            "model_name": "vlm",
                        },
                    },
                )
            )

        self.assertIn("需要重新观察", result["text"])
        self.assertIn("完整 x 和 y", result["text"])
        self.assertIn("observe_coordinate_incomplete", result["unknowns"])
        self.assertEqual(result["coordinate_status"]["status"], "incomplete")

    def test_chat_stream_preserves_observation_when_followup_brain_returns_empty_text(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(
            text="Observe WeChat",
            decision=BrainDecision.observe(
                "点击 Dock 栏里的微信",
                observe_prompt="请给出微信图标可点击中心点的完整 x 和 y 坐标。",
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": "Dock 中可见微信图标，可点击中心点的 macOS 屏幕坐标为 x=",
            "observations": [{"claim": "Dock 中可见微信图标，可点击中心点的 macOS 屏幕坐标为 x="}],
            "unknowns": ["observe_coordinate_incomplete"],
            "coordinate_status": {"status": "incomplete", "reason": "missing complete x/y"},
        }

        with mock.patch.object(
            backend_app,
            "run_brain_turn",
            new=mock.AsyncMock(side_effect=[completion, BrainLLMError("OpenAI-compatible provider returned empty text.")]),
        ), mock.patch.object(
            backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
        ):
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "请点击 Dock 栏里的微信", "session_id": "neo-incomplete-coordinate"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        events = _sse_events(body)
        self.assertNotIn("approval_required", [name for name, _ in events])
        done = [data for name, data in events if name == "done"][-1]
        self.assertIn("Dock 中可见微信图标", done["text"])
        self.assertIn("完整 x 和 y", done["text"])
        self.assertNotIn("Brain 读取 observe 结果失败", done["text"])
        self.assertEqual(done["decision"]["kind"], "say")

    def test_chat_stream_brain_observe_prompt_drives_openai_observe_model_then_click_review(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    },
                    "human_ops": {
                        "observe_model": {
                            "enabled": True,
                            "provider": "openai_compatible",
                            "model_endpoint": "https://observe.example/v1",
                            "model_name": "fast-vlm",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        class FakeAnalyzer:
            frames: list[dict] = []

            def __init__(self, config):
                self.config = config

            def enrich_payload(self, frame):
                FakeAnalyzer.frames.append(frame)
                return SimpleNamespace(
                    payload={
                        **frame,
                        "observe_answer": "Dock 中可见系统设置图标，可点击中心点大约是 x=452, y=1187。",
                        "observations": [{"claim": "Dock 中可见系统设置图标，可点击中心点大约是 x=452, y=1187。", "source": "fake-vlm"}],
                        "analysis": {"status": "ok"},
                    },
                    status={"status": "ok"},
                )

        completion = SimpleNamespace(
            text="Observe Dock settings",
            decision=BrainDecision.observe(
                "Dock 设置",
                observe_prompt="我需要找到 Dock 栏里的设置应用的位置，请观看屏幕图像并用自然语言告诉我可点击中心坐标。",
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        followup = SimpleNamespace(
            text="Propose click",
            decision=BrainDecision.propose_act("click", {"x": 452, "y": 1187, "label": "Dock 系统设置"}),
            provider="openai_compatible",
            model="neo-model",
        )
        frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,iVBORw0KGgo=",
            "display_layout": [{"x": 0, "y": 0, "width": 1728, "height": 1117}],
            "active_observation": {"target_hint": "Dock 设置"},
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[completion, followup])), mock.patch.object(
            backend_app, "_send_desktop_command", new=mock.AsyncMock(return_value={"frame": frame})
        ), mock.patch.object(backend_app, "VisionAnalyzer", new=FakeAnalyzer, create=True):
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "点击一下 Dock 栏里的设置", "session_id": "neo-brain-observe-click"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        active = FakeAnalyzer.frames[0]["active_observation"]
        self.assertIn("可点击中心坐标", active["observe_prompt"])
        self.assertEqual(active["screen_resolution"], {"width": 1728, "height": 1117})
        approvals = [data for name, data in _sse_events(body) if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["preview"]["x"], 452)
        self.assertEqual(approvals[0]["preview"]["label"], "Dock 系统设置")

    def test_chat_stream_click_observe_with_partial_coordinate_asks_for_complete_xy_without_brain_followup(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(
            text="Observe Dock settings",
            decision=BrainDecision.observe("Dock 设置"),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": "Dock 中可见系统设置图标，可点击中心点大约是 x=452。",
            "observations": [{"claim": "Dock 中可见系统设置图标，可点击中心点大约是 x=452。"}],
            "unknowns": [],
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(return_value=completion)) as run_mock, mock.patch.object(
            backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
        ):
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "点击 Dock 设置", "session_id": "neo-partial-observe-coordinate"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 1)
        events = _sse_events(body)
        self.assertNotIn("approval_required", [name for name, _ in events])
        token_text = "".join(data.get("text", "") for name, data in events if name == "token")
        self.assertIn("Dock 中可见系统设置图标", token_text)
        self.assertIn("需要完整的 x 和 y", token_text)

    def test_chat_stream_click_observe_empty_brain_followup_preserves_observation_and_asks_for_coordinates(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(
            text="Observe Dock settings",
            decision=BrainDecision.observe("Dock 设置"),
            provider="openai_compatible",
            model="neo-model",
        )
        empty_followup = SimpleNamespace(
            text="",
            decision=BrainDecision.say(""),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": "Dock 中可见系统设置图标，但没有给出可点击中心坐标。",
            "observations": [{"claim": "Dock 中可见系统设置图标，但没有给出可点击中心坐标。"}],
            "unknowns": [],
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[completion, empty_followup])):
            with mock.patch.object(backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)):
                with self.client.stream(
                    "POST",
                    "/api/chat/stream",
                    json={"text": "点击 Dock 设置", "session_id": "neo-empty-followup-coordinate"},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    body = resp.read().decode("utf-8")

        events = _sse_events(body)
        self.assertNotIn("approval_required", [name for name, _ in events])
        token_text = "".join(data.get("text", "") for name, data in events if name == "token")
        self.assertIn("Dock 中可见系统设置图标", token_text)
        self.assertIn("需要完整的 x 和 y", token_text)
        self.assertNotIn("Brain 读取 observe 结果失败", token_text)

    def test_chat_stream_verbal_click_request_observes_then_emits_reviewable_click(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(
            text="我已经准备好啦，请批准我执行这个点击操作。",
            decision=BrainDecision.say("我已经准备好啦，请批准我执行这个点击操作。"),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": "我看到 Dock 中的系统设置图标，可点击中心点大约是 x=452, y=1187。",
            "observations": [{"claim": "Dock 中可见系统设置图标，可点击中心点大约是 x=452, y=1187。"}],
            "unknowns": [],
        }
        followup = SimpleNamespace(
            text="Propose click",
            decision=BrainDecision.propose_act("click", {"x": 452, "y": 1187, "label": "Dock 系统设置"}),
            provider="openai_compatible",
            model="neo-model",
        )

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[completion, followup])) as run_mock, mock.patch.object(
            backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
        ) as observe_mock, mock.patch.object(
            backend_app, "_perform_human_ops_click", new=mock.AsyncMock(return_value={"ok": True})
        ) as click_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "试试点一下我的dock栏里的设置这个应用", "session_id": "neo-observe-click"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 2)
        observe_mock.assert_awaited_once()
        click_mock.assert_not_awaited()
        events = _sse_events(body)
        approvals = [data for name, data in events if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["action_type"], "click")
        self.assertEqual(approvals[0]["preview"]["marker"], "red_dot")
        self.assertEqual(approvals[0]["preview"]["x"], 452)
        self.assertEqual(approvals[0]["preview"]["label"], "Dock 系统设置")
        self.assertNotIn("done", [name for name, _ in events])

    def test_chat_stream_click_decision_emits_reviewable_red_dot_without_executing(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(
            text="Propose click",
            decision=BrainDecision.propose_act("click", {"x": 120, "y": 240, "label": "发送按钮"}),
            provider="openai_compatible",
            model="neo-model",
        )

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(return_value=completion)), mock.patch.object(
            backend_app, "_perform_human_ops_click", new=mock.AsyncMock(return_value={"ok": True})
        ) as click_mock:
            with self.client.stream("POST", "/api/chat/stream", json={"text": "点击发送", "session_id": "neo-click"}) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        click_mock.assert_not_awaited()
        events = _sse_events(body)
        approvals = [data for name, data in events if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        approval = approvals[0]
        self.assertEqual(approval["proposal_type"], "act")
        self.assertEqual(approval["action_type"], "click")
        self.assertEqual(approval["preview"]["marker"], "red_dot")
        self.assertEqual(approval["preview"]["x"], 120)
        self.assertTrue(approval["proposal_id"])
        self.assertNotIn("done", [name for name, _ in events])
        self.assertIsNone(backend_app.TOPIC_STORE.get_topic_detail("neo-click"))

    def test_chat_stream_text_and_enter_decisions_emit_reviewable_actions(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        text_completion = SimpleNamespace(
            text="Propose typing",
            decision=BrainDecision.propose_act(
                "type_text",
                {"text": "收到，我马上处理。", "label": "微信聊天输入框"},
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        enter_completion = SimpleNamespace(
            text="Propose enter",
            decision=BrainDecision.propose_act("key_press", {"key": "enter", "label": "发送消息"}),
            provider="openai_compatible",
            model="neo-model",
        )

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[text_completion, enter_completion])):
            with self.client.stream("POST", "/api/chat/stream", json={"text": "回复微信联系人", "session_id": "neo-type"}) as resp:
                self.assertEqual(resp.status_code, 200)
                text_body = resp.read().decode("utf-8")
            with self.client.stream("POST", "/api/chat/stream", json={"text": "发送这条微信回复", "session_id": "neo-enter"}) as resp:
                self.assertEqual(resp.status_code, 200)
                enter_body = resp.read().decode("utf-8")

        text_approval = [data for name, data in _sse_events(text_body) if name == "approval_required"][-1]
        self.assertEqual(text_approval["action_type"], "type_text")
        self.assertIsNone(text_approval["preview"])
        self.assertIn("输入", text_approval["tools"][0]["summary"])
        self.assertIn("微信聊天输入框", text_approval["tools"][0]["summary"])
        enter_approval = [data for name, data in _sse_events(enter_body) if name == "approval_required"][-1]
        self.assertEqual(enter_approval["action_type"], "key_press")
        self.assertIsNone(enter_approval["preview"])
        self.assertIn("回车", enter_approval["tools"][0]["summary"])

    def test_chat_stream_corrects_unsupported_desktop_action_to_simple_human_action(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        unsupported = SimpleNamespace(
            text="Launch WeChat directly",
            decision=BrainDecision.propose_act(
                "launch_app",
                {"app": "微信", "label": "微信"},
                goal={
                    "objective": "打开微信并根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "launch_app",
                    "next": "locate_contact",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        corrected = SimpleNamespace(
            text="Click WeChat in Dock",
            decision=BrainDecision.propose_act(
                "click",
                {"x": 520, "y": 930, "label": "Dock 微信图标"},
                goal={
                    "objective": "打开微信并根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "launch_app",
                    "next": "locate_contact",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[unsupported, corrected])) as run_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开微信并根据张三聊天信息回复张三", "session_id": "neo-unsupported-action-correction"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 2)
        correction_prompt = run_mock.await_args_list[1].kwargs["user_text"]
        self.assertIn("launch_app", correction_prompt)
        self.assertIn("click、type_text、key_press enter", correction_prompt)
        approvals = [data for name, data in _sse_events(body) if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["action_type"], "click")
        self.assertEqual(approvals[0]["preview"]["marker"], "red_dot")
        self.assertNotIn("launch_app", [approval["action_type"] for approval in approvals])

    def test_chat_stream_corrects_click_without_complete_coordinates_before_review(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        incomplete_click = SimpleNamespace(
            text="Click WeChat without coordinates",
            decision=BrainDecision.propose_act(
                "click",
                {"label": "Dock 微信图标"},
                goal={
                    "objective": "打开微信并根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "launch_app",
                    "next": "locate_contact",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        corrected = SimpleNamespace(
            text="Click WeChat in Dock",
            decision=BrainDecision.propose_act(
                "click",
                {"x": 520, "y": 930, "label": "Dock 微信图标"},
                goal={
                    "objective": "打开微信并根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "launch_app",
                    "next": "locate_contact",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[incomplete_click, corrected])) as run_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开微信并根据张三聊天信息回复张三", "session_id": "neo-click-coordinate-correction"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 2)
        correction_prompt = run_mock.await_args_list[1].kwargs["user_text"]
        self.assertIn("click missing complete x/y", correction_prompt)
        approvals = [data for name, data in _sse_events(body) if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["action_type"], "click")
        self.assertEqual(approvals[0]["preview"]["x"], 520)
        self.assertEqual(approvals[0]["preview"]["y"], 930)

    def test_human_ops_text_and_enter_approval_execute_desktop_commands(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(
            text="Propose typing",
            decision=BrainDecision.propose_act(
                "type_text",
                {"text": "收到，我马上处理。", "label": "微信聊天输入框"},
            ),
            provider="openai_compatible",
            model="neo-model",
        )

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(return_value=completion)):
            with self.client.stream("POST", "/api/chat/stream", json={"text": "回复微信联系人", "session_id": "neo-type-approve"}) as resp:
                body = resp.read().decode("utf-8")
        proposal_id = [data for name, data in _sse_events(body) if name == "approval_required"][-1]["proposal_id"]

        with mock.patch.object(
            backend_app,
            "_send_desktop_command",
            new=mock.AsyncMock(return_value={"typed": True, "text": "收到，我马上处理。"}),
        ) as command_mock:
            with self.client.stream(
                "POST",
                f"/api/human-ops/proposals/{proposal_id}/decision",
                json={"approved": True},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                approved_body = resp.read().decode("utf-8")

        command_mock.assert_awaited_once()
        args, _kwargs = command_mock.await_args
        self.assertEqual(args[0], "human_ops_type_text")
        self.assertEqual(args[1]["text"], "收到，我马上处理。")
        done = [data for name, data in _sse_events(approved_body) if name == "done"][-1]
        self.assertEqual(done["execution"]["typed"], True)
        self.assertIn("已执行输入", done["text"])

    def test_human_ops_type_text_continuation_observes_draft_then_requests_enter(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        draft_text = "收到，我下午3点带资料。"
        initial = SimpleNamespace(
            text="Propose typing reply",
            decision=BrainDecision.propose_act(
                "type_text",
                {
                    "text": draft_text,
                    "label": "微信聊天输入框",
                    "continue_after_approval": True,
                },
                goal={
                    "objective": "根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "type_reply",
                    "next": "send_reply",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        enter = SimpleNamespace(
            text="Propose enter",
            decision=BrainDecision.propose_act(
                "key_press",
                {"key": "enter", "label": "发送微信回复"},
                goal={
                    "objective": "根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "send_reply",
                    "next": "verify_result",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": f"微信聊天输入框中已出现草稿：{draft_text}。发送入口可用，也可以按回车发送。",
            "observations": [{"claim": "输入框草稿可见，发送入口可用"}],
            "unknowns": [],
            "surface": {"kind": "wechat_gui", "region": "chat", "confidence": 0.76},
            "affordances": [
                {"kind": "chat_input", "supports": ["type_text", "key_press"], "confidence": 0.76},
                {"kind": "button", "label": "发送", "supports": ["click", "key_press"], "confidence": 0.72},
            ],
            "chat_context": {
                "contact": "张三",
                "recent_messages": [{"speaker": "张三", "text": "下午3点记得带资料"}],
                "input_ready": True,
                "send_ready": True,
            },
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[initial, enter])) as run_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "根据张三聊天信息回复张三", "session_id": "neo-type-to-enter"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")
            proposal_id = [data for name, data in _sse_events(body) if name == "approval_required"][-1]["proposal_id"]
            with mock.patch.object(backend_app, "_perform_human_ops_action", new=mock.AsyncMock(return_value={"typed": True, "text": draft_text})), mock.patch.object(
                backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
            ) as observe_mock:
                with self.client.stream(
                    "POST",
                    f"/api/human-ops/proposals/{proposal_id}/decision",
                    json={"approved": True},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    approved_body = resp.read().decode("utf-8")

        observe_decision = observe_mock.await_args.args[0]
        self.assertIn(draft_text, observe_decision.payload["observe_prompt"])
        self.assertIn("草稿", observe_decision.payload["observe_prompt"])
        self.assertIn("回车", observe_decision.payload["observe_prompt"])
        followup_prompt = run_mock.await_args_list[1].kwargs["user_text"]
        self.assertIn("chat_context", followup_prompt)
        self.assertIn("send_ready", followup_prompt)
        self.assertIn("recent_messages", followup_prompt)
        self.assertIn("不要反复观察", followup_prompt)
        approvals = [data for name, data in _sse_events(approved_body) if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["action_type"], "key_press")
        next_proposal = backend_app.HUMAN_OPS_PENDING_PROPOSALS[approvals[0]["proposal_id"]]["proposal"]
        next_args = next_proposal.payload["arguments"]
        self.assertEqual(next_args["expected_text"], draft_text)
        self.assertIn("回车", approvals[0]["tools"][0]["summary"])
        self.assertNotIn("done", [name for name, _ in _sse_events(approved_body)])

    def test_human_ops_enter_continuation_observes_sent_message_then_done(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        sent_text = "收到，我下午3点带资料。"
        initial = SimpleNamespace(
            text="Propose enter send",
            decision=BrainDecision.propose_act(
                "key_press",
                {
                    "key": "enter",
                    "label": "发送微信回复",
                    "expected_text": sent_text,
                    "continue_after_approval": True,
                },
                goal={
                    "objective": "根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "send_reply",
                    "next": "verify_result",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        done_completion = SimpleNamespace(
            text="已回复张三。",
            decision=BrainDecision.say(
                "已回复张三。",
                goal={
                    "objective": "根据张三聊天信息回复张三",
                    "status": "done",
                    "stage": "verify_result",
                    "evidence": [f"聊天记录中已出现回复：{sent_text}"],
                    "next": "stop",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": f"微信聊天记录底部已出现我发送的消息：{sent_text}。聊天输入框已清空。",
            "observations": [{"claim": "发送后的消息已出现在聊天记录底部"}],
            "unknowns": [],
            "surface": {"kind": "wechat_gui", "region": "chat", "confidence": 0.78},
            "affordances": [{"kind": "chat_input", "supports": ["type_text", "key_press"], "confidence": 0.76}],
            "chat_context": {
                "contact": "张三",
                "recent_messages": [{"speaker": "我", "text": sent_text}],
                "input_ready": True,
                "send_ready": False,
            },
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[initial, done_completion])) as run_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "根据张三聊天信息回复张三", "session_id": "neo-enter-to-done"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")
            proposal_id = [data for name, data in _sse_events(body) if name == "approval_required"][-1]["proposal_id"]
            with mock.patch.object(backend_app, "_perform_human_ops_action", new=mock.AsyncMock(return_value={"pressed": True, "key": "enter"})), mock.patch.object(
                backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
            ) as observe_mock:
                with self.client.stream(
                    "POST",
                    f"/api/human-ops/proposals/{proposal_id}/decision",
                    json={"approved": True},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    approved_body = resp.read().decode("utf-8")

        observe_decision = observe_mock.await_args.args[0]
        observe_prompt = observe_decision.payload["observe_prompt"]
        self.assertIn(sent_text, observe_prompt)
        self.assertIn("聊天记录", observe_prompt)
        self.assertIn("是否已经发送", observe_prompt)
        self.assertEqual(run_mock.await_count, 2)
        followup_prompt = run_mock.await_args_list[1].kwargs["user_text"]
        self.assertIn("chat_context", followup_prompt)
        self.assertIn(sent_text, followup_prompt)
        events = _sse_events(approved_body)
        self.assertNotIn("approval_required", [name for name, _ in events])
        done = [data for name, data in events if name == "done"][-1]
        self.assertEqual(done["text"], "已回复张三。")
        self.assertEqual(done["decision"]["payload"]["goal"]["status"], "done")

    def test_human_ops_enter_send_defaults_continue_even_when_goal_points_to_verify_result(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        sent_text = "收到，我下午3点带资料。"
        initial = SimpleNamespace(
            text="Propose enter send",
            decision=BrainDecision.propose_act(
                "key_press",
                {
                    "key": "enter",
                    "label": "发送微信回复",
                    "expected_text": sent_text,
                },
                goal={
                    "objective": "根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "verify_result",
                    "next": "stop",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        done_completion = SimpleNamespace(
            text="已回复张三。",
            decision=BrainDecision.say(
                "已回复张三。",
                goal={
                    "objective": "根据张三聊天信息回复张三",
                    "status": "done",
                    "stage": "verify_result",
                    "evidence": [f"聊天记录中已出现回复：{sent_text}"],
                    "next": "stop",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": f"微信聊天记录底部已出现我发送的消息：{sent_text}。聊天输入框已清空。",
            "observations": [{"claim": "发送后的消息已出现在聊天记录底部"}],
            "unknowns": [],
            "surface": {"kind": "wechat_gui", "region": "chat", "confidence": 0.78},
            "affordances": [{"kind": "chat_input", "supports": ["type_text", "key_press"], "confidence": 0.76}],
            "chat_context": {
                "contact": "张三",
                "recent_messages": [{"speaker": "我", "text": sent_text}],
                "input_ready": True,
                "send_ready": False,
            },
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[initial, done_completion])) as run_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "根据张三聊天信息回复张三", "session_id": "neo-enter-default-verify"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")
            proposal_id = [data for name, data in _sse_events(body) if name == "approval_required"][-1]["proposal_id"]
            first_proposal = backend_app.HUMAN_OPS_PENDING_PROPOSALS[proposal_id]["proposal"]
            self.assertTrue(first_proposal.payload["arguments"]["continue_after_approval"])
            with mock.patch.object(backend_app, "_perform_human_ops_action", new=mock.AsyncMock(return_value={"pressed": True, "key": "enter"})), mock.patch.object(
                backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
            ) as observe_mock:
                with self.client.stream(
                    "POST",
                    f"/api/human-ops/proposals/{proposal_id}/decision",
                    json={"approved": True},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    approved_body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 2)
        observe_prompt = observe_mock.await_args.args[0].payload["observe_prompt"]
        self.assertIn(sent_text, observe_prompt)
        events = _sse_events(approved_body)
        self.assertNotIn("approval_required", [name for name, _ in events])
        done = [data for name, data in events if name == "done"][-1]
        self.assertEqual(done["decision"]["payload"]["goal"]["status"], "done")

    def test_wechat_reply_full_human_action_chain_observe_click_type_enter_done(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        draft_text = "收到，我下午3点带资料。"
        initial_observe = SimpleNamespace(
            text="Observe Dock",
            decision=BrainDecision.observe(
                "screen",
                observe_prompt="请观察 Dock 里是否有微信图标，并给出可点击中心点。",
                goal={
                    "objective": "打开微信并根据张三聊天信息回复张三",
                    "status": "in_progress",
                    "stage": "launch_app",
                    "next": "click_dock_wechat",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        dock_click = SimpleNamespace(
            text="Click WeChat in Dock",
            decision=BrainDecision.propose_act(
                "click",
                {"x": 520, "y": 930, "label": "Dock 微信图标"},
                goal={
                    "objective": "打开微信并根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "launch_app",
                    "next": "locate_contact",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        contact_click = SimpleNamespace(
            text="Click Zhang San thread",
            decision=BrainDecision.propose_act(
                "click",
                {"x": 240, "y": 310, "label": "张三聊天条目"},
                goal={
                    "objective": "打开微信并根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "locate_contact",
                    "next": "read_context",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        focus_input = SimpleNamespace(
            text="Click input",
            decision=BrainDecision.propose_act(
                "click",
                {"x": 760, "y": 905, "label": "微信聊天输入框"},
                goal={
                    "objective": "打开微信并根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "focus_input",
                    "next": "type_reply",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        type_reply = SimpleNamespace(
            text="Type reply",
            decision=BrainDecision.propose_act(
                "type_text",
                {"text": draft_text, "label": "微信聊天输入框"},
                goal={
                    "objective": "打开微信并根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "type_reply",
                    "next": "send_reply",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        enter_send = SimpleNamespace(
            text="Press enter",
            decision=BrainDecision.propose_act(
                "key_press",
                {"key": "enter", "label": "发送微信回复"},
                goal={
                    "objective": "打开微信并根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "verify_result",
                    "next": "stop",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        done_completion = SimpleNamespace(
            text="已回复张三。",
            decision=BrainDecision.say(
                "已回复张三。",
                goal={
                    "objective": "打开微信并根据张三聊天信息回复张三",
                    "status": "done",
                    "stage": "verify_result",
                    "evidence": [f"聊天记录中已出现回复：{draft_text}"],
                    "next": "stop",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        dock_observation = {
            "text": "Dock 中可见微信图标，可点击中心点的 macOS 屏幕坐标为 x=520, y=930。",
            "observations": [{"claim": "Dock 微信图标可点击"}],
            "unknowns": [],
            "surface": {"kind": "desktop_gui", "region": "dock", "confidence": 0.86},
            "affordances": [
                {
                    "kind": "app_icon",
                    "label": "微信",
                    "supports": ["click_to_open"],
                    "location": {"x": 520, "y": 930},
                    "confidence": 0.82,
                }
            ],
        }
        wechat_app_observation = {
            "text": "微信已打开，左侧联系人“张三”的聊天条目可点击，中心点为 x=240, y=310。",
            "observations": [{"claim": "张三聊天条目可点击"}],
            "unknowns": [],
            "surface": {"kind": "wechat_gui", "region": "app", "confidence": 0.76},
            "affordances": [
                {"kind": "chat_thread", "label": "张三", "supports": ["click"], "location": {"x": 240, "y": 310}}
            ],
        }
        chat_observation = {
            "text": "微信聊天窗口显示联系人张三。最近聊天内容：张三：下午3点记得带资料；我：收到。底部聊天输入框可见，中心点为 x=760, y=905，发送入口可用。",
            "observations": [{"claim": "张三聊天内容、输入框和发送入口可见"}],
            "unknowns": [],
            "surface": {"kind": "wechat_gui", "region": "chat", "confidence": 0.78},
            "affordances": [
                {"kind": "chat_input", "supports": ["click", "type_text", "key_press"], "location": {"x": 760, "y": 905}},
                {"kind": "button", "label": "发送", "supports": ["click", "key_press"]},
            ],
            "chat_context": {
                "contact": "张三",
                "recent_messages": [{"speaker": "张三", "text": "下午3点记得带资料"}, {"speaker": "我", "text": "收到"}],
                "input_ready": True,
                "input_focused": False,
                "send_ready": True,
            },
        }
        focused_observation = {
            "text": "微信聊天输入框已聚焦，里面有光标，可以直接输入回复。",
            "observations": [{"claim": "聊天输入框已聚焦"}],
            "unknowns": [],
            "surface": {"kind": "wechat_gui", "region": "chat", "confidence": 0.78},
            "affordances": [{"kind": "chat_input", "supports": ["click", "type_text", "key_press"], "location": {"x": 760, "y": 905}}],
            "chat_context": {"contact": "张三", "input_ready": True, "input_focused": True, "send_ready": True},
        }
        draft_observation = {
            "text": f"微信聊天输入框中已出现草稿：{draft_text}。发送入口可用，也可以按回车发送。",
            "observations": [{"claim": "回复草稿完整可见"}],
            "unknowns": [],
            "surface": {"kind": "wechat_gui", "region": "chat", "confidence": 0.78},
            "affordances": [
                {"kind": "chat_input", "supports": ["click", "type_text", "key_press"]},
                {"kind": "button", "label": "发送", "supports": ["click", "key_press"]},
            ],
            "chat_context": {"contact": "张三", "input_ready": True, "input_focused": True, "send_ready": True},
        }
        sent_observation = {
            "text": f"微信聊天记录底部已出现我发送的消息：{draft_text}。聊天输入框已清空。",
            "observations": [{"claim": "发送后的消息已出现在聊天记录底部"}],
            "unknowns": [],
            "surface": {"kind": "wechat_gui", "region": "chat", "confidence": 0.78},
            "affordances": [{"kind": "chat_input", "supports": ["click", "type_text", "key_press"]}],
            "chat_context": {
                "contact": "张三",
                "recent_messages": [{"speaker": "我", "text": draft_text}],
                "input_ready": True,
                "input_focused": False,
                "send_ready": False,
            },
        }

        with mock.patch.object(
            backend_app,
            "run_brain_turn",
            new=mock.AsyncMock(side_effect=[initial_observe, dock_click, contact_click, focus_input, type_reply, enter_send, done_completion]),
        ) as run_mock, mock.patch.object(
            backend_app,
            "_perform_human_ops_observe",
            new=mock.AsyncMock(
                side_effect=[
                    dock_observation,
                    wechat_app_observation,
                    chat_observation,
                    focused_observation,
                    draft_observation,
                    sent_observation,
                ]
            ),
        ) as observe_mock, mock.patch.object(
            backend_app,
            "_perform_human_ops_action",
            new=mock.AsyncMock(
                side_effect=[
                    {"clicked": True, "target": "Dock 微信图标"},
                    {"clicked": True, "target": "张三聊天条目"},
                    {"clicked": True, "target": "微信聊天输入框"},
                    {"typed": True, "text": draft_text},
                    {"pressed": True, "key": "enter"},
                ]
            ),
        ) as action_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开微信并根据张三聊天信息回复张三", "session_id": "neo-full-wechat-chain"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")
            approval_events = [data for name, data in _sse_events(body) if name == "approval_required"]
            self.assertEqual([approval_events[-1]["action_type"]], ["click"])
            proposal_id = approval_events[-1]["proposal_id"]
            action_sequence: list[str] = ["click"]
            enter_proposal_id = ""
            for expected_next_action in ("click", "click", "type_text", "key_press", ""):
                with self.client.stream(
                    "POST",
                    f"/api/human-ops/proposals/{proposal_id}/decision",
                    json={"approved": True},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    approved_body = resp.read().decode("utf-8")
                events = _sse_events(approved_body)
                approvals = [data for name, data in events if name == "approval_required"]
                if not expected_next_action:
                    self.assertEqual(approvals, [])
                    done = [data for name, data in events if name == "done"][-1]
                    self.assertEqual(done["decision"]["payload"]["goal"]["status"], "done")
                    self.assertEqual(done["text"], "已回复张三。")
                    break
                self.assertEqual(len(approvals), 1)
                self.assertEqual(approvals[-1]["action_type"], expected_next_action)
                proposal_id = approvals[-1]["proposal_id"]
                action_sequence.append(expected_next_action)
                if expected_next_action == "key_press":
                    enter_proposal_id = proposal_id

        self.assertEqual(action_sequence, ["click", "click", "click", "type_text", "key_press"])
        self.assertEqual(run_mock.await_count, 7)
        self.assertEqual(observe_mock.await_count, 6)
        self.assertEqual(action_mock.await_count, 5)
        enter_proposal = backend_app.HUMAN_OPS_PENDING_PROPOSALS[enter_proposal_id]["proposal"]
        self.assertEqual(enter_proposal.payload["arguments"]["expected_text"], draft_text)

    def test_human_ops_approval_continues_complex_goal_to_next_reviewable_action(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        initial = SimpleNamespace(
            text="Propose open WeChat",
            decision=BrainDecision.propose_act(
                "click",
                {
                    "x": 520,
                    "y": 930,
                    "label": "Dock 微信图标",
                    "continue_after_approval": True,
                },
                goal={
                    "objective": "打开微信并回复联系人",
                    "status": "handoff_review",
                    "stage": "launch_app",
                    "next": "verify_app_open",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        followup = SimpleNamespace(
            text="Propose type reply",
            decision=BrainDecision.propose_act(
                "type_text",
                {
                    "text": "收到，我马上处理。",
                    "label": "微信聊天输入框",
                    "continue_after_approval": True,
                },
                goal={
                    "objective": "打开微信并回复联系人",
                    "status": "handoff_review",
                    "stage": "type_reply",
                    "next": "send_reply",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": "微信已经打开，当前联系人聊天输入框可见。",
            "observations": [{"claim": "聊天输入框可见"}],
            "unknowns": [],
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[initial, followup])) as run_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开微信并根据联系人聊天回复", "session_id": "neo-complex-continue"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")
            proposal_id = [data for name, data in _sse_events(body) if name == "approval_required"][-1]["proposal_id"]
            with mock.patch.object(backend_app, "_perform_human_ops_action", new=mock.AsyncMock(return_value={"clicked": True})), mock.patch.object(
                backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
            ) as observe_mock:
                with self.client.stream(
                    "POST",
                    f"/api/human-ops/proposals/{proposal_id}/decision",
                    json={"approved": True},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    approved_body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 2)
        observe_mock.assert_awaited_once()
        followup_prompt = run_mock.await_args_list[1].kwargs["user_text"]
        self.assertIn("上一项已批准并执行", followup_prompt)
        self.assertIn("微信已经打开", followup_prompt)
        events = _sse_events(approved_body)
        approvals = [data for name, data in events if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["action_type"], "type_text")
        self.assertIn("微信聊天输入框", approvals[0]["tools"][0]["summary"])
        self.assertNotIn("done", [name for name, _ in events])

    def test_human_ops_continuation_corrects_click_without_coordinates_before_next_review(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        initial = SimpleNamespace(
            text="Propose open WeChat",
            decision=BrainDecision.propose_act(
                "click",
                {
                    "x": 520,
                    "y": 930,
                    "label": "Dock 微信图标",
                    "continue_after_approval": True,
                },
                goal={
                    "objective": "打开微信并回复张三",
                    "status": "handoff_review",
                    "stage": "launch_app",
                    "next": "locate_contact",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        incomplete_next = SimpleNamespace(
            text="Click contact without coordinates",
            decision=BrainDecision.propose_act(
                "click",
                {"label": "张三聊天条目"},
                goal={
                    "objective": "打开微信并回复张三",
                    "status": "handoff_review",
                    "stage": "locate_contact",
                    "next": "read_context",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        corrected_next = SimpleNamespace(
            text="Click contact with coordinates",
            decision=BrainDecision.propose_act(
                "click",
                {"x": 240, "y": 310, "label": "张三聊天条目"},
                goal={
                    "objective": "打开微信并回复张三",
                    "status": "handoff_review",
                    "stage": "locate_contact",
                    "next": "read_context",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": "微信已打开，张三聊天条目可见。",
            "observations": [{"claim": "张三聊天条目可见"}],
            "unknowns": [],
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[initial, incomplete_next, corrected_next])) as run_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开微信并根据张三聊天信息回复张三", "session_id": "neo-continuation-coordinate-correction"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")
            proposal_id = [data for name, data in _sse_events(body) if name == "approval_required"][-1]["proposal_id"]
            with mock.patch.object(backend_app, "_perform_human_ops_action", new=mock.AsyncMock(return_value={"clicked": True})), mock.patch.object(
                backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
            ):
                with self.client.stream(
                    "POST",
                    f"/api/human-ops/proposals/{proposal_id}/decision",
                    json={"approved": True},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    approved_body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 3)
        correction_prompt = run_mock.await_args_list[2].kwargs["user_text"]
        self.assertIn("click missing complete x/y", correction_prompt)
        approvals = [data for name, data in _sse_events(approved_body) if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["preview"]["x"], 240)
        self.assertEqual(approvals[0]["preview"]["y"], 310)

    def test_human_ops_complex_goal_defaults_continue_after_approval_when_missing(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        initial = SimpleNamespace(
            text="Propose open WeChat",
            decision=BrainDecision.propose_act(
                "click",
                {
                    "x": 520,
                    "y": 930,
                    "label": "Dock 微信图标",
                },
                goal={
                    "objective": "打开微信并回复联系人",
                    "status": "handoff_review",
                    "stage": "launch_app",
                    "next": "verify_app_open",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        followup = SimpleNamespace(
            text="Propose type reply",
            decision=BrainDecision.propose_act(
                "type_text",
                {
                    "text": "收到，我马上处理。",
                    "label": "微信聊天输入框",
                    "continue_after_approval": True,
                },
                goal={
                    "objective": "打开微信并回复联系人",
                    "status": "handoff_review",
                    "stage": "type_reply",
                    "next": "send_reply",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": "微信已经打开，当前联系人聊天输入框可见。",
            "observations": [{"claim": "聊天输入框可见"}],
            "unknowns": [],
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[initial, followup])) as run_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开微信并根据联系人聊天回复", "session_id": "neo-complex-default-continue"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")
            proposal_id = [data for name, data in _sse_events(body) if name == "approval_required"][-1]["proposal_id"]
            first_proposal = backend_app.HUMAN_OPS_PENDING_PROPOSALS[proposal_id]["proposal"]
            self.assertTrue(first_proposal.payload["arguments"]["continue_after_approval"])
            with mock.patch.object(backend_app, "_perform_human_ops_action", new=mock.AsyncMock(return_value={"clicked": True})), mock.patch.object(
                backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
            ):
                with self.client.stream(
                    "POST",
                    f"/api/human-ops/proposals/{proposal_id}/decision",
                    json={"approved": True},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    approved_body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 2)
        approvals = [data for name, data in _sse_events(approved_body) if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["action_type"], "type_text")
        self.assertNotIn("done", [name for name, _ in _sse_events(approved_body)])

    def test_post_approval_observe_prompt_for_wechat_reply_reads_chat_context(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        initial = SimpleNamespace(
            text="Propose contact click",
            decision=BrainDecision.propose_act(
                "click",
                {
                    "x": 240,
                    "y": 310,
                    "label": "张三聊天条目",
                    "continue_after_approval": True,
                },
                goal={
                    "objective": "打开微信并根据张三聊天信息回复张三",
                    "status": "handoff_review",
                    "stage": "locate_contact",
                    "next": "read_context",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        followup = SimpleNamespace(
            text="Need to draft reply",
            decision=BrainDecision.say(
                "还需要读取聊天内容。",
                goal={
                    "objective": "打开微信并根据张三聊天信息回复张三",
                    "status": "blocked",
                    "stage": "read_context",
                    "next": "need_chat_context",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        observe_result = {
            "text": "张三聊天窗口已打开，最近聊天内容可见，聊天输入框可见。",
            "observations": [{"claim": "聊天内容和输入框可见"}],
            "unknowns": [],
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[initial, followup])):
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开微信并根据张三聊天信息回复张三", "session_id": "neo-post-approval-chat-prompt"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")
            proposal_id = [data for name, data in _sse_events(body) if name == "approval_required"][-1]["proposal_id"]
            with mock.patch.object(backend_app, "_perform_human_ops_action", new=mock.AsyncMock(return_value={"clicked": True})), mock.patch.object(
                backend_app, "_perform_human_ops_observe", new=mock.AsyncMock(return_value=observe_result)
            ) as observe_mock:
                with self.client.stream(
                    "POST",
                    f"/api/human-ops/proposals/{proposal_id}/decision",
                    json={"approved": True},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    resp.read()

        observe_decision = observe_mock.await_args.args[0]
        prompt = observe_decision.payload["observe_prompt"]
        self.assertIn("张三", prompt)
        self.assertIn("最近聊天内容", prompt)
        self.assertIn("聊天输入框", prompt)
        self.assertIn("聚焦", prompt)
        self.assertIn("发送入口", prompt)

    def test_human_ops_approval_continuation_reacts_through_observe_to_next_action(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        initial = SimpleNamespace(
            text="Propose open WeChat",
            decision=BrainDecision.propose_act(
                "click",
                {
                    "x": 520,
                    "y": 930,
                    "label": "Dock 微信图标",
                    "continue_after_approval": True,
                },
                goal={
                    "objective": "打开微信并回复张三",
                    "status": "handoff_review",
                    "stage": "launch_app",
                    "next": "locate_contact",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        needs_observe = SimpleNamespace(
            text="Need contact affordance",
            decision=BrainDecision.observe(
                "微信 张三",
                observe_prompt="请观察微信窗口，找到联系人张三的聊天条目或搜索框，并给出可操作入口。",
                goal={
                    "objective": "打开微信并回复张三",
                    "status": "in_progress",
                    "stage": "locate_contact",
                    "next": "observe_contact_entry",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        next_action = SimpleNamespace(
            text="Propose contact click",
            decision=BrainDecision.propose_act(
                "click",
                {
                    "x": 240,
                    "y": 310,
                    "label": "张三聊天条目",
                    "continue_after_approval": True,
                },
                goal={
                    "objective": "打开微信并回复张三",
                    "status": "handoff_review",
                    "stage": "locate_contact",
                    "next": "read_context",
                },
            ),
            provider="openai_compatible",
            model="neo-model",
        )
        post_click_observation = {
            "text": "微信已经打开，但当前还没有确认张三的聊天条目。",
            "observations": [{"claim": "微信窗口可见"}],
            "unknowns": [],
            "surface": {"kind": "wechat_gui", "region": "app", "confidence": 0.74},
            "affordances": [],
        }
        contact_observation = {
            "text": "微信左侧联系人“张三”的聊天条目可点击，中心点的 macOS 屏幕坐标为 x=240, y=310。",
            "observations": [{"claim": "张三聊天条目可点击，x=240, y=310。"}],
            "unknowns": [],
            "surface": {"kind": "wechat_gui", "region": "chat", "confidence": 0.76},
            "affordances": [
                {
                    "kind": "chat_thread",
                    "label": "张三",
                    "supports": ["click"],
                    "location": {"x": 240, "y": 310},
                    "confidence": 0.74,
                }
            ],
        }

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=[initial, needs_observe, next_action])) as run_mock:
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "打开微信并根据张三聊天信息回复张三", "session_id": "neo-continue-observe"},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")
            proposal_id = [data for name, data in _sse_events(body) if name == "approval_required"][-1]["proposal_id"]
            with mock.patch.object(backend_app, "_perform_human_ops_action", new=mock.AsyncMock(return_value={"clicked": True})), mock.patch.object(
                backend_app,
                "_perform_human_ops_observe",
                new=mock.AsyncMock(side_effect=[post_click_observation, contact_observation]),
            ) as observe_mock:
                with self.client.stream(
                    "POST",
                    f"/api/human-ops/proposals/{proposal_id}/decision",
                    json={"approved": True},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    approved_body = resp.read().decode("utf-8")

        self.assertEqual(run_mock.await_count, 3)
        self.assertEqual(observe_mock.await_count, 2)
        observe_followup_prompt = run_mock.await_args_list[2].kwargs["user_text"]
        self.assertIn("Structured computer-use context", observe_followup_prompt)
        self.assertIn("chat_thread", observe_followup_prompt)
        self.assertIn("张三", observe_followup_prompt)
        events = _sse_events(approved_body)
        approvals = [data for name, data in events if name == "approval_required"]
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["action_type"], "click")
        self.assertEqual(approvals[0]["preview"]["x"], 240)
        self.assertIn("张三聊天条目", approvals[0]["tools"][0]["summary"])
        self.assertNotIn("done", [name for name, _ in events])

    def test_human_ops_click_approval_executes_once_and_streams_result(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(
            text="Propose click",
            decision=BrainDecision.propose_act("click", {"x": 12, "y": 34, "label": "Codex"}),
            provider="openai_compatible",
            model="neo-model",
        )
        click_result = {"ok": True, "clicked": True, "x": 12, "y": 34}

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(return_value=completion)):
            with self.client.stream("POST", "/api/chat/stream", json={"text": "点一下", "session_id": "neo-click-approve"}) as resp:
                body = resp.read().decode("utf-8")
        proposal_id = [data for name, data in _sse_events(body) if name == "approval_required"][-1]["proposal_id"]

        with mock.patch.object(backend_app, "_perform_human_ops_click", new=mock.AsyncMock(return_value=click_result)) as click_mock:
            with self.client.stream(
                "POST",
                f"/api/human-ops/proposals/{proposal_id}/decision",
                json={"approved": True},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                approved_body = resp.read().decode("utf-8")

        click_mock.assert_awaited_once()
        args, _kwargs = click_mock.await_args
        self.assertEqual(args[0].payload["action_type"], "click")
        events = _sse_events(approved_body)
        done = [data for name, data in events if name == "done"][-1]
        self.assertEqual(done["proposal_id"], proposal_id)
        self.assertEqual(done["execution"]["clicked"], True)
        self.assertIn("已执行点击", done["text"])

    def test_chat_stream_retry_truncates_persisted_branch_before_append(self) -> None:
        backend_app.TOPIC_STORE.create_topic(topic_id="neo-retry")
        backend_app.TOPIC_STORE.append_exchange("neo-retry", user_text="原问题一", assistant_text="原回答一")
        backend_app.TOPIC_STORE.append_exchange("neo-retry", user_text="原问题二", assistant_text="原回答二")
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "openai_compatible",
                        "model_endpoint": "https://llm.example",
                        "model_name": "neo-model",
                    }
                }
            ),
            encoding="utf-8",
        )
        completion = SimpleNamespace(text="重试回答二", provider="openai_compatible", model="neo-model")

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(return_value=completion)):
            with self.client.stream(
                "POST",
                "/api/chat/stream",
                json={"text": "改写后的问题二", "session_id": "neo-retry", "retry_from_assistant_turn": 2},
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        events = _sse_events(body)
        done = [data for name, data in events if name == "done"][-1]
        self.assertEqual(done["retry_from_assistant_turn"], 2)
        detail = backend_app.TOPIC_STORE.get_topic_detail("neo-retry") or {}
        self.assertEqual(
            [item["content"] for item in detail["messages"]],
            ["原问题一", "原回答一", "改写后的问题二", "重试回答二"],
        )
        self.assertEqual(detail["messages"][-1]["assistant_turn"], 2)

    def test_chat_stream_redacts_brain_errors_before_sse_and_history(self) -> None:
        backend_app.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "brain": {
                        "provider": "anthropic_compatible",
                        "model_endpoint": "https://secret-token@example.com/v1",
                        "model_name": "claude-test",
                        "api_key": "secret-token",
                    }
                }
            ),
            encoding="utf-8",
        )
        error = BrainLLMError("401 bearer secret-token at https://secret-token@example.com/v1")

        with mock.patch.object(backend_app, "run_brain_turn", new=mock.AsyncMock(side_effect=error)):
            with self.client.stream("POST", "/api/chat/stream", json={"text": "你好", "session_id": "neo-error"}) as resp:
                self.assertEqual(resp.status_code, 200)
                body = resp.read().decode("utf-8")

        self.assertNotIn("secret-token", body)
        self.assertNotIn("https://secret-token@example.com/v1", body)
        events = _sse_events(body)
        token_text = "".join(data.get("text", "") for name, data in events if name == "token")
        self.assertIn("Brain 调用失败", token_text)
        topic = backend_app.TOPIC_STORE.get_topic_detail("neo-error")
        self.assertIsNotNone(topic)
        assistant_text = topic["messages"][-1]["content"]
        self.assertNotIn("secret-token", assistant_text)

    def test_skills_list_is_empty_recipe_shell(self) -> None:
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

    def test_legacy_runtime_management_routes_are_not_registered(self) -> None:
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
                self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
