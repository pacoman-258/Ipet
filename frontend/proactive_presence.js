(() => {
  function normalizeConfig(value = {}) {
    const source = value && typeof value === "object" ? value : {};
    const mode = ["off", "shadow", "active"].includes(source.mode) ? source.mode : "off";
    return {
      ...source,
      mode,
      poll_interval_sec: Math.max(5, Math.min(300, Number(source.poll_interval_sec) || 15)),
      speak_enabled: source.speak_enabled !== false,
      open_chat_on_speak: source.open_chat_on_speak !== false,
    };
  }

  function createProactivePresenceController(deps = {}) {
    const state = deps.state || {};
    const runtimeWindow = deps.window || window;
    const runtimeDocument = deps.document || runtimeWindow.document || document;
    const fetchFn = deps.fetch || ((...args) => runtimeWindow.fetch(...args));
    const getChatState = typeof deps.getChatState === "function" ? deps.getChatState : () => "idle";
    const getMemoryMode = typeof deps.getMemoryMode === "function" ? deps.getMemoryMode : () => "persistent";
    const isAsrBusy = typeof deps.isAsrBusy === "function" ? deps.isAsrBusy : () => false;
    const speechController = deps.speechController || {};
    const appendMessage = typeof deps.appendMessage === "function" ? deps.appendMessage : () => {};
    const openChat = typeof deps.openChat === "function" ? deps.openChat : () => {};

    let config = normalizeConfig();
    let timerId = 0;
    let polling = false;
    let listenersInstalled = false;
    let lastUserActivityAt = Date.now();

    function backendUrl() {
      return String(state.chat?.backend_url || "http://127.0.0.1:8008").replace(/\/$/, "");
    }

    function sessionId() {
      return String(state.chat?.session_id || "default");
    }

    function noteUserActivity() {
      lastUserActivityAt = Date.now();
    }

    function installActivityListeners() {
      if (listenersInstalled) return;
      listenersInstalled = true;
      ["keydown", "pointerdown", "input"].forEach((eventName) => {
        runtimeDocument.addEventListener(eventName, noteUserActivity, { passive: true });
      });
    }

    function clientBusy() {
      const chatState = String(getChatState() || "idle");
      return (
        chatState !== "idle" ||
        isAsrBusy() ||
        speechController.isPlaying?.() === true ||
        runtimeDocument.hidden === true
      );
    }

    async function postJson(path, payload) {
      const response = await fetchFn(`${backendUrl()}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return response.json();
    }

    async function sendFeedback(id, outcome) {
      if (!id) return;
      try {
        await postJson("/api/environment/feedback", { id, outcome, session_id: sessionId() });
      } catch (_) {}
    }

    async function deliver(delivery) {
      const id = String(delivery?.id || "");
      const text = String(delivery?.text || "").trim();
      if (!id || !text) return;
      if (config.mode !== "active" || getMemoryMode() !== "persistent") {
        await sendFeedback(id, "dismissed");
        return;
      }
      if (clientBusy() || Date.now() - lastUserActivityAt < 5000) {
        await sendFeedback(id, "deferred");
        return;
      }
      if (delivery.open_chat !== false && config.open_chat_on_speak) {
        openChat();
      }
      appendMessage("assistant", text, { speakerName: state.chat?.pet_display_name || "机魂" });
      if (delivery.speak !== false && config.speak_enabled) {
        speechController.beginStream?.();
        speechController.enqueueTTSChunk?.(text);
        speechController.markStreamFinished?.();
      }
      await sendFeedback(id, "delivered");
    }

    async function pollNow() {
      if (polling || config.mode !== "active" || getMemoryMode() !== "persistent") return;
      if (clientBusy() || Date.now() - lastUserActivityAt < 5000) return;
      polling = true;
      try {
        const payload = await postJson("/api/environment/pulse", {
          session_id: sessionId(),
          memory_mode: getMemoryMode(),
          client_state: {
            chat_busy: String(getChatState() || "idle") !== "idle",
            asr_busy: isAsrBusy(),
            speech_busy: speechController.isPlaying?.() === true,
            document_hidden: runtimeDocument.hidden === true,
          },
        });
        if (payload?.delivery) {
          await deliver(payload.delivery);
        }
      } catch (_) {
        // Presence is an enhancement; a backend restart must never disturb chat.
      } finally {
        polling = false;
      }
    }

    function stop() {
      if (timerId) {
        runtimeWindow.clearInterval(timerId);
        timerId = 0;
      }
    }

    function applyConfig(value) {
      config = normalizeConfig(value);
      state.environment = { ...config };
      stop();
      installActivityListeners();
      if (config.mode === "active") {
        timerId = runtimeWindow.setInterval(pollNow, config.poll_interval_sec * 1000);
      }
    }

    return Object.freeze({ applyConfig, pollNow, stop, noteUserActivity });
  }

  window.IpetProactivePresence = Object.freeze({
    createProactivePresenceController,
    normalizeConfig,
  });
})();
