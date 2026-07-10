from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _parse_source(relative_path: str) -> ast.Module:
    return ast.parse((ROOT_DIR / relative_path).read_text(encoding="utf-8"))


class ScreenVisionControllerSplitTests(unittest.TestCase):
    def test_main_imports_screen_vision_controller_without_defining_it(self) -> None:
        tree = _parse_source("main.py")

        class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
        self.assertNotIn("ScreenVisionController", class_names)

        imports_controller = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "body.screen_vision_controller"
            and any(alias.name == "ScreenVisionController" for alias in node.names)
            for node in ast.walk(tree)
        )
        self.assertTrue(imports_controller)

    def test_screen_vision_controller_module_does_not_import_main(self) -> None:
        module_path = ROOT_DIR / "body/screen_vision_controller.py"
        self.assertTrue(module_path.exists(), "body.screen_vision_controller module should exist")
        tree = _parse_source("body/screen_vision_controller.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertNotIn("main", {alias.name for alias in node.names})
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "main")

        script = (
            "import importlib, sys\n"
            "module = importlib.import_module('body.screen_vision_controller')\n"
            "print(hasattr(module, 'ScreenVisionController'))\n"
            "print('main' in sys.modules)\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.splitlines(), ["True", "False"])


if __name__ == "__main__":
    unittest.main()
