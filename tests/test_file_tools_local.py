from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from backend.mcp.local_server import LocalMCPServer
from backend.tooling.file_tools import FileTools
from backend.tooling.security import SecurityError, SecurityPolicy, normalize_file_allowlist


def _install_qt_stubs() -> None:
    if "PySide6" in sys.modules or "PyQt6" in sys.modules:
        return

    class _QtType:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __call__(self, *args, **kwargs):
            return None

    class _QtProxy:
        def __getattr__(self, _name: str):
            return 0

    def _signal(*_args, **_kwargs):
        return object()

    def _slot(*_args, **_kwargs):
        def decorator(func):
            return func

        return decorator

    qtcore = types.ModuleType("PySide6.QtCore")
    qtcore.QObject = type("QObject", (), {})
    qtcore.QPoint = type("QPoint", (), {})
    qtcore.Qt = _QtProxy()
    qtcore.QEvent = _QtProxy()
    qtcore.QSignalBlocker = type("QSignalBlocker", (), {})
    qtcore.QTimer = type("QTimer", (), {})
    qtcore.QUrl = type("QUrl", (), {})
    qtcore.Signal = _signal
    qtcore.Slot = _slot

    qtgui = types.ModuleType("PySide6.QtGui")
    qtgui.QAction = type("QAction", (), {})
    qtgui.QColor = type("QColor", (), {})
    qtgui.QGuiApplication = type("QGuiApplication", (), {})

    qtwebchannel = types.ModuleType("PySide6.QtWebChannel")
    qtwebchannel.QWebChannel = type("QWebChannel", (), {})

    qtwebenginecore = types.ModuleType("PySide6.QtWebEngineCore")
    qtwebenginecore.QWebEnginePage = type("QWebEnginePage", (), {})
    qtwebenginecore.QWebEngineSettings = type("QWebEngineSettings", (), {})

    qtwebenginewidgets = types.ModuleType("PySide6.QtWebEngineWidgets")
    qtwebenginewidgets.QWebEngineView = type("QWebEngineView", (), {})

    qtwidgets = types.ModuleType("PySide6.QtWidgets")
    for name in [
        "QApplication",
        "QCheckBox",
        "QComboBox",
        "QDoubleSpinBox",
        "QFileDialog",
        "QFormLayout",
        "QGridLayout",
        "QGroupBox",
        "QHBoxLayout",
        "QLabel",
        "QLineEdit",
        "QInputDialog",
        "QMainWindow",
        "QMenu",
        "QPlainTextEdit",
        "QPushButton",
        "QSlider",
        "QSpinBox",
        "QVBoxLayout",
        "QWidget",
    ]:
        setattr(qtwidgets, name, type(name, (), {}))

    pyside6 = types.ModuleType("PySide6")
    pyside6.QtCore = qtcore
    pyside6.QtGui = qtgui
    pyside6.QtWebChannel = qtwebchannel
    pyside6.QtWebEngineCore = qtwebenginecore
    pyside6.QtWebEngineWidgets = qtwebenginewidgets
    pyside6.QtWidgets = qtwidgets

    sys.modules["PySide6"] = pyside6
    sys.modules["PySide6.QtCore"] = qtcore
    sys.modules["PySide6.QtGui"] = qtgui
    sys.modules["PySide6.QtWebChannel"] = qtwebchannel
    sys.modules["PySide6.QtWebEngineCore"] = qtwebenginecore
    sys.modules["PySide6.QtWebEngineWidgets"] = qtwebenginewidgets
    sys.modules["PySide6.QtWidgets"] = qtwidgets


_install_qt_stubs()
main = importlib.import_module("main")


class _FakePet:
    def __init__(self) -> None:
        self.responses: list[tuple[str, dict[str, object]]] = []

    def _write_runtime_command_response(self, _command, status: str, result: dict[str, object] | None = None) -> None:
        self.responses.append((status, result or {}))

    def _write_runtime_host_heartbeat(self) -> None:
        return None

    def raise_(self) -> None:
        return None

    def activateWindow(self) -> None:
        return None


