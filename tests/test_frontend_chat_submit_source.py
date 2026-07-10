from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_CHAT_SECTIONS_JS = ROOT / "frontend" / "controller_graph_chat_sections.js"
CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"
CHAT_SUBMIT_JS = ROOT / "frontend" / "chat_submit.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendChatSubmitSourceTests(unittest.TestCase):
    def test_chat_submit_module_owns_submit_and_stream_actions(self) -> None:
        self.assertTrue(CHAT_SUBMIT_JS.exists(), "frontend/chat_submit.js should exist")
        source = CHAT_SUBMIT_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetChatSubmit", source)
        self.assertIn("function createChatSubmitController", source)
        self.assertIn("async function continueApproval", source)
        self.assertIn("async function streamChat", source)
        self.assertIn("async function submitChatInput", source)
        self.assertIn("fetch(`${backend}/api/chat/stream`", source)
        self.assertIn("/api/human-ops/proposals/", source)
        self.assertIn("retry_from_assistant_turn: retryFromAssistantTurn,", source)
        self.assertIn("getPendingRetryEdit", source)
        self.assertIn("setReceivedStructuredSegment", source)

    def test_index_wires_chat_submit_through_facade_registry(self) -> None:
        chat_sections_source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")

        self.assertIn(
            "graphState.chatSubmitController = runtimeWindow.IpetChatSubmit.createChatSubmitController",
            app_sections_source,
        )
        self.assertIn("controllerRegistry.chatSubmit = graphState.chatSubmitController;", app_sections_source)
        for name in ("continueApproval", "streamChat", "submitChatInput"):
            self.assertIn(f'"{name}": ["chatSubmit", "{name}"],', facade_source)
            self.assertNotIn(f"function {name}", index_source)

        for facade_use in (
            "continueApproval: facade.continueApproval,",
            "streamChat: facade.streamChat,",
            "submitChatInput: facade.submitChatInput,",
        ):
            self.assertIn(facade_use, "\n".join((chat_sections_source, app_sections_source)))
        self.assertNotIn("fetch(`${backend}/api/chat/stream`", index_source)
        self.assertNotIn("retry_from_assistant_turn: retryFromAssistantTurn,", index_source)


if __name__ == "__main__":
    unittest.main()
