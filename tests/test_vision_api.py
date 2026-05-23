from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend.conversation_store import ConversationStore
from backend.ipet_memory_store import IpetMemoryStore
from backend.vision import VisionService


class _FakeRuntimeClient:
    runtime_id = "hermes"

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict]] = []

    async def stream_sse(self, path, payload):
        self.requests.append(("POST", path, payload))
        yield "done", {"text": "ok"}


class VisionApiTests(unittest.TestCase):
    token_headers = {"X-Ipet-Local-Token": "vision-test-token"}

    def setUp(self) -> None:
        self.client = TestClient(backend_app.app)
        backend_app._VISION_SERVICE = None
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
        backend_app._VISION_SERVICE = None
        self.client.close()

    def test_status_does_not_leak_data_url(self) -> None:
        service = VisionService({"enabled": True}, now=lambda: 10.0)
        service.update_frame({"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"})
        backend_app._VISION_SERVICE = service
        with mock.patch.object(backend_app, "_load_settings_config", return_value={"vision": {"enabled": True}}):
            resp = self.client.get("/api/vision/status")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["has_frame"])
        self.assertNotIn("data_url", resp.text)

    def test_status_and_context_do_not_leak_api_keys_or_desktop_secrets(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "analyzer": {
                    "enabled": True,
                    "provider": "openai_compatible_vlm",
                    "api_key": "sk-status-secret",
                    "api_key_env": "VISION_STATUS_SECRET_ENV",
                },
            }
        }
        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ):
            frame_resp = self.client.post(
                "/api/vision/frame",
                json={
                    "mime_type": "image/jpeg",
                    "data_url": "data:image/jpeg;base64,abc",
                    "desktop_context": {
                        "foreground_app": "Code",
                        "api_key": "desktop-secret",
                        "access_token": "desktop-token",
                    },
                },
                headers=self.token_headers,
            )
            status_resp = self.client.get("/api/vision/status")
            context_resp = self.client.get("/api/vision/context", headers=self.token_headers)

        self.assertEqual(frame_resp.status_code, 200)
        combined = "\n".join([status_resp.text, context_resp.text, json.dumps(frame_resp.json(), ensure_ascii=False)])
        self.assertNotIn("sk-status-secret", combined)
        self.assertNotIn("VISION_STATUS_SECRET_ENV", combined)
        self.assertNotIn("desktop-secret", combined)
        self.assertNotIn("desktop-token", combined)
        self.assertNotIn("api_key", status_resp.json()["analyzer"])
        self.assertNotIn("api_key_env", context_resp.json()["analyzer"])

    def test_disabled_rejects_frame(self) -> None:
        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value={"vision": {"enabled": False}}
        ):
            resp = self.client.post(
                "/api/vision/frame",
                json={"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"},
                headers=self.token_headers,
            )
        self.assertEqual(resp.status_code, 409)

    def test_disabled_vision_does_not_run_analyzer(self) -> None:
        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={
                "vision": {
                    "enabled": False,
                    "analyzer": {"enabled": True, "provider": "macos_vision_ocr"},
                }
            },
        ), mock.patch("backend.app.VisionAnalyzer") as analyzer_cls:
            resp = self.client.post(
                "/api/vision/frame",
                json={"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"},
                headers=self.token_headers,
        )
        self.assertEqual(resp.status_code, 409)
        analyzer_cls.assert_not_called()

    def test_enabled_frame_with_disabled_analyzer_does_not_run_analyzer(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "analyzer": {"enabled": False, "provider": "macos_vision_ocr"},
            }
        }
        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch("backend.app.VisionAnalyzer") as analyzer_cls:
            resp = self.client.post(
                "/api/vision/frame",
                json={"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"},
                headers=self.token_headers,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["last_route_decision"]["reason"], "analyzer_disabled")
        analyzer_cls.assert_not_called()

    def test_routing_disabled_analyzes_each_enabled_frame(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "routing": {"enabled": False},
                "analyzer": {"enabled": True, "provider": "macos_vision_ocr"},
            }
        }

        def enrich(payload):
            from backend.vision_analyzer import merge_analysis_into_payload

            return merge_analysis_into_payload(
                payload,
                settings["vision"]["analyzer"],
                {
                    "summary": "设置页可见。",
                    "observations": [
                        {
                            "claim": "屏幕显示设置页",
                            "evidence": "测试分析器返回的证据",
                            "confidence": 0.8,
                            "source": "unit-test-vlm",
                        }
                    ],
                },
            )

        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch("backend.app.VisionAnalyzer") as analyzer_cls:
            analyzer_cls.return_value.enrich_payload.side_effect = enrich
            first_resp = self.client.post(
                "/api/vision/frame",
                json={"mime_type": "image/png", "data_url": "data:image/png;base64,repeat", "visual_hash": "0000"},
                headers=self.token_headers,
            )
            second_resp = self.client.post(
                "/api/vision/frame",
                json={"mime_type": "image/png", "data_url": "data:image/png;base64,repeat", "visual_hash": "0000"},
                headers=self.token_headers,
            )

        self.assertEqual(first_resp.status_code, 200)
        self.assertEqual(second_resp.status_code, 200)
        self.assertEqual(analyzer_cls.return_value.enrich_payload.call_count, 2)
        self.assertEqual(second_resp.json()["last_route_decision"]["reason"], "routing_disabled")

    def test_enabled_accepts_frame_and_context_hides_image_by_default(self) -> None:
        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value={"vision": {"enabled": True}}
        ):
            frame_resp = self.client.post(
                "/api/vision/frame",
                json={"mime_type": "image/png", "data_url": "data:image/png;base64,abc"},
                headers=self.token_headers,
            )
            context_resp = self.client.get("/api/vision/context", headers=self.token_headers)
        self.assertEqual(frame_resp.status_code, 200)
        self.assertTrue(frame_resp.json()["has_frame"])
        self.assertEqual(context_resp.status_code, 200)
        self.assertTrue(context_resp.json()["available"])
        self.assertNotIn("data_url", context_resp.text)
        self.assertNotIn("image", context_resp.json())

    def test_frame_enabled_analyzer_generates_observations_without_leaking_image(self) -> None:
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="自动视觉 设置", stderr="")

        settings = {
            "vision": {
                "enabled": True,
                "analyzer": {
                    "enabled": True,
                    "provider": "macos_vision_ocr",
                    "timeout_sec": 0.5,
                    "max_text_chars": 80,
                },
            }
        }
        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch("backend.app.VisionAnalyzer") as analyzer_cls:
            analyzer = analyzer_cls.return_value
            from backend.vision_analyzer import VisionAnalyzer

            analyzer.enrich_payload.side_effect = lambda payload: VisionAnalyzer(
                settings["vision"]["analyzer"], runner=runner
            ).enrich_payload(payload)
            frame_resp = self.client.post(
                "/api/vision/frame",
                json={"mime_type": "image/png", "data_url": "data:image/png;base64,iVBORw0KGgo="},
                headers=self.token_headers,
            )
            status_resp = self.client.get("/api/vision/status")
            context_resp = self.client.get("/api/vision/context", headers=self.token_headers)

        self.assertEqual(frame_resp.status_code, 200)
        self.assertTrue(status_resp.json()["grounded"])
        self.assertEqual(status_resp.json()["evidence_count"], 1)
        self.assertEqual(status_resp.json()["analyzer"]["last_status"], "ok")
        self.assertNotIn("data_url", status_resp.text)
        context = context_resp.json()
        self.assertEqual(context["observations"][0]["source"], "macos-vision-ocr")
        self.assertIn("自动视觉", context["observations"][0]["claim"])
        self.assertEqual(context["analyzer"]["last_status"], "ok")
        self.assertEqual(context["analyzer"]["observations_added"], 1)
        self.assertNotIn("data_url", context_resp.text)

    def test_repeated_frame_reuses_evidence_without_second_analyzer_call(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "routing": {"stable_after_change_ms": 0, "vlm_cooldown_sec": 10},
                "analyzer": {"enabled": True, "provider": "macos_vision_ocr"},
            }
        }

        def enrich(payload):
            from backend.vision_analyzer import merge_analysis_into_payload

            return merge_analysis_into_payload(
                payload,
                settings["vision"]["analyzer"],
                {
                    "summary": "设置页可见。",
                    "observations": [
                        {
                            "claim": "屏幕显示设置页",
                            "evidence": "测试分析器返回的证据",
                            "confidence": 0.8,
                            "source": "unit-test-vlm",
                        }
                    ],
                },
            )

        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch("backend.app.VisionAnalyzer") as analyzer_cls:
            analyzer_cls.return_value.enrich_payload.side_effect = enrich
            first_resp = self.client.post(
                "/api/vision/frame",
                json={
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,repeat",
                    "visual_hash": "0000",
                    "desktop_context": {"foreground_app": "Code", "window_title": "settings"},
                },
                headers=self.token_headers,
            )
            second_resp = self.client.post(
                "/api/vision/frame",
                json={
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,repeat",
                    "visual_hash": "0000",
                    "desktop_context": {"foreground_app": "Code", "window_title": "settings"},
                },
                headers=self.token_headers,
            )
            context_resp = self.client.get("/api/vision/context", headers=self.token_headers)

        self.assertEqual(first_resp.status_code, 200)
        self.assertEqual(second_resp.status_code, 200)
        self.assertEqual(analyzer_cls.return_value.enrich_payload.call_count, 1)
        self.assertTrue(context_resp.json()["grounded"])
        self.assertEqual(context_resp.json()["observations"][0]["source"], "unit-test-vlm")
        self.assertEqual(second_resp.json()["last_route_decision"]["reason"], "no_change")
        self.assertNotIn("data_url", second_resp.text)
        self.assertNotIn("data_url", context_resp.text)

    def test_status_and_context_include_route_and_capture_metadata_without_image(self) -> None:
        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value={"vision": {"enabled": True}}
        ):
            frame_resp = self.client.post(
                "/api/vision/frame",
                json={
                    "mime_type": "image/jpeg",
                    "data_url": "data:image/jpeg;base64,abc",
                    "capture_backend": "macos_screencapture",
                    "capture_scope": "visible_spaces_all_displays",
                    "display_count": 2,
                    "desktop_context": {"foreground_app": "Safari", "window_title": "Docs"},
                },
                headers=self.token_headers,
            )
            status_resp = self.client.get("/api/vision/status")
            context_resp = self.client.get("/api/vision/context", headers=self.token_headers)

        self.assertEqual(frame_resp.status_code, 200)
        status = status_resp.json()
        context = context_resp.json()
        self.assertEqual(status["capture_backend"], "macos_screencapture")
        self.assertEqual(status["capture_scope"], "visible_spaces_all_displays")
        self.assertEqual(status["display_count"], 2)
        self.assertEqual(status["desktop_context"]["foreground_app"], "Safari")
        self.assertIn("last_route_decision", status)
        self.assertIn("recent_events", context)
        self.assertNotIn("data_url", status_resp.text)
        self.assertNotIn("data_url", context_resp.text)

    def test_frame_provider_empty_does_not_use_active_runtime_vision_fallback(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "analyzer": {
                    "enabled": True,
                    "provider": "",
                    "fallback_to_runtime": True,
                },
            }
        }
        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch.object(backend_app, "_runtime_vision_fallback", new=mock.AsyncMock(return_value={})) as fallback_mock:
            frame_resp = self.client.post(
                "/api/vision/frame",
                json={"mime_type": "image/png", "data_url": "data:image/png;base64,iVBORw0KGgo="},
                headers=self.token_headers,
            )
            context_resp = self.client.get("/api/vision/context", headers=self.token_headers)

        self.assertEqual(frame_resp.status_code, 200)
        fallback_mock.assert_not_awaited()
        context = context_resp.json()
        self.assertFalse(context["grounded"])
        self.assertEqual(context["observations"], [])
        self.assertIn(context["analyzer"]["last_status"], {"", "disabled"})

    def test_passive_frame_without_observations_does_not_use_runtime_fallback(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "routing": {"stable_after_change_ms": 0, "vlm_cooldown_sec": 0},
                "analyzer": {
                    "enabled": True,
                    "provider": "macos_vision_ocr",
                    "fallback_to_runtime": True,
                },
            }
        }

        def empty_enrich(payload):
            from backend.vision_analyzer import merge_analysis_into_payload

            return merge_analysis_into_payload(
                payload,
                settings["vision"]["analyzer"],
                {"summary": "", "observations": [], "unknowns": ["no useful OCR"]},
            )

        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch("backend.app.VisionAnalyzer") as analyzer_cls, mock.patch.object(
            backend_app, "_runtime_vision_fallback", new=mock.AsyncMock(return_value={})
        ) as fallback_mock:
            analyzer_cls.return_value.enrich_payload.side_effect = empty_enrich
            frame_resp = self.client.post(
                "/api/vision/frame",
                json={"mime_type": "image/png", "data_url": "data:image/png;base64,passive-empty", "visual_hash": "1111"},
                headers=self.token_headers,
            )
            context_resp = self.client.get("/api/vision/context", headers=self.token_headers)

        self.assertEqual(frame_resp.status_code, 200)
        fallback_mock.assert_not_awaited()
        context = context_resp.json()
        self.assertFalse(context["grounded"])
        self.assertEqual(context["timeline"], [])
        self.assertIn("no useful OCR", "\n".join(context["unknowns"]))

    def test_frame_observations_surface_grounded_status_without_image(self) -> None:
        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value={"vision": {"enabled": True}}
        ):
            frame_resp = self.client.post(
                "/api/vision/frame",
                json={
                    "mime_type": "image/jpeg",
                    "data_url": "data:image/jpeg;base64,abc",
                    "frame_id": "frame-123",
                    "frame_hash": "hash-123",
                    "summary": "设置页可见。",
                    "observations": [
                        {
                            "claim": "屏幕显示设置页",
                            "evidence": "左侧导航和自动视觉标题可见",
                            "confidence": 0.8,
                            "source": "unit-test",
                        }
                    ],
                    "unknowns": ["无法确认背后窗口内容"],
                },
                headers=self.token_headers,
            )
            status_resp = self.client.get("/api/vision/status")
            context_resp = self.client.get("/api/vision/context", headers=self.token_headers)

        self.assertEqual(frame_resp.status_code, 200)
        self.assertTrue(status_resp.json()["grounded"])
        self.assertEqual(status_resp.json()["evidence_count"], 1)
        self.assertEqual(status_resp.json()["frame_id"], "frame-123")
        self.assertEqual(status_resp.json()["frame_hash"], "hash-123")
        self.assertNotIn("data_url", status_resp.text)
        context = context_resp.json()
        self.assertTrue(context["grounded"])
        self.assertEqual(context["verified_summary"], "设置页可见。")
        self.assertEqual(context["observations"][0]["claim"], "屏幕显示设置页")
        self.assertNotIn("data_url", context_resp.text)

    def test_context_requires_local_token(self) -> None:
        service = VisionService({"enabled": True}, now=lambda: 10.0)
        service.update_frame({"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"})
        backend_app._VISION_SERVICE = service
        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value={"vision": {"enabled": True}}
        ):
            resp = self.client.get("/api/vision/context?include_image=true")
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn("data_url", resp.text)

    def test_context_ttl_marks_stale_frame_unavailable(self) -> None:
        ticks = [10.0]
        service = VisionService({"enabled": True, "context_ttl_sec": 5}, now=lambda: ticks[0])
        service.update_frame({"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"})
        backend_app._VISION_SERVICE = service
        ticks[0] = 16.0
        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value={"vision": {"enabled": True, "context_ttl_sec": 5}}
        ):
            resp = self.client.get("/api/vision/context?include_image=true", headers=self.token_headers)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["available"])
        self.assertNotIn("data_url", resp.text)

    def test_observe_endpoint_captures_active_frame_and_stores_evidence(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "active_observation": {"enabled": True, "timeout_sec": 12, "settle_ms": 500},
                "analyzer": {"enabled": False},
            }
        }
        host_frame = {
            "mime_type": "image/jpeg",
            "data_url": "data:image/jpeg;base64,active",
            "frame_id": "active-1",
            "summary": "浏览器窗口显示 example.com。",
            "observations": [
                {
                    "claim": "浏览器窗口显示 example.com 页面",
                    "evidence": "截图中页面标题区域可见 example.com",
                    "confidence": 0.86,
                    "source": "unit-test-vlm",
                }
            ],
            "active_observation": {"mode": "desktop_survey", "target_id": "desktop_survey", "status": "success"},
        }

        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch.object(
            backend_app,
            "_send_active_vision_capture_command",
            new=mock.AsyncMock(return_value={"ok": True, "frame": host_frame, "trace": host_frame["active_observation"]}),
            create=True,
        ):
            resp = self.client.post(
                "/api/vision/observe",
                json={"text": "浏览器打开的是什么网站？", "force": True, "target_hint": "browser"},
                headers=self.token_headers,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["context"]["grounded"])
        self.assertEqual(body["context"]["observations"][0]["claim"], "浏览器窗口显示 example.com 页面")
        self.assertEqual(body["context"]["active_observation"]["mode"], "desktop_survey")
        self.assertEqual(body["context"]["active_observation"]["target_id"], "desktop_survey")
        self.assertNotIn("data_url", resp.text)
        self.assertTrue(backend_app._VISION_SERVICE.context()["grounded"])

    def test_observe_endpoint_active_lane_can_return_one_shot_image(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "active_observation": {"enabled": True},
                "analyzer": {"enabled": False},
            }
        }
        host_frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,active-image",
            "frame_id": "active-image-1",
            "observations": [{"claim": "当前屏幕可见", "confidence": 0.7}],
        }

        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch.object(
            backend_app,
            "_send_active_vision_capture_command",
            new=mock.AsyncMock(return_value={"ok": True, "frame": host_frame, "trace": {"status": "success"}}),
            create=True,
        ):
            resp = self.client.post(
                "/api/vision/observe?lane=active&include_image=true",
                json={"text": "看一下当前屏幕", "force": True},
                headers=self.token_headers,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["lane"], "active")
        self.assertEqual(body["context"]["image"]["data_url"], "data:image/png;base64,active-image")
        self.assertEqual(body["image_urls"], ["base64://active-image"])

    def test_observe_endpoint_returns_iterative_retry_metadata(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "active_observation": {"enabled": True},
                "analyzer": {"enabled": False},
            }
        }
        host_frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,active-ipet",
            "frame_id": "active-ipet",
            "frame_hash": "hash-ipet",
            "desktop_context": {"foreground_app": "Ipet", "window_title": "Ipet chat"},
            "observations": [],
            "active_observation": {
                "mode": "desktop_survey",
                "target_id": "desktop_survey",
                "desktop_targets": [
                    {"target_id": "target-safari", "app": "Safari", "title": "Video", "frontmost": False}
                ],
            },
        }

        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch.object(
            backend_app,
            "_send_active_vision_capture_command",
            new=mock.AsyncMock(return_value={"ok": True, "frame": host_frame, "trace": {"status": "success"}}),
            create=True,
        ):
            resp = self.client.post(
                "/api/vision/observe?lane=active&include_image=true",
                json={
                    "text": "现在看的视频是什么？",
                    "force": True,
                    "target_hint": "frontmost",
                    "attempt_reason": "先看前台窗口",
                    "exclude_seen": [{"target_hint": "current_desktop", "frame_hash": "old"}],
                },
                headers=self.token_headers,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        active = body["context"]["active_observation"]
        self.assertEqual(active["attempt_index"], 3)
        self.assertEqual(active["mode"], "focus_target")
        self.assertEqual(active["target_id"], "target-safari")
        self.assertEqual(active["attempt_reason"], "retry after not_relevant:self_occluded")
        self.assertEqual(active["foreground_app"], "Ipet")
        self.assertEqual(active["window_title"], "Ipet chat")
        self.assertEqual(active["frame_hash"], "hash-ipet")
        self.assertTrue(active["self_occluded"])
        self.assertTrue(active["stop"])
        self.assertEqual(active["relevance_hint"], "stop:repeated_frame")
        self.assertEqual(active["available_next_targets"], [])
        self.assertIn("desktop_survey", active["attempted_targets"])
        self.assertIn("target-safari", active["attempted_targets"])

    def test_observe_endpoint_retries_self_occluded_frame_with_next_target(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "active_observation": {"enabled": True, "timeout_sec": 20},
                "analyzer": {"enabled": False},
            }
        }
        attempts: list[dict[str, object]] = []

        async def capture_side_effect(_decision, _vision_cfg, **kwargs):
            attempts.append(dict(kwargs))
            if len(attempts) == 1:
                return {
                    "ok": True,
                    "frame": {
                        "mime_type": "image/png",
                        "data_url": "data:image/png;base64,self-occluded",
                        "frame_id": "active-self",
                        "frame_hash": "hash-self",
                        "desktop_context": {"foreground_app": "Ipet", "window_title": "Ipet chat"},
                        "observations": [],
                        "active_observation": {
                            "mode": "desktop_survey",
                            "target_id": "desktop_survey",
                            "desktop_targets": [
                                {"target_id": "target-safari", "app": "Safari", "title": "Ipet docs"}
                            ],
                        },
                    },
                    "trace": {"status": "success"},
                }
            return {
                "ok": True,
                "frame": {
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,browser-target",
                    "frame_id": "active-browser",
                    "frame_hash": "hash-browser",
                    "desktop_context": {"foreground_app": "Safari", "window_title": "Ipet docs"},
                    "summary": "浏览器显示 Ipet 文档。",
                    "observations": [
                        {
                            "claim": "浏览器显示 Ipet 文档页面",
                            "evidence": "主动观察第二次截图中可见 Ipet docs 标题",
                            "confidence": 0.9,
                        }
                    ],
                    "active_observation": {
                        "mode": "focus_target",
                        "target_id": "target-safari",
                        "desktop_targets": [
                            {"target_id": "target-safari", "app": "Safari", "title": "Ipet docs"}
                        ],
                    },
                },
                "trace": {"status": "success"},
            }

        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch.object(
            backend_app,
            "_send_active_vision_capture_command",
            new=mock.AsyncMock(side_effect=capture_side_effect),
            create=True,
        ):
            resp = self.client.post(
                "/api/vision/observe",
                json={"text": "当前窗口显示什么？", "force": True, "target_hint": "frontmost"},
                headers=self.token_headers,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["mode"], "desktop_survey")
        self.assertEqual(attempts[1]["mode"], "focus_target")
        self.assertEqual(attempts[1]["target_id"], "target-safari")
        self.assertIn("desktop_survey", attempts[1]["exclude_seen"][0]["target_id"])
        active = body["context"]["active_observation"]
        self.assertEqual(active["attempt_index"], 2)
        self.assertIn("desktop_survey", active["attempted_targets"])
        self.assertIn("target-safari", active["attempted_targets"])
        self.assertEqual(body["context"]["frame_id"], "active-browser")
        self.assertEqual(body["context"]["observations"][0]["claim"], "浏览器显示 Ipet 文档页面")
        self.assertNotIn("data_url", resp.text)

    def test_observe_endpoint_retries_fallback_candidate_when_desktop_targets_empty(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "active_observation": {"enabled": True, "timeout_sec": 20},
                "analyzer": {"enabled": False},
            }
        }
        attempts: list[dict[str, object]] = []

        async def capture_side_effect(_decision, _vision_cfg, **kwargs):
            attempts.append(dict(kwargs))
            if len(attempts) == 1:
                return {
                    "ok": True,
                    "frame": {
                        "mime_type": "image/png",
                        "data_url": "data:image/png;base64,system-events-failed",
                        "frame_id": "active-survey-fallback",
                        "frame_hash": "hash-survey-fallback",
                        "desktop_context": {"foreground_app": "Ipet", "window_title": "Ipet chat"},
                        "observations": [],
                        "active_observation": {
                            "mode": "desktop_survey",
                            "target_id": "desktop_survey",
                            "desktop_targets": [],
                            "discovery_errors": ["System Events failed: -10827"],
                            "target_candidates": [
                                {
                                    "target_id": "running:safari",
                                    "source": "running_app",
                                    "app": "Safari",
                                    "title": "Safari",
                                    "focusable": True,
                                }
                            ],
                        },
                    },
                    "trace": {"status": "success"},
                }
            return {
                "ok": True,
                "frame": {
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,focused-fallback",
                    "frame_id": "active-focused-fallback",
                    "frame_hash": "hash-focused-fallback",
                    "desktop_context": {"foreground_app": "Safari", "window_title": "Problem page"},
                    "summary": "页面主内容区可见。",
                    "observations": [{"claim": "页面主内容区可见", "confidence": 0.86}],
                    "active_observation": {
                        "mode": "focus_target",
                        "target_id": "running:safari",
                        "desktop_targets": [],
                        "target_candidates": [
                            {
                                "target_id": "running:safari",
                                "source": "running_app",
                                "app": "Safari",
                                "title": "Safari",
                                "focusable": True,
                            }
                        ],
                        "focus_result": {"status": "success", "method": "activate_running_app"},
                        "verify_result": {"status": "captured", "frame_hash": "hash-focused-fallback"},
                        "detail_frames_count": 1,
                    },
                },
                "trace": {"status": "success"},
            }

        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch.object(
            backend_app,
            "_send_active_vision_capture_command",
            new=mock.AsyncMock(side_effect=capture_side_effect),
            create=True,
        ):
            resp = self.client.post(
                "/api/vision/observe",
                json={"text": "当前页面是什么？", "force": True},
                headers=self.token_headers,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["mode"], "desktop_survey")
        self.assertEqual(attempts[1]["mode"], "focus_target")
        self.assertEqual(attempts[1]["target_id"], "running:safari")
        active = body["context"]["active_observation"]
        self.assertEqual(active["desktop_targets"], [])
        self.assertEqual(active["target_candidates"][0]["source"], "running_app")
        self.assertEqual(active["selected_candidate"]["target_id"], "running:safari")
        self.assertEqual(active["focus_result"]["method"], "activate_running_app")
        self.assertEqual(active["verify_result"]["status"], "captured")
        self.assertEqual(active["detail_frames_count"], 1)
        self.assertEqual(body["target_candidates"][0]["target_id"], "running:safari")
        self.assertEqual(body["discovery_errors"], ["System Events failed: -10827"])

    def test_observe_endpoint_stops_repeated_frame_without_third_capture(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "active_observation": {"enabled": True, "timeout_sec": 20},
                "analyzer": {"enabled": False},
            }
        }
        attempts: list[dict[str, object]] = []

        async def capture_side_effect(_decision, _vision_cfg, **kwargs):
            attempts.append(dict(kwargs))
            if len(attempts) == 1:
                return {
                    "ok": True,
                    "frame": {
                        "mime_type": "image/png",
                        "data_url": "data:image/png;base64,first-repeat-seed",
                        "frame_id": "active-repeat-1",
                        "frame_hash": "hash-repeat",
                        "desktop_context": {"foreground_app": "Ipet", "window_title": "Ipet chat"},
                        "observations": [],
                        "active_observation": {
                            "mode": "desktop_survey",
                            "target_id": "desktop_survey",
                            "desktop_targets": [
                                {"target_id": "target-safari", "app": "Safari", "title": "Old tab"}
                            ],
                        },
                    },
                    "trace": {"status": "success"},
                }
            if len(attempts) == 2:
                return {
                    "ok": True,
                    "frame": {
                        "mime_type": "image/png",
                        "data_url": "data:image/png;base64,second-repeat",
                        "frame_id": "active-repeat-2",
                        "frame_hash": "hash-repeat",
                        "desktop_context": {"foreground_app": "Safari", "window_title": "Old tab"},
                        "observations": [],
                        "active_observation": {
                            "mode": "focus_target",
                            "target_id": "target-safari",
                            "desktop_targets": [
                                {"target_id": "target-safari", "app": "Safari", "title": "Old tab"}
                            ],
                        },
                    },
                    "trace": {"status": "success"},
                }
            return {
                "ok": True,
                "frame": {
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,new-target",
                    "frame_id": "active-new-target",
                    "frame_hash": "hash-new-target",
                    "desktop_context": {"foreground_app": "Preview", "window_title": "Current image"},
                    "summary": "当前图片窗口。",
                    "observations": [{"claim": "当前图片窗口可见", "confidence": 0.86}],
                },
                "trace": {"status": "success"},
            }

        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch.object(
            backend_app,
            "_send_active_vision_capture_command",
            new=mock.AsyncMock(side_effect=capture_side_effect),
            create=True,
        ):
            resp = self.client.post(
                "/api/vision/observe",
                json={
                    "text": "当前图片窗口里是什么？",
                    "force": True,
                    "target_hint": "frontmost",
                },
                headers=self.token_headers,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(attempts), 2)
        self.assertFalse(body["ok"])
        self.assertEqual(body["context"]["frame_id"], "active-repeat-2")
        active = body["context"]["active_observation"]
        self.assertTrue(active["stop"])
        self.assertEqual(active["relevance_hint"], "stop:repeated_frame")
        self.assertEqual(active["available_next_targets"], [])
        self.assertEqual(body["context"]["observations"], [])

    def test_active_observe_does_not_use_runtime_vision_fallback(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "active_observation": {"enabled": True},
                "analyzer": {"enabled": True, "provider": "", "fallback_to_runtime": True},
            }
        }
        host_frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,active-no-fallback",
            "frame_id": "active-no-fallback",
        }

        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch.object(
            backend_app,
            "_send_active_vision_capture_command",
            new=mock.AsyncMock(return_value={"ok": True, "frame": host_frame, "trace": {"status": "success"}}),
            create=True,
        ), mock.patch.object(
            backend_app, "_runtime_vision_fallback", new=mock.AsyncMock(return_value={})
        ) as fallback_mock:
            resp = self.client.post(
                "/api/vision/observe?lane=active&include_image=true",
                json={"text": "看一下当前屏幕", "force": True},
                headers=self.token_headers,
            )

        self.assertEqual(resp.status_code, 200)
        fallback_mock.assert_not_awaited()
        self.assertEqual(resp.json()["image_urls"], ["base64://active-no-fallback"])

    def test_passive_in_flight_keeps_latest_frame_and_does_not_block_active_observe(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "routing": {"stable_after_change_ms": 0, "vlm_cooldown_sec": 0},
                "analyzer": {"enabled": True, "provider": "macos_vision_ocr"},
                "active_observation": {"enabled": True},
            }
        }
        active_frame = {
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,active-while-passive-pending",
            "frame_id": "active-while-passive-pending",
            "observations": [{"claim": "主动观察成功", "confidence": 0.8}],
        }

        acquired = backend_app._VISION_ANALYSIS_LOCK.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
                backend_app, "_load_settings_config", return_value=settings
            ), mock.patch("backend.app.VisionAnalyzer") as analyzer_cls, mock.patch.object(
                backend_app,
                "_send_active_vision_capture_command",
                new=mock.AsyncMock(return_value={"ok": True, "frame": active_frame, "trace": {"status": "success"}}),
                create=True,
            ):
                from backend.vision_analyzer import merge_analysis_into_payload

                analyzer_cls.return_value.enrich_payload.side_effect = lambda payload: merge_analysis_into_payload(
                    payload,
                    settings["vision"]["analyzer"],
                    {"summary": "主动观察成功", "observations": payload.get("observations") or []},
                )
                first_resp = self.client.post(
                    "/api/vision/frame",
                    json={
                        "mime_type": "image/png",
                        "data_url": "data:image/png;base64,passive-old",
                        "frame_id": "passive-old",
                        "visual_hash": "0001",
                    },
                    headers=self.token_headers,
                )
                latest_resp = self.client.post(
                    "/api/vision/frame",
                    json={
                        "mime_type": "image/png",
                        "data_url": "data:image/png;base64,passive-new",
                        "frame_id": "passive-new",
                        "visual_hash": "0002",
                    },
                    headers=self.token_headers,
                )
                active_resp = self.client.post(
                    "/api/vision/observe?lane=active",
                    json={"text": "现在看一下屏幕", "force": True},
                    headers=self.token_headers,
                )
        finally:
            if backend_app._VISION_ANALYSIS_LOCK.locked():
                backend_app._VISION_ANALYSIS_LOCK.release()

        self.assertEqual(first_resp.status_code, 200)
        self.assertEqual(latest_resp.status_code, 200)
        self.assertEqual(latest_resp.json()["frame_id"], "passive-new")
        self.assertEqual(analyzer_cls.return_value.enrich_payload.call_count, 1)
        self.assertEqual(
            analyzer_cls.return_value.enrich_payload.call_args.args[0]["frame_id"],
            "active-while-passive-pending",
        )
        self.assertEqual(active_resp.status_code, 200)
        self.assertTrue(active_resp.json()["ok"])
        self.assertEqual(active_resp.json()["context"]["frame_id"], "active-while-passive-pending")

    def test_observe_endpoint_permission_failure_returns_unknowns_without_fake_observations(self) -> None:
        settings = {"vision": {"enabled": True, "active_observation": {"enabled": True}}}
        service = VisionService({"enabled": True}, now=lambda: 10.0)
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,old-passive",
                "frame_id": "old-passive",
                "summary": "旧 passive 帧。",
                "observations": [{"claim": "旧 passive 观察", "confidence": 0.8}],
            }
        )
        backend_app._VISION_SERVICE = service
        host_result = {
            "ok": False,
            "error": "screen recording permission denied",
            "trace": {"target_hint": "browser", "status": "error", "unknowns": ["screen recording permission denied"]},
        }

        with mock.patch.dict(os.environ, {"IPET_LOCAL_API_TOKEN": "vision-test-token"}), mock.patch.object(
            backend_app, "_load_settings_config", return_value=settings
        ), mock.patch.object(
            backend_app,
            "_send_active_vision_capture_command",
            new=mock.AsyncMock(return_value=host_result),
            create=True,
        ):
            resp = self.client.post(
                "/api/vision/observe",
                json={"text": "当前窗口是什么？", "force": True},
                headers=self.token_headers,
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertFalse(body["context"]["grounded"])
        self.assertEqual(body["context"]["observations"], [])
        self.assertEqual(body["context"]["lane"], "active")
        self.assertFalse(body["context"]["available"])
        self.assertIn("screen recording permission denied", "\n".join(body["context"]["unknowns"]))
        self.assertNotIn("旧 passive 观察", resp.text)

    def test_chat_stream_injects_grounded_evidence_for_visual_question(self) -> None:
        service = VisionService({"enabled": True, "inject_policy": "when_requested"}, now=lambda: 10.0)
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,abc",
                "frame_id": "screen-1",
                "frame_hash": "hash-1",
                "summary": "设置窗口可见。",
                "observations": [{"claim": "自动视觉标题可见", "evidence": "页面中部标题为自动视觉", "confidence": 0.9}],
            }
        )
        backend_app._VISION_SERVICE = service
        fake = _FakeRuntimeClient()

        passive_settings = {
            "vision": {
                "enabled": True,
                "inject_policy": "when_requested",
                "active_observation": {"enabled": False},
                "passive_capture": {"use_for_forced": True},
            }
        }
        with mock.patch.object(backend_app, "_load_settings_config", return_value=passive_settings), mock.patch.object(
            backend_app, "_get_runtime_client", return_value=fake
        ):
            resp = self.client.post("/api/chat/stream", json={"text": "请看屏幕", "expression_mode": False})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("event: done", resp.text)
        injected_text = fake.requests[0][2]["text"]
        self.assertIn("[强制视觉证据]", injected_text)
        self.assertIn("主动视觉证据", injected_text)
        self.assertIn("active observation disabled", injected_text)
        self.assertIn("被动后台视觉", injected_text)
        self.assertIn("页面中部标题为自动视觉", injected_text)
        self.assertIn("请看屏幕", injected_text)

        fake.requests.clear()
        with mock.patch.object(backend_app, "_load_settings_config", return_value=passive_settings), mock.patch.object(
            backend_app, "_get_runtime_client", return_value=fake
        ):
            resp = self.client.post("/api/chat/stream", json={"text": "普通聊天", "expression_mode": False})

        self.assertEqual(resp.status_code, 200)
        plain_text = fake.requests[0][2]["text"]
        self.assertIn("普通聊天", plain_text)
        self.assertNotIn("强制视觉证据", plain_text)
        self.assertNotIn("主动视觉证据", plain_text)

    def test_chat_stream_visual_question_without_evidence_requires_refusal(self) -> None:
        service = VisionService({"enabled": True, "inject_policy": "when_requested"}, now=lambda: 10.0)
        service.update_frame({"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"})
        backend_app._VISION_SERVICE = service
        fake = _FakeRuntimeClient()

        with mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={
                "vision": {
                    "enabled": True,
                    "inject_policy": "when_requested",
                    "active_observation": {"enabled": False},
                    "passive_capture": {"use_for_forced": True},
                }
            },
        ), mock.patch.object(backend_app, "_get_runtime_client", return_value=fake):
            resp = self.client.post("/api/chat/stream", json={"text": "你看到屏幕上是什么？", "expression_mode": False})

        self.assertEqual(resp.status_code, 200)
        injected_text = fake.requests[0][2]["text"]
        self.assertIn("[强制视觉证据]", injected_text)
        self.assertIn("主动视觉证据", injected_text)
        self.assertIn("当前没有新鲜、可验证的屏幕观察结果", injected_text)
        self.assertIn("active observation disabled", injected_text)
        self.assertIn("我无法从当前截图确认", injected_text)

    def test_chat_stream_force_grounding_config_affects_non_visual_question(self) -> None:
        service = VisionService({"enabled": True, "inject_policy": "when_requested", "force_grounding": True}, now=lambda: 10.0)
        service.update_frame({"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,abc"})
        backend_app._VISION_SERVICE = service
        fake = _FakeRuntimeClient()

        with mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={
                "vision": {
                    "enabled": True,
                    "inject_policy": "when_requested",
                    "force_grounding": True,
                    "active_observation": {"enabled": False},
                }
            },
        ), mock.patch.object(backend_app, "_get_runtime_client", return_value=fake):
            resp = self.client.post("/api/chat/stream", json={"text": "普通聊天", "expression_mode": False})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("[强制视觉证据]", fake.requests[0][2]["text"])

    def test_chat_stream_visual_question_triggers_active_observation_before_runtime(self) -> None:
        fake = _FakeRuntimeClient()
        active_context = {
            "enabled": True,
            "available": True,
            "grounded": True,
            "frame_id": "active-2",
            "frame_hash": "hash-active-2",
            "captured_at": 10.0,
            "age_sec": 0.0,
            "verified_summary": "主动观察确认浏览器页面。",
            "observations": [
                {
                    "claim": "浏览器显示 Ipet 项目页面",
                    "evidence": "主动截图中的页面标题为 Ipet",
                    "confidence": 0.9,
                }
            ],
            "unknowns": [],
        }
        active_mock = mock.AsyncMock(return_value={"ok": True, "context": active_context, "decision": {"target_hint": "browser"}})

        with mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={"vision": {"enabled": True, "inject_policy": "when_requested", "active_observation": {"enabled": True}}},
        ), mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_perform_active_vision_observation", new=active_mock, create=True
        ):
            resp = self.client.post("/api/chat/stream", json={"text": "浏览器打开的是什么网站？", "expression_mode": False})

        self.assertEqual(resp.status_code, 200)
        active_mock.assert_awaited_once()
        injected_text = fake.requests[0][2]["text"]
        self.assertIn("frame_id=active-2", injected_text)
        self.assertIn("浏览器显示 Ipet 项目页面", injected_text)

    def test_chat_stream_astrbot_visual_question_attaches_active_screenshot_for_model_viewing(self) -> None:
        service = VisionService({"enabled": True, "inject_policy": "when_requested"}, now=lambda: 10.0)
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,passive-astrbot",
                "frame_id": "passive-astrbot",
                "summary": "后台观察到浏览器窗口切换。",
                "important_objects": ["浏览器"],
                "visible_text": ["Ipet"],
                "confidence": 0.7,
                "route_decision": {
                    "action": "analyze",
                    "reason": "change_detected",
                    "events": ["initial_frame"],
                    "should_analyze": True,
                },
            }
        )
        backend_app._VISION_SERVICE = service
        fake = _FakeRuntimeClient()
        fake.runtime_id = "astrbot"
        active_context = {
            "enabled": True,
            "available": True,
            "grounded": True,
            "frame_id": "active-astrbot",
            "frame_hash": "hash-active-astrbot",
            "captured_at": 10.0,
            "age_sec": 0.0,
            "verified_summary": "主动观察确认浏览器页面。",
            "observations": [
                {
                    "claim": "浏览器页面标题显示 Ipet",
                    "evidence": "主动观察截图中的标签页标题包含 Ipet",
                    "confidence": 0.91,
                }
            ],
            "unknowns": [],
            "active_observation": {"attempted_targets": ["browser"], "target_hint": "browser"},
        }
        active_mock = mock.AsyncMock(
            return_value={
                "ok": True,
                "context": active_context,
                "decision": {"target_hint": "browser"},
                "inline_frame": {
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,raw-astrbot-image",
                    "frame_id": "active-astrbot",
                    "frame_hash": "hash-active-astrbot",
                },
            }
        )

        with mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={
                "vision": {
                    "enabled": True,
                    "inject_policy": "when_requested",
                    "active_observation": {"enabled": True},
                    "analyzer": {"enabled": True, "provider": "", "fallback_to_runtime": True},
                }
            },
        ), mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_perform_active_vision_observation", new=active_mock, create=True
        ):
            resp = self.client.post("/api/chat/stream", json={"text": "浏览器打开的是什么网站？", "expression_mode": False})

        self.assertEqual(resp.status_code, 200)
        active_mock.assert_awaited_once()
        payload = fake.requests[0][2]
        injected_text = payload["text"]
        self.assertIn("主动视觉证据", injected_text)
        self.assertIn("浏览器页面标题显示 Ipet", injected_text)
        self.assertIn("被动后台视觉", injected_text)
        self.assertIn("后台观察到浏览器窗口切换。", injected_text)
        self.assertIn("用户消息：浏览器打开的是什么网站？", injected_text)
        self.assertEqual(payload["vision_frame"]["data_url"], "data:image/png;base64,raw-astrbot-image")
        self.assertEqual(payload["vision_frame"]["frame_id"], "active-astrbot")

    def test_chat_stream_attaches_active_screenshot_even_when_structured_evidence_is_missing(self) -> None:
        fake = _FakeRuntimeClient()
        fake.runtime_id = "astrbot"
        active_mock = mock.AsyncMock(
            return_value={
                "ok": False,
                "context": {
                    "enabled": True,
                    "available": True,
                    "grounded": False,
                    "frame_id": "active-menu-only",
                    "frame_hash": "hash-menu-only",
                    "captured_at": 10.0,
                    "age_sec": 0.0,
                    "observations": [],
                    "unknowns": ["VLM 尚未返回结构化画面证据"],
                    "active_observation": {"target_hint": "frontmost", "attempted_targets": ["frontmost"]},
                },
                "decision": {"target_hint": "frontmost"},
                "inline_frame": {
                    "mime_type": "image/jpeg",
                    "data_url": "data:image/jpeg;base64,real-screenshot",
                    "frame_id": "active-menu-only",
                    "frame_hash": "hash-menu-only",
                },
            }
        )

        with mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={
                "vision": {
                    "enabled": True,
                    "inject_policy": "when_requested",
                    "active_observation": {"enabled": True},
                    "analyzer": {"enabled": True, "provider": "openai_compatible_vlm"},
                }
            },
        ), mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_perform_active_vision_observation", new=active_mock, create=True
        ):
            resp = self.client.post(
                "/api/chat/stream",
                json={"text": "就描述一下你看到的我电脑的画面", "expression_mode": False},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(active_mock.await_args.kwargs.get("defer_runtime_analysis"))
        payload = fake.requests[0][2]
        self.assertIn("主动视觉证据", payload["text"])
        self.assertIn("直接看本轮附带的 active screenshot/vision_frames", payload["text"])
        self.assertNotIn("必须回答“我无法从当前截图确认”", payload["text"])
        self.assertEqual(payload["vision_frame"]["data_url"], "data:image/jpeg;base64,real-screenshot")
        self.assertNotIn("菜单栏左上角显示当前前台应用", payload["text"])

    def test_deferred_active_observe_returns_first_screenshot_without_waiting_for_analyzer_or_retry(self) -> None:
        settings = {
            "vision": {
                "enabled": True,
                "active_observation": {"enabled": True, "timeout_sec": 20},
                "analyzer": {"enabled": True, "provider": "openai_compatible_vlm"},
            }
        }
        capture = mock.AsyncMock(
            return_value={
                "ok": True,
                "frame": {
                    "mime_type": "image/jpeg",
                    "data_url": "data:image/jpeg;base64,current-screen",
                    "frame_id": "active-current",
                    "frame_hash": "hash-current",
                    "desktop_context": {"foreground_app": "Chrome", "window_title": "Problem list"},
                    "observations": [],
                    "active_observation": {
                        "mode": "desktop_survey",
                        "target_id": "desktop_survey",
                        "target_candidates": [
                            {
                                "target_id": "running:finder",
                                "source": "running_app",
                                "app": "Finder",
                                "title": "Finder",
                                "focusable": True,
                            }
                        ],
                    },
                },
                "trace": {"status": "success"},
            }
        )
        analyzer = mock.AsyncMock(side_effect=AssertionError("deferred chat path must not run analyzer first"))

        with mock.patch.object(backend_app, "_send_active_vision_capture_command", new=capture, create=True), mock.patch.object(
            backend_app, "_enrich_vision_payload", new=analyzer, create=True
        ):
            result = asyncio.run(
                backend_app._perform_active_vision_observation(
                    "请直接看当前截图",
                    settings,
                    force=True,
                    defer_runtime_analysis=True,
                )
            )

        self.assertEqual(capture.await_count, 1)
        analyzer.assert_not_awaited()
        self.assertEqual(result["inline_frame"]["frame_id"], "active-current")
        self.assertEqual(result["trace"]["relevance_hint"], "likely_relevant:image_attached")
        self.assertTrue(result["trace"]["stop"])
        self.assertEqual(result["trace"]["available_next_targets"], [])

    def test_chat_stream_passes_active_vision_frames_while_preserving_single_frame(self) -> None:
        fake = _FakeRuntimeClient()
        fake.runtime_id = "astrbot"
        active_mock = mock.AsyncMock(
            return_value={
                "ok": False,
                "context": {
                    "enabled": True,
                    "available": True,
                    "grounded": False,
                    "frame_id": "active-main",
                    "frame_hash": "hash-main",
                    "observations": [],
                    "unknowns": ["文字不清晰"],
                    "active_observation": {"target_hint": "desktop_survey", "attempted_targets": ["desktop_survey"]},
                },
                "inline_frame": {
                    "mime_type": "image/jpeg",
                    "data_url": "data:image/jpeg;base64,main-frame",
                    "frame_id": "active-main",
                    "frame_hash": "hash-main",
                },
                "inline_frames": [
                    {
                        "mime_type": "image/jpeg",
                        "data_url": "data:image/jpeg;base64,main-frame",
                        "frame_id": "active-main",
                        "frame_hash": "hash-main",
                    },
                    {
                        "mime_type": "image/jpeg",
                        "data_url": "data:image/jpeg;base64,detail-frame",
                        "frame_id": "active-detail-1",
                        "frame_hash": "hash-detail",
                    },
                ],
            }
        )

        with mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={"vision": {"enabled": True, "active_observation": {"enabled": True}}},
        ), mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_perform_active_vision_observation", new=active_mock, create=True
        ):
            resp = self.client.post(
                "/api/chat/stream",
                json={"text": "请读一下屏幕上的文字", "expression_mode": False},
            )

        self.assertEqual(resp.status_code, 200)
        payload = fake.requests[0][2]
        self.assertIn("直接看本轮附带的 active screenshot/vision_frames", payload["text"])
        self.assertIn("detail crop/局部图只辅助读字", payload["text"])
        self.assertEqual(payload["vision_frame"]["data_url"], "data:image/jpeg;base64,main-frame")
        self.assertEqual([frame["frame_id"] for frame in payload["vision_frames"]], ["active-main", "active-detail-1"])

    def test_chat_stream_astrbot_active_observe_insufficient_evidence_injects_refusal_guard(self) -> None:
        fake = _FakeRuntimeClient()
        fake.runtime_id = "astrbot"
        active_mock = mock.AsyncMock(
            return_value={
                "ok": False,
                "context": {
                    "enabled": True,
                    "available": True,
                    "grounded": False,
                    "frame_id": "active-empty",
                    "frame_hash": "hash-active-empty",
                    "captured_at": 10.0,
                    "age_sec": 0.0,
                    "observations": [],
                    "unknowns": ["OCR 没有识别到相关文字"],
                    "active_observation": {
                        "target_hint": "browser",
                        "attempted_targets": ["browser", "frontmost"],
                        "relevance_hint": "insufficient_evidence",
                    },
                },
                "decision": {"target_hint": "browser"},
            }
        )

        with mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={
                "vision": {
                    "enabled": True,
                    "inject_policy": "when_requested",
                    "active_observation": {"enabled": True},
                    "analyzer": {"enabled": True, "provider": "macos_vision_ocr", "fallback_to_runtime": True},
                }
            },
        ), mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_perform_active_vision_observation", new=active_mock, create=True
        ):
            resp = self.client.post("/api/chat/stream", json={"text": "浏览器打开的是什么网站？", "expression_mode": False})

        self.assertEqual(resp.status_code, 200)
        active_mock.assert_awaited_once()
        payload = fake.requests[0][2]
        injected_text = payload["text"]
        self.assertIn("主动视觉证据", injected_text)
        self.assertIn("无法从当前截图确认", injected_text)
        self.assertIn("已尝试目标：browser, frontmost", injected_text)
        self.assertIn("OCR 没有识别到相关文字", injected_text)
        self.assertNotIn("vision_frame", payload)

    def test_chat_stream_plain_chat_does_not_trigger_active_observation(self) -> None:
        fake = _FakeRuntimeClient()
        active_mock = mock.AsyncMock(return_value={})

        with mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={"vision": {"enabled": True, "active_observation": {"enabled": True}}},
        ), mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_perform_active_vision_observation", new=active_mock, create=True
        ):
            resp = self.client.post("/api/chat/stream", json={"text": "普通聊天", "expression_mode": False})

        self.assertEqual(resp.status_code, 200)
        active_mock.assert_not_awaited()
        self.assertEqual(fake.requests[0][2]["text"], "用户消息：普通聊天")

    def test_chat_stream_grounding_always_ignores_old_passive_frame_when_active_fails(self) -> None:
        service = VisionService({"enabled": True, "inject_policy": "when_requested", "grounding_mode": "always"}, now=lambda: 10.0)
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,old",
                "frame_id": "old-passive",
                "summary": "旧周期截图。",
                "observations": [{"claim": "旧周期截图里有设置页", "evidence": "被动截图", "confidence": 0.7}],
            }
        )
        backend_app._VISION_SERVICE = service
        fake = _FakeRuntimeClient()
        active_mock = mock.AsyncMock(
            return_value={
                "ok": False,
                "context": {
                    "enabled": True,
                    "available": False,
                    "grounded": False,
                    "observations": [],
                    "unknowns": ["active observation host unavailable"],
                },
                "decision": {"target_hint": "frontmost"},
            }
        )

        with mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={
                "vision": {
                    "enabled": True,
                    "inject_policy": "when_requested",
                    "grounding_mode": "always",
                    "active_observation": {"enabled": True},
                    "passive_capture": {"use_for_forced": False},
                }
            },
        ), mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_perform_active_vision_observation", new=active_mock, create=True
        ):
            resp = self.client.post("/api/chat/stream", json={"text": "普通聊天", "expression_mode": False})

        self.assertEqual(resp.status_code, 200)
        injected_text = fake.requests[0][2]["text"]
        self.assertIn("我无法从当前截图确认", injected_text)
        self.assertIn("active observation host unavailable", injected_text)
        self.assertIn("被动后台视觉", injected_text)
        self.assertIn("旧周期截图", injected_text)

    def test_astrbot_grounding_always_runs_active_observe_and_injects_passive_background(self) -> None:
        service = VisionService({"enabled": True, "inject_policy": "when_requested", "grounding_mode": "always"}, now=lambda: 10.0)
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,passive",
                "frame_id": "passive-1",
                "summary": "后台观察到设置页。",
                "important_objects": ["设置页"],
                "visible_text": ["自动视觉"],
                "confidence": 0.8,
                "route_decision": {
                    "action": "analyze",
                    "reason": "change_detected",
                    "events": ["initial_frame"],
                    "should_analyze": True,
                },
            }
        )
        backend_app._VISION_SERVICE = service
        fake = _FakeRuntimeClient()
        fake.runtime_id = "astrbot"
        active_mock = mock.AsyncMock(
            return_value={
                "ok": False,
                "context": {
                    "enabled": True,
                    "available": False,
                    "grounded": False,
                    "observations": [],
                    "unknowns": ["active observation host unavailable"],
                    "active_observation": {"target_hint": "current_desktop", "attempted_targets": ["current_desktop"]},
                },
                "decision": {"target_hint": "current_desktop"},
            }
        )

        with mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={
                "runtime": {"active": "astrbot"},
                "vision": {
                    "enabled": True,
                    "inject_policy": "when_requested",
                    "grounding_mode": "always",
                    "active_observation": {"enabled": True},
                },
            },
        ), mock.patch.object(backend_app, "_get_runtime_client", return_value=fake), mock.patch.object(
            backend_app, "_perform_active_vision_observation", new=active_mock, create=True
        ):
            resp = self.client.post("/api/chat/stream", json={"text": "普通聊天", "expression_mode": False})

        self.assertEqual(resp.status_code, 200)
        active_mock.assert_awaited_once()
        injected_text = fake.requests[0][2]["text"]
        self.assertIn("主动视觉证据", injected_text)
        self.assertIn("我无法从当前截图确认", injected_text)
        self.assertIn("已尝试目标：current_desktop", injected_text)
        self.assertIn("被动后台视觉", injected_text)
        self.assertIn("最近屏幕变化：", injected_text)
        self.assertIn("后台观察到设置页。", injected_text)
        self.assertIn("用户消息：普通聊天", injected_text)

    def test_chat_stream_forced_visual_marks_passive_frame_as_background_when_active_disabled(self) -> None:
        service = VisionService({"enabled": True, "inject_policy": "when_requested"}, now=lambda: 10.0)
        service.update_frame(
            {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,old",
                "frame_id": "old-passive",
                "summary": "旧周期截图。",
                "observations": [{"claim": "旧周期截图里有 YouTube", "evidence": "被动截图", "confidence": 0.7}],
            }
        )
        backend_app._VISION_SERVICE = service
        fake = _FakeRuntimeClient()

        with mock.patch.object(
            backend_app,
            "_load_settings_config",
            return_value={
                "vision": {
                    "enabled": True,
                    "inject_policy": "when_requested",
                    "active_observation": {"enabled": False},
                    "passive_capture": {"use_for_forced": False},
                }
            },
        ), mock.patch.object(backend_app, "_get_runtime_client", return_value=fake):
            resp = self.client.post("/api/chat/stream", json={"text": "浏览器打开的是什么网站？", "expression_mode": False})

        self.assertEqual(resp.status_code, 200)
        injected_text = fake.requests[0][2]["text"]
        self.assertIn("我无法从当前截图确认", injected_text)
        self.assertIn("active observation disabled", injected_text)
        self.assertIn("被动后台视觉", injected_text)
        self.assertIn("旧周期截图", injected_text)


if __name__ == "__main__":
    unittest.main()
