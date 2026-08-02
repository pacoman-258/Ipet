(() => {
  function wireChatInputSection(sectionContext) {
    const { runtimeWindow, refs, controllerRegistry, graphState } = sectionContext;
    const { chatSendEl, chatInputEl, chatTokenCountEl } = refs;
    const chatInputStateController = runtimeWindow.IpetChatInputState.createChatInputStateController({
      refs: {
        chatSendEl,
        chatInputEl,
        chatTokenCountEl,
      },
      getChatState: () => graphState.chatState,
      isAsrBusy: () => graphState.asrController.isBusy(),
    });
    controllerRegistry.chatInputState = chatInputStateController;

    return { chatInputStateController };
  }

  function wireChatSection(sectionContext) {
    const { runtimeWindow, runtimeDocument, helpers, state, refs, facade, controllerRegistry, graphState } =
      sectionContext;
    const {
      chatPanelEl,
      chatCloseEl,
      chatHistoryDrawerEl,
      chatHistoryCurrentEl,
      chatHistoryStatusEl,
      chatHistoryListEl,
      chatSkillsToggleEl,
      chatModeButtons,
      memoryModeButtons,
      chatSkillsDrawerEl,
      chatSkillsStatusEl,
      chatSkillsListEl,
      chatMessagesEl,
      chatInputEl,
      chatInputWrapEl,
      chatAsrStatusEl,
      chatSendEl,
      chatHeaderEl,
      chatResizerRightEl,
      chatResizerBottomEl,
      chatResizerCornerEl,
    } = refs;

    const chatMessagesController = runtimeWindow.IpetChatMessages.createChatMessagesController({
      refs: {
        chatMessagesEl,
        chatInputEl,
        chatInputWrapEl,
        chatSendEl,
      },
      speakerNameForRole: facade.speakerNameForRole,
      phaseLabel: facade.phaseLabel,
      resetChatTimelineState: facade.resetChatTimelineState,
      hideHumanOpsClickPreview: facade.hideHumanOpsClickPreview,
      getChatState: () => graphState.chatState,
      getPendingRetryEdit: () => graphState.pendingRetryEdit,
      setPendingRetryEdit: (value) => {
        graphState.pendingRetryEdit = value || null;
      },
      getActiveApprovalId: () => graphState.activeApprovalId,
      setActiveApprovalId: (value) => {
        graphState.activeApprovalId = String(value || "");
      },
      getPendingApprovalInputTurnId: () => graphState.pendingApprovalInputTurnId,
      setPendingApprovalInputTurnId: (value) => {
        graphState.pendingApprovalInputTurnId = String(value || "");
      },
      getChatMessageSequence: () => graphState.chatMessageSequence,
      setChatMessageSequence: (value) => {
        graphState.chatMessageSequence = Number(value || 0) || 0;
      },
      document: runtimeDocument,
    });
    controllerRegistry.chatMessages = chatMessagesController;
    graphState.asrController = runtimeWindow.IpetAsr.createAsrController({
      helpers,
      state,
      refs: {
        chatPanelEl,
        chatInputEl,
        chatAsrStatusEl,
      },
      getChatState: () => graphState.chatState,
      syncChatInputAvailability: facade.syncChatInputAvailability,
      callQtBridge: facade.callQtBridge,
    });
    controllerRegistry.asr = graphState.asrController;
    const chatPanelController = runtimeWindow.IpetChatPanel.createChatPanelController({
      refs: {
        chatPanelEl,
        chatInputEl,
        chatHistoryDrawerEl,
        chatHeaderEl,
        chatCloseEl,
        chatResizerRightEl,
        chatResizerBottomEl,
        chatResizerCornerEl,
      },
      setChatStateValue: (value) => {
        graphState.chatState = value;
      },
      syncChatInputAvailability: facade.syncChatInputAvailability,
      updateShellButtons: facade.updateShellButtons,
      loadChatSkills: facade.loadChatSkills,
      renderTopicHistoryList: facade.renderTopicHistoryList,
      setAsrStatus: facade.setAsrStatus,
      defaultAsrStatusText: facade.defaultAsrStatusText,
      isAsrBusy: () => graphState.asrController.isBusy(),
      cancelAsrSession: facade.cancelAsrSession,
      toggleChatSkillsDrawer: facade.toggleChatSkillsDrawer,
      getPetBounds: () => controllerRegistry.petScene?.modelBoundsInClientSpace?.() || null,
      window: runtimeWindow,
    });
    controllerRegistry.chatPanel = chatPanelController;
    const chatWorklogController = runtimeWindow.IpetChatWorklog.createChatWorklogController({
      helpers,
      refs: {
        chatMessagesEl,
        chatInputEl,
      },
      speakerNameForRole: facade.speakerNameForRole,
      appendMessage: facade.appendMessage,
      appendToolList: facade.appendToolList,
      setChatState: facade.setChatState,
      continueApproval: facade.continueApproval,
      showHumanOpsClickPreview: facade.showHumanOpsClickPreview,
      hideHumanOpsClickPreview: facade.hideHumanOpsClickPreview,
      getActiveApprovalId: () => graphState.activeApprovalId,
      setActiveApprovalId: (value) => {
        graphState.activeApprovalId = String(value || "");
      },
      getPendingApprovalInputTurnId: () => graphState.pendingApprovalInputTurnId,
      setPendingApprovalInputTurnId: (value) => {
        graphState.pendingApprovalInputTurnId = String(value || "");
      },
      getQtBridge: () => graphState.qtBridge,
      document: runtimeDocument,
    });
    controllerRegistry.chatWorklog = chatWorklogController;
    const chatSidebarsController = runtimeWindow.IpetChatSidebars.createChatSidebarsController({
      helpers,
      state,
      refs: {
        chatHistoryDrawerEl,
        chatHistoryCurrentEl,
        chatHistoryStatusEl,
        chatHistoryListEl,
        chatSkillsDrawerEl,
        chatSkillsStatusEl,
        chatSkillsListEl,
        chatMessagesEl,
      },
      getChatMode: facade.getCurrentChatMode,
      getMemoryMode: facade.getCurrentMemoryMode,
      appendMessage: facade.appendMessage,
      appendAssistantHistoryBlock: facade.appendAssistantHistoryBlock,
      updateShellButtons: facade.updateShellButtons,
      applyChatModeUI: facade.applyChatModeUI,
      openChat: facade.openChat,
      clearPendingRetryEdit: facade.clearPendingRetryEdit,
      resetChatTimelineState: facade.resetChatTimelineState,
      hideHumanOpsClickPreview: facade.hideHumanOpsClickPreview,
    });
    controllerRegistry.chatSidebars = chatSidebarsController;
    const chatModesController = runtimeWindow.IpetChatModes.createChatModesController({
      refs: {
        chatModeButtons,
        memoryModeButtons,
        chatSkillsToggleEl,
        chatSkillsDrawerEl,
      },
      normalizeChatMode: facade.normalizeChatMode,
      normalizeMemoryMode: facade.normalizeMemoryMode,
      activeChatSkillIds: facade.activeChatSkillIds,
      setChatSkillsStatus: facade.setChatSkillsStatus,
      renderTopicHistoryList: facade.renderTopicHistoryList,
      updateShellButtons: facade.updateShellButtons,
      syncTopicIndicator: facade.syncTopicIndicator,
      chatSidebarsController,
    });
    controllerRegistry.chatModes = chatModesController;

    return {
      chatMessagesController,
      chatPanelController,
      chatWorklogController,
      chatSidebarsController,
      chatModesController,
    };
  }

  function createControllerGraphChatSections(sectionContext) {
    return {
      ...wireChatInputSection(sectionContext),
      ...wireChatSection(sectionContext),
    };
  }

  window.IpetControllerGraphChatSections = Object.freeze({
    createControllerGraphChatSections,
  });
})();
