from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_JS = ROOT / "frontend" / "index.js"
APP_BOOTSTRAP_JS = ROOT / "frontend" / "app_bootstrap.js"
PET_APP_API_JS = ROOT / "frontend" / "pet_app_api.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"


class FrontendAppBootstrapSourceTests(unittest.TestCase):
    def test_index_loads_pet_app_api_after_config_and_before_bootstrap(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        config_script = '<script src="./frontend/app_config.js"></script>'
        pet_app_api_script = '<script src="./frontend/pet_app_api.js"></script>'
        bootstrap_script = '<script src="./frontend/app_bootstrap.js"></script>'
        facade_script = '<script src="./frontend/controller_facade.js"></script>'
        graph_script = '<script src="./frontend/controller_graph.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(config_script, source)
        self.assertIn(pet_app_api_script, source)
        self.assertIn(bootstrap_script, source)
        self.assertIn(facade_script, source)
        self.assertIn(graph_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(config_script), source.find(pet_app_api_script))
        self.assertLess(source.find(pet_app_api_script), source.find(bootstrap_script))
        self.assertLess(source.find(config_script), source.find(bootstrap_script))
        self.assertLess(source.find(bootstrap_script), source.find(facade_script))
        self.assertLess(source.find(facade_script), source.find(graph_script))
        self.assertLess(source.find(graph_script), source.find(app_script))

    def test_app_bootstrap_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(APP_BOOTSTRAP_JS.exists(), "frontend/app_bootstrap.js should exist")
        source = APP_BOOTSTRAP_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetAppBootstrap", source)
        self.assertIn("function createAppBootstrapController", source)
        self.assertIn("function start()", source)
        self.assertIn("async function init()", source)
        self.assertIn("function installPetAppApi()", source)
        self.assertIn("window.IpetAppBootstrap = Object.freeze", source)

    def test_pet_app_api_module_installs_expected_api_methods(self) -> None:
        self.assertTrue(PET_APP_API_JS.exists(), "frontend/pet_app_api.js should exist")
        source = PET_APP_API_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetPetAppApi", source)
        self.assertIn("function createPetAppApi", source)
        self.assertIn("function installPetAppApi", source)
        self.assertIn("runtimeWindow.PET_APP = createPetAppApi", source)
        self.assertIn(
            'const getCurrentModel =\n      typeof petSceneController.getCurrentModel === "function"',
            source,
        )
        self.assertIn("const currentModel = getCurrentModel();", source)
        for snippet in (
            "async applyConfig(config)",
            "async reloadModel()",
            "playMotion(group, index = 0)",
            "playExpression(name)",
            "getState()",
            "openChat,",
            "closeChat,",
            "async sendChat(text)",
            "async speakText(text)",
            "stopSpeak()",
        ):
            self.assertIn(snippet, source)

    def test_app_bootstrap_owns_runtime_startup_and_delegates_pet_app_api(self) -> None:
        source = APP_BOOTSTRAP_JS.read_text(encoding="utf-8")

        for snippet in (
            "let pendingConfig = null;",
            "new runtimeWindow.PIXI.Application({",
            "petSceneController.setPixiApp(pixiApp);",
            "pixiApp.ticker.add(() => {",
            "new runtimeWindow.QWebChannel(runtimeWindow.qt.webChannelTransport",
            "setQtBridge(channel.objects.qtBridge);",
            "navSettingsButtonEl.addEventListener(\"click\"",
            "chatInputEl.addEventListener(\"keydown\"",
            "runtimeWindow.addEventListener(\"blur\"",
            "chatPanelController.installChatPanelInteractions();",
            "petSceneController.consumePendingModelUrl();",
            "const petAppApi = runtimeWindow.IpetPetAppApi || window.IpetPetAppApi;",
            "petAppApi.installPetAppApi",
            "setPendingConfig",
            "return init().catch((err) => {",
        ):
            self.assertIn(snippet, source)

        for forbidden in (
            "runtimeWindow.PET_APP = {",
            "async reloadModel()",
            "playExpression(name)",
            "async sendChat(text)",
            "async speakText(text)",
            "stopSpeak()",
            "window.IpetPetAppApi.installPetAppApi",
        ):
            self.assertNotIn(forbidden, source)

    def test_controller_facade_module_exposes_wrapper_namespace(self) -> None:
        self.assertTrue(CONTROLLER_FACADE_JS.exists(), "frontend/controller_facade.js should exist")
        source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetControllerFacade", source)
        self.assertIn("function createControllerFacade", source)
        self.assertIn('"init": ["appBootstrap", "start"]', source)
        self.assertIn('"appendMessage": ["chatMessages", "appendMessage"]', source)
        self.assertIn('"streamChat": ["chatSubmit", "streamChat"]', source)
        self.assertIn('"applyConfig": ["appConfig", "applyConfig"]', source)
        self.assertIn("function requireControllerMethod", source)
        self.assertIn("controller is not initialized", source)
        self.assertIn("window.IpetControllerFacade = Object.freeze", source)

    def test_controller_graph_delegates_bootstrap_to_controller_facade(self) -> None:
        graph_source = CONTROLLER_GRAPH_JS.read_text(encoding="utf-8")
        sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")

        self.assertIn(
            "const appBootstrapController = runtimeWindow.IpetAppBootstrap.createAppBootstrapController",
            sections_source,
        )
        self.assertIn(
            "const controllerRegistry = deps.controllers || {};",
            graph_source,
        )
        self.assertIn(
            "const facade = runtimeWindow.IpetControllerFacade.createControllerFacade",
            graph_source,
        )
        self.assertIn(
            "controllerRegistry.appBootstrap = appBootstrapController;",
            sections_source,
        )
        self.assertIn("appendMessage: facade.appendMessage", sections_source)
        self.assertIn("streamChat: facade.streamChat", sections_source)
        self.assertIn("applyConfig: facade.applyConfig", sections_source)
        self.assertIn("window.IpetControllerGraph.createIpetControllerGraph", index_source)
        self.assertIn("graph.facade.init();", index_source)
        for function_name in ("appendMessage", "streamChat", "applyConfig", "init"):
            self.assertNotRegex(index_source, rf"function {function_name}\(")

    def test_index_no_longer_owns_bootstrap_runtime_wiring(self) -> None:
        source = INDEX_JS.read_text(encoding="utf-8")

        for forbidden in (
            "new window.PIXI.Application",
            "new window.QWebChannel",
            "window.PET_APP = {",
            "let pendingConfig",
            "pendingConfig =",
            "navSettingsButtonEl.addEventListener",
            "chatInputEl.addEventListener",
            "window.addEventListener(\"blur\"",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