class _FakeParent:
    def __init__(self) -> None:
        self.mkdir_calls: list[tuple[bool, bool]] = []

    def mkdir(self, *, parents: bool = False, exist_ok: bool = False) -> None:
        self.mkdir_calls.append((parents, exist_ok))


class _FakePath:
    def __init__(self, path_text: str, *, exists: bool = True, is_dir: bool = False, size: int = 0) -> None:
        self.path_text = path_text
        self._exists = exists
        self._is_dir = is_dir
        self._size = size
        self.parent = _FakeParent()
        self.replaced_to = None
        self.unlinked = False

    def exists(self) -> bool:
        return self._exists

    def is_dir(self) -> bool:
        return self._is_dir

    def is_file(self) -> bool:
        return self._exists and not self._is_dir

    def stat(self):
        return types.SimpleNamespace(st_size=self._size, st_mtime=1234)

    def replace(self, other) -> None:
        self.replaced_to = other
        self._exists = False

    def unlink(self) -> None:
        self.unlinked = True
        self._exists = False

    def __str__(self) -> str:
        return self.path_text


class FileToolSecurityTests(unittest.TestCase):
    def test_normalize_file_allowlist_dedupes_and_falls_back(self) -> None:
        root = str(main.ROOT_DIR)
        normalized = normalize_file_allowlist([root, "", root], default_paths=["ignored"])
        self.assertEqual(normalized, [main.ROOT_DIR.resolve()])
        fallback = normalize_file_allowlist([], default_paths=[str(main.ROOT_DIR)])
        self.assertEqual(fallback, [main.ROOT_DIR.resolve()])

    def test_security_policy_allows_multiple_directories_and_relative_paths(self) -> None:
        project_root = main.ROOT_DIR.resolve()
        backend_root = (project_root / "backend").resolve()
        policy = SecurityPolicy([str(project_root), str(backend_root)], [])

        resolved_relative = policy.ensure_file_path("agent_graph.py")
        self.assertEqual(resolved_relative, (backend_root / "agent_graph.py").resolve())

        resolved_absolute = policy.ensure_file_path(str(project_root / "settings.js"))
        self.assertEqual(resolved_absolute, (project_root / "settings.js").resolve())

        with self.assertRaises(SecurityError):
            policy.ensure_file_path(str(project_root.parent / "outside.txt"))


class FileToolsTests(unittest.TestCase):
    def test_extended_file_tools_read_only_behaviors(self) -> None:
        root_path = main.ROOT_DIR.resolve()
        backend_dir = root_path / "backend"
        tools = FileTools(SecurityPolicy([str(root_path)], []))

        listing = tools.list_dir(str(backend_dir), recursive=False)
        self.assertTrue(any(item["name"] == "app.py" and item["type"] == "file" for item in listing["entries"]))

        search = tools.search_files(str(backend_dir), pattern="*.py", recursive=True, max_entries=50)
        self.assertGreater(search["count"], 0)
        self.assertTrue(any(item["name"] == "agent_graph.py" for item in search["entries"]))

        stat = tools.stat_path(str(root_path / "settings.js"))
        self.assertEqual(stat["entry"]["type"], "file")
        self.assertGreater(stat["entry"]["size"], 0)

    def test_copy_move_delete_use_structured_results(self) -> None:
        policy = mock.Mock(spec=SecurityPolicy)
        tools = FileTools(policy)

        src = _FakePath("src.txt", exists=True, is_dir=False, size=12)
        dst = _FakePath("dst.txt", exists=False, is_dir=False, size=12)
        policy.ensure_file_path.side_effect = [src, dst]
        with mock.patch("backend.tooling.file_tools.shutil.copy2") as copy2_mock:
            copied = tools.copy_file("src.txt", "dst.txt")
        copy2_mock.assert_called_once_with(src, dst)
        self.assertTrue(copied["copied"])
        self.assertEqual(copied["bytes_copied"], 12)
        self.assertEqual(dst.parent.mkdir_calls, [(True, True)])

        moved_src = _FakePath("from.txt", exists=True, is_dir=False, size=8)
        moved_dst = _FakePath("to.txt", exists=False, is_dir=False, size=8)
        policy.ensure_file_path.side_effect = [moved_src, moved_dst]
        moved = tools.move_file("from.txt", "to.txt")
        self.assertTrue(moved["moved"])
        self.assertIs(moved_src.replaced_to, moved_dst)
        self.assertEqual(moved_dst.parent.mkdir_calls, [(True, True)])

        delete_target = _FakePath("delete.txt", exists=True, is_dir=False, size=5)
        policy.ensure_file_path.side_effect = [delete_target]
        deleted = tools.delete_file("delete.txt")
        self.assertTrue(deleted["deleted"])
        self.assertTrue(delete_target.unlinked)

        delete_dir = _FakePath("folder", exists=True, is_dir=True)
        policy.ensure_file_path.side_effect = [delete_dir]
        with self.assertRaises(IsADirectoryError):
            tools.delete_file("folder")


