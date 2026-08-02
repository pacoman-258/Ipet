from __future__ import annotations

import ast
import inspect
import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import click_preview
from body import active_vision as body_active_vision
import main


class _FakeTimer:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.interval = 0
        self.timeout = mock.Mock()
        self.timeout.connect = mock.Mock()

    def start(self, interval: int) -> None:
        self.started = True
        self.stopped = False
        self.interval = interval

    def stop(self) -> None:
        self.stopped = True
        self.started = False


class _FakeWindow:
    def __init__(self) -> None:
        self.visible = True
        self.calls: list[str] = []

    def isVisible(self) -> bool:
        return self.visible

    def hide(self) -> None:
        self.calls.append("hide")
        self.visible = False

    def show(self) -> None:
        self.calls.append("show")
        self.visible = True

    def raise_(self) -> None:
        self.calls.append("raise")

    def activateWindow(self) -> None:
        self.calls.append("activate")


class _DesktopCommandHost(_FakeWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config = {
            "vision": {
                "enabled": True,
                "active_observation": {"enabled": True, "settle_ms": 1, "timeout_sec": 12},
            }
        }
        self.responses: list[tuple[dict, str, dict | None]] = []

    def _write_desktop_command_response(self, command, status, result=None) -> None:
        self.responses.append((command, status, result))

    def _write_desktop_host_heartbeat(self) -> None:
        pass


class _FakeSettingsWindow:
    def __init__(self, *, visible: bool = True) -> None:
        self.visible = visible
        self.calls: list[str] = []
        self.urls: list[str] = []

    def isVisible(self) -> bool:
        return self.visible

    def show(self) -> None:
        self.calls.append("show")
        self.visible = True

    def raise_(self) -> None:
        self.calls.append("raise")

    def activateWindow(self) -> None:
        self.calls.append("activate")

    def setUrl(self, url) -> None:
        self.calls.append("setUrl")
        self.urls.append(str(url))


class _SettingsWindowHost:
    def __init__(self, window: _FakeSettingsWindow | None = None) -> None:
        self.config = {"chat": {"backend_url": "http://127.0.0.1:8008"}}
        self.settings_window = window
        self._settings_window_url = "http://127.0.0.1:8008/settings" if window is not None else ""
        self.created: list[_FakeSettingsWindow] = []
        self.backend_calls = 0

    def ensure_backend_service(self) -> None:
        self.backend_calls += 1

    def _create_settings_window(self, url: str):
        window = _FakeSettingsWindow(visible=False)
        self.created.append(window)
        return window

    def _settings_page_url(self) -> str:
        return main.DesktopPet._settings_page_url(self)

    def _settings_window_is_usable(self, window) -> bool:
        return main.DesktopPet._settings_window_is_usable(self, window)

    def _navigate_settings_window(self, window, url: str) -> None:
        main.DesktopPet._navigate_settings_window(self, window, url)

    def _show_or_focus_settings_window(self, url: str) -> None:
        main.DesktopPet._show_or_focus_settings_window(self, url)


class MainDesktopEnvTests(unittest.TestCase):
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

    def test_service_log_path_falls_under_service_logs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir(parents=True, exist_ok=True)
            with mock.patch.object(main, "ROOT_DIR", root), mock.patch.object(main, "SERVICE_LOG_DIR", root / ".service-logs"):
                path = main._service_log_path("backend")
            self.assertEqual(path, root / ".service-logs" / "backend.log")

    def test_tail_service_log_returns_last_non_empty_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backend.log"
            path.write_text("one\n\n two \nthree\n", encoding="utf-8")
            tail = main._tail_service_log(path, max_lines=2)
        self.assertEqual(tail, " two\nthree")

    def test_macos_uses_transparent_window_defaults(self) -> None:
        self.assertTrue(main._should_use_translucent_window(force_opaque=False, platform_name="darwin"))
        self.assertEqual(main._desktop_pet_background_color("darwin"), (0, 0, 0, 0))

    def test_macos_window_flags_create_frameless_always_on_top_pet(self) -> None:
        flags = main._desktop_pet_window_flags("darwin")
        self.assertTrue(bool(flags & main.Qt.WindowType.FramelessWindowHint))
        self.assertTrue(bool(flags & main.Qt.WindowType.WindowStaysOnTopHint))
        self.assertTrue(bool(flags & main.Qt.WindowType.Tool))
        self.assertEqual(
            main._window_defaults.always_visible_tool_window_attribute(
                main.Qt.WidgetAttribute,
                is_macos=True,
            ),
            main.Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow,
        )

    def test_macos_qt_runtime_env_defaults_disable_gpu_paths(self) -> None:
        env = main._qt_runtime_env_defaults("darwin")
        self.assertNotIn("QT_OPENGL", env)
        self.assertIn("--disable-logging", env["QTWEBENGINE_CHROMIUM_FLAGS"])
        self.assertIn("--log-level=3", env["QTWEBENGINE_CHROMIUM_FLAGS"])
        self.assertIn("--enable-webgl", env["QTWEBENGINE_CHROMIUM_FLAGS"])
        self.assertIn("--ignore-gpu-blocklist", env["QTWEBENGINE_CHROMIUM_FLAGS"])

    def test_macos_native_stderr_filter_only_matches_known_input_method_noise(self) -> None:
        self.assertTrue(
            main._is_macos_native_stderr_noise(
                "2026-05-26 18:48:24.192 python3[72161:5490386] "
                "TSMSendMessageToUIServer: CFMessagePortSendRequest FAILED(-1) to send to port com.apple.tsm.uiserver",
                platform_name="darwin",
            )
        )
        self.assertTrue(
            main._is_macos_native_stderr_noise(
                "2026-05-26 18:48:48.740 python3[72161:5490386] "
                "error messaging the mach port for IMKCFRunLoopWakeUpReliable",
                platform_name="darwin",
            )
        )
        self.assertFalse(main._is_macos_native_stderr_noise("real traceback line", platform_name="darwin"))
        self.assertFalse(
            main._is_macos_native_stderr_noise(
                "TSMSendMessageToUIServer: CFMessagePortSendRequest FAILED(-1)",
                platform_name="linux",
            )
        )

    def test_webgl_remains_enabled_for_live2d(self) -> None:
        self.assertTrue(main._should_enable_webgl("darwin"))
        self.assertTrue(main._should_enable_webgl("win32"))

    def test_open_settings_page_creates_single_qt_settings_window(self) -> None:
        host = _SettingsWindowHost()

        main.DesktopPet.open_settings_page(host)
        main.DesktopPet.open_settings_page(host)

        self.assertEqual(host.backend_calls, 2)
        self.assertEqual(len(host.created), 1)
        self.assertIs(host.settings_window, host.created[0])
        self.assertEqual(host.created[0].calls, ["setUrl", "show", "raise", "activate", "raise", "activate"])
        self.assertEqual(host._settings_window_url, "http://127.0.0.1:8008/settings")

    def test_open_settings_page_refocuses_existing_settings_window(self) -> None:
        window = _FakeSettingsWindow(visible=True)
        host = _SettingsWindowHost(window)

        main.DesktopPet.open_settings_page(host)

        self.assertEqual(host.created, [])
        self.assertEqual(window.calls, ["raise", "activate"])

    def test_open_settings_page_shows_hidden_existing_settings_window(self) -> None:
        window = _FakeSettingsWindow(visible=False)
        host = _SettingsWindowHost(window)

        main.DesktopPet.open_settings_page(host)

        self.assertEqual(host.created, [])
        self.assertEqual(window.calls, ["show", "raise", "activate"])

    def test_only_non_macos_forces_software_opengl(self) -> None:
        self.assertFalse(main._should_force_software_opengl("darwin"))
        self.assertTrue(main._should_force_software_opengl("win32"))

    def test_macos_disables_python_event_filters(self) -> None:
        self.assertFalse(main._should_install_python_event_filters("darwin"))
        self.assertTrue(main._should_install_python_event_filters("win32"))

    def test_runtime_prefers_pyside_bindings(self) -> None:
        self.assertFalse(main._prefer_pyqt_bindings("darwin"))
        self.assertFalse(main._prefer_pyqt_bindings("win32"))

    def test_default_config_omits_legacy_runtime_platform_config(self) -> None:
        self.assertNotIn("hermes", main.DEFAULT_CONFIG)
        self.assertNotIn("runtime", main.DEFAULT_CONFIG)
        self.assertNotIn("tooling", main.DEFAULT_CONFIG["chat"])
        self.assertNotIn("skills", main.DEFAULT_CONFIG["chat"])
        for key in ("brain", "human_ops", "memory", "skills"):
            self.assertIn(key, main.DEFAULT_CONFIG)

    def test_load_config_drops_legacy_runtime_platform_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir(parents=True, exist_ok=True)
            config_path = root / "pet_config.json"
            removed_runtime_key = "run" + "time"
            removed_service_key = "her" + "mes"
            config_path.write_text(
                json.dumps(
                    {
                        removed_service_key: {
                            "enabled": True,
                            "auto_start": True,
                            "command": "uv run old-service",
                            "cwd": "old-service",
                            "base_url": "http://127.0.0.1:9100/",
                            "health_path": "healthz",
                            "startup_timeout_sec": "7",
                        },
                        removed_runtime_key: {"active": "old-service"},
                        "chat": {"tooling": {"enabled": True}, "skills": {"enabled": True}},
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(main, "ROOT_DIR", root), mock.patch.object(main, "CONFIG_PATH", config_path):
                config = main.load_config()
        self.assertNotIn(removed_service_key, config)
        self.assertNotIn(removed_runtime_key, config)
        self.assertNotIn("tooling", config["chat"])
        self.assertNotIn("skills", config["chat"])

    def test_backend_route_support_uses_health_not_legacy_topic_openapi(self) -> None:
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = {"ok": True}

        with mock.patch.object(main.requests, "get", return_value=response) as get_mock:
            self.assertTrue(main.backend_supports_required_routes("http://127.0.0.1:8008"))

        get_mock.assert_called_once_with("http://127.0.0.1:8008/api/health", timeout=1.5)

    def test_desktop_source_omits_removed_sidecar_surface(self) -> None:
        source = inspect.getsource(main.DesktopPet)
        forbidden = [
            "ensure_" + "active_" + "runtime_" + "sidecar",
            "ensure_" + "her" + "mes_" + "sidecar",
            "stop_" + "her" + "mes_" + "sidecar",
            "stop_" + "runtime_" + "sidecar",
            "is_" + "her" + "mes_" + "healthy",
        ]
        for name in forbidden:
            self.assertNotIn(name, source)

    def test_desktop_init_source_does_not_start_removed_sidecar(self) -> None:
        tree = ast.parse(textwrap.dedent(inspect.getsource(main.DesktopPet.__init__)))
        calls = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        self.assertNotIn("ensure_" + "active_" + "runtime_" + "sidecar", calls)
        self.assertNotIn("ensure_" + "her" + "mes_" + "sidecar", calls)

    def test_reload_config_from_disk_does_not_start_removed_sidecar(self) -> None:
        loaded_config = {"window": {"locked": False}, "chat": {"asr": {"enabled": False}}}
        host = SimpleNamespace(
            config=loaded_config,
            vision_controller=mock.Mock(),
            apply_window_geometry_from_config=mock.Mock(),
            refresh_motion_list=mock.Mock(),
            ensure_asr_service=mock.Mock(),
            stop_asr_service=mock.Mock(),
            apply_config_to_web=mock.Mock(),
        )

        with mock.patch.object(main, "load_config", return_value=loaded_config) as load_config_mock:
            main.DesktopPet.reload_config_from_disk(host)

        self.assertFalse(hasattr(main.DesktopPet, "ensure_" + "active_" + "runtime_" + "sidecar"))
        self.assertFalse(hasattr(main.DesktopPet, "ensure_" + "her" + "mes_" + "sidecar"))
        load_config_mock.assert_called_once_with()
        host.stop_asr_service.assert_called_once()

    def test_control_panel_source_does_not_call_legacy_mcp_routes(self) -> None:
        source = inspect.getsource(main.ControlPanel)
        removed_path = "/api/" + "m" + "cp" + "/"
        self.assertNotIn(removed_path, source)

    def test_load_config_normalizes_vision_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir(parents=True, exist_ok=True)
            config_path = root / "pet_config.json"
            config_path.write_text(
                """
                {
                  "vision": {
                    "enabled": true,
                    "capture_interval_ms": 10,
                    "max_width": 9999,
                    "jpeg_quality": 10,
                    "context_ttl_sec": 999,
                    "inject_policy": "bad",
                    "persist_frames": true
                  }
                }
                """,
                encoding="utf-8",
            )
            with mock.patch.object(main, "ROOT_DIR", root), mock.patch.object(main, "CONFIG_PATH", config_path):
                config = main.load_config()
        self.assertTrue(config["vision"]["enabled"])
        self.assertEqual(config["vision"]["capture_interval_ms"], 1000)
        self.assertEqual(config["vision"]["max_width"], 2560)
        self.assertEqual(config["vision"]["jpeg_quality"], 35)
        self.assertEqual(config["vision"]["context_ttl_sec"], 300)
        self.assertEqual(config["vision"]["inject_policy"], "when_requested")
        self.assertFalse(config["vision"]["persist_frames"])

    def test_load_config_preserves_saved_brain_and_observe_api_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir(parents=True, exist_ok=True)
            config_path = root / "pet_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "brain": {
                            "provider": "google_aistudio",
                            "model_name": "gemini-chat",
                            "api_key": "gemini-saved-secret",
                        },
                        "human_ops": {
                            "observe_model": {
                                "enabled": True,
                                "provider": "google_aistudio",
                                "model_name": "gemini-observe",
                                "api_key": "observe-saved-secret",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(main, "ROOT_DIR", root), mock.patch.object(main, "CONFIG_PATH", config_path):
                config = main.load_config()

        self.assertEqual(config["brain"]["provider"], "google_aistudio")
        self.assertEqual(config["brain"]["api_key"], "gemini-saved-secret")
        self.assertEqual(config["human_ops"]["observe_model"]["provider"], "google_aistudio")
        self.assertEqual(config["human_ops"]["observe_model"]["api_key"], "observe-saved-secret")

    def test_screen_vision_controller_disabled_does_not_start_timer(self) -> None:
        timer = _FakeTimer()
        controller = main.ScreenVisionController(
            timer_factory=lambda parent=None: timer,
            screen_provider=lambda: None,
            frame_encoder=lambda screen, config: ("image/jpeg", "data:image/jpeg;base64,abc"),
            observation_provider=lambda config: {},
            task_runner=lambda task: task(),
            post_frame=lambda url, payload, timeout: None,
        )
        controller.apply_config({"vision": {"enabled": False}, "chat": {"backend_url": "http://127.0.0.1:8008"}})
        self.assertFalse(timer.started)

    def test_screen_vision_controller_posts_frame_when_enabled(self) -> None:
        timer = _FakeTimer()
        posts = []
        screen = object()
        controller = main.ScreenVisionController(
            timer_factory=lambda parent=None: timer,
            screen_provider=lambda: screen,
            frame_encoder=lambda active_screen, config: ("image/jpeg", "data:image/jpeg;base64,abc"),
            observation_provider=lambda config: {
                "change_summary": "macOS 前台应用切换为：QQ",
                "important_objects": ["QQ"],
                "visible_text": ["Apple", "QQ", "编辑", "窗口", "帮助"],
                "desktop_context": {"foreground_app": "QQ"},
            },
            task_runner=lambda task: task(),
            post_frame=lambda url, payload, timeout: posts.append((url, payload, timeout)),
        )
        controller.apply_config(
            {
                "vision": {"enabled": True, "capture_interval_ms": 1234},
                "chat": {"backend_url": "http://127.0.0.1:8008"},
            }
        )
        self.assertTrue(timer.started)
        self.assertEqual(timer.interval, 1234)

        controller.capture_once()
        self.assertEqual(posts[0][0], "http://127.0.0.1:8008/api/vision/frame")
        self.assertEqual(posts[0][1]["mime_type"], "image/jpeg")
        self.assertEqual(posts[0][1]["data_url"], "data:image/jpeg;base64,abc")
        self.assertEqual(posts[0][1]["change_summary"], "macOS 前台应用切换为：QQ")
        self.assertEqual(posts[0][1]["desktop_context"]["foreground_app"], "QQ")
        self.assertNotIn("summary", posts[0][1])
        self.assertNotIn("observations", posts[0][1])

        controller.stop()
        self.assertTrue(timer.stopped)

    def test_screen_vision_controller_posts_on_background_runner_and_skips_overlap(self) -> None:
        timer = _FakeTimer()
        tasks = []
        posts = []
        screen = object()
        controller = main.ScreenVisionController(
            timer_factory=lambda parent=None: timer,
            screen_provider=lambda: screen,
            frame_encoder=lambda active_screen, config: ("image/jpeg", "data:image/jpeg;base64,abc"),
            observation_provider=lambda config: {"change_summary": "后台采集", "desktop_context": {"foreground_app": "QQ"}},
            task_runner=lambda task: tasks.append(task),
            post_frame=lambda url, payload, timeout: posts.append((url, payload, timeout)),
        )
        controller.apply_config({"vision": {"enabled": True}, "chat": {"backend_url": "http://127.0.0.1:8008"}})

        controller.capture_once()
        controller.capture_once()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(posts, [])

        tasks[0]()
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][1]["change_summary"], "后台采集")
        self.assertNotIn("observations", posts[0][1])

    def test_screen_vision_controller_can_encode_frame_on_background_runner(self) -> None:
        timer = _FakeTimer()
        tasks = []
        posts = []
        encoded = []
        screen = object()

        def frame_encoder(active_screen, config):
            encoded.append(active_screen)
            return "image/jpeg", "data:image/jpeg;base64,abc"

        controller = main.ScreenVisionController(
            timer_factory=lambda parent=None: timer,
            screen_provider=lambda: screen,
            frame_encoder=frame_encoder,
            observation_provider=lambda config: {},
            task_runner=lambda task: tasks.append(task),
            post_frame=lambda url, payload, timeout: posts.append((url, payload, timeout)),
            background_capture=True,
        )
        controller.apply_config({"vision": {"enabled": True}, "chat": {"backend_url": "http://127.0.0.1:8008"}})

        controller.capture_once()

        self.assertEqual(encoded, [])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(posts, [])

        tasks[0]()

        self.assertEqual(encoded, [screen])
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][1]["data_url"], "data:image/jpeg;base64,abc")

    def test_screen_vision_controller_extends_timeout_for_enabled_analyzer(self) -> None:
        timer = _FakeTimer()
        posts = []
        controller = main.ScreenVisionController(
            timer_factory=lambda parent=None: timer,
            screen_provider=lambda: object(),
            frame_encoder=lambda active_screen, config: ("image/jpeg", "data:image/jpeg;base64,abc"),
            observation_provider=lambda config: {},
            task_runner=lambda task: task(),
            post_frame=lambda url, payload, timeout: posts.append((url, payload, timeout)),
        )
        controller.apply_config(
            {
                "vision": {
                    "enabled": True,
                    "analyzer": {"enabled": True, "provider": "macos_vision_ocr", "timeout_sec": 12.5},
                },
                "chat": {"backend_url": "http://127.0.0.1:8008"},
            }
        )

        controller.capture_once()

        self.assertEqual(posts[0][2], 13.5)

    def test_capture_screen_frame_payload_prefers_macos_screencapture_metadata(self) -> None:
        qt_capture = mock.Mock()

        payload = main.capture_screen_frame_payload(
            object(),
            {"max_width": 1280, "jpeg_quality": 75},
            platform_name="darwin",
            macos_capture=lambda config: {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,macos",
                "capture_backend": "macos_screencapture",
                "capture_scope": "visible_spaces_all_displays",
                "display_count": 2,
            },
            qt_capture=qt_capture,
        )

        self.assertEqual(payload["capture_backend"], "macos_screencapture")
        self.assertEqual(payload["capture_scope"], "visible_spaces_all_displays")
        self.assertEqual(payload["display_count"], 2)
        qt_capture.assert_not_called()

    def test_capture_screen_frame_payload_falls_back_to_qt_capture(self) -> None:
        qt_capture = mock.Mock(
            return_value={
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,qt",
                "capture_backend": "qt_fallback",
                "capture_scope": "visible_spaces_all_displays",
                "display_count": 1,
            }
        )

        payload = main.capture_screen_frame_payload(
            object(),
            {"max_width": 1280, "jpeg_quality": 75},
            platform_name="darwin",
            macos_capture=lambda config: (_ for _ in ()).throw(RuntimeError("screencapture failed")),
            qt_capture=qt_capture,
        )

        self.assertEqual(payload["capture_backend"], "qt_fallback")
        self.assertEqual(payload["capture_scope"], "visible_spaces_all_displays")
        self.assertEqual(payload["display_count"], 1)
        qt_capture.assert_called_once()

    def test_capture_screen_frame_payload_can_disable_qt_fallback(self) -> None:
        qt_capture = mock.Mock()

        with self.assertRaises(RuntimeError):
            main.capture_screen_frame_payload(
                object(),
                {"max_width": 1280, "jpeg_quality": 75},
                platform_name="darwin",
                macos_capture=lambda config: (_ for _ in ()).throw(RuntimeError("screencapture failed")),
                qt_capture=qt_capture,
                allow_qt_fallback=False,
            )

        qt_capture.assert_not_called()

    def test_active_vision_focus_interaction_blocks_generic_ui_actions(self) -> None:
        scripts = []

        def runner(argv, **kwargs):
            scripts.append(" ".join(str(part) for part in argv))
            completed = mock.Mock()
            completed.returncode = 0
            completed.stdout = "accessibility_raise=success\nwindow_focus_click=success\n"
            completed.stderr = ""
            return completed

        trace = main.run_active_vision_light_interaction(
            mode="focus_target",
            target_id="target-safari",
            desktop_targets=[
                {
                    "target_id": "target-safari",
                    "app": "Safari",
                    "title": "Video",
                    "bounds": {"x": 10, "y": 20, "width": 900, "height": 600},
                    "focus_point": {"x": 120, "y": 32},
                }
            ],
            actions=["focus_target", "type_text", "delete_file", "small_scroll"],
            platform_name="darwin",
            runner=runner,
        )

        combined = "\n".join(scripts)
        self.assertIn("System Events", combined)
        self.assertIn("click at {120, 32}", combined)
        self.assertNotIn("keystroke", combined)
        self.assertNotIn("click button", combined)
        self.assertNotIn("delete_file", combined)
        self.assertIn("focus_target", trace["actions"])
        self.assertEqual(trace["click_policy"], "window_focus_only")
        self.assertEqual(trace["focused_target"]["target_id"], "target-safari")
        self.assertEqual(
            [entry["action"] for entry in trace["action_trace"]],
            ["accessibility_raise", "window_focus_click"],
        )
        self.assertEqual(trace["action_trace"][1]["click_policy"], "window_focus_only")
        self.assertEqual(trace["action_trace"][1]["click_point"], {"x": 120, "y": 32})
        self.assertEqual(trace["click_point"], {"x": 120, "y": 32})
        self.assertIn("type_text", trace["blocked_actions"])
        self.assertIn("delete_file", trace["blocked_actions"])
        self.assertIn("small_scroll", trace["blocked_actions"])

    def test_active_vision_focus_interaction_reports_unknown_step_statuses(self) -> None:
        def runner(argv, **kwargs):
            completed = mock.Mock()
            completed.returncode = 0
            completed.stdout = "accessibility_raise=unknown:permission denied\nwindow_focus_click=unknown:permission denied\n"
            completed.stderr = ""
            return completed

        trace = main.run_active_vision_light_interaction(
            mode="focus_target",
            target_id="target-safari",
            desktop_targets=[
                {
                    "target_id": "target-safari",
                    "app": "Safari",
                    "title": "Video",
                    "bounds": {"x": 10, "y": 20, "width": 900, "height": 600},
                    "focus_point": {"x": 120, "y": 32},
                }
            ],
            actions=["focus_target"],
            platform_name="darwin",
            runner=runner,
        )

        self.assertEqual(trace["status"], "error")
        self.assertNotIn("focus_target", trace["actions"])
        self.assertEqual(trace["click_point"], {})
        self.assertEqual(trace["action_trace"][0]["status"], "unknown")
        self.assertEqual(trace["action_trace"][1]["status"], "unknown")
        self.assertTrue(any("accessibility_raise failed" in item for item in trace["unknowns"]))
        self.assertTrue(any("window_focus_click failed" in item for item in trace["unknowns"]))

    def test_active_vision_focus_interaction_blocks_non_focus_click_policy(self) -> None:
        runner = mock.Mock()

        trace = main.run_active_vision_light_interaction(
            mode="focus_target",
            target_id="target-safari",
            click_policy="content_click",
            desktop_targets=[
                {
                    "target_id": "target-safari",
                    "app": "Safari",
                    "title": "Video",
                    "bounds": {"x": 10, "y": 20, "width": 900, "height": 600},
                    "focus_point": {"x": 120, "y": 32},
                }
            ],
            actions=["focus_target"],
            platform_name="darwin",
            runner=runner,
        )

        runner.assert_not_called()
        self.assertIn("click_policy:content_click", trace["blocked_actions"])
        self.assertIn("focus_target", trace["blocked_actions"])
        self.assertEqual(trace["action_trace"], [])

    def test_macos_desktop_target_script_uses_non_conflicting_rows_variable(self) -> None:
        calls = []
        timeouts = []

        def runner(argv, **kwargs):
            calls.append(list(argv))
            timeouts.append(kwargs.get("timeout"))
            return main.subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        targets, errors = main.discover_macos_active_vision_desktop_targets(
            platform_name="darwin",
            runner=runner,
        )

        self.assertEqual(targets, [])
        self.assertEqual(errors, [])
        script = "\n".join(calls[0][2::2])
        self.assertEqual(timeouts, [3.0])
        self.assertIn("set windowRows to {}", script)
        self.assertIn("set end of windowRows to rowText", script)
        self.assertNotIn("set rows to {}", script)

    def test_running_app_candidates_use_system_events_when_appkit_unavailable(self) -> None:
        calls = []
        timeouts = []

        def runner(argv, **kwargs):
            calls.append(list(argv))
            timeouts.append(kwargs.get("timeout"))
            self.assertEqual(argv[0], "osascript")
            self.assertIn("application processes whose background only is false", "\n".join(argv[2::2]))
            return main.subprocess.CompletedProcess(
                args=argv,
                returncode=0,
                stdout=(
                    "Finder\tcom.apple.finder\t401\ttrue\n"
                    "logd\t\t2\tfalse\n"
                    "systemstats\t\t3\tfalse\n"
                    "Codex\tcom.openai.codex\t777\tfalse\n"
                    ".hidden\t\t8\tfalse\n"
                ),
                stderr="",
            )

        with mock.patch.dict("sys.modules", {"AppKit": None}):
            candidates, errors = main.enumerate_active_vision_running_app_candidates(
                platform_name="darwin",
                runner=runner,
                include_errors=True,
            )

        self.assertTrue(any("AppKit" in item for item in errors))
        self.assertEqual(timeouts, [2.5])
        self.assertEqual([item["app"] for item in candidates], ["Finder", "Codex"])
        self.assertEqual({item["source"] for item in candidates}, {"running_app"})
        self.assertTrue(all(item["focusable"] for item in candidates))
        self.assertFalse(any(item["source"] == "running_process" for item in candidates))

    def test_dock_item_candidates_parse_accessibility_positions(self) -> None:
        timeouts = []

        def runner(argv, **kwargs):
            timeouts.append(kwargs.get("timeout"))
            return main.subprocess.CompletedProcess(
                args=argv,
                returncode=0,
                stdout=(
                    "Steam\t878\t872\t57\t73\n"
                    "微信\t935\t872\t57\t73\n"
                    "QQ\t992\t872\t57\t73\n"
                ),
                stderr="",
            )

        candidates, errors = main.enumerate_macos_dock_item_candidates(
            platform_name="darwin",
            runner=runner,
            include_errors=True,
        )

        self.assertEqual(errors, [])
        self.assertEqual(timeouts, [3.0])
        wechat = next(item for item in candidates if item["app"] == "微信")
        self.assertEqual(wechat["source"], "dock_item")
        self.assertEqual(wechat["bounds"], {"x": 935, "y": 872, "width": 57, "height": 73})
        self.assertEqual(wechat["focus_point"], {"x": 963, "y": 908})
        self.assertTrue(wechat["focusable"])

    def test_active_vision_discovery_boosts_matching_dock_item_from_target_hint(self) -> None:
        discovery = main.discover_active_vision_target_candidates(
            desktop_targets_provider=lambda **kwargs: [],
            dock_items_provider=lambda **kwargs: [
                {"target_id": "dock:steam", "source": "dock_item", "app": "Steam", "score": 88},
                {
                    "target_id": "dock:wechat",
                    "source": "dock_item",
                    "app": "微信",
                    "focus_point": {"x": 963, "y": 908},
                    "score": 88,
                },
                {"target_id": "dock:qq", "source": "dock_item", "app": "QQ", "score": 88},
            ],
            running_apps_provider=lambda **kwargs: [],
            target_hint="打开微信",
            platform_name="darwin",
        )

        self.assertEqual(discovery["target_candidates"][0]["app"], "微信")
        self.assertEqual(discovery["target_candidates"][0]["source"], "dock_item")
        self.assertEqual(discovery["target_candidates"][0]["reason"], "target_hint_match")

    def test_active_vision_discovery_keeps_matching_dock_item_beyond_context_cap(self) -> None:
        dock_items = [
            {"target_id": f"dock:other-{index}", "source": "dock_item", "app": f"App {index}", "score": 88}
            for index in range(main.ACTIVE_VISION_MAX_CANDIDATES + 3)
        ]
        dock_items.append(
            {
                "target_id": "dock:wechat",
                "source": "dock_item",
                "app": "微信",
                "focus_point": {"x": 963, "y": 908},
                "score": 88,
            }
        )

        discovery = main.discover_active_vision_target_candidates(
            desktop_targets_provider=lambda **kwargs: [],
            dock_items_provider=lambda **kwargs: dock_items,
            running_apps_provider=lambda **kwargs: [],
            target_hint="打开微信并根据张三聊天信息回复张三",
            platform_name="darwin",
        )

        self.assertLessEqual(len(discovery["target_candidates"]), main.ACTIVE_VISION_MAX_CANDIDATES)
        self.assertEqual(discovery["target_candidates"][0]["app"], "微信")
        self.assertEqual(discovery["target_candidates"][0]["reason"], "target_hint_match")

    def test_running_app_candidates_extract_gui_apps_from_ps_fallback_when_system_events_fails(self) -> None:
        calls = []

        def runner(argv, **kwargs):
            calls.append(list(argv))
            if argv[0] == "osascript":
                return main.subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="not allowed")
            if argv[:3] == ["/bin/ps", "-axo", "comm="]:
                return main.subprocess.CompletedProcess(
                    args=argv,
                    returncode=0,
                    stdout=(
                        "/usr/sbin/logd\n"
                        "/System/Library/PrivateFrameworks/com.apple.MediaKit.framework/Versions/A/mediaagent\n"
                        "/Applications/StudyBrowser.app/Contents/MacOS/StudyBrowser Helper\n"
                        "/Applications/MediaBox.app/Contents/MacOS/MediaBox\n"
                        "/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder\n"
                        "/Applications/StudyBrowser.app/Contents/Frameworks/GPU Helper\n"
                    ),
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {argv}")

        with mock.patch.dict("sys.modules", {"AppKit": None}):
            candidates, errors = main.enumerate_active_vision_running_app_candidates(
                platform_name="darwin",
                runner=runner,
                include_errors=True,
            )

        self.assertTrue(any("System Events running app fallback failed" in item for item in errors))
        self.assertEqual([item["source"] for item in candidates[:2]], ["running_app", "running_app"])
        self.assertEqual([item["app"] for item in candidates[:2]], ["StudyBrowser", "MediaBox"])
        self.assertTrue(all(item["focusable"] for item in candidates[:2]))
        self.assertTrue(candidates[0]["target_id"].startswith("running_app:"))
        self.assertIn("Finder", [item["app"] for item in candidates])
        finder = next(item for item in candidates if item["app"] == "Finder")
        self.assertEqual(finder["source"], "running_app")
        self.assertTrue(finder["focusable"])
        self.assertFalse(any(item["app"] == "logd" for item in candidates))
        self.assertFalse(any(item["app"] == "com" for item in candidates))
        self.assertEqual(calls[0][0], "osascript")
        self.assertEqual(calls[1][:3], ["/bin/ps", "-axo", "comm="])

    def test_running_app_ps_fallback_filters_private_system_app_bundles(self) -> None:
        def runner(argv, **kwargs):
            if argv[0] == "osascript":
                return main.subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="-10827")
            if argv[:3] == ["/bin/ps", "-axo", "comm="]:
                return main.subprocess.CompletedProcess(
                    args=argv,
                    returncode=0,
                    stdout=(
                        "/System/Library/PrivateFrameworks/SocialLayer.framework/Versions/A/Resources/sociallayerd.app/Contents/MacOS/sociallayerd\n"
                        "/System/Library/PrivateFrameworks/WeatherKit.framework/Versions/A/Resources/Weather.app/Contents/MacOS/Weather\n"
                        "/Applications/WeChat.app/Contents/MacOS/WeChat\n"
                        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n"
                    ),
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {argv}")

        with mock.patch.dict("sys.modules", {"AppKit": None}):
            candidates, errors = main.enumerate_active_vision_running_app_candidates(
                platform_name="darwin",
                runner=runner,
                include_errors=True,
            )

        self.assertTrue(any("System Events running app fallback failed" in item for item in errors))
        self.assertEqual([item["app"] for item in candidates], ["WeChat", "Google Chrome"])
        self.assertTrue(all(item["source"] == "running_app" for item in candidates))
        self.assertTrue(all(item["focusable"] for item in candidates))
        self.assertFalse(any(item["app"] in {"sociallayerd", "Weather"} for item in candidates))

    def test_running_app_ps_fallback_keeps_user_apps_after_many_system_apps(self) -> None:
        system_app_lines = "\n".join(
            f"/System/Applications/SystemApp{index}.app/Contents/MacOS/SystemApp{index}" for index in range(20)
        )

        def runner(argv, **kwargs):
            if argv[0] == "osascript":
                return main.subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="-10827")
            if argv[:3] == ["/bin/ps", "-axo", "comm="]:
                return main.subprocess.CompletedProcess(
                    args=argv,
                    returncode=0,
                    stdout=(
                        f"{system_app_lines}\n"
                        "/Applications/WeChat.app/Contents/MacOS/WeChat\n"
                        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n"
                    ),
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {argv}")

        with mock.patch.dict("sys.modules", {"AppKit": None}):
            candidates, errors = main.enumerate_active_vision_running_app_candidates(
                platform_name="darwin",
                runner=runner,
                include_errors=True,
            )

        self.assertTrue(any("System Events running app fallback failed" in item for item in errors))
        self.assertIn("WeChat", [item["app"] for item in candidates])
        self.assertIn("Google Chrome", [item["app"] for item in candidates])
        self.assertLess([item["app"] for item in candidates].index("WeChat"), 3)

    def test_active_vision_discovery_uses_gui_app_fallback_after_window_enumeration_10827(self) -> None:
        calls = []

        def runner(argv, **kwargs):
            calls.append(list(argv))
            if argv[0] == "osascript":
                return main.subprocess.CompletedProcess(
                    args=argv,
                    returncode=1,
                    stdout="",
                    stderr="System Events got an error: Application isn't running. (-10827)",
                )
            if argv[:3] == ["/bin/ps", "-axo", "comm="]:
                return main.subprocess.CompletedProcess(
                    args=argv,
                    returncode=0,
                    stdout=(
                        "/System/Library/PrivateFrameworks/SocialLayer.framework/Versions/A/Resources/sociallayerd.app/Contents/MacOS/sociallayerd\n"
                        "/System/Library/PrivateFrameworks/WeatherKit.framework/Versions/A/Resources/Weather.app/Contents/MacOS/Weather\n"
                        "/Applications/WeChat.app/Contents/MacOS/WeChat\n"
                        "/Applications/Codex.app/Contents/MacOS/Codex\n"
                        "/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper\n"
                    ),
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {argv}")

        with mock.patch.dict("sys.modules", {"AppKit": None}):
            discovery = main.discover_active_vision_target_candidates(platform_name="darwin", runner=runner)

        self.assertEqual([call[0] for call in calls], ["osascript", "osascript", "osascript", "/bin/ps"])
        self.assertTrue(any("-10827" in item for item in discovery["discovery_errors"]))
        running_apps = [item for item in discovery["target_candidates"] if item["source"] == "running_app"]
        self.assertEqual([item["app"] for item in running_apps], ["WeChat", "Google Chrome", "Codex"])
        self.assertTrue(all(item["focusable"] for item in running_apps))
        self.assertFalse(any(item["app"] in {"sociallayerd", "Weather"} for item in discovery["target_candidates"]))
        self.assertFalse(any(item["source"] == "running_process" for item in discovery["target_candidates"]))

    def test_running_app_focus_uses_safe_runner_fallback_without_appkit(self) -> None:
        calls = []

        def runner(argv, **kwargs):
            calls.append(list(argv))
            return main.subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with mock.patch.dict("sys.modules", {"AppKit": None}):
            trace = main.run_active_vision_light_interaction(
                mode="focus_target",
                target_id="running:codex",
                target_candidates=[
                    {
                        "target_id": "running:codex",
                        "source": "running_app",
                        "app": "Codex",
                        "title": "Codex",
                        "focusable": True,
                    }
                ],
                actions=["focus_target"],
                platform_name="darwin",
                runner=runner,
            )

        self.assertEqual(calls, [["/usr/bin/open", "-a", "Codex"]])
        self.assertEqual(trace["focus_result"], {"status": "success", "method": "activate_running_app", "reason": ""})
        self.assertEqual(trace["action_trace"][0]["action"], "activate_running_app")
        self.assertEqual(trace["action_trace"][0]["status"], "success")
        self.assertEqual(trace["actions"], ["focus_target"])

    def test_desktop_context_app_level_focus_uses_safe_activation_fallback(self) -> None:
        calls = []

        def runner(argv, **kwargs):
            calls.append(list(argv))
            return main.subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with mock.patch.dict("sys.modules", {"AppKit": None}):
            trace = main.run_active_vision_light_interaction(
                mode="focus_target",
                target_id="desktop-context:fluxdesk",
                target_candidates=[
                    {
                        "target_id": "desktop-context:fluxdesk",
                        "source": "desktop_context",
                        "app": "FluxDesk",
                        "title": "Weekly Problem List",
                        "focusable": True,
                    }
                ],
                actions=["focus_target"],
                platform_name="darwin",
                runner=runner,
            )

        self.assertEqual(calls, [["/usr/bin/open", "-a", "FluxDesk"]])
        self.assertEqual(trace["focus_result"], {"status": "success", "method": "activate_running_app", "reason": ""})
        self.assertEqual(trace["action_trace"][0]["action"], "activate_running_app")
        self.assertEqual(trace["action_trace"][0]["status"], "success")
        self.assertEqual(trace["actions"], ["focus_target"])

    def test_active_vision_desktop_survey_hides_pet_without_interaction_and_returns_targets(self) -> None:
        window = _FakeWindow()
        events = []

        def frame_encoder(screen, config, **kwargs):
            events.append("capture")
            return {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,active",
                "capture_backend": "unit-test",
            }

        payload = main.capture_active_vision_frame_payload(
            window,
            {"mode": "desktop_survey", "settle_ms": 1},
            {"enabled": True, "active_observation": {"enabled": True, "settle_ms": 1}},
            screen_provider=lambda: object(),
            frame_encoder=frame_encoder,
            observation_provider=lambda config, platform_name=None: {"desktop_context": {"foreground_app": "Finder"}},
            desktop_targets_provider=lambda **kwargs: [
                {"target_id": "target-safari", "app": "Safari", "title": "Video"}
            ],
            dock_items_provider=None,
            interaction_runner=lambda **kwargs: (_ for _ in ()).throw(AssertionError("survey must not interact")),
            sleeper=lambda seconds: events.append(f"sleep:{seconds}"),
        )

        self.assertEqual(window.calls[0], "hide")
        self.assertEqual(events[-1], "capture")
        self.assertEqual(window.calls[-1:], ["show"])
        self.assertNotIn("raise", window.calls)
        self.assertTrue(window.visible)
        self.assertEqual(payload["active_observation"]["mode"], "desktop_survey")
        self.assertEqual(payload["active_observation"]["status"], "success")
        self.assertEqual(payload["active_observation"]["desktop_targets"][0]["target_id"], "target-safari")
        self.assertEqual(payload["active_observation"]["action_trace"], [])
        self.assertNotIn("data_url", payload["active_observation"])

    def test_active_vision_application_capture_hides_and_restores_ipet(self) -> None:
        window = _FakeWindow()
        events = []

        with mock.patch(
            "body.active_vision._flush_qt_window_state",
            side_effect=lambda: events.append("process_events"),
        ):
            payload = main.capture_active_vision_frame_payload(
                window,
                {
                    "mode": "desktop_survey",
                    "target_app": "Google Chrome",
                    "accessibility_enabled": False,
                    "settle_ms": 1,
                },
                {"enabled": True, "active_observation": {"enabled": True, "settle_ms": 1}},
                platform_name="darwin",
                screen_provider=lambda: object(),
                frame_encoder=lambda screen, config, **kwargs: (
                    events.append("capture")
                    or {
                        "mime_type": "image/jpeg",
                        "data_url": "data:image/jpeg;base64,active",
                        "capture_backend": "unit-test",
                    }
                ),
                desktop_targets_provider=lambda **kwargs: [],
                dock_items_provider=None,
                running_apps_provider=lambda **kwargs: [],
                sleeper=lambda seconds: events.append(f"sleep:{seconds}"),
            )

        self.assertEqual(window.calls, ["hide", "show"])
        self.assertEqual(
            events,
            [
                "process_events",
                "sleep:0.001",
                "capture",
                "process_events",
            ],
        )
        self.assertNotIn("raise", window.calls)
        self.assertEqual(payload["active_observation"]["capture_scope"], "application")
        self.assertEqual(payload["active_observation"]["target_app"], "Google Chrome")

    def test_application_frame_crop_preserves_screen_coordinate_origin(self) -> None:
        copies = []

        class FakeImage:
            def width(self):
                return 1920

            def height(self):
                return 1249

            def isNull(self):
                return False

            def copy(self, x, y, width, height):
                copies.append((x, y, width, height))
                return object()

        with (
            mock.patch.object(
                body_active_vision,
                "_decode_frame_image",
                return_value=FakeImage(),
            ),
            mock.patch.object(
                body_active_vision._screen_capture,
                "_encode_pixmap_frame",
                return_value=(
                    "image/jpeg",
                    "data:image/jpeg;base64,cropped",
                    880,
                    640,
                ),
            ),
            mock.patch.object(
                body_active_vision._screen_capture,
                "_visual_hash_from_image_like",
                return_value="ahash:cropped",
            ),
        ):
            result = body_active_vision._crop_application_frame_payload(
                {
                    "mime_type": "image/jpeg",
                    "data_url": "data:image/jpeg;base64,full",
                    "frame_hash": "sha256:full",
                    "display_layout": [
                        {
                            "x": 0,
                            "y": 0,
                            "width": 1920,
                            "height": 1249,
                        }
                    ],
                },
                {"x": 15, "y": 125, "width": 880, "height": 640},
                {},
            )

        self.assertEqual(copies, [(15, 125, 880, 640)])
        self.assertEqual(result["data_url"], "data:image/jpeg;base64,cropped")
        self.assertEqual(result["capture_scope"], "application")
        self.assertEqual(result["source_capture_scope"], "")
        self.assertEqual(result["capture_region"], "target_application_window")
        self.assertEqual(
            result["display_layout"],
            [
                {
                    "x": 15,
                    "y": 125,
                    "width": 880,
                    "height": 640,
                    "name": "target_application_window",
                    "device_pixel_ratio": 1.0,
                }
            ],
        )
        self.assertEqual(result["image_width"], 880)
        self.assertEqual(result["image_height"], 640)

    def test_active_vision_survey_records_discovery_error_and_fallback_candidates(self) -> None:
        window = _FakeWindow()

        def frame_encoder(screen, config, **kwargs):
            return {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,active",
                "capture_backend": "unit-test",
            }

        payload = main.capture_active_vision_frame_payload(
            window,
            {"mode": "desktop_survey", "settle_ms": 1},
            {"enabled": True, "active_observation": {"enabled": True, "settle_ms": 1}},
            screen_provider=lambda: object(),
            frame_encoder=frame_encoder,
            observation_provider=lambda config, platform_name=None: {"desktop_context": {"foreground_app": "Finder"}},
            desktop_targets_provider=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("System Events failed: -10827")),
            dock_items_provider=None,
            running_apps_provider=lambda **kwargs: [
                {"target_id": "running:safari", "source": "running_app", "app": "Safari", "focusable": True}
            ],
            interaction_runner=lambda **kwargs: (_ for _ in ()).throw(AssertionError("survey must not interact")),
            sleeper=lambda seconds: None,
        )

        active = payload["active_observation"]
        self.assertEqual(active["desktop_targets"], [])
        self.assertIn("System Events failed: -10827", active["discovery_errors"][0])
        self.assertEqual([item["source"] for item in active["target_candidates"]], ["running_app", "screenshot_region"])
        self.assertEqual(active["target_candidates"][0]["target_id"], "running:safari")

    def test_active_vision_uses_high_quality_capture_config(self) -> None:
        window = _FakeWindow()
        seen_configs = []

        def frame_encoder(screen, config, **kwargs):
            seen_configs.append(dict(config))
            return {
                "mime_type": "image/jpeg",
                "data_url": "data:image/jpeg;base64,active",
                "capture_backend": "unit-test",
            }

        main.capture_active_vision_frame_payload(
            window,
            {"mode": "desktop_survey", "settle_ms": 1},
            {
                "enabled": True,
                "max_width": 320,
                "jpeg_quality": 35,
                "active_observation": {"enabled": True, "settle_ms": 1},
            },
            screen_provider=lambda: object(),
            frame_encoder=frame_encoder,
            observation_provider=lambda config, platform_name=None: {},
            desktop_targets_provider=lambda **kwargs: [],
            dock_items_provider=None,
            running_apps_provider=lambda **kwargs: [],
            sleeper=lambda seconds: None,
        )

        self.assertGreaterEqual(seen_configs[0]["max_width"], 1920)
        self.assertGreaterEqual(seen_configs[0]["jpeg_quality"], 88)

    def test_desktop_command_active_vision_capture_returns_frame_response(self) -> None:
        host = _DesktopCommandHost()
        command = {
            "nonce": "active-test",
            "type": "active_vision_capture",
            "payload": {"mode": "desktop_survey"},
        }
        frame = {"mime_type": "image/jpeg", "data_url": "data:image/jpeg;base64,active"}

        with mock.patch.object(main, "capture_active_vision_frame_payload", return_value=frame) as capture_mock:
            main.DesktopPet.process_desktop_command(host, command)

        capture_mock.assert_called_once()
        self.assertEqual(host.responses[0][1], "success")
        self.assertEqual(host.responses[0][2]["frame"], frame)

    def test_execute_human_ops_click_invokes_macos_system_events(self) -> None:
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return main.subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        result = main.execute_human_ops_click(
            {"x": "12", "y": "34", "label": "发送按钮"},
            platform_name="darwin",
            runner=runner,
            event_clicker=None,
        )

        self.assertEqual(result, {"clicked": True, "x": 12, "y": 34, "label": "发送按钮", "method": "system_events"})
        self.assertEqual(calls[0][0][:2], ["osascript", "-e"])
        self.assertIn("click at {12, 34}", calls[0][0][-1])
        self.assertTrue(calls[0][1]["capture_output"])

    def test_execute_human_ops_click_prefers_core_graphics_event(self) -> None:
        calls = []

        def runner(args, **kwargs):
            calls.append(("system_events", args, kwargs))
            return main.subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        def event_clicker(x, y):
            calls.append(("core_graphics", x, y))

        result = main.execute_human_ops_click(
            {"x": "12", "y": "34", "label": "发送按钮"},
            platform_name="darwin",
            runner=runner,
            event_clicker=event_clicker,
        )

        self.assertEqual(result, {"clicked": True, "x": 12, "y": 34, "label": "发送按钮", "method": "core_graphics"})
        self.assertEqual(calls, [("core_graphics", 12, 34)])

    def test_execute_human_ops_click_falls_back_to_system_events_when_core_graphics_fails(self) -> None:
        calls = []

        def runner(args, **kwargs):
            calls.append(("system_events", args, kwargs))
            return main.subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        def event_clicker(x, y):
            calls.append(("core_graphics", x, y))
            raise RuntimeError("CoreGraphics unavailable")

        result = main.execute_human_ops_click(
            {"x": "12", "y": "34", "label": "发送按钮"},
            platform_name="darwin",
            runner=runner,
            event_clicker=event_clicker,
        )

        self.assertEqual(result, {"clicked": True, "x": 12, "y": 34, "label": "发送按钮", "method": "system_events"})
        self.assertEqual(calls[0], ("core_graphics", 12, 34))
        self.assertEqual(calls[1][0], "system_events")
        self.assertIn("click at {12, 34}", calls[1][1][-1])

    def test_execute_human_ops_type_text_uses_core_graphics_unicode(self) -> None:
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return main.subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with mock.patch("human_ops.desktop_actions._post_core_graphics_text") as event_typer:
            result = main.execute_human_ops_type_text(
                {"text": "https://example.com", "label": "浏览器地址栏"},
                platform_name="darwin",
                runner=runner,
            )

        self.assertEqual(
            result,
            {"typed": True, "text": "https://example.com", "label": "浏览器地址栏", "method": "core_graphics_unicode"},
        )
        event_typer.assert_called_once_with("https://example.com")
        self.assertEqual(calls, [])

    def test_execute_human_ops_key_press_enter_uses_key_code_36(self) -> None:
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return main.subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        result = main.execute_human_ops_key_press(
            {"key": "enter", "label": "发送消息"},
            platform_name="darwin",
            runner=runner,
        )

        self.assertEqual(result, {"pressed": True, "key": "enter", "label": "发送消息", "method": "system_events"})
        self.assertEqual(calls[0][0][:2], ["osascript", "-e"])
        self.assertIn("key code 36", calls[0][0][-1])

    def test_desktop_command_human_ops_click_returns_response(self) -> None:
        host = _DesktopCommandHost()
        command = {
            "nonce": "click-test",
            "type": "human_ops_click",
            "payload": {"target_app": "WeChat", "x": 12, "y": 34, "label": "发送按钮"},
        }
        click_result = {"clicked": True, "x": 12, "y": 34, "label": "发送按钮"}
        focus_result = {"focused": True, "target_app": "WeChat", "frontmost_app": "WeChat"}

        with mock.patch.object(main, "focus_macos_application", return_value=focus_result) as focus_mock, mock.patch.object(
            main, "execute_human_ops_click", return_value=click_result
        ) as click_mock:
            main.DesktopPet.process_desktop_command(host, command)

        focus_mock.assert_called_once_with(command["payload"])
        click_mock.assert_called_once_with(command["payload"])
        self.assertEqual(host.responses[0][1], "success")
        self.assertEqual(host.responses[0][2], {**focus_result, **click_result})

    def test_desktop_command_human_ops_type_text_returns_response(self) -> None:
        host = _DesktopCommandHost()
        command = {
            "nonce": "type-test",
            "type": "human_ops_type_text",
            "payload": {"target_app": "WeChat", "text": "收到，我马上处理。", "label": "微信聊天输入框"},
        }
        type_result = {"typed": True, "text": "收到，我马上处理。", "label": "微信聊天输入框"}
        focus_result = {"focused": True, "target_app": "WeChat", "frontmost_app": "WeChat"}

        with mock.patch.object(main, "focus_macos_application", return_value=focus_result) as focus_mock, mock.patch.object(
            main, "execute_human_ops_type_text", return_value=type_result
        ) as type_mock:
            main.DesktopPet.process_desktop_command(host, command)

        focus_mock.assert_called_once_with(command["payload"])
        type_mock.assert_called_once_with(command["payload"])
        self.assertEqual(host.responses[0][1], "success")
        self.assertEqual(host.responses[0][2], {**focus_result, **type_result})

    def test_desktop_command_human_ops_key_press_returns_response(self) -> None:
        host = _DesktopCommandHost()
        command = {
            "nonce": "enter-test",
            "type": "human_ops_key_press",
            "payload": {"target_app": "WeChat", "key": "enter", "label": "发送消息"},
        }
        key_result = {"pressed": True, "key": "enter", "label": "发送消息"}
        focus_result = {"focused": True, "target_app": "WeChat", "frontmost_app": "WeChat"}

        with mock.patch.object(main, "focus_macos_application", return_value=focus_result) as focus_mock, mock.patch.object(
            main, "execute_human_ops_key_press", return_value=key_result
        ) as key_mock:
            main.DesktopPet.process_desktop_command(host, command)

        focus_mock.assert_called_once_with(command["payload"])
        key_mock.assert_called_once_with(command["payload"])
        self.assertEqual(host.responses[0][1], "success")
        self.assertEqual(host.responses[0][2], {**focus_result, **key_result})

    def test_desktop_command_human_ops_click_keeps_pet_window_state_unchanged(self) -> None:
        host = _DesktopCommandHost()
        command = {
            "nonce": "click-through-window-test",
            "type": "human_ops_click",
            "payload": {"target_app": "Finder", "x": 120, "y": 240, "label": "桌面目标"},
        }

        def click_side_effect(payload):
            self.assertTrue(host.visible)
            return {"clicked": True, "x": payload["x"], "y": payload["y"], "label": payload["label"]}

        with mock.patch.object(
            main,
            "focus_macos_application",
            return_value={"focused": True, "target_app": "Finder", "frontmost_app": "Finder"},
        ), mock.patch.object(main, "execute_human_ops_click", side_effect=click_side_effect):
            main.DesktopPet.process_desktop_command(host, command)

        self.assertNotIn("hide", host.calls)
        self.assertNotIn("show", host.calls)
        self.assertNotIn("raise", host.calls)
        self.assertTrue(host.visible)
        self.assertEqual(host.responses[0][1], "success")

    def test_qt_capture_payload_uses_all_display_composer(self) -> None:
        screens = [object(), object()]
        composer = mock.Mock(return_value=("canvas", {"display_count": 2, "display_layout": [{"x": 0}, {"x": 100}]}))
        encoder = mock.Mock(return_value=("image/jpeg", "data:image/jpeg;base64,qt"))

        payload = main.capture_qt_screen_frame_payload(
            object(),
            {"max_width": 1280, "jpeg_quality": 75},
            screens_provider=lambda: screens,
            pixmap_composer=composer,
            pixmap_encoder=encoder,
        )

        self.assertEqual(payload["capture_backend"], "qt_fallback")
        self.assertEqual(payload["capture_scope"], "visible_spaces_all_displays")
        self.assertEqual(payload["display_count"], 2)
        self.assertEqual(payload["display_layout"], [{"x": 0}, {"x": 100}])
        composer.assert_called_once_with(screens)
        encoder.assert_called_once_with("canvas", {"max_width": 1280, "jpeg_quality": 75})

    def test_qt_capture_payload_records_encoded_image_dimensions(self) -> None:
        screens = [object()]
        composer = mock.Mock(
            return_value=(
                "canvas",
                {"display_count": 1, "display_layout": [{"x": 0, "y": 0, "width": 1728, "height": 1117}]},
            )
        )
        encoder = mock.Mock(return_value=("image/jpeg", "data:image/jpeg;base64,scaled", 3456, 2234))

        payload = main.capture_qt_screen_frame_payload(
            object(),
            {"max_width": 3456, "jpeg_quality": 88},
            screens_provider=lambda: screens,
            pixmap_composer=composer,
            pixmap_encoder=encoder,
        )

        self.assertEqual(payload["image_width"], 3456)
        self.assertEqual(payload["image_height"], 2234)

    def test_click_preview_overlay_uses_tooltip_level_and_paints_dot(self) -> None:
        source = inspect.getsource(main.ClickPreviewOverlay)

        self.assertIs(main.normalize_click_preview_payload, click_preview.normalize_click_preview_payload)
        self.assertEqual(main.ClickPreviewOverlay.__module__, click_preview.__name__)
        self.assertIn("class ClickPreviewOverlay(widget_base):", source)
        self.assertIn("qt.WindowType.ToolTip", source)
        self.assertIn("def paintEvent", source)
        self.assertIn("drawEllipse", source)

    def test_collect_screen_observations_reads_macos_menu_bar_as_metadata_only(self) -> None:
        result = main.subprocess.CompletedProcess(
            args=["osascript"],
            returncode=0,
            stdout="QQ\nApple, QQ, 编辑, 窗口, 帮助\n",
            stderr="",
        )

        payload = main.collect_screen_observations(
            {"enabled": True, "include_ui_metadata": True},
            platform_name="darwin",
            runner=lambda *args, **kwargs: result,
        )

        self.assertEqual(payload["desktop_context"]["foreground_app"], "QQ")
        self.assertEqual(payload["desktop_context"]["frontmost_process"], "QQ")
        self.assertEqual(payload["desktop_context"]["menu_bar_items"], ["Apple", "QQ", "编辑", "窗口", "帮助"])
        self.assertNotIn("summary", payload)
        self.assertNotIn("observations", payload)

    def test_collect_screen_observations_skips_when_disabled_or_non_macos(self) -> None:
        runner = mock.Mock()

        self.assertEqual(
            main.collect_screen_observations({"include_ui_metadata": False}, platform_name="darwin", runner=runner),
            {},
        )
        self.assertEqual(main.collect_screen_observations({"include_ui_metadata": True}, platform_name="win32", runner=runner), {})
        runner.assert_not_called()

    def test_removed_sidecar_helpers_are_not_exported(self) -> None:
        removed_names = [
            "_" + "her" + "mes_" + "missing_command_message",
            "_" + "runtime_" + "missing_command_message",
            "is_" + "her" + "mes_" + "healthy",
            "is_" + "runtime_" + "healthy",
        ]
        for name in removed_names:
            self.assertFalse(hasattr(main, name) or hasattr(main.DesktopPet, name))
