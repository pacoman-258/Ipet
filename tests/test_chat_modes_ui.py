from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
INDEX_CSS = ROOT / "frontend" / "index.css"
INDEX_APP_REFS_JS = ROOT / "frontend" / "app_refs.js"
INDEX_HELPERS_JS = ROOT / "frontend" / "index_helpers.js"
INDEX_SHELL_VISUALS_JS = ROOT / "frontend" / "shell_visuals.js"
INDEX_SHELL_BRIDGE_JS = ROOT / "frontend" / "shell_bridge.js"
INDEX_HUMAN_OPS_PREVIEW_JS = ROOT / "frontend" / "human_ops_preview.js"
INDEX_CHAT_MODES_JS = ROOT / "frontend" / "chat_modes.js"
INDEX_CHAT_INPUT_STATE_JS = ROOT / "frontend" / "chat_input_state.js"
INDEX_ASR_JS = ROOT / "frontend" / "asr.js"
INDEX_CHAT_TOPIC_HISTORY_JS = ROOT / "frontend" / "chat_topic_history.js"
INDEX_CHAT_SKILLS_JS = ROOT / "frontend" / "chat_skills.js"
INDEX_CHAT_SIDEBARS_JS = ROOT / "frontend" / "chat_sidebars.js"
INDEX_SPEECH_JS = ROOT / "frontend" / "speech.js"
INDEX_CHAT_WORKLOG_JS = ROOT / "frontend" / "chat_worklog.js"
INDEX_CHAT_STREAM_JS = ROOT / "frontend" / "chat_stream.js"
INDEX_CHAT_SUBMIT_JS = ROOT / "frontend" / "chat_submit.js"
INDEX_CHAT_MESSAGES_JS = ROOT / "frontend" / "chat_messages.js"
INDEX_CHAT_PANEL_JS = ROOT / "frontend" / "chat_panel.js"
INDEX_PET_SCENE_JS = ROOT / "frontend" / "pet_scene.js"
INDEX_APP_BOOTSTRAP_JS = ROOT / "frontend" / "app_bootstrap.js"
INDEX_CONTROLLER_FACADE_JS = ROOT / "frontend" / "controller_facade.js"
INDEX_CONTROLLER_GRAPH_CHAT_SECTIONS_JS = ROOT / "frontend" / "controller_graph_chat_sections.js"
INDEX_CONTROLLER_GRAPH_APP_SECTIONS_JS = ROOT / "frontend" / "controller_graph_app_sections.js"
INDEX_CONTROLLER_GRAPH_SECTIONS_JS = ROOT / "frontend" / "controller_graph_sections.js"
INDEX_CONTROLLER_GRAPH_JS = ROOT / "frontend" / "controller_graph.js"
INDEX_JS = ROOT / "frontend" / "index.js"
SETTINGS_HTML = ROOT / "settings.html"
SETTINGS_JS = ROOT / "settings.js"
SETTINGS_FORM_JS = ROOT / "settings_form.js"
SETTINGS_MODEL_PICKER_JS = ROOT / "settings_model_picker.js"


def read_index_ui_source() -> str:
    parts = [INDEX_HTML.read_text(encoding="utf-8")]
    for path in (
        INDEX_CSS,
        INDEX_APP_REFS_JS,
        INDEX_HELPERS_JS,
        INDEX_SHELL_VISUALS_JS,
        INDEX_SHELL_BRIDGE_JS,
        INDEX_HUMAN_OPS_PREVIEW_JS,
        INDEX_CHAT_MODES_JS,
        INDEX_CHAT_INPUT_STATE_JS,
        INDEX_ASR_JS,
        INDEX_CHAT_TOPIC_HISTORY_JS,
        INDEX_CHAT_SKILLS_JS,
        INDEX_CHAT_SIDEBARS_JS,
        INDEX_SPEECH_JS,
        INDEX_CHAT_WORKLOG_JS,
        INDEX_CHAT_STREAM_JS,
        INDEX_CHAT_SUBMIT_JS,
        INDEX_CHAT_MESSAGES_JS,
        INDEX_CHAT_PANEL_JS,
        INDEX_PET_SCENE_JS,
        INDEX_APP_BOOTSTRAP_JS,
        INDEX_CONTROLLER_FACADE_JS,
        INDEX_CONTROLLER_GRAPH_CHAT_SECTIONS_JS,
        INDEX_CONTROLLER_GRAPH_APP_SECTIONS_JS,
        INDEX_CONTROLLER_GRAPH_SECTIONS_JS,
        INDEX_CONTROLLER_GRAPH_JS,
        INDEX_JS,
    ):
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


