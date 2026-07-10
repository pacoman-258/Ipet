from __future__ import annotations

import hashlib
import importlib
import subprocess
import unittest
from unittest import mock


def _screen_capture_module():
    try:
        return importlib.import_module("body.screen_capture")
    except ImportError as exc:
        raise AssertionError("body.screen_capture module should be importable") from exc


class ScreenCapturePayloadTests(unittest.TestCase):
    def test_payload_from_encoded_frame_records_hash_scope_layout_and_dimensions(self) -> None:
        screen_capture = _screen_capture_module()
        data_url = "data:image/jpeg;base64,abc"

        payload = screen_capture._payload_from_encoded_frame(
            "image/jpeg",
            data_url,
            capture_backend="qt_fallback",
            display_layout=[{"x": 0, "y": 0, "width": 100, "height": 80}],
            image_width=50,
            image_height=40,
        )

        self.assertEqual(payload["mime_type"], "image/jpeg")
        self.assertEqual(payload["data_url"], data_url)
        self.assertEqual(
            payload["frame_hash"],
            "sha256:" + hashlib.sha256(data_url.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(payload["visual_hash"], payload["frame_hash"])
        self.assertEqual(payload["capture_backend"], "qt_fallback")
        self.assertEqual(payload["capture_scope"], "visible_spaces_all_displays")
        self.assertEqual(payload["display_count"], 1)
        self.assertEqual(payload["display_layout"], [{"x": 0, "y": 0, "width": 100, "height": 80}])
        self.assertEqual(payload["image_width"], 50)
        self.assertEqual(payload["image_height"], 40)

    def test_qt_capture_payload_uses_injected_screen_composer_and_encoder(self) -> None:
        screen_capture = _screen_capture_module()
        screens = [object(), object()]
        composer = mock.Mock(
            return_value=(
                "canvas",
                {
                    "display_count": 2,
                    "display_layout": [
                        {"x": 0, "y": 0, "width": 120, "height": 90},
                        {"x": 120, "y": 0, "width": 80, "height": 90},
                    ],
                },
            )
        )
        encoder = mock.Mock(return_value=("image/jpeg", "data:image/jpeg;base64,qt", 200, 90))

        payload = screen_capture.capture_qt_screen_frame_payload(
            object(),
            {"max_width": 1280, "jpeg_quality": 75},
            screens_provider=lambda: screens,
            pixmap_composer=composer,
            pixmap_encoder=encoder,
        )

        self.assertEqual(payload["capture_backend"], "qt_fallback")
        self.assertEqual(payload["capture_scope"], "visible_spaces_all_displays")
        self.assertEqual(payload["display_count"], 2)
        self.assertEqual(payload["display_layout"][1]["x"], 120)
        self.assertEqual(payload["image_width"], 200)
        self.assertEqual(payload["image_height"], 90)
        composer.assert_called_once_with(screens)
        encoder.assert_called_once_with("canvas", {"max_width": 1280, "jpeg_quality": 75})

    def test_collect_screen_observations_uses_injected_macos_runner(self) -> None:
        screen_capture = _screen_capture_module()
        result = subprocess.CompletedProcess(
            args=["osascript"],
            returncode=0,
            stdout="QQ\nApple, QQ, 编辑, 窗口, 帮助\n主窗口\n",
            stderr="",
        )
        runner = mock.Mock(return_value=result)

        payload = screen_capture.collect_screen_observations(
            {"enabled": True, "include_ui_metadata": True},
            platform_name="darwin",
            runner=runner,
        )

        self.assertEqual(payload["desktop_context"]["foreground_app"], "QQ")
        self.assertEqual(payload["desktop_context"]["frontmost_process"], "QQ")
        self.assertEqual(payload["desktop_context"]["window_title"], "主窗口")
        self.assertEqual(payload["desktop_context"]["menu_bar_items"], ["Apple", "QQ", "编辑", "窗口", "帮助"])
        runner.assert_called_once()

    def test_capture_screen_frame_payload_prefers_macos_and_falls_back_to_qt(self) -> None:
        screen_capture = _screen_capture_module()
        qt_capture = mock.Mock(
            return_value={
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,qt",
                "capture_backend": "qt_fallback",
                "capture_scope": "visible_spaces_all_displays",
                "display_count": 1,
            }
        )

        payload = screen_capture.capture_screen_frame_payload(
            object(),
            {"max_width": 1280, "jpeg_quality": 75},
            platform_name="darwin",
            macos_capture=lambda config: (_ for _ in ()).throw(RuntimeError("screencapture failed")),
            qt_capture=qt_capture,
        )

        self.assertEqual(payload["capture_backend"], "qt_fallback")
        qt_capture.assert_called_once()


if __name__ == "__main__":
    unittest.main()
