from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path
from unittest import mock

import main


ROOT_DIR = Path(__file__).resolve().parents[1]


def _parse_source(relative_path: str) -> ast.Module:
    return ast.parse((ROOT_DIR / relative_path).read_text(encoding="utf-8"))


class _FakeWindowType:
    FramelessWindowHint = 1
    WindowStaysOnTopHint = 2
    ToolTip = 4
    WindowDoesNotAcceptFocus = 8


class _FakeWidgetAttribute:
    WA_TranslucentBackground = "translucent-background"
    WA_TransparentForMouseEvents = "transparent-for-mouse"
    WA_ShowWithoutActivating = "show-without-activating"


class _FakeQt:
    WindowType = _FakeWindowType
    WidgetAttribute = _FakeWidgetAttribute


class _FakeColor:
    def __init__(self, *rgba: int) -> None:
        self.rgba = rgba


class _FakePainter:
    class RenderHint:
        Antialiasing = "antialiasing"

    def __init__(self, widget) -> None:
        self.widget = widget
        self.calls: list[tuple[str, tuple]] = []

    def setRenderHint(self, *args) -> None:
        self.calls.append(("setRenderHint", args))

    def setBrush(self, *args) -> None:
        self.calls.append(("setBrush", args))

    def setPen(self, *args) -> None:
        self.calls.append(("setPen", args))

    def drawEllipse(self, *args) -> None:
        self.calls.append(("drawEllipse", args))

    def end(self) -> None:
        self.calls.append(("end", ()))


class _FakeWidget:
    def __init__(self, parent=None) -> None:
        self.parent = parent
        self.flags = None
        self.attributes: list[tuple[object, bool]] = []
        self.fixed_size = (0, 0)
        self.tooltip = ""
        self.position = (0, 0)
        self.calls: list[str] = []

    def setWindowFlags(self, flags) -> None:
        self.flags = flags

    def setAttribute(self, attribute, enabled: bool) -> None:
        self.attributes.append((attribute, enabled))

    def setFixedSize(self, width: int, height: int) -> None:
        self.fixed_size = (width, height)

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = tooltip

    def move(self, x: int, y: int) -> None:
        self.position = (x, y)

    def show(self) -> None:
        self.calls.append("show")

    def raise_(self) -> None:
        self.calls.append("raise")

    def update(self) -> None:
        self.calls.append("update")

    def hide(self) -> None:
        self.calls.append("hide")

    def width(self) -> int:
        return self.fixed_size[0]

    def height(self) -> int:
        return self.fixed_size[1]


class _FakeOverlay:
    instances: list["_FakeOverlay"] = []

    def __init__(self) -> None:
        self.payloads: list[dict] = []
        _FakeOverlay.instances.append(self)

    def show_preview(self, payload: dict | None) -> bool:
        self.payloads.append(payload or {})
        return bool(payload)

    def hide(self) -> None:
        self.payloads.append({"hidden": True})


