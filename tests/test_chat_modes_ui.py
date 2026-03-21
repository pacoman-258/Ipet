from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"


class ChatModesUiTests(unittest.TestCase):
    def test_index_contains_chat_mode_buttons_and_payload(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("ReAct模式", source)
        self.assertIn("聊天模式", source)
        self.assertIn("Skill模式", source)
        self.assertIn('data-chat-mode="react"', source)
        self.assertIn('data-chat-mode="chat"', source)
        self.assertIn('data-chat-mode="skill"', source)
        self.assertIn("chat_mode: currentChatMode", source)
        self.assertIn("router_enabled: !!state.chat.router_enabled", source)
        self.assertIn("router_model: state.chat.router_model || \"\"", source)

    def test_index_blocks_skill_mode_without_active_skills(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("Skill模式至少需要启用一个技能", source)
        self.assertIn('currentChatMode === "chat"', source)
        self.assertIn("聊天模式不使用 Skills", source)

    def test_index_contains_shell_action_buttons(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('id="nav-settings"', source)
        self.assertIn('id="nav-chat"', source)
        self.assertIn('id="window-minimize"', source)
        self.assertIn('id="window-close"', source)


if __name__ == "__main__":
    unittest.main()
