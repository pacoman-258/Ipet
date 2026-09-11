from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from app.default_config import create_default_config
from app import desktop_runtime
from backend.vision import DEFAULT_VISION_CONFIG
import main


class DesktopDefaultConfigTests(unittest.TestCase):
    def test_macos_default_asr_is_enabled_with_option_internal_key(self) -> None:
        config = desktop_runtime.default_asr_config("darwin")

        self.assertTrue(config["enabled"])
        self.assertEqual(config["push_to_talk_key"], "Alt")
        self.assertEqual(config["api_key"], "")

    def test_create_default_config_returns_full_neo_shape(self) -> None:
        default_asr_config = desktop_runtime.default_asr_config("linux")
        config = create_default_config(
            default_vision_config=DEFAULT_VISION_CONFIG,
            default_asr_config=default_asr_config,
            default_backend_url="http://127.0.0.1:8008",
            default_chat_model="gpt-5.4",
            default_brain_model="gpt-5.4",
            default_model_path="/models/default.model3.json",
        )

        self.assertEqual(
            set(config),
            {"vision", "environment", "game", "model_path", "brain", "human_ops", "memory", "skills", "window", "pet", "chat"},
        )
        self.assertEqual(config["brain"]["provider"], "openai_compatible")
        self.assertEqual(config["brain"]["model_name"], "gpt-5.4")
        self.assertEqual(config["brain"]["max_output_tokens"], 1024)
        self.assertEqual(config["brain"]["reasoning_effort"], "")
        self.assertFalse(config["brain"]["streaming_enabled"])
        self.assertFalse(config["brain"]["web_search_enabled"])
        self.assertEqual(config["brain"]["persona_prompt_file"], "")
        self.assertEqual(config["brain"]["persona"], "")
        self.assertEqual(config["brain"]["self_state"], "等待用户目标，并在 act / remember 前交给 Human Ops 处理。")
        self.assertEqual(config["human_ops"]["observe_screen"], True)
        self.assertEqual(config["human_ops"]["authorization_mode"], "review")
        self.assertEqual(config["human_ops"]["playwright_profile"], "")
        self.assertEqual(config["human_ops"]["click_preview"], {"x": 160, "y": 54, "label": "目标位置", "size": 16})
        self.assertEqual(config["memory"]["review_queue"], [])
        self.assertEqual(config["skills"]["recipes"], [])
        self.assertEqual(config["window"]["width"], 420)
        self.assertIs(config["window"]["follow_desktop"], True)
        self.assertEqual(config["pet"]["background_overlay_opacity"], 0.42)
        self.assertEqual(config["chat"]["backend_url"], "http://127.0.0.1:8008")
        self.assertEqual(config["chat"]["model"], "gpt-5.4")
        self.assertEqual(config["chat"]["asr"], default_asr_config)
        self.assertEqual(config["vision"], DEFAULT_VISION_CONFIG)
        self.assertEqual(config["environment"]["mode"], "off")
        self.assertTrue(config["game"]["enabled"])
        self.assertEqual(config["game"]["max_reactions_per_minute"], 6)

    def test_create_default_config_returns_independent_nested_structures(self) -> None:
        config_one = create_default_config(
            default_vision_config=DEFAULT_VISION_CONFIG,
            default_asr_config=desktop_runtime.default_asr_config("linux"),
            default_backend_url="http://127.0.0.1:8008",
            default_chat_model="gpt-5.4",
            default_brain_model="gpt-5.4",
            default_model_path="/models/default.model3.json",
        )
        config_two = create_default_config(
            default_vision_config=DEFAULT_VISION_CONFIG,
            default_asr_config=desktop_runtime.default_asr_config("linux"),
            default_backend_url="http://127.0.0.1:8008",
            default_chat_model="gpt-5.4",
            default_brain_model="gpt-5.4",
            default_model_path="/models/default.model3.json",
        )

        config_one["memory"]["review_queue"].append("note")
        config_one["skills"]["recipes"].append("recipe")
        config_one["chat"]["asr"]["provider"] = "changed"
        config_one["vision"]["active_observation"]["settle_ms"] = 999

        self.assertEqual(config_two["memory"]["review_queue"], [])
        self.assertEqual(config_two["skills"]["recipes"], [])
        self.assertEqual(config_two["chat"]["asr"]["provider"], "funasr")
        self.assertEqual(config_two["vision"]["active_observation"]["settle_ms"], 500)

    def test_main_default_config_is_still_exported_and_uses_factory(self) -> None:
        self.assertIn("brain", main.DEFAULT_CONFIG)
        self.assertEqual(main.DEFAULT_CONFIG["chat"]["backend_url"], main.DEFAULT_BACKEND_URL)
        self.assertEqual(main.DEFAULT_CONFIG["chat"]["model"], main.DEFAULT_BRAIN_MODEL)

        source = Path(main.__file__).read_text(encoding="utf-8")
        self.assertIn("create_default_config(", source)
        self.assertNotIn("DEFAULT_CONFIG = {", source)

    def test_app_default_config_module_does_not_need_main(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import app.default_config; print('main' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
