from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"
CHAT_STREAM_JS = ROOT / "frontend" / "chat_stream.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendChatStreamSourceTests(unittest.TestCase):
    def test_index_loads_chat_stream_between_worklog_and_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        helpers_script = '<script src="./frontend/index_helpers.js"></script>'
        asr_script = '<script src="./frontend/asr.js"></script>'
        sidebars_script = '<script src="./frontend/chat_sidebars.js"></script>'
        speech_script = '<script src="./frontend/speech.js"></script>'
        worklog_script = '<script src="./frontend/chat_worklog.js"></script>'
        stream_script = '<script src="./frontend/chat_stream.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(helpers_script, source)
        self.assertIn(asr_script, source)
        self.assertIn(sidebars_script, source)
        self.assertIn(speech_script, source)
        self.assertIn(worklog_script, source)
        self.assertIn(stream_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(helpers_script), source.find(asr_script))
        self.assertLess(source.find(asr_script), source.find(sidebars_script))
        self.assertLess(source.find(sidebars_script), source.find(speech_script))
        self.assertLess(source.find(speech_script), source.find(worklog_script))
        self.assertLess(source.find(worklog_script), source.find(stream_script))
        self.assertLess(source.find(stream_script), source.find(app_script))

    def test_chat_stream_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(CHAT_STREAM_JS.exists(), "frontend/chat_stream.js should exist")
        source = CHAT_STREAM_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetChatStream", source)
        self.assertIn("function createChatStreamController", source)
        self.assertIn("async function consumeChatStream", source)
        self.assertIn("function parseServerSentEvent", source)
        self.assertIn("function dispatchChatStreamEvent", source)
        self.assertIn("reader.read()", source)
        self.assertIn('buffer.split("\\n\\n")', source)
        self.assertIn("JSON.parse(dataText)", source)

    def test_index_wires_stream_consumption_through_facade_registry(self) -> None:
        sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")

        self.assertIn(
            "const chatStreamController = runtimeWindow.IpetChatStream.createChatStreamController",
            sections_source,
        )
        self.assertIn("controllerRegistry.chatStream = chatStreamController;", sections_source)
        self.assertIn('"consumeChatStream": ["chatStream", "consumeChatStream"],', facade_source)
        self.assertIn("consumeChatStream: facade.consumeChatStream,", sections_source)
        self.assertNotIn("function consumeChatStream", index_source)
        self.assertIn("getReceivedStructuredSegment", sections_source)
        self.assertIn("setReceivedStructuredSegment", sections_source)
        self.assertNotIn("const decoder = new TextDecoder();", index_source)
        self.assertNotIn("const reader = resp.body.getReader();", index_source)
        self.assertNotIn('buffer.split("\\n\\n")', index_source)


if __name__ == "__main__":
    unittest.main()
