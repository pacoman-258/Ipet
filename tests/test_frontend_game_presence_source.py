from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
GAME_JS = ROOT / "frontend" / "game_presence.js"
APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"


class FrontendGamePresenceSourceTests(unittest.TestCase):
    def test_game_controller_loads_before_app_wiring_and_never_owns_chat_ui(self) -> None:
        index = INDEX_HTML.read_text(encoding="utf-8")
        source = GAME_JS.read_text(encoding="utf-8")
        game_script = '<script src="./frontend/game_presence.js"></script>'
        sections_script = '<script src="./frontend/controller_graph_app_sections.js"></script>'

        self.assertIn(game_script, index)
        self.assertLess(index.index(game_script), index.index(sections_script))
        self.assertIn("createGamePresenceController", APP_SECTIONS_JS.read_text(encoding="utf-8"))
        self.assertNotIn("appendMessage", source)
        self.assertNotIn("openChat", source)
        self.assertIn("const POLL_INTERVAL_MS = 750", source)
        self.assertIn('X-Ipet-Game-Capability', source)
        self.assertNotIn('X-Ipet-Local-Token', source)

    def test_voice_only_delivery_menu_states_pause_and_disconnect_cleanup(self) -> None:
        script = textwrap.dedent(
            """
            const fs = require("fs");
            const vm = require("vm");
            const source = fs.readFileSync(process.argv[1], "utf8");
            const calls = [];
            const spoken = [];
            const menu = {
              disabled: false,
              attributes: {},
              classes: {},
              classList: { toggle(name, value) { menu.classes[name] = value; } },
              setAttribute(name, value) { this.attributes[name] = value; },
            };
            const label = { textContent: "" };
            const detail = { textContent: "" };
            const statusResults = [
              { state: "active", connected: true, session_id: "s1" },
              { state: "waiting", connected: false, session_id: "" },
              { state: "incompatible", connected: true, session_id: "s2", local_player_identified: false },
            ];
            const pulseResults = [
              {
                status: { state: "active", connected: true, session_id: "s1" },
                delivery: {
                  id: "r1", session_id: "s1", text: "这一下很疼。",
                  priority: "important", interrupt: true,
                },
              },
              { status: { state: "waiting", connected: false, session_id: "" }, delivery: null },
              {
                status: { state: "incompatible", connected: true, session_id: "s2", local_player_identified: false },
                delivery: null,
              },
            ];
            const window = {
              document: { hidden: false },
              setInterval: () => 7,
              clearInterval() {},
            };
            const sandbox = { window, document: window.document, Promise };
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: process.argv[1] });
            const fetch = async (url, options = {}) => {
              const body = JSON.parse(options.body || "{}");
              calls.push({ url, body });
              if (url.endsWith("/status")) {
                return { ok: true, json: async () => statusResults.shift() };
              }
              if (url.endsWith("/pulse")) {
                const payload = pulseResults.shift();
                return { ok: true, json: async () => payload };
              }
              if (url.endsWith("/pause")) {
                return {
                  ok: true,
                  json: async () => ({ ok: true, state: "paused", connected: true, session_id: "s1" }),
                };
              }
              return { ok: true, json: async () => ({ ok: true }) };
            };
            const speech = {
              currentSource: "",
              isPlaying: () => false,
              queueLength: () => 0,
              source() { return this.currentSource; },
              stopSpeaking() { spoken.push("stop"); this.currentSource = ""; },
              beginStream(source) { spoken.push(`begin:${source}`); this.currentSource = source; return 9; },
              enqueueTTSChunk(text) { spoken.push(text); },
              markStreamFinished() { spoken.push("end"); },
              whenRunSettled: async () => "delivered",
            };
            const controller = sandbox.window.IpetGamePresence.createGamePresenceController({
              state: { chat: { backend_url: "http://local" }, game_capability: "game-capability" },
              refs: { petMenuGameEl: menu, petMenuGameLabelEl: label, petMenuGameDetailEl: detail },
              speechController: speech,
              getChatState: () => "idle",
              isAsrBusy: () => false,
              fetch,
              window,
              document: window.document,
            });
            (async () => {
              await controller.pollNow();
              await Promise.resolve();
              const active = { label: label.textContent, detail: detail.textContent, disabled: menu.disabled };
              await controller.togglePause();
              const paused = { label: label.textContent, stopped: spoken.filter(x => x === "stop").length };
              await controller.pollNow();
              const waiting = { label: label.textContent, detail: detail.textContent, disabled: menu.disabled };
              await controller.pollNow();
              const incompatible = { detail: detail.textContent, disabled: menu.disabled };
              console.log(JSON.stringify({ calls, spoken, active, paused, waiting, incompatible }));
            })();
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(GAME_JS)],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)

        self.assertEqual(payload["spoken"][:4], ["stop", "begin:game", "这一下很疼。", "end"])
        self.assertEqual(payload["active"]["label"], "游戏模式 · 运行中")
        self.assertFalse(payload["active"]["disabled"])
        self.assertEqual(payload["paused"]["label"], "游戏模式 · 已暂停")
        self.assertGreaterEqual(payload["paused"]["stopped"], 2)
        self.assertEqual(payload["waiting"]["detail"], "等待杀戮尖塔 2")
        self.assertEqual(payload["waiting"]["label"], "游戏模式 · 等待杀戮尖塔 2")
        self.assertTrue(payload["waiting"]["disabled"])
        self.assertEqual(payload["incompatible"]["detail"], "无法识别本地玩家")
        self.assertTrue(payload["incompatible"]["disabled"])
        feedback = [call for call in payload["calls"] if call["url"].endswith("/feedback")]
        self.assertEqual(feedback[0]["body"], {"id": "r1", "outcome": "delivered"})
        pause = [call for call in payload["calls"] if call["url"].endswith("/pause")]
        self.assertEqual(pause[0]["body"], {"paused": True, "session_id": "s1"})
        status_calls = [call for call in payload["calls"] if call["url"].endswith("/status")]
        self.assertEqual(len(status_calls), 3)

    def test_status_keeps_refreshing_while_reaction_generation_is_pending(self) -> None:
        script = textwrap.dedent(
            """
            const fs = require("fs");
            const vm = require("vm");
            const source = fs.readFileSync(process.argv[1], "utf8");
            const statuses = [
              { state: "active", connected: true, session_id: "s1" },
              { state: "paused", connected: true, session_id: "s1" },
            ];
            let statusCalls = 0;
            let pulseCalls = 0;
            const menu = {
              disabled: false,
              classList: { toggle() {} },
              setAttribute() {},
            };
            const label = { textContent: "" };
            const window = {
              document: { hidden: false },
              setInterval: () => 7,
              clearInterval() {},
            };
            const sandbox = { window, document: window.document, Promise };
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: process.argv[1] });
            const fetch = async (url) => {
              if (url.endsWith("/status")) {
                const payload = statuses[statusCalls++];
                return { ok: true, json: async () => payload };
              }
              if (url.endsWith("/pulse")) {
                pulseCalls += 1;
                return new Promise(() => {});
              }
              throw new Error(`unexpected request: ${url}`);
            };
            const controller = sandbox.window.IpetGamePresence.createGamePresenceController({
              state: { chat: { backend_url: "http://local" }, game_capability: "game-capability" },
              refs: { petMenuGameEl: menu, petMenuGameLabelEl: label },
              fetch,
              window,
              document: window.document,
            });
            (async () => {
              controller.pollNow();
              await new Promise(resolve => setImmediate(resolve));
              const firstLabel = label.textContent;
              controller.pollNow();
              await new Promise(resolve => setImmediate(resolve));
              console.log(JSON.stringify({ firstLabel, secondLabel: label.textContent, statusCalls, pulseCalls }));
            })();
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(GAME_JS)],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)

        self.assertEqual(payload["firstLabel"], "游戏模式 · 运行中")
        self.assertEqual(payload["secondLabel"], "游戏模式 · 已暂停")
        self.assertEqual(payload["statusCalls"], 2)
        self.assertEqual(payload["pulseCalls"], 1)


if __name__ == "__main__":
    unittest.main()
