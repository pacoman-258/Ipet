from __future__ import annotations

import importlib
import unittest
from pathlib import Path
from types import SimpleNamespace

import backend.app as backend_app
from backend import app_adapters


class BackendObserveResultSplitTests(unittest.TestCase):
    def _observe_result_module(self):
        try:
            return importlib.import_module("backend.observe_result")
        except ModuleNotFoundError:
            self.fail("backend.observe_result module should exist")

    def _observation_text_from_result(self, result: dict) -> str:
        return self._observe_result_module().observation_text_from_result(result)

    def test_observe_result_module_does_not_import_backend_app(self) -> None:
        module = self._observe_result_module()
        source = Path(module.__file__).read_text(encoding="utf-8")

        self.assertNotIn("backend.app", source)
        self.assertNotIn("from .app", source)

    def test_backend_app_observation_text_entrypoint_is_thin_wrapper(self) -> None:
        source = Path(backend_app.__file__).read_text(encoding="utf-8")
        adapter_source = Path(app_adapters.__file__).read_text(encoding="utf-8")

        self.assertIs(backend_app._observation_text_from_result, app_adapters._observation_text_from_result)
        self.assertIn("_observe_result_helpers.observation_text_from_result(result)", adapter_source)
        self.assertNotIn("observe_answer", source)
        self.assertNotIn("observations[:3]", source)
        self.assertNotIn("foreground_app", source)
        self.assertNotIn("last_error", source)

    def test_observe_answer_takes_priority_over_observation_summaries(self) -> None:
        text = self._observation_text_from_result(
            {
                "frame": {
                    "observe_answer": "Dock 中可见微信图标。",
                    "observations": [{"claim": "这条摘要不应该覆盖 observe_answer"}],
                    "foreground_app": {"name": "Finder"},
                }
            }
        )

        self.assertEqual(text, "Dock 中可见微信图标。")

    def test_observations_are_summarized_from_first_three_claim_shapes(self) -> None:
        text = self._observation_text_from_result(
            {
                "frame": {
                    "observations": [
                        {"text": "第一条可见内容"},
                        {"claim": "第二条可见内容"},
                        {"summary": "第三条可见内容"},
                        {"text": "第四条不应出现"},
                    ]
                }
            }
        )

        self.assertEqual(text, "我看到：第一条可见内容；第二条可见内容；第三条可见内容")

    def test_foreground_app_fallback_uses_foreground_and_active_observation(self) -> None:
        text = self._observation_text_from_result(
            {
                "frame": {
                    "foreground_app": {"name": "Safari"},
                    "active_observation": {"window_title": "Neo Aspect Notes"},
                }
            }
        )

        self.assertEqual(text, "我看了一下屏幕，前台大概是：Safari · Neo Aspect Notes")

    def test_unknowns_filter_metadata_noise_before_screenshot_fallback(self) -> None:
        text = self._observation_text_from_result(
            {
                "frame": {
                    "data_url": "data:image/png;base64,abc",
                    "capture_backend": "macos_screencapture",
                    "unknowns": [
                        "无法读取 macOS 前台应用元数据：Command '['osascript'] timed out",
                        "active vision metadata observation failed",
                    ],
                }
            }
        )

        self.assertIn("截取了当前屏幕", text)
        self.assertNotIn("不能确认内容", text)
        self.assertNotIn("osascript", text)
        self.assertNotIn("无法读取 macOS 前台应用元数据", text)

    def test_unknowns_report_meaningful_analysis_error(self) -> None:
        text = self._observation_text_from_result(
            {
                "frame": {
                    "analysis": {"last_error": "observe model timeout"},
                }
            }
        )

        self.assertEqual(text, "我试着观察了屏幕，但还不能确认内容：observe model timeout")

    def test_analysis_enabled_status_messages_use_screenshot_context(self) -> None:
        base_frame = {
            "data_url": "data:image/jpeg;base64,abc",
            "capture_backend": "macos_screencapture",
        }

        ok_text = self._observation_text_from_result(
            {
                "frame": {
                    **base_frame,
                    "analysis": {"enabled": True, "provider": "openai_compatible_vlm", "status": "ok"},
                }
            }
        )
        timeout_text = self._observation_text_from_result(
            {
                "frame": {
                    **base_frame,
                    "analysis": {"enabled": True, "provider": "openai_compatible_vlm", "status": "timeout"},
                }
            }
        )

        self.assertIn("也调用了 observe 模型", ok_text)
        self.assertIn("没有返回可见内容", ok_text)
        self.assertIn("状态为 timeout", timeout_text)

    def test_screenshot_fallback_reports_capture_backend(self) -> None:
        text = self._observation_text_from_result(
            {
                "frame": {
                    "data_url": "data:image/png;base64,abc",
                    "capture_backend": "screen_capture",
                }
            }
        )

        self.assertEqual(text, "我已经截取了当前屏幕（screen_capture），但还没有提取到明确内容。")

    def test_enrich_observation_frame_with_model_is_config_driven(self) -> None:
        module = self._observe_result_module()

        class FakeAnalyzer:
            calls: list[dict] = []

            def __init__(self, config: dict):
                self.config = config
                FakeAnalyzer.calls.append(config)

            def enrich_payload(self, frame: dict):
                return SimpleNamespace(payload={**frame, "observe_answer": "模型整理后的结果"})

        frame = {"data_url": "data:image/png;base64,abc"}

        disabled = module.enrich_observation_frame_with_model(frame, {"enabled": False}, analyzer_class=FakeAnalyzer)
        enriched = module.enrich_observation_frame_with_model(
            frame,
            {"enabled": True, "provider": "fake"},
            analyzer_class=FakeAnalyzer,
        )

        self.assertIs(disabled, frame)
        self.assertEqual(FakeAnalyzer.calls, [{"enabled": True, "provider": "fake"}])
        self.assertEqual(enriched["observe_answer"], "模型整理后的结果")


if __name__ == "__main__":
    unittest.main()
