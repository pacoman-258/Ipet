from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_CHAT_SECTIONS_JS = ROOT / "frontend" / "controller_graph_chat_sections.js"
CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"
APP_REFS_JS = ROOT / "frontend" / "app_refs.js"


EXPECTED_IDS = [
    "window-background",
    "window-background-image",
    "status",
    "error",
    "canvas",
    "nav-settings",
    "nav-history",
    "nav-chat",
    "window-minimize",
    "window-close",
    "pet-context-menu",
    "pet-menu-chat",
    "pet-menu-temporary-chat",
    "pet-menu-history",
    "pet-menu-game",
    "pet-menu-game-label",
    "pet-menu-game-detail",
    "pet-menu-settings",
    "pet-menu-minimize",
    "pet-menu-close",
    "chat-panel",
    "chat-close",
    "chat-history-drawer",
    "chat-history-current",
    "chat-history-refresh",
    "chat-history-status",
    "chat-history-list",
    "chat-skills-toggle",
    "chat-skills-drawer",
    "chat-skills-refresh",
    "chat-skills-reset",
    "chat-skills-status",
    "chat-skills-list",
    "chat-messages",
    "chat-input",
    "chat-input-wrap",
    "chat-token-count",
    "chat-asr-status",
    "chat-send",
    "chat-stop",
    "chat-header",
    "chat-title",
    "chat-resizer-right",
    "chat-resizer-bottom",
    "chat-resizer-corner",
]

EXPECTED_SELECTORS = [
    ".chat-mode-btn[data-chat-mode]",
    ".chat-mode-btn[data-memory-mode]",
]

EXPECTED_REF_KEYS = [
    "backgroundLayerEl",
    "backgroundImageEl",
    "statusEl",
    "errorEl",
    "canvas",
    "navSettingsButtonEl",
    "navHistoryButtonEl",
    "navChatButtonEl",
    "windowMinimizeEl",
    "windowCloseEl",
    "petContextMenuEl",
    "petMenuChatEl",
    "petMenuTemporaryChatEl",
    "petMenuHistoryEl",
    "petMenuGameEl",
    "petMenuGameLabelEl",
    "petMenuGameDetailEl",
    "petMenuSettingsEl",
    "petMenuMinimizeEl",
    "petMenuCloseEl",
    "chatPanelEl",
    "chatCloseEl",
    "chatHistoryDrawerEl",
    "chatHistoryCurrentEl",
    "chatHistoryRefreshEl",
    "chatHistoryStatusEl",
    "chatHistoryListEl",
    "chatSkillsToggleEl",
    "chatModeButtons",
    "memoryModeButtons",
    "chatSkillsDrawerEl",
    "chatSkillsRefreshEl",
    "chatSkillsResetEl",
    "chatSkillsStatusEl",
    "chatSkillsListEl",
    "chatMessagesEl",
    "chatInputEl",
    "chatInputWrapEl",
    "chatTokenCountEl",
    "chatAsrStatusEl",
    "chatSendEl",
    "chatStopEl",
    "chatHeaderEl",
    "chatTitleEl",
    "chatResizerRightEl",
    "chatResizerBottomEl",
    "chatResizerCornerEl",
]


