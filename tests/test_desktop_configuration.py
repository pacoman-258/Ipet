from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app import configuration


class DesktopConfigurationTests(unittest.TestCase):
    def test_deep_merge_keeps_default_shape_and_ignores_unknown_keys(self) -> None:
        base = {
            "chat": {"model": "default-model", "session_id": "default", "asr": {"enabled": False}},
            "pet": {"scale": 0.3},
        }
        merged = configuration.deep_merge(
            base,
            {
                "chat": {"model": "saved-model", "tooling": {"enabled": True}},
                "pet": {"scale": 0.7},
                "legacy": True,
            },
        )

        self.assertEqual(merged["chat"]["model"], "saved-model")
        self.assertEqual(merged["pet"]["scale"], 0.7)
        self.assertNotIn("legacy", merged)
        self.assertNotIn("tooling", merged["chat"])

    def test_load_config_uses_injected_paths_and_normalizers(self) -> None:
        default_config = {
            "vision": {"enabled": False},
            "model_path": "model/default.model3.json",
            "pet": {
                "background_enabled": False,
                "background_image": "",
                "background_overlay_opacity": 0.42,
            },
            "chat": {"model": "default-brain", "session_id": "default"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "pet_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "model_path": "model/saved.model3.json",
                        "pet": {"background_enabled": "yes", "background_overlay_opacity": "2.0"},
                        "chat": {"model": "  ", "session_id": "", "skills": {"enabled": True}},
                        "vision": {"enabled": True},
                        "runtime": {"active": "old"},
                    }
                ),
                encoding="utf-8",
            )

            config = configuration.load_config(
                config_path=config_path,
                default_config=default_config,
                default_brain_model="fallback-brain",
                normalize_model_path_func=lambda value: f"normalized:{value}",
                normalize_vision_config_func=lambda value: {"normalized": bool(value.get("enabled"))},
            )

        self.assertEqual(config["model_path"], "normalized:model/saved.model3.json")
        self.assertTrue(config["pet"]["background_enabled"])
        self.assertEqual(config["pet"]["background_overlay_opacity"], 0.9)
        self.assertEqual(config["chat"]["model"], "fallback-brain")
        self.assertEqual(config["chat"]["session_id"], "default")
        self.assertNotIn("skills", config["chat"])
        self.assertNotIn("runtime", config)
        self.assertEqual(config["vision"], {"normalized": True})

    def test_extract_pet_display_name_prefers_character_names(self) -> None:
        prompt = json.dumps({"character": {"name": "Neo", "name_cn": "小昼"}, "name": "Fallback"})

        self.assertEqual(configuration.extract_pet_display_name(prompt), "小昼")
        self.assertEqual(configuration.extract_pet_display_name("plain text"), "桌宠")


if __name__ == "__main__":
    unittest.main()
