from __future__ import annotations

import ast
import inspect
import textwrap
import unittest
from unittest import mock

import main


class _FakeApplication:
    def __init__(self, *, supports_display_name: bool = True) -> None:
        self.application_name = ""
        self.display_name = ""
        self.icon = None
        self.quit_on_last_window_closed = True
        if not supports_display_name:
            self.setApplicationDisplayName = None

    def setApplicationName(self, name: str) -> None:
        self.application_name = name

    def setApplicationDisplayName(self, name: str) -> None:
        self.display_name = name

    def setWindowIcon(self, icon) -> None:
        self.icon = icon

    def setQuitOnLastWindowClosed(self, enabled: bool) -> None:
        self.quit_on_last_window_closed = enabled


class DesktopApplicationIdentityTests(unittest.TestCase):
    def test_runtime_identity_uses_ipet_name_and_project_icon(self) -> None:
        app = _FakeApplication()
        icon = object()

        with mock.patch.object(main, "_application_icon", return_value=icon):
            main.apply_application_identity(app)

        self.assertEqual(main.APP_DISPLAY_NAME, "Ipet")
        self.assertTrue(main.APP_ICON_PATH.is_file())
        self.assertEqual(app.application_name, "Ipet")
        self.assertEqual(app.display_name, "Ipet")
        self.assertIs(app.icon, icon)

    def test_runtime_identity_supports_qt_without_display_name_api(self) -> None:
        app = _FakeApplication(supports_display_name=False)
        icon = object()

        with mock.patch.object(main, "_application_icon", return_value=icon):
            main.apply_application_identity(app)

        self.assertEqual(app.application_name, "Ipet")
        self.assertIs(app.icon, icon)

    def test_auxiliary_window_close_does_not_exit_desktop_host(self) -> None:
        app = _FakeApplication()

        main.configure_application_lifecycle(app)

        self.assertFalse(app.quit_on_last_window_closed)

    def test_desktop_entrypoint_applies_application_lifecycle_policy(self) -> None:
        tree = ast.parse(inspect.getsource(main))
        called_functions = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        self.assertIn("configure_application_lifecycle", called_functions)

    def test_every_qt_quit_path_requests_desktop_shutdown(self) -> None:
        tree = ast.parse(inspect.getsource(main))
        connections = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "aboutToQuit"
        ]
        self.assertTrue(connections)
        self.assertTrue(
            any(
                node.args
                and isinstance(node.args[0], ast.Attribute)
                and node.args[0].attr == "shutdown_desktop"
                for node in connections
            )
        )

    def test_desktop_window_sets_the_same_title_and_icon(self) -> None:
        tree = ast.parse(textwrap.dedent(inspect.getsource(main.DesktopPet.__init__)))
        calls = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]

        self.assertIn("setWindowTitle", calls)
        self.assertIn("setWindowIcon", calls)


if __name__ == "__main__":
    unittest.main()
