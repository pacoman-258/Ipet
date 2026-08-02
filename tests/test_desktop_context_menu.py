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


def _context_menu_module():
    try:
        return importlib.import_module("app.context_menu")
    except ImportError as exc:
        raise AssertionError("app.context_menu module should be importable") from exc


class _FakePoint:
    def __init__(self, x: int, y: int) -> None:
        self._x = x
        self._y = y

    def x(self) -> int:
        return self._x

    def y(self) -> int:
        return self._y


class _FakePage:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    def runJavaScript(self, script: str) -> None:
        self.scripts.append(script)


class _FakeBrowser:
    def __init__(self) -> None:
        self.page_obj = _FakePage()

    def page(self) -> _FakePage:
        return self.page_obj


class DesktopContextMenuSplitTests(unittest.TestCase):
    def test_context_menu_module_exists_without_importing_main(self) -> None:
        tree = _parse_source("app/context_menu.py")
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)

        self.assertNotIn("main", imported_modules)
        self.assertTrue(hasattr(_context_menu_module(), "DesktopContextMenuController"))

    def test_controller_dispatches_local_position_to_web_pet_menu(self) -> None:
        owner = SimpleNamespace(browser=_FakeBrowser())
        controller = _context_menu_module().DesktopContextMenuController(owner)

        controller.show_context_menu(_FakePoint(37, 92))

        self.assertEqual(
            owner.browser.page_obj.scripts,
            ["window.PET_APP && window.PET_APP.showContextMenuAt(37, 92);"],
        )

    def test_main_constructs_and_delegates_context_menu_controller(self) -> None:
        tree = _parse_source("main.py")
        imports_controller = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "app.context_menu"
            and any(alias.name == "DesktopContextMenuController" for alias in node.names)
            for node in ast.walk(tree)
        )
        self.assertTrue(imports_controller)

        builder = next(
            node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_build_context_menu_controller"
        )
        constructor = next(
            node
            for node in ast.walk(builder)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "DesktopContextMenuController"
        )
        self.assertEqual(len(constructor.args), 1)
        self.assertEqual(constructor.keywords, [])

        controller = mock.Mock()
        host = SimpleNamespace(_context_menu_controller=controller)
        main.DesktopPet.show_context_menu(host, "local-pos")
        controller.show_context_menu.assert_called_once_with("local-pos")


if __name__ == "__main__":
    unittest.main()
