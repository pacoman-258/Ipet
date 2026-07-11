(() => {
  function createAppBootstrapController(deps = {}) {
    const state = deps.state || {};
    const refs = deps.refs || {};
    const runtimeWindow = deps.window || window;
    const petSceneController = deps.petSceneController || {};
    const asrController = deps.asrController || {};
    const chatPanelController = deps.chatPanelController || {};

    const canvas = refs.canvas;
    const navSettingsButtonEl = refs.navSettingsButtonEl;
    const navHistoryButtonEl = refs.navHistoryButtonEl;
    const navChatButtonEl = refs.navChatButtonEl;
    const windowMinimizeEl = refs.windowMinimizeEl;
    const windowCloseEl = refs.windowCloseEl;
    const chatModeButtons = refs.chatModeButtons || [];
    const memoryModeButtons = refs.memoryModeButtons || [];
    const chatCloseEl = refs.chatCloseEl;
    const chatSkillsToggleEl = refs.chatSkillsToggleEl;
    const chatHistoryRefreshEl = refs.chatHistoryRefreshEl;
    const chatSkillsRefreshEl = refs.chatSkillsRefreshEl;
    const chatSkillsResetEl = refs.chatSkillsResetEl;
    const chatSendEl = refs.chatSendEl;
    const chatInputEl = refs.chatInputEl;
    const chatStopEl = refs.chatStopEl;

    const setQtBridge = typeof deps.setQtBridge === "function" ? deps.setQtBridge : () => {};
    const showError = typeof deps.showError === "function" ? deps.showError : () => {};
    const logToQt = typeof deps.logToQt === "function" ? deps.logToQt : () => {};
    const openSettingsPage = typeof deps.openSettingsPage === "function" ? deps.openSettingsPage : () => {};
    const minimizeWindow = typeof deps.minimizeWindow === "function" ? deps.minimizeWindow : () => {};
    const closeWindow = typeof deps.closeWindow === "function" ? deps.closeWindow : () => {};
    const applyBackgroundVisuals =
      typeof deps.applyBackgroundVisuals === "function" ? deps.applyBackgroundVisuals : () => {};
    const setupStageInteraction =
      typeof deps.setupStageInteraction === "function" ? deps.setupStageInteraction : () => {};
    const openChat = typeof deps.openChat === "function" ? deps.openChat : () => {};
    const closeChat = typeof deps.closeChat === "function" ? deps.closeChat : () => {};
    const toggleChatHistoryDrawer =
      typeof deps.toggleChatHistoryDrawer === "function" ? deps.toggleChatHistoryDrawer : () => {};
    const toggleChatSkillsDrawer =
      typeof deps.toggleChatSkillsDrawer === "function" ? deps.toggleChatSkillsDrawer : () => {};
    const getCurrentMemoryMode =
      typeof deps.getCurrentMemoryMode === "function" ? deps.getCurrentMemoryMode : () => "persistent";
    const getChatState = typeof deps.getChatState === "function" ? deps.getChatState : () => "idle";
    const updateShellButtons = typeof deps.updateShellButtons === "function" ? deps.updateShellButtons : () => {};
    const createNewTopic = typeof deps.createNewTopic === "function" ? deps.createNewTopic : async () => {};
    const setChatMode = typeof deps.setChatMode === "function" ? deps.setChatMode : () => {};
    const setMemoryMode = typeof deps.setMemoryMode === "function" ? deps.setMemoryMode : () => {};
    const loadTopicHistory = typeof deps.loadTopicHistory === "function" ? deps.loadTopicHistory : () => {};
    const loadChatSkills = typeof deps.loadChatSkills === "function" ? deps.loadChatSkills : () => {};
    const resetChatSkillsToDefault =
      typeof deps.resetChatSkillsToDefault === "function" ? deps.resetChatSkillsToDefault : () => {};
    const submitChatInput = typeof deps.submitChatInput === "function" ? deps.submitChatInput : async () => {};
    const matchesPushToTalkKey =
      typeof deps.matchesPushToTalkKey === "function" ? deps.matchesPushToTalkKey : () => false;
    const shouldHandlePushToTalk =
      typeof deps.shouldHandlePushToTalk === "function" ? deps.shouldHandlePushToTalk : () => false;
    const getPendingRetryEdit =
      typeof deps.getPendingRetryEdit === "function" ? deps.getPendingRetryEdit : () => null;
    const clearPendingRetryEdit =
      typeof deps.clearPendingRetryEdit === "function" ? deps.clearPendingRetryEdit : () => {};
    const defaultAsrStatusText =
      typeof deps.defaultAsrStatusText === "function" ? deps.defaultAsrStatusText : () => "";
    const cancelAsrSession = typeof deps.cancelAsrSession === "function" ? deps.cancelAsrSession : () => {};
    const stopSpeaking = typeof deps.stopSpeaking === "function" ? deps.stopSpeaking : () => {};
    const startPushToTalk = typeof deps.startPushToTalk === "function" ? deps.startPushToTalk : async () => {};
    const stopPushToTalk = typeof deps.stopPushToTalk === "function" ? deps.stopPushToTalk : () => {};
    const applyConfig = typeof deps.applyConfig === "function" ? deps.applyConfig : async () => {};
    const loadModel = typeof deps.loadModel === "function" ? deps.loadModel : async () => {};
    const isRendererReady = typeof deps.isRendererReady === "function" ? deps.isRendererReady : () => false;
    const playMotionCompat = typeof deps.playMotionCompat === "function" ? deps.playMotionCompat : () => false;
    const streamChat = typeof deps.streamChat === "function" ? deps.streamChat : async () => {};
    const enqueueTTSChunk = typeof deps.enqueueTTSChunk === "function" ? deps.enqueueTTSChunk : () => {};
    const syncPetDisplayName = typeof deps.syncPetDisplayName === "function" ? deps.syncPetDisplayName : () => {};
    const applyChatModeUI = typeof deps.applyChatModeUI === "function" ? deps.applyChatModeUI : () => {};
    const applyMemoryModeUI = typeof deps.applyMemoryModeUI === "function" ? deps.applyMemoryModeUI : () => {};
    const refreshStatus = typeof deps.refreshStatus === "function" ? deps.refreshStatus : () => {};

    let pendingConfig = null;

    function canChangeConversation() {
      return ["idle", "awaiting_followup_input"].includes(getChatState());
    }

    function guardConversationChange() {
      if (canChangeConversation()) {
        return true;
      }
      showError("当前回复尚未结束，请稍后再切换会话。");
      return false;
    }

    function installPixiRenderer() {
      if (!runtimeWindow.PIXI) {
        showError("PIXI 加载失败。");
        return null;
      }

      const pixiApp = new runtimeWindow.PIXI.Application({
        view: canvas,
        resizeTo: runtimeWindow,
        autoStart: true,
        backgroundAlpha: 0,
        antialias: true,
      });
      petSceneController.setPixiApp(pixiApp);
      applyBackgroundVisuals();

      setupStageInteraction();

      pixiApp.ticker.add(() => {
        const currentModel = petSceneController.getCurrentModel();
        const currentApp = petSceneController.getPixiApp();
        if (!currentModel || !currentApp || !state.follow_mouse) {
          return;
        }

        const interaction = currentApp.renderer?.plugins?.interaction;
        const mouse = interaction?.mouse?.global;
        if (!mouse || typeof currentModel.focus !== "function") {
          return;
        }
        currentModel.focus(mouse.x, mouse.y);
      });

      return pixiApp;
    }

    function installQWebChannel() {
      if (runtimeWindow.qt && runtimeWindow.QWebChannel) {
        new runtimeWindow.QWebChannel(runtimeWindow.qt.webChannelTransport, (channel) => {
          setQtBridge(channel.objects.qtBridge);
          logToQt("QWebChannel ready");
        });
      }
    }

    function installEventListeners() {
      navSettingsButtonEl.addEventListener("click", () => {
        openSettingsPage();
      });
      navHistoryButtonEl.addEventListener("click", () => {
        openChat();
        toggleChatHistoryDrawer();
      });
      navChatButtonEl.addEventListener("click", async () => {
        if (!guardConversationChange()) {
          return;
        }
        await createNewTopic(true);
      });
      windowMinimizeEl.addEventListener("click", () => {
        minimizeWindow();
      });
      windowCloseEl.addEventListener("click", () => {
        closeWindow();
      });
      for (const button of chatModeButtons) {
        button.addEventListener("click", () => setChatMode(button.dataset.chatMode));
      }
      for (const button of memoryModeButtons) {
        button.addEventListener("click", async () => {
          if (!guardConversationChange()) {
            return;
          }
          const previousMode = getCurrentMemoryMode();
          setMemoryMode(button.dataset.memoryMode);
          if (getCurrentMemoryMode() !== previousMode) {
            await createNewTopic(false);
          }
        });
      }
      chatCloseEl.addEventListener("click", () => closeChat());
      chatSkillsToggleEl.addEventListener("click", () => toggleChatSkillsDrawer());
      chatHistoryRefreshEl.addEventListener("click", () => loadTopicHistory(true));
      chatSkillsRefreshEl.addEventListener("click", () => loadChatSkills(true));
      chatSkillsResetEl.addEventListener("click", () => resetChatSkillsToDefault());
      updateShellButtons();
      chatSendEl.addEventListener("click", async () => {
        await submitChatInput();
      });
      chatInputEl.addEventListener("keydown", async (event) => {
        if (matchesPushToTalkKey(event) && shouldHandlePushToTalk(event)) {
          event.preventDefault();
          return;
        }
        if (event.key === "Escape" && getPendingRetryEdit()) {
          event.preventDefault();
          clearPendingRetryEdit();
          return;
        }
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          await submitChatInput();
        }
      });
      chatStopEl.addEventListener("click", () => {
        if (asrController.isBusy()) {
          cancelAsrSession({ restoreInput: true, statusMessage: defaultAsrStatusText(), tone: "idle" });
          return;
        }
        stopSpeaking(true);
      });
      runtimeWindow.addEventListener("keydown", async (event) => {
        if (!matchesPushToTalkKey(event) || !shouldHandlePushToTalk(event)) {
          return;
        }
        if (event.repeat) {
          event.preventDefault();
          return;
        }
        event.preventDefault();
        await startPushToTalk();
      });
      runtimeWindow.addEventListener("keyup", (event) => {
        if (!matchesPushToTalkKey(event)) {
          return;
        }
        event.preventDefault();
        stopPushToTalk();
      });
      runtimeWindow.addEventListener("blur", () => {
        if (asrController.isBusy()) {
          cancelAsrSession({ restoreInput: true, statusMessage: defaultAsrStatusText(), tone: "idle" });
        }
      });
      chatPanelController.installChatPanelInteractions();
    }

    async function consumeStartupConfig() {
      if (pendingConfig) {
        const cfg = pendingConfig;
        pendingConfig = null;
        await applyConfig(cfg);
        return;
      }

      const url = petSceneController.consumePendingModelUrl();
      if (url) {
        await loadModel(url);
      }
    }

    async function init() {
      const pixiApp = installPixiRenderer();
      if (!pixiApp) {
        return;
      }

      installQWebChannel();
      installEventListeners();
      await consumeStartupConfig();

      syncPetDisplayName();
      applyChatModeUI();
      applyMemoryModeUI();
      refreshStatus();
    }

    function installPetAppApi() {
      const petAppApi = runtimeWindow.IpetPetAppApi || window.IpetPetAppApi;
      petAppApi.installPetAppApi({
        state,
        window: runtimeWindow,
        petSceneController,
        showError,
        applyConfig,
        loadModel,
        isRendererReady,
        playMotionCompat,
        openChat,
        closeChat,
        streamChat,
        stopSpeaking,
        enqueueTTSChunk,
        setPendingConfig(config) {
          pendingConfig = config;
        },
      });
    }

    function start() {
      installPetAppApi();
      return init().catch((err) => {
        showError(err?.message || String(err));
      });
    }

    return Object.freeze({
      init,
      installPetAppApi,
      start,
    });
  }

  window.IpetAppBootstrap = Object.freeze({
    createAppBootstrapController,
  });
})();
