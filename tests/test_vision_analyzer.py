from __future__ import annotations

import subprocess
import unittest

import httpx

from backend.vision_analyzer import VisionAnalyzer, merge_observations


PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgo="


class VisionAnalyzerTests(unittest.TestCase):
    def test_disabled_analyzer_returns_payload_unchanged(self) -> None:
        payload = {"mime_type": "image/png", "data_url": PNG_DATA_URL, "observations": [{"claim": "caller"}]}
        analyzer = VisionAnalyzer({"enabled": False, "provider": "macos_vision_ocr"})

        result = analyzer.enrich_payload(payload)

        self.assertEqual(result.payload, payload)
        self.assertEqual(result.status["status"], "disabled")

    def test_macos_ocr_runner_output_produces_bounded_observation(self) -> None:
        long_text = "设置 自动视觉 " + ("很长 " * 200)
        calls: list[list[str]] = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=long_text, stderr="")

        analyzer = VisionAnalyzer(
            {
                "enabled": True,
                "provider": "macos_vision_ocr",
                "timeout_sec": 0.5,
                "max_text_chars": 60,
            },
            runner=runner,
        )

        result = analyzer.enrich_payload({"mime_type": "image/png", "data_url": PNG_DATA_URL})

        self.assertTrue(calls)
        self.assertEqual(result.status["status"], "ok")
        observation = result.payload["observations"][0]
        self.assertEqual(observation["source"], "macos-vision-ocr")
        self.assertEqual(observation["region"], "screen_ocr")
        self.assertIn("屏幕上可见文字", observation["claim"])
        self.assertLessEqual(len(observation["claim"]), 240)
        self.assertLessEqual(len(observation["evidence"]), 360)
        self.assertLessEqual(len(observation["evidence"]), 80)

    def test_openai_compatible_vlm_generates_visual_observations(self) -> None:
        calls = []

        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def post(self, url, *, json, headers):
                calls.append((url, json, headers, self.kwargs))
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json={
                        "choices": [
                            {
                                "message": {
                                    "content": """
                                    {
                                      "summary": "屏幕中央有一个动漫风格女孩角色，右侧有聊天窗口。",
                                      "observations": [
                                        {
                                          "claim": "屏幕中央偏左可见动漫风格女孩角色",
                                          "evidence": "画面中有人形角色、棕色头发和校服样式服装",
                                          "region": "center-left",
                                          "confidence": 0.82
                                        }
                                      ],
                                      "unknowns": []
                                    }
                                    """
                                }
                            }
                        ]
                    },
                )

        analyzer = VisionAnalyzer(
            {
                "enabled": True,
                "provider": "openai_compatible_vlm",
                "base_url": "https://vlm.example/v1",
                "model": "demo-vlm",
                "api_key": "typed-vlm-key",
                "api_key_env": "",
                "image_detail": "low",
            },
            http_client_factory=FakeClient,
        )

        result = analyzer.enrich_payload({"mime_type": "image/png", "data_url": PNG_DATA_URL})

        self.assertEqual(result.status["status"], "ok")
        self.assertEqual(result.payload["observations"][0]["source"], "openai-compatible-vlm")
        self.assertIn("动漫风格女孩角色", result.payload["observations"][0]["claim"])
        self.assertEqual(calls[0][0], "https://vlm.example/v1/chat/completions")
        self.assertEqual(calls[0][2]["Authorization"], "Bearer typed-vlm-key")
        content = calls[0][1]["messages"][1]["content"]
        self.assertEqual(content[1]["image_url"]["url"], PNG_DATA_URL)
        self.assertEqual(content[1]["image_url"]["detail"], "low")

    def test_unavailable_custom_vlm_provider_produces_error_for_runtime_fallback(self) -> None:
        analyzer = VisionAnalyzer({"enabled": True, "provider": "custom_provider"})

        result = analyzer.enrich_payload({"mime_type": "image/png", "data_url": PNG_DATA_URL})

        self.assertEqual(result.status["status"], "error")
        self.assertIn("provider unavailable", result.status["last_error"])
        self.assertIn("unknowns", result.payload)

    def test_analyzer_failure_produces_unknowns_and_last_error_without_fabricated_claim(self) -> None:
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="vision failed")

        analyzer = VisionAnalyzer(
            {"enabled": True, "provider": "macos_vision_ocr", "timeout_sec": 0.5},
            runner=runner,
        )

        result = analyzer.enrich_payload({"mime_type": "image/png", "data_url": PNG_DATA_URL})

        self.assertEqual(result.payload.get("observations"), None)
        self.assertIn("unknowns", result.payload)
        self.assertIn("vision failed", result.status["last_error"])
        self.assertEqual(result.payload["analysis"]["last_error"], result.status["last_error"])

    def test_merge_preserves_caller_observations_and_deduplicates_analyzer_duplicates(self) -> None:
        caller = [{"claim": "屏幕显示设置页", "source": "unit", "confidence": 0.8}]
        analyzer = [
            {"claim": "屏幕显示设置页", "source": "unit", "confidence": 0.9},
            {"claim": "屏幕上可见文字：自动视觉", "source": "macos-vision-ocr", "confidence": 0.74},
        ]

        merged = merge_observations(caller, analyzer)

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["claim"], "屏幕显示设置页")
        self.assertEqual(merged[0]["confidence"], 0.8)
        self.assertEqual(merged[1]["source"], "macos-vision-ocr")

    def test_data_url_remains_absent_from_result_status(self) -> None:
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="自动视觉", stderr="")

        analyzer = VisionAnalyzer({"enabled": True, "provider": "macos_vision_ocr"}, runner=runner)

        result = analyzer.enrich_payload({"mime_type": "image/png", "data_url": PNG_DATA_URL})

        self.assertNotIn("data_url", str(result.status))
        self.assertNotIn("data_url", str(result.payload["analysis"]))


if __name__ == "__main__":
    unittest.main()
