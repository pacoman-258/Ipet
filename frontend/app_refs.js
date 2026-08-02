(() => {
  function byId(runtimeDocument, id) {
    return runtimeDocument.getElementById(id);
  }

  function collectAppRefs(runtimeDocument = document) {
    return {
      backgroundLayerEl: byId(runtimeDocument, "window-background"),
      backgroundImageEl: byId(runtimeDocument, "window-background-image"),
      statusEl: byId(runtimeDocument, "status"),
      errorEl: byId(runtimeDocument, "error"),
      canvas: byId(runtimeDocument, "canvas"),
      navSettingsButtonEl: byId(runtimeDocument, "nav-settings"),
      navHistoryButtonEl: byId(runtimeDocument, "nav-history"),
      navChatButtonEl: byId(runtimeDocument, "nav-chat"),
      windowMinimizeEl: byId(runtimeDocument, "window-minimize"),
      windowCloseEl: byId(runtimeDocument, "window-close"),
      petContextMenuEl: byId(runtimeDocument, "pet-context-menu"),
      petMenuChatEl: byId(runtimeDocument, "pet-menu-chat"),
      petMenuTemporaryChatEl: byId(runtimeDocument, "pet-menu-temporary-chat"),
      petMenuHistoryEl: byId(runtimeDocument, "pet-menu-history"),
      petMenuSettingsEl: byId(runtimeDocument, "pet-menu-settings"),
      petMenuMinimizeEl: byId(runtimeDocument, "pet-menu-minimize"),
      petMenuCloseEl: byId(runtimeDocument, "pet-menu-close"),
      chatPanelEl: byId(runtimeDocument, "chat-panel"),
      chatCloseEl: byId(runtimeDocument, "chat-close"),
      chatHistoryDrawerEl: byId(runtimeDocument, "chat-history-drawer"),
      chatHistoryCurrentEl: byId(runtimeDocument, "chat-history-current"),
      chatHistoryRefreshEl: byId(runtimeDocument, "chat-history-refresh"),
      chatHistoryStatusEl: byId(runtimeDocument, "chat-history-status"),
      chatHistoryListEl: byId(runtimeDocument, "chat-history-list"),
      chatSkillsToggleEl: byId(runtimeDocument, "chat-skills-toggle"),
      chatModeButtons: Array.from(runtimeDocument.querySelectorAll(".chat-mode-btn[data-chat-mode]")),
      memoryModeButtons: Array.from(runtimeDocument.querySelectorAll(".chat-mode-btn[data-memory-mode]")),
      chatSkillsDrawerEl: byId(runtimeDocument, "chat-skills-drawer"),
      chatSkillsRefreshEl: byId(runtimeDocument, "chat-skills-refresh"),
      chatSkillsResetEl: byId(runtimeDocument, "chat-skills-reset"),
      chatSkillsStatusEl: byId(runtimeDocument, "chat-skills-status"),
      chatSkillsListEl: byId(runtimeDocument, "chat-skills-list"),
      chatMessagesEl: byId(runtimeDocument, "chat-messages"),
      chatInputEl: byId(runtimeDocument, "chat-input"),
      chatInputWrapEl: byId(runtimeDocument, "chat-input-wrap"),
      chatTokenCountEl: byId(runtimeDocument, "chat-token-count"),
      chatAsrStatusEl: byId(runtimeDocument, "chat-asr-status"),
      chatSendEl: byId(runtimeDocument, "chat-send"),
      chatStopEl: byId(runtimeDocument, "chat-stop"),
      chatHeaderEl: byId(runtimeDocument, "chat-header"),
      chatTitleEl: byId(runtimeDocument, "chat-title"),
      chatResizerRightEl: byId(runtimeDocument, "chat-resizer-right"),
      chatResizerBottomEl: byId(runtimeDocument, "chat-resizer-bottom"),
      chatResizerCornerEl: byId(runtimeDocument, "chat-resizer-corner"),
    };
  }

  window.IpetAppRefs = Object.freeze({
    collectAppRefs,
  });
})();
