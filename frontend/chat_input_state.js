(() => {
  function createChatInputStateController(deps = {}) {
    const refs = deps.refs || {};
    const chatSendEl = refs.chatSendEl || null;
    const chatInputEl = refs.chatInputEl || null;
    const chatTokenCountEl = refs.chatTokenCountEl || null;
    const getChatState = typeof deps.getChatState === "function" ? deps.getChatState : () => "idle";
    const isAsrBusy = typeof deps.isAsrBusy === "function" ? deps.isAsrBusy : () => false;

    function isSubmittableChatState(chatState) {
      return ["idle", "stopped", "awaiting_followup_input"].includes(chatState);
    }

    function canSubmitChatInput() {
      return isSubmittableChatState(getChatState());
    }

    function setChatTokenUsage(usage) {
      if (!chatTokenCountEl) {
        return;
      }
      if (!usage || typeof usage !== "object") {
        chatTokenCountEl.textContent = "Token --";
        return;
      }
      const count = (value) => {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0;
      };
      const input = count(usage.input_tokens);
      const output = count(usage.output_tokens);
      const total = count(usage.total_tokens || input + output);
      chatTokenCountEl.textContent = `输入 ${input} · 输出 ${output} · 总计 ${total}`;
    }

    function syncChatInputAvailability() {
      const chatState = getChatState();
      const asrBusy = isAsrBusy();
      if (chatSendEl) {
        const stoppable = ["streaming", "awaiting_approval", "stopping"].includes(chatState);
        chatSendEl.disabled = (!stoppable && !isSubmittableChatState(chatState)) || asrBusy || chatState === "stopping";
        chatSendEl.classList.toggle("is-stop", stoppable);
        chatSendEl.textContent = chatState === "stopping" ? "停止中…" : stoppable ? "■ 停止" : "发送";
        chatSendEl.setAttribute("aria-label", stoppable ? "停止当前任务" : "发送消息");
      }
      if (chatInputEl) {
        chatInputEl.disabled = ["streaming", "stopping"].includes(chatState);
        chatInputEl.readOnly = asrBusy;
      }
    }

    return Object.freeze({
      canSubmitChatInput,
      setChatTokenUsage,
      syncChatInputAvailability,
    });
  }

  window.IpetChatInputState = Object.freeze({
    createChatInputStateController,
  });
})();
