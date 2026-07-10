from __future__ import annotations

from dataclasses import dataclass
import importlib
from typing import Any


@dataclass(frozen=True, slots=True)
class QtBindings:
    binding_name: str
    QByteArray: Any
    QBuffer: Any
    QIODevice: Any
    QObject: Any
    QPoint: Any
    Qt: Any
    QEvent: Any
    QTimer: Any
    QUrl: Any
    Signal: Any
    Slot: Any
    QAction: Any
    QColor: Any
    QGuiApplication: Any
    QImage: Any
    QPainter: Any
    QPixmap: Any
    QWebChannel: Any
    QWebEnginePage: Any
    QWebEngineSettings: Any
    QWebEngineView: Any
    QApplication: Any
    QFileDialog: Any
    QMainWindow: Any
    QMenu: Any
    QWidget: Any


def load_qt_bindings(prefer_pyqt: bool) -> QtBindings:
    """Load Qt symbols in the same PyQt6/PySide6 priority order main.py used."""
    last_error: ImportError | None = None
    for binding_name in _binding_order(prefer_pyqt):
        try:
            return _load_binding(binding_name)
        except ImportError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ImportError("No Qt bindings were attempted")


def _binding_order(prefer_pyqt: bool) -> tuple[str, str]:
    if prefer_pyqt:
        return ("PyQt6", "PySide6")
    return ("PySide6", "PyQt6")


def _load_binding(binding_name: str) -> QtBindings:
    core = importlib.import_module(f"{binding_name}.QtCore")
    gui = importlib.import_module(f"{binding_name}.QtGui")
    web_channel = importlib.import_module(f"{binding_name}.QtWebChannel")
    web_engine_core = importlib.import_module(f"{binding_name}.QtWebEngineCore")
    web_engine_widgets = importlib.import_module(f"{binding_name}.QtWebEngineWidgets")
    widgets = importlib.import_module(f"{binding_name}.QtWidgets")

    if binding_name == "PyQt6":
        signal = _required_attr(core, "pyqtSignal", binding_name)
        slot = _required_attr(core, "pyqtSlot", binding_name)
    else:
        signal = _required_attr(core, "Signal", binding_name)
        slot = _required_attr(core, "Slot", binding_name)

    return QtBindings(
        binding_name=binding_name,
        QByteArray=_required_attr(core, "QByteArray", binding_name),
        QBuffer=_required_attr(core, "QBuffer", binding_name),
        QIODevice=_required_attr(core, "QIODevice", binding_name),
        QObject=_required_attr(core, "QObject", binding_name),
        QPoint=_required_attr(core, "QPoint", binding_name),
        Qt=_required_attr(core, "Qt", binding_name),
        QEvent=_required_attr(core, "QEvent", binding_name),
        QTimer=_required_attr(core, "QTimer", binding_name),
        QUrl=_required_attr(core, "QUrl", binding_name),
        Signal=signal,
        Slot=slot,
        QAction=_required_attr(gui, "QAction", binding_name),
        QColor=_required_attr(gui, "QColor", binding_name),
        QGuiApplication=_required_attr(gui, "QGuiApplication", binding_name),
        QImage=_required_attr(gui, "QImage", binding_name),
        QPainter=_required_attr(gui, "QPainter", binding_name),
        QPixmap=_required_attr(gui, "QPixmap", binding_name),
        QWebChannel=_required_attr(web_channel, "QWebChannel", binding_name),
        QWebEnginePage=_required_attr(web_engine_core, "QWebEnginePage", binding_name),
        QWebEngineSettings=_required_attr(web_engine_core, "QWebEngineSettings", binding_name),
        QWebEngineView=_required_attr(web_engine_widgets, "QWebEngineView", binding_name),
        QApplication=_required_attr(widgets, "QApplication", binding_name),
        QFileDialog=_required_attr(widgets, "QFileDialog", binding_name),
        QMainWindow=_required_attr(widgets, "QMainWindow", binding_name),
        QMenu=_required_attr(widgets, "QMenu", binding_name),
        QWidget=_required_attr(widgets, "QWidget", binding_name),
    )


def _required_attr(module: Any, name: str, binding_name: str) -> Any:
    try:
        return getattr(module, name)
    except AttributeError as exc:
        raise ImportError(f"{binding_name} is missing {module.__name__}.{name}") from exc
