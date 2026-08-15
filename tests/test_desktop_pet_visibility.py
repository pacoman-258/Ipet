from __future__ import annotations

import unittest

from app.desktop_pet_visibility import DesktopPetVisibilityController, deactivate_macos_application


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def stop(self) -> None:
        self.calls.append("stop")

    def apply_config(self, config) -> None:
        self.calls.append(("apply_config", config))


class _Page:
    def __init__(self, calls) -> None:
        self.calls = calls

    def runJavaScript(self, script: str) -> None:
        self.calls.append(("javascript", script))


class _Browser:
    def __init__(self, calls) -> None:
        self._page = _Page(calls)

    def page(self):
        return self._page


class _Owner:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.config = {"environment": {"mode": "active"}}
        self.vision_controller = _Recorder()
        self.environment_controller = _Recorder()
        self.browser = _Browser(self.calls)

    def hide(self) -> None:
        self.calls.append("hide")

    def show(self) -> None:
        self.calls.append("show")

    def raise_(self) -> None:
        self.calls.append("raise")

    def activateWindow(self) -> None:
        self.calls.append("activate")


class DesktopPetVisibilityTests(unittest.TestCase):
    def test_hide_pauses_presence_and_closes_frontend_chat(self) -> None:
        owner = _Owner()
        deactivations: list[str] = []
        controller = DesktopPetVisibilityController(
            owner,
            deactivate_application=lambda: deactivations.append("deactivate"),
        )

        self.assertTrue(controller.hide())
        self.assertTrue(controller.hidden)
        self.assertEqual(owner.vision_controller.calls, ["stop"])
        self.assertEqual(owner.environment_controller.calls, ["stop"])
        self.assertEqual(owner.calls[-1], "hide")
        self.assertIn("prepareForDesktopHide", owner.calls[0][1])
        self.assertEqual(deactivations, ["deactivate"])
        self.assertFalse(controller.hide())

    def test_non_macos_deactivation_is_a_noop(self) -> None:
        self.assertFalse(deactivate_macos_application("linux"))

    def test_application_activation_restores_only_the_pet_and_presence(self) -> None:
        owner = _Owner()
        controller = DesktopPetVisibilityController(owner)
        controller.hide()
        owner.calls.clear()

        self.assertTrue(controller.on_application_activated())
        self.assertFalse(controller.hidden)
        self.assertEqual(owner.calls[:3], ["show", "raise", "activate"])
        self.assertIn("restoreFromDesktopHide", owner.calls[-1][1])
        expected = [("apply_config", owner.config)]
        self.assertEqual(owner.vision_controller.calls[-1:], expected)
        self.assertEqual(owner.environment_controller.calls[-1:], expected)
        self.assertFalse(controller.on_application_activated())


if __name__ == "__main__":
    unittest.main()
