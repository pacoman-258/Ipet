from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class BodyPayloadBridgeTests(unittest.TestCase):
    def test_body_bridge_import_does_not_import_main(self) -> None:
        code = (
            "import sys\n"
            "sys.modules.pop('main', None)\n"
            "import app.body_bridge\n"
            "raise SystemExit(1 if 'main' in sys.modules else 0)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], check=False)
        self.assertEqual(result.returncode, 0)

    def test_main_builder_injects_motion_refresh_dependencies(self) -> None:
        import main

        extract_lipsync_meta = mock.Mock()
        action_items_from_defs = mock.Mock()
        json_loads = mock.Mock()
        with mock.patch.object(main, "extract_lipsync_meta", extract_lipsync_meta), mock.patch.object(
            main, "action_items_from_defs", action_items_from_defs
        ), mock.patch.object(main.json, "loads", json_loads):
            bridge = main._build_body_bridge()

        self.assertIs(bridge.extract_lipsync_meta, extract_lipsync_meta)
        self.assertIs(bridge.action_items_from_defs, action_items_from_defs)
        self.assertIs(bridge.json_loads, json_loads)

    def test_current_body_payload_uses_injected_helpers_and_sets_live2d_path(self) -> None:
        from app.body_bridge import BodyBridge

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_model_path = root / "default.model3.json"
            default_model_path.write_text("{}", encoding="utf-8")
            live2d_path = root / "default.autogen.model3.json"
            live2d_path.write_text("{}", encoding="utf-8")
            background_path = root / "bg.png"
            background_path.write_bytes(b"png")
            missing_model_path = root / "missing.model3.json"

            resolved_models = {
                "missing": missing_model_path,
                "default": default_model_path,
            }
            ensure_calls: list[Path] = []

            def ensure_model(path: Path):
                ensure_calls.append(path)
                return live2d_path, {"Idle": []}, []

            bridge = BodyBridge(
                resolve_model_path=lambda text: resolved_models[str(text)],
                default_model_path_text="default",
                ensure_live2d_model=ensure_model,
                resolve_background_image_path=lambda _text: background_path,
                local_file_url=lambda path: f"local://{Path(path).name}",
                extract_pet_display_name=lambda prompt: f"display:{prompt}",
                normalize_vision_config=lambda config: {"normalized": dict(config)},
            )
            host = mock.Mock()
            host.config = {
                "model_path": "missing",
                "pet": {"background_enabled": True, "background_image": "bg.png"},
                "chat": {
                    "system_prompt": "Neo name",
                    "voice": "voice-a",
                    "api_key": "secret-value",
                    "tts_api_key": "fish-secret-value",
                },
                "vision": {"enabled": True},
            }
            host.expression_names = ["smile", "blink"]
            host.lipsync_meta = {
                "gain": "1.25",
                "mouth_open_ids": ["ParamMouthOpenY"],
                "mouth_form_ids": ["ParamMouthForm"],
            }
            host.live2d_model_path = None

            payload = bridge.current_body_payload(host)

        self.assertEqual(ensure_calls, [default_model_path])
        self.assertEqual(host.live2d_model_path, live2d_path)
        self.assertEqual(payload["model_url"], "local://default.autogen.model3.json")
        self.assertEqual(payload["pet"]["background_image_url"], "local://bg.png")
        self.assertEqual(payload["chat"]["pet_display_name"], "display:Neo name")
        self.assertNotIn("api_key", payload["chat"])
        self.assertNotIn("tts_api_key", payload["chat"])
        self.assertEqual(payload["chat"]["available_expressions"], ["smile", "blink"])
        self.assertEqual(payload["chat"]["lip_sync_gain"], 1.25)
        self.assertEqual(payload["chat"]["mouth_parameter_ids"], ["ParamMouthOpenY"])
        self.assertEqual(payload["chat"]["mouth_form_parameter_ids"], ["ParamMouthForm"])
        self.assertEqual(payload["vision"], {"normalized": {"enabled": True}})
        self.assertEqual(payload["environment"], {})

    def test_js_bridge_scripts_apply_config_and_actions(self) -> None:
        from app.body_bridge import BodyBridge

        class Page:
            def __init__(self) -> None:
                self.scripts: list[str] = []

            def runJavaScript(self, script: str) -> None:
                self.scripts.append(script)

        class Browser:
            def __init__(self) -> None:
                self._page = Page()

            def page(self) -> Page:
                return self._page

        with tempfile.TemporaryDirectory() as tmp:
            model_path = Path(tmp) / "pet.model3.json"
            model_path.write_text("{}", encoding="utf-8")

            bridge = BodyBridge(
                resolve_model_path=lambda _text: model_path,
                default_model_path_text="",
                ensure_live2d_model=lambda path: (path, {}, []),
                resolve_background_image_path=lambda _text: Path(tmp) / "missing.png",
                local_file_url=lambda path: f"local://{Path(path).name}",
                extract_pet_display_name=lambda _prompt: "Ipet",
                normalize_vision_config=lambda config: dict(config),
            )
            host = mock.Mock()
            host.browser = Browser()
            host.config = {"model_path": "pet", "pet": {}, "chat": {}, "vision": {}}
            host.expression_names = []
            host.lipsync_meta = {}

            bridge.apply_config_to_web(host)
            bridge.play_motion(host, "Wave", 2)
            bridge.play_expression(host, "开心")
            bridge.play_motion(host, "Tap Body", 0, reload_model=True)

        scripts = host.browser.page().scripts
        self.assertEqual(
            scripts[0],
            'window.PET_APP && window.PET_APP.applyConfig({"model_url": "local://pet.model3.json", '
            '"pet": {"background_image_url": ""}, "chat": {"pet_display_name": "Ipet", '
            '"available_expressions": [], "lip_sync_gain": 1.0, "mouth_parameter_ids": [], '
            '"mouth_form_parameter_ids": []}, "vision": {}, "environment": {}});',
        )
        self.assertEqual(
            scripts[1],
            'window.PET_APP && window.PET_APP.playMotion("Wave", 2);',
        )
        self.assertEqual(
            scripts[2],
            'window.PET_APP && window.PET_APP.playExpression("开心");',
        )
        self.assertIn("applyConfig(", scripts[3])
        self.assertIn('then(() => { window.PET_APP && window.PET_APP.playMotion("Tap Body", 0); });', scripts[3])

    def test_body_bridge_owns_motion_refresh_and_web_transform_sync(self) -> None:
        from app.body_bridge import BodyBridge

        with tempfile.TemporaryDirectory() as tmp:
            model_path = Path(tmp) / "pet.model3.json"
            model_path.write_text('{"Version": 3}', encoding="utf-8")
            live2d_path = Path(tmp) / "pet.autogen.model3.json"

            panel = SimpleNamespace(
                model_path_input=SimpleNamespace(text=lambda: " panel-model "),
                motion_combo=SimpleNamespace(count=lambda: 1, setCurrentIndex=mock.Mock()),
                update_pet_widgets_from_web_state=mock.Mock(),
            )
            host = SimpleNamespace(
                config={"model_path": "config-model", "pet": {"scale": 0.3}},
                control_panel=panel,
                sync_panel=mock.Mock(),
                live2d_model_path=None,
                lipsync_meta={},
                motion_items=[],
                expression_names=[],
            )
            bridge = BodyBridge(
                resolve_model_path=lambda text: model_path if text == "panel-model" else Path(tmp) / text,
                default_model_path_text="",
                ensure_live2d_model=lambda path: (live2d_path, {}, [{"Name": "smile"}, {"Name": " "}]),
                resolve_background_image_path=lambda _text: Path(tmp) / "missing.png",
                local_file_url=lambda path: f"local://{Path(path).name}",
                extract_pet_display_name=lambda _prompt: "Ipet",
                normalize_vision_config=lambda config: dict(config),
                extract_lipsync_meta=lambda model: {"gain": float(model["Version"])},
                action_items_from_defs=lambda _groups, _expressions: [],
            )

            bridge.refresh_motion_list(host, prefer_reset=True)
            bridge.on_web_state_changed(
                host,
                json.dumps({"scale": 0.8, "offset_x": 12, "ignored": "value"}),
            )
            bridge.on_web_state_changed(host, "not-json")

        self.assertEqual(host.live2d_model_path, live2d_path)
        self.assertEqual(host.lipsync_meta, {"gain": 3.0})
        self.assertEqual(host.expression_names, ["smile"])
        self.assertEqual(
            host.motion_items,
            [{"type": "motion", "group": "Idle", "index": 0, "label": "Idle[0]"}],
        )
        host.sync_panel.assert_called_once_with()
        panel.motion_combo.setCurrentIndex.assert_called_once_with(0)
        self.assertEqual(host.config["pet"], {"scale": 0.8, "offset_x": 12})
        panel.update_pet_widgets_from_web_state.assert_called_once_with(
            {"scale": 0.8, "offset_x": 12, "ignored": "value"}
        )

    def test_desktop_pet_wrappers_delegate_to_body_bridge(self) -> None:
        import main

        host = mock.Mock()
        bridge = mock.Mock()
        host._body_bridge = bridge
        bridge.current_body_payload.return_value = {"ok": True}

        self.assertEqual(main.DesktopPet.current_body_payload(host), {"ok": True})
        main.DesktopPet.apply_config_to_web(host, after_script="after();")
        main.DesktopPet.play_motion(host, "Idle", 3, reload_model=True)
        main.DesktopPet.play_expression(host, "smile", reload_model=True)
        main.DesktopPet.play_action(host, {"type": "motion", "group": "Tap", "index": 1})
        main.DesktopPet.refresh_motion_list(host, prefer_reset=True)
        main.DesktopPet.on_web_state_changed(host, '{"scale": 0.5}')

        bridge.current_body_payload.assert_called_once_with(host)
        bridge.apply_config_to_web.assert_called_once_with(host, after_script="after();")
        bridge.play_motion.assert_called_once_with(host, "Idle", 3, reload_model=True)
        bridge.play_expression.assert_called_once_with(host, "smile", reload_model=True)
        bridge.play_action.assert_called_once_with(host, {"type": "motion", "group": "Tap", "index": 1})
        bridge.refresh_motion_list.assert_called_once_with(host, prefer_reset=True)
        bridge.on_web_state_changed.assert_called_once_with(host, '{"scale": 0.5}')


if __name__ == "__main__":
    unittest.main()
