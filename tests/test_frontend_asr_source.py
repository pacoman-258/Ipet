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
ASR_JS = ROOT / "frontend" / "asr.js"
ASR_AUDIO_WORKLET_JS = ROOT / "frontend" / "asr_audio_worklet.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendAsrSourceTests(unittest.TestCase):
    def test_index_loads_asr_module_between_helpers_and_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        helpers_script = '<script src="./frontend/index_helpers.js"></script>'
        asr_script = '<script src="./frontend/asr.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(helpers_script, source)
        self.assertIn(asr_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(helpers_script), source.find(asr_script))
        self.assertLess(source.find(asr_script), source.find(app_script))

    def test_asr_module_exposes_controller_namespace(self) -> None:
        source = ASR_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetAsr", source)
        self.assertIn("function createAsrController", source)
        for name in (
            "normalizeAsrConfig",
            "isProbablyMacOS",
            "defaultAsrStatusText",
            "candidateAsrBaseUrls",
            "buildAsrWebSocketUrl",
            "buildAsrWarmupUrl",
            "probeAsrAvailability",
            "requestAsrWarmup",
            "startPushToTalk",
            "stopPushToTalk",
        ):
            self.assertIn(f"function {name}", source)

    def test_asr_capture_uses_audio_worklet(self) -> None:
        source = ASR_JS.read_text(encoding="utf-8")
        worklet_source = ASR_AUDIO_WORKLET_JS.read_text(encoding="utf-8")

        self.assertNotIn("createScriptProcessor", source)
        self.assertIn("audioWorklet.addModule", source)
        self.assertIn("new AudioWorkletNodeCtor", source)
        self.assertIn("registerProcessor(\"ipet-asr-audio-processor\"", worklet_source)
        self.assertIn('event.key === "Option"', source)
        self.assertIn('event.key === "AltGraph"', source)
        self.assertIn('"awaiting_followup_input"', source)
        self.assertIn("asrFinalTimer", source)
        self.assertIn("ASR 识别超时，请检查 API Key、网络或 ASR 服务。", source)
        self.assertIn("if (ready) {", source)
        self.assertIn("handleAsrError(error);", source)

    def test_controller_facade_delegates_asr_work(self) -> None:
        chat_sections_source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        self.assertTrue(CONTROLLER_FACADE_JS.exists(), "frontend/controller_facade.js should exist")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")

        self.assertIn("asrController = runtimeWindow.IpetAsr.createAsrController", chat_sections_source)
        self.assertIn('"startPushToTalk": ["asr", "startPushToTalk"]', facade_source)
        self.assertIn('"stopPushToTalk": ["asr", "stopPushToTalk"]', facade_source)
        self.assertIn("startPushToTalk: facade.startPushToTalk", app_sections_source)
        self.assertIn("stopPushToTalk: facade.stopPushToTalk", app_sections_source)
        self.assertNotRegex(index_source, r"function startPushToTalk\(")
        self.assertNotRegex(index_source, r"function stopPushToTalk\(")
        self.assertNotIn("new WebSocket(buildAsrWebSocketUrl())", index_source)
        self.assertNotIn("navigator.mediaDevices.getUserMedia", index_source)
        self.assertNotIn("downsampleFloat32ToInt16Buffer(channelData", index_source)


if __name__ == "__main__":
    unittest.main()
