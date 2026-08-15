from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_HTML = ROOT / "settings.html"
SETTINGS_JS = ROOT / "settings.js"
SETTINGS_FORM_JS = ROOT / "settings_form.js"
BACKEND_APP = ROOT / "backend" / "app.py"
BACKEND_APP_ROUTE_CONTEXT = ROOT / "backend" / "app_route_context.py"
BACKEND_SETTINGS_ASSETS = ROOT / "backend" / "settings_assets.py"
BACKEND_SETTINGS_ROUTES = ROOT / "backend" / "settings_routes.py"


class SettingsFormSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = SETTINGS_HTML.read_text(encoding="utf-8")
        self.settings_js = SETTINGS_JS.read_text(encoding="utf-8")
        self.backend_app = BACKEND_APP.read_text(encoding="utf-8")
        self.backend_app_route_context = BACKEND_APP_ROUTE_CONTEXT.read_text(encoding="utf-8")
        self.backend_settings_assets = BACKEND_SETTINGS_ASSETS.read_text(encoding="utf-8")
        self.backend_settings_routes = BACKEND_SETTINGS_ROUTES.read_text(encoding="utf-8")
        self.assertTrue(
            SETTINGS_FORM_JS.exists(),
            "settings_form.js should own settings page form defaults and model read/write behavior.",
        )
        self.form_js = SETTINGS_FORM_JS.read_text(encoding="utf-8")

    def test_settings_page_loads_form_before_model_picker_and_settings_controller(self) -> None:
        form_script = '<script src="/settings_form.js?v=3"></script>'
        model_picker_script = '<script src="/settings_model_picker.js?v=1"></script>'
        settings_script = '<script src="/settings.js?v=11"></script>'

        self.assertIn(form_script, self.html)
        self.assertIn(model_picker_script, self.html)
        self.assertIn(settings_script, self.html)
        self.assertLess(self.html.index(form_script), self.html.index(model_picker_script))
        self.assertLess(self.html.index(model_picker_script), self.html.index(settings_script))

    def test_form_module_exposes_stable_controller_namespace(self) -> None:
        self.assertIn("window.IpetSettingsForm", self.form_js)
        self.assertIn("createSettingsFormController", self.form_js)
        for dependency in ("els", "getSettingsPayload", "renderSummary", "renderDiagnostics"):
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, self.form_js)

    def test_form_module_owns_defaults_read_write_and_existing_semantics(self) -> None:
        for token in (
            "function clone(value)",
            "function numberValue(input, fallback)",
            "function intValue(input, fallback)",
            "function stringValue(input, fallback = \"\")",
            "function linesValue(input)",
            "function textFromLines(lines)",
            "function neoDefaults()",
            "function mergedNeo(config)",
            "function fallbackSettings()",
            "function populateForm(config)",
            "function readForm()",
            'const GOOGLE_AISTUDIO_DEFAULT_MODEL = "gemini-3.5-flash"',
            "setSecretPlaceholder(els.brainApiKey, neo.brain.api_key_preview, \"API Key\")",
            "setSecretPlaceholder(els.opsObserveApiKey, neo.human_ops.observe_model.api_key_preview, \"observe API Key\")",
            "model_endpoint: isEndpointlessProvider(brainProvider) ? \"\" : stringValue(els.brainModelEndpoint)",
            "reasoning_effort: stringValue(els.brainReasoningEffort)",
            "streaming_enabled: !!els.brainStreamingEnabled?.checked",
            "web_search_enabled: !!els.brainWebSearchEnabled?.checked",
            'authorization_mode: "review"',
            'stringValue(els.opsAuthorizationMode, "review")',
            "playwright_profile: stringValue(els.opsPlaywrightProfile)",
            "model_endpoint: isEndpointlessProvider(observeProvider) ? \"\" : stringValue(els.opsObserveModelEndpoint)",
            "follow_up_enabled: !!els.memoryFollowUpEnabled?.checked",
            "follow_up_cooldown_hours: intValue(els.memoryFollowUpCooldownHours, 24)",
            "recipes: linesValue(els.skillsRecipeList)",
            "proposal_queue: linesValue(els.skillsProposalQueue)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.form_js)

        self.assertNotIn("memoryReviewQueue", self.form_js)

        read_form = self.form_js.split("  function readForm() {", 1)[1].split("  return {", 1)[0]
        self.assertNotIn("backend_url", read_form)

    def test_settings_page_can_choose_a_saved_chrome_profile(self) -> None:
        for element_id in (
            "ops-playwright-profile",
            "ops-playwright-profile-refresh",
            "ops-playwright-profile-status",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn('/api/settings/chrome-profiles', self.settings_js)
        self.assertIn('opsPlaywrightProfile: $("ops-playwright-profile")', self.settings_js)
        self.assertIn('setValue(els.opsPlaywrightProfile, neo.human_ops.playwright_profile || "")', self.form_js)
        self.assertIn('opsAuthorizationMode: $("ops-authorization-mode")', self.settings_js)
        self.assertIn('id="ops-authorization-mode"', self.html)

    def test_form_module_does_not_query_unrelated_globals(self) -> None:
        for forbidden in ("document.", "document[", "querySelector", "getElementById", "fetch("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.form_js)

    def test_settings_js_keeps_thin_form_compatibility_wrappers(self) -> None:
        self.assertIn("createSettingsFormController", self.settings_js)
        wrappers = {
            "clone": ("value", "value"),
            "numberValue": ("input, fallback", "input, fallback"),
            "intValue": ("input, fallback", "input, fallback"),
            "stringValue": ("input, fallback = \"\"", "input, fallback"),
            "linesValue": ("input", "input"),
            "textFromLines": ("lines", "lines"),
            "neoDefaults": ("", ""),
            "mergedNeo": ("config", "config"),
            "fallbackSettings": ("", ""),
            "populateForm": ("config", "config"),
            "readForm": ("", ""),
            "updateClickPreview": ("", ""),
            "syncProviderControls": ("", ""),
            "isGoogleAistudio": ("value", "value"),
            "isCodex": ("value", "value"),
        }
        for function_name, (signature, call_args) in wrappers.items():
            with self.subTest(function_name=function_name):
                pattern = (
                    rf"function {function_name}\({re.escape(signature)}\) \{{\n"
                    rf"    return formController\.{function_name}\({re.escape(call_args)}\);\n"
                    rf"  \}}"
                )
                self.assertRegex(self.settings_js, pattern)

        for moved_token in (
            "JSON.parse(JSON.stringify",
            'system_prompt: ""',
            "review_queue: linesValue",
            "recipes: linesValue",
            "model_endpoint: isEndpointlessProvider(brainProvider) ?",
            "setSecretPlaceholder(els.brainApiKey",
        ):
            with self.subTest(moved_token=moved_token):
                self.assertNotIn(moved_token, self.settings_js)

    def test_backend_serves_form_asset_with_no_store_cache(self) -> None:
        self.assertIn('@router.get("/settings_form.js")', self.backend_settings_routes)
        self.assertIn("return deps.settings_form_js_response()", self.backend_settings_routes)
        self.assertIn(
            "self.settings_form_js_response = lambda: self._read(\"_settings_asset_helpers\").settings_form_js_response()",
            self.backend_app_route_context,
        )
        self.assertNotIn('@app.get("/settings_form.js")', self.backend_app)

        form_asset_spec = self.backend_settings_assets.split("SETTINGS_FORM_JS_ASSET = SettingsAsset(", 1)[1].split(
            "SETTINGS_MODEL_PICKER_JS_ASSET = SettingsAsset(",
            1,
        )[0]
        self.assertIn('path=ROOT_DIR / "settings_form.js"', form_asset_spec)
        self.assertIn('media_type="application/javascript"', form_asset_spec)
        self.assertIn('missing_detail="settings_form.js not found."', form_asset_spec)
        self.assertIn("NO_STORE_HEADERS", self.backend_settings_assets)


if __name__ == "__main__":
    unittest.main()
