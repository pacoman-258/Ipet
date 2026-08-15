(() => {
  function createAppConfigController(deps = {}) {
    const state = deps.state || {};
    const refs = deps.refs || {};
    const chatHistoryDrawerEl = refs.chatHistoryDrawerEl;
    const asrController = deps.asrController || {};
    const chatSidebarsController = deps.chatSidebarsController || {};
    const petSceneController = deps.petSceneController || {};
    const proactivePresenceController = deps.proactivePresenceController || {};
    const gamePresenceController = deps.gamePresenceController || {};

    const syncPetDisplayName = typeof deps.syncPetDisplayName === "function" ? deps.syncPetDisplayName : () => {};
    const normalizeAsrConfig =
      typeof deps.normalizeAsrConfig === "function" ? deps.normalizeAsrConfig : (value) => value || {};
    const topicHistoryEnabled = typeof deps.topicHistoryEnabled === "function" ? deps.topicHistoryEnabled : () => false;
    const renderTopicHistoryList =
      typeof deps.renderTopicHistoryList === "function" ? deps.renderTopicHistoryList : () => {};
    const applyChatModeUI = typeof deps.applyChatModeUI === "function" ? deps.applyChatModeUI : () => {};
    const applyMemoryModeUI = typeof deps.applyMemoryModeUI === "function" ? deps.applyMemoryModeUI : () => {};
    const updateShellButtons = typeof deps.updateShellButtons === "function" ? deps.updateShellButtons : () => {};
    const cancelAsrSession = typeof deps.cancelAsrSession === "function" ? deps.cancelAsrSession : () => {};
    const defaultAsrStatusText =
      typeof deps.defaultAsrStatusText === "function" ? deps.defaultAsrStatusText : () => "";
    const setAsrStatus = typeof deps.setAsrStatus === "function" ? deps.setAsrStatus : () => {};
    const syncChatInputAvailability =
      typeof deps.syncChatInputAvailability === "function" ? deps.syncChatInputAvailability : () => {};
    const backgroundOverlayValue =
      typeof deps.backgroundOverlayValue === "function" ? deps.backgroundOverlayValue : (value) => value;
    const applyBackgroundVisuals =
      typeof deps.applyBackgroundVisuals === "function" ? deps.applyBackgroundVisuals : () => {};
    const loadModel = typeof deps.loadModel === "function" ? deps.loadModel : async () => {};
    const showError = typeof deps.showError === "function" ? deps.showError : () => {};
    const applyModelTransform =
      typeof deps.applyModelTransform === "function" ? deps.applyModelTransform : () => {};
    const refreshStatus = typeof deps.refreshStatus === "function" ? deps.refreshStatus : () => {};

    async function applyConfig(config) {
      const incoming = config || {};

      if (incoming.pet && typeof incoming.pet === "object") {
        Object.assign(state, incoming.pet);
      } else {
        Object.assign(state, incoming);
      }
      if (incoming.game_capability) {
        state.game_capability = String(incoming.game_capability || "");
      }
      if (incoming.chat && typeof incoming.chat === "object") {
        Object.assign(state.chat, incoming.chat);
        state.chat.asr = normalizeAsrConfig(state.chat?.asr || {});
        const configuredDefaults = Array.isArray(incoming.chat.skills?.default_active_ids)
          ? incoming.chat.skills.default_active_ids
          : [];
        chatSidebarsController.setConfiguredSkillDefaults(configuredDefaults);
        chatSidebarsController.syncTopicFromConfig();
        if (!topicHistoryEnabled()) {
          chatHistoryDrawerEl.classList.remove("open");
        }
        renderTopicHistoryList();
        applyChatModeUI();
        applyMemoryModeUI();
        updateShellButtons();
        if (!state.chat.asr.enabled && asrController.isBusy()) {
          cancelAsrSession({ restoreInput: true, statusMessage: defaultAsrStatusText(), tone: "idle" });
        } else {
          setAsrStatus(defaultAsrStatusText(), "idle");
          syncChatInputAvailability();
        }
      }
      if (incoming.environment && typeof incoming.environment === "object") {
        proactivePresenceController.applyConfig?.(incoming.environment);
      }
      if (incoming.game && typeof incoming.game === "object") {
        gamePresenceController.applyConfig?.(incoming.game);
      }
      syncPetDisplayName();
      state.chat.asr = normalizeAsrConfig(state.chat?.asr || {});
      setAsrStatus(defaultAsrStatusText(), "idle");
      syncChatInputAvailability();
      state.background_enabled = !!state.background_enabled;
      state.background_image = String(state.background_image || "");
      state.background_image_url = String(state.background_image_url || "");
      state.background_overlay_opacity = backgroundOverlayValue(state.background_overlay_opacity);
      applyBackgroundVisuals();

      const nextModelUrl = incoming.model_url || incoming.modelUrl || state.model_url;
      if (typeof nextModelUrl === "string" && nextModelUrl.length > 0) {
        const changed = nextModelUrl !== state.model_url;
        state.model_url = nextModelUrl;
        if (changed || !petSceneController.getCurrentModel()) {
          try {
            await loadModel(state.model_url);
          } catch (err) {
            showError(`加载模型失败：${err?.message || String(err)}`);
          }
        } else {
          applyModelTransform();
        }
      } else {
        applyModelTransform();
      }

      refreshStatus();
    }

    return Object.freeze({
      applyConfig,
    });
  }

  window.IpetAppConfig = Object.freeze({
    createAppConfigController,
  });
})();
