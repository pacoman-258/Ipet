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
CHAT_WORKLOG_JS = ROOT / "frontend" / "chat_worklog.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendChatWorklogSourceTests(unittest.TestCase):
    def test_index_loads_chat_worklog_between_speech_and_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        helpers_script = '<script src="./frontend/index_helpers.js"></script>'
        asr_script = '<script src="./frontend/asr.js"></script>'
        sidebars_script = '<script src="./frontend/chat_sidebars.js"></script>'
        speech_script = '<script src="./frontend/speech.js"></script>'
        worklog_script = '<script src="./frontend/chat_worklog.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(helpers_script, source)
        self.assertIn(asr_script, source)
        self.assertIn(sidebars_script, source)
        self.assertIn(speech_script, source)
        self.assertIn(worklog_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(helpers_script), source.find(asr_script))
        self.assertLess(source.find(asr_script), source.find(sidebars_script))
        self.assertLess(source.find(sidebars_script), source.find(speech_script))
        self.assertLess(source.find(speech_script), source.find(worklog_script))
        self.assertLess(source.find(worklog_script), source.find(app_script))

    def test_chat_worklog_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(CHAT_WORKLOG_JS.exists(), "frontend/chat_worklog.js should exist")
        source = CHAT_WORKLOG_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetChatWorklog", source)
        self.assertIn("function createChatWorklogController", source)
        for name in (
            "phaseLabel",
            "beginThoughtSession",
            "clearActiveThoughtSession",
            "ensureThoughtGroup",
            "removeEmptyPendingThoughtGroup",
            "appendThoughtPhase",
            "ensureAssistantWorklogTurn",
            "appendWorklogPhase",
            "finishWorklogProcess",
            "updateWorklogFinalText",
            "appendAssistantHistoryBlock",
            "appendApprovalBubble",
            "removeApprovalBubble",
            "resolveApprovalThoughtSessionId",
        ):
            self.assertIn(f"function {name}", source)

    def test_index_wires_worklog_rendering_through_facade_registry(self) -> None:
        sections_source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")
        consumer_source = "\n".join((sections_source, app_sections_source))

        self.assertIn(
            "const chatWorklogController = runtimeWindow.IpetChatWorklog.createChatWorklogController",
            sections_source,
        )
        self.assertIn("controllerRegistry.chatWorklog = chatWorklogController;", sections_source)
        for name in (
            "phaseLabel",
            "removeApprovalBubble",
            "resetChatTimelineState",
            "createThoughtSessionId",
            "beginThoughtSession",
            "clearActiveThoughtSession",
            "ensureThoughtGroup",
            "removeEmptyPendingThoughtGroup",
            "appendThoughtPhase",
            "ensureAssistantWorklogTurn",
            "appendWorklogPhase",
            "finishWorklogProcess",
            "updateWorklogFinalText",
            "appendAssistantHistoryBlock",
            "appendApprovalBubble",
        ):
            self.assertIn(f'"{name}": ["chatWorklog", "{name}"],', facade_source)
            self.assertNotIn(f"function {name}", index_source)

        for facade_use in (
            "phaseLabel: facade.phaseLabel,",
            "appendWorklogPhase: facade.appendWorklogPhase,",
            "finishWorklogProcess: facade.finishWorklogProcess,",
            "appendApprovalBubble: facade.appendApprovalBubble,",
            "appendAssistantHistoryBlock: facade.appendAssistantHistoryBlock,",
        ):
            self.assertIn(facade_use, consumer_source)
        self.assertNotIn("let assistantWorklogCounter = 0;", index_source)
        self.assertNotIn("const approvalBubbleRefs = new Map();", index_source)
        self.assertNotIn("function buildAssistantWorklogTurn", index_source)

    def test_worklog_uses_unified_execution_categories_timing_and_auto_collapse(self) -> None:
        source = CHAT_WORKLOG_JS.read_text(encoding="utf-8")

        for category in (
            "planning",
            "searching",
            "observing",
            "acting",
            "waiting_approval",
            "verifying",
            "blocked",
            "completed",
        ):
            self.assertIn(f'"{category}"', source)
        self.assertIn('processDetailsEl.className = "worklog-process";', source)
        self.assertIn("setInterval(() => {", source)
        self.assertIn("settleWorklogStatus", source)
        self.assertIn('target.processDetailsEl.open = false;', source)
        self.assertIn("Brain → Observe：", source)


if __name__ == "__main__":
    unittest.main()
