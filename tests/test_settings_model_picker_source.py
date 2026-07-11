from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_HTML = ROOT / "settings.html"
SETTINGS_JS = ROOT / "settings.js"
SETTINGS_MODEL_PICKER_JS = ROOT / "settings_model_picker.js"
BACKEND_APP = ROOT / "backend" / "app.py"
BACKEND_APP_ROUTE_CONTEXT = ROOT / "backend" / "app_route_context.py"
BACKEND_SETTINGS_ASSETS = ROOT / "backend" / "settings_assets.py"
BACKEND_SETTINGS_ROUTES = ROOT / "backend" / "settings_routes.py"


class SettingsModelPickerSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = SETTINGS_HTML.read_text(encoding="utf-8")
        self.settings_js = SETTINGS_JS.read_text(encoding="utf-8")
        self.backend_app = BACKEND_APP.read_text(encoding="utf-8")
        self.backend_app_route_context = BACKEND_APP_ROUTE_CONTEXT.read_text(encoding="utf-8")
        self.backend_settings_assets = BACKEND_SETTINGS_ASSETS.read_text(encoding="utf-8")
        self.backend_settings_routes = BACKEND_SETTINGS_ROUTES.read_text(encoding="utf-8")
        self.assertTrue(
            SETTINGS_MODEL_PICKER_JS.exists(),
            "settings_model_picker.js should own settings page model list fetch/render behavior.",
        )
        self.model_picker_js = SETTINGS_MODEL_PICKER_JS.read_text(encoding="utf-8")

    def test_settings_page_loads_model_picker_before_settings_controller(self) -> None:
        model_picker_script = '<script src="/settings_model_picker.js?v=1"></script>'
        settings_script = '<script src="/settings.js?v=9"></script>'

        self.assertIn(model_picker_script, self.html)
        self.assertIn(settings_script, self.html)
        self.assertLess(self.html.index(model_picker_script), self.html.index(settings_script))

    def test_model_picker_module_exposes_stable_controller_namespace(self) -> None:
        self.assertIn("window.IpetSettingsModelPicker", self.model_picker_js)
        self.assertIn("createSettingsModelPickerController", self.model_picker_js)
        for dependency in (
            "els",
            "fetchJson",
            "stringValue",
            "isGoogleAistudio",
            "isCodex",
            "getSettingsPayload",
            "renderSummary",
            "readForm",
            "showToast",
        ):
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, self.model_picker_js)

    def test_settings_js_keeps_thin_compatibility_wrappers(self) -> None:
        self.assertIn("createSettingsModelPickerController", self.settings_js)
        for function_name in (
            "setBrainModelStatus",
            "renderBrainModelOptions",
            "fetchBrainModels",
            "setObserveModelStatus",
            "renderObserveModelOptions",
            "fetchObserveModels",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(f"function {function_name}", self.settings_js)
                self.assertIn(f"modelPickerController.{function_name}", self.settings_js)

        self.assertNotIn('document.createElement("button")', self.settings_js)
        self.assertNotIn('JSON.stringify(payload)', self.settings_js)
        self.assertNotIn('scope: "observe"', self.settings_js)

    def test_model_picker_preserves_brain_and_observe_payload_semantics(self) -> None:
        self.assertIn('fetchJson("/api/brain/models"', self.model_picker_js)
        self.assertIn("provider,", self.model_picker_js)
        self.assertIn("model_endpoint: endpoint", self.model_picker_js)
        self.assertIn("payload.api_key = els.brainApiKey.value", self.model_picker_js)
        self.assertIn('scope: "observe"', self.model_picker_js)
        self.assertIn("payload.api_key = els.opsObserveApiKey.value", self.model_picker_js)
        self.assertIn("!endpoint && !isGoogleAistudio(provider) && !isCodex(provider)", self.model_picker_js)
        self.assertGreaterEqual(
            self.model_picker_js.count("!endpoint && !isGoogleAistudio(provider) && !isCodex(provider)"),
            2,
        )
        self.assertIn("default_reasoning_effort", self.model_picker_js)
        self.assertIn("reasoning_efforts", self.model_picker_js)
        self.assertIn("renderBrainReasoningOptions", self.model_picker_js)

    def test_backend_serves_model_picker_asset_with_no_store_cache(self) -> None:
        self.assertIn('@router.get("/settings_model_picker.js")', self.backend_settings_routes)
        self.assertIn("return deps.settings_model_picker_js_response()", self.backend_settings_routes)
        self.assertIn(
            "lambda: self._read(\"_settings_asset_helpers\").settings_model_picker_js_response()",
            self.backend_app_route_context,
        )
        self.assertNotIn('@app.get("/settings_model_picker.js")', self.backend_app)

        model_picker_asset_spec = self.backend_settings_assets.split(
            "SETTINGS_MODEL_PICKER_JS_ASSET = SettingsAsset(",
            1,
        )[1].split(
            "def settings_asset_response",
            1,
        )[0]
        self.assertIn('path=ROOT_DIR / "settings_model_picker.js"', model_picker_asset_spec)
        self.assertIn('media_type="application/javascript"', model_picker_asset_spec)
        self.assertIn('missing_detail="settings_model_picker.js not found."', model_picker_asset_spec)
        self.assertIn("NO_STORE_HEADERS", self.backend_settings_assets)


if __name__ == "__main__":
    unittest.main()