class ChatModesUiTests(unittest.TestCase):
    def test_index_loads_split_frontend_assets(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('<link rel="stylesheet" href="./frontend/index.css" />', source)
        self.assertIn('<script src="./frontend/app_refs.js"></script>', source)
        self.assertIn('<script src="./frontend/index_helpers.js"></script>', source)
        self.assertIn('<script src="./frontend/shell_visuals.js"></script>', source)
        self.assertIn('<script src="./frontend/shell_bridge.js"></script>', source)
        self.assertIn('<script src="./frontend/human_ops_preview.js"></script>', source)
        self.assertIn('<script src="./frontend/chat_modes.js"></script>', source)
        self.assertIn('<script src="./frontend/chat_input_state.js"></script>', source)
        self.assertIn('<script src="./frontend/asr.js"></script>', source)
        self.assertIn('<script src="./frontend/chat_topic_history.js"></script>', source)
        self.assertIn('<script src="./frontend/chat_skills.js"></script>', source)
        self.assertIn('<script src="./frontend/chat_sidebars.js"></script>', source)
        self.assertIn('<script src="./frontend/speech.js"></script>', source)
        self.assertIn('<script src="./frontend/chat_worklog.js"></script>', source)
        self.assertIn('<script src="./frontend/chat_stream.js"></script>', source)
        self.assertIn('<script src="./frontend/chat_submit.js"></script>', source)
        self.assertIn('<script src="./frontend/chat_messages.js"></script>', source)
        self.assertIn('<script src="./frontend/chat_panel.js"></script>', source)
        self.assertIn('<script src="./frontend/pet_scene.js"></script>', source)
        self.assertIn('<script src="./frontend/app_bootstrap.js"></script>', source)
        self.assertIn('<script src="./frontend/controller_facade.js"></script>', source)
        self.assertIn('<script src="./frontend/controller_graph_chat_sections.js"></script>', source)
        self.assertIn('<script src="./frontend/controller_graph_app_sections.js"></script>', source)
        self.assertIn('<script src="./frontend/controller_graph_sections.js"></script>', source)
        self.assertIn('<script src="./frontend/controller_graph.js"></script>', source)
        self.assertIn('<script src="./frontend/index.js"></script>', source)
        self.assertLess(
            source.find('<script src="./frontend/app_refs.js"></script>'),
            source.find('<script src="./frontend/index_helpers.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/index_helpers.js"></script>'),
            source.find('<script src="./frontend/shell_visuals.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/shell_visuals.js"></script>'),
            source.find('<script src="./frontend/shell_bridge.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/shell_bridge.js"></script>'),
            source.find('<script src="./frontend/human_ops_preview.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/human_ops_preview.js"></script>'),
            source.find('<script src="./frontend/chat_modes.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/chat_modes.js"></script>'),
            source.find('<script src="./frontend/chat_input_state.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/chat_input_state.js"></script>'),
            source.find('<script src="./frontend/asr.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/asr.js"></script>'),
            source.find('<script src="./frontend/chat_topic_history.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/chat_topic_history.js"></script>'),
            source.find('<script src="./frontend/chat_skills.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/chat_skills.js"></script>'),
            source.find('<script src="./frontend/chat_sidebars.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/chat_sidebars.js"></script>'),
            source.find('<script src="./frontend/speech.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/speech.js"></script>'),
            source.find('<script src="./frontend/chat_worklog.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/chat_worklog.js"></script>'),
            source.find('<script src="./frontend/chat_stream.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/chat_stream.js"></script>'),
            source.find('<script src="./frontend/chat_submit.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/chat_submit.js"></script>'),
            source.find('<script src="./frontend/chat_messages.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/chat_messages.js"></script>'),
            source.find('<script src="./frontend/chat_panel.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/chat_panel.js"></script>'),
            source.find('<script src="./frontend/pet_scene.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/pet_scene.js"></script>'),
            source.find('<script src="./frontend/app_bootstrap.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/app_bootstrap.js"></script>'),
            source.find('<script src="./frontend/controller_facade.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/controller_facade.js"></script>'),
            source.find('<script src="./frontend/controller_graph_chat_sections.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/controller_graph_chat_sections.js"></script>'),
            source.find('<script src="./frontend/controller_graph_app_sections.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/controller_graph_app_sections.js"></script>'),
            source.find('<script src="./frontend/controller_graph_sections.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/controller_graph_sections.js"></script>'),
            source.find('<script src="./frontend/controller_graph.js"></script>'),
        )
        self.assertLess(
            source.find('<script src="./frontend/controller_graph.js"></script>'),
            source.find('<script src="./frontend/index.js"></script>'),
        )
        self.assertTrue(INDEX_CSS.exists())
        self.assertTrue(INDEX_APP_REFS_JS.exists())
        self.assertTrue(INDEX_HELPERS_JS.exists())
        self.assertTrue(INDEX_SHELL_VISUALS_JS.exists())
        self.assertTrue(INDEX_SHELL_BRIDGE_JS.exists())
        self.assertTrue(INDEX_HUMAN_OPS_PREVIEW_JS.exists())
        self.assertTrue(INDEX_CHAT_MODES_JS.exists())
        self.assertTrue(INDEX_CHAT_INPUT_STATE_JS.exists())
        self.assertTrue(INDEX_ASR_JS.exists())
        self.assertTrue(INDEX_CHAT_TOPIC_HISTORY_JS.exists())
        self.assertTrue(INDEX_CHAT_SKILLS_JS.exists())
        self.assertTrue(INDEX_CHAT_SIDEBARS_JS.exists())
        self.assertTrue(INDEX_SPEECH_JS.exists())
        self.assertTrue(INDEX_CHAT_WORKLOG_JS.exists())
        self.assertTrue(INDEX_CHAT_STREAM_JS.exists())
        self.assertTrue(INDEX_CHAT_SUBMIT_JS.exists())
        self.assertTrue(INDEX_CHAT_MESSAGES_JS.exists())
        self.assertTrue(INDEX_CHAT_PANEL_JS.exists())
        self.assertTrue(INDEX_PET_SCENE_JS.exists())
        self.assertTrue(INDEX_APP_BOOTSTRAP_JS.exists())
        self.assertTrue(INDEX_CONTROLLER_FACADE_JS.exists())
        self.assertTrue(INDEX_CONTROLLER_GRAPH_CHAT_SECTIONS_JS.exists())
        self.assertTrue(INDEX_CONTROLLER_GRAPH_APP_SECTIONS_JS.exists())
        self.assertTrue(INDEX_CONTROLLER_GRAPH_SECTIONS_JS.exists())
        self.assertTrue(INDEX_CONTROLLER_GRAPH_JS.exists())
        self.assertTrue(INDEX_JS.exists())
        self.assertIn("window.IpetAppRefs", read_index_ui_source())
        self.assertIn("window.IpetIndexHelpers", read_index_ui_source())
        self.assertIn("window.IpetShellVisuals", read_index_ui_source())
        self.assertIn("window.IpetShellBridge", read_index_ui_source())
        self.assertIn("window.IpetHumanOpsPreview", read_index_ui_source())
        self.assertIn("window.IpetChatModes", read_index_ui_source())
        self.assertIn("window.IpetChatInputState", read_index_ui_source())
        self.assertIn("window.IpetAsr", read_index_ui_source())
        self.assertIn("window.IpetChatTopicHistory", read_index_ui_source())
        self.assertIn("window.IpetChatSkills", read_index_ui_source())
        self.assertIn("window.IpetChatSidebars", read_index_ui_source())
        self.assertIn("window.IpetSpeech", read_index_ui_source())
        self.assertIn("window.IpetChatWorklog", read_index_ui_source())
        self.assertIn("window.IpetChatStream", read_index_ui_source())
        self.assertIn("window.IpetChatSubmit", read_index_ui_source())
        self.assertIn("window.IpetChatMessages", read_index_ui_source())
        self.assertIn("window.IpetChatPanel", read_index_ui_source())
        self.assertIn("window.IpetPetScene", read_index_ui_source())
        self.assertIn("window.IpetControllerFacade", read_index_ui_source())
        self.assertIn("window.IpetControllerGraphChatSections", read_index_ui_source())
        self.assertIn("window.IpetControllerGraphAppSections", read_index_ui_source())
        self.assertIn("window.IpetControllerGraphSections", read_index_ui_source())
        self.assertIn("window.IpetControllerGraph", read_index_ui_source())

    def test_index_contains_chat_mode_buttons_and_payload(self) -> None:
        source = read_index_ui_source()
        app_bootstrap_source = INDEX_APP_BOOTSTRAP_JS.read_text(encoding="utf-8")
        chat_sections_source = INDEX_CONTROLLER_GRAPH_CHAT_SECTIONS_JS.read_text(encoding="utf-8")
        app_sections_source = INDEX_CONTROLLER_GRAPH_APP_SECTIONS_JS.read_text(encoding="utf-8")
        self.assertIn('data-chat-mode="react"', source)
        self.assertIn('data-chat-mode="chat"', source)
        self.assertIn('data-chat-mode="skill"', source)
        self.assertIn('data-memory-mode="persistent"', source)
        self.assertIn('data-memory-mode="temporary"', source)
        self.assertIn(
            'chatModeButtons: Array.from(runtimeDocument.querySelectorAll(".chat-mode-btn[data-chat-mode]"))',
            source,
        )
        self.assertIn(
            'memoryModeButtons: Array.from(runtimeDocument.querySelectorAll(".chat-mode-btn[data-memory-mode]"))',
            source,
        )
        self.assertIn('let currentMemoryMode = "persistent"', source)
        self.assertIn(
            "const chatModesController = runtimeWindow.IpetChatModes.createChatModesController",
            chat_sections_source,
        )
        self.assertIn("function normalizeMemoryMode", source)
        self.assertIn("chat_mode: currentChatMode", source)
        self.assertIn("memory_mode: currentMemoryMode", source)
        self.assertIn('if (getCurrentMemoryMode() !== "temporary")', source)
        self.assertIn('if (getMemoryMode() === "temporary") {\n        renderTopicHistoryList();\n        return [];', source)
        self.assertIn('if (getCurrentMemoryMode() !== "temporary" && topicHistoryEnabled())', source)
        self.assertTrue(INDEX_CONTROLLER_FACADE_JS.exists(), "frontend/controller_facade.js should exist")
        facade_source = INDEX_CONTROLLER_FACADE_JS.read_text(encoding="utf-8")
        self.assertIn('"setMemoryMode": ["chatModes", "setMemoryMode"]', facade_source)
        self.assertIn("setMemoryMode: facade.setMemoryMode", app_sections_source)
        self.assertNotRegex(INDEX_JS.read_text(encoding="utf-8"), r"function setMemoryMode\(")
        self.assertIn('const previousMode = getCurrentMemoryMode();', app_bootstrap_source)
        self.assertIn('setMemoryMode(button.dataset.memoryMode);', app_bootstrap_source)
        self.assertIn('if (getCurrentMemoryMode() !== previousMode)', app_bootstrap_source)
        self.assertIn('await createNewTopic(false);', app_bootstrap_source)
        self.assertIn('function guardConversationChange()', app_bootstrap_source)
        self.assertIn('if (!guardConversationChange())', app_bootstrap_source)
        self.assertIn('getChatState: () => graphState.chatState', app_sections_source)
        memory_sync_snippet = 'if (payload.memory_mode) {\n          setCurrentMemoryMode(normalizeMemoryMode(payload.memory_mode));\n          applyMemoryModeUI();\n        }'
        self.assertEqual(source.count(memory_sync_snippet), 2)
        self.assertIn('model: state.chat.model || "gpt-5.4"', source)
        self.assertNotIn("router_enabled: !!state.chat.router_enabled", source)
        self.assertNotIn('router_model: state.chat.router_model || ""', source)

    def test_index_contains_sidebar_buttons_in_new_order(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        settings_pos = source.find("<span>设置</span>")
        history_pos = source.find("<span>历史</span>")
        new_chat_pos = source.find("<span>新对话</span>")
        self.assertNotEqual(settings_pos, -1)
        self.assertNotEqual(history_pos, -1)
        self.assertNotEqual(new_chat_pos, -1)
        self.assertLess(settings_pos, history_pos)
        self.assertLess(history_pos, new_chat_pos)
        self.assertIn('id="nav-history"', source)
        self.assertIn('id="nav-chat"', source)

    def test_index_contains_topic_history_ui_and_api_hooks(self) -> None:
        source = read_index_ui_source()
        self.assertIn('id="chat-history-drawer"', source)
        self.assertIn('id="chat-history-list"', source)
        self.assertIn('id="chat-history-refresh"', source)
        self.assertIn('/api/chat/topics', source)
        self.assertIn('async function createNewTopic', source)
        self.assertIn('const temporaryId = `temporary-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`', source)
        self.assertIn('persisted: false', source)
        self.assertIn('async function loadTopicHistory', source)
        self.assertIn('async function selectTopic', source)
        self.assertIn('async function deleteTopic', source)
        self.assertIn('chat-history-delete', source)
        self.assertIn('pendingDeleteTopicId', source)
        self.assertIn('armPendingDeleteTopic', source)
        self.assertIn('clearPendingDeleteTopic', source)
        self.assertIn('\u786e\u8ba4\u5220\u9664', source)
        self.assertIn('chat-history-delete.is-danger', source)
        self.assertIn('deleteButton.textContent = isPendingDelete ? "\\u786e\\u8ba4\\u5220\\u9664" : "\\u5220\\u9664"', source)
        self.assertIn('deleteButton.setAttribute("aria-label", isPendingDelete ? "\\u786e\\u8ba4\\u5220\\u9664\\u8bdd\\u9898" : "\\u5220\\u9664\\u8bdd\\u9898")', source)
        self.assertNotIn('window.confirm', source)
        self.assertIn('method: "DELETE"', source)
        self.assertIn('/delete', source)
        self.assertIn('Array.isArray(payload.topics)', source)
        self.assertNotIn('405\\uff08\\u65b9\\u6cd5\\u4e0d\\u5141\\u8bb8\\uff09', source)
        self.assertIn('topic_id', source)
        self.assertIn('chat-asr-status', source)
        self.assertIn('function canStartPushToTalk', source)
        self.assertIn('startPushToTalk', source)
        self.assertIn('/api/asr/stream', source)
        self.assertIn('/api/health', source)
        self.assertIn('/api/asr/warmup', source)
        self.assertIn('state.chat?.asr?.api_base_url', source)
        self.assertIn('function candidateAsrBaseUrls()', source)
        self.assertIn('function buildAsrWarmupUrl(baseUrl = "")', source)
        self.assertIn('callQtBridge("startAsrWarmup")', source)
        self.assertIn('function isProbablyMacOS()', source)
        self.assertIn('function pushToTalkKeyLabel()', source)
        self.assertIn('return key === "Alt" && isProbablyMacOS() ? "Option" : key;', source)
        self.assertIn('event.code === "AltLeft"', source)
        self.assertIn('\\u8bed\\u97f3\\u8f93\\u5165\\u5df2\\u5173\\u95ed', source)
        self.assertIn('function shouldRetryAsrAvailability(message)', source)
        self.assertIn('String(state.chat?.backend_url || "").trim()', source)
        self.assertIn('let sawSocketError = false;', source)
        self.assertIn('let lastSocketErrorMessage = "";', source)
        self.assertIn('lastSocketErrorMessage || (sawSocketError ? "ASR WebSocket 连接错误" : "ASR 连接已关闭")', source)
        self.assertIn('while (token === asrRunToken && asrHotkeyPressed && !availability.ok) {', source)

    def test_index_tracks_thought_sessions_per_turn_boundary(self) -> None:
        source = read_index_ui_source()
        self.assertIn('let activeThoughtSessionId = "";', source)
        self.assertIn('const approvalThoughtSessionRefs = new Map();', source)
        self.assertNotIn('const memorySuggestionRefs = new Map();', source)
        self.assertIn("function createThoughtSessionId()", source)
        self.assertIn("function beginThoughtSession(existingSessionId = \"\")", source)
        self.assertIn("function clearActiveThoughtSession(sessionId = \"\")", source)
        self.assertIn("function ensureThoughtGroup(round = 1, pending = false, sessionId = \"\")", source)
        self.assertIn("currentThoughtGroup.sessionId === normalizedSessionId", source)
        self.assertIn("const thoughtSessionId = beginThoughtSession();", source)
        self.assertIn("chatWorklogController.resolveApprovalThoughtSessionId(turnKey)", source)
        self.assertIn("await consumeChatStream(resp, { thoughtSessionId })", source)
        self.assertIn("clearActiveThoughtSession(thoughtSessionId);", source)

    def test_index_supports_searcher_role_and_step_batch_meta(self) -> None:
        source = read_index_ui_source()
        self.assertIn('value === "searcher"', source)
        self.assertIn("function formatStepMeta(payload)", source)
        self.assertIn("步骤 ${stepIndex}", source)
        self.assertIn("批次：${batchSize} 次工具调用", source)
        self.assertIn("const metaText = formatStepMeta(payload);", source)
        self.assertNotIn('memory_save_suggestion', source)
        self.assertNotIn('/api/chat/memory/decision', source)

    def test_index_streams_tts_by_sentence_and_flushes_after_stream_finishes(self) -> None:
        source = read_index_ui_source()
        self.assertIn("function feedSpeakBuffer(delta, force = false, expr = null)", source)
        self.assertIn("const completedSentence = /[^。！？!?；;\\n]*[。！？!?；;\\n]+", source)
        self.assertIn("enqueueTTSChunk(sentence, sentenceExpr);", source)
        self.assertIn("if (force) {\n        const finalText = pendingSpeakBuffer.trim();", source)
        self.assertIn('feedSpeakBuffer("", true);', source)
        self.assertNotIn("pendingSpeakBuffer.match", source)
        self.assertNotIn("await fallbackSpeakByBrowser(nextText)", source)

    def test_index_respects_local_topic_history_capabilities(self) -> None:
        source = read_index_ui_source()
        self.assertIn("supports_history_detail: raw.supports_history_detail !== false", source)
        self.assertIn("supports_delete: raw.supports_delete !== false", source)
        self.assertIn("if (item.supports_delete) {", source)
        self.assertIn("if (payload.history_unavailable) {", source)
        self.assertIn("if (!resp.ok || payload.ok === false) {", source)
        self.assertNotIn("该话题由 AstrBot 管理，当前请在 AstrBot WebUI 删除。", source)
        self.assertNotIn("AstrBot 当前只开放会话列表；历史明细请在 AstrBot WebUI 查看。", source)

    def test_index_contains_codex_worklog_rendering_functions(self) -> None:
        source = read_index_ui_source()
        self.assertIn("function ensureAssistantWorklogTurn", source)
        self.assertIn("function appendWorklogPhase", source)
        self.assertIn("function updateWorklogFinalText", source)
        self.assertIn("function appendAssistantHistoryBlock", source)
        self.assertIn("assistant-worklog", source)
        self.assertIn("worklog-status-list", source)
        self.assertIn("worklog-final-text", source)

    def test_index_streams_ai_final_output_into_worklog_not_pet_bubble(self) -> None:
        source = read_index_ui_source()
        self.assertNotIn('finalMsg = appendMessage("pet"', source)
        self.assertNotIn('appendMessage("pet", text, {\n                  phase,', source)
        self.assertIn("updateWorklogFinalText(\n          ensureAssistantWorklogTurn", source)
        self.assertIn("appendWorklogPhase(payload", source)

    def test_index_user_messages_still_use_bubbles(self) -> None:
        source = read_index_ui_source()
        self.assertIn('appendMessage("user", userText, { speakerName: "你", assistantTurn: retryFromAssistantTurn });', source)
        self.assertIn('appendMessage("user", String(item.content || "")', source)

    def test_index_supports_edit_retry_from_user_message(self) -> None:
        source = read_index_ui_source()
        sections_source = INDEX_CONTROLLER_GRAPH_SECTIONS_JS.read_text(encoding="utf-8")

        self.assertIn("pendingRetryEdit: null,", sections_source)
        self.assertIn("chatMessageSequence: 0,", sections_source)
        self.assertIn('className = "msg-edit-button"', source)
        self.assertIn('title = "编辑并重试"', source)
        self.assertIn('function beginUserMessageEditRetry', source)
        self.assertIn('function truncateChatAfterMessage', source)
        self.assertIn('function clearPendingRetryEdit', source)
        self.assertIn('retry_from_assistant_turn: retryFromAssistantTurn,', source)
        self.assertIn('el.dataset.assistantTurn = String(assistantTurn);', source)
        self.assertIn('appendMessage("user", String(item.content || "")', source)
        self.assertIn('assistantTurn: Number(item.assistant_turn || 0) || null,', source)

    def test_index_routes_human_ops_approval_to_proposal_endpoint_and_red_dot_preview(self) -> None:
        source = read_index_ui_source()

        self.assertIn("function showHumanOpsClickPreview", source)
        self.assertIn("function hideHumanOpsClickPreview", source)
        self.assertIn(
            "const humanOpsPreviewController = runtimeWindow.IpetHumanOpsPreview.createHumanOpsPreviewController",
            source,
        )
        self.assertIn('id = "human-ops-click-preview-dot"', source)
        self.assertIn('className = "human-ops-click-preview-dot"', source)
        self.assertIn("window.screenX", source)
        self.assertIn("window.screenY", source)
        self.assertIn("qtBridge.showClickPreview(JSON.stringify(preview))", source)
        self.assertIn("qtBridge.hideClickPreview()", source)
        self.assertIn("previewInsideWindow", source)
        self.assertNotIn("Math.max(0, Math.min(window.innerWidth, viewportX))", source)
        self.assertNotIn("Math.max(0, Math.min(window.innerHeight, viewportY))", source)
        self.assertIn('payload.preview?.marker === "red_dot"', source)
        self.assertIn('typeof qtBridge.showApprovalNotification === "function"', source)
        self.assertIn("qtBridge.showApprovalNotification(JSON.stringify({", source)
        self.assertIn("bindApprovalNotificationDecisions", source)
        self.assertNotIn('approveBtn.textContent = "批准"', source)
        self.assertNotIn('rejectBtn.textContent = "拒绝"', source)
        self.assertNotIn('continueApproval(turnId, false, "", { native: true })', source)
        self.assertNotIn('const endpoint = nativeApproval ? "native-decision" : "decision";', source)
        self.assertNotIn('fetch(`${backend}/api/chat/approval`', source)

    def test_index_history_assistant_messages_render_without_pet_bubbles(self) -> None:
        source = read_index_ui_source()
        self.assertIn("appendAssistantHistoryBlock(item)", source)
        self.assertNotIn('appendMessage(role === "assistant" ? "pet" : "user"', source)

    def test_settings_replaces_old_topic_history_controls_with_neo_aspect_controls(self) -> None:
        html = SETTINGS_HTML.read_text(encoding="utf-8")
        js = SETTINGS_JS.read_text(encoding="utf-8")
        model_picker_js = SETTINGS_MODEL_PICKER_JS.read_text(encoding="utf-8")
        self.assertNotIn('id="chat-topic-history-enabled"', html)
        self.assertNotIn('id="chat-topic-history-summary-interval"', html)
        self.assertNotIn('id="chat-long-term-memory-enabled"', html)
        self.assertNotIn('id="chat-long-term-memory-project"', html)
        self.assertNotIn('id="chat-long-term-memory-read-enabled"', html)
        self.assertNotIn('id="chat-long-term-memory-ask-before-save"', html)
        self.assertIn('id="chat-asr-enabled"', html)
        self.assertIn('id="chat-asr-push-to-talk-key"', html)
        self.assertIn('id="memory-conversation-saving"', html)
        self.assertIn('id="memory-long-term-enabled"', html)
        self.assertIn('id="memory-review-queue"', html)
        self.assertIn('id="brain-decision-temperature"', html)
        self.assertIn('id="ops-observe-screen"', html)
        self.assertIn('id="ops-observe-model-enabled"', html)
        self.assertIn('id="ops-observe-model-provider"', html)
        self.assertIn('value="google_aistudio"', html)
        self.assertIn('id="ops-observe-model-endpoint"', html)
        self.assertIn('id="ops-observe-fetch-models-btn"', html)
        self.assertIn('id="ops-observe-model-list"', html)
        self.assertIn('id="ops-observe-model-name"', html)
        self.assertIn('id="ops-observe-api-key"', html)
        self.assertIn('id="ops-require-act-review"', html)
        self.assertIn('id="click-preview-dot"', html)
        self.assertIn('observe_model', js)
        self.assertIn('function fetchObserveModels', js)
        self.assertIn('scope: "observe"', model_picker_js)
        self.assertNotIn('id="chat-asr-interim-results"', html)
        self.assertNotIn('id="vision-enabled"', html)
        self.assertNotIn('id="vision-analyzer-api-key"', html)
        self.assertNotIn('id="chat-max-reasoning-steps"', html)
        self.assertNotIn("运行时与语音", html)
        self.assertNotIn("默认 AstrBot 连接", html)
        self.assertNotIn('id="hermes-enabled"', html)
        self.assertNotIn('id="hermes-auto-start"', html)
        self.assertNotIn('id="hermes-base-url"', html)
        self.assertNotIn('id="hermes-command"', html)
        self.assertNotIn('id="hermes-status-detail"', html)
        self.assertNotIn("/api/hermes/status", js)

    def test_settings_excludes_removed_platform_controls(self) -> None:
        html = SETTINGS_HTML.read_text(encoding="utf-8")
        js = SETTINGS_JS.read_text(encoding="utf-8")
        form_js = SETTINGS_FORM_JS.read_text(encoding="utf-8")
        settings_source = "\n".join((html, js, form_js))
        self.assertIn('id="brain-model-endpoint"', html)
        self.assertIn('id="brain-model-name"', html)
        self.assertIn('id="brain-api-key"', html)
        self.assertIn('id="skills-recipes-enabled"', html)
        self.assertIn('id="skills-proposal-queue"', html)
        self.assertNotIn('id="runtime-active-hermes"', html)
        self.assertNotIn('id="runtime-active-astrbot"', html)
        self.assertNotIn("AstrBot 保持默认", html)
        self.assertNotIn('runtime.active || "astrbot"', js)
        self.assertNotIn('els.runtimeActiveHermes.checked = activeRuntime === "hermes"', js)
        self.assertNotIn('els.runtimeActiveAstrBot.checked = activeRuntime !== "hermes"', js)
        self.assertNotIn('active: els.runtimeActiveHermes?.checked ? "hermes" : "astrbot"', js)
        self.assertNotIn('id="astrbot-base-url"', html)
        self.assertNotIn('id="astrbot-api-key"', html)
        self.assertNotIn('id="astrbot-api-key-clear"', html)
        self.assertNotIn('id="napcatqq-reverse-ws-url"', html)
        self.assertNotIn("/api/runtime/status", js)
        self.assertNotIn('api_key_action: astrobotApiKeyAction', js)
        self.assertNotIn("ws://127.0.0.1:6199/ws", js)
        self.assertNotIn('id="runtime-main-model-presets"', html)
        self.assertNotIn('id="runtime-session-presets"', html)
        self.assertNotIn('id="runtime-router-model-presets"', html)
        self.assertNotIn('id="fetch-remote-models-btn"', html)
        self.assertNotIn('id="fetch-router-models-btn"', html)
        self.assertNotIn('max="12"', html)
        self.assertNotIn('chatTopicHistoryEnabled', js)
        self.assertNotIn('chatTopicHistorySummaryInterval', js)
        self.assertNotIn('chatLongTermMemoryEnabled', js)
        self.assertNotIn('chatLongTermMemoryProject', js)
        self.assertNotIn('chatLongTermMemoryReadEnabled', js)
        self.assertNotIn('chatLongTermMemoryAskBeforeSave', js)
        self.assertIn('chatAsrEnabled', settings_source)
        self.assertIn('chatAsrPushToTalkKey', settings_source)
        self.assertIn('opsObserveScreen', js)
        self.assertIn('opsRequireActReview', js)
        self.assertNotIn('chatAsrInterimResults', js)
        self.assertNotIn('visionEnabled', js)
        self.assertNotIn('visionForceGrounding', js)
        self.assertNotIn('visionActiveObservationEnabled', js)
        self.assertNotIn('visionActiveObservationInteraction', js)
        self.assertNotIn('visionPassiveCaptureUseForForced', js)
        self.assertNotIn('active_observation', js)
        self.assertNotIn('passive_capture', js)
        self.assertNotIn('visionRoutingVisualChangeThreshold', js)
        self.assertNotIn('visionRoutingVlmCooldownSec', js)
        self.assertNotIn('visionRoutingStableAfterChangeMs', js)
        self.assertNotIn('visionAnalyzerEnabled', js)
        self.assertNotIn('visionAnalyzerProvider', js)
        self.assertNotIn('visionAnalyzerBaseUrl', js)
        self.assertNotIn('visionAnalyzerModel', js)
        self.assertNotIn('visionAnalyzerApiKey', js)
        self.assertNotIn('visionAnalyzerApiKeyClear', js)
        self.assertNotIn('fetchVisionAnalyzerModelsBtn', js)
        self.assertNotIn('loadVisionAnalyzerModels', js)
        self.assertNotIn('/api/vision/models', js)
        self.assertNotIn('visionAnalyzerImageDetail', js)
        self.assertNotIn('visionAnalyzerMaxTextChars', js)
        self.assertNotIn('visionAnalyzerMaxObservations', js)
        self.assertNotIn('visionAnalyzerFallbackToRuntime', js)
        self.assertNotIn('evidence_count', js)
        self.assertNotIn('grounded', js)
        self.assertNotIn('last_route_decision', js)
        self.assertNotIn('capture_backend', js)
        self.assertNotIn('analyzer.last_status', js)
        self.assertNotIn('next.vision', js)
        self.assertNotIn('/api/vision/status', js)
        self.assertNotIn('next.chat.topic_history', js)
        self.assertNotIn('next.chat.long_term_memory', js)
        self.assertNotIn('config.chat?.long_term_memory', js)
        self.assertIn('next.chat.asr', form_js)
        self.assertIn('push_to_talk_key: stringValue(els.chatAsrPushToTalkKey, "Alt")', form_js)
        self.assertNotIn('normalizeLongTermMemoryConfig', js)
        self.assertNotIn("router", html.lower())
        self.assertNotIn("legacy", html.lower())
        self.assertNotIn("主 API", html)
        self.assertNotIn("副 API", html)
        self.assertNotIn("总结", html)
        self.assertNotIn('renderHermesRuntimeModelPresets', js)
        self.assertNotIn('renderHermesSessionPresets', js)
        self.assertNotIn('HERMES_INTERNAL_MODEL_PRESETS', js)
        self.assertNotIn('fetchRemoteModelsBtn', js)
        self.assertNotIn('fetchRouterModelsBtn', js)
        self.assertNotIn('populateMcpPresets(settingsPayload.mcp_server_presets)', js)
        self.assertNotIn('Object.entries(mcpServerPresets)', js)
        self.assertNotIn("Hermes MCP", html)
        self.assertNotIn("统一管理写入 Hermes 的 MCP 服务", html)
        self.assertNotIn("Hermes 技能", html)
        self.assertNotIn("导入并写入 Hermes", html)
        self.assertNotIn("创建并写入", html)
        self.assertNotIn('</section>\n\n      <section id="mcp"', html)
        self.assertNotIn("内建文件工具", js)
        self.assertNotIn("Hermes MCP 服务已写入", js)
        self.assertNotIn("本地 Hermes 技能已写入", js)
        self.assertNotIn('const isBuiltin = !!item.builtin || item.source_type === "builtin";', js)
        self.assertNotIn("内建文件工具会随运行时自动注册", js)


if __name__ == "__main__":
    unittest.main()
