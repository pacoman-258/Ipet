from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main


class MainRuntimeEnvTests(unittest.TestCase):
    def test_augment_process_path_includes_project_and_user_bins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            home = Path(tmp) / "home"
            project_bin = root / ".venv" / "bin"
            local_bin = home / ".local" / "bin"
            project_bin.mkdir(parents=True, exist_ok=True)
            local_bin.mkdir(parents=True, exist_ok=True)
            env = {"PATH": "/usr/bin"}
            with mock.patch.object(main, "ROOT_DIR", root), mock.patch("main.Path.home", return_value=home):
                path_value = main._augment_process_path(env)
            parts = path_value.split(os.pathsep)
            self.assertEqual(parts[0], str(project_bin))
            self.assertIn(str(local_bin), parts)

    def test_resolve_backend_python_uses_python_version_hint(self) -> None:
        with mock.patch.object(main, "_preferred_python_commands", return_value=["python3.12"]), mock.patch.object(
            main,
            "_python_supports_backend",
            side_effect=lambda candidate: candidate == "python3.12",
        ), mock.patch.object(
            main,
            "_python_command_exists",
            side_effect=lambda candidate: candidate == "python3.12",
        ), mock.patch.object(main, "sys") as sys_mock:
            sys_mock.executable = "/usr/bin/python3"
            resolved = main.resolve_backend_python()
        self.assertEqual(resolved, "python3.12")

    def test_runtime_log_path_falls_under_runtime_logs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir(parents=True, exist_ok=True)
            with mock.patch.object(main, "ROOT_DIR", root), mock.patch.object(main, "RUNTIME_LOG_DIR", root / ".runtime-logs"):
                path = main._runtime_log_path("backend")
            self.assertEqual(path, root / ".runtime-logs" / "backend.log")

    def test_tail_runtime_log_returns_last_non_empty_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backend.log"
            path.write_text("one\n\n two \nthree\n", encoding="utf-8")
            tail = main._tail_runtime_log(path, max_lines=2)
        self.assertEqual(tail, " two\nthree")

    def test_macos_uses_opaque_window_defaults(self) -> None:
        self.assertFalse(main._should_use_translucent_window(force_opaque=False, platform_name="darwin"))
        self.assertEqual(main._desktop_pet_background_color("darwin"), (18, 18, 18, 255))

    def test_macos_window_flags_exclude_tool_flag(self) -> None:
        flags = main._desktop_pet_window_flags("darwin")
        self.assertEqual(flags, main.Qt.WindowType.Window)
        self.assertFalse(bool(flags & main.Qt.WindowType.FramelessWindowHint))
        self.assertFalse(bool(flags & main.Qt.WindowType.WindowStaysOnTopHint))

    def test_macos_qt_runtime_env_defaults_disable_gpu_paths(self) -> None:
        env = main._qt_runtime_env_defaults("darwin")
        self.assertNotIn("QT_OPENGL", env)
        self.assertIn("--disable-logging", env["QTWEBENGINE_CHROMIUM_FLAGS"])
        self.assertIn("--log-level=3", env["QTWEBENGINE_CHROMIUM_FLAGS"])
        self.assertIn("--enable-webgl", env["QTWEBENGINE_CHROMIUM_FLAGS"])
        self.assertIn("--ignore-gpu-blocklist", env["QTWEBENGINE_CHROMIUM_FLAGS"])

    def test_webgl_remains_enabled_for_live2d(self) -> None:
        self.assertTrue(main._should_enable_webgl("darwin"))
        self.assertTrue(main._should_enable_webgl("win32"))

    def test_only_non_macos_forces_software_opengl(self) -> None:
        self.assertFalse(main._should_force_software_opengl("darwin"))
        self.assertTrue(main._should_force_software_opengl("win32"))

    def test_macos_disables_python_event_filters(self) -> None:
        self.assertFalse(main._should_install_python_event_filters("darwin"))
        self.assertTrue(main._should_install_python_event_filters("win32"))

    def test_macos_prefers_pyqt_bindings(self) -> None:
        self.assertTrue(main._prefer_pyqt_bindings("darwin"))
        self.assertFalse(main._prefer_pyqt_bindings("win32"))
