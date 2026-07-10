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


class _FakeAction:
    def __init__(self, text: str, owner) -> None:
        self.text = text
        self.owner = owner


class _FakeBrowser:
    def __init__(self) -> None:
        self.global_positions: list[object] = []

    def mapToGlobal(self, pos):
        self.global_positions.append(pos)
        return ("global", pos)


class _FakeOwner:
    def __init__(self, *, selected_text: str, locked: bool = False) -> None:
        self.selected_text = selected_text
        self.window_locked = locked
        self.config = {"window": {"locked": locked}}
        self.browser = _FakeBrowser()
        self.opened_settings = 0
        self.applied_config = 0
        self.closed = 0

    def open_settings_page(self) -> None:
        self.opened_settings += 1

    def apply_config_to_web(self) -> None:
        self.applied_config += 1

    def close(self) -> None:
        self.closed += 1


class _FakeMenu:
    instances: list["_FakeMenu"] = []

    def __init__(self, owner) -> None:
        self.owner = owner
        self.items: list[_FakeAction | str] = []
        self.exec_positions: list[object] = []
        self.__class__.instances.append(self)

    def addAction(self, action: _FakeAction) -> None:
        self.items.append(action)

    def addSeparator(self) -> None:
        self.items.append("separator")

    def exec(self, global_pos):
        self.exec_positions.append(global_pos)
        for item in self.items:
            if isinstance(item, _FakeAction) and item.text == self.owner.selected_text:
                return item
        return None


class DesktopContextMenuSplitTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeMenu.instances = []

    def _show_menu(self, selected_text: str, *, locked: bool = False):
        module = _context_menu_module()
        _FakeMenu.instances = []
        owner = _FakeOwner(selected_text=selected_text, locked=locked)
        controller = module.DesktopContextMenuController(
            owner,
            q_menu_factory=_FakeMenu,
            q_action_factory=_FakeAction,
        )

        controller.show_context_menu("local-pos")

        self.assertEqual(len(_FakeMenu.instances), 1)
        return owner, _FakeMenu.instances[0]

    def test_context_menu_module_exists_without_importing_main(self) -> None:
        tree = _parse_source("app/context_menu.py")

        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)

        self.assertNotIn("main", imported_modules)

        module = _context_menu_module()
        self.assertTrue(hasattr(module, "DesktopContextMenuController"))

    def test_controller_builds_menu_at_browser_global_position(self) -> None:
        owner, menu = self._show_menu("打开设置页")

        self.assertEqual(
            [item.text if isinstance(item, _FakeAction) else item for item in menu.items],
            ["打开设置页", "锁定窗口", "重新加载模型", "separator", "退出"],
        )
        self.assertEqual(owner.browser.global_positions, ["local-pos"])
        self.assertEqual(menu.exec_positions, [("global", "local-pos")])
        self.assertEqual(owner.opened_settings, 1)

    def test_controller_dispatches_selected_action(self) -> None:
        owner, _menu = self._show_menu("重新加载模型")
        self.assertEqual(owner.applied_config, 1)

        owner, _menu = self._show_menu("退出")
        self.assertEqual(owner.closed, 1)

    def test_controller_toggles_window_lock_and_uses_current_label(self) -> None:
        owner, menu = self._show_menu("锁定窗口", locked=False)
        self.assertTrue(owner.window_locked)
        self.assertTrue(owner.config["window"]["locked"])
        self.assertEqual(
            [item.text for item in menu.items if isinstance(item, _FakeAction)][1],
            "锁定窗口",
        )

        owner, menu = self._show_menu("解锁窗口", locked=True)
        self.assertFalse(owner.window_locked)
        self.assertFalse(owner.config["window"]["locked"])
        self.assertEqual(
            [item.text for item in menu.items if isinstance(item, _FakeAction)][1],
            "解锁窗口",
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

        desktop_pet = next(
            node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "DesktopPet"
        )
        show_context_menu = next(
            node
            for node in desktop_pet.body
            if isinstance(node, ast.FunctionDef) and node.name == "show_context_menu"
        )
        called_names = {
            node.func.id
            for node in ast.walk(show_context_menu)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("QMenu", called_names)
        self.assertNotIn("QAction", called_names)

        constructs_controller = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "DesktopContextMenuController"
            for node in ast.walk(tree)
        )
        self.assertTrue(constructs_controller)

        controller = mock.Mock()
        host = SimpleNamespace(_context_menu_controller=controller)

        main.DesktopPet.show_context_menu(host, "local-pos")

        controller.show_context_menu.assert_called_once_with("local-pos")


if __name__ == "__main__":
    unittest.main()
