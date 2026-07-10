from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"
SHELL_VISUALS_JS = ROOT / "frontend" / "shell_visuals.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendShellVisualsSourceTests(unittest.TestCase):
    def test_index_loads_shell_visuals_after_helpers_and_before_app_modules(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        helpers_script = '<script src="./frontend/index_helpers.js"></script>'
        shell_visuals_script = '<script src="./frontend/shell_visuals.js"></script>'
        app_config_script = '<script src="./frontend/app_config.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(helpers_script, source)
        self.assertIn(shell_visuals_script, source)
        self.assertIn(app_config_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(helpers_script), source.find(shell_visuals_script))
        self.assertLess(source.find(shell_visuals_script), source.find(app_config_script))
        self.assertLess(source.find(shell_visuals_script), source.find(app_script))

    def test_shell_visuals_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(SHELL_VISUALS_JS.exists(), "frontend/shell_visuals.js should exist")
        source = SHELL_VISUALS_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetShellVisuals", source)
        self.assertIn("function createShellVisualsController", source)
        for name in (
            "showError",
            "clearError",
            "logToQt",
            "clamp",
            "toRateString",
            "roundInt",
            "backgroundOverlayValue",
            "applyBackgroundVisuals",
            "refreshStatus",
            "updateShellButtons",
        ):
            self.assertIn(name, source)
        self.assertIn("window.IpetShellVisuals = Object.freeze", source)

    def test_controller_facade_owns_shell_visual_wrappers(self) -> None:
        sections_source = CONTROLLER_GRAPH_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        self.assertTrue(CONTROLLER_FACADE_JS.exists(), "frontend/controller_facade.js should exist")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")

        self.assertIn(
            "const shellVisualsController = runtimeWindow.IpetShellVisuals.createShellVisualsController",
            sections_source,
        )
        self.assertIn("window.IpetControllerFacade", facade_source)
        self.assertIn("function createControllerFacade", facade_source)
        for mapping in (
            '"showError": ["shellVisuals", "showError"]',
            '"clearError": ["shellVisuals", "clearError"]',
            '"logToQt": ["shellVisuals", "logToQt"]',
            '"clamp": ["shellVisuals", "clamp"]',
            '"toRateString": ["shellVisuals", "toRateString"]',
            '"roundInt": ["shellVisuals", "roundInt"]',
            '"backgroundOverlayValue": ["shellVisuals", "backgroundOverlayValue"]',
            '"applyBackgroundVisuals": ["shellVisuals", "applyBackgroundVisuals"]',
            '"refreshStatus": ["shellVisuals", "refreshStatus"]',
            '"updateShellButtons": ["shellVisuals", "updateShellButtons"]',
        ):
            self.assertIn(mapping, facade_source)
        self.assertIn("showError: facade.showError", app_sections_source)
        self.assertNotRegex(index_source, r"function showError\(")

    def test_index_no_longer_inlines_shell_visual_logic(self) -> None:
        source = INDEX_JS.read_text(encoding="utf-8")

        for function_name in (
            "showError",
            "clearError",
            "logToQt",
            "clamp",
            "toRateString",
            "roundInt",
            "backgroundOverlayValue",
            "applyBackgroundVisuals",
            "refreshStatus",
            "updateShellButtons",
        ):
            self.assertNotRegex(source, rf"function {function_name}\(")

        self.assertNotIn('backgroundImageEl.addEventListener("load"', source)
        self.assertNotIn('backgroundImageEl.addEventListener("error"', source)
        self.assertNotIn('errorEl.textContent = String(msg || "未知错误");', source)
        self.assertNotIn('document.documentElement.style.setProperty("--backdrop-overlay"', source)
        self.assertNotIn('statusEl.textContent = [', source)
        self.assertNotIn('navChatButtonEl.classList.toggle("is-active"', source)
        self.assertNotIn('navHistoryButtonEl.title = enabled ?', source)

    def test_shell_visuals_module_keeps_required_behavior(self) -> None:
        source = SHELL_VISUALS_JS.read_text(encoding="utf-8")

        for snippet in (
            'backgroundImageEl.addEventListener("load"',
            'backgroundLayerEl.classList.add("has-image");',
            'backgroundImageEl.style.display = "block";',
            'backgroundImageEl.addEventListener("error"',
            'backgroundLayerEl.classList.remove("has-image");',
            'backgroundImageEl.style.display = "none";',
            'backgroundImageEl.removeAttribute("src");',
            'logToQt(`background image unavailable: ${state.background_image}`);',
            'errorEl.textContent = String(msg || "未知错误");',
            'errorEl.style.display = "block";',
            'errorEl.style.display = "none";',
            'if (qtBridge && typeof qtBridge.log === "function")',
            'qtBridge.log(String(msg));',
            "console.log(msg);",
            'document.documentElement.style.setProperty("--backdrop-overlay", String(overlay));',
            'document.documentElement.style.setProperty("--shell-overlay", String(overlay));',
            'const modelName = state.model_url ? state.model_url.split("/").pop() : "(none)";',
            '`offset: (${roundInt(state.offset_x)}, ${roundInt(state.offset_y)})`,',
            '`bg: ${state.background_enabled && state.background_image_url ? "image" : "default"}`,',
            'navChatButtonEl.classList.toggle("is-active", chatOpen && !historyOpen);',
            'navHistoryButtonEl.disabled = !enabled;',
            'navHistoryButtonEl.title = enabled ? "\\u67e5\\u770b\\u5386\\u53f2\\u8bdd\\u9898" : "\\u5f53\\u524d\\u672a\\u542f\\u7528\\u8bdd\\u9898\\u4fdd\\u5b58";',
        ):
            self.assertIn(snippet, source)

        for dependency in (
            "helpers",
            "state",
            "document",
            "refs",
            "getQtBridge",
            "topicHistoryEnabled",
        ):
            self.assertIn(dependency, source)


if __name__ == "__main__":
    unittest.main()
