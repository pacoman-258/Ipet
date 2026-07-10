from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_CHAT_SECTIONS_JS = ROOT / "frontend" / "controller_graph_chat_sections.js"
CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"


COORDINATOR_FACTORIES = {
    "shellVisuals": "IpetShellVisuals.createShellVisualsController",
    "shellBridge": "IpetShellBridge.createShellBridgeController",
    "humanOpsPreview": "IpetHumanOpsPreview.createHumanOpsPreviewController",
    "petStateNotify": "IpetPetStateNotify.createPetStateNotifyController",
    "petScene": "IpetPetScene.createPetSceneController",
}


CHAT_FACTORIES = {
    "chatInputState": "IpetChatInputState.createChatInputStateController",
    "chatMessages": "IpetChatMessages.createChatMessagesController",
    "asr": "IpetAsr.createAsrController",
    "chatPanel": "IpetChatPanel.createChatPanelController",
    "chatWorklog": "IpetChatWorklog.createChatWorklogController",
    "chatSidebars": "IpetChatSidebars.createChatSidebarsController",
    "chatModes": "IpetChatModes.createChatModesController",
}


APP_FACTORIES = {
    "speech": "IpetSpeech.createSpeechController",
    "chatStream": "IpetChatStream.createChatStreamController",
    "chatSubmit": "IpetChatSubmit.createChatSubmitController",
    "appConfig": "IpetAppConfig.createAppConfigController",
    "appBootstrap": "IpetAppBootstrap.createAppBootstrapController",
}


