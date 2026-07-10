(() => {
  const controllerMethodMap = Object.freeze({
    "showError": ["shellVisuals", "showError"],
    "clearError": ["shellVisuals", "clearError"],
    "logToQt": ["shellVisuals", "logToQt"],
    "clamp": ["shellVisuals", "clamp"],
    "toRateString": ["shellVisuals", "toRateString"],
    "roundInt": ["shellVisuals", "roundInt"],
    "backgroundOverlayValue": ["shellVisuals", "backgroundOverlayValue"],
    "applyBackgroundVisuals": ["shellVisuals", "applyBackgroundVisuals"],
    "refreshStatus": ["shellVisuals", "refreshStatus"],
    "updateShellButtons": ["shellVisuals", "updateShellButtons"],
    "callQtBridge": ["shellBridge", "callQtBridge"],
    "openSettingsPage": ["shellBridge", "openSettingsPage"],
    "minimizeWindow": ["shellBridge", "minimizeWindow"],
    "closeWindow": ["shellBridge", "closeWindow"],
    "loadChatPanelState": ["chatPanel", "loadChatPanelState"],
    "saveChatPanelState": ["chatPanel", "saveChatPanelState"],
    "syncPetDisplayName": ["petStateNotify", "syncPetDisplayName"],
    "speakerNameForRole": ["petStateNotify", "speakerNameForRole"],
    "activeChatSkillIds": ["chatSidebars", "activeChatSkillIds"],
    "getCurrentChatMode": ["chatModes", "getCurrentChatMode"],
    "setCurrentChatMode": ["chatModes", "setCurrentChatMode"],
    "getCurrentMemoryMode": ["chatModes", "getCurrentMemoryMode"],
    "setCurrentMemoryMode": ["chatModes", "setCurrentMemoryMode"],
    "setChatMode": ["chatModes", "setChatMode"],
    "setMemoryMode": ["chatModes", "setMemoryMode"],
    "applyChatModeUI": ["chatModes", "applyChatModeUI"],
    "applyMemoryModeUI": ["chatModes", "applyMemoryModeUI"],
    "setChatSkillsStatus": ["chatSidebars", "setChatSkillsStatus"],
    "normalizeAsrConfig": ["asr", "normalizeAsrConfig"],
    "asrConfig": ["asr", "asrConfig"],
    "isProbablyMacOS": ["asr", "isProbablyMacOS"],
    "defaultAsrStatusText": ["asr", "defaultAsrStatusText"],
    "activeAsrStatusText": ["asr", "activeAsrStatusText"],
    "setAsrStatus": ["asr", "setAsrStatus"],
    "syncChatInputAvailability": ["chatInputState", "syncChatInputAvailability"],
    "canSubmitChatInput": ["chatInputState", "canSubmitChatInput"],
    "matchesPushToTalkKey": ["asr", "matchesPushToTalkKey"],
    "canStartPushToTalk": ["asr", "canStartPushToTalk"],
    "shouldHandlePushToTalk": ["asr", "shouldHandlePushToTalk"],
    "candidateAsrBaseUrls": ["asr", "candidateAsrBaseUrls"],
    "buildAsrWebSocketUrl": ["asr", "buildAsrWebSocketUrl"],
    "buildAsrHealthUrl": ["asr", "buildAsrHealthUrl"],
    "buildAsrWarmupUrl": ["asr", "buildAsrWarmupUrl"],
    "probeAsrAvailability": ["asr", "probeAsrAvailability"],
    "requestAsrWarmup": ["asr", "requestAsrWarmup"],
    "shouldRetryAsrAvailability": ["asr", "shouldRetryAsrAvailability"],
    "cancelAsrSession": ["asr", "cancelAsrSession"],
    "downsampleFloat32ToInt16Buffer": ["asr", "downsampleFloat32ToInt16Buffer"],
    "startPushToTalk": ["asr", "startPushToTalk"],
    "stopPushToTalk": ["asr", "stopPushToTalk"],
    "topicHistoryEnabled": ["chatSidebars", "topicHistoryEnabled"],
    "setChatHistoryStatus": ["chatSidebars", "setChatHistoryStatus"],
    "normalizeTopicRecord": ["chatSidebars", "normalizeTopicRecord"],
    "currentTopicLabel": ["chatSidebars", "currentTopicLabel"],
    "syncTopicIndicator": ["chatSidebars", "syncTopicIndicator"],
    "replaceChatMessages": ["chatSidebars", "replaceChatMessages"],
    "clearPendingDeleteTopic": ["chatSidebars", "clearPendingDeleteTopic"],
    "armPendingDeleteTopic": ["chatSidebars", "armPendingDeleteTopic"],
    "renderTopicHistoryList": ["chatSidebars", "renderTopicHistoryList"],
    "loadTopicHistory": ["chatSidebars", "loadTopicHistory"],
    "createNewTopic": ["chatSidebars", "createNewTopic"],
    "loadTopicDetail": ["chatSidebars", "loadTopicDetail"],
    "selectTopic": ["chatSidebars", "selectTopic"],
    "deleteTopic": ["chatSidebars", "deleteTopic"],
    "ensureTopicReady": ["chatSidebars", "ensureTopicReady"],
    "toggleChatHistoryDrawer": ["chatSidebars", "toggleChatHistoryDrawer"],
    "normalizeSkillRecord": ["chatSidebars", "normalizeSkillRecord"],
    "renderChatSkillDrawer": ["chatSidebars", "renderChatSkillDrawer"],
    "loadChatSkills": ["chatSidebars", "loadChatSkills"],
    "toggleChatSkillsDrawer": ["chatSidebars", "toggleChatSkillsDrawer"],
    "resetChatSkillsToDefault": ["chatSidebars", "resetChatSkillsToDefault"],
    "phaseLabel": ["chatWorklog", "phaseLabel"],
    "removeApprovalBubble": ["chatWorklog", "removeApprovalBubble"],
    "hideHumanOpsClickPreview": ["humanOpsPreview", "hideHumanOpsClickPreview"],
    "resetChatTimelineState": ["chatWorklog", "resetChatTimelineState"],
    "showHumanOpsClickPreview": ["humanOpsPreview", "showHumanOpsClickPreview"],
    "createThoughtSessionId": ["chatWorklog", "createThoughtSessionId"],
    "beginThoughtSession": ["chatWorklog", "beginThoughtSession"],
    "clearActiveThoughtSession": ["chatWorklog", "clearActiveThoughtSession"],
    "ensureThoughtGroup": ["chatWorklog", "ensureThoughtGroup"],
    "removeEmptyPendingThoughtGroup": ["chatWorklog", "removeEmptyPendingThoughtGroup"],
    "appendThoughtPhase": ["chatWorklog", "appendThoughtPhase"],
    "ensureAssistantWorklogTurn": ["chatWorklog", "ensureAssistantWorklogTurn"],
    "appendWorklogPhase": ["chatWorklog", "appendWorklogPhase"],
    "updateWorklogFinalText": ["chatWorklog", "updateWorklogFinalText"],
    "appendAssistantHistoryBlock": ["chatWorklog", "appendAssistantHistoryBlock"],
    "findUserMessageById": ["chatMessages", "findUserMessageById"],
    "clearPendingRetryEdit": ["chatMessages", "clearPendingRetryEdit"],
    "beginUserMessageEditRetry": ["chatMessages", "beginUserMessageEditRetry"],
    "truncateChatAfterMessage": ["chatMessages", "truncateChatAfterMessage"],
    "appendMessage": ["chatMessages", "appendMessage"],
    "updateMessageText": ["chatMessages", "updateMessageText"],
    "appendToolList": ["chatMessages", "appendToolList"],
    "setChatState": ["chatPanel", "setChatState"],
    "stopSpeaking": ["speech", "stopSpeaking"],
    "openChat": ["chatPanel", "openChat"],
    "closeChat": ["chatPanel", "closeChat"],
    "scheduleNotifyState": ["petStateNotify", "scheduleNotifyState"],
    "notifyStateNow": ["petStateNotify", "notifyStateNow"],
    "setMouthOpen": ["speech", "setMouthOpen"],
    "resolveExpressionName": ["speech", "resolveExpressionName"],
    "triggerExpressionSafe": ["speech", "triggerExpressionSafe"],
    "stopLipSyncLoop": ["speech", "stopLipSyncLoop"],
    "startLipSync": ["speech", "startLipSync"],
    "fallbackSpeakByBrowser": ["speech", "fallbackSpeakByBrowser"],
    "enqueueTTSChunk": ["speech", "enqueueTTSChunk"],
    "feedSpeakBuffer": ["speech", "feedSpeakBuffer"],
    "playNextTTSChunk": ["speech", "playNextTTSChunk"],
    "appendApprovalBubble": ["chatWorklog", "appendApprovalBubble"],
    "consumeChatStream": ["chatStream", "consumeChatStream"],
    "continueApproval": ["chatSubmit", "continueApproval"],
    "streamChat": ["chatSubmit", "streamChat"],
    "submitChatInput": ["chatSubmit", "submitChatInput"],
    "applyModelTransform": ["petScene", "applyModelTransform"],
    "bindModelInteraction": ["petScene", "bindModelInteraction"],
    "setupStageInteraction": ["petScene", "setupStageInteraction"],
    "isRendererReady": ["petScene", "isRendererReady"],
    "playMotionCompat": ["petScene", "playMotionCompat"],
    "loadModel": ["petScene", "loadModel"],
    "applyConfig": ["appConfig", "applyConfig"],
    "init": ["appBootstrap", "start"],
  });

  const helperMethodNames = Object.freeze([
    "normalizeSkillId",
    "normalizeChatMode",
    "normalizeMemoryMode",
    "dedupeSkillIds",
  ]);

  function availableControllerNames(controllers) {
    return Object.keys(controllers || {})
      .filter((name) => !!controllers[name])
      .sort()
      .join(", ");
  }

  function requireControllerMethod(controllers, controllerName, methodName) {
    const controller = controllers ? controllers[controllerName] : null;
    if (!controller) {
      const available = availableControllerNames(controllers) || "none";
      throw new Error(
        `IpetControllerFacade: ${controllerName} controller is not initialized for ${methodName}(); ` +
          `available controllers: ${available}`,
      );
    }
    const method = controller[methodName];
    if (typeof method !== "function") {
      throw new Error(`IpetControllerFacade: ${controllerName}.${methodName} is not a function`);
    }
    return method.bind(controller);
  }

  function requireHelperMethod(helpers, methodName) {
    const method = helpers ? helpers[methodName] : null;
    if (typeof method !== "function") {
      throw new Error(`IpetControllerFacade: helpers.${methodName} is not a function`);
    }
    return method.bind(helpers);
  }

  function createControllerFacade(deps = {}) {
    const helpers = deps.helpers || {};
    const controllers = deps.controllers || {};
    const facade = {};

    for (const [publicName, target] of Object.entries(controllerMethodMap)) {
      const [controllerName, methodName] = target;
      facade[publicName] = (...args) => requireControllerMethod(controllers, controllerName, methodName)(...args);
    }
    for (const methodName of helperMethodNames) {
      facade[methodName] = (...args) => requireHelperMethod(helpers, methodName)(...args);
    }

    return Object.freeze(facade);
  }

  window.IpetControllerFacade = Object.freeze({
    createControllerFacade,
  });
})();
