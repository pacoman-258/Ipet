(() => {
  function createPetAppApi(deps = {}) {
    const state = deps.state || {};
    const petSceneController = deps.petSceneController || {};
    const showError = typeof deps.showError === "function" ? deps.showError : () => {};
    const applyConfig = typeof deps.applyConfig === "function" ? deps.applyConfig : async () => {};
    const loadModel = typeof deps.loadModel === "function" ? deps.loadModel : async () => {};
    const isRendererReady = typeof deps.isRendererReady === "function" ? deps.isRendererReady : () => false;
    const playMotionCompat = typeof deps.playMotionCompat === "function" ? deps.playMotionCompat : () => false;
    const openChat = typeof deps.openChat === "function" ? deps.openChat : () => {};
    const closeChat = typeof deps.closeChat === "function" ? deps.closeChat : () => {};
    const streamChat = typeof deps.streamChat === "function" ? deps.streamChat : async () => {};
    const stopSpeaking = typeof deps.stopSpeaking === "function" ? deps.stopSpeaking : () => {};
    const enqueueTTSChunk = typeof deps.enqueueTTSChunk === "function" ? deps.enqueueTTSChunk : () => {};
    const showContextMenuAt =
      typeof deps.showContextMenuAt === "function" ? deps.showContextMenuAt : () => false;
    const setPendingConfig = typeof deps.setPendingConfig === "function" ? deps.setPendingConfig : () => {};
    const getCurrentModel =
      typeof petSceneController.getCurrentModel === "function" ? () => petSceneController.getCurrentModel() : () => null;

    return {
      async applyConfig(config) {
        if (!isRendererReady()) {
          setPendingConfig(config || {});
          return;
        }
        await applyConfig(config);
      },
      async reloadModel() {
        if (state.model_url) {
          try {
            await loadModel(state.model_url);
          } catch (err) {
            showError(`重新加载模型失败：${err?.message || String(err)}`);
          }
        }
      },
      playMotion(group, index = 0) {
        if (!playMotionCompat(group, index)) {
          showError(`动作播放失败：无法播放 ${String(group)}[${Number(index) || 0}]`);
        }
      },
      playExpression(name) {
        const currentModel = getCurrentModel();
        if (!currentModel || typeof currentModel.expression !== "function") {
          return;
        }
        try {
          currentModel.expression(name);
        } catch (err) {
          showError(`表情切换失败：${String(err)}`);
        }
      },
      getState() {
        return { ...state };
      },
      showContextMenuAt,
      openChat,
      closeChat,
      async sendChat(text) {
        await streamChat(text || "");
      },
      async speakText(text) {
        const content = (text || "").trim();
        if (!content) return;
        stopSpeaking(false);
        enqueueTTSChunk(content, null);
      },
      stopSpeak() {
        stopSpeaking(true);
      },
    };
  }

  function installPetAppApi(deps = {}) {
    const runtimeWindow = deps.window || window;
    runtimeWindow.PET_APP = createPetAppApi(deps);
    return runtimeWindow.PET_APP;
  }

  window.IpetPetAppApi = Object.freeze({
    createPetAppApi,
    installPetAppApi,
  });
})();
