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
CHAT_MODES_JS = ROOT / "frontend" / "chat_modes.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendChatModesSourceTests(unittest.TestCase):
    def test_index_loads_chat_modes_after_helpers_and_before_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        helpers_script = '<script src="./frontend/index_helpers.js"></script>'
        chat_modes_script = '<script src="./frontend/chat_modes.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(helpers_script, source)
        self.assertIn(chat_modes_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(helpers_script), source.find(chat_modes_script))
        self.assertLess(source.find(chat_modes_script), source.find(app_script))

    def test_chat_modes_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(CHAT_MODES_JS.exists(), "frontend/chat_modes.js should exist")
        source = CHAT_MODES_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetChatModes", source)
        self.assertIn("function createChatModesController", source)
        for name in (
            "getCurrentChatMode",
            "setCurrentChatMode",
            "getCurrentMemoryMode",
            "setCurrentMemoryMode",
            "setChatMode",
            "setMemoryMode",
            "applyChatModeUI",
            "applyMemoryModeUI",
        ):
            self.assertIn(name, source)
        self.assertIn("window.IpetChatModes = Object.freeze", source)

    def test_index_wires_chat_modes_through_facade_registry(self) -> None:
        sections_source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")
        consumer_source = "\n".join((sections_source, app_sections_source))

        self.assertIn(
            "const chatModesController = runtimeWindow.IpetChatModes.createChatModesController",
            sections_source,
        )
        self.assertIn("controllerRegistry.chatModes = chatModesController;", sections_source)
        for name in (
            "getCurrentChatMode",
            "setCurrentChatMode",
            "getCurrentMemoryMode",
            "setCurrentMemoryMode",
            "setChatMode",
            "setMemoryMode",
            "applyChatModeUI",
            "applyMemoryModeUI",
        ):
            self.assertIn(f'"{name}": ["chatModes", "{name}"],', facade_source)
            self.assertNotIn(f"function {name}", index_source)

        for facade_use in (
            "getCurrentChatMode: facade.getCurrentChatMode,",
            "getCurrentMemoryMode: facade.getCurrentMemoryMode,",
            "setCurrentChatMode: facade.setCurrentChatMode,",
            "setCurrentMemoryMode: facade.setCurrentMemoryMode,",
            "setChatMode: facade.setChatMode,",
            "setMemoryMode: facade.setMemoryMode,",
            "applyChatModeUI: facade.applyChatModeUI,",
            "applyMemoryModeUI: facade.applyMemoryModeUI,",
        ):
            self.assertIn(facade_use, consumer_source)

    def test_index_no_longer_inlines_mode_ui_body(self) -> None:
        source = INDEX_JS.read_text(encoding="utf-8")

        for function_name in (
            "getCurrentChatMode",
            "setCurrentChatMode",
            "getCurrentMemoryMode",
            "setCurrentMemoryMode",
            "setChatMode",
            "setMemoryMode",
            "applyChatModeUI",
            "applyMemoryModeUI",
        ):
            self.assertNotIn(f"function {function_name}", source)

        self.assertNotIn("let currentChatMode =", source)
        self.assertNotIn("let currentMemoryMode =", source)
        self.assertNotIn('chatSkillsToggleEl.title = skillsEnabled ? "管理当前会话技能" : "聊天模式不使用技能";', source)
        self.assertNotIn('setChatSkillsStatus("当前是聊天模式，只允许联网搜索工具，不使用技能。");', source)
        self.assertNotIn("setChatSkillsStatus(`技能模式当前启用", source)
        self.assertNotIn("setChatSkillsStatus(`当前会话已选择", source)

    def test_chat_modes_module_keeps_required_behavior(self) -> None:
        source = CHAT_MODES_JS.read_text(encoding="utf-8")

        for snippet in (
            'let currentChatMode = "react";',
            'let currentMemoryMode = "persistent";',
            "currentChatMode = normalizeChatMode(mode);",
            "currentMemoryMode = normalizeMemoryMode(mode);",
            "renderTopicHistoryList();",
            "updateShellButtons();",
            "for (const button of chatModeButtons)",
            "for (const button of memoryModeButtons)",
            'chatSkillsToggleEl.title = skillsEnabled ? "管理当前会话技能" : "聊天模式不使用技能";',
            'chatSkillsDrawerEl.classList.remove("open");',
            'setChatSkillsStatus("当前是聊天模式，只允许联网搜索工具，不使用技能。");',
            "setChatSkillsStatus(`技能模式当前启用 ${activeChatSkillIds().length} 个技能。`);",
            "chatSidebarsController.chatSkillsLoaded()",
            "setChatSkillsStatus(`当前会话已选择 ${activeChatSkillIds().length} 个技能。`);",
            "syncTopicIndicator();",
        ):
            self.assertIn(snippet, source)


if __name__ == "__main__":
    unittest.main()
