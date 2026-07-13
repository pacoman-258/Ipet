from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"
SPEECH_JS = ROOT / "frontend" / "speech.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendSpeechSourceTests(unittest.TestCase):
    def test_index_loads_speech_module_between_sidebars_and_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        helpers_script = '<script src="./frontend/index_helpers.js"></script>'
        asr_script = '<script src="./frontend/asr.js"></script>'
        sidebars_script = '<script src="./frontend/chat_sidebars.js"></script>'
        speech_script = '<script src="./frontend/speech.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(helpers_script, source)
        self.assertIn(asr_script, source)
        self.assertIn(sidebars_script, source)
        self.assertIn(speech_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(helpers_script), source.find(asr_script))
        self.assertLess(source.find(asr_script), source.find(sidebars_script))
        self.assertLess(source.find(sidebars_script), source.find(speech_script))
        self.assertLess(source.find(speech_script), source.find(app_script))

    def test_speech_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(SPEECH_JS.exists(), "frontend/speech.js should exist")
        source = SPEECH_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetSpeech", source)
        self.assertIn("function createSpeechController", source)
        for name in (
            "stopSpeaking",
            "setMouthOpen",
            "resolveExpressionName",
            "triggerExpressionSafe",
            "stopLipSyncLoop",
            "startLipSync",
            "fallbackSpeakByBrowser",
            "enqueueTTSChunk",
            "feedSpeakBuffer",
            "playNextTTSChunk",
        ):
            self.assertIn(f"function {name}", source)

    def test_qwen_tts_failure_uses_a_concise_unavailable_message(self) -> None:
        source = SPEECH_JS.read_text(encoding="utf-8")
        self.assertIn('state.chat?.tts_provider === "qwen_tts_local"', source)
        self.assertIn('return "TTS异常";', source)

    def test_speech_starts_per_sentence_and_prepares_the_next_audio_while_playing(self) -> None:
        source = SPEECH_JS.read_text(encoding="utf-8")
        self.assertIn("const completedSentence = /[^。！？!?；;\\n]*[。！？!?；;\\n]+", source)
        self.assertIn("function requestNextTTSChunk", source)
        self.assertIn("let preparedAudioQueue = [];", source)
        self.assertIn("requestNextTTSChunk();", source)
        self.assertIn("preparedAudioQueue.push(prepared);", source)

    def test_index_wires_speech_work_through_facade_registry(self) -> None:
        sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")

        self.assertIn("const speechController = runtimeWindow.IpetSpeech.createSpeechController", sections_source)
        self.assertIn("controllerRegistry.speech = speechController;", sections_source)
        for name in (
            "stopSpeaking",
            "setMouthOpen",
            "resolveExpressionName",
            "triggerExpressionSafe",
            "stopLipSyncLoop",
            "startLipSync",
            "fallbackSpeakByBrowser",
            "enqueueTTSChunk",
            "feedSpeakBuffer",
            "playNextTTSChunk",
        ):
            self.assertIn(f'"{name}": ["speech", "{name}"],', facade_source)
            self.assertNotIn(f"function {name}", index_source)

        for facade_use in (
            "stopSpeaking: facade.stopSpeaking,",
            "feedSpeakBuffer: facade.feedSpeakBuffer,",
            "enqueueTTSChunk: facade.enqueueTTSChunk,",
        ):
            self.assertIn(facade_use, sections_source)
        self.assertNotIn("let activeAudio = null;", index_source)
        self.assertNotIn("let ttsQueue = [];", index_source)
        self.assertNotIn("new Audio(blobUrl)", index_source)
        self.assertNotIn('fetch(`${backend}/api/tts`', index_source)


if __name__ == "__main__":
    unittest.main()
