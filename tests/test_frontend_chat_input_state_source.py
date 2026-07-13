from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_CSS = ROOT / "frontend" / "index.css"
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_CHAT_SECTIONS_JS = ROOT / "frontend" / "controller_graph_chat_sections.js"
CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"
CHAT_INPUT_STATE_JS = ROOT / "frontend" / "chat_input_state.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendChatInputStateSourceTests(unittest.TestCase):
    def test_chat_input_shows_provider_token_counter(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        css = INDEX_CSS.read_text(encoding="utf-8")

        self.assertIn('id="chat-token-count"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn("Token --", html)
        self.assertIn("#chat-token-count", css)

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
        self.assertIn("function setChatTokenUsage", source)
        self.assertIn("function syncChatInputAvailability", source)
        self.assertIn("Object.freeze({", source)

    def test_chat_input_state_module_owns_availability_behavior(self) -> None:
        self.assertTrue(CHAT_INPUT_STATE_JS.exists(), "frontend/chat_input_state.js should exist")
        source = CHAT_INPUT_STATE_JS.read_text(encoding="utf-8")

        self.assertIn("const refs = deps.refs || {};", source)
        self.assertIn("const getChatState =", source)
        self.assertIn("const isAsrBusy =", source)
        self.assertIn('["idle", "stopped", "awaiting_followup_input"].includes(chatState)', source)
        self.assertIn("const asrBusy = isAsrBusy();", source)
        self.assertIn('const stoppable = ["streaming", "awaiting_approval", "stopping"].includes(chatState);', source)
        self.assertIn('chatSendEl.textContent = chatState === "stopping"', source)
        self.assertIn('chatInputEl.disabled = ["streaming", "stopping"].includes(chatState);', source)
        self.assertIn("chatInputEl.readOnly = asrBusy;", source)

    def test_token_counter_renders_exact_provider_usage(self) -> None:
        script = textwrap.dedent(
            """
            const fs = require("fs");
            const vm = require("vm");
            const source = fs.readFileSync(process.argv[1], "utf8");
            const input = { value: "" };
            const counter = { textContent: "" };
            const sandbox = { window: {} };
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: process.argv[1] });
            const controller = sandbox.window.IpetChatInputState.createChatInputStateController({
              refs: { chatInputEl: input, chatTokenCountEl: counter },
            });
            controller.setChatTokenUsage({ input_tokens: 17, output_tokens: 5, total_tokens: 22 });
            console.log(JSON.stringify({
              label: counter.textContent,
            }));
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(CHAT_INPUT_STATE_JS)],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)

        self.assertEqual(payload, {"label": "输入 17 · 输出 5 · 总计 22"})

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
        self.assertIn('"setChatTokenUsage": ["chatInputState", "setChatTokenUsage"],', facade_source)
        self.assertIn('"canSubmitChatInput": ["chatInputState", "canSubmitChatInput"],', facade_source)
        self.assertIn("chatTokenCountEl,", chat_sections_source)
        self.assertIn("syncChatInputAvailability: facade.syncChatInputAvailability,", chat_sections_source)
        self.assertIn("canSubmitChatInput: facade.canSubmitChatInput,", app_sections_source)
        self.assertNotIn("function syncChatInputAvailability", index_source)
        self.assertNotIn("function canSubmitChatInput", index_source)
        self.assertNotIn("chatSendEl.disabled", index_source)
        self.assertNotIn("chatInputEl.disabled", index_source)
        self.assertNotIn("chatInputEl.readOnly", index_source)


if __name__ == "__main__":
    unittest.main()
