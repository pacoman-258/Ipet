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
APP_BOOTSTRAP_JS = ROOT / "frontend" / "app_bootstrap.js"
CHAT_PANEL_JS = ROOT / "frontend" / "chat_panel.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendChatPanelSourceTests(unittest.TestCase):
    def test_index_loads_chat_panel_between_messages_and_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        messages_script = '<script src="./frontend/chat_messages.js"></script>'
        panel_script = '<script src="./frontend/chat_panel.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(messages_script, source)
        self.assertIn(panel_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(messages_script), source.find(panel_script))
        self.assertLess(source.find(panel_script), source.find(app_script))

    def test_chat_panel_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(CHAT_PANEL_JS.exists(), "frontend/chat_panel.js should exist")
        source = CHAT_PANEL_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetChatPanel", source)
        self.assertIn("function createChatPanelController", source)
        for name in (
            "loadChatPanelState",
            "saveChatPanelState",
            "setChatState",
            "openChat",
            "closeChat",
            "installChatPanelInteractions",
        ):
            self.assertIn(f"function {name}", source)

    def test_chat_panel_module_owns_storage_clamp_and_open_close_behavior(self) -> None:
        self.assertTrue(CHAT_PANEL_JS.exists(), "frontend/chat_panel.js should exist")
        source = CHAT_PANEL_JS.read_text(encoding="utf-8")

        self.assertIn('const STORAGE_KEY = "desktopPet.chatPanel";', source)
        self.assertIn("runtimeWindow.localStorage.getItem(STORAGE_KEY)", source)
        self.assertIn("runtimeWindow.localStorage.setItem(STORAGE_KEY", source)
        self.assertIn("Math.min(", source)
        self.assertIn("Math.max(", source)
        self.assertIn('chatPanelEl.classList.add("open");', source)
        self.assertIn('chatPanelEl.classList.remove("open");', source)
        self.assertIn('chatHistoryDrawerEl.classList.remove("open");', source)
        self.assertIn("loadChatSkills(false);", source)
        self.assertIn("renderTopicHistoryList();", source)
        self.assertIn("saveChatPanelState();", source)

    def test_index_wires_chat_panel_actions_through_facade_registry(self) -> None:
        chat_sections_source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")

        self.assertIn(
            "const chatPanelController = runtimeWindow.IpetChatPanel.createChatPanelController",
            chat_sections_source,
        )
        self.assertIn("controllerRegistry.chatPanel = chatPanelController;", chat_sections_source)
        for name in ("loadChatPanelState", "saveChatPanelState", "setChatState", "openChat", "closeChat"):
            self.assertIn(f'"{name}": ["chatPanel", "{name}"],', facade_source)
            self.assertNotIn(f"function {name}", index_source)

        for facade_use in (
            "setChatState: facade.setChatState,",
            "openChat: facade.openChat,",
            "closeChat: facade.closeChat,",
        ):
            self.assertIn(facade_use, "\n".join((chat_sections_source, app_sections_source)))
        self.assertNotIn("chatPanelController.installChatPanelInteractions();", index_source)
        self.assertNotIn("window.localStorage.getItem(\"desktopPet.chatPanel\")", index_source)
        self.assertNotIn("window.localStorage.setItem(\"desktopPet.chatPanel\"", index_source)
        self.assertNotIn('chatPanelEl.classList.add("open");', index_source)
        self.assertNotIn('chatPanelEl.classList.remove("open");', index_source)

    def test_app_bootstrap_installs_chat_panel_interactions(self) -> None:
        source = APP_BOOTSTRAP_JS.read_text(encoding="utf-8")

        self.assertIn("const chatPanelController = deps.chatPanelController || {};", source)
        match = re.search(
            r"function installEventListeners\(\) \{(?P<body>.*?)\n    \}",
            source,
            re.S,
        )
        self.assertIsNotNone(match)
        self.assertIn("chatPanelController.installChatPanelInteractions();", match.group("body"))


if __name__ == "__main__":
    unittest.main()
