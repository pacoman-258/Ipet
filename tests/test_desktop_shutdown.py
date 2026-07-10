from __future__ import annotations

import ast
import importlib
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


class _FakeSignal:
    def __init__(self) -> None:
        self.disconnected: list[object] = []

    def disconnect(self, callback: object) -> None:
        self.disconnected.append(callback)


class _FakePage:
    def __init__(self) -> None:
        self.web_channels: list[object] = []

    def setWebChannel(self, channel: object) -> None:
        self.web_channels.append(channel)


class _FakeBrowser:
    def __init__(self) -> None:
        self.loadFinished = _FakeSignal()
        self.page_obj = _FakePage()
        self.calls: list[str] = []
        self.removed_filters: list[object] = []

    def stop(self) -> None:
        self.calls.append("stop")

    def removeEventFilter(self, owner: object) -> None:
        self.removed_filters.append(owner)

    def page(self) -> _FakePage:
        return self.page_obj

    def close(self) -> None:
        self.calls.append("close")

    def deleteLater(self) -> None:
        self.calls.append("deleteLater")


class _FakePanel:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def close(self) -> None:
        self.calls.append("close")

    def deleteLater(self) -> None:
        self.calls.append("deleteLater")


class _FakeTimer:
    def __init__(self, active: bool = True) -> None:
        self.active = active
        self.stopped = False

    def isActive(self) -> bool:
        return self.active

    def stop(self) -> None:
        self.stopped = True
        self.active = False


class _FakeApplication:
    _instance: "_FakeApplication | None" = None

    def __init__(self) -> None:
        self.removed_filters: list[object] = []
        _FakeApplication._instance = self

    @classmethod
    def instance(cls):
        return cls._instance

    def removeEventFilter(self, owner: object) -> None:
        self.removed_filters.append(owner)


class DesktopShutdownSplitTests(unittest.TestCase):
    def test_shutdown_module_exists_without_importing_main(self) -> None:
        tree = _parse_source("app/desktop_shutdown.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertNotIn("main", {alias.name for alias in node.names})
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "main")

        module = importlib.import_module("app.desktop_shutdown")
        self.assertTrue(hasattr(module, "DesktopShutdownController"))

    def test_desktop_pet_shutdown_desktop_delegates_to_shutdown_controller(self) -> None:
        tree = _parse_source("main.py")
        desktop_pet = next(
            node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "DesktopPet"
        )
        shutdown_desktop = next(
            node
            for node in desktop_pet.body
            if isinstance(node, ast.FunctionDef) and node.name == "shutdown_desktop"
        )

        self.assertLessEqual(shutdown_desktop.end_lineno - shutdown_desktop.lineno, 4)
        self.assertIn(
            "shutdown_desktop",
            {node.attr for node in ast.walk(shutdown_desktop) if isinstance(node, ast.Attribute)},
        )

        controller = mock.Mock()
        host = SimpleNamespace(_desktop_shutdown_controller=controller)

        main.DesktopPet.shutdown_desktop(host)

        controller.shutdown_desktop.assert_called_once_with()

    def test_controller_attempts_desktop_resource_cleanup_and_stops_services(self) -> None:
        module = importlib.import_module("app.desktop_shutdown")
        app = _FakeApplication()
        browser = _FakeBrowser()
        panel = _FakePanel()
        timer = _FakeTimer()
        channel = mock.Mock()
        owner = SimpleNamespace(
            _shutdown_in_progress=False,
            _python_event_filters_installed=True,
            vision_controller=mock.Mock(),
            _config_poll_timer=timer,
            control_panel=panel,
            browser=browser,
            channel=channel,
            on_web_loaded=object(),
            stop_asr_service=mock.Mock(),
            stop_backend_service=mock.Mock(),
        )

        controller = module.DesktopShutdownController(owner, q_application=_FakeApplication)
        controller.shutdown_desktop()

        self.assertTrue(owner._shutdown_in_progress)
        owner.vision_controller.stop.assert_called_once_with()
        self.assertTrue(timer.stopped)
        self.assertEqual(app.removed_filters, [owner])
        self.assertEqual(panel.calls, ["close", "deleteLater"])
        self.assertIsNone(owner.control_panel)
        self.assertEqual(browser.loadFinished.disconnected, [owner.on_web_loaded])
        self.assertEqual(browser.calls, ["stop", "close", "deleteLater"])
        self.assertEqual(browser.removed_filters, [owner])
        self.assertEqual(browser.page_obj.web_channels, [None])
        channel.deleteLater.assert_called_once_with()
        owner.stop_asr_service.assert_called_once_with()
        owner.stop_backend_service.assert_called_once_with()

    def test_controller_shutdown_guard_skips_second_cleanup(self) -> None:
        module = importlib.import_module("app.desktop_shutdown")
        owner = SimpleNamespace(
            _shutdown_in_progress=True,
            vision_controller=mock.Mock(),
            stop_asr_service=mock.Mock(),
            stop_backend_service=mock.Mock(),
        )

        module.DesktopShutdownController(owner, q_application=_FakeApplication).shutdown_desktop()

        owner.vision_controller.stop.assert_not_called()
        owner.stop_asr_service.assert_not_called()
        owner.stop_backend_service.assert_not_called()


if __name__ == "__main__":
    unittest.main()
