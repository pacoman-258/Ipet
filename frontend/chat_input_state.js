(() => {
  function createChatInputStateController(deps = {}) {
    const refs = deps.refs || {};
    const chatSendEl = refs.chatSendEl || null;
    const chatInputEl = refs.chatInputEl || null;
    const getChatState = typeof deps.getChatState === "function" ? deps.getChatState : () => "idle";
    const isAsrBusy = typeof deps.isAsrBusy === "function" ? deps.isAsrBusy : () => false;

    function isSubmittableChatState(chatState) {
      return chatState === "idle" || chatState === "awaiting_followup_input";
    }

    function canSubmitChatInput() {
      return isSubmittableChatState(getChatState());
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
      syncChatInputAvailability,
    });
  }

  window.IpetChatInputState = Object.freeze({
    createChatInputStateController,
  });
})();
