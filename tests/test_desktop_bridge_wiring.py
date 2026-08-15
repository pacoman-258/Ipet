from __future__ import annotations

import ast
import importlib
from pathlib import Path
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]


def _parse_source(relative_path: str) -> ast.Module:
    return ast.parse((ROOT_DIR / relative_path).read_text(encoding="utf-8"))


class _FakeSignal:
    def __init__(self) -> None:
        self.connected: list[object] = []

    def connect(self, callback) -> None:
        self.connected.append(callback)


class _FakeBridge:
    def __init__(self) -> None:
        self.stateChanged = _FakeSignal()
        self.interactiveRegionsChanged = _FakeSignal()
        self.openSettingsRequested = _FakeSignal()
        self.startAsrWarmupRequested = _FakeSignal()
        self.minimizeWindowRequested = _FakeSignal()
        self.closeWindowRequested = _FakeSignal()
        self.showClickPreviewRequested = _FakeSignal()
        self.hideClickPreviewRequested = _FakeSignal()
        self.showApprovalNotificationRequested = _FakeSignal()
        self.cancelApprovalNotificationRequested = _FakeSignal()


class _Owner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def on_web_state_changed(self, *args) -> None:
        self.calls.append("stateChanged")

    def apply_interactive_regions(self, *args) -> None:
        self.calls.append("interactiveRegionsChanged")

    def open_settings_page(self, *args) -> None:
        self.calls.append("openSettingsRequested")

    def request_asr_warmup(self, *args) -> None:
        self.calls.append("startAsrWarmupRequested")

    def hide_desktop_pet(self, *args) -> None:
        self.calls.append("minimizeWindowRequested")

    def close(self, *args) -> None:
        self.calls.append("closeWindowRequested")

    def show_click_preview(self, *args) -> None:
        self.calls.append("showClickPreviewRequested")

    def hide_click_preview(self, *args) -> None:
        self.calls.append("hideClickPreviewRequested")

    def show_approval_notification(self, *args) -> None:
        self.calls.append("showApprovalNotificationRequested")

    def cancel_approval_notification(self, *args) -> None:
        self.calls.append("cancelApprovalNotificationRequested")


class DesktopBridgeWiringTests(unittest.TestCase):
    def test_create_connected_pet_bridge_wires_all_handlers(self) -> None:
        module = importlib.import_module("app.desktop_bridge_wiring")
        owner = _Owner()
        bridge = _FakeBridge()
        bridge_factory_calls: list[str] = []

        def bridge_factory():
            bridge_factory_calls.append("called")
            return bridge

        connected_bridge = module.create_connected_pet_bridge(owner, bridge_factory)

        self.assertIs(connected_bridge, bridge)
        self.assertEqual(bridge_factory_calls, ["called"])
        self.assertEqual(
            [
                bridge.stateChanged.connected,
                bridge.interactiveRegionsChanged.connected,
                bridge.openSettingsRequested.connected,
                bridge.startAsrWarmupRequested.connected,
                bridge.minimizeWindowRequested.connected,
                bridge.closeWindowRequested.connected,
                bridge.showClickPreviewRequested.connected,
                bridge.hideClickPreviewRequested.connected,
                bridge.showApprovalNotificationRequested.connected,
                bridge.cancelApprovalNotificationRequested.connected,
            ],
            [
                [owner.on_web_state_changed],
                [owner.apply_interactive_regions],
                [owner.open_settings_page],
                [owner.request_asr_warmup],
                [owner.hide_desktop_pet],
                [owner.close],
                [owner.show_click_preview],
                [owner.hide_click_preview],
                [owner.show_approval_notification],
                [owner.cancel_approval_notification],
            ],
        )

    def test_main_init_delegates_bridge_setup(self) -> None:
        tree = _parse_source("main.py")
        desktop_pet_class = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DesktopPet"
        )
        desktop_pet_init = next(
            node for node in desktop_pet_class.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        init_source = ast.get_source_segment((ROOT_DIR / "main.py").read_text(encoding="utf-8"), desktop_pet_init) or ""

        self.assertIn("create_connected_pet_bridge(self, PetBridge)", init_source)
        self.assertNotIn("PetBridge()", init_source)
        self.assertNotIn("openSettingsRequested", init_source)
        self.assertNotIn("startAsrWarmupRequested", init_source)
        self.assertNotIn("minimizeWindowRequested", init_source)
        self.assertNotIn("closeWindowRequested", init_source)
        self.assertNotIn("showClickPreviewRequested", init_source)
        self.assertNotIn("hideClickPreviewRequested", init_source)


if __name__ == "__main__":
    unittest.main()
