from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _parse_source(relative_path: str) -> ast.Module:
    return ast.parse((ROOT_DIR / relative_path).read_text(encoding="utf-8"))


class ControlPanelSplitTests(unittest.TestCase):
    def test_main_imports_control_panel_without_defining_it(self) -> None:
        tree = _parse_source("main.py")

        class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
        self.assertNotIn("ControlPanel", class_names)

        imports_control_panel = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "app.control_panel"
            and any(alias.name == "ControlPanel" for alias in node.names)
            for node in ast.walk(tree)
        )
        self.assertTrue(imports_control_panel)

    def test_control_panel_module_does_not_import_main(self) -> None:
        tree = _parse_source("app/control_panel.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertNotIn("main", {alias.name for alias in node.names})
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "main")

        module = importlib.import_module("app.control_panel")
        self.assertTrue(hasattr(module, "ControlPanel"))


if __name__ == "__main__":
    unittest.main()