class LocalMCPServerTests(unittest.TestCase):
    def test_local_server_exposes_extended_file_tools(self) -> None:
        server = LocalMCPServer(file_allowlist=[str(main.ROOT_DIR)])
        names = {item["function"]["name"] for item in server.list_tools()}
        self.assertTrue({"delete_file", "copy_file", "stat_path", "search_files"}.issubset(names))


class BackendLaunchSelectionTests(unittest.TestCase):
    def test_qt_runtime_env_defaults_keep_windows_gpu_workarounds(self) -> None:
        defaults = main._qt_runtime_env_defaults("win32")

        self.assertEqual(defaults["QTWEBENGINE_DISABLE_SANDBOX"], "1")
        self.assertEqual(defaults["QT_OPENGL"], "software")
        self.assertIn("--use-gl=angle", defaults["QTWEBENGINE_CHROMIUM_FLAGS"])
        self.assertIn("--disable-direct-composition", defaults["QTWEBENGINE_CHROMIUM_FLAGS"])

    def test_qt_runtime_env_defaults_keep_macos_conservative(self) -> None:
        defaults = main._qt_runtime_env_defaults("darwin")

        self.assertEqual(defaults["QTWEBENGINE_DISABLE_SANDBOX"], "1")
        self.assertNotIn("QT_OPENGL", defaults)
        self.assertIn("--enable-webgl", defaults["QTWEBENGINE_CHROMIUM_FLAGS"])
        self.assertIn("--ignore-gpu-blocklist", defaults["QTWEBENGINE_CHROMIUM_FLAGS"])

    def test_default_asr_config_disables_macos_by_default(self) -> None:
        self.assertFalse(main._default_asr_config("darwin")["enabled"])
        self.assertTrue(main._default_asr_config("win32")["enabled"])

    def test_is_backend_healthy_requires_delete_route_support(self) -> None:
        health_resp = mock.Mock(status_code=200)
        openapi_resp = mock.Mock(status_code=200)
        openapi_resp.json.return_value = {
            "paths": {
                "/api/chat/topics": {"get": {}, "post": {}},
                "/api/chat/topics/{topic_id}": {"get": {}},
            }
        }
        with mock.patch.object(main.requests, "get", side_effect=[health_resp, openapi_resp]):
            self.assertFalse(main.is_backend_healthy("http://127.0.0.1:8008"))

    def test_pick_backend_launch_url_uses_next_free_local_port(self) -> None:
        with mock.patch.object(main, "is_service_port_available", side_effect=[False, True]):
            self.assertEqual(main.pick_backend_launch_url("http://127.0.0.1:8008"), "http://127.0.0.1:8009")

    def test_ensure_backend_service_switches_away_from_stale_local_backend(self) -> None:
        fake = types.SimpleNamespace(
            config={"chat": {"backend_url": "http://127.0.0.1:8008"}},
            backend_process=None,
            backend_started_by_app=False,
        )
        proc = mock.Mock()
        with mock.patch.object(main, "is_backend_healthy", side_effect=[False, False]):
            with mock.patch.object(main, "is_backend_live", return_value=True):
                with mock.patch.object(main, "pick_backend_launch_url", return_value="http://127.0.0.1:8009"):
                    with mock.patch.object(main.subprocess, "Popen", return_value=proc) as popen_mock:
                        with mock.patch.object(main.time, "sleep", return_value=None):
                            main.DesktopPet.ensure_backend_service(fake)
        self.assertEqual(fake.config["chat"]["backend_url"], "http://127.0.0.1:8009")
        self.assertIs(fake.backend_process, proc)
        self.assertTrue(fake.backend_started_by_app)
        cmd = popen_mock.call_args.args[0]
        self.assertIn("--port", cmd)
        self.assertIn("8009", cmd)

    def test_ensure_asr_service_reuses_local_backend_url(self) -> None:
        fake = types.SimpleNamespace(
            config={"chat": {"backend_url": "http://127.0.0.1:8009", "asr": {"enabled": True, "api_base_url": "http://127.0.0.1:8012"}}},
            asr_process=None,
            asr_started_by_app=False,
        )

        with mock.patch.object(main.subprocess, "Popen") as popen_mock:
            main.DesktopPet.ensure_asr_service(fake)

        self.assertEqual(fake.config["chat"]["asr"]["api_base_url"], "http://127.0.0.1:8009")
        popen_mock.assert_not_called()

    def test_request_asr_warmup_posts_to_backend_and_starts_monitor(self) -> None:
        fake = types.SimpleNamespace(
            config={"chat": {"backend_url": "http://127.0.0.1:8009", "asr": {"enabled": True, "api_base_url": "http://127.0.0.1:8009"}}},
            ensure_backend_service=mock.Mock(),
            ensure_asr_service=mock.Mock(),
            _start_asr_warmup_progress_monitor=mock.Mock(),
        )
        response = mock.Mock()
        response.headers = {"content-type": "application/json"}
        response.json.return_value = {"ok": True, "started": True, "ready": False, "message": "ASR 正在加载模型，请稍后再试。"}

        with mock.patch.object(main.requests, "post", return_value=response):
            main.DesktopPet.request_asr_warmup(fake)

        fake.ensure_backend_service.assert_called_once()
        fake.ensure_asr_service.assert_called_once()
        fake._start_asr_warmup_progress_monitor.assert_called_once_with("http://127.0.0.1:8009")



