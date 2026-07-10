from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path
from types import SimpleNamespace

import main


ROOT_DIR = Path(__file__).resolve().parents[1]


def _parse_source(relative_path: str) -> ast.Module:
    return ast.parse((ROOT_DIR / relative_path).read_text(encoding="utf-8"))


class _FakePoint:
    def __init__(self, x: int = 0, y: int = 0) -> None:
        self._x = x
        self._y = y

    def x(self) -> int:
        return self._x

    def y(self) -> int:
        return self._y

    def __add__(self, other: "_FakePoint") -> "_FakePoint":
        return _FakePoint(self._x + other.x(), self._y + other.y())

    def __sub__(self, other: "_FakePoint") -> "_FakePoint":
        return _FakePoint(self._x - other.x(), self._y - other.y())

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _FakePoint) and (self._x, self._y) == (other.x(), other.y())


class _FakePosition:
    def __init__(self, point: _FakePoint) -> None:
        self._point = point

    def toPoint(self) -> _FakePoint:
        return self._point


class _FakeGeometry:
    def __init__(self, left: int, top: int, width: int, height: int) -> None:
        self._left = left
        self._top = top
        self._width = width
        self._height = height

    def left(self) -> int:
        return self._left

    def top(self) -> int:
        return self._top

    def right(self) -> int:
        return self._left + self._width - 1

    def bottom(self) -> int:
        return self._top + self._height - 1

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height

    def topLeft(self) -> _FakePoint:
        return _FakePoint(self._left, self._top)


class _FakeBrowser:
    def __init__(self) -> None:
        self.cursors: list[object] = []

    def setCursor(self, cursor: object) -> None:
        self.cursors.append(cursor)


class _FakeOwner:
    def __init__(self) -> None:
        self.config = {"pet": {"edit_mode": True}, "window": {}}
        self.browser = _FakeBrowser()
        self.resize_margin = 8
        self.window_locked = False
        self.drag_offset = _FakePoint()
        self._window_dragging = False
        self._window_resizing = False
        self._resize_edges = (False, False, False, False)
        self._drag_start_global = _FakePoint()
        self._drag_start_geometry = self.geometry()
        self._geometry = _FakeGeometry(10, 20, 200, 160)
        self.sync_count = 0

    def rect(self) -> _FakeGeometry:
        return _FakeGeometry(0, 0, self._geometry.width(), self._geometry.height())

    def geometry(self) -> _FakeGeometry:
        geometry = getattr(self, "_geometry", _FakeGeometry(10, 20, 200, 160))
        return _FakeGeometry(geometry.left(), geometry.top(), geometry.width(), geometry.height())

    def frameGeometry(self) -> _FakeGeometry:
        return self.geometry()

    def minimumWidth(self) -> int:
        return 40

    def minimumHeight(self) -> int:
        return 40

    def setGeometry(self, left: int, top: int, width: int, height: int) -> None:
        self._geometry = _FakeGeometry(left, top, width, height)

    def move(self, point: _FakePoint) -> None:
        self._geometry = _FakeGeometry(point.x(), point.y(), self.width(), self.height())

    def x(self) -> int:
        return self._geometry.left()

    def y(self) -> int:
        return self._geometry.top()

    def width(self) -> int:
        return self._geometry.width()

    def height(self) -> int:
        return self._geometry.height()

    def sync_panel(self) -> None:
        self.sync_count += 1


class _FakeQt:
    class MouseButton:
        LeftButton = 1

    class KeyboardModifier:
        AltModifier = 2

    class CursorShape:
        ArrowCursor = "arrow"
        ClosedHandCursor = "closed-hand"
        OpenHandCursor = "open-hand"
        SizeBDiagCursor = "size-bdiag"
        SizeFDiagCursor = "size-fdiag"
        SizeHorCursor = "size-hor"
        SizeVerCursor = "size-ver"


class _FakeQEvent:
    class Type:
        Leave = "leave"
        MouseButtonPress = "press"
        MouseButtonRelease = "release"
        MouseMove = "move"


class _FakeQApplication:
    modifiers = 0

    @classmethod
    def keyboardModifiers(cls) -> int:
        return cls.modifiers


class _FakeEvent:
    def __init__(
        self,
        event_type: object,
        *,
        button: int = 0,
        buttons: int = 0,
        local: _FakePoint | None = None,
        global_: _FakePoint | None = None,
    ) -> None:
        self._type = event_type
        self._button = button
        self._buttons = buttons
        self._local = local or _FakePoint()
        self._global = global_ or _FakePoint()

    def type(self) -> object:
        return self._type

    def button(self) -> int:
        return self._button

    def buttons(self) -> int:
        return self._buttons

    def position(self) -> _FakePosition:
        return _FakePosition(self._local)

    def globalPosition(self) -> _FakePosition:
        return _FakePosition(self._global)


def _controller_for(owner: _FakeOwner):
    module = importlib.import_module("app.window_interaction")
    return module.WindowInteractionController(
        owner,
        qt=_FakeQt,
        q_event=_FakeQEvent,
        q_application=_FakeQApplication,
        q_point=_FakePoint,
    )


