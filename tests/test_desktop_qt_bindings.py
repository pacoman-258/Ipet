from __future__ import annotations

import ast
import importlib
from pathlib import Path
from types import ModuleType
import unittest
from unittest import mock

import main
import app.qt_bindings as qt_bindings


ROOT_DIR = Path(__file__).resolve().parents[1]

QT_EXPORT_NAMES = (
    "QByteArray",
    "QBuffer",
    "QIODevice",
    "QObject",
    "QPoint",
    "Qt",
    "QEvent",
    "QTimer",
    "QUrl",
    "Signal",
    "Slot",
    "QAction",
    "QColor",
    "QGuiApplication",
    "QImage",
    "QPainter",
    "QPixmap",
    "QWebChannel",
    "QWebEnginePage",
    "QWebEngineSettings",
    "QWebEngineView",
    "QApplication",
    "QFileDialog",
    "QMainWindow",
    "QMenu",
    "QWidget",
)


def _module(name: str, **attrs) -> ModuleType:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _fake_qt_modules(binding_name: str) -> dict[str, ModuleType]:
    core_attrs = {
        "QByteArray": f"{binding_name}.QByteArray",
        "QBuffer": f"{binding_name}.QBuffer",
        "QIODevice": f"{binding_name}.QIODevice",
        "QObject": f"{binding_name}.QObject",
        "QPoint": f"{binding_name}.QPoint",
        "Qt": f"{binding_name}.Qt",
        "QEvent": f"{binding_name}.QEvent",
        "QTimer": f"{binding_name}.QTimer",
        "QUrl": f"{binding_name}.QUrl",
    }
    if binding_name == "PyQt6":
        core_attrs["pyqtSignal"] = f"{binding_name}.Signal"
        core_attrs["pyqtSlot"] = f"{binding_name}.Slot"
    else:
        core_attrs["Signal"] = f"{binding_name}.Signal"
        core_attrs["Slot"] = f"{binding_name}.Slot"

    return {
        f"{binding_name}.QtCore": _module(f"{binding_name}.QtCore", **core_attrs),
        f"{binding_name}.QtGui": _module(
            f"{binding_name}.QtGui",
            QAction=f"{binding_name}.QAction",
            QColor=f"{binding_name}.QColor",
            QGuiApplication=f"{binding_name}.QGuiApplication",
            QImage=f"{binding_name}.QImage",
            QPainter=f"{binding_name}.QPainter",
            QPixmap=f"{binding_name}.QPixmap",
        ),
        f"{binding_name}.QtWebChannel": _module(
            f"{binding_name}.QtWebChannel",
            QWebChannel=f"{binding_name}.QWebChannel",
        ),
        f"{binding_name}.QtWebEngineCore": _module(
            f"{binding_name}.QtWebEngineCore",
            QWebEnginePage=f"{binding_name}.QWebEnginePage",
            QWebEngineSettings=f"{binding_name}.QWebEngineSettings",
        ),
        f"{binding_name}.QtWebEngineWidgets": _module(
            f"{binding_name}.QtWebEngineWidgets",
            QWebEngineView=f"{binding_name}.QWebEngineView",
        ),
        f"{binding_name}.QtWidgets": _module(
            f"{binding_name}.QtWidgets",
            QApplication=f"{binding_name}.QApplication",
            QFileDialog=f"{binding_name}.QFileDialog",
            QMainWindow=f"{binding_name}.QMainWindow",
            QMenu=f"{binding_name}.QMenu",
            QWidget=f"{binding_name}.QWidget",
        ),
    }


class _FakeQtImporter:
    def __init__(self, available_bindings: set[str]) -> None:
        self.available_bindings = available_bindings
        self.modules: dict[str, ModuleType] = {}
        self.calls: list[str] = []
        for binding_name in ("PyQt6", "PySide6"):
            self.modules.update(_fake_qt_modules(binding_name))

    def __call__(self, name: str, package: str | None = None) -> ModuleType:
        del package
        self.calls.append(name)
        binding_name = name.split(".", 1)[0]
        if binding_name not in self.available_bindings:
            raise ImportError(f"{binding_name} unavailable")
        return self.modules[name]

    def binding_attempts(self) -> list[str]:
        attempts: list[str] = []
        for name in self.calls:
            binding_name = name.split(".", 1)[0]
            if not attempts or attempts[-1] != binding_name:
                attempts.append(binding_name)
        return attempts


class DesktopQtBindingsTests(unittest.TestCase):
    def test_prefer_pyqt_loads_pyqt_before_pyside_fallback(self) -> None:
        importer = _FakeQtImporter({"PySide6"})

        with mock.patch.object(qt_bindings.importlib, "import_module", importer):
            bindings = qt_bindings.load_qt_bindings(prefer_pyqt=True)

        self.assertEqual(importer.binding_attempts(), ["PyQt6", "PySide6"])
        self.assertEqual(bindings.binding_name, "PySide6")
        self.assertEqual(bindings.Signal, "PySide6.Signal")
        self.assertEqual(bindings.Slot, "PySide6.Slot")

    def test_prefer_pyside_loads_pyside_before_pyqt_fallback(self) -> None:
        importer = _FakeQtImporter({"PyQt6"})

        with mock.patch.object(qt_bindings.importlib, "import_module", importer):
            bindings = qt_bindings.load_qt_bindings(prefer_pyqt=False)

        self.assertEqual(importer.binding_attempts(), ["PySide6", "PyQt6"])
        self.assertEqual(bindings.binding_name, "PyQt6")
        self.assertEqual(bindings.Signal, "PyQt6.Signal")
        self.assertEqual(bindings.Slot, "PyQt6.Slot")

    def test_main_uses_qt_bindings_module_without_direct_qt_import_blocks(self) -> None:
        source = (ROOT_DIR / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        imports_loader = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "app.qt_bindings"
            and any(alias.name == "load_qt_bindings" for alias in node.names)
            for node in ast.walk(tree)
        )
        calls_loader = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "load_qt_bindings"
            for node in ast.walk(tree)
        )
        direct_qt_imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module.startswith("PyQt6.") or node.module.startswith("PySide6."))
        ]

        self.assertTrue(imports_loader)
        self.assertTrue(calls_loader)
        self.assertEqual(direct_qt_imports, [])

    def test_main_still_exports_qt_symbols_for_existing_callers(self) -> None:
        for name in QT_EXPORT_NAMES:
            self.assertTrue(hasattr(main, name), name)
        self.assertIn(main._QT_BINDINGS.binding_name, {"PyQt6", "PySide6"})


if __name__ == "__main__":
    unittest.main()
