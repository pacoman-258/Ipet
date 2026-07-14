(() => {
  function wireSpeechSection(sectionContext, sectionControllers) {
    const { runtimeWindow, helpers, state, facade, controllerRegistry, graphState } = sectionContext;
    const { petSceneController, chatSidebarsController, chatWorklogController } = sectionControllers;
    const speechController = runtimeWindow.IpetSpeech.createSpeechController({
      helpers,
      state,
      getCurrentModel: () => petSceneController.getCurrentModel(),
      getPixiApp: () => petSceneController.getPixiApp(),
      getAudioContext: () => graphState.speechAudioContext,
      setAudioContext: (nextAudioContext) => {
        graphState.speechAudioContext = nextAudioContext;
      },
      setChatState: facade.setChatState,
      appendMessage: facade.appendMessage,
      showError: facade.showError,
      fetch: typeof runtimeWindow.fetch === "function" ? runtimeWindow.fetch.bind(runtimeWindow) : undefined,
      window: runtimeWindow,
    });
    controllerRegistry.speech = speechController;
    const chatStreamController = runtimeWindow.IpetChatStream.createChatStreamController({
      state,
      normalizeChatMode: facade.normalizeChatMode,
      normalizeMemoryMode: facade.normalizeMemoryMode,
      applyChatModeUI: facade.applyChatModeUI,
      applyMemoryModeUI: facade.applyMemoryModeUI,
      renderTopicHistoryList: facade.renderTopicHistoryList,
      updateShellButtons: facade.updateShellButtons,
      chatSidebarsController,
      speechController,
      beginThoughtSession: facade.beginThoughtSession,
      ensureThoughtGroup: facade.ensureThoughtGroup,
      ensureAssistantWorklogTurn: facade.ensureAssistantWorklogTurn,
      removeEmptyPendingThoughtGroup: facade.removeEmptyPendingThoughtGroup,
      appendWorklogPhase: facade.appendWorklogPhase,
      finishWorklogProcess: facade.finishWorklogProcess,
      appendApprovalBubble: facade.appendApprovalBubble,
      updateWorklogFinalText: facade.updateWorklogFinalText,
      clearActiveThoughtSession: facade.clearActiveThoughtSession,
      feedSpeakBuffer: facade.feedSpeakBuffer,
      topicHistoryEnabled: facade.topicHistoryEnabled,
      loadTopicHistory: facade.loadTopicHistory,
      syncPetDisplayName: facade.syncPetDisplayName,
      getCurrentMemoryMode: facade.getCurrentMemoryMode,
      setCurrentMemoryMode: facade.setCurrentMemoryMode,
      setCurrentChatMode: facade.setCurrentChatMode,
      getReceivedStructuredSegment: () => graphState.receivedStructuredSegment,
      setReceivedStructuredSegment: (value) => {
        graphState.receivedStructuredSegment = !!value;
      },
      setChatTokenUsage: facade.setChatTokenUsage,
    });
    controllerRegistry.chatStream = chatStreamController;
    graphState.chatSubmitController = runtimeWindow.IpetChatSubmit.createChatSubmitController({
      state,
      refs: {
        chatInputEl: sectionContext.refs.chatInputEl,
        chatInputWrapEl: sectionContext.refs.chatInputWrapEl,
        chatSendEl: sectionContext.refs.chatSendEl,
      },
      asrController: graphState.asrController,
      chatWorklogController,
      speechController,
      appendMessage: facade.appendMessage,
      stopSpeaking: facade.stopSpeaking,
      setChatState: facade.setChatState,
      getChatState: () => graphState.chatState,
      consumeChatStream: facade.consumeChatStream,
      beginThoughtSession: facade.beginThoughtSession,
      clearActiveThoughtSession: facade.clearActiveThoughtSession,
      feedSpeakBuffer: facade.feedSpeakBuffer,
      ensureTopicReady: facade.ensureTopicReady,
      loadChatSkills: facade.loadChatSkills,
      activeChatSkillIds: facade.activeChatSkillIds,
      toggleChatSkillsDrawer: facade.toggleChatSkillsDrawer,
      truncateChatAfterMessage: facade.truncateChatAfterMessage,
      clearPendingRetryEdit: facade.clearPendingRetryEdit,
      removeApprovalBubble: facade.removeApprovalBubble,
      canSubmitChatInput: facade.canSubmitChatInput,
      getCurrentChatMode: facade.getCurrentChatMode,
      getCurrentMemoryMode: facade.getCurrentMemoryMode,
      getPendingApprovalInputTurnId: () => graphState.pendingApprovalInputTurnId,
      setPendingApprovalInputTurnId: (value) => {
        graphState.pendingApprovalInputTurnId = String(value || "");
      },
      setActiveApprovalId: (value) => {
        graphState.activeApprovalId = String(value || "");
      },
      getActiveApprovalId: () => graphState.activeApprovalId,
      getQtBridge: () => graphState.qtBridge,
      getPendingRetryEdit: () => graphState.pendingRetryEdit,
      setPendingRetryEdit: (value) => {
        graphState.pendingRetryEdit = value || null;
      },
      setReceivedStructuredSegment: (value) => {
        graphState.receivedStructuredSegment = !!value;
      },
      fetch: typeof runtimeWindow.fetch === "function" ? runtimeWindow.fetch.bind(runtimeWindow) : undefined,
    });
    controllerRegistry.chatSubmit = graphState.chatSubmitController;

    return { speechController, chatStreamController };
  }

  function wireBootstrapSection(sectionContext, sectionControllers) {
    const { runtimeWindow, state, refs, facade, controllerRegistry, graphState } = sectionContext;
    const { asrController } = graphState;
    const { chatSidebarsController, petSceneController, chatPanelController, shellBridgeController } = sectionControllers;
    const {
      canvas,
      navSettingsButtonEl,
      navHistoryButtonEl,
      navChatButtonEl,
      windowMinimizeEl,
      windowCloseEl,
      chatModeButtons,
      memoryModeButtons,
      chatCloseEl,
      chatSkillsToggleEl,
      chatHistoryRefreshEl,
      chatSkillsRefreshEl,
      chatSkillsResetEl,
      chatSendEl,
      chatInputEl,
      chatStopEl,
      chatHistoryDrawerEl,
    } = refs;

    const appConfigController = runtimeWindow.IpetAppConfig.createAppConfigController({
      state,
      refs: {
        chatHistoryDrawerEl,
      },
      asrController,
      chatSidebarsController,
      petSceneController,
      syncPetDisplayName: facade.syncPetDisplayName,
      normalizeAsrConfig: facade.normalizeAsrConfig,
      topicHistoryEnabled: facade.topicHistoryEnabled,
      renderTopicHistoryList: facade.renderTopicHistoryList,
      applyChatModeUI: facade.applyChatModeUI,
      applyMemoryModeUI: facade.applyMemoryModeUI,
      updateShellButtons: facade.updateShellButtons,
      cancelAsrSession: facade.cancelAsrSession,
      defaultAsrStatusText: facade.defaultAsrStatusText,
      setAsrStatus: facade.setAsrStatus,
      syncChatInputAvailability: facade.syncChatInputAvailability,
      backgroundOverlayValue: facade.backgroundOverlayValue,
      applyBackgroundVisuals: facade.applyBackgroundVisuals,
      loadModel: facade.loadModel,
      showError: facade.showError,
      applyModelTransform: facade.applyModelTransform,
      refreshStatus: facade.refreshStatus,
    });
    controllerRegistry.appConfig = appConfigController;
    const appBootstrapController = runtimeWindow.IpetAppBootstrap.createAppBootstrapController({
      state,
      refs: {
        canvas,
        navSettingsButtonEl,
        navHistoryButtonEl,
        navChatButtonEl,
        windowMinimizeEl,
        windowCloseEl,
        chatModeButtons,
        memoryModeButtons,
        chatCloseEl,
        chatSkillsToggleEl,
        chatHistoryRefreshEl,
        chatSkillsRefreshEl,
        chatSkillsResetEl,
        chatSendEl,
        chatInputEl,
        chatStopEl,
      },
      petSceneController,
      asrController,
      chatPanelController,
      setQtBridge: (value) => {
        graphState.qtBridge = value || null;
      },
      onQtBridgeReady: () => {
        shellBridgeController.bindApprovalNotificationDecisions((rawPayload) => {
          try {
            const payload = JSON.parse(String(rawPayload || "{}"));
            const proposalId = String(payload.proposal_id || "").trim();
            if (proposalId && proposalId === graphState.activeApprovalId && graphState.chatState === "awaiting_approval") {
              facade.continueApproval(proposalId, payload.approved === true);
            }
          } catch (_) {}
        });
      },
      getPendingRetryEdit: () => graphState.pendingRetryEdit,
      showError: facade.showError,
      logToQt: facade.logToQt,
      openSettingsPage: facade.openSettingsPage,
      minimizeWindow: facade.minimizeWindow,
      closeWindow: facade.closeWindow,
      applyBackgroundVisuals: facade.applyBackgroundVisuals,
      setupStageInteraction: facade.setupStageInteraction,
      openChat: facade.openChat,
      closeChat: facade.closeChat,
      toggleChatHistoryDrawer: facade.toggleChatHistoryDrawer,
      toggleChatSkillsDrawer: facade.toggleChatSkillsDrawer,
      getCurrentMemoryMode: facade.getCurrentMemoryMode,
      getChatState: () => graphState.chatState,
      updateShellButtons: facade.updateShellButtons,
      createNewTopic: facade.createNewTopic,
      setChatMode: facade.setChatMode,
      setMemoryMode: facade.setMemoryMode,
      loadTopicHistory: facade.loadTopicHistory,
      loadChatSkills: facade.loadChatSkills,
      resetChatSkillsToDefault: facade.resetChatSkillsToDefault,
      submitChatInput: facade.submitChatInput,
      stopTask: facade.stopTask,
      matchesPushToTalkKey: facade.matchesPushToTalkKey,
      shouldHandlePushToTalk: facade.shouldHandlePushToTalk,
      clearPendingRetryEdit: facade.clearPendingRetryEdit,
      defaultAsrStatusText: facade.defaultAsrStatusText,
      cancelAsrSession: facade.cancelAsrSession,
      stopSpeaking: facade.stopSpeaking,
      startPushToTalk: facade.startPushToTalk,
      stopPushToTalk: facade.stopPushToTalk,
      applyConfig: facade.applyConfig,
      loadModel: facade.loadModel,
      isRendererReady: facade.isRendererReady,
      playMotionCompat: facade.playMotionCompat,
      streamChat: facade.streamChat,
      enqueueTTSChunk: facade.enqueueTTSChunk,
      syncPetDisplayName: facade.syncPetDisplayName,
      applyChatModeUI: facade.applyChatModeUI,
      applyMemoryModeUI: facade.applyMemoryModeUI,
      refreshStatus: facade.refreshStatus,
      window: runtimeWindow,
    });
    controllerRegistry.appBootstrap = appBootstrapController;

    return { appConfigController, appBootstrapController };
  }

  function createControllerGraphAppSections(sectionContext, sectionControllers) {
    const speechControllers = wireSpeechSection(sectionContext, sectionControllers);
    const bootstrapControllers = wireBootstrapSection(sectionContext, sectionControllers);

    return {
      ...speechControllers,
      ...bootstrapControllers,
    };
  }

  window.IpetControllerGraphAppSections = Object.freeze({
    createControllerGraphAppSections,
  });
})();
