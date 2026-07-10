from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_CHAT_SECTIONS_JS = ROOT / "frontend" / "controller_graph_chat_sections.js"
CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"
CHAT_INPUT_STATE_JS = ROOT / "frontend" / "chat_input_state.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendChatInputStateSourceTests(unittest.TestCase):
    def test_index_loads_chat_input_state_between_modes_and_asr(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        chat_modes_script = '<script src="./frontend/chat_modes.js"></script>'
        chat_input_state_script = '<script src="./frontend/chat_input_state.js"></script>'
        asr_script = '<script src="./frontend/asr.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(chat_modes_script, source)
        self.assertIn(chat_input_state_script, source)
        self.assertIn(asr_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(chat_modes_script), source.find(chat_input_state_script))
        self.assertLess(source.find(chat_input_state_script), source.find(asr_script))
        self.assertLess(source.find(asr_script), source.find(app_script))

    def test_chat_input_state_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(CHAT_INPUT_STATE_JS.exists(), "frontend/chat_input_state.js should exist")
        source = CHAT_INPUT_STATE_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetChatInputState", source)
        self.assertIn("function createChatInputStateController", source)
        self.assertIn("function canSubmitChatInput", source)
        self.assertIn("function syncChatInputAvailability", source)
        self.assertIn("Object.freeze({", source)

    def test_chat_input_state_module_owns_availability_behavior(self) -> None:
        self.assertTrue(CHAT_INPUT_STATE_JS.exists(), "frontend/chat_input_state.js should exist")
        source = CHAT_INPUT_STATE_JS.read_text(encoding="utf-8")

        self.assertIn("const refs = deps.refs || {};", source)
        self.assertIn("const getChatState =", source)
        self.assertIn("const isAsrBusy =", source)
        self.assertIn('chatState === "idle" || chatState === "awaiting_followup_input"', source)
        self.assertIn("const asrBusy = isAsrBusy();", source)
        self.assertIn("chatSendEl.disabled = !isSubmittableChatState(chatState) || asrBusy;", source)
        self.assertIn('chatInputEl.disabled = chatState === "streaming";', source)
        self.assertIn("chatInputEl.readOnly = asrBusy;", source)

    def test_index_wires_chat_input_state_through_facade_registry(self) -> None:
        chat_sections_source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")

        self.assertIn(
            "const chatInputStateController = runtimeWindow.IpetChatInputState.createChatInputStateController",
            chat_sections_source,
        )
        self.assertIn("controllerRegistry.chatInputState = chatInputStateController;", chat_sections_source)
        self.assertIn('"syncChatInputAvailability": ["chatInputState", "syncChatInputAvailability"],', facade_source)
        self.assertIn('"canSubmitChatInput": ["chatInputState", "canSubmitChatInput"],', facade_source)
        self.assertIn("syncChatInputAvailability: facade.syncChatInputAvailability,", chat_sections_source)
        self.assertIn("canSubmitChatInput: facade.canSubmitChatInput,", app_sections_source)
        self.assertNotIn("function syncChatInputAvailability", index_source)
        self.assertNotIn("function canSubmitChatInput", index_source)
        self.assertNotIn("chatSendEl.disabled", index_source)
        self.assertNotIn("chatInputEl.disabled", index_source)
        self.assertNotIn("chatInputEl.readOnly", index_source)


if __name__ == "__main__":
    unittest.main()
