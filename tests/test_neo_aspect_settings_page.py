from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_HTML = ROOT / "settings.html"
SETTINGS_JS = ROOT / "settings.js"
SETTINGS_FORM_JS = ROOT / "settings_form.js"
SETTINGS_MODEL_PICKER_JS = ROOT / "settings_model_picker.js"


class NeoAspectSettingsPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = SETTINGS_HTML.read_text(encoding="utf-8")
        self.js = SETTINGS_JS.read_text(encoding="utf-8")
        self.form_js = SETTINGS_FORM_JS.read_text(encoding="utf-8")
        self.model_picker_js = SETTINGS_MODEL_PICKER_JS.read_text(encoding="utf-8")

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

    def test_body_tts_provider_offers_local_qwen_clone(self) -> None:
        self.assertIn('<option value="qwen_tts_local">qwenTTS 本地（音色克隆）</option>', self.html)

    def test_body_tts_provider_offers_fish_audio_clone_settings(self) -> None:
        self.assertIn('<option value="fish_audio">Fish Audio（音色克隆）</option>', self.html)
        for element_id in ("chat-tts-voice-id", "chat-tts-model", "chat-tts-api-key", "chat-tts-api-key-clear"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn("tts_voice_id", self.form_js)
        self.assertIn("tts_model", self.form_js)
        self.assertIn("tts_api_key_preview", self.form_js)

    def test_dock_uses_the_requested_section_icons(self) -> None:
        expected_glyphs = {
            "overview": "⚙",
            "brain": "🧠",
            "human-ops": "☺",
            "memory": "📖",
            "skills": "☭",
        }
        dock = self.html.split('<nav class="desktop-dock"', 1)[1].split("</nav>", 1)[0]
        for section_id, glyph in expected_glyphs.items():
            with self.subTest(section_id=section_id):
                button = dock.split(f'data-window-target="{section_id}"', 1)[1].split("</button>", 1)[0]
                self.assertIn(f'<span class="dock-glyph">{glyph}</span>', button)

    def test_page_removed_agent_runtime_management_copy(self) -> None:
        combined = f"{self.html}\n{self.js}".lower()
        for forbidden in ("hermes", "astrbot", "mcp", "runtime sidecar", "/api/runtime/status", "/api/hermes/status", "/api/mcp/", "/api/skills"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)

    def test_human_ops_click_preview_is_reviewable_and_visible(self) -> None:
        self.assertIn("click-preview-dot", self.html)
        self.assertIn("click-preview-dot", self.js)
        self.assertIn("clickPreviewDot", self.form_js)
        self.assertIn("act 需要批准", self.html)

    def test_human_ops_exposes_playwright_chrome_profile_picker(self) -> None:
        self.assertIn('id="ops-playwright-profile"', self.html)
        self.assertIn('id="ops-playwright-profile-refresh"', self.html)
        self.assertIn('id="ops-playwright-profile-status"', self.html)
        self.assertIn('/api/settings/chrome-profiles', self.js)
        self.assertIn('playwright_profile: stringValue(els.opsPlaywrightProfile)', self.form_js)

    def test_human_ops_exposes_explicit_full_authorization_mode(self) -> None:
        self.assertIn('id="ops-authorization-mode"', self.html)
        self.assertIn('<option value="review">', self.html)
        self.assertIn('<option value="full">完全授权（动作免审批）</option>', self.html)
        self.assertIn("每步执行前通知", self.html)
        self.assertIn('authorization_mode: "review"', self.form_js)
        self.assertIn("require_act_review: authorizationMode !== \"full\"", self.form_js)

    def test_human_ops_exposes_persistent_ax_index_refresh(self) -> None:
        self.assertIn('id="ops-ax-index-refresh"', self.html)
        self.assertIn('id="ops-ax-index-status"', self.html)
        self.assertIn("data/ax_trees/", self.html)
        self.assertIn("全部运行中的 GUI 应用", self.html)
        self.assertIn("普通 macOS GUI 应用", self.html)
        self.assertIn("/api/settings/accessibility-index", self.js)
        self.assertIn("/api/settings/accessibility-index/refresh", self.js)
        self.assertIn("refreshAccessibilityIndex", self.js)
        self.assertIn("安全跳过 Ipet 自身", self.js)

    def test_brain_provider_selector_supports_api_formats_including_google_aistudio(self) -> None:
        self.assertIn('id="brain-provider"', self.html)
        combined_js = f"{self.js}\n{self.form_js}"
        for provider in ("openai_compatible", "ollama", "anthropic_compatible", "google_aistudio", "codex"):
            with self.subTest(provider=provider):
                self.assertIn(f'value="{provider}"', self.html)
                self.assertIn(provider, combined_js)
        self.assertIn("brainProvider", self.form_js)
        self.assertIn("provider:", self.form_js)
        self.assertIn("model_endpoint: isEndpointlessProvider(brainProvider) ? \"\" : stringValue(els.brainModelEndpoint)", self.form_js)
        self.assertIn("Google AI Studio 只需要 API Key", self.form_js)
        self.assertIn("复用本机 Codex 登录与账户额度", self.form_js)
        self.assertIn('id="brain-reasoning-effort"', self.html)
        self.assertIn("brainReasoningEffort", self.js)
        self.assertIn("reasoning_effort: stringValue(els.brainReasoningEffort)", self.form_js)
        self.assertIn('id="brain-streaming-enabled"', self.html)
        self.assertIn("brainStreamingEnabled", self.js)
        self.assertIn("streaming_enabled: !!els.brainStreamingEnabled?.checked", self.form_js)
        self.assertIn('id="brain-web-search-enabled"', self.html)
        self.assertIn("brainWebSearchEnabled", self.js)
        self.assertIn("web_search_enabled: !!els.brainWebSearchEnabled?.checked", self.form_js)
        self.assertIn('currentModel === "gpt-5.4"', self.form_js)
        self.assertNotIn("next.chat.backend_url = stringValue(els.brainModelEndpoint", self.form_js)
        read_form = self.form_js.split("  function readForm() {", 1)[1].split("  return {", 1)[0]
        self.assertNotIn("backend_url", read_form)

    def test_brain_model_fetch_button_and_one_click_fill_exist(self) -> None:
        self.assertIn('id="brain-fetch-models-btn"', self.html)
        self.assertIn('id="brain-model-list"', self.html)
        self.assertIn('id="brain-model-status"', self.html)
        self.assertIn("brainFetchModelsBtn", self.js)
        self.assertIn("/api/brain/models", self.model_picker_js)
        self.assertIn("renderBrainModelOptions", self.js)
        self.assertIn("renderBrainModelOptions", self.model_picker_js)
        self.assertIn("target.value = model.id", self.model_picker_js)
        self.assertIn("model.isDefault", self.model_picker_js)
        self.assertIn("renderBrainReasoningOptions", self.model_picker_js)

    def test_brain_persona_prompt_picker_shows_selected_file_and_preview(self) -> None:
        for element_id in (
            "brain-persona-prompt-select-btn",
            "brain-persona-prompt-clear-btn",
            "brain-persona-prompt-file",
            "brain-persona-prompt-current",
            "brain-persona-prompt-preview",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn("/api/settings/persona-prompts", self.js)
        self.assertIn("persona_prompt_file: stringValue(els.brainPersonaPromptFile)", self.form_js)
        self.assertIn("renderPersonaPromptState", self.js)
        self.assertNotIn('id="brain-persona"', self.html)
        self.assertNotIn('id="brain-response-style"', self.html)

    def test_observe_model_provider_supports_codex_without_endpoint_or_key(self) -> None:
        brain_provider = self.html.split('<select id="brain-provider">', 1)[1].split("</select>", 1)[0]
        observe_provider = self.html.split('<select id="ops-observe-model-provider">', 1)[1].split("</select>", 1)[0]
        codex_option = '<option value="codex">Codex（本机账户）</option>'
        self.assertEqual(brain_provider.count(codex_option), 1)
        self.assertEqual(observe_provider.count(codex_option), 1)
        self.assertIn("const observeCodex = isCodex", self.form_js)
        self.assertIn("Codex observe 复用本机登录与账户额度", self.form_js)
        self.assertIn("model_endpoint: isEndpointlessProvider(observeProvider)", self.form_js)

    def test_settings_js_still_uses_existing_config_endpoint(self) -> None:
        self.assertIn("/api/settings/config", self.js)
        self.assertIn("PUT", self.js)

    def test_settings_js_has_static_preview_fallback(self) -> None:
        self.assertIn("fallbackSettings", self.js)
        self.assertIn("static_preview", self.form_js)


if __name__ == "__main__":
    unittest.main()
