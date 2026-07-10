from __future__ import annotations

import inspect
import unittest

import main
from app import settings_window
from app.settings_window import SettingsWindowController


class _FakeSettingsWindow:
    def __init__(self, *, visible: bool = False) -> None:
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


class _PatchPointHost:
    def __init__(self) -> None:
        self.config = {"chat": {"backend_url": "http://127.0.0.1:8008"}}
        self.settings_window = None
        self._settings_window_url = ""
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

    def _show_or_focus_settings_window(self, url: str) -> None:
        main.DesktopPet._show_or_focus_settings_window(self, url)


class DesktopSettingsWindowSplitTests(unittest.TestCase):
    def test_settings_window_module_does_not_import_main(self) -> None:
        source = inspect.getsource(settings_window)

        self.assertNotIn("import main", source)
        self.assertNotIn("from main", source)

    def test_controller_uses_owner_patch_point_when_creating_window(self) -> None:
        host = _PatchPointHost()
        controller = SettingsWindowController(host)

        controller.show_or_focus_settings_window("http://127.0.0.1:8008/settings")
        controller.show_or_focus_settings_window("http://127.0.0.1:8008/settings")

        self.assertEqual(len(host.created), 1)
        self.assertIs(host.settings_window, host.created[0])
        self.assertEqual(host._settings_window_url, "http://127.0.0.1:8008/settings")
        self.assertEqual(
            host.created[0].calls,
            ["setUrl", "show", "raise", "activate", "raise", "activate"],
        )

    def test_desktop_pet_wrapper_lazily_attaches_settings_window_controller(self) -> None:
        host = _PatchPointHost()

        main.DesktopPet.open_settings_page(host)

        self.assertEqual(host.backend_calls, 1)
        self.assertIsInstance(host._settings_window_controller, SettingsWindowController)
        self.assertEqual(len(host.created), 1)


if __name__ == "__main__":
    unittest.main()
