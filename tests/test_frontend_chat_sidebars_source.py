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
CHAT_SIDEBARS_JS = ROOT / "frontend" / "chat_sidebars.js"
CHAT_TOPIC_HISTORY_JS = ROOT / "frontend" / "chat_topic_history.js"
CHAT_SKILLS_JS = ROOT / "frontend" / "chat_skills.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendChatSidebarsSourceTests(unittest.TestCase):
    def test_index_loads_chat_sidebars_between_asr_and_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        helpers_script = '<script src="./frontend/index_helpers.js"></script>'
        asr_script = '<script src="./frontend/asr.js"></script>'
        topic_history_script = '<script src="./frontend/chat_topic_history.js"></script>'
        skills_script = '<script src="./frontend/chat_skills.js"></script>'
        sidebars_script = '<script src="./frontend/chat_sidebars.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(helpers_script, source)
        self.assertIn(asr_script, source)
        self.assertIn(topic_history_script, source)
        self.assertIn(skills_script, source)
        self.assertIn(sidebars_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(helpers_script), source.find(asr_script))
        self.assertLess(source.find(asr_script), source.find(topic_history_script))
        self.assertLess(source.find(topic_history_script), source.find(skills_script))
        self.assertLess(source.find(skills_script), source.find(sidebars_script))
        self.assertLess(source.find(sidebars_script), source.find(app_script))

    def test_chat_sidebar_modules_own_separate_responsibilities(self) -> None:
        self.assertTrue(CHAT_SIDEBARS_JS.exists(), "frontend/chat_sidebars.js should exist")
        self.assertTrue(CHAT_TOPIC_HISTORY_JS.exists(), "frontend/chat_topic_history.js should exist")
        self.assertTrue(CHAT_SKILLS_JS.exists(), "frontend/chat_skills.js should exist")
        sidebars_source = CHAT_SIDEBARS_JS.read_text(encoding="utf-8")
        history_source = CHAT_TOPIC_HISTORY_JS.read_text(encoding="utf-8")
        skills_source = CHAT_SKILLS_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetChatSidebars", sidebars_source)
        self.assertIn("function createChatSidebarsController", sidebars_source)
        self.assertIn("window.IpetChatTopicHistory", history_source)
        self.assertIn("function createChatTopicHistoryController", history_source)
        self.assertIn("window.IpetChatSkills", skills_source)
        self.assertIn("function createChatSkillsController", skills_source)
        for name in (
            "topicHistoryEnabled",
            "setChatHistoryStatus",
            "normalizeTopicRecord",
            "currentTopicLabel",
            "syncTopicIndicator",
            "replaceChatMessages",
            "ensureTopicReady",
            "loadTopicHistory",
            "toggleChatHistoryDrawer",
        ):
            self.assertIn(f"function {name}", history_source)
            self.assertNotIn(f"function {name}", sidebars_source)
        for name in (
            "activeChatSkillIds",
            "loadChatSkills",
            "renderChatSkillDrawer",
            "toggleChatSkillsDrawer",
        ):
            self.assertIn(f"function {name}", skills_source)
            self.assertNotIn(f"function {name}", sidebars_source)
        self.assertIn("IpetChatTopicHistory.createChatTopicHistoryController", sidebars_source)
        self.assertIn("IpetChatSkills.createChatSkillsController", sidebars_source)
        for state_declaration in (
            "let topicHistoryItems",
            "let currentTopicId",
            "let pendingDeleteTopicTimer",
            "let chatSkillCatalog",
            "let chatSkillDefaultIds",
            "let chatSelectedSkillIds",
        ):
            self.assertNotIn(state_declaration, sidebars_source)

    def test_controller_facade_delegates_sidebar_work(self) -> None:
        sections_source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        self.assertTrue(CONTROLLER_FACADE_JS.exists(), "frontend/controller_facade.js should exist")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")

        self.assertIn(
            "const chatSidebarsController = runtimeWindow.IpetChatSidebars.createChatSidebarsController",
            sections_source,
        )
        for mapping in (
            '"ensureTopicReady": ["chatSidebars", "ensureTopicReady"]',
            '"activeChatSkillIds": ["chatSidebars", "activeChatSkillIds"]',
            '"loadTopicHistory": ["chatSidebars", "loadTopicHistory"]',
            '"loadChatSkills": ["chatSidebars", "loadChatSkills"]',
        ):
            self.assertIn(mapping, facade_source)
        self.assertIn("loadTopicHistory: facade.loadTopicHistory", app_sections_source)
        self.assertIn("loadChatSkills: facade.loadChatSkills", app_sections_source)
        for function_name in ("ensureTopicReady", "activeChatSkillIds", "loadTopicHistory", "loadChatSkills"):
            self.assertNotRegex(index_source, rf"function {function_name}\(")
        self.assertNotIn("let topicHistoryItems = [];", index_source)
        self.assertNotIn("let chatSkillCatalog = [];", index_source)


if __name__ == "__main__":
    unittest.main()
