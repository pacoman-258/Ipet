from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import desktop_runtime


class DesktopRuntimeTests(unittest.TestCase):
    def test_qt_binding_preference_is_pyside_first_and_process_consistent(self) -> None:
        with mock.patch.dict(desktop_runtime.sys.modules, {}, clear=True):
            self.assertFalse(desktop_runtime.prefer_pyqt_bindings("darwin"))
        with mock.patch.dict(
            desktop_runtime.sys.modules,
            {"PyQt6.QtCore": object()},
            clear=True,
        ):
            self.assertTrue(desktop_runtime.prefer_pyqt_bindings("darwin"))
        with mock.patch.dict(
            desktop_runtime.sys.modules,
            {"PyQt6.QtCore": object(), "PySide6.QtCore": object()},
            clear=True,
        ):
            self.assertFalse(desktop_runtime.prefer_pyqt_bindings("darwin"))

    def test_macos_runtime_env_keeps_webgl_without_software_opengl_default(self) -> None:
        env = desktop_runtime.qt_runtime_env_defaults("darwin")

        self.assertEqual(env["QTWEBENGINE_DISABLE_SANDBOX"], "1")
        self.assertNotIn("QT_OPENGL", env)
        flags = env["QTWEBENGINE_CHROMIUM_FLAGS"]
        self.assertIn("--enable-webgl", flags)
        self.assertIn("--ignore-gpu-blocklist", flags)

    def test_local_service_url_and_host_port_parsing(self) -> None:
        self.assertTrue(desktop_runtime.is_local_service_url("http://localhost:8008"))
        self.assertTrue(desktop_runtime.is_local_service_url("http://127.0.0.1:8012/api"))
        self.assertFalse(desktop_runtime.is_local_service_url("http://192.168.1.20:8008"))
        self.assertFalse(desktop_runtime.is_local_service_url("https://example.com"))

        self.assertEqual(
            desktop_runtime.parse_service_host_port("http://localhost:9000/api", default_port=8008),
            ("localhost", 9000),
        )
        self.assertEqual(
            desktop_runtime.parse_service_host_port("", default_port=8008),
            ("127.0.0.1", 8008),
        )

    def test_service_log_tail_returns_last_non_empty_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backend.log"
            path.write_text("one\n\n two \nthree\n\nfour\n", encoding="utf-8")

            tail = desktop_runtime.tail_service_log(path, max_lines=3)

        self.assertEqual(tail, " two\nthree\nfour")

    def test_backend_launch_url_skips_unavailable_local_ports_with_injected_checker(self) -> None:
        checked: list[tuple[str, int]] = []

        def available(host: str, port: int) -> bool:
            checked.append((host, port))
            return port == 8010

        launch_url = desktop_runtime.pick_backend_launch_url(
            "http://127.0.0.1:8008",
            max_offset=4,
            is_port_available=available,
            default_backend_url="http://127.0.0.1:8008",
        )

        self.assertEqual(launch_url, "http://127.0.0.1:8010")
        self.assertEqual(checked, [("127.0.0.1", 8008), ("127.0.0.1", 8009), ("127.0.0.1", 8010)])

    def test_backend_launch_url_leaves_remote_preference_unchanged(self) -> None:
        launch_url = desktop_runtime.pick_backend_launch_url(
            "https://api.example.test",
            is_port_available=lambda host, port: self.fail("remote URLs must not probe local ports"),
            default_backend_url="http://127.0.0.1:8008",
        )

        self.assertEqual(launch_url, "https://api.example.test")


if __name__ == "__main__":
    unittest.main()
