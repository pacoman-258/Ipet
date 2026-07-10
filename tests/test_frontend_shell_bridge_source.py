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
SHELL_BRIDGE_JS = ROOT / "frontend" / "shell_bridge.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendShellBridgeSourceTests(unittest.TestCase):
    def test_index_loads_shell_bridge_near_shell_modules_and_before_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        shell_visuals_script = '<script src="./frontend/shell_visuals.js"></script>'
        shell_bridge_script = '<script src="./frontend/shell_bridge.js"></script>'
        chat_modes_script = '<script src="./frontend/chat_modes.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(shell_visuals_script, source)
        self.assertIn(shell_bridge_script, source)
        self.assertIn(chat_modes_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(shell_visuals_script), source.find(shell_bridge_script))
        self.assertLess(source.find(shell_bridge_script), source.find(chat_modes_script))
        self.assertLess(source.find(shell_bridge_script), source.find(app_script))

    def test_shell_bridge_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(SHELL_BRIDGE_JS.exists(), "frontend/shell_bridge.js should exist")
        source = SHELL_BRIDGE_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetShellBridge", source)
        self.assertIn("function createShellBridgeController", source)
        for name in (
            "callQtBridge",
            "openSettingsPage",
            "minimizeWindow",
            "closeWindow",
        ):
            self.assertIn(name, source)
        self.assertIn("window.IpetShellBridge = Object.freeze", source)

    def test_index_wires_bridge_actions_through_facade_registry(self) -> None:
        sections_source = CONTROLLER_GRAPH_SECTIONS_JS.read_text(encoding="utf-8")
        chat_sections_source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")
        consumer_source = "\n".join((chat_sections_source, app_sections_source))

        self.assertIn(
            "const shellBridgeController = runtimeWindow.IpetShellBridge.createShellBridgeController",
            sections_source,
        )
        self.assertIn("controllerRegistry.shellBridge = shellBridgeController;", sections_source)
        for name in ("callQtBridge", "openSettingsPage", "minimizeWindow", "closeWindow"):
            self.assertIn(f'"{name}": ["shellBridge", "{name}"],', facade_source)
            self.assertIn(f"facade.{name}", consumer_source)
            self.assertNotIn(f"function {name}", index_source)

    def test_index_no_longer_inlines_bridge_logic(self) -> None:
        source = INDEX_JS.read_text(encoding="utf-8")

        for function_name in (
            "callQtBridge",
            "openSettingsPage",
            "minimizeWindow",
            "closeWindow",
        ):
            self.assertNotIn(f"function {function_name}", source)

        self.assertNotIn("qtBridge[methodName]();", source)
        self.assertNotIn('callQtBridge("openSettingsPage")', source)
        self.assertNotIn('callQtBridge("minimizeWindow")', source)
        self.assertNotIn('callQtBridge("closeWindow")', source)
        self.assertNotIn("const backendBase = String(state.chat.backend_url", source)
        self.assertNotIn("const normalizedBase = backendBase.replace", source)
        self.assertNotIn('window.open(`${normalizedBase}/settings`, "_blank", "noopener");', source)

    def test_shell_bridge_module_keeps_required_behavior(self) -> None:
        source = SHELL_BRIDGE_JS.read_text(encoding="utf-8")

        for snippet in (
            'if (!qtBridge || typeof qtBridge[methodName] !== "function")',
            "qtBridge[methodName]();",
            "return true;",
            'if (callQtBridge("openSettingsPage"))',
            'const backendBase = String(state.chat?.backend_url || "http://127.0.0.1:8008").trim() || "http://127.0.0.1:8008";',
            'const normalizedBase = backendBase.replace(/\\/+$/, "");',
            'window.open(`${normalizedBase}/settings`, "_blank", "noopener");',
            'return callQtBridge("minimizeWindow");',
            'return callQtBridge("closeWindow");',
        ):
            self.assertIn(snippet, source)


if __name__ == "__main__":
    unittest.main()
