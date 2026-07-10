(() => {
  function createChatStreamController(deps = {}) {
    const state = deps.state || {};
    const chatSidebarsController = deps.chatSidebarsController || {};
    const speechController = deps.speechController || {};

    const normalizeChatMode = typeof deps.normalizeChatMode === "function" ? deps.normalizeChatMode : (value) => value;
    const normalizeMemoryMode =
      typeof deps.normalizeMemoryMode === "function" ? deps.normalizeMemoryMode : (value) => value;
    const applyChatModeUI = typeof deps.applyChatModeUI === "function" ? deps.applyChatModeUI : () => {};
    const applyMemoryModeUI = typeof deps.applyMemoryModeUI === "function" ? deps.applyMemoryModeUI : () => {};
    const renderTopicHistoryList =
      typeof deps.renderTopicHistoryList === "function" ? deps.renderTopicHistoryList : () => {};
    const updateShellButtons = typeof deps.updateShellButtons === "function" ? deps.updateShellButtons : () => {};
    const beginThoughtSession =
      typeof deps.beginThoughtSession === "function" ? deps.beginThoughtSession : (sessionId = "") => sessionId;
    const ensureThoughtGroup = typeof deps.ensureThoughtGroup === "function" ? deps.ensureThoughtGroup : () => {};
    const ensureAssistantWorklogTurn =
      typeof deps.ensureAssistantWorklogTurn === "function" ? deps.ensureAssistantWorklogTurn : () => null;
    const removeEmptyPendingThoughtGroup =
      typeof deps.removeEmptyPendingThoughtGroup === "function" ? deps.removeEmptyPendingThoughtGroup : () => {};
    const appendWorklogPhase =
      typeof deps.appendWorklogPhase === "function" ? deps.appendWorklogPhase : () => {};
    const appendApprovalBubble =
      typeof deps.appendApprovalBubble === "function" ? deps.appendApprovalBubble : () => {};
    const updateWorklogFinalText =
      typeof deps.updateWorklogFinalText === "function" ? deps.updateWorklogFinalText : () => {};
    const clearActiveThoughtSession =
      typeof deps.clearActiveThoughtSession === "function" ? deps.clearActiveThoughtSession : () => {};
    const feedSpeakBuffer = typeof deps.feedSpeakBuffer === "function" ? deps.feedSpeakBuffer : () => {};
    const topicHistoryEnabled = typeof deps.topicHistoryEnabled === "function" ? deps.topicHistoryEnabled : () => false;
    const loadTopicHistory = typeof deps.loadTopicHistory === "function" ? deps.loadTopicHistory : async () => [];
    const syncPetDisplayName = typeof deps.syncPetDisplayName === "function" ? deps.syncPetDisplayName : () => {};
    const getCurrentMemoryMode =
      typeof deps.getCurrentMemoryMode === "function" ? deps.getCurrentMemoryMode : () => "persistent";
    const setCurrentMemoryMode =
      typeof deps.setCurrentMemoryMode === "function" ? deps.setCurrentMemoryMode : () => {};
    const setCurrentChatMode = typeof deps.setCurrentChatMode === "function" ? deps.setCurrentChatMode : () => {};
    const getReceivedStructuredSegment =
      typeof deps.getReceivedStructuredSegment === "function" ? deps.getReceivedStructuredSegment : () => false;
    const setReceivedStructuredSegment =
      typeof deps.setReceivedStructuredSegment === "function" ? deps.setReceivedStructuredSegment : () => {};

    function parseServerSentEvent(raw) {
      const lines = String(raw || "").split("\n");
      let eventName = "";
      let dataText = "";
      for (const line of lines) {
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataText += line.slice(5).trim();
        }
      }
      if (!eventName || !dataText) {
        return null;
      }
      return { eventName, payload: JSON.parse(dataText) };
    }

    async function dispatchChatStreamEvent(context, eventName, payload) {
      if (eventName === "meta") {
        if (payload.pet_display_name) {
          state.chat.pet_display_name = String(payload.pet_display_name);
          syncPetDisplayName();
        }
        if (payload.chat_mode) {
          setCurrentChatMode(normalizeChatMode(payload.chat_mode));
          applyChatModeUI();
        }
        if (payload.memory_mode) {
          setCurrentMemoryMode(normalizeMemoryMode(payload.memory_mode));
          applyMemoryModeUI();
        }
        if (payload.topic_id && getCurrentMemoryMode() !== "temporary") {
          chatSidebarsController.syncTopicFromPayload(payload.topic_id);
        }
        renderTopicHistoryList();
        updateShellButtons();
        return;
      }

      if (eventName === "phase") {
        removeEmptyPendingThoughtGroup(context.thoughtSessionId);
        appendWorklogPhase(payload, context.thoughtSessionId);
        return;
      }

      if (eventName === "approval_required") {
        context.awaitingApproval = true;
        removeEmptyPendingThoughtGroup(context.thoughtSessionId);
        appendApprovalBubble(payload, context.thoughtSessionId);
        return;
      }

      if (eventName === "segment") {
        removeEmptyPendingThoughtGroup(context.thoughtSessionId);
        setReceivedStructuredSegment(true);
        const segText = String(payload.text || "");
        const segExpr = payload.expr == null ? null : String(payload.expr || "");
        if (segText) {
          feedSpeakBuffer(segText, false, segExpr);
        }
        return;
      }

      if (eventName === "display_segment") {
        removeEmptyPendingThoughtGroup(context.thoughtSessionId);
        context.receivedDisplaySegment = true;
        context.full += String(payload.text || "");
        updateWorklogFinalText(
          ensureAssistantWorklogTurn(context.thoughtSessionId),
          context.full || "少女思考中...",
        );
        return;
      }

      if (eventName === "token") {
        if (getReceivedStructuredSegment()) {
          return;
        }
        removeEmptyPendingThoughtGroup(context.thoughtSessionId);
        context.full += payload.delta || "";
        updateWorklogFinalText(
          ensureAssistantWorklogTurn(context.thoughtSessionId),
          context.full || "少女思考中...",
        );
        feedSpeakBuffer(payload.delta || "");
        return;
      }

      if (eventName === "done") {
        context.full = payload.text || context.full;
        if (payload.memory_mode) {
          setCurrentMemoryMode(normalizeMemoryMode(payload.memory_mode));
          applyMemoryModeUI();
        }
        if (payload.topic_id && getCurrentMemoryMode() !== "temporary") {
          chatSidebarsController.handleDoneTopic(payload);
        }
        removeEmptyPendingThoughtGroup(context.thoughtSessionId);
        if (getReceivedStructuredSegment() && !context.receivedDisplaySegment && !context.full) {
          context.full = "(\u8bed\u97f3\u8f93\u51fa\u5df2\u5b8c\u6210)";
        }
        if (
          !getReceivedStructuredSegment() &&
          !speechController.hasPendingSpeakBuffer() &&
          context.full.trim()
        ) {
          speechController.setPendingSpeakBuffer(context.full);
        }
        updateWorklogFinalText(context.ensureFinalMessage(), context.full || "(\u7a7a\u54cd\u5e94)");
        if (getCurrentMemoryMode() !== "temporary" && topicHistoryEnabled()) {
          await loadTopicHistory(true);
        } else {
          renderTopicHistoryList();
        }
        clearActiveThoughtSession(context.thoughtSessionId);
        return;
      }

      if (eventName === "error") {
        clearActiveThoughtSession(context.thoughtSessionId);
        throw new Error(payload.message || "聊天流返回错误");
      }
    }

    async function consumeChatStream(resp, options = {}) {
      if (!resp.ok) {
        let detail = `chat http ${resp.status}`;
        try {
          const payload = await resp.json();
          detail = String(payload?.detail || payload?.message || detail);
        } catch (_) {
          try {
            const text = await resp.text();
            if (text) {
              detail = text;
            }
          } catch (_) {}
        }
        throw new Error(detail);
      }
      if (!resp.body) {
        throw new Error("聊天响应缺少内容");
      }

      const decoder = new TextDecoder();
      const reader = resp.body.getReader();
      let buffer = "";
      let assistantTurn = null;
      const thoughtSessionId = beginThoughtSession(String(options?.thoughtSessionId || "").trim());
      ensureThoughtGroup(1, true, thoughtSessionId);
      assistantTurn = ensureAssistantWorklogTurn(thoughtSessionId);
      setReceivedStructuredSegment(false);

      const context = {
        full: "",
        awaitingApproval: false,
        receivedDisplaySegment: false,
        thoughtSessionId,
        ensureFinalMessage() {
          assistantTurn = ensureAssistantWorklogTurn(thoughtSessionId);
          return assistantTurn;
        },
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";

        for (const raw of chunks) {
          const parsed = parseServerSentEvent(raw);
          if (!parsed) {
            continue;
          }
          await dispatchChatStreamEvent(context, parsed.eventName, parsed.payload);
        }
      }

      if (!context.awaitingApproval) {
        removeEmptyPendingThoughtGroup(thoughtSessionId);
      }
      return { full: context.full, awaitingApproval: context.awaitingApproval, thoughtSessionId };
    }

    return {
      parseServerSentEvent,
      dispatchChatStreamEvent,
      consumeChatStream,
    };
  }

  window.IpetChatStream = {
    createChatStreamController,
  };
})();
