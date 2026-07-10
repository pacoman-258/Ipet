(() => {
  function createChatModesController(deps = {}) {
    const refs = deps.refs || {};
    const chatSidebarsController = deps.chatSidebarsController || {};
    const normalizeChatMode = typeof deps.normalizeChatMode === "function" ? deps.normalizeChatMode : (value) => value;
    const normalizeMemoryMode =
      typeof deps.normalizeMemoryMode === "function" ? deps.normalizeMemoryMode : (value) => value;
    const activeChatSkillIds = typeof deps.activeChatSkillIds === "function" ? deps.activeChatSkillIds : () => [];
    const setChatSkillsStatus =
      typeof deps.setChatSkillsStatus === "function" ? deps.setChatSkillsStatus : () => {};
    const renderTopicHistoryList =
      typeof deps.renderTopicHistoryList === "function" ? deps.renderTopicHistoryList : () => {};
    const updateShellButtons = typeof deps.updateShellButtons === "function" ? deps.updateShellButtons : () => {};
    const syncTopicIndicator =
      typeof deps.syncTopicIndicator === "function" ? deps.syncTopicIndicator : () => {};

    const {
      chatModeButtons = [],
      memoryModeButtons = [],
      chatSkillsToggleEl,
      chatSkillsDrawerEl,
    } = refs;

    let currentChatMode = "react";
    let currentMemoryMode = "persistent";

    function getCurrentChatMode() {
      return currentChatMode;
    }

    function setCurrentChatMode(mode) {
      currentChatMode = normalizeChatMode(mode);
      return currentChatMode;
    }

    function getCurrentMemoryMode() {
      return currentMemoryMode;
    }

    function setCurrentMemoryMode(mode) {
      currentMemoryMode = normalizeMemoryMode(mode);
      return currentMemoryMode;
    }

    function setChatMode(mode) {
      setCurrentChatMode(mode);
      applyChatModeUI();
    }

    function setMemoryMode(mode) {
      setCurrentMemoryMode(mode);
      applyMemoryModeUI();
      renderTopicHistoryList();
      updateShellButtons();
    }

    function applyChatModeUI() {
      for (const button of chatModeButtons) {
        const mode = normalizeChatMode(button.dataset.chatMode);
        const active = mode === currentChatMode;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
      }
      const skillsEnabled = currentChatMode !== "chat";
      if (chatSkillsToggleEl) {
        chatSkillsToggleEl.disabled = !skillsEnabled;
        chatSkillsToggleEl.title = skillsEnabled ? "管理当前会话技能" : "聊天模式不使用技能";
      }
      if (!skillsEnabled) {
        chatSkillsDrawerEl.classList.remove("open");
        setChatSkillsStatus("当前是聊天模式，只允许联网搜索工具，不使用技能。");
        return;
      }
      if (currentChatMode === "skill") {
        setChatSkillsStatus(`技能模式当前启用 ${activeChatSkillIds().length} 个技能。`);
        return;
      }
      if (chatSidebarsController.chatSkillsLoaded()) {
        setChatSkillsStatus(`当前会话已选择 ${activeChatSkillIds().length} 个技能。`);
      }
    }

    function applyMemoryModeUI() {
      for (const button of memoryModeButtons) {
        const mode = normalizeMemoryMode(button.dataset.memoryMode);
        const active = mode === currentMemoryMode;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
      }
      syncTopicIndicator();
    }

    return Object.freeze({
      getCurrentChatMode,
      setCurrentChatMode,
      getCurrentMemoryMode,
      setCurrentMemoryMode,
      setChatMode,
      setMemoryMode,
      applyChatModeUI,
      applyMemoryModeUI,
    });
  }

  window.IpetChatModes = Object.freeze({
    createChatModesController,
  });
})();
