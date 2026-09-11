from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import main


ROOT_DIR = Path(__file__).resolve().parents[1]


def _parse_source(relative_path: str) -> ast.Module:
    path = ROOT_DIR / relative_path
    if not path.exists():
        raise AssertionError(f"{relative_path} should exist")
    return ast.parse(path.read_text(encoding="utf-8"))


def _actions_module():
    try:
        return importlib.import_module("app.desktop_config_actions")
    except ImportError as exc:
        raise AssertionError("app.desktop_config_actions module should be importable") from exc


class _TextInput:
    def __init__(self, value: str) -> None:
        self._value = value

    def text(self) -> str:
        return self._value


class _PlainTextInput:
    def __init__(self, value: str) -> None:
        self._value = value

    def toPlainText(self) -> str:
        return self._value


class _ValueInput:
    def __init__(self, value) -> None:
        self._value = value

    def value(self):
        return self._value


class _CheckInput:
    def __init__(self, checked: bool) -> None:
        self._checked = checked

    def isChecked(self) -> bool:
        return self._checked


class _ComboInput:
    def __init__(self, value: str) -> None:
        self._value = value

    def currentText(self) -> str:
        return self._value


def _fake_panel() -> SimpleNamespace:
    return SimpleNamespace(
        model_path_input=_TextInput("  model/Neo.model3.json  "),
        scale_spin=_ValueInput("0.75"),
        offset_x_spin=_ValueInput("12"),
        offset_y_spin=_ValueInput("-8"),
        rotation_spin=_ValueInput("5.5"),
        opacity_spin=_ValueInput("0.86"),
        edit_mode_check=_CheckInput(True),
        follow_mouse_check=_CheckInput(False),
        chat_model_input=_TextInput("  "),
        chat_voice_input=_TextInput("  "),
        chat_rate_slider=_ValueInput(145),
        chat_tts_provider_combo=_ComboInput("  "),
        chat_tts_provider_url_input=_TextInput("  http://tts.local/api  "),
        expression_mode_check=_CheckInput(False),
        system_prompt_input=_PlainTextInput("  你好，Neo  "),
        win_x_spin=_ValueInput("101"),
        win_y_spin=_ValueInput("202"),
        win_w_spin=_ValueInput("303"),
        win_h_spin=_ValueInput("404"),
        lock_window_check=_CheckInput(True),
    )


class _FakeOwner:
    def __init__(self) -> None:
        self.config = {
            "model_path": "old",
            "vision": {"enabled": True},
            "pet": {
                "background_enabled": True,
                "background_image": "bg.png",
                "background_overlay_opacity": "0.33",
            },
            "chat": {
                "backend_url": "http://backend.local",
                "model": "old-model",
                "session_id": "session-a",
                "expression_output_format": "json",
                "asr": {"enabled": True, "nested": {"lang": "zh"}},
            },
            "window": {"x": 1, "y": 2, "width": 3, "height": 4, "locked": False},
        }
        self.control_panel = _fake_panel()
        self.window_locked = False
        self.geometry_calls: list[tuple[int, int, int, int]] = []
        self.refresh_calls: list[bool] = []
        self.apply_web_calls = 0
        self.apply_window_calls = 0
        self._geometry = (11, 22, 333, 444)
        self._mtime_token = ("mtime", 9)
        self._config_mtime = None

    def setGeometry(self, x: int, y: int, width: int, height: int) -> None:
        self.geometry_calls.append((x, y, width, height))
        self._geometry = (x, y, width, height)

    def x(self) -> int:
        return self._geometry[0]

    def y(self) -> int:
        return self._geometry[1]

    def width(self) -> int:
        return self._geometry[2]

    def height(self) -> int:
        return self._geometry[3]

    def refresh_motion_list(self, *, prefer_reset: bool) -> None:
        self.refresh_calls.append(prefer_reset)

    def apply_config_to_web(self) -> None:
        self.apply_web_calls += 1

    def apply_window_geometry_from_config(self) -> None:
        self.apply_window_calls += 1

    def _config_mtime_token(self):
        return self._mtime_token


def _default_config() -> dict:
    return {
        "model_path": "default.model3.json",
        "vision": {"enabled": False},
        "pet": {"scale": 0.3},
        "chat": {
            "backend_url": "http://127.0.0.1:8008",
            "model": "default-brain",
            "session_id": "default",
            "voice": "zh-CN-XiaoxiaoNeural",
            "rate_pct": 0,
            "tts_provider": "edge_tts",
            "tts_provider_url": "",
            "expression_mode": True,
            "expression_output_format": "ndjson_v1",
            "asr": {"enabled": False, "language": "zh-CN"},
            "system_prompt": "",
        },
        "window": {"x": 10, "y": 20, "width": 420, "height": 640, "locked": True},
    }


