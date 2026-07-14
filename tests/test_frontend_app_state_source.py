from __future__ import annotations

import json
import re
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
APP_STATE_JS = ROOT / "frontend" / "app_state.js"


class FrontendAppStateSourceTests(unittest.TestCase):
    def test_index_loads_app_state_before_frontend_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        app_state_script = '<script src="./frontend/app_state.js"></script>'
        helpers_script = '<script src="./frontend/index_helpers.js"></script>'
        config_script = '<script src="./frontend/app_config.js"></script>'
        bootstrap_script = '<script src="./frontend/app_bootstrap.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(app_state_script, source)
        self.assertIn(helpers_script, source)
        self.assertIn(config_script, source)
        self.assertIn(bootstrap_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(app_state_script), source.find(helpers_script))
        self.assertLess(source.find(app_state_script), source.find(config_script))
        self.assertLess(source.find(config_script), source.find(bootstrap_script))
        self.assertLess(source.find(bootstrap_script), source.find(app_script))

    def test_app_state_module_exposes_default_state_factory(self) -> None:
        self.assertTrue(APP_STATE_JS.exists(), "frontend/app_state.js should exist")
        source = APP_STATE_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetAppState", source)
        self.assertIn("function createDefaultAppState()", source)
        self.assertIn("window.IpetAppState = Object.freeze", source)
        self.assertIn("createDefaultAppState", source)

    def test_app_state_factory_returns_independent_default_state(self) -> None:
        self.assertTrue(APP_STATE_JS.exists(), "frontend/app_state.js should exist")
        script = textwrap.dedent(
            """
            const fs = require("fs");
            const vm = require("vm");
            const source = fs.readFileSync(process.argv[1], "utf8");
            const sandbox = { window: {} };
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: process.argv[1] });
            const stateA = sandbox.window.IpetAppState.createDefaultAppState();
            const stateB = sandbox.window.IpetAppState.createDefaultAppState();
            console.log(JSON.stringify({
              namespaceFrozen: Object.isFrozen(sandbox.window.IpetAppState),
              independentTop: stateA !== stateB,
              independentChat: stateA.chat !== stateB.chat,
              independentSkills: stateA.chat.skills !== stateB.chat.skills,
              independentAsr: stateA.chat.asr !== stateB.chat.asr,
              independentExpressions: stateA.chat.available_expressions !== stateB.chat.available_expressions,
              independentMouthIds: stateA.chat.mouth_parameter_ids !== stateB.chat.mouth_parameter_ids,
              state: stateA,
            }));
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(APP_STATE_JS)],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        state = payload["state"]

        self.assertTrue(payload["namespaceFrozen"])
        self.assertTrue(payload["independentTop"])
        self.assertTrue(payload["independentChat"])
        self.assertTrue(payload["independentSkills"])
        self.assertTrue(payload["independentAsr"])
        self.assertTrue(payload["independentExpressions"])
        self.assertTrue(payload["independentMouthIds"])

        self.assertEqual(state["model_url"], "")
        self.assertEqual(state["scale"], 0.3)
        self.assertEqual(state["offset_x"], 0)
        self.assertEqual(state["offset_y"], 40)
        self.assertEqual(state["rotation"], 0)
        self.assertEqual(state["opacity"], 1)
        self.assertFalse(state["edit_mode"])
        self.assertTrue(state["follow_mouse"])
        self.assertFalse(state["background_enabled"])
        self.assertEqual(state["background_image"], "")
        self.assertEqual(state["background_image_url"], "")
        self.assertEqual(state["background_overlay_opacity"], 0.42)

        chat = state["chat"]
        self.assertEqual(chat["backend_url"], "http://127.0.0.1:8008")
        self.assertEqual(chat["model"], "gpt-5.4")
        self.assertEqual(chat["session_id"], "default")
        self.assertEqual(chat["voice"], "zh-CN-XiaoxiaoNeural")
        self.assertEqual(chat["rate_pct"], 0)
        self.assertEqual(chat["tts_provider"], "edge_tts")
        self.assertEqual(chat["tts_provider_url"], "")
        self.assertTrue(chat["expression_mode"])
        self.assertEqual(chat["expression_output_format"], "ndjson_v1")
        self.assertTrue(chat["react_enabled"])
        self.assertEqual(chat["react_visibility"], "inline")
        self.assertEqual(chat["max_reasoning_steps"], 10)
        self.assertEqual(chat["available_expressions"], [])
        self.assertEqual(chat["lip_sync_gain"], 1)
        self.assertEqual(chat["mouth_parameter_ids"], [])
        self.assertEqual(chat["mouth_form_parameter_ids"], [])
        self.assertEqual(chat["skills"], {"enabled": True, "default_active_ids": []})
        self.assertEqual(
            chat["asr"],
            {
                "enabled": True,
                "provider": "funasr",
                "api_base_url": "http://127.0.0.1:8012",
                "provider_url": "https://api.groq.com/openai/v1/audio/transcriptions",
                "model": "whisper-large-v3-turbo",
                "push_to_talk_key": "Alt",
                "interim_results": True,
            },
        )
        self.assertEqual(chat["system_prompt"], "")
        self.assertEqual(chat["pet_display_name"], "机魂")

    def test_index_uses_app_state_factory_instead_of_inline_default_state(self) -> None:
        graph_source = CONTROLLER_GRAPH_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")

        self.assertIn("const state = deps.state || runtimeWindow.IpetAppState.createDefaultAppState();", graph_source)
        self.assertIsNone(re.search(r"const state = \\{\\s*\\n\\s*model_url:", index_source))
        self.assertNotIn("background_overlay_opacity: 0.42", index_source)
        self.assertNotIn("pet_display_name: \"机魂\"", index_source)


if __name__ == "__main__":
    unittest.main()
