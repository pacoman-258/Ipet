import json
import os
import shutil
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from unittest import mock
from uuid import uuid4

import backend.app as backend_app
import backend.mcp_bridge as mcp_bridge_module
import third_party_mcp.jmmcp.main as jmmcp_main
from backend.mcp.stdio_client import StdioMCPClient


TEST_TMP_ROOT = Path(__file__).resolve().parent / ".tmp_test"
TEST_TMP_ROOT.mkdir(exist_ok=True)


def _install_qt_stubs() -> None:
    if "PySide6" in sys.modules or "PyQt6" in sys.modules:
        return

    class Dummy:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    def signal_stub(*_args, **_kwargs):
        return object()

    def slot_stub(*_args, **_kwargs):
        def decorator(func):
            return func

        return decorator

    qt_core = ModuleType("PySide6.QtCore")
    qt_core.QObject = Dummy
    qt_core.QPoint = Dummy
    qt_core.Qt = Dummy
    qt_core.QEvent = Dummy
    qt_core.QSignalBlocker = Dummy
    qt_core.QUrl = Dummy
    qt_core.Signal = signal_stub
    qt_core.Slot = slot_stub

    qt_gui = ModuleType("PySide6.QtGui")
    qt_gui.QAction = Dummy
    qt_gui.QColor = Dummy
    qt_gui.QGuiApplication = Dummy

    qt_web_channel = ModuleType("PySide6.QtWebChannel")
    qt_web_channel.QWebChannel = Dummy

    qt_web_engine_core = ModuleType("PySide6.QtWebEngineCore")
    qt_web_engine_core.QWebEngineSettings = Dummy

    qt_web_engine_widgets = ModuleType("PySide6.QtWebEngineWidgets")
    qt_web_engine_widgets.QWebEngineView = Dummy

    qt_widgets = ModuleType("PySide6.QtWidgets")
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
        setattr(qt_widgets, name, Dummy)

    pyside6 = ModuleType("PySide6")
    sys.modules["PySide6"] = pyside6
    sys.modules["PySide6.QtCore"] = qt_core
    sys.modules["PySide6.QtGui"] = qt_gui
    sys.modules["PySide6.QtWebChannel"] = qt_web_channel
    sys.modules["PySide6.QtWebEngineCore"] = qt_web_engine_core
    sys.modules["PySide6.QtWebEngineWidgets"] = qt_web_engine_widgets
    sys.modules["PySide6.QtWidgets"] = qt_widgets


_install_qt_stubs()
import main as desktop_main


def workspace_tempdir():
    return _workspace_tempdir()


@contextmanager
def _workspace_tempdir():
    path = TEST_TMP_ROOT / f"case_{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class JmMcpTests(unittest.TestCase):
    def test_build_option_limits_threading_and_disables_after_album_pdf(self) -> None:
        with workspace_tempdir() as tmpdir:
            with mock.patch.dict(
                os.environ,
                {"JM_PHOTO_THREADS": "2", "JM_IMAGE_THREADS": "3", "JM_RETRY_TIMES": "7"},
                clear=False,
            ):
                option = jmmcp_main._build_option(Path(tmpdir))

        option_dict = option.deconstruct()
        self.assertEqual(option_dict["download"]["threading"]["photo"], 2)
        self.assertEqual(option_dict["download"]["threading"]["image"], 3)
        self.assertEqual(option_dict["client"]["retry_times"], 7)
        self.assertNotIn("after_album", option_dict.get("plugins", {}))

    def test_download_failure_skips_pdf_generation_and_cleans_temp_dir(self) -> None:
        with workspace_tempdir() as tmpdir:
            temp_root = Path(tmpdir) / "tmp"
            save_root = Path(tmpdir) / "pdf"
            with (
                mock.patch.object(jmmcp_main, "DEFAULT_TEMP_ROOT", temp_root),
                mock.patch.object(jmmcp_main, "DEFAULT_SAVE_DIR", save_root),
                mock.patch.object(jmmcp_main, "download_album", side_effect=RuntimeError("boom")),
                mock.patch.object(jmmcp_main, "_generate_pdf") as generate_pdf,
            ):
                result = jmmcp_main.download_jm_album_pdf("123456")

        self.assertFalse(result["ok"])
        self.assertEqual(result["album_id"], "123456")
        self.assertEqual(result["error"], "boom")
        generate_pdf.assert_not_called()
        lingering_jobs = [path for path in temp_root.glob("jm_*")] if temp_root.exists() else []
        self.assertEqual(lingering_jobs, [])


class StdioMcpClientTests(unittest.TestCase):
    def test_timeout_stops_underlying_process(self) -> None:
        with workspace_tempdir() as tmpdir:
            script_path = Path(tmpdir) / "slow_server.py"
            script_path.write_text(
                textwrap.dedent(
                    """
                    import json
                    import sys
                    import time

                    for line in sys.stdin:
                        request = json.loads(line)
                        method = request.get("method")
                        if method == "initialize":
                            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}), flush=True)
                            continue
                        if method == "notifications/initialized":
                            continue
                        if method == "tools/call":
                            time.sleep(5)
                    """
                ),
                encoding="utf-8",
            )

            client = StdioMCPClient([sys.executable, str(script_path)], Path(tmpdir), timeout_sec=1)
            client.start()
            proc = client.proc

            with self.assertRaises(TimeoutError):
                client.call_tool("slow_tool", {})

            time.sleep(0.2)
            self.assertIsNone(client.proc)
            self.assertIsNotNone(proc)
            self.assertIsNotNone(proc.poll())