class DesktopClickPreviewSplitTests(unittest.TestCase):
    def test_click_preview_module_exists_without_importing_main(self) -> None:
        tree = _parse_source("app/click_preview.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertNotIn("main", {alias.name for alias in node.names})
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "main")

    def test_payload_normalization_clamps_size_and_cleans_label(self) -> None:
        click_preview = importlib.import_module("app.click_preview")

        payload = click_preview.normalize_click_preview_payload(
            {"x": "12.4", "y": "18.6", "size": "500", "label": "  click\x00 here  "}
        )

        self.assertEqual(payload, {"x": 12, "y": 19, "size": 48, "label": "click here"})
        self.assertEqual(
            click_preview.normalize_click_preview_payload({"x": 5, "y": 6, "size": 1, "target": "按钮"}),
            {"x": 5, "y": 6, "size": 10, "label": "按钮"},
        )
        self.assertEqual(
            click_preview.normalize_click_preview_payload({"x": 1, "y": 2, "label": ""})["label"],
            "目标位置",
        )
        self.assertEqual(click_preview.normalize_click_preview_payload({"x": "bad", "y": 2}), {})

    def test_main_compat_entries_delegate_to_click_preview_module(self) -> None:
        click_preview = importlib.import_module("app.click_preview")

        self.assertIs(main.normalize_click_preview_payload, click_preview.normalize_click_preview_payload)
        self.assertEqual(main.ClickPreviewOverlay.__module__, click_preview.__name__)

    def test_click_preview_module_owns_host_overlay_lifecycle(self) -> None:
        click_preview = importlib.import_module("app.click_preview")
        host = type("Host", (), {"click_preview_overlay": None})()
        _FakeOverlay.instances = []

        click_preview.show_click_preview(
            host,
            '{"x": 10, "y": 20}',
            overlay_factory=_FakeOverlay,
        )
        click_preview.show_click_preview(
            host,
            "not-json",
            overlay_factory=_FakeOverlay,
        )
        click_preview.hide_click_preview(host)

        self.assertEqual(len(_FakeOverlay.instances), 1)
        self.assertEqual(
            _FakeOverlay.instances[0].payloads,
            [{"x": 10, "y": 20}, {}, {"hidden": True}],
        )

    def test_desktop_pet_click_preview_methods_are_thin_module_delegates(self) -> None:
        click_preview = importlib.import_module("app.click_preview")
        host = type("Host", (), {"click_preview_overlay": None})()

        with mock.patch.object(click_preview, "show_click_preview") as show_preview, mock.patch.object(
            click_preview, "hide_click_preview"
        ) as hide_preview:
            main.DesktopPet.show_click_preview(host, "payload")
            main.DesktopPet.hide_click_preview(host)

        show_preview.assert_called_once()
        self.assertIs(show_preview.call_args.args[0], host)
        self.assertEqual(show_preview.call_args.args[1], "payload")
        hide_preview.assert_called_once_with(host)

    def test_desktop_pet_show_click_preview_uses_compat_overlay_entry(self) -> None:
        host = type("Host", (), {"click_preview_overlay": None})()
        _FakeOverlay.instances = []

        with mock.patch.object(main, "ClickPreviewOverlay", _FakeOverlay):
            main.DesktopPet.show_click_preview(host, '{"x": 10, "y": 20, "size": 12, "label": "ok"}')
            main.DesktopPet.show_click_preview(host, "not-json")

        self.assertEqual(len(_FakeOverlay.instances), 1)
        self.assertIs(host.click_preview_overlay, _FakeOverlay.instances[0])
        self.assertEqual(
            _FakeOverlay.instances[0].payloads,
            [{"x": 10, "y": 20, "size": 12, "label": "ok"}, {}],
        )

    def test_overlay_show_preview_uses_fake_widget_without_real_gui(self) -> None:
        click_preview = importlib.import_module("app.click_preview")
        deps = click_preview.ClickPreviewQtDependencies(
            qt=_FakeQt,
            widget_base=_FakeWidget,
            q_painter=_FakePainter,
            q_color=_FakeColor,
        )
        overlay_class = click_preview.create_click_preview_overlay_class(deps)
        overlay = overlay_class()

        shown = overlay.show_preview({"x": 100.2, "y": 80.7, "size": 9, "label": "  target  "})

        self.assertTrue(shown)
        self.assertEqual(overlay.flags, 15)
        self.assertIn((_FakeWidgetAttribute.WA_TranslucentBackground, True), overlay.attributes)
        self.assertIn((_FakeWidgetAttribute.WA_TransparentForMouseEvents, True), overlay.attributes)
        self.assertIn((_FakeWidgetAttribute.WA_ShowWithoutActivating, True), overlay.attributes)
        self.assertEqual(overlay.fixed_size, (10, 10))
        self.assertEqual(overlay.tooltip, "target")
        self.assertEqual(overlay.position, (95, 76))
        self.assertEqual(overlay.calls, ["show", "raise", "update"])

        self.assertFalse(overlay.show_preview({"x": "bad", "y": 1}))
        self.assertEqual(overlay.calls[-1], "hide")


if __name__ == "__main__":
    unittest.main()
