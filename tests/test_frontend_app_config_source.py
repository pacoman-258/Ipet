from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"
APP_CONFIG_JS = ROOT / "frontend" / "app_config.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendAppConfigSourceTests(unittest.TestCase):
    def test_index_loads_app_config_after_split_modules_and_before_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        panel_script = '<script src="./frontend/chat_panel.js"></script>'
        scene_script = '<script src="./frontend/pet_scene.js"></script>'
        config_script = '<script src="./frontend/app_config.js"></script>'
        bootstrap_script = '<script src="./frontend/app_bootstrap.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(panel_script, source)
        self.assertIn(scene_script, source)
        self.assertIn(config_script, source)
        self.assertIn(bootstrap_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(panel_script), source.find(scene_script))
        self.assertLess(source.find(scene_script), source.find(config_script))
        self.assertLess(source.find(config_script), source.find(bootstrap_script))
        self.assertLess(source.find(bootstrap_script), source.find(app_script))

    def test_app_config_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(APP_CONFIG_JS.exists(), "frontend/app_config.js should exist")
        source = APP_CONFIG_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetAppConfig", source)
        self.assertIn("function createAppConfigController", source)
        self.assertIn("applyConfig(config)", source)
        self.assertIn("window.IpetAppConfig = Object.freeze", source)

    def test_controller_facade_exposes_apply_config_delegate(self) -> None:
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")

        self.assertIn('"applyConfig": ["appConfig", "applyConfig"],', facade_source)
        self.assertIn(
            "facade[publicName] = (...args) => requireControllerMethod(controllers, controllerName, methodName)(...args);",
            facade_source,
        )

    def test_index_wires_app_config_through_facade_registry(self) -> None:
        sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")

        self.assertIn(
            "const appConfigController = runtimeWindow.IpetAppConfig.createAppConfigController",
            sections_source,
        )
        self.assertIn("controllerRegistry.appConfig = appConfigController;", sections_source)
        self.assertIn("applyConfig: facade.applyConfig,", sections_source)
        self.assertNotIn("function applyConfig", index_source)
        self.assertNotIn("async function applyConfig", index_source)

    def test_index_no_longer_inlines_app_config_body(self) -> None:
        source = INDEX_JS.read_text(encoding="utf-8")

        self.assertNotIn("function applyConfig", source)
        self.assertNotIn("async function applyConfig", source)
        self.assertNotIn("Object.assign(state, incoming.pet)", source)
        self.assertNotIn("setConfiguredSkillDefaults(configuredDefaults)", source)
        self.assertNotIn("backgroundOverlayValue(state.background_overlay_opacity)", source)
        self.assertNotIn("incoming.model_url || incoming.modelUrl", source)

    def test_app_config_module_keeps_required_dependencies_and_behavior(self) -> None:
        self.assertTrue(APP_CONFIG_JS.exists(), "frontend/app_config.js should exist")
        source = APP_CONFIG_JS.read_text(encoding="utf-8")

        for snippet in (
            "Object.assign(state, incoming.pet);",
            "Object.assign(state.chat, incoming.chat);",
            "normalizeAsrConfig(state.chat?.asr || {})",
            "incoming.chat.skills?.default_active_ids",
            "chatSidebarsController.setConfiguredSkillDefaults(configuredDefaults);",
            "chatSidebarsController.syncTopicFromConfig();",
            'chatHistoryDrawerEl.classList.remove("open");',
            "renderTopicHistoryList();",
            "applyChatModeUI();",
            "applyMemoryModeUI();",
            "updateShellButtons();",
            "asrController.isBusy()",
            "cancelAsrSession({ restoreInput: true, statusMessage: defaultAsrStatusText(), tone: \"idle\" });",
            "state.background_enabled = !!state.background_enabled;",
            "backgroundOverlayValue(state.background_overlay_opacity)",
            "applyBackgroundVisuals();",
            "incoming.model_url || incoming.modelUrl || state.model_url",
            "await loadModel(state.model_url);",
            "showError(`加载模型失败：${err?.message || String(err)}`);",
            "applyModelTransform();",
            "refreshStatus();",
        ):
            self.assertIn(snippet, source)

        for dependency in (
            "state",
            "asrController",
            "chatSidebarsController",
            "chatHistoryDrawerEl",
            "syncPetDisplayName",
            "normalizeAsrConfig",
            "topicHistoryEnabled",
            "renderTopicHistoryList",
            "applyChatModeUI",
            "applyMemoryModeUI",
            "updateShellButtons",
            "cancelAsrSession",
            "defaultAsrStatusText",
            "setAsrStatus",
            "syncChatInputAvailability",
            "backgroundOverlayValue",
            "applyBackgroundVisuals",
            "petSceneController",
            "loadModel",
            "showError",
            "applyModelTransform",
            "refreshStatus",
        ):
            self.assertIn(dependency, source)

        self.assertNotIn("document.getElementById", source)
        self.assertNotIn("document.querySelector", source)


if __name__ == "__main__":
    unittest.main()