class McpBridgeTests(unittest.TestCase):
    def test_failed_third_party_call_marks_server_failed_and_restarts_on_next_call(self) -> None:
        class FakeLocalServer:
            def __init__(self, **_: object) -> None:
                self.calls: list[tuple[str, dict]] = []

            def list_tools(self) -> list[dict]:
                return []

            def call(self, name: str, arguments: dict) -> dict:
                self.calls.append((name, arguments))
                return {"local": True}

        class FakeManager:
            def __init__(self, root_dir: Path) -> None:
                self.root_dir = root_dir

            def list_manifests(self) -> list[dict]:
                return []

            def load_manifest(self, manifest_path: str) -> dict:
                server_dir = str(Path(manifest_path).parent)
                return {
                    "name": "jmmcp",
                    "version": "0.1.0",
                    "runtime": "python",
                    "manifest_path": manifest_path,
                    "server_dir": server_dir,
                    "enabled": True,
                    "env": {},
                }

            def ensure_installed(self, manifest_path: str) -> dict:
                return {"install_status": "ready", "manifest_path": manifest_path}

            def build_command(self, manifest: dict) -> list[str]:
                return [sys.executable, "-c", "print('fake')"]

        class FakeClient:
            instances: list["FakeClient"] = []

            def __init__(self, **_: object) -> None:
                self.index = len(self.instances)
                self.stopped = False
                self.instances.append(self)

            def start(self) -> None:
                return None

            def list_tools(self) -> list[dict]:
                return [
                    {
                        "name": "download_jm_album_pdf",
                        "description": "fake tool",
                        "inputSchema": {"type": "object"},
                    }
                ]

            def is_healthy(self) -> bool:
                return not self.stopped

            def stop(self) -> None:
                self.stopped = True

            def call_tool(self, name: str, arguments: dict) -> dict:
                if self.index == 0:
                    self.stopped = True
                    raise TimeoutError("timeout")
                return {"ok": True, "name": name, "arguments": arguments, "instance": self.index}

        with workspace_tempdir() as tmpdir:
            cfg = {
                "file_allowlist": [tmpdir],
                "network_allow_domains": [],
                "tool_timeout_sec": 180,
                "third_party": {
                    "enabled": True,
                    "servers": [
                        {
                            "name": "jmmcp",
                            "enabled": True,
                            "manifest_path": str(Path(tmpdir) / "manifest.json"),
                        }
                    ],
                },
            }
            with (
                mock.patch.object(mcp_bridge_module, "LocalMCPServer", FakeLocalServer),
                mock.patch.object(mcp_bridge_module, "ThirdPartyMCPManager", FakeManager),
                mock.patch.object(mcp_bridge_module, "StdioMCPClient", FakeClient),
            ):
                bridge = mcp_bridge_module.MCPBridge(cfg, Path(tmpdir))

                with self.assertRaises(TimeoutError):
                    bridge.call_tool("download_jm_album_pdf", {"album_id": "1"})

                self.assertEqual(bridge.third_party_status["jmmcp"]["health_status"], "failed")
                result = bridge.call_tool("download_jm_album_pdf", {"album_id": "2"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["instance"], 1)
        self.assertEqual(bridge.third_party_status["jmmcp"]["health_status"], "online")
        self.assertEqual(bridge.local.calls, [])


class ToolTimeoutMigrationTests(unittest.TestCase):
    def test_backend_runtime_tool_timeout_migrates_legacy_value(self) -> None:
        with workspace_tempdir() as tmpdir:
            config_path = Path(tmpdir) / "pet_config.json"
            config_path.write_text(
                json.dumps({"chat": {"tooling": {"tool_timeout_sec": 10}}}, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(backend_app, "CONFIG_PATH", config_path):
                config = backend_app._load_runtime_tooling_config()

        self.assertEqual(config["tool_timeout_sec"], 180)

    def test_backend_runtime_tool_timeout_preserves_custom_value(self) -> None:
        with workspace_tempdir() as tmpdir:
            config_path = Path(tmpdir) / "pet_config.json"
            config_path.write_text(
                json.dumps({"chat": {"tooling": {"tool_timeout_sec": 77}}}, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(backend_app, "CONFIG_PATH", config_path):
                config = backend_app._load_runtime_tooling_config()

        self.assertEqual(config["tool_timeout_sec"], 77)

    def test_desktop_load_config_uses_new_default_timeout(self) -> None:
        with workspace_tempdir() as tmpdir:
            config_path = Path(tmpdir) / "missing.json"
            with mock.patch.object(desktop_main, "CONFIG_PATH", config_path):
                config = desktop_main.load_config()

        self.assertEqual(config["chat"]["tooling"]["tool_timeout_sec"], 180)

    def test_desktop_load_config_migrates_legacy_timeout(self) -> None:
        with workspace_tempdir() as tmpdir:
            config_path = Path(tmpdir) / "pet_config.json"
            config_path.write_text(
                json.dumps({"chat": {"tooling": {"tool_timeout_sec": 10}}}, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(desktop_main, "CONFIG_PATH", config_path):
                config = desktop_main.load_config()

        self.assertEqual(config["chat"]["tooling"]["tool_timeout_sec"], 180)


if __name__ == "__main__":
    unittest.main()
