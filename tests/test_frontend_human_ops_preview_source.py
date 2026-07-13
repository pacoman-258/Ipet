from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_CHAT_SECTIONS_JS = ROOT / "frontend" / "controller_graph_chat_sections.js"
HUMAN_OPS_PREVIEW_JS = ROOT / "frontend" / "human_ops_preview.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendHumanOpsPreviewSourceTests(unittest.TestCase):
    def test_index_loads_human_ops_preview_near_shell_modules_and_before_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        shell_bridge_script = '<script src="./frontend/shell_bridge.js"></script>'
        preview_script = '<script src="./frontend/human_ops_preview.js"></script>'
        chat_modes_script = '<script src="./frontend/chat_modes.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(shell_bridge_script, source)
        self.assertIn(preview_script, source)
        self.assertIn(chat_modes_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(shell_bridge_script), source.find(preview_script))
        self.assertLess(source.find(preview_script), source.find(chat_modes_script))
        self.assertLess(source.find(preview_script), source.find(app_script))

    def test_human_ops_preview_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(HUMAN_OPS_PREVIEW_JS.exists(), "frontend/human_ops_preview.js should exist")
        source = HUMAN_OPS_PREVIEW_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetHumanOpsPreview", source)
        self.assertIn("function createHumanOpsPreviewController", source)
        self.assertIn("showHumanOpsClickPreview", source)
        self.assertIn("hideHumanOpsClickPreview", source)
        self.assertIn("getQtBridge", source)
        self.assertIn("window.IpetHumanOpsPreview = Object.freeze", source)

    def test_index_wires_human_ops_preview_through_facade_registry(self) -> None:
        sections_source = CONTROLLER_GRAPH_SECTIONS_JS.read_text(encoding="utf-8")
        chat_sections_source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")

        self.assertIn(
            "const humanOpsPreviewController = runtimeWindow.IpetHumanOpsPreview.createHumanOpsPreviewController",
            sections_source,
        )
        self.assertIn("controllerRegistry.humanOpsPreview = humanOpsPreviewController;", sections_source)
        for snippet in (
            "getQtBridge: () => graphState.qtBridge,",
            "window: runtimeWindow,",
            "document: runtimeDocument,",
        ):
            self.assertIn(snippet, sections_source)

        for name in ("hideHumanOpsClickPreview", "showHumanOpsClickPreview"):
            self.assertIn(f'"{name}": ["humanOpsPreview", "{name}"],', facade_source)
            self.assertIn(f"facade.{name}", chat_sections_source)
            self.assertNotIn(f"function {name}", index_source)

    def test_index_no_longer_inlines_human_ops_preview_dom_or_viewport_logic(self) -> None:
        source = INDEX_JS.read_text(encoding="utf-8")

        for function_name in ("showHumanOpsClickPreview", "hideHumanOpsClickPreview"):
            self.assertNotIn(f"function {function_name}", source)

        for snippet in (
            "let humanOpsClickPreviewEl",
            'document.createElement("span")',
            'dot.id = "human-ops-click-preview-dot"',
            'dot.className = "human-ops-click-preview-dot"',
            "window.screenX",
            "window.screenY",
            "previewInsideWindow",
            "qtBridge.showClickPreview(JSON.stringify(preview))",
            "qtBridge.hideClickPreview()",
        ):
            self.assertNotIn(snippet, source)

    def test_human_ops_preview_module_keeps_required_behavior(self) -> None:
        source = HUMAN_OPS_PREVIEW_JS.read_text(encoding="utf-8")

        for snippet in (
            "let humanOpsClickPreviewEl = null;",
            "const qtBridge = getQtBridge();",
            "humanOpsClickPreviewEl.remove();",
            "qtBridge.hideClickPreview();",
            'preview.marker !== "red_dot"',
            "Number.isFinite(x)",
            "Number.isFinite(y)",
            "Math.max(10, Math.min(48, Number(preview.size) || 24))",
            "window.screenX",
            "window.screenY",
            "qtBridge.showClickPreview(JSON.stringify(preview));",
            "previewInsideWindow",
            'document.createElement("span")',
            'dot.id = "human-ops-click-preview-dot"',
            'dot.className = "human-ops-click-preview-dot"',
            'dot.title = String(preview.label || "目标位置");',
            "document.body.appendChild(dot);",
        ):
            self.assertIn(snippet, source)
        self.assertIn(
            'qtBridge.showClickPreview(JSON.stringify(preview));\n        return;',
            source,
        )


if __name__ == "__main__":
    unittest.main()
