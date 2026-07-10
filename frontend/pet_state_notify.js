(() => {
  function createPetStateNotifyController(deps = {}) {
    const helpers = deps.helpers || window.IpetIndexHelpers || {};
    const state = deps.state || {};
    const refs = deps.refs || {};
    const chatTitleEl = refs.chatTitleEl;
    const getQtBridge = typeof deps.getQtBridge === "function" ? deps.getQtBridge : () => null;
    const parsePetNameFromPrompt =
      typeof helpers.parsePetNameFromPrompt === "function" ? helpers.parsePetNameFromPrompt : () => "";
    const roundInt =
      typeof helpers.roundInt === "function" ? helpers.roundInt : (value) => Math.round(Number(value) || 0);

    let notifyTimer = null;

    function ensureChatState() {
      if (!state.chat || typeof state.chat !== "object") {
        state.chat = {};
      }
      return state.chat;
    }

    function currentPetDisplayName() {
      return String(state.chat?.pet_display_name || "\u684c\u5ba0").trim() || "\u684c\u5ba0";
    }

    function syncPetDisplayName() {
      const chat = ensureChatState();
      const parsed = parsePetNameFromPrompt(state.chat?.system_prompt || "");
      const next = String(chat.pet_display_name || parsed || "\u684c\u5ba0").trim() || "\u684c\u5ba0";
      state.chat.pet_display_name = next;
      if (chatTitleEl) {
        chatTitleEl.textContent = `${next}\u5bf9\u8bdd`;
      }
      return next;
    }

    function speakerNameForRole(role) {
      if (typeof helpers.speakerNameForRole === "function") {
        return helpers.speakerNameForRole(role, currentPetDisplayName());
      }
      return role === "user" ? "\u4f60" : currentPetDisplayName();
    }

    function scheduleNotifyState() {
      if (notifyTimer) {
        return;
      }
      notifyTimer = setTimeout(() => {
        notifyTimer = null;
        notifyStateNow();
      }, 80);
    }

    function notifyStateNow() {
      const bridge = getQtBridge();
      if (!bridge || typeof bridge.petStateChanged !== "function") {
        return;
      }

      const payload = {
        scale: state.scale,
        offset_x: roundInt(state.offset_x),
        offset_y: roundInt(state.offset_y),
        rotation: state.rotation,
        opacity: state.opacity,
      };

      bridge.petStateChanged(JSON.stringify(payload));
    }

    return Object.freeze({
      syncPetDisplayName,
      speakerNameForRole,
      scheduleNotifyState,
      notifyStateNow,
    });
  }

  window.IpetPetStateNotify = Object.freeze({
    createPetStateNotifyController,
  });
})();
