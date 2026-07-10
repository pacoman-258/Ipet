from __future__ import annotations

import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock


class _FakeScreenCaptureModule:
    VISION_CAPTURE_SCOPE = "fake-scope"

    def __init__(self) -> None:
        self.qt_calls: list[dict] = []
        self.capture_calls: list[dict] = []
        self.collect_calls: list[dict] = []

        def compose_qt_screens_pixmap(screens, **kwargs):
            return "pixmap", {"screens": list(screens), **kwargs}

        def encode_pixmap_frame(pixmap, config, **kwargs):
            return "image/jpeg", "data:image/jpeg;base64,fake", 12, 8

        def capture_qt_screen_frame_payload(screen, config, **kwargs):
            self.qt_calls.append(kwargs)
            return {"screen": screen, "config": config, "backend": "qt"}

        def capture_macos_screencapture_payload(config, **kwargs):
            return {"config": config, "backend": "macos", "kwargs": kwargs}

        def capture_screen_frame_payload(screen, config, **kwargs):
            self.capture_calls.append(kwargs)
            return {"screen": screen, "config": config, "backend": "screen"}

        def collect_screen_observations(config=None, **kwargs):
            self.collect_calls.append(kwargs)
            return {"config": config or {}, "observed": True}

        self.compose_qt_screens_pixmap = compose_qt_screens_pixmap
        self._encode_pixmap_frame = encode_pixmap_frame
        self.capture_qt_screen_frame_payload = capture_qt_screen_frame_payload
        self.capture_macos_screencapture_payload = capture_macos_screencapture_payload
        self.capture_screen_frame_payload = capture_screen_frame_payload
        self.collect_screen_observations = collect_screen_observations

    def _qiodevice_write_only_mode(self, q_io_device):
        return ("write-only", q_io_device)

    def _qt_smooth_transformation_mode(self, qt):
        return ("smooth", qt)

    def _image_like_dimensions(self, image_like):
        return image_like

    def _encoded_frame_parts(self, encoded_frame):
        return encoded_frame

    def _vision_hash_from_data_url(self, data_url):
        return f"hash:{data_url}"

    def _visual_hash_from_image_like(self, image_like):
        return f"image:{image_like}"

    def _visual_hash_from_pixmap(self, pixmap):
        return f"pixmap:{pixmap}"

    def _screen_layout_entry(self, screen):
        return {"screen": screen}

    def _screen_layout(self, screens):
        return [{"screen": screen} for screen in screens]

    def _payload_from_encoded_frame(self, mime_type, data_url, **kwargs):
        return {"mime_type": mime_type, "data_url": data_url, **kwargs}

    def encode_screen_frame(self, screen, config, **kwargs):
        return screen, config, kwargs

    def _clean_vision_text(self, value, *, max_length: int):
        return str(value)[:max_length]


