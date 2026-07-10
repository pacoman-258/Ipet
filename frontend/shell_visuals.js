(() => {
  function createShellVisualsController(deps = {}) {
    const helpers = deps.helpers || window.IpetIndexHelpers || {};
    const state = deps.state || {};
    const refs = deps.refs || {};
    const document = deps.document || window.document;
    const getQtBridge = typeof deps.getQtBridge === "function" ? deps.getQtBridge : () => null;
    const topicHistoryEnabled = typeof deps.topicHistoryEnabled === "function" ? deps.topicHistoryEnabled : () => false;
    const clampValue = typeof helpers.clamp === "function"
      ? helpers.clamp
      : (value, min, max) => Math.min(max, Math.max(min, value));
    const toRateStringValue = typeof helpers.toRateString === "function"
      ? helpers.toRateString
      : (ratePct) => {
        let n = Number(ratePct);
        if (!Number.isFinite(n)) {
          n = 0;
        }
        n = Math.round(clampValue(n, -50, 100));
        return `${n >= 0 ? "+" : ""}${n}%`;
      };
    const roundIntValue = typeof helpers.roundInt === "function"
      ? helpers.roundInt
      : (value) => Math.round(Number(value) || 0);
    const helperBackgroundOverlayValue = typeof helpers.backgroundOverlayValue === "function"
      ? helpers.backgroundOverlayValue
      : (value) => {
        const n = Number(value);
        if (!Number.isFinite(n)) {
          return 0.42;
        }
        return clampValue(n, 0, 0.9);
      };

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

    function showError(msg) {
      if (!errorEl) {
        return;
      }
      errorEl.textContent = String(msg || "未知错误");
      errorEl.style.display = "block";
    }

    function clearError() {
      if (!errorEl) {
        return;
      }
      errorEl.style.display = "none";
      errorEl.textContent = "";
    }

    function logToQt(msg) {
      const qtBridge = getQtBridge();
      if (qtBridge && typeof qtBridge.log === "function") {
        qtBridge.log(String(msg));
      } else {
        console.log(msg);
      }
    }

    function clamp(value, min, max) {
      return clampValue(value, min, max);
    }

    function toRateString(ratePct) {
      return toRateStringValue(ratePct);
    }

    function roundInt(value) {
      return roundIntValue(value);
    }

    function backgroundOverlayValue(value) {
      return helperBackgroundOverlayValue(value);
    }

    function applyBackgroundVisuals() {
      const enabled = !!state.background_enabled;
      const imageUrl = String(state.background_image_url || "").trim();
      const overlay = backgroundOverlayValue(state.background_overlay_opacity);
      document.documentElement.style.setProperty("--backdrop-overlay", String(overlay));
      document.documentElement.style.setProperty("--shell-overlay", String(overlay));
      if (!enabled || !imageUrl) {
        if (backgroundLayerEl) {
          backgroundLayerEl.classList.remove("has-image");
        }
        if (backgroundImageEl) {
          backgroundImageEl.removeAttribute("src");
          backgroundImageEl.style.display = "none";
        }
        return;
      }
      if (backgroundImageEl) {
        backgroundImageEl.src = imageUrl;
      }
    }

    function refreshStatus() {
      if (!statusEl) {
        return;
      }
      statusEl.style.display = state.edit_mode ? "block" : "none";
      const modelName = state.model_url ? state.model_url.split("/").pop() : "(none)";
      statusEl.textContent = [
        `model: ${modelName}`,
        `edit: ${state.edit_mode ? "on" : "off"}`,
        `scale: ${state.scale.toFixed(2)}`,
        `offset: (${roundInt(state.offset_x)}, ${roundInt(state.offset_y)})`,
        `bg: ${state.background_enabled && state.background_image_url ? "image" : "default"}`,
      ].join("\n");
    }

    function updateShellButtons() {
      if (!chatPanelEl || !chatHistoryDrawerEl || !navChatButtonEl) {
        return;
      }
      const chatOpen = chatPanelEl.classList.contains("open");
      const historyOpen = chatOpen && chatHistoryDrawerEl.classList.contains("open");
      navChatButtonEl.classList.toggle("is-active", chatOpen && !historyOpen);
      navChatButtonEl.setAttribute("aria-pressed", String(chatOpen && !historyOpen));
      if (navHistoryButtonEl) {
        const enabled = topicHistoryEnabled();
        navHistoryButtonEl.disabled = !enabled;
        navHistoryButtonEl.classList.toggle("is-active", historyOpen);
        navHistoryButtonEl.setAttribute("aria-pressed", String(historyOpen));
        navHistoryButtonEl.title = enabled ? "\u67e5\u770b\u5386\u53f2\u8bdd\u9898" : "\u5f53\u524d\u672a\u542f\u7528\u8bdd\u9898\u4fdd\u5b58";
      }
    }

    if (backgroundImageEl) {
      backgroundImageEl.addEventListener("load", () => {
        if (backgroundLayerEl) {
          backgroundLayerEl.classList.add("has-image");
        }
        backgroundImageEl.style.display = "block";
      });

      backgroundImageEl.addEventListener("error", () => {
        if (backgroundLayerEl) {
          backgroundLayerEl.classList.remove("has-image");
        }
        backgroundImageEl.style.display = "none";
        backgroundImageEl.removeAttribute("src");
        if (state.background_enabled && state.background_image) {
          logToQt(`background image unavailable: ${state.background_image}`);
        }
      });
    }

    return Object.freeze({
      showError,
      clearError,
      logToQt,
      clamp,
      toRateString,
      roundInt,
      backgroundOverlayValue,
      applyBackgroundVisuals,
      refreshStatus,
      updateShellButtons,
    });
  }

  window.IpetShellVisuals = Object.freeze({
    createShellVisualsController,
  });
})();
