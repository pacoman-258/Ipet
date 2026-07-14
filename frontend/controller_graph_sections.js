(() => {
  function createPlaceholderChatSubmitController() {
    return {
      continueApproval: async () => {
        throw new Error("chat submit controller is not initialized");
      },
      streamChat: async () => {
        throw new Error("chat submit controller is not initialized");
      },
      submitChatInput: async () => {
        throw new Error("chat submit controller is not initialized");
      },
      stopTask: async () => {},
    };
  }

  function createGraphRuntimeState() {
    return {
      qtBridge: null,
      chatState: "idle",
      speechAudioContext: null,
      receivedStructuredSegment: false,
      activeApprovalId: "",
      pendingApprovalInputTurnId: "",
      pendingRetryEdit: null,
      chatMessageSequence: 0,
      asrController: {
        isBusy: () => false,
      },
      chatSubmitController: createPlaceholderChatSubmitController(),
    };
  }

  function installPlaceholderControllers(sectionContext) {
    const { controllerRegistry, graphState } = sectionContext;
    controllerRegistry.asr = graphState.asrController;
    controllerRegistry.chatSubmit = graphState.chatSubmitController;
  }

  function wireShellSection(sectionContext) {
    const { runtimeWindow, runtimeDocument, helpers, state, refs, facade, controllerRegistry, graphState } =
      sectionContext;
    const {
      backgroundLayerEl,
      backgroundImageEl,
      statusEl,
      errorEl,
      chatPanelEl,
      chatHistoryDrawerEl,
      navChatButtonEl,
      navHistoryButtonEl,
    } = refs;
    const shellVisualsController = runtimeWindow.IpetShellVisuals.createShellVisualsController({
      helpers,
      state,
      refs: {
        backgroundLayerEl,
        backgroundImageEl,
        statusEl,
        errorEl,
        chatPanelEl,
        chatHistoryDrawerEl,
        navChatButtonEl,
        navHistoryButtonEl,
      },
      getQtBridge: () => graphState.qtBridge,
      topicHistoryEnabled: facade.topicHistoryEnabled,
      document: runtimeDocument,
    });
    controllerRegistry.shellVisuals = shellVisualsController;
    const shellBridgeController = runtimeWindow.IpetShellBridge.createShellBridgeController({
      state,
      getQtBridge: () => graphState.qtBridge,
      window: runtimeWindow,
    });
    controllerRegistry.shellBridge = shellBridgeController;
    const humanOpsPreviewController = runtimeWindow.IpetHumanOpsPreview.createHumanOpsPreviewController({
      getQtBridge: () => graphState.qtBridge,
      window: runtimeWindow,
      document: runtimeDocument,
    });
    controllerRegistry.humanOpsPreview = humanOpsPreviewController;

    return { shellVisualsController, shellBridgeController, humanOpsPreviewController };
  }

  function wirePetSection(sectionContext) {
    const { runtimeWindow, helpers, state, refs, facade, controllerRegistry, graphState } = sectionContext;
    const { chatTitleEl } = refs;
    const petStateNotifyController = runtimeWindow.IpetPetStateNotify.createPetStateNotifyController({
      helpers,
      state,
      refs: {
        chatTitleEl,
      },
      getQtBridge: () => graphState.qtBridge,
    });
    controllerRegistry.petStateNotify = petStateNotifyController;
    const petSceneController = runtimeWindow.IpetPetScene.createPetSceneController({
      helpers,
      state,
      refreshStatus: facade.refreshStatus,
      scheduleNotifyState: facade.scheduleNotifyState,
      notifyStateNow: facade.notifyStateNow,
      clearError: facade.clearError,
      window: runtimeWindow,
    });
    controllerRegistry.petScene = petSceneController;

    return { petStateNotifyController, petSceneController };
  }

  function createControllerGraphSections(context) {
    const graphState = createGraphRuntimeState();
    const sectionContext = {
      ...context,
      controllerRegistry: context.controllers,
      graphState,
    };
    const { runtimeWindow } = sectionContext;

    installPlaceholderControllers(sectionContext);
    const shellControllers = wireShellSection(sectionContext);
    const petControllers = wirePetSection(sectionContext);
    const chatControllers =
      runtimeWindow.IpetControllerGraphChatSections.createControllerGraphChatSections(sectionContext);
    const appControllers = runtimeWindow.IpetControllerGraphAppSections.createControllerGraphAppSections(
      sectionContext,
      {
        ...shellControllers,
        ...petControllers,
        ...chatControllers,
      },
    );

    return {
      ...shellControllers,
      ...petControllers,
      ...chatControllers,
      ...appControllers,
    };
  }

  window.IpetControllerGraphSections = Object.freeze({
    createControllerGraphSections,
  });
})();