class ScreenCaptureBridgeTests(unittest.TestCase):
    def test_bridge_import_does_not_import_main_qt_or_body_capture(self) -> None:
        code = (
            "import sys\n"
            "for name in ('main', 'PySide6', 'PyQt6', 'body.screen_capture'):\n"
            "    sys.modules.pop(name, None)\n"
            "import app.screen_capture_bridge\n"
            "blocked = [name for name in ('main', 'PySide6', 'PyQt6', 'body.screen_capture') if name in sys.modules]\n"
            "raise SystemExit(1 if blocked else 0)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], check=False)
        self.assertEqual(result.returncode, 0)

    def test_bridge_unwraps_default_qt_composer_and_encoder_to_body_providers(self) -> None:
        from app.screen_capture_bridge import ScreenCaptureBridge

        fake = _FakeScreenCaptureModule()
        deps = SimpleNamespace(q_io_device="io-device", qt="qt")
        bridge = ScreenCaptureBridge(
            screen_capture_module=fake,
            qt_deps_factory=lambda: deps,
            is_macos_func=lambda _platform=None: False,
            vision_config_normalizer=lambda config: dict(config),
        )

        def main_composer(_screens):
            raise AssertionError("main composer wrapper should be unwrapped")

        def main_encoder(_pixmap, _config):
            raise AssertionError("main pixmap encoder wrapper should be unwrapped")

        bridge.capture_qt_screen_frame_payload(
            "screen",
            {"quality": 75},
            screens_provider=lambda: ["screen"],
            pixmap_composer=main_composer,
            pixmap_encoder=main_encoder,
            default_pixmap_composer=main_composer,
            default_pixmap_encoder=main_encoder,
        )

        self.assertIs(fake.qt_calls[0]["pixmap_composer"], fake.compose_qt_screens_pixmap)
        self.assertIs(fake.qt_calls[0]["pixmap_encoder"], fake._encode_pixmap_frame)
        self.assertIs(fake.qt_calls[0]["qt_deps"], deps)

    def test_bridge_unwraps_default_capture_and_observation_providers(self) -> None:
        from app.screen_capture_bridge import ScreenCaptureBridge

        fake = _FakeScreenCaptureModule()
        deps = SimpleNamespace(q_io_device="io-device", qt="qt")
        bridge = ScreenCaptureBridge(
            screen_capture_module=fake,
            qt_deps_factory=lambda: deps,
            is_macos_func=lambda platform=None: platform == "darwin",
            vision_config_normalizer=lambda config: {"normalized": dict(config)},
        )

        def main_macos_capture(_config):
            raise AssertionError("main macOS wrapper should be unwrapped")

        def main_qt_capture(_screen, _config):
            raise AssertionError("main Qt wrapper should be unwrapped")

        def main_collect(_config):
            raise AssertionError("main collect wrapper should be unwrapped")

        bridge.capture_screen_frame_payload(
            "screen",
            {"enabled": True},
            platform_name="darwin",
            macos_capture=main_macos_capture,
            qt_capture=main_qt_capture,
            default_macos_capture=main_macos_capture,
            default_qt_capture=main_qt_capture,
            display_layout=[{"x": 0}],
        )
        collect_provider = bridge.unwrap_collect_screen_observations_provider(
            main_collect,
            default_provider=main_collect,
        )

        self.assertIs(fake.capture_calls[0]["macos_capture"], fake.capture_macos_screencapture_payload)
        self.assertIs(fake.capture_calls[0]["qt_capture"], fake.capture_qt_screen_frame_payload)
        self.assertEqual(fake.capture_calls[0]["display_layout"], [{"x": 0}])
        self.assertIs(fake.capture_calls[0]["qt_deps"], deps)
        self.assertIs(fake.capture_calls[0]["is_macos_func"], bridge.is_macos_func)
        self.assertIs(collect_provider, fake.collect_screen_observations)

    def test_bridge_keeps_custom_capture_providers(self) -> None:
        from app.screen_capture_bridge import ScreenCaptureBridge

        fake = _FakeScreenCaptureModule()
        bridge = ScreenCaptureBridge(
            screen_capture_module=fake,
            qt_deps_factory=lambda: SimpleNamespace(q_io_device="io-device", qt="qt"),
            is_macos_func=lambda _platform=None: False,
            vision_config_normalizer=lambda config: dict(config),
        )

        def default_qt(_screen, _config):
            raise AssertionError("default wrapper should be unwrapped only by identity")

        def custom_qt(_screen, _config):
            return {"backend": "custom"}

        bridge.capture_screen_frame_payload(
            "screen",
            {},
            qt_capture=custom_qt,
            default_qt_capture=default_qt,
        )

        self.assertIs(fake.capture_calls[0]["qt_capture"], custom_qt)

    def test_main_screen_capture_wrappers_delegate_to_bridge(self) -> None:
        import main

        bridge = mock.Mock()
        bridge.capture_qt_screen_frame_payload.return_value = {"backend": "qt"}
        bridge.capture_screen_frame_payload.return_value = {"backend": "screen"}
        bridge.collect_screen_observations.return_value = {"observed": True}

        with mock.patch.object(main, "_screen_capture_bridge", return_value=bridge):
            qt_payload = main.capture_qt_screen_frame_payload("screen", {"quality": 75})
            screen_payload = main.capture_screen_frame_payload("screen", {"enabled": True})
            observations = main.collect_screen_observations({"include_ui_metadata": True}, platform_name="darwin")

        self.assertEqual(qt_payload, {"backend": "qt"})
        self.assertEqual(screen_payload, {"backend": "screen"})
        self.assertEqual(observations, {"observed": True})
        bridge.capture_qt_screen_frame_payload.assert_called_once()
        self.assertIs(
            bridge.capture_qt_screen_frame_payload.call_args.kwargs["default_pixmap_composer"],
            main.compose_qt_screens_pixmap,
        )
        self.assertIs(
            bridge.capture_qt_screen_frame_payload.call_args.kwargs["default_pixmap_encoder"],
            main._encode_pixmap_frame,
        )
        bridge.capture_screen_frame_payload.assert_called_once()
        self.assertIs(
            bridge.capture_screen_frame_payload.call_args.kwargs["default_macos_capture"],
            main.capture_macos_screencapture_payload,
        )
        self.assertIs(
            bridge.capture_screen_frame_payload.call_args.kwargs["default_qt_capture"],
            main.capture_qt_screen_frame_payload,
        )
        bridge.collect_screen_observations.assert_called_once_with(
            {"include_ui_metadata": True},
            platform_name="darwin",
            runner=main.subprocess.run,
        )


if __name__ == "__main__":
    unittest.main()
