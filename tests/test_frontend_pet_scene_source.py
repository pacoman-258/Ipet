from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_JS = ROOT / "frontend" / "index.js"
CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"
PET_SCENE_JS = ROOT / "frontend" / "pet_scene.js"
CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"


class FrontendPetSceneSourceTests(unittest.TestCase):
    def test_index_loads_pet_scene_between_chat_panel_and_app(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")

        panel_script = '<script src="./frontend/chat_panel.js"></script>'
        scene_script = '<script src="./frontend/pet_scene.js"></script>'
        app_script = '<script src="./frontend/index.js"></script>'

        self.assertIn(panel_script, source)
        self.assertIn(scene_script, source)
        self.assertIn(app_script, source)
        self.assertLess(source.find(panel_script), source.find(scene_script))
        self.assertLess(source.find(scene_script), source.find(app_script))

    def test_pet_scene_module_exposes_controller_namespace(self) -> None:
        self.assertTrue(PET_SCENE_JS.exists(), "frontend/pet_scene.js should exist")
        source = PET_SCENE_JS.read_text(encoding="utf-8")

        self.assertIn("window.IpetPetScene", source)
        self.assertIn("function createPetSceneController", source)
        for name in (
            "setPixiApp",
            "getCurrentModel",
            "modelBoundsInClientSpace",
            "isPointOnModel",
            "clampModelPosition",
            "applyModelTransform",
            "bindModelInteraction",
            "setupStageInteraction",
            "isRendererReady",
            "playMotionCompat",
            "loadModel",
        ):
            self.assertIn(f"{name}", source)

    def test_pet_scene_module_owns_live2d_model_logic(self) -> None:
        self.assertTrue(PET_SCENE_JS.exists(), "frontend/pet_scene.js should exist")
        source = PET_SCENE_JS.read_text(encoding="utf-8")

        for snippet in (
            "state.scale = clamp(Number(state.scale) || DEFAULT_SCALE, MIN_SCALE, MAX_SCALE);",
            "state.opacity = clamp(Number(state.opacity) || 1, 0.1, 1);",
            "model.rotation = (state.rotation * Math.PI) / 180;",
            "const MODEL_MARGIN = 18;",
            "const TARGET_MODEL_HEIGHT_RATIO = 0.38;",
            "function defaultModelPosition()",
            "function preferredModelScale()",
            "x: rendererWidth - scaledWidth - marginX,",
            "y: rendererHeight - scaledHeight - marginY,",
            "model.on(\"pointerdown\"",
            "state.edit_mode",
            "model.on(\"pointertap\"",
            "model.on(\"hit\"",
            "const canvasRect = view?.getBoundingClientRect?.();",
            "x >= bounds.left",
            "const minY = Math.min(0, rendererHeight - scaledHeight);",
            "scheduleNotifyState();",
            "addEventListener(",
            "\"wheel\"",
            "manager.startMotion(group, index);",
            "pendingModelUrl = url;",
            "model.removeAllListeners();",
            "app.stage.removeChild(model);",
            "model.destroy();",
            "PIXI.live2d.Live2DModel.from(url);",
            "fitScaleX",
            "fitScaleY",
            "notifyStateNow();",
        ):
            self.assertIn(snippet, source)

    def test_controller_facade_delegates_pet_scene_actions(self) -> None:
        sections_source = CONTROLLER_GRAPH_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        index_source = INDEX_JS.read_text(encoding="utf-8")
        self.assertTrue(CONTROLLER_FACADE_JS.exists(), "frontend/controller_facade.js should exist")
        facade_source = CONTROLLER_FACADE_JS.read_text(encoding="utf-8")

        self.assertIn(
            "const petSceneController = runtimeWindow.IpetPetScene.createPetSceneController",
            sections_source,
        )
        for mapping in (
            '"applyModelTransform": ["petScene", "applyModelTransform"]',
            '"bindModelInteraction": ["petScene", "bindModelInteraction"]',
            '"setupStageInteraction": ["petScene", "setupStageInteraction"]',
            '"isRendererReady": ["petScene", "isRendererReady"]',
            '"playMotionCompat": ["petScene", "playMotionCompat"]',
            '"loadModel": ["petScene", "loadModel"]',
        ):
            self.assertIn(mapping, facade_source)
        self.assertIn("loadModel: facade.loadModel", app_sections_source)
        for function_name in (
            "applyModelTransform",
            "bindModelInteraction",
            "setupStageInteraction",
            "isRendererReady",
            "playMotionCompat",
            "loadModel",
        ):
            self.assertNotRegex(index_source, rf"function {function_name}\(")

        self.assertNotIn("model.on(\"pointerdown\"", index_source)
        self.assertNotIn("window.PIXI.live2d.Live2DModel.from(url);", index_source)
        self.assertNotIn("manager.startMotion(group, index);", index_source)
        self.assertNotIn("pendingModelUrl = url;", index_source)
        self.assertNotIn("model.destroy();", index_source)


if __name__ == "__main__":
    unittest.main()
