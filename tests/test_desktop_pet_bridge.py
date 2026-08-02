from __future__ import annotations

import ast
import importlib
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import main


ROOT_DIR = Path(__file__).resolve().parents[1]


def _parse_source(relative_path: str) -> ast.Module:
    return ast.parse((ROOT_DIR / relative_path).read_text(encoding="utf-8"))


class _FakeBoundSignal:
    def __init__(self) -> None:
        self.emitted: list[tuple[object, ...]] = []
        self.connected: list[object] = []

    def emit(self, *args) -> None:
        self.emitted.append(args)
        for callback in self.connected:
            callback(*args)

    def connect(self, callback) -> None:
        self.connected.append(callback)


class _FakeSignalDescriptor:
    def __init__(self, *types) -> None:
        self.types = types
        self.name = ""

    def __set_name__(self, owner, name: str) -> None:
        self.name = name

    def __get__(self, instance, owner):
        if instance is None:
            return self
        signals = instance.__dict__.setdefault("_fake_signals", {})
        if self.name not in signals:
            signals[self.name] = _FakeBoundSignal()
        return signals[self.name]


class _FakeQObject:
    pass


def _fake_signal_factory(*types):
    return _FakeSignalDescriptor(*types)


def _fake_slot_decorator(*types):
    def decorate(func):
        func._fake_slot_types = types
        return func

    return decorate


def _pet_bridge_class():
    module = importlib.import_module("app.pet_bridge")
    return module.create_pet_bridge_class(
        _FakeQObject,
        _fake_signal_factory,
        _fake_slot_decorator,
        print_func=print,
    )


class DesktopPetBridgeTests(unittest.TestCase):
    def test_pet_bridge_module_does_not_import_main_or_qt(self) -> None:
        tree = _parse_source("app/pet_bridge.py")
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
            "import app.pet_bridge\n"
            "raise SystemExit(1 if 'main' in sys.modules else 0)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], check=False)
        self.assertEqual(result.returncode, 0)

    def test_slots_emit_matching_signals_with_payloads(self) -> None:
        Bridge = _pet_bridge_class()
        bridge = Bridge()

        bridge.petStateChanged("pet-state")
        bridge.setInteractiveRegions('{"regions": []}')
        bridge.openSettingsPage()
        bridge.showClickPreview('{"x": 1}')
        bridge.showClickPreview(None)
        bridge.hideClickPreview()

        self.assertEqual(bridge.stateChanged.emitted, [("pet-state",)])
        self.assertEqual(bridge.interactiveRegionsChanged.emitted, [('{"regions": []}',)])
        self.assertEqual(bridge.openSettingsRequested.emitted, [()])
        self.assertEqual(bridge.showClickPreviewRequested.emitted, [('{"x": 1}',), ("",)])
        self.assertEqual(bridge.hideClickPreviewRequested.emitted, [()])

    def test_log_prints_web_prefix(self) -> None:
        Bridge = _pet_bridge_class()
        bridge = Bridge()
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            bridge.log("hello bridge")

        self.assertEqual(stdout.getvalue(), "[WEB] hello bridge\n")

    def test_main_exports_factory_created_pet_bridge(self) -> None:
        tree = _parse_source("main.py")
        inline_class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
        self.assertNotIn("PetBridge", inline_class_names)

        imports_factory = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "app.pet_bridge"
            and any(alias.name == "create_pet_bridge_class" for alias in node.names)
            for node in ast.walk(tree)
        )
        self.assertTrue(imports_factory)

        self.assertEqual(main.PetBridge.__name__, "PetBridge")
        self.assertTrue(hasattr(main.PetBridge, "petStateChanged"))


if __name__ == "__main__":
    unittest.main()