class DesktopWindowInteractionSplitTests(unittest.TestCase):
    def test_window_interaction_module_exists_without_importing_main(self) -> None:
        tree = _parse_source("app/window_interaction.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertNotIn("main", {alias.name for alias in node.names})
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "main")

        module = importlib.import_module("app.window_interaction")
        self.assertTrue(hasattr(module, "WindowInteractionController"))

    def test_desktop_pet_event_filter_is_thin_delegate_wrapper(self) -> None:
        tree = _parse_source("main.py")
        desktop_pet = next(
            node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "DesktopPet"
        )
        event_filter = next(
            node for node in desktop_pet.body if isinstance(node, ast.FunctionDef) and node.name == "eventFilter"
        )

        nested_functions = [
            node for node in ast.walk(event_filter) if isinstance(node, ast.FunctionDef) and node is not event_filter
        ]
        self.assertEqual(nested_functions, [])
        self.assertLessEqual(event_filter.end_lineno - event_filter.lineno, 8)
        self.assertIn(
            "event_filter",
            {node.attr for node in ast.walk(event_filter) if isinstance(node, ast.Attribute)},
        )

    def test_desktop_pet_event_filter_keeps_direct_patch_point_and_delegates(self) -> None:
        class _FakeController:
            def __init__(self) -> None:
                self.calls: list[tuple[object, object]] = []

            def event_filter(self, watched: object, event: object) -> object:
                self.calls.append((watched, event))
                return "handled-by-controller"

        controller = _FakeController()
        host = SimpleNamespace(_window_interaction_controller=controller)

        result = main.DesktopPet.eventFilter(host, "watched", "event")

        self.assertEqual(result, "handled-by-controller")
        self.assertEqual(controller.calls, [("watched", "event")])

    def test_controller_preserves_edit_mode_resize_and_drag_state(self) -> None:
        owner = _FakeOwner()
        controller = _controller_for(owner)

        resize_press = _FakeEvent(
            _FakeQEvent.Type.MouseButtonPress,
            button=_FakeQt.MouseButton.LeftButton,
            local=_FakePoint(198, 80),
            global_=_FakePoint(300, 400),
        )
        self.assertIs(controller.event_filter(owner.browser, resize_press), True)
        self.assertTrue(owner._window_resizing)
        self.assertFalse(owner._window_dragging)
        self.assertEqual(owner._resize_edges, (False, False, True, False))
        self.assertEqual(owner.browser.cursors[-1], _FakeQt.CursorShape.SizeHorCursor)

        resize_move = _FakeEvent(
            _FakeQEvent.Type.MouseMove,
            buttons=_FakeQt.MouseButton.LeftButton,
            local=_FakePoint(228, 80),
            global_=_FakePoint(330, 400),
        )
        self.assertIs(controller.event_filter(owner.browser, resize_move), True)
        self.assertEqual((owner.x(), owner.y(), owner.width(), owner.height()), (10, 20, 230, 160))
        self.assertEqual(owner.config["window"], {"x": 10, "y": 20, "width": 230, "height": 160})
        self.assertEqual(owner.sync_count, 1)

        resize_release = _FakeEvent(
            _FakeQEvent.Type.MouseButtonRelease,
            button=_FakeQt.MouseButton.LeftButton,
            local=_FakePoint(228, 80),
            global_=_FakePoint(330, 400),
        )
        self.assertIs(controller.event_filter(owner.browser, resize_release), True)
        self.assertFalse(owner._window_resizing)
        self.assertEqual(owner._resize_edges, (False, False, False, False))
        self.assertEqual(owner.browser.cursors[-1], _FakeQt.CursorShape.OpenHandCursor)

        drag_owner = _FakeOwner()
        drag_controller = _controller_for(drag_owner)
        drag_press = _FakeEvent(
            _FakeQEvent.Type.MouseButtonPress,
            button=_FakeQt.MouseButton.LeftButton,
            local=_FakePoint(30, 20),
            global_=_FakePoint(300, 400),
        )
        self.assertIs(drag_controller.event_filter(drag_owner.browser, drag_press), True)
        self.assertTrue(drag_owner._window_dragging)
        self.assertEqual(drag_owner.browser.cursors[-1], _FakeQt.CursorShape.ClosedHandCursor)

        drag_move = _FakeEvent(
            _FakeQEvent.Type.MouseMove,
            buttons=_FakeQt.MouseButton.LeftButton,
            local=_FakePoint(45, 50),
            global_=_FakePoint(315, 430),
        )
        self.assertIs(drag_controller.event_filter(drag_owner.browser, drag_move), True)
        self.assertEqual((drag_owner.x(), drag_owner.y(), drag_owner.width(), drag_owner.height()), (25, 50, 200, 160))
        self.assertEqual(drag_owner.config["window"], {"x": 25, "y": 50, "width": 200, "height": 160})


if __name__ == "__main__":
    unittest.main()