class FrontendAppRefsSourceTests(unittest.TestCase):
    def test_index_loads_app_refs_before_controllers_and_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        app_state_script = '<script src="./frontend/app_state.js"></script>'
        app_refs_script = '<script src="./frontend/app_refs.js"></script>'
        helpers_script = '<script src="./frontend/index_helpers.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(app_state_script, source)
        self.assertIn(app_refs_script, source)
        self.assertIn(helpers_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(app_state_script), source.find(app_refs_script))
        self.assertLess(source.find(app_refs_script), source.find(helpers_script))
        self.assertLess(source.find(app_refs_script), source.find(app_script))

    def test_app_refs_module_exposes_ref_collector_namespace(self) -> None:
        self.assertTrue(APP_REFS_JS.exists(), "frontend/app_refs.js should exist")
        source = APP_REFS_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetAppRefs", source)
        self.assertIn("function collectAppRefs", source)
        self.assertIn("window.IpetAppRefs = Object.freeze", source)
        self.assertIn("collectAppRefs", source)

    def test_collect_app_refs_reads_expected_ids_and_selectors(self) -> None:
        self.assertTrue(APP_REFS_JS.exists(), "frontend/app_refs.js should exist")
        script = textwrap.dedent(
            """
            const fs = require("fs");
            const vm = require("vm");
            const source = fs.readFileSync(process.argv[1], "utf8");
            const ids = [];
            const selectors = [];
            const fakeDocument = {
              getElementById(id) {
                ids.push(id);
                return { id };
              },
              querySelectorAll(selector) {
                selectors.push(selector);
                return [{ selector, index: 0 }, { selector, index: 1 }];
              },
            };
            const sandbox = { window: {} };
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: process.argv[1] });
            const refs = sandbox.window.IpetAppRefs.collectAppRefs(fakeDocument);
            console.log(JSON.stringify({
              ids,
              selectors,
              refKeys: Object.keys(refs),
              namespaceFrozen: Object.isFrozen(sandbox.window.IpetAppRefs),
              chatModeButtonsAreArray: Array.isArray(refs.chatModeButtons),
              memoryModeButtonsAreArray: Array.isArray(refs.memoryModeButtons),
              backgroundLayerId: refs.backgroundLayerEl.id,
              chatInputId: refs.chatInputEl.id,
              chatModeButtonCount: refs.chatModeButtons.length,
              memoryModeButtonCount: refs.memoryModeButtons.length,
            }));
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(APP_REFS_JS)],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)

        self.assertEqual(payload["ids"], EXPECTED_IDS)
        self.assertEqual(payload["selectors"], EXPECTED_SELECTORS)
        self.assertEqual(payload["refKeys"], EXPECTED_REF_KEYS)
        self.assertTrue(payload["namespaceFrozen"])
        self.assertTrue(payload["chatModeButtonsAreArray"])
        self.assertTrue(payload["memoryModeButtonsAreArray"])
        self.assertEqual(payload["backgroundLayerId"], "window-background")
        self.assertEqual(payload["chatInputId"], "chat-input")
        self.assertEqual(payload["chatModeButtonCount"], 2)
        self.assertEqual(payload["memoryModeButtonCount"], 2)

    def test_index_uses_app_refs_instead_of_inline_dom_queries(self) -> None:
        graph_source = CONTROLLER_GRAPH_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")

        self.assertIn("const refs = deps.refs || runtimeWindow.IpetAppRefs.collectAppRefs(runtimeDocument);", graph_source)
        self.assertNotIn("document.getElementById", index_source)
        self.assertNotIn("document.querySelectorAll", index_source)

    def test_index_keeps_controller_ref_injection_semantics(self) -> None:
        sections_source = CONTROLLER_GRAPH_SECTIONS_JS.read_text(encoding="utf-8")
        chat_sections_source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")

        for snippet in (
            "const shellVisualsController = runtimeWindow.IpetShellVisuals.createShellVisualsController",
        ):
            self.assertIn(snippet, sections_source)

        for snippet in (
            "const chatInputStateController = runtimeWindow.IpetChatInputState.createChatInputStateController",
            "const chatModesController = runtimeWindow.IpetChatModes.createChatModesController",
            "chatModeButtons,",
            "memoryModeButtons,",
            "chatResizerCornerEl,",
        ):
            self.assertIn(snippet, chat_sections_source)

        for snippet in (
            "chatHistoryRefreshEl,",
            "chatSkillsResetEl,",
        ):
            self.assertIn(snippet, app_sections_source)


if __name__ == "__main__":
    unittest.main()
