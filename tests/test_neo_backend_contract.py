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
        active = FakeAnalyzer.frames[0]["active_observation"]
        self.assertIn("设置应用的位置", active["observe_prompt"])
        self.assertIn("自然语言", active["observe_prompt"])
        self.assertIn("系统设置图标", result["text"])
        self.assertIn("x=452", result["text"])

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