class MainRuntimeCommandTests(unittest.TestCase):
    def test_pick_directory_runtime_command_writes_success_response(self) -> None:
        fake = _FakePet()
        with mock.patch.object(main.QFileDialog, "getExistingDirectory", return_value=str(main.ROOT_DIR), create=True):
            main.DesktopPet.process_runtime_command(
                fake,
                {
                    "nonce": "nonce-1",
                    "type": "pick_directory",
                    "payload": {"start_dir": str(main.ROOT_DIR)},
                },
            )
        self.assertEqual(fake.responses, [("success", {"directory": str(main.ROOT_DIR), "start_dir": str(main.ROOT_DIR)})])

    def test_pick_directory_runtime_command_writes_cancelled_response(self) -> None:
        fake = _FakePet()
        with mock.patch.object(main.QFileDialog, "getExistingDirectory", return_value="", create=True):
            main.DesktopPet.process_runtime_command(
                fake,
                {
                    "nonce": "nonce-2",
                    "type": "pick_directory",
                    "payload": {"start_dir": str(main.ROOT_DIR)},
                },
            )
        self.assertEqual(fake.responses, [("cancelled", {"directory": "", "start_dir": str(main.ROOT_DIR)})])

    def test_pick_image_file_runtime_command_writes_success_response(self) -> None:
        fake = _FakePet()
        with mock.patch.object(
            main.QFileDialog,
            "getOpenFileName",
            return_value=(str(main.ROOT_DIR / "background.png"), "Images (*.png)"),
            create=True,
        ):
            main.DesktopPet.process_runtime_command(
                fake,
                {
                    "nonce": "nonce-3",
                    "type": "pick_image_file",
                    "payload": {"start_path": str(main.ROOT_DIR / "seed.png")},
                },
            )
        self.assertEqual(
            fake.responses,
            [("success", {"path": str(main.ROOT_DIR / "background.png"), "start_path": str(main.ROOT_DIR / "seed.png")})],
        )


if __name__ == "__main__":
    unittest.main()
