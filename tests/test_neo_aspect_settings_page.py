from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_HTML = ROOT / "settings.html"
SETTINGS_JS = ROOT / "settings.js"


class NeoAspectSettingsPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = SETTINGS_HTML.read_text(encoding="utf-8")
        self.js = SETTINGS_JS.read_text(encoding="utf-8")

    def test_page_keeps_existing_desktop_visual_shell(self) -> None:
        for token in (
            "desktop-menubar",
            "desktop-stage",
            "content-area",
            "panel-card",
            "desktop-window",
            "desktop-icons",
            "desktop-dock",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.html)

    def test_page_uses_neo_aspect_sections(self) -> None:
        for section_id in ("overview", "body", "brain", "human-ops", "memory", "skills", "diagnostics"):
            with self.subTest(section_id=section_id):
                self.assertIn(f'id="{section_id}"', self.html)
                self.assertIn(f'data-window-target="{section_id}"', self.html)

    def test_page_removed_agent_runtime_management_copy(self) -> None:
        combined = f"{self.html}\n{self.js}".lower()
        for forbidden in ("hermes", "astrbot", "mcp", "runtime sidecar", "/api/runtime/status", "/api/hermes/status", "/api/mcp/", "/api/skills"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)

    def test_human_ops_click_preview_is_reviewable_and_visible(self) -> None:
        self.assertIn("click-preview-dot", self.html)
        self.assertIn("click-preview-dot", self.js)
        self.assertIn("act 需要批准", self.html)

    def test_brain_provider_selector_supports_three_api_formats(self) -> None:
        self.assertIn('id="brain-provider"', self.html)
        for provider in ("openai_compatible", "ollama", "anthropic_compatible"):
            with self.subTest(provider=provider):
                self.assertIn(f'value="{provider}"', self.html)
                self.assertIn(provider, self.js)
        self.assertIn("brainProvider", self.js)
        self.assertIn("provider:", self.js)
        self.assertIn("model_endpoint: stringValue(els.brainModelEndpoint)", self.js)
        self.assertNotIn("next.chat.backend_url = stringValue(els.brainModelEndpoint", self.js)
        read_form = self.js.split("  function readForm() {", 1)[1].split("  async function loadSettings()", 1)[0]
        self.assertNotIn("backend_url", read_form)

    def test_brain_model_fetch_button_and_one_click_fill_exist(self) -> None:
        self.assertIn('id="brain-fetch-models-btn"', self.html)
        self.assertIn('id="brain-model-list"', self.html)
        self.assertIn('id="brain-model-status"', self.html)
        self.assertIn("brainFetchModelsBtn", self.js)
        self.assertIn("/api/brain/models", self.js)
        self.assertIn("renderBrainModelOptions", self.js)
        self.assertIn("els.brainModelName.value = model.id", self.js)

    def test_settings_js_still_uses_existing_config_endpoint(self) -> None:
        self.assertIn("/api/settings/config", self.js)
        self.assertIn("PUT", self.js)

    def test_settings_js_has_static_preview_fallback(self) -> None:
        self.assertIn("fallbackSettings", self.js)
        self.assertIn("static_preview", self.js)


if __name__ == "__main__":
    unittest.main()
