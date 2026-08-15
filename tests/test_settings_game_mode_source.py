from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SettingsGameModeSourceTests(unittest.TestCase):
    def test_game_page_has_persistent_controls_and_runtime_status_actions(self) -> None:
        html = (ROOT / "settings.html").read_text(encoding="utf-8")
        form = (ROOT / "settings_form.js").read_text(encoding="utf-8")
        controller = (ROOT / "settings.js").read_text(encoding="utf-8")

        for element_id in (
            "game", "game-enabled", "game-category-combat", "game-category-growth",
            "game-category-route", "game-category-resources", "game-category-outcome",
            "game-min-reaction-interval-sec", "game-max-reactions-per-minute",
            "game-reaction-instruction", "game-refresh-btn", "game-clear-btn",
            "game-status-cards", "game-status-text",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('maxlength="500"', html)
        self.assertIn("min_reaction_interval_sec: intValue(els.gameMinReactionIntervalSec, 5)", form)
        self.assertIn("max_reactions_per_minute: intValue(els.gameMaxReactionsPerMinute, 6)", form)
        self.assertIn('reaction_instruction: String(els.gameReactionInstruction?.value || "").trim().slice(0, 500)', form)
        self.assertIn('fetchJson("/api/game/status")', controller)
        self.assertIn('fetchJson("/api/game/clear"', controller)
        self.assertIn("bridge_sequence", controller)
        self.assertIn("accepted_sequence", controller)


if __name__ == "__main__":
    unittest.main()
