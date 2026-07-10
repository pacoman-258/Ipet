from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from body import live2d_assets


class Live2DAssetsTests(unittest.TestCase):
    def test_motion_group_inference_uses_common_live2d_prefixes(self) -> None:
        self.assertEqual(live2d_assets.infer_motion_group("idle_01.motion3.json"), "Idle")
        self.assertEqual(live2d_assets.infer_motion_group("tap-02.motion3.json"), "Tap")
        self.assertEqual(live2d_assets.infer_motion_group("wave happy.motion3.json"), "Wave")

    def test_ensure_model_scans_missing_motion_expression_defs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "pet"
            (model_dir / "motions").mkdir(parents=True)
            (model_dir / "expressions").mkdir()
            model_path = model_dir / "pet.model3.json"
            model_path.write_text(json.dumps({"Version": 3}), encoding="utf-8")
            (model_dir / "motions" / "idle_01.motion3.json").write_text("{}", encoding="utf-8")
            (model_dir / "expressions" / "smile.exp3.json").write_text("{}", encoding="utf-8")

            generated_path, groups, exprs = live2d_assets.ensure_live2d_model(model_path)

        self.assertEqual(generated_path.name, "pet.model3.autogen.model3.json")
        self.assertEqual(groups, {"Idle": [{"File": "motions/idle_01.motion3.json"}]})
        self.assertEqual(exprs, [{"Name": "smile", "File": "expressions/smile.exp3.json"}])

    def test_lipsync_meta_adds_controller_parameter_ids(self) -> None:
        meta = live2d_assets.extract_lipsync_meta(
            {
                "Controllers": {
                    "LipSync": {"Gain": "1.5"},
                    "FaceTracking": {
                        "MouthOpenY": [{"Id": "ParamCustomOpen"}],
                        "MouthForm": [{"Id": "ParamCustomForm"}],
                    },
                }
            }
        )

        self.assertEqual(meta["gain"], 1.5)
        self.assertIn("ParamCustomOpen", meta["mouth_open_ids"])
        self.assertIn("ParamCustomForm", meta["mouth_form_ids"])


if __name__ == "__main__":
    unittest.main()
