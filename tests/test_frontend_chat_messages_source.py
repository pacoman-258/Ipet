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
CHAT_MESSAGES_JS = ROOT / "frontend" / "chat_messages.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendChatMessagesSourceTests(unittest.TestCase):
    def test_index_loads_chat_messages_between_submit_and_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        submit_script = '<script src="./frontend/chat_submit.js"></script>'
        messages_script = '<script src="./frontend/chat_messages.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(submit_script, source)
        self.assertIn(messages_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(submit_script), source.find(messages_script))
        self.assertLess(source.find(messages_script), source.find(app_script))

    def test_chat_messages_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(CHAT_MESSAGES_JS.exists(), "frontend/chat_messages.js should exist")
        source = CHAT_MESSAGES_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetChatMessages", source)
        self.assertIn("function createChatMessagesController", source)
        for name in (
            "findUserMessageById",
            "clearPendingRetryEdit",
            "beginUserMessageEditRetry",
            "truncateChatAfterMessage",
            "appendMessage",
            "updateMessageText",
            "appendToolList",
        ):
            self.assertIn(f"function {name}", source)

    def test_index_wires_chat_message_rendering_through_facade_registry(self) -> None:
        sections_source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")
        consumer_source = "\n".join((sections_source, app_sections_source))

        self.assertIn(
            "const chatMessagesController = runtimeWindow.IpetChatMessages.createChatMessagesController",
            sections_source,
        )
        self.assertIn("controllerRegistry.chatMessages = chatMessagesController;", sections_source)
        for snippet in (
            "getPendingRetryEdit: () => graphState.pendingRetryEdit",
            "setPendingRetryEdit: (value) => {",
            "getActiveApprovalId: () => graphState.activeApprovalId",
            "setActiveApprovalId: (value) => {",
            "getPendingApprovalInputTurnId: () => graphState.pendingApprovalInputTurnId",
            "setPendingApprovalInputTurnId: (value) => {",
            "getChatMessageSequence: () => graphState.chatMessageSequence",
            "setChatMessageSequence: (value) => {",
        ):
            self.assertIn(snippet, sections_source)

        for name in (
            "findUserMessageById",
            "clearPendingRetryEdit",
            "beginUserMessageEditRetry",
            "truncateChatAfterMessage",
            "appendMessage",
            "updateMessageText",
            "appendToolList",
        ):
            self.assertIn(f'"{name}": ["chatMessages", "{name}"],', facade_source)
            self.assertNotIn(f"function {name}", index_source)

        for facade_use in (
            "appendMessage: facade.appendMessage,",
            "appendToolList: facade.appendToolList,",
            "clearPendingRetryEdit: facade.clearPendingRetryEdit,",
            "truncateChatAfterMessage: facade.truncateChatAfterMessage,",
        ):
            self.assertIn(facade_use, consumer_source)

    def test_append_message_dom_body_no_longer_lives_in_index(self) -> None:
        source = INDEX_JS.read_text(encoding="utf-8")

        self.assertNotIn('const el = document.createElement("article");', source)
        self.assertNotIn('editButton.className = "msg-edit-button";', source)
        self.assertNotIn('editButton.addEventListener("click"', source)
        self.assertNotIn("chatMessagesEl.appendChild(el);", source)
        self.assertNotIn('const listEl = document.createElement("div");', source)


if __name__ == "__main__":
    unittest.main()