class FrontendControllerGraphSourceTests(unittest.TestCase):
    def test_index_loads_controller_graph_sections_before_graph_and_entrypoint(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        facade_script = '<script src="./frontend/controller_facade.js"></script>'
        chat_sections_script = '<script src="./frontend/controller_graph_chat_sections.js"></script>'
        app_sections_script = '<script src="./frontend/controller_graph_app_sections.js"></script>'
        sections_script = '<script src="./frontend/controller_graph_sections.js"></script>'
        graph_script = '<script src="./frontend/controller_graph.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(facade_script, source)
        self.assertIn(chat_sections_script, source)
        self.assertIn(app_sections_script, source)
        self.assertIn(sections_script, source)
        self.assertIn(graph_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(facade_script), source.find(chat_sections_script))
        self.assertLess(source.find(chat_sections_script), source.find(app_sections_script))
        self.assertLess(source.find(app_sections_script), source.find(sections_script))
        self.assertLess(source.find(sections_script), source.find(graph_script))
        self.assertLess(source.find(graph_script), source.find(app_script))

    def test_controller_graph_exposes_namespace_and_delegates_section_wiring(self) -> None:
        self.assertTrue(CONTROLLER_GRAPH_JS.exists(), "frontend/controller_graph.js should exist")
        source = CONTROLLER_GRAPH_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetControllerGraph", source)
        self.assertIn("function createIpetControllerGraph(deps = {})", source)
        self.assertIn("const helpers = deps.helpers || runtimeWindow.IpetIndexHelpers || {};", source)
        self.assertIn("const controllerRegistry = deps.controllers || {};", source)
        self.assertIn("runtimeWindow.IpetControllerFacade.createControllerFacade", source)
        self.assertIn("runtimeWindow.IpetAppState.createDefaultAppState()", source)
        self.assertIn("runtimeWindow.IpetAppRefs.collectAppRefs(runtimeDocument)", source)
        self.assertIn("runtimeWindow.IpetControllerGraphSections.createControllerGraphSections", source)
        self.assertIn("return { facade, controllers: controllerRegistry, state, refs };", source)

        self.assertLessEqual(len(source.splitlines()), 80)
        self.assertNotRegex(
            source,
            r"runtimeWindow\.Ipet(?!IndexHelpers|ControllerFacade|AppState|AppRefs|ControllerGraphSections)",
        )
        self.assertNotRegex(source, r"controllerRegistry\.[A-Za-z0-9_]+\s*=")

    def test_controller_graph_sections_coordinate_runtime_shell_and_pet_wiring(self) -> None:
        self.assertTrue(
            CONTROLLER_GRAPH_SECTIONS_JS.exists(),
            "frontend/controller_graph_sections.js should exist",
        )
        source = CONTROLLER_GRAPH_SECTIONS_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetControllerGraphSections", source)
        self.assertIn("function createControllerGraphSections", source)
        self.assertIn("function wireShellSection", source)
        self.assertIn("function wirePetSection", source)
        self.assertIn("function createGraphRuntimeState", source)
        self.assertIn("function installPlaceholderControllers", source)
        self.assertIn("runtimeWindow.IpetControllerGraphChatSections.createControllerGraphChatSections", source)
        self.assertIn("runtimeWindow.IpetControllerGraphAppSections.createControllerGraphAppSections", source)
        self.assertNotIn("function wireChatSection", source)
        self.assertNotIn("function wireBootstrapSection", source)

        for registry_name, factory_name in COORDINATOR_FACTORIES.items():
            with self.subTest(registry_name=registry_name):
                self.assertIn(f"runtimeWindow.{factory_name}", source)
                self.assertRegex(source, rf"controllerRegistry\.{registry_name}\s*=")

        for registry_name, factory_name in {**CHAT_FACTORIES, **APP_FACTORIES}.items():
            with self.subTest(registry_name=registry_name):
                self.assertNotIn(f"runtimeWindow.{factory_name}", source)

        self.assertIn("controllerRegistry.asr = graphState.asrController;", source)
        self.assertIn("controllerRegistry.chatSubmit = graphState.chatSubmitController;", source)
        self.assertLessEqual(len(source.splitlines()), 260)

    def test_controller_graph_chat_sections_own_chat_wiring_details(self) -> None:
        self.assertTrue(
            CONTROLLER_GRAPH_CHAT_SECTIONS_JS.exists(),
            "frontend/controller_graph_chat_sections.js should exist",
        )
        source = CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetControllerGraphChatSections", source)
        self.assertIn("function createControllerGraphChatSections", source)
        self.assertIn("function wireChatInputSection", source)
        self.assertIn("function wireChatSection", source)
        for registry_name, factory_name in CHAT_FACTORIES.items():
            with self.subTest(registry_name=registry_name):
                self.assertIn(f"runtimeWindow.{factory_name}", source)
                self.assertRegex(source, rf"controllerRegistry\.{registry_name}\s*=")

        self.assertIn("graphState.asrController = runtimeWindow.IpetAsr.createAsrController", source)
        self.assertIn("appendMessage: facade.appendMessage", source)
        for factory_name in APP_FACTORIES.values():
            self.assertNotIn(f"runtimeWindow.{factory_name}", source)

    def test_controller_graph_app_sections_own_speech_submit_and_bootstrap_wiring(self) -> None:
        self.assertTrue(
            CONTROLLER_GRAPH_APP_SECTIONS_JS.exists(),
            "frontend/controller_graph_app_sections.js should exist",
        )
        source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetControllerGraphAppSections", source)
        self.assertIn("function createControllerGraphAppSections", source)
        self.assertIn("function wireSpeechSection", source)
        self.assertIn("function wireBootstrapSection", source)
        for registry_name, factory_name in APP_FACTORIES.items():
            with self.subTest(registry_name=registry_name):
                self.assertIn(f"runtimeWindow.{factory_name}", source)
                self.assertRegex(source, rf"controllerRegistry\.{registry_name}\s*=")

        self.assertIn("streamChat: facade.streamChat", source)
        self.assertIn("applyConfig: facade.applyConfig", source)
        self.assertIn("setMemoryMode: facade.setMemoryMode", source)
        self.assertIn("const { petSceneController, chatSidebarsController, chatWorklogController }", source)
        for factory_name in CHAT_FACTORIES.values():
            if factory_name in (
                "IpetChatStream.createChatStreamController",
                "IpetChatSubmit.createChatSubmitController",
            ):
                continue
            self.assertNotIn(f"runtimeWindow.{factory_name}", source)

    def test_controller_graph_sections_own_controller_wiring_details(self) -> None:
        graph_source = CONTROLLER_GRAPH_JS.read_text(encoding="utf-8")

        for registry_name, factory_name in {**COORDINATOR_FACTORIES, **CHAT_FACTORIES, **APP_FACTORIES}.items():
            with self.subTest(registry_name=registry_name):
                self.assertNotIn(f"runtimeWindow.{factory_name}", graph_source)
                self.assertNotIn(f"controllerRegistry.{registry_name} =", graph_source)

    def test_index_is_only_the_startup_entrypoint(self) -> None:
        source = INDEX_JS.read_text(encoding="utf-8")

        self.assertLessEqual(len(source.splitlines()), 16)
        self.assertIn("window.IpetControllerGraph.createIpetControllerGraph", source)
        self.assertIn("graph.facade.init();", source)
        self.assertNotIn("controllerRegistry.", source)
        self.assertNotRegex(source, r"Ipet[A-Za-z]+\.create[A-Z][A-Za-z]+Controller\(")
        self.assertNotIn("createDefaultAppState", source)
        self.assertNotIn("collectAppRefs", source)


if __name__ == "__main__":
    unittest.main()