def _build_actions(
    owner: _FakeOwner,
    config_path: Path,
    calls: list[str],
    *,
    screen_provider=None,
    load_config=None,
):
    module = _actions_module()
    kwargs = {"load_config": load_config} if load_config is not None else {}
    return module.DesktopConfigActions(
        owner,
        json_module=json,
        config_path=config_path,
        default_config=_default_config(),
        default_asr_config={"enabled": False, "language": "zh-CN"},
        default_backend_url="http://127.0.0.1:8008",
        default_brain_model="fallback-brain",
        normalize_model_path=lambda value: f"normalized:{value}",
        normalize_vision_config=lambda value: {"normalized_vision": dict(value)},
        keep_neo_config_shape=lambda config: calls.append("keep"),
        normalize_neo_chat_config=lambda config: calls.append("normalize_chat"),
        screen_provider=screen_provider or (lambda: None),
        **kwargs,
    )


class DesktopConfigActionsSplitTests(unittest.TestCase):
    def test_actions_module_exists_without_importing_main(self) -> None:
        tree = _parse_source("app/desktop_config_actions.py")

        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)

        self.assertNotIn("main", imported_modules)

        code = (
            "import sys\n"
            "sys.modules.pop('main', None)\n"
            "import app.desktop_config_actions\n"
            "raise SystemExit(1 if 'main' in sys.modules else 0)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], check=False)
        self.assertEqual(result.returncode, 0)

    def test_main_config_methods_delegate_to_actions(self) -> None:
        actions = mock.Mock()
        actions.config_mtime_token.return_value = ("mtime", 4)
        host = SimpleNamespace()

        with (
            mock.patch.object(main, "_desktop_config_actions_for", return_value=actions) as factory,
            mock.patch.object(main, "apply_desktop_follow") as follow_desktop,
        ):
            self.assertEqual(main.DesktopPet._config_mtime_token(host), ("mtime", 4))
            main.DesktopPet.reload_config_from_disk(host)
            main.DesktopPet.apply_from_panel(host)
            main.DesktopPet.save_config(host)
            main.DesktopPet.reset_to_default(host)

        self.assertEqual(factory.call_args_list, [mock.call(host)] * 5)
        actions.config_mtime_token.assert_called_once_with()
        actions.reload_config_from_disk.assert_called_once_with()
        actions.apply_from_panel.assert_called_once_with()
        actions.save_config.assert_called_once_with()
        actions.reset_to_default.assert_called_once_with()
        self.assertEqual(follow_desktop.call_args_list, [mock.call(host), mock.call(host)])

    def test_panel_preserves_disabled_desktop_follow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            owner = _FakeOwner()
            owner.config.setdefault("window", {})["follow_desktop"] = False
            actions = _build_actions(owner, Path(tmp) / "config.json", [])
            actions.apply_from_panel()
            self.assertIs(owner.config["window"]["follow_desktop"], False)

    def test_main_builder_resolves_patched_load_config_when_reloading(self) -> None:
        loaded = {
            "window": {"locked": False},
            "chat": {"asr": {"enabled": False}},
            "vision": {},
        }
        owner = SimpleNamespace(
            config={},
            apply_window_geometry_from_config=mock.Mock(),
            refresh_motion_list=mock.Mock(),
            stop_asr_service=mock.Mock(),
            ensure_asr_service=mock.Mock(),
            vision_controller=mock.Mock(),
            apply_config_to_web=mock.Mock(),
        )
        actions = main._build_desktop_config_actions(owner)

        with mock.patch.object(main, "load_config", return_value=loaded) as load_config_mock:
            actions.reload_config_from_disk()

        self.assertIs(owner.config, loaded)
        load_config_mock.assert_called_once_with()

    def test_actions_own_config_token_and_reload_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "pet_config.json"
            config_path.write_text("{}", encoding="utf-8")
            events: list[object] = []
            loaded = {
                "window": {"locked": True},
                "chat": {"asr": {"enabled": False}},
                "vision": {"enabled": True},
            }
            owner = _FakeOwner()
            owner.apply_window_geometry_from_config = lambda: events.append("geometry")
            owner.refresh_motion_list = lambda *, prefer_reset: events.append(("refresh", prefer_reset))
            owner.ensure_asr_service = lambda: events.append("ensure_asr")
            owner.stop_asr_service = lambda: events.append("stop_asr")
            owner.vision_controller = SimpleNamespace(
                apply_config=lambda config: events.append(("vision", config))
            )
            owner.apply_config_to_web = lambda: events.append("web")
            actions = _build_actions(
                owner,
                config_path,
                [],
                load_config=lambda: loaded,
            )

            self.assertEqual(
                actions.config_mtime_token(),
                (config_path.stat().st_mtime_ns, config_path.stat().st_size),
            )
            actions.reload_config_from_disk()

        self.assertIs(owner.config, loaded)
        self.assertTrue(owner.window_locked)
        self.assertEqual(
            events,
            [
                "geometry",
                ("refresh", False),
                "stop_asr",
                ("vision", loaded),
                "web",
            ],
        )

    def test_apply_from_panel_updates_config_geometry_and_web(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            owner = _FakeOwner()
            old_asr = owner.config["chat"]["asr"]
            calls: list[str] = []
            actions = _build_actions(owner, Path(tmp) / "pet_config.json", calls)

            actions.apply_from_panel()

        self.assertEqual(owner.config["model_path"], "normalized:model/Neo.model3.json")
        self.assertEqual(
            owner.config["pet"],
            {
                "scale": 0.75,
                "offset_x": 12,
                "offset_y": -8,
                "rotation": 5.5,
                "opacity": 0.86,
                "edit_mode": True,
                "follow_mouse": False,
                "background_enabled": True,
                "background_image": "bg.png",
                "background_overlay_opacity": 0.33,
            },
        )
        self.assertEqual(owner.config["chat"]["backend_url"], "http://backend.local")
        self.assertEqual(owner.config["chat"]["model"], "fallback-brain")
        self.assertEqual(owner.config["chat"]["session_id"], "session-a")
        self.assertEqual(owner.config["chat"]["voice"], "zh-CN-XiaoxiaoNeural")
        self.assertEqual(owner.config["chat"]["rate_pct"], 100)
        self.assertEqual(owner.config["chat"]["tts_provider"], "edge_tts")
        self.assertEqual(owner.config["chat"]["tts_provider_url"], "http://tts.local/api")
        self.assertFalse(owner.config["chat"]["expression_mode"])
        self.assertEqual(owner.config["chat"]["expression_output_format"], "json")
        self.assertEqual(owner.config["chat"]["system_prompt"], "你好，Neo")
        self.assertEqual(owner.config["chat"]["asr"], {"enabled": True, "nested": {"lang": "zh"}})
        self.assertIsNot(owner.config["chat"]["asr"], old_asr)
        self.assertEqual(
            owner.config["window"],
            {"x": 101, "y": 202, "width": 303, "height": 404, "locked": True, "follow_desktop": True},
        )
        self.assertTrue(owner.window_locked)
        self.assertEqual(owner.geometry_calls, [(101, 202, 303, 404)])
        self.assertEqual(owner.refresh_calls, [False])
        self.assertEqual(owner.apply_web_calls, 1)
        self.assertEqual(calls, ["keep", "normalize_chat"])

    def test_save_config_normalizes_writes_json_and_updates_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "pet_config.json"
            owner = _FakeOwner()
            owner.config["model_path"] = "models/Neo.model3.json"
            owner.config["vision"] = {"enabled": True, "provider": "vlm"}
            owner.config["chat"]["system_prompt"] = "少女"
            owner._geometry = (50, 60, 700, 800)
            calls: list[str] = []
            actions = _build_actions(owner, config_path, calls)

            actions.save_config()

            saved_text = config_path.read_text(encoding="utf-8")
            saved = json.loads(saved_text)

        self.assertIn('\n  "model_path"', saved_text)
        self.assertEqual(saved["model_path"], "normalized:models/Neo.model3.json")
        self.assertEqual(saved["vision"], {"normalized_vision": {"enabled": True, "provider": "vlm"}})
        self.assertEqual(saved["chat"]["system_prompt"], "少女")
        self.assertEqual(saved["window"], {"x": 50, "y": 60, "width": 700, "height": 800, "locked": False})
        self.assertEqual(owner._config_mtime, ("mtime", 9))
        self.assertEqual(calls, ["keep", "normalize_chat"])

    def test_reset_to_default_deep_copies_config_places_window_and_applies(self) -> None:
        class Geometry:
            def right(self) -> int:
                return 1440

            def bottom(self) -> int:
                return 900

        class Screen:
            def availableGeometry(self) -> Geometry:
                return Geometry()

        with tempfile.TemporaryDirectory() as tmp:
            owner = _FakeOwner()
            default_config = _default_config()
            module = _actions_module()
            actions = module.DesktopConfigActions(
                owner,
                json_module=json,
                config_path=Path(tmp) / "pet_config.json",
                default_config=default_config,
                default_asr_config={"enabled": False, "language": "zh-CN"},
                default_backend_url="http://127.0.0.1:8008",
                default_brain_model="fallback-brain",
                normalize_model_path=lambda value: value,
                normalize_vision_config=lambda value: value,
                keep_neo_config_shape=lambda config: None,
                normalize_neo_chat_config=lambda config: None,
                screen_provider=lambda: Screen(),
            )

            actions.reset_to_default()
            owner.config["chat"]["asr"]["enabled"] = True

        self.assertEqual(owner.config["window"]["x"], 1440 - 420 - 40)
        self.assertEqual(owner.config["window"]["y"], 900 - 640 - 60)
        self.assertTrue(owner.window_locked)
        self.assertEqual(owner.apply_window_calls, 1)
        self.assertEqual(owner.refresh_calls, [True])
        self.assertEqual(owner.apply_web_calls, 1)
        self.assertFalse(default_config["chat"]["asr"]["enabled"])


if __name__ == "__main__":
    unittest.main()
