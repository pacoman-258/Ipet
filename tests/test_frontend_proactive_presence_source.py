from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
PROACTIVE_JS = ROOT / "frontend" / "proactive_presence.js"
APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"


class FrontendProactivePresenceSourceTests(unittest.TestCase):
    def test_index_loads_presence_controller_before_app_wiring(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        proactive = '<script src="./frontend/proactive_presence.js"></script>'
        sections = '<script src="./frontend/controller_graph_app_sections.js"></script>'
        self.assertIn(proactive, source)
        self.assertLess(source.find(proactive), source.find(sections))
        self.assertIn("createProactivePresenceController", APP_SECTIONS_JS.read_text(encoding="utf-8"))

    def test_controller_delivers_only_after_idle_and_reports_feedback(self) -> None:
        script = textwrap.dedent(
            """
            const fs = require("fs");
            const vm = require("vm");
            const source = fs.readFileSync(process.argv[1], "utf8");
            let now = 1000;
            const calls = [];
            const spoken = [];
            const messages = [];
            const document = {
              hidden: false,
              addEventListener() {},
            };
            const window = {
              document,
              Date: { now: () => now },
              setInterval: () => 7,
              clearInterval() {},
            };
            const sandbox = { window, document, Date: window.Date };
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: process.argv[1] });
            const fetch = async (url, options) => {
              calls.push({ url, body: JSON.parse(options.body) });
              if (url.endsWith("/pulse")) {
                return {
                  ok: true,
                  json: async () => ({
                    delivery: { id: "one", text: "回来啦？", speak: true, open_chat: false },
                  }),
                };
              }
              return { ok: true, json: async () => ({ ok: true }) };
            };
            const controller = sandbox.window.IpetProactivePresence.createProactivePresenceController({
              state: { chat: { backend_url: "http://local", session_id: "topic", pet_display_name: "Ipet" } },
              getChatState: () => "idle",
              getMemoryMode: () => "persistent",
              isAsrBusy: () => false,
              speechController: {
                isPlaying: () => false,
                beginStream: () => spoken.push("begin"),
                enqueueTTSChunk: (text) => spoken.push(text),
                markStreamFinished: () => spoken.push("end"),
              },
              appendMessage: (role, text) => messages.push({ role, text }),
              fetch,
              window,
              document,
            });
            controller.applyConfig({ mode: "active", poll_interval_sec: 15 });
            now = 7001;
            controller.pollNow().then(() => {
              console.log(JSON.stringify({ calls, spoken, messages }));
            });
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(PROACTIVE_JS)],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        self.assertTrue(payload["calls"][0]["url"].endswith("/api/environment/pulse"))
        self.assertEqual(payload["calls"][1]["body"]["outcome"], "delivered")
        self.assertEqual(payload["messages"], [{"role": "assistant", "text": "回来啦？"}])
        self.assertEqual(payload["spoken"], ["begin", "回来啦？", "end"])

    def test_inflight_delivery_is_dismissed_after_presence_is_disabled(self) -> None:
        script = textwrap.dedent(
            """
            const fs = require("fs");
            const vm = require("vm");
            const source = fs.readFileSync(process.argv[1], "utf8");
            let now = 1000;
            let resolvePulse;
            const calls = [];
            const messages = [];
            const document = { hidden: false, addEventListener() {} };
            const window = {
              document,
              setInterval: () => 7,
              clearInterval() {},
            };
            const sandbox = { window, document, Date: { now: () => now } };
            vm.createContext(sandbox);
            vm.runInContext(source, sandbox, { filename: process.argv[1] });
            const fetch = async (url, options) => {
              calls.push({ url, body: JSON.parse(options.body) });
              if (url.endsWith("/pulse")) {
                return new Promise((resolve) => {
                  resolvePulse = () => resolve({
                    ok: true,
                    json: async () => ({ delivery: { id: "one", text: "回来啦？" } }),
                  });
                });
              }
              return { ok: true, json: async () => ({ ok: true }) };
            };
            const controller = sandbox.window.IpetProactivePresence.createProactivePresenceController({
              state: { chat: { backend_url: "http://local", session_id: "topic" } },
              getChatState: () => "idle",
              getMemoryMode: () => "persistent",
              appendMessage: (role, text) => messages.push({ role, text }),
              fetch,
              window,
              document,
            });
            (async () => {
              controller.applyConfig({ mode: "active" });
              now = 7001;
              const pending = controller.pollNow();
              controller.applyConfig({ mode: "off" });
              resolvePulse();
              await pending;
              console.log(JSON.stringify({ calls, messages }));
            })();
            """
        )
        result = subprocess.run(
            ["node", "-e", script, str(PROACTIVE_JS)],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["messages"], [])
        self.assertEqual(payload["calls"][1]["body"]["outcome"], "dismissed")


if __name__ == "__main__":
    unittest.main()
