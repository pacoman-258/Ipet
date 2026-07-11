(() => {
  function createChatInputStateController(deps = {}) {
    const refs = deps.refs || {};
    const chatSendEl = refs.chatSendEl || null;
    const chatInputEl = refs.chatInputEl || null;
    const chatTokenCountEl = refs.chatTokenCountEl || null;
    const getChatState = typeof deps.getChatState === "function" ? deps.getChatState : () => "idle";
    const isAsrBusy = typeof deps.isAsrBusy === "function" ? deps.isAsrBusy : () => false;

    function isSubmittableChatState(chatState) {
      return chatState === "idle" || chatState === "awaiting_followup_input";
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
        chatSendEl.disabled = !isSubmittableChatState(chatState) || asrBusy;
      }
      if (chatInputEl) {
        chatInputEl.disabled = chatState === "streaming";
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
