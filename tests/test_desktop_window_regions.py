from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import main


ROOT_DIR = Path(__file__).resolve().parents[1]


class _FakeRegion:
    def __init__(self, x: int | None = None, y: int | None = None, width: int | None = None, height: int | None = None) -> None:
        self.rects = [] if x is None else [(x, y, width, height)]

    def united(self, other: "_FakeRegion") -> "_FakeRegion":
        combined = _FakeRegion(*self.rects[0])
        combined.rects = [*self.rects, *other.rects]
        return combined


class _FakeOwner:
    def __init__(self, width: int = 420, height: int = 640) -> None:
        self._width = width
        self._height = height
        self.masks: list[_FakeRegion] = []
        self.clear_count = 0
        self.attributes: list[tuple[object, bool]] = []

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height

    def setMask(self, mask: _FakeRegion) -> None:
        self.masks.append(mask)

    def clearMask(self) -> None:
        self.clear_count += 1

    def setAttribute(self, attribute: object, enabled: bool) -> None:
        self.attributes.append((attribute, enabled))


class DesktopWindowRegionTests(unittest.TestCase):
    def test_module_has_no_main_or_qt_import(self) -> None:
        tree = ast.parse((ROOT_DIR / "app/window_regions.py").read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        self.assertNotIn("main", imported)
        self.assertFalse(any(name.startswith(("PySide6", "PyQt6")) for name in imported))

    def test_controller_clamps_and_unites_interactive_rectangles(self) -> None:
        module = importlib.import_module("app.window_regions")
        owner = _FakeOwner(width=400, height=300)
        controller = module.WindowRegionController(
            owner,
            q_region_factory=_FakeRegion,
            mouse_transparent_attribute="mouse-transparent",
        )

        controller.apply_interactive_regions(
            '{"regions": ['
            '{"x": -5.2, "y": 10.1, "width": 105.4, "height": 50.2},'
            '{"x": 350, "y": 260, "width": 100, "height": 100}'
            "]}"
        )

        self.assertEqual(owner.clear_count, 0)
        self.assertEqual(owner.masks[-1].rects, [(0, 10, 101, 51), (350, 260, 50, 40)])
        self.assertEqual(owner.attributes, [("mouse-transparent", False)])

    def test_invalid_or_empty_payload_clears_mask_and_passes_mouse_through(self) -> None:
        module = importlib.import_module("app.window_regions")
        owner = _FakeOwner()
        controller = module.WindowRegionController(
            owner,
            q_region_factory=_FakeRegion,
            mouse_transparent_attribute="mouse-transparent",
        )

        for payload in ("", "not-json", '{"regions": []}', '{"regions": [{"x": 0, "y": 0, "width": 0, "height": 10}]}'):
            controller.apply_interactive_regions(payload)

        self.assertEqual(owner.masks, [])
        self.assertEqual(owner.clear_count, 4)
        self.assertEqual(owner.attributes, [("mouse-transparent", True)] * 4)

    def test_desktop_pet_delegates_region_payload(self) -> None:
        controller = SimpleNamespace(apply_interactive_regions=mock.Mock())
        host = SimpleNamespace(_window_region_controller=controller)

        main.DesktopPet.apply_interactive_regions(host, '{"regions": []}')

        controller.apply_interactive_regions.assert_called_once_with('{"regions": []}')


if __name__ == "__main__":
    unittest.main()
