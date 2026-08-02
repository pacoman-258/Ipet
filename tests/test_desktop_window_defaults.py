from __future__ import annotations

import ast
import importlib
import subprocess
import sys
import unittest
from enum import IntFlag
from pathlib import Path

import main


ROOT_DIR = Path(__file__).resolve().parents[1]


class _FakeWindowType(IntFlag):
    Window = 1
    FramelessWindowHint = 2
    WindowStaysOnTopHint = 4
    Tool = 8


class _FakeWidgetAttribute:
    WA_MacAlwaysShowToolWindow = "mac-always-visible"


def _parse_source(relative_path: str) -> ast.Module:
    return ast.parse((ROOT_DIR / relative_path).read_text(encoding="utf-8"))


def _function_node(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} should exist")


class DesktopWindowDefaultsTests(unittest.TestCase):
    def test_window_defaults_module_does_not_import_main_or_qt(self) -> None:
        tree = _parse_source("app/window_defaults.py")
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)

        self.assertNotIn("main", imported_modules)
        self.assertFalse(any(name.startswith(("PySide6", "PyQt6")) for name in imported_modules))

        code = (
            "import sys\n"
            "sys.modules.pop('main', None)\n"
            "import app.window_defaults\n"
            "raise SystemExit(1 if 'main' in sys.modules else 0)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], check=False)
        self.assertEqual(result.returncode, 0)

    def test_window_flags_use_injected_flag_values(self) -> None:
        module = importlib.import_module("app.window_defaults")

        self.assertEqual(
            module.desktop_pet_window_flags(_FakeWindowType, is_macos=True),
            (
                _FakeWindowType.FramelessWindowHint
                | _FakeWindowType.WindowStaysOnTopHint
                | _FakeWindowType.Tool
            ),
        )
        self.assertEqual(
            module.desktop_pet_window_flags(_FakeWindowType, is_macos=False),
            (
                _FakeWindowType.FramelessWindowHint
                | _FakeWindowType.WindowStaysOnTopHint
                | _FakeWindowType.Tool
            ),
        )

    def test_translucency_and_background_color_policy(self) -> None:
        module = importlib.import_module("app.window_defaults")

        self.assertTrue(module.should_use_translucent_window(force_opaque=False, is_macos=True))
        self.assertFalse(module.should_use_translucent_window(force_opaque=True, is_macos=False))
        self.assertTrue(module.should_use_translucent_window(force_opaque=False, is_macos=False))
        self.assertEqual(module.desktop_pet_background_color(translucent=True), (0, 0, 0, 0))
        self.assertEqual(module.desktop_pet_background_color(translucent=False), (18, 18, 18, 255))

    def test_macos_tool_window_stays_visible_when_another_app_is_active(self) -> None:
        module = importlib.import_module("app.window_defaults")

        self.assertEqual(
            module.always_visible_tool_window_attribute(_FakeWidgetAttribute, is_macos=True),
            "mac-always-visible",
        )
        self.assertIsNone(
            module.always_visible_tool_window_attribute(_FakeWidgetAttribute, is_macos=False)
        )
        self.assertIsNone(
            module.always_visible_tool_window_attribute(object, is_macos=True)
        )

    def test_window_geometry_stays_visible_and_negative_defaults_anchor_bottom_right(self) -> None:
        module = importlib.import_module("app.window_defaults")

        self.assertEqual(
            module.visible_window_geometry(
                x=-1,
                y=-1,
                width=420,
                height=640,
                available_x=0,
                available_y=25,
                available_width=1180,
                available_height=743,
            ),
            (742, 110, 420, 640),
        )
        self.assertEqual(
            module.visible_window_geometry(
                x=136,
                y=33,
                width=1290,
                height=805,
                available_x=0,
                available_y=25,
                available_width=1180,
                available_height=743,
            ),
            (0, 25, 1180, 743),
        )

    def test_main_keeps_compatibility_wrappers_thin(self) -> None:
        tree = _parse_source("main.py")

        imports_defaults = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "app"
            and any(alias.name == "window_defaults" for alias in node.names)
            for node in ast.walk(tree)
        )
        self.assertTrue(imports_defaults)

        for name in (
            "_desktop_pet_window_flags",
            "_should_use_translucent_window",
            "_desktop_pet_background_color",
        ):
            node = _function_node(tree, name)
            self.assertFalse(any(isinstance(child, ast.If) for child in ast.walk(node)))
            self.assertTrue(
                any(
                    isinstance(child, ast.Attribute)
                    and isinstance(child.value, ast.Name)
                    and child.value.id == "_window_defaults"
                    for child in ast.walk(node)
                ),
                f"{name} should delegate to app.window_defaults",
            )

        macos_flags = main._desktop_pet_window_flags("darwin")
        self.assertTrue(bool(macos_flags & main.Qt.WindowType.FramelessWindowHint))
        self.assertTrue(bool(macos_flags & main.Qt.WindowType.WindowStaysOnTopHint))
        self.assertEqual(
            main._window_defaults.always_visible_tool_window_attribute(
                main.Qt.WidgetAttribute,
                is_macos=True,
            ),
            main.Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow,
        )
        self.assertTrue(main._should_use_translucent_window(force_opaque=False, platform_name="darwin"))
        self.assertEqual(main._desktop_pet_background_color("darwin"), (0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
