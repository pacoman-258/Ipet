from __future__ import annotations

import os
import subprocess
import unittest
from unittest import mock

import httpx

from backend.vision_analyzer import VisionAnalyzer, build_vlm_user_prompt, merge_observations


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
                                    "content": "屏幕中央偏左可见动漫风格女孩角色，右侧有聊天窗口。"
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
        self.assertEqual(result.payload["observe_answer"], "屏幕中央偏左可见动漫风格女孩角色，右侧有聊天窗口。")
        self.assertEqual(calls[0][0], "https://vlm.example/v1/chat/completions")
        self.assertEqual(calls[0][2]["Authorization"], "Bearer typed-vlm-key")
        self.assertIs(calls[0][3]["trust_env"], False)
        self.assertIn("natural language", calls[0][1]["messages"][0]["content"])
        self.assertNotIn("Return only a JSON object", calls[0][1]["messages"][0]["content"])
        content = calls[0][1]["messages"][1]["content"]
        self.assertEqual(content[1]["image_url"]["url"], PNG_DATA_URL)
        self.assertEqual(content[1]["image_url"]["detail"], "low")

    def test_remote_vlm_uses_http_proxy_env_without_socks_all_proxy(self) -> None:
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
                    json={"choices": [{"message": {"content": "可以看到屏幕。"}}]},
                )

        env = {
            "HTTPS_PROXY": "http://127.0.0.1:10808",
            "HTTP_PROXY": "http://127.0.0.1:10808",
            "ALL_PROXY": "socks5://127.0.0.1:10808",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            analyzer = VisionAnalyzer(
                {
                    "enabled": True,
                    "provider": "openai_compatible_vlm",
                    "base_url": "https://vlm.example/v1",
                    "model": "demo-vlm",
                },
                http_client_factory=FakeClient,
            )

            result = analyzer.enrich_payload({"mime_type": "image/png", "data_url": PNG_DATA_URL})

        self.assertEqual(result.status["status"], "ok")
        self.assertIs(calls[0][3]["trust_env"], False)
        self.assertEqual(calls[0][3]["proxy"], "http://127.0.0.1:10808")

    def test_local_vlm_does_not_proxy_loopback_endpoint(self) -> None:
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
                    json={"choices": [{"message": {"content": "本地模型回复。"}}]},
                )

        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:10808"}, clear=True):
            analyzer = VisionAnalyzer(
                {
                    "enabled": True,
                    "provider": "local_vlm",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "model": "local-vlm",
                },
                http_client_factory=FakeClient,
            )

            result = analyzer.enrich_payload({"mime_type": "image/png", "data_url": PNG_DATA_URL})

        self.assertEqual(result.status["status"], "ok")
        self.assertIs(calls[0][3]["trust_env"], False)
        self.assertNotIn("proxy", calls[0][3])

    def test_local_vlm_provider_does_not_proxy_lan_endpoint(self) -> None:
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
                    json={"choices": [{"message": {"content": "局域网本地模型回复。"}}]},
                )

        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:10808"}, clear=True):
            analyzer = VisionAnalyzer(
                {
                    "enabled": True,
                    "provider": "local_vlm",
                    "base_url": "http://192.168.1.20:11434/v1",
                    "model": "local-vlm",
                },
                http_client_factory=FakeClient,
            )

            result = analyzer.enrich_payload({"mime_type": "image/png", "data_url": PNG_DATA_URL})

        self.assertEqual(result.status["status"], "ok")
        self.assertIs(calls[0][3]["trust_env"], False)
        self.assertNotIn("proxy", calls[0][3])

    def test_openai_compatible_vlm_preserves_natural_coordinate_answer(self) -> None:
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
                                    "content": "Dock 中可见系统设置图标，齿轮图标位于 Dock 左侧区域；可点击中心点大约是 x=452, y=1187。"
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
                "image_detail": "low",
            },
            http_client_factory=FakeClient,
        )

        result = analyzer.enrich_payload(
            {
                "mime_type": "image/png",
                "data_url": PNG_DATA_URL,
                "active_observation": {
                    "target_hint": "点击 Dock 栏里的设置",
                    "observe_prompt": "我需要找到 Dock 栏里的设置应用的位置，请观看屏幕图像并用自然语言告诉我可点击中心坐标。",
                    "image_resolution": {"width": 3456, "height": 2234},
                    "screen_resolution": {"width": 1728, "height": 1117},
                    "screen_bounds": {"x": 0, "y": 0, "width": 1728, "height": 1117},
                    "coordinate_scale": {
                        "image_to_screen_x": 0.5,
                        "image_to_screen_y": 0.5,
                        "screen_to_image_x": 2.0,
                        "screen_to_image_y": 2.0,
                    },
                },
            }
        )

        self.assertEqual(result.status["status"], "ok")
        self.assertIn("x=452", result.payload["observe_answer"])
        self.assertIn("y=1187", result.payload["observe_answer"])
        prompt_text = calls[0][1]["messages"][1]["content"][0]["text"]
        self.assertIn("Brain asks", prompt_text)
        self.assertIn("自然语言", prompt_text)
        self.assertIn("不要输出 JSON", prompt_text)
        self.assertIn("可点击中心坐标", prompt_text)
        self.assertIn("3456x2234", prompt_text)
        self.assertIn("1728x1117", prompt_text)
        self.assertIn("macOS screen coordinates", prompt_text)
        self.assertIn("not screenshot pixel coordinates", prompt_text)
        self.assertIn("image-to-screen scale: x=0.5, y=0.5", prompt_text)

    def test_google_aistudio_vlm_uses_generate_content_with_inline_image(self) -> None:
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
                        "candidates": [
                            {
                                "content": {
                                    "parts": [
                                        {
                                            "text": "屏幕左上角可见设置窗口，中心点大约是 x=120, y=80。"
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                )

        analyzer = VisionAnalyzer(
            {
                "enabled": True,
                "provider": "google_aistudio_vlm",
                "model": "gemini-test",
                "api_key": "gemini-secret",
                "image_detail": "low",
            },
            http_client_factory=FakeClient,
        )

        result = analyzer.enrich_payload(
            {
                "mime_type": "image/png",
                "data_url": PNG_DATA_URL,
                "active_observation": {
                    "target_hint": "点击设置",
                    "observe_prompt": "请找到设置窗口中心点。",
                    "screen_resolution": {"width": 1728, "height": 1117},
                },
            }
        )

        self.assertEqual(result.status["status"], "ok")
        self.assertEqual(result.payload["observations"][0]["source"], "google-aistudio-vlm")
        self.assertIn("x=120", result.payload["observe_answer"])
        self.assertEqual(
            calls[0][0],
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent",
        )
        self.assertEqual(calls[0][2]["x-goog-api-key"], "gemini-secret")
        self.assertIs(calls[0][3]["trust_env"], False)
        self.assertIn("natural language", calls[0][1]["system_instruction"]["parts"][0]["text"])
        parts = calls[0][1]["contents"][0]["parts"]
        self.assertIn("请找到设置窗口中心点", parts[0]["text"])
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "image/png")
        self.assertTrue(parts[1]["inline_data"]["data"])

    def test_openai_vlm_prompt_uses_natural_language_question(self) -> None:
        click_prompt = build_vlm_user_prompt(
            max_observations=4,
            target_hint="点击 Dock 栏里的设置",
            observe_prompt="我需要找到 Dock 栏里的设置应用的位置，请观看屏幕图像并用自然语言告诉我可点击中心坐标。",
            image_resolution={"width": 3456, "height": 2234},
            screen_resolution={"width": 1728, "height": 1117},
            screen_bounds={"x": 0, "y": 0, "width": 1728, "height": 1117},
            coordinate_scale={
                "image_to_screen_x": 0.5,
                "image_to_screen_y": 0.5,
                "screen_to_image_x": 2.0,
                "screen_to_image_y": 2.0,
            },
        )
        read_prompt = build_vlm_user_prompt(
            max_observations=4,
            target_hint="看一下当前屏幕上有什么",
            observe_prompt="我需要了解当前屏幕主要内容，请概括可见窗口、文字和状态。",
            image_resolution={"width": 3456, "height": 2234},
            screen_resolution={"width": 1728, "height": 1117},
            screen_bounds={"x": 0, "y": 0, "width": 1728, "height": 1117},
            coordinate_scale={
                "image_to_screen_x": 0.5,
                "image_to_screen_y": 0.5,
                "screen_to_image_x": 2.0,
                "screen_to_image_y": 2.0,
            },
        )

        self.assertIn("Brain asks", click_prompt)
        self.assertIn("找到 Dock 栏里的设置应用", click_prompt)
        self.assertIn("不要输出 JSON", click_prompt)
        self.assertIn("Attached image pixel size: 3456x2234", click_prompt)
        self.assertIn("macOS screen coordinate space: origin=(0, 0), size=1728x1117 points", click_prompt)
        self.assertIn("image-to-screen scale: x=0.5, y=0.5", click_prompt)
        self.assertIn("Return macOS screen coordinates", click_prompt)
        self.assertIn("not screenshot pixel coordinates", click_prompt)
        self.assertIn("1728x1117", click_prompt)
        self.assertIn("Brain asks", read_prompt)
        self.assertIn("概括可见窗口", read_prompt)
        self.assertIn("用自然语言回答", read_prompt)

    def test_codex_vlm_uses_local_account_with_temporary_image(self) -> None:
        calls = []

        def runner(command, **kwargs):
            image_path = command[command.index("--image") + 1]
            self.assertTrue(os.path.isfile(image_path))
            calls.append((command, kwargs, image_path))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"type":"item.completed","item":{"type":"agent_message","text":"屏幕中可见设置按钮，中心点 x=120, y=80。"}}\n',
                stderr="",
            )

        analyzer = VisionAnalyzer(
            {"enabled": True, "provider": "codex_vlm", "model": "gpt-vision", "timeout_sec": 90},
            runner=runner,
        )
        with mock.patch("backend.vision_analyzer._codex_executable", return_value="/mock/codex"):
            result = analyzer.enrich_payload(
                {
                    "mime_type": "image/png",
                    "data_url": PNG_DATA_URL,
                    "active_observation": {"observe_prompt": "请找设置按钮。"},
                }
            )

        command, kwargs, image_path = calls[0]
        self.assertEqual(command[:4], ["/mock/codex", "-a", "never", "exec"])
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertEqual(command[command.index("--model") + 1], "gpt-vision")
        self.assertIn("请找设置按钮", kwargs["input"])
        self.assertEqual(kwargs["timeout"], 90.0)
        self.assertFalse(os.path.exists(image_path))
        self.assertEqual(result.status["status"], "ok")
        self.assertEqual(result.payload["observations"][0]["source"], "codex-vlm")

    def test_click_vlm_prompt_requires_coordinate_self_check_against_target_icon(self) -> None:
        prompt = build_vlm_user_prompt(
            max_observations=4,
            target_hint="点击 Dock 栏里的 Chrome",
            observe_prompt="请找到 Dock 栏里的 Chrome 图标，并返回可点击中心坐标。",
            image_resolution={"width": 1920, "height": 1249},
            screen_resolution={"width": 1470, "height": 956},
            screen_bounds={"x": 0, "y": 0, "width": 1470, "height": 956},
            coordinate_scale={
                "image_to_screen_x": 0.765625,
                "image_to_screen_y": 0.765412,
                "screen_to_image_x": 1.306122,
                "screen_to_image_y": 1.306485,
            },
        )

        self.assertIn("screen-to-image scale: x=1.306122, y=1.306485", prompt)
        self.assertIn("反投回截图", prompt)
        self.assertIn("目标图标", prompt)
        self.assertIn("不要只根据 Dock 图标序号或相邻关系估算", prompt)
        self.assertIn("如果坐标落到相邻图标", prompt)

    def test_unavailable_custom_vlm_provider_produces_error_without_fabricated_claim(self) -> None:
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
