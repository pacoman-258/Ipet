from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
SETTINGS_HTML = ROOT / "settings.html"
SETTINGS_JS = ROOT / "settings.js"


class ChatModesUiTests(unittest.TestCase):
    def test_index_contains_chat_mode_buttons_and_payload(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('data-chat-mode="react"', source)
        self.assertIn('data-chat-mode="chat"', source)
        self.assertIn('data-chat-mode="skill"', source)
        self.assertIn("chat_mode: currentChatMode", source)
        self.assertIn("router_enabled: !!state.chat.router_enabled", source)
        self.assertIn('router_model: state.chat.router_model || ""', source)

    def test_index_contains_sidebar_buttons_in_new_order(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        settings_pos = source.find("<span>Settings</span>")
        history_pos = source.find("<span>Chat History</span>")
        new_chat_pos = source.find("<span>New Chat</span>")
        self.assertNotEqual(settings_pos, -1)
        self.assertNotEqual(history_pos, -1)
        self.assertNotEqual(new_chat_pos, -1)
        self.assertLess(settings_pos, history_pos)
        self.assertLess(history_pos, new_chat_pos)
        self.assertIn('id="nav-history"', source)
        self.assertIn('id="nav-chat"', source)

    def test_index_contains_topic_history_ui_and_api_hooks(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('id="chat-history-drawer"', source)
        self.assertIn('id="chat-history-list"', source)
        self.assertIn('id="chat-history-refresh"', source)
        self.assertIn('/api/chat/topics', source)
        self.assertIn("async function createNewTopic", source)
        self.assertIn("async function loadTopicHistory", source)
        self.assertIn("async function selectTopic", source)
        self.assertIn("topic_history_enabled", source)
        self.assertIn("topic_id", source)

    def test_settings_exposes_topic_history_controls_and_reasoning_input(self) -> None:
        html = SETTINGS_HTML.read_text(encoding="utf-8")
        js = SETTINGS_JS.read_text(encoding="utf-8")
        self.assertIn('id="chat-topic-history-enabled"', html)
        self.assertIn('id="chat-topic-history-summary-interval"', html)
        self.assertIn('id="chat-max-reasoning-steps"', html)
        self.assertNotIn('max="12"', html)
        self.assertIn("chatTopicHistoryEnabled", js)
        self.assertIn("chatTopicHistorySummaryInterval", js)
        self.assertIn("next.chat.topic_history", js)


if __name__ == "__main__":
    unittest.main()
