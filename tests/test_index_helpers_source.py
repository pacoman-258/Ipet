from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HELPERS_JS = ROOT / "frontend" / "index_helpers.js"
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_CHAT_SECTIONS_JS = ROOT / "frontend" / "controller_graph_chat_sections.js"
CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"
CHAT_WORKLOG_JS = ROOT / "frontend" / "chat_worklog.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class IndexHelpersSourceTests(unittest.TestCase):
    def test_helper_bundle_exposes_pure_index_helpers(self) -> None:
        source = INDEX_HELPERS_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetIndexHelpers", source)
        for name in (
            "clamp",
            "toRateString",
            "roundInt",
            "backgroundOverlayValue",
            "parsePetNameFromPrompt",
            "normalizeSkillId",
            "normalizeChatMode",
            "normalizeMemoryMode",
            "dedupeSkillIds",
            "normalizeAsrConfig",
            "downsampleFloat32ToInt16Buffer",
            "normalizeTopicRecord",
            "normalizeSkillRecord",
            "formatStepMeta",
        ):
            self.assertIn(f"function {name}", source)

    def test_helper_bundle_stays_free_of_runtime_integrations(self) -> None:
        source = INDEX_HELPERS_JS.read_text(encoding="utf-8")

        self.assertNotIn("document.", source)
        self.assertNotIn("querySelector", source)
        self.assertNotIn("fetch(", source)
        self.assertNotIn("qtBridge", source)
        self.assertNotIn("QWebChannel", source)
        self.assertNotIn("navigator.", source)

    def test_index_uses_helper_namespace_but_moves_pet_state_wrappers_to_controller(self) -> None:
        index_source = INDEX_JS.read_text(encoding="utf-8")
        graph_source = CONTROLLER_GRAPH_JS.read_text(encoding="utf-8")
        sections_source = CONTROLLER_GRAPH_SECTIONS_JS.read_text(encoding="utf-8")
        chat_sections_source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")

        self.assertIn("const helpers = deps.helpers || runtimeWindow.IpetIndexHelpers || {};", graph_source)
        self.assertIn("runtimeWindow.IpetControllerGraphSections.createControllerGraphSections", graph_source)
        self.assertIn(
            "const shellVisualsController = runtimeWindow.IpetShellVisuals.createShellVisualsController",
            sections_source,
        )
        self.assertIn(
            "const petStateNotifyController = runtimeWindow.IpetPetStateNotify.createPetStateNotifyController",
            sections_source,
        )
        self.assertIn("controllerRegistry.shellVisuals = shellVisualsController;", sections_source)
        self.assertIn("controllerRegistry.petStateNotify = petStateNotifyController;", sections_source)

        facade_shell_visual_methods = {
            "clamp",
            "toRateString",
            "roundInt",
            "backgroundOverlayValue",
            "applyBackgroundVisuals",
            "refreshStatus",
            "updateShellButtons",
            "showError",
            "clearError",
            "logToQt",
        }
        pure_helper_methods = {
            "normalizeSkillId",
            "normalizeChatMode",
            "normalizeMemoryMode",
            "dedupeSkillIds",
        }
        pet_state_methods = {
            "speakerNameForRole",
            "syncPetDisplayName",
            "scheduleNotifyState",
            "notifyStateNow",
        }
        for method_name in sorted(facade_shell_visual_methods):
            self.assertIn(f'"{method_name}": ["shellVisuals", "{method_name}"],', facade_source)
            self.assertNotIn(f"function {method_name}", index_source)
        for method_name in sorted(pure_helper_methods):
            self.assertIn(f'"{method_name}",', facade_source)
            self.assertNotIn(f"function {method_name}", index_source)
        for method_name in sorted(pet_state_methods):
            self.assertIn(f'"{method_name}": ["petStateNotify", "{method_name}"],', facade_source)
            self.assertNotIn(f"function {method_name}", index_source)

        for snippet in (
            "refreshStatus: facade.refreshStatus,",
            "clearError: facade.clearError,",
            "scheduleNotifyState: facade.scheduleNotifyState,",
            "notifyStateNow: facade.notifyStateNow,",
        ):
            self.assertIn(snippet, sections_source)

        for snippet in (
            "speakerNameForRole: facade.speakerNameForRole,",
            "normalizeChatMode: facade.normalizeChatMode,",
            "normalizeMemoryMode: facade.normalizeMemoryMode,",
        ):
            self.assertIn(snippet, chat_sections_source)

        for snippet in (
            "backgroundOverlayValue: facade.backgroundOverlayValue,",
            "applyBackgroundVisuals: facade.applyBackgroundVisuals,",
            "updateShellButtons: facade.updateShellButtons,",
            "showError: facade.showError,",
            "logToQt: facade.logToQt,",
            "syncPetDisplayName: facade.syncPetDisplayName,",
        ):
            self.assertIn(snippet, app_sections_source)

        self.assertNotIn("function parsePetNameFromPrompt", index_source)
        self.assertNotIn("helpers.parsePetNameFromPrompt(raw)", index_source)

    def test_worklog_specific_format_step_meta_lives_with_worklog_rendering(self) -> None:
        index_source = INDEX_JS.read_text(encoding="utf-8")
        helper_source = INDEX_HELPERS_JS.read_text(encoding="utf-8")
        worklog_source = CHAT_WORKLOG_JS.read_text(encoding="utf-8")

        self.assertNotIn("function formatStepMeta(payload) {", index_source)
        self.assertIn("function formatStepMeta(payload) {", helper_source)
        self.assertIn("function formatStepMeta(payload) {", worklog_source)
        self.assertIn("return helpers.formatStepMeta(payload);", worklog_source)


if __name__ == "__main__":
    unittest.main()
