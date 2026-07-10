from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_CHAT_SECTIONS_JS = ROOT / "frontend" / "controller_graph_chat_sections.js"
CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"
PET_STATE_NOTIFY_JS = ROOT / "frontend" / "pet_state_notify.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendPetStateNotifySourceTests(unittest.TestCase):
    def test_index_loads_pet_state_notify_near_scene_modules_before_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        scene_script = '<script src="./frontend/pet_scene.js"></script>'
        notify_script = '<script src="./frontend/pet_state_notify.js"></script>'
        config_script = '<script src="./frontend/app_config.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(scene_script, source)
        self.assertIn(notify_script, source)
        self.assertIn(config_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(scene_script), source.find(notify_script))
        self.assertLess(source.find(notify_script), source.find(config_script))
        self.assertLess(source.find(config_script), source.find(app_script))

    def test_pet_state_notify_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(PET_STATE_NOTIFY_JS.exists(), "frontend/pet_state_notify.js should exist")
        source = PET_STATE_NOTIFY_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetPetStateNotify", source)
        self.assertIn("function createPetStateNotifyController", source)
        for name in (
            "syncPetDisplayName",
            "speakerNameForRole",
            "scheduleNotifyState",
            "notifyStateNow",
        ):
            self.assertIn(f"{name}", source)
        self.assertIn("window.IpetPetStateNotify = Object.freeze", source)

    def test_pet_state_notify_module_owns_name_and_bridge_notification_behavior(self) -> None:
        self.assertTrue(PET_STATE_NOTIFY_JS.exists(), "frontend/pet_state_notify.js should exist")
        source = PET_STATE_NOTIFY_JS.read_text(encoding="utf-8")

        for snippet in (
            "parsePetNameFromPrompt(state.chat?.system_prompt || \"\")",
            "state.chat.pet_display_name = next;",
            "chatTitleEl.textContent = `${next}\\u5bf9\\u8bdd`;",
            "return helpers.speakerNameForRole(role, currentPetDisplayName());",
            "let notifyTimer = null;",
            "if (notifyTimer) {\n        return;\n      }",
            "notifyStateNow();\n      }, 80);",
            "const bridge = getQtBridge();",
            "typeof bridge.petStateChanged !== \"function\"",
            "offset_x: roundInt(state.offset_x),",
            "offset_y: roundInt(state.offset_y),",
            "bridge.petStateChanged(JSON.stringify(payload));",
        ):
            self.assertIn(snippet, source)

        payload_match = re.search(r"const payload = \{(?P<body>.*?)\n      \};", source, re.S)
        self.assertIsNotNone(payload_match)
        payload_body = payload_match.group("body")
        for field in ("scale", "offset_x", "offset_y", "rotation", "opacity"):
            self.assertIn(field, payload_body)

    def test_index_wires_pet_state_notify_actions_through_facade_registry(self) -> None:
        sections_source = CONTROLLER_GRAPH_SECTIONS_JS.read_text(encoding="utf-8")
        chat_sections_source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")
        consumer_source = "\n".join((sections_source, chat_sections_source, app_sections_source))

        self.assertIn(
            "const petStateNotifyController = runtimeWindow.IpetPetStateNotify.createPetStateNotifyController",
            sections_source,
        )
        self.assertIn("controllerRegistry.petStateNotify = petStateNotifyController;", sections_source)
        for name in ("syncPetDisplayName", "speakerNameForRole", "scheduleNotifyState", "notifyStateNow"):
            self.assertIn(f'"{name}": ["petStateNotify", "{name}"],', facade_source)
            self.assertIn(f"facade.{name}", consumer_source)
            self.assertNotIn(f"function {name}", index_source)

        self.assertNotIn("function parsePetNameFromPrompt", index_source)
        self.assertNotIn("let notifyTimer = null;", index_source)
        self.assertNotIn("qtBridge.petStateChanged(JSON.stringify(payload));", index_source)


if __name__ == "__main__":
    unittest.main()
