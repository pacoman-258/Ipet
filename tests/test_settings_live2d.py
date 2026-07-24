from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend import settings_live2d


class SettingsLive2dTests(unittest.TestCase):
    def test_local_model_catalog_exposes_motion_and_expression_preview_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model_dir = root / "model" / "pet"
            model_dir.mkdir(parents=True)
            model_path = model_dir / "pet.model3.json"
            model_path.write_text(
                json.dumps(
                    {
                        "FileReferences": {
                            "Motions": {"Tap": [{"File": "tap.motion3.json"}, {"File": "tap2.motion3.json"}]},
                            "Expressions": [{"Name": "smile", "File": "smile.exp3.json"}],
                        }
                    }
                ),
                encoding="utf-8",
            )

            models = settings_live2d.list_local_models(root_dir=root)

        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["path"], "model/pet/pet.model3.json")
        self.assertEqual(models[0]["motion_actions"], [
            {"group": "Tap", "index": 0, "label": "Tap[0]"},
            {"group": "Tap", "index": 1, "label": "Tap[1]"},
        ])
        self.assertEqual(models[0]["expression_actions"], [{"name": "smile", "label": "smile"}])

    def test_preview_command_normalizes_and_validates_desktop_command_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model_path = root / "model" / "pet.model3.json"
            model_path.parent.mkdir(parents=True)
            model_path.write_text("{}", encoding="utf-8")

            command = settings_live2d.preview_command(
                {
                    "type": "play_motion",
                    "model_path": "model/pet.model3.json",
                    "group": "Tap",
                    "index": "2",
                },
                root_dir=root,
            )

        self.assertEqual(command, {
            "type": "play_motion",
            "payload": {
                "model_path": "model/pet.model3.json",
                "group": "Tap",
                "index": 2,
            },
        })
        with self.assertRaisesRegex(ValueError, "model_path is required"):
            settings_live2d.preview_command({"type": "load_model"}, root_dir=root)


if __name__ == "__main__":
    unittest.main()
