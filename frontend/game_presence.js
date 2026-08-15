(() => {
  const POLL_INTERVAL_MS = 750;

  function normalizeConfig(value = {}) {
    const source = value && typeof value === "object" ? value : {};
    const categories = source.categories && typeof source.categories === "object" ? source.categories : {};
    return {
      enabled: source.enabled !== false,
      adapter: "slay_the_spire_2",
      min_reaction_interval_sec: Math.max(0, Math.min(60, Number(source.min_reaction_interval_sec) || 5)),
      max_reactions_per_minute: Math.max(1, Math.min(30, Number(source.max_reactions_per_minute) || 6)),
      reaction_instruction: String(source.reaction_instruction || "").slice(0, 500),
      categories: {
        combat: categories.combat !== false,
        growth: categories.growth !== false,
        route: categories.route !== false,
        resources: categories.resources !== false,
        outcome: categories.outcome !== false,
      },
    };
  }

  function createGamePresenceController(deps = {}) {
    const state = deps.state || {};
    const refs = deps.refs || {};
    const runtimeWindow = deps.window || window;
    const runtimeDocument = deps.document || runtimeWindow.document || document;
    const fetchFn = deps.fetch || ((...args) => runtimeWindow.fetch(...args));
    const getChatState = typeof deps.getChatState === "function" ? deps.getChatState : () => "idle";
    const isAsrBusy = typeof deps.isAsrBusy === "function" ? deps.isAsrBusy : () => false;
    const speechController = deps.speechController || {};

    let config = normalizeConfig();
    let status = { state: "waiting", connected: false, paused: false, session_id: "" };
    let timerId = 0;
    let statusPolling = false;
    let reactionPolling = false;

    function backendUrl() {
      return String(state.chat?.backend_url || "http://127.0.0.1:8008").replace(/\/$/, "");
    }

    function gameCapability() {
      return String(state.game_capability || "").trim();
    }

    let capabilityRequest = null;

    async function ensureGameCapability() {
      const existing = gameCapability();
      if (existing) return existing;
      if (!capabilityRequest) {
        capabilityRequest = (async () => {
          const response = await fetchFn(`${backendUrl()}/api/settings/config`, { method: "GET" });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const payload = await response.json();
          const capability = String(payload?.game_capability || "").trim();
          if (!capability) throw new Error("game capability is unavailable");
          state.game_capability = capability;
          return capability;
        })().finally(() => {
          capabilityRequest = null;
        });
      }
      return capabilityRequest;
    }

    async function postJson(path, payload) {
      const headers = { "Content-Type": "application/json" };
      headers["X-Ipet-Game-Capability"] = await ensureGameCapability();
      const response = await fetchFn(`${backendUrl()}${path}`, {
        method: "POST",
        headers,
        body: JSON.stringify(payload || {}),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    }

    async function getJson(path) {
      const headers = {};
      headers["X-Ipet-Game-Capability"] = await ensureGameCapability();
      const response = await fetchFn(`${backendUrl()}${path}`, { method: "GET", headers });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    }

    function detailForStatus(nextStatus) {
      const current = String(nextStatus?.state || "waiting");
      if (current === "active") return "已连接，点击暂停本局反应";
      if (current === "paused") return "本局已暂停，点击恢复";
      if (current === "connected") return "已连接，等待新 Run";
      if (current === "incompatible") {
        return nextStatus?.local_player_identified === false ? "无法识别本地玩家" : "当前游戏版本不兼容";
      }
      if (current === "disabled") return "已在设置中关闭";
      return "等待杀戮尖塔 2";
    }

    function renderStatus(nextStatus = status) {
      status = { ...status, ...(nextStatus || {}) };
      const menu = refs.petMenuGameEl;
      if (!menu) return;
      const active = status.state === "active";
      const paused = status.state === "paused";
      menu.classList.toggle("is-active", active);
      menu.classList.toggle("is-paused", paused);
      menu.disabled = !status.connected || ["connected", "incompatible", "disabled"].includes(status.state);
      menu.setAttribute("aria-pressed", active ? "true" : "false");
      if (refs.petMenuGameLabelEl) {
        refs.petMenuGameLabelEl.textContent = paused
          ? "游戏模式 · 已暂停"
          : active
            ? "游戏模式 · 运行中"
            : status.state === "connected"
              ? "游戏模式 · 已连接"
              : status.state === "incompatible"
                ? "游戏模式 · 不兼容"
                : status.state === "disabled"
                  ? "游戏模式 · 已关闭"
                  : "游戏模式 · 等待杀戮尖塔 2";
      }
      if (refs.petMenuGameDetailEl) {
        refs.petMenuGameDetailEl.textContent = detailForStatus(status);
      }
    }

    function clientState() {
      return {
        chat_busy: String(getChatState() || "idle") !== "idle",
        asr_busy: isAsrBusy(),
        speech_busy: speechController.isPlaying?.() === true || Number(speechController.queueLength?.() || 0) > 0,
        document_hidden: runtimeDocument.hidden === true,
      };
    }

    async function sendFeedback(id, outcome) {
      if (!id) return;
      try {
        await postJson("/api/game/feedback", { id, outcome });
      } catch (_) {}
    }

    async function deliver(delivery) {
      const id = String(delivery?.id || "");
      const text = String(delivery?.text || "").trim();
      if (!id || !text) return;
      const outcomeAfterRun = status.state === "connected" && delivery.category === "outcome";
      if ((!outcomeAfterRun && status.state !== "active")
        || String(delivery.session_id || "") !== String(status.session_id || "")) {
        await sendFeedback(id, "expired");
        return;
      }
      if (delivery.interrupt === true) {
        speechController.stopSpeaking?.(false);
      } else if (clientState().speech_busy) {
        await sendFeedback(id, "replaced");
        return;
      }
      const speechRunId = speechController.beginStream?.("game");
      speechController.enqueueTTSChunk?.(text);
      speechController.markStreamFinished?.();
      if (typeof speechController.whenRunSettled === "function") {
        speechController.whenRunSettled(speechRunId).then((outcome) => {
          const normalized = ["delivered", "failed", "replaced"].includes(outcome) ? outcome : "failed";
          sendFeedback(id, normalized);
        });
      } else {
        await sendFeedback(id, "delivered");
      }
    }

    function applyStatus(nextStatus) {
      const previousState = status.state;
      const previousSession = status.session_id;
      renderStatus(nextStatus);
      const disconnected = previousSession && (!status.connected || status.session_id !== previousSession);
      const becameUnavailable = ["waiting", "disabled", "incompatible", "paused"].includes(status.state)
        && previousState === "active";
      if ((disconnected || becameUnavailable) && speechController.source?.() === "game") {
        speechController.stopSpeaking?.(false);
      }
    }

    async function refreshStatus() {
      if (statusPolling) return;
      statusPolling = true;
      try {
        applyStatus(await getJson("/api/game/status"));
      } catch (_) {
        applyStatus({ state: config.enabled ? "waiting" : "disabled", connected: false, session_id: "" });
      } finally {
        statusPolling = false;
      }
    }

    async function pollReaction() {
      if (reactionPolling) return;
      reactionPolling = true;
      try {
        const payload = await postJson("/api/game/pulse", { client_state: clientState() });
        if (payload?.status) applyStatus(payload.status);
        if (payload?.delivery) await deliver(payload.delivery);
      } catch (_) {
        // Connection state is owned by the independent, non-blocking status request.
      } finally {
        reactionPolling = false;
      }
    }

    function pollNow() {
      return Promise.all([refreshStatus(), pollReaction()]);
    }

    async function togglePause() {
      if (!status.connected || !["active", "paused"].includes(status.state)) return false;
      try {
        const payload = await postJson("/api/game/session/pause", {
          paused: status.state !== "paused",
          session_id: status.session_id,
        });
        applyStatus(payload);
        return payload?.ok === true;
      } catch (_) {
        return false;
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
      state.game = { ...config };
      stop();
      if (!config.enabled && speechController.source?.() === "game") {
        speechController.stopSpeaking?.(false);
      }
      renderStatus({ state: config.enabled ? "waiting" : "disabled", connected: false, session_id: "" });
      if (config.enabled) {
        timerId = runtimeWindow.setInterval(pollNow, POLL_INTERVAL_MS);
        pollNow();
      }
    }

    renderStatus(status);
    return Object.freeze({ applyConfig, pollNow, togglePause, stop, status: () => ({ ...status }) });
  }

  window.IpetGamePresence = Object.freeze({ createGamePresenceController, normalizeConfig });
})();
