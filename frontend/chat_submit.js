(() => {
  function createChatSubmitController(deps = {}) {
    const state = deps.state || {};
    const refs = deps.refs || {};
    const chatInputEl = refs.chatInputEl || null;
    const chatWorklogController = deps.chatWorklogController || {};
    const speechController = deps.speechController || {};
    const asrController = deps.asrController || {};
    const fetch =
      typeof deps.fetch === "function"
        ? deps.fetch
        : typeof window.fetch === "function"
          ? window.fetch.bind(window)
          : async () => {
              throw new Error("fetch is unavailable");
            };

    const appendMessage = typeof deps.appendMessage === "function" ? deps.appendMessage : () => {};
    const stopSpeaking = typeof deps.stopSpeaking === "function" ? deps.stopSpeaking : () => {};
    const setChatState = typeof deps.setChatState === "function" ? deps.setChatState : () => {};
    const getChatState = typeof deps.getChatState === "function" ? deps.getChatState : () => "idle";
    const consumeChatStream =
      typeof deps.consumeChatStream === "function" ? deps.consumeChatStream : async () => ({ awaitingApproval: false });
    const beginThoughtSession =
      typeof deps.beginThoughtSession === "function" ? deps.beginThoughtSession : (sessionId = "") => sessionId;
    const clearActiveThoughtSession =
      typeof deps.clearActiveThoughtSession === "function" ? deps.clearActiveThoughtSession : () => {};
    const feedSpeakBuffer = typeof deps.feedSpeakBuffer === "function" ? deps.feedSpeakBuffer : () => {};
    const ensureTopicReady = typeof deps.ensureTopicReady === "function" ? deps.ensureTopicReady : async () => {};
    const loadChatSkills = typeof deps.loadChatSkills === "function" ? deps.loadChatSkills : async () => {};
    const activeChatSkillIds = typeof deps.activeChatSkillIds === "function" ? deps.activeChatSkillIds : () => [];
    const toggleChatSkillsDrawer =
      typeof deps.toggleChatSkillsDrawer === "function" ? deps.toggleChatSkillsDrawer : () => {};
    const truncateChatAfterMessage =
      typeof deps.truncateChatAfterMessage === "function"
        ? deps.truncateChatAfterMessage
        : () => ({ assistantTurn: null });
    const setPendingRetryEdit =
      typeof deps.setPendingRetryEdit === "function" ? deps.setPendingRetryEdit : () => {};
    const clearPendingRetryEdit =
      typeof deps.clearPendingRetryEdit === "function"
        ? deps.clearPendingRetryEdit
        : () => setPendingRetryEdit(null);
    const removeApprovalBubble =
      typeof deps.removeApprovalBubble === "function" ? deps.removeApprovalBubble : () => {};
    const canSubmitChatInput = typeof deps.canSubmitChatInput === "function" ? deps.canSubmitChatInput : () => true;
    const getCurrentChatMode =
      typeof deps.getCurrentChatMode === "function" ? deps.getCurrentChatMode : () => "react";
    const getCurrentMemoryMode =
      typeof deps.getCurrentMemoryMode === "function" ? deps.getCurrentMemoryMode : () => "persistent";
    const getPendingApprovalInputTurnId =
      typeof deps.getPendingApprovalInputTurnId === "function"
        ? deps.getPendingApprovalInputTurnId
        : () => "";
    const setPendingApprovalInputTurnId =
      typeof deps.setPendingApprovalInputTurnId === "function"
        ? deps.setPendingApprovalInputTurnId
        : () => {};
    const setActiveApprovalId =
      typeof deps.setActiveApprovalId === "function" ? deps.setActiveApprovalId : () => {};
    const getActiveApprovalId =
      typeof deps.getActiveApprovalId === "function" ? deps.getActiveApprovalId : () => "";
    const getQtBridge = typeof deps.getQtBridge === "function" ? deps.getQtBridge : () => null;
    const getPendingRetryEdit =
      typeof deps.getPendingRetryEdit === "function" ? deps.getPendingRetryEdit : () => null;
    const setReceivedStructuredSegment =
      typeof deps.setReceivedStructuredSegment === "function" ? deps.setReceivedStructuredSegment : () => {};
    const beginSpeechStream =
      typeof speechController.beginStream === "function" ? () => speechController.beginStream() : () => {};
    const markSpeechStreamFinished =
      typeof speechController.markStreamFinished === "function"
        ? () => speechController.markStreamFinished()
        : () => {};
    const isSpeechPlaying =
      typeof speechController.isPlaying === "function" ? () => speechController.isPlaying() : () => false;
    const speechQueueLength =
      typeof speechController.queueLength === "function" ? () => speechController.queueLength() : () => 0;
    const isAsrBusy = typeof asrController.isBusy === "function" ? () => asrController.isBusy() : () => false;
    let activeTaskId = "";
    let activeAbortController = null;

    function newTaskId() {
      return globalThis.crypto?.randomUUID?.() || `task-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    async function stopTask() {
      if (!activeTaskId || !["streaming", "awaiting_approval", "stopping"].includes(getChatState())) {
        return;
      }
      setChatState("stopping");
      stopSpeaking(false);
      activeAbortController?.abort();
      const taskId = activeTaskId;
      const approvalId = getActiveApprovalId();
      if (approvalId) {
        const qtBridge = getQtBridge();
        if (qtBridge && typeof qtBridge.cancelApprovalNotification === "function") {
          qtBridge.cancelApprovalNotification(approvalId);
        }
        removeApprovalBubble(approvalId);
      }
      let summary = { completed: [], not_executed: ["后续 Brain、Observe 与动作步骤"], uncertain: [] };
      try {
        const response = await fetch(`${backendBaseUrl()}/api/chat/tasks/${encodeURIComponent(taskId)}/stop`, {
          method: "POST",
        });
        if (response.ok) {
          summary = await response.json();
        }
      } catch (_) {}
      const parts = ["已由用户停止。"];
      if (summary.completed?.length) parts.push(`已完成：${summary.completed.join("、")}。`);
      if (summary.not_executed?.length) parts.push(`未执行：${summary.not_executed.join("、")}。`);
      if (summary.uncertain?.length) parts.push(`仍不确定：${summary.uncertain.join("、")}。`);
      const stoppedTurn = chatWorklogController.appendWorklogPhase?.({
        category: "stopped",
        status: "stopped",
        text: "已由用户停止",
        source: "local_system",
        transient: false,
      });
      chatWorklogController.finishWorklogProcess?.(stoppedTurn, "stopped");
      appendMessage("pet", parts.join(" "), { speakerName: "系统" });
      setActiveApprovalId("");
      setPendingApprovalInputTurnId("");
      activeAbortController = null;
      activeTaskId = "";
      setChatState("stopped");
    }

    function backendBaseUrl() {
      return String(state.chat?.backend_url || "").replace(/\/$/, "");
    }

    function finishSpeechIfIdle() {
      markSpeechStreamFinished();
      feedSpeakBuffer("", true);
      if (!isSpeechPlaying() && speechQueueLength() === 0) {
        setChatState("idle");
      }
    }

    function buildChatStreamPayload(userText, retryFromAssistantTurn) {
      const currentChatMode = getCurrentChatMode();
      const currentMemoryMode = getCurrentMemoryMode();
      return {
        task_id: activeTaskId,
        session_id: state.chat.session_id || "default",
        model: state.chat.model || "gpt-5.4",
        system_prompt: state.chat.system_prompt || "",
        expression_mode: !!state.chat.expression_mode,
        expression_output_format: state.chat.expression_output_format || "ndjson_v1",
        available_expressions: Array.isArray(state.chat.available_expressions)
          ? state.chat.available_expressions
          : [],
        react_enabled: state.chat.react_enabled !== false,
        react_visibility: state.chat.react_visibility || "inline",
        max_reasoning_steps: Number(state.chat.max_reasoning_steps || 10),
        chat_mode: currentChatMode,
        memory_mode: currentMemoryMode,
        skill_ids: currentChatMode === "chat" ? [] : activeChatSkillIds(),
        retry_from_assistant_turn: retryFromAssistantTurn,
        text: userText,
      };
    }

    async function continueApproval(turnId, approved, userText = "", options = {}) {
      const backend = backendBaseUrl();
      if (!backend) {
        appendMessage("error", "后端地址未配置");
        return;
      }
      stopSpeaking(false);
      setChatState("streaming");
      beginSpeechStream();
      setReceivedStructuredSegment(false);
      const turnKey = String(turnId || "").trim();
      const thoughtSessionId = chatWorklogController.resolveApprovalThoughtSessionId(turnKey);
      activeAbortController = new AbortController();
      try {
        const resp = await fetch(`${backend}/api/human-ops/proposals/${encodeURIComponent(turnId)}/decision`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approved: !!approved, user_text: String(userText || "") }),
          signal: activeAbortController.signal,
        });
        removeApprovalBubble(turnId);
        const result = await consumeChatStream(resp, { thoughtSessionId });
        if (result.awaitingApproval) {
          setChatState("awaiting_approval");
          chatWorklogController.setApprovalThoughtSessionId(turnKey, thoughtSessionId);
          return;
        }
        chatWorklogController.deleteApprovalThoughtSessionId(turnKey);
        if (!result.awaitingApproval) {
          finishSpeechIfIdle();
        }
        if (result.approved === false && chatInputEl && typeof chatInputEl.focus === "function") {
          setChatState("idle");
          chatInputEl.focus();
        }
      } catch (err) {
        if (err?.name === "AbortError" && ["stopping", "stopped"].includes(getChatState())) return;
        chatWorklogController.deleteApprovalThoughtSessionId(turnKey);
        clearActiveThoughtSession(thoughtSessionId);
        appendMessage("error", `对话失败：${err?.message || String(err)}`, {
          speakerName: "系统",
        });
        setChatState("idle");
      } finally {
        setActiveApprovalId("");
        setPendingApprovalInputTurnId("");
        const currentState = getChatState();
        if (currentState !== "awaiting_approval" && currentState !== "idle" && !isSpeechPlaying()) {
          setChatState("idle");
        }
      }
    }

    async function streamChat(text) {
      const backend = backendBaseUrl();
      if (!backend) {
        appendMessage("error", "后端地址未配置", { speakerName: "系统" });
        return;
      }
      const userText = String(text || "").trim();
      if (!userText) {
        return;
      }
      const pendingApprovalInputTurnId = getPendingApprovalInputTurnId();
      if (pendingApprovalInputTurnId) {
        appendMessage("user", userText, { speakerName: "你" });
        setActiveApprovalId(String(pendingApprovalInputTurnId));
        await continueApproval(pendingApprovalInputTurnId, false, userText);
        return;
      }
      if (getCurrentMemoryMode() !== "temporary") {
        await ensureTopicReady();
      }
      if (getCurrentChatMode() === "skill") {
        await loadChatSkills(false);
        if (!activeChatSkillIds().length) {
          appendMessage("error", "技能模式至少需要启用一个技能。请先打开“技能”面板，或去设置页启用默认技能。", {
            speakerName: "系统",
          });
          toggleChatSkillsDrawer(true);
          return;
        }
      }
      const retryEdit = getPendingRetryEdit();
      let retryFromAssistantTurn = null;
      if (retryEdit?.messageId) {
        const truncated = truncateChatAfterMessage(retryEdit.messageId);
        retryFromAssistantTurn = Number(truncated?.assistantTurn || retryEdit.assistantTurn || 0) || null;
        clearPendingRetryEdit();
      }
      appendMessage("user", userText, { speakerName: "你", assistantTurn: retryFromAssistantTurn });
      stopSpeaking(false);
      setChatState("streaming");
      beginSpeechStream();
      setReceivedStructuredSegment(false);
      setActiveApprovalId("");
      activeTaskId = newTaskId();
      activeAbortController = new AbortController();
      const thoughtSessionId = beginThoughtSession();

      try {
        const resp = await fetch(`${backend}/api/chat/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildChatStreamPayload(userText, retryFromAssistantTurn)),
          signal: activeAbortController.signal,
        });

        const result = await consumeChatStream(resp, { thoughtSessionId });
        if (result.awaitingApproval) {
          setChatState("awaiting_approval");
          return;
        }
      } catch (err) {
        if (err?.name === "AbortError" && ["stopping", "stopped"].includes(getChatState())) return;
        clearActiveThoughtSession(thoughtSessionId);
        appendMessage("error", `对话失败：${err?.message || String(err)}`, {
          speakerName: "系统",
        });
        setChatState("idle");
        return;
      }

      finishSpeechIfIdle();
    }

    async function submitChatInput() {
      if (!canSubmitChatInput() || isAsrBusy()) {
        return;
      }
      const text = chatInputEl?.value || "";
      if (!String(text || "").trim()) {
        chatInputEl?.focus();
        return;
      }
      if (chatInputEl) {
        chatInputEl.value = "";
      }
      await streamChat(text);
    }

    return {
      continueApproval,
      stopTask,
      streamChat,
      submitChatInput,
    };
  }

  window.IpetChatSubmit = {
    createChatSubmitController,
  };
})();
