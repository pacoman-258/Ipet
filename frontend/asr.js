(() => {
  function createAsrController(deps = {}) {
    const helpers = deps.helpers || window.IpetIndexHelpers || {};
    const state = deps.state || {};
    const refs = deps.refs || {};
    const runtimeWindow = deps.window || window;
    const runtimeDocument = deps.document || document;
    const runtimeNavigator = deps.navigator || runtimeWindow.navigator || {};
    const fetchFn = deps.fetch || runtimeWindow.fetch;
    const syncChatInputAvailability = typeof deps.syncChatInputAvailability === "function"
      ? deps.syncChatInputAvailability
      : () => {};
    const getChatState = typeof deps.getChatState === "function" ? deps.getChatState : () => "";
    const callQtBridge = typeof deps.callQtBridge === "function" ? deps.callQtBridge : () => false;

    let asrAudioContext = null;
    let asrMediaStream = null;
    let asrInputSource = null;
    let asrProcessor = null;
    let asrSilenceGain = null;
    let asrSocket = null;
    let asrStarting = false;
    let asrRecording = false;
    let asrAwaitingFinal = false;
    let asrHotkeyPressed = false;
    let asrRunToken = 0;
    let asrDraftState = null;
    let activeAsrBaseUrl = "";
    let asrStatusTimer = 0;

    function chatInputEl() {
      return refs.chatInputEl;
    }

    function chatAsrStatusEl() {
      return refs.chatAsrStatusEl;
    }

    function chatPanelEl() {
      return refs.chatPanelEl;
    }

    function normalizeAsrConfig(raw) {
      return helpers.normalizeAsrConfig(raw);
    }

    function asrConfig() {
      return normalizeAsrConfig(state.chat?.asr || {});
    }

    function isProbablyMacOS() {
      const platform = String(runtimeNavigator.userAgentData?.platform || runtimeNavigator.platform || "").toLowerCase();
      return platform.includes("mac");
    }

    function defaultAsrStatusText() {
      const config = asrConfig();
      if (!config.enabled) {
        if (isProbablyMacOS()) {
          return "[macOS] ASR 默认关闭，聊天仍可用";
        }
        return "\u8bed\u97f3\u8f93\u5165\u5df2\u5173\u95ed";
      }
      return `\u6309\u4f4f ${config.push_to_talk_key} \u8bf4\u8bdd`;
    }

    function activeAsrStatusText() {
      return `\u6309\u4f4f ${asrConfig().push_to_talk_key} \uff1a\u8bed\u97f3\u8f93\u5165\u4e2d\uff0c\u677e\u5f00\u7ed3\u675f`;
    }

    function setAsrStatus(message, tone = "idle") {
      const statusEl = chatAsrStatusEl();
      if (!statusEl) {
        return;
      }
      if (asrStatusTimer) {
        runtimeWindow.clearTimeout(asrStatusTimer);
        asrStatusTimer = 0;
      }
      statusEl.textContent = String(message || defaultAsrStatusText());
      statusEl.classList.toggle("is-active", tone === "active");
      statusEl.classList.toggle("is-error", tone === "error");
      if (tone === "error") {
        asrStatusTimer = runtimeWindow.setTimeout(() => {
          statusEl.classList.remove("is-error");
          statusEl.classList.remove("is-active");
          statusEl.textContent = defaultAsrStatusText();
          asrStatusTimer = 0;
        }, 2600);
      }
    }

    function isBusy() {
      return asrStarting || asrRecording || asrAwaitingFinal;
    }

    function matchesPushToTalkKey(event) {
      const config = asrConfig();
      const key = config.push_to_talk_key;
      if (key === "Ctrl") {
        return event.key === "Control" && !event.altKey && !event.metaKey && !event.shiftKey;
      }
      if (key === "Space") {
        return (event.code === "Space" || event.key === " " || event.key === "Spacebar") && !event.ctrlKey && !event.altKey && !event.metaKey;
      }
      return event.key === "Alt" && !event.ctrlKey && !event.metaKey && !event.shiftKey;
    }

    function canStartPushToTalk(options = {}) {
      const { allowWithoutFocus = false } = options;
      return asrConfig().enabled
        && chatPanelEl()?.classList?.contains("open")
        && getChatState() === "idle"
        && (allowWithoutFocus || runtimeDocument.hasFocus());
    }

    function shouldHandlePushToTalk(event, options = {}) {
      return !!event && canStartPushToTalk(options);
    }

    function candidateAsrBaseUrls() {
      const seen = new Set();
      const candidates = [
        String(state.chat?.backend_url || "").trim(),
        String(state.chat?.asr?.api_base_url || "").trim(),
      ];
      return candidates.filter((value) => {
        if (!value || seen.has(value)) {
          return false;
        }
        seen.add(value);
        return true;
      });
    }

    function buildAsrWebSocketUrl(baseUrl = "") {
      const asrBaseUrl = String(baseUrl || activeAsrBaseUrl || candidateAsrBaseUrls()[0] || "").trim();
      if (!asrBaseUrl) {
        throw new Error("\u540e\u7aef\u5730\u5740\u672a\u914d\u7f6e");
      }
      const url = new URL(asrBaseUrl);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const basePath = url.pathname.replace(/\/+$/, "");
      url.pathname = `${basePath}/api/asr/stream`.replace(/\/+/g, "/");
      url.search = "";
      url.hash = "";
      return url.toString();
    }

    function buildAsrHealthUrl(baseUrl = "") {
      const asrBaseUrl = String(baseUrl || activeAsrBaseUrl || candidateAsrBaseUrls()[0] || "").trim();
      if (!asrBaseUrl) {
        throw new Error("\u540e\u7aef\u5730\u5740\u672a\u914d\u7f6e");
      }
      const url = new URL(asrBaseUrl);
      const basePath = url.pathname.replace(/\/+$/, "");
      url.pathname = `${basePath}/api/health`.replace(/\/+/g, "/");
      url.search = "";
      url.hash = "";
      return url.toString();
    }

    function buildAsrWarmupUrl(baseUrl = "") {
      const asrBaseUrl = String(baseUrl || candidateAsrBaseUrls()[0] || "").trim();
      if (!asrBaseUrl) {
        throw new Error("\u540e\u7aef\u5730\u5740\u672a\u914d\u7f6e");
      }
      const url = new URL(asrBaseUrl);
      const basePath = url.pathname.replace(/\/+$/, "");
      url.pathname = `${basePath}/api/asr/warmup`.replace(/\/+/g, "/");
      url.search = "";
      url.hash = "";
      return url.toString();
    }

    async function probeAsrAvailability() {
      let lastMessage = "ASR \u4e0d\u53ef\u7528";
      for (const baseUrl of candidateAsrBaseUrls()) {
        const AbortCtor = typeof runtimeWindow.AbortController === "function" ? runtimeWindow.AbortController : null;
        const controller = AbortCtor ? new AbortCtor() : null;
        const timer = controller
          ? runtimeWindow.setTimeout(() => controller.abort(), 1500)
          : 0;
        try {
          const response = await fetchFn(buildAsrHealthUrl(baseUrl), {
            method: "GET",
            cache: "no-store",
            signal: controller?.signal,
          });
          const payload = await response.json().catch(() => ({}));
          const failureMessage = String(payload?.message || "ASR \u4e0d\u53ef\u7528").trim() || "ASR \u4e0d\u53ef\u7528";
          lastMessage = failureMessage;
          if (!response.ok || !payload?.asr) {
            continue;
          }
          return {
            ok: true,
            message: "",
            baseUrl,
          };
        } catch (_) {
          lastMessage = "ASR \u4e0d\u53ef\u7528";
        } finally {
          if (timer) {
            runtimeWindow.clearTimeout(timer);
          }
        }
      }
      return {
        ok: false,
        message: lastMessage,
        baseUrl: "",
      };
    }

    async function requestAsrWarmup() {
      if (callQtBridge("startAsrWarmup")) {
        return;
      }
      for (const baseUrl of candidateAsrBaseUrls()) {
        try {
          await fetchFn(buildAsrWarmupUrl(baseUrl), {
            method: "POST",
            cache: "no-store",
          });
          return;
        } catch (_) {
          // try next candidate
        }
      }
    }

    function shouldRetryAsrAvailability(message) {
      const text = String(message || "").trim();
      return text.includes("尚未启动") || text.includes("开始初始化") || text.includes("正在加载模型") || text.includes("正在预热");
    }

    function createAsrDraft() {
      const inputEl = chatInputEl();
      const value = String(inputEl.value || "");
      const start = Number.isFinite(inputEl.selectionStart) ? inputEl.selectionStart : value.length;
      const end = Number.isFinite(inputEl.selectionEnd) ? inputEl.selectionEnd : start;
      asrDraftState = { baseValue: value, start, end };
    }

    function renderAsrDraft(text) {
      const inputEl = chatInputEl();
      if (!asrDraftState) {
        createAsrDraft();
      }
      const draftText = String(text || "");
      const before = asrDraftState.baseValue.slice(0, asrDraftState.start);
      const after = asrDraftState.baseValue.slice(asrDraftState.end);
      inputEl.value = `${before}${draftText}${after}`;
      const cursor = before.length + draftText.length;
      inputEl.setSelectionRange(cursor, cursor);
    }

    function revertAsrDraft() {
      const inputEl = chatInputEl();
      if (!asrDraftState) {
        return;
      }
      inputEl.value = asrDraftState.baseValue;
      inputEl.setSelectionRange(asrDraftState.start, asrDraftState.end);
      asrDraftState = null;
      inputEl.focus();
    }

    function commitAsrDraft(text) {
      const inputEl = chatInputEl();
      const finalText = String(text || "").trim();
      if (!asrDraftState) {
        if (finalText) {
          inputEl.value = `${inputEl.value || ""}${finalText}`;
        }
        inputEl.focus();
        return;
      }
      if (!finalText) {
        revertAsrDraft();
        return;
      }
      renderAsrDraft(finalText);
      asrDraftState = null;
      inputEl.focus();
    }

    function webSocketCtor() {
      return deps.WebSocket || runtimeWindow.WebSocket;
    }

    function closeAsrSocket() {
      if (!asrSocket) {
        return;
      }
      try {
        const WebSocketCtor = webSocketCtor();
        const openState = WebSocketCtor?.OPEN ?? 1;
        const connectingState = WebSocketCtor?.CONNECTING ?? 0;
        asrSocket.onopen = null;
        asrSocket.onmessage = null;
        asrSocket.onerror = null;
        asrSocket.onclose = null;
        if (asrSocket.readyState === openState || asrSocket.readyState === connectingState) {
          asrSocket.close();
        }
      } catch (_) {}
      asrSocket = null;
    }

    function stopAsrCapturePipeline() {
      if (asrProcessor) {
        try {
          asrProcessor.disconnect();
        } catch (_) {}
      }
      if (asrInputSource) {
        try {
          asrInputSource.disconnect();
        } catch (_) {}
      }
      if (asrSilenceGain) {
        try {
          asrSilenceGain.disconnect();
        } catch (_) {}
      }
      if (asrMediaStream) {
        try {
          for (const track of asrMediaStream.getTracks()) {
            track.stop();
          }
        } catch (_) {}
      }
      if (asrAudioContext) {
        try {
          asrAudioContext.close().catch(() => {});
        } catch (_) {}
      }
      asrProcessor = null;
      asrInputSource = null;
      asrSilenceGain = null;
      asrMediaStream = null;
      asrAudioContext = null;
    }

    function cancelAsrSession(options = {}) {
      const { restoreInput = true, statusMessage = defaultAsrStatusText(), tone = "idle" } = options;
      asrRunToken += 1;
      asrHotkeyPressed = false;
      asrStarting = false;
      asrRecording = false;
      asrAwaitingFinal = false;
      activeAsrBaseUrl = "";
      stopAsrCapturePipeline();
      closeAsrSocket();
      if (restoreInput) {
        revertAsrDraft();
      } else {
        asrDraftState = null;
      }
      syncChatInputAvailability();
      setAsrStatus(statusMessage, tone);
    }

    function handleAsrPartial(text) {
      const partialText = String(text || "");
      if (!partialText) {
        return;
      }
      renderAsrDraft(partialText);
    }

    function handleAsrFinal(text) {
      const finalText = String(text || "");
      stopAsrCapturePipeline();
      closeAsrSocket();
      asrHotkeyPressed = false;
      asrStarting = false;
      asrRecording = false;
      asrAwaitingFinal = false;
      commitAsrDraft(finalText);
      syncChatInputAvailability();
      setAsrStatus(defaultAsrStatusText(), "idle");
    }

    function handleAsrError(error) {
      cancelAsrSession({
        restoreInput: true,
        statusMessage: `\u8bed\u97f3\u8f93\u5165\u5931\u8d25\uff1a${error?.message || String(error)}`,
        tone: "error",
      });
    }

    function downsampleFloat32ToInt16Buffer(input, inputSampleRate, outputSampleRate = 16000) {
      return helpers.downsampleFloat32ToInt16Buffer(input, inputSampleRate, outputSampleRate);
    }

    async function openAsrSocket(token) {
      return await new Promise((resolve, reject) => {
        let settled = false;
        let sawSocketError = false;
        let lastSocketErrorMessage = "";
        const WebSocketCtor = webSocketCtor();
        if (typeof WebSocketCtor !== "function") {
          reject(new Error("ASR WebSocket 不可用"));
          return;
        }
        const socket = new WebSocketCtor(buildAsrWebSocketUrl());
        socket.binaryType = "arraybuffer";
        asrSocket = socket;

        const fail = (error) => {
          if (settled) {
            return;
          }
          settled = true;
          reject(error);
        };

        socket.onopen = () => {
          if (token !== asrRunToken) {
            fail(new Error("ASR start cancelled"));
            return;
          }
          try {
            socket.send(JSON.stringify({
              type: "start",
              key: asrConfig().push_to_talk_key,
              language: "zh",
              punctuation: true,
              interim_results: asrConfig().interim_results,
            }));
          } catch (error) {
            fail(error);
          }
        };

        socket.onmessage = (event) => {
          let payload = null;
          try {
            payload = JSON.parse(String(event.data || "{}"));
          } catch (_) {
            return;
          }
          const type = String(payload?.type || "");
          if (type === "ready") {
            if (!settled) {
              settled = true;
              resolve(payload);
            }
            return;
          }
          if (type === "partial") {
            handleAsrPartial(String(payload?.text || ""));
            return;
          }
          if (type === "final") {
            handleAsrFinal(String(payload?.text || ""));
            return;
          }
          if (type === "error") {
            lastSocketErrorMessage = String(payload?.message || "ASR 错误");
            fail(new Error(lastSocketErrorMessage));
          }
        };

        socket.onerror = () => {
          sawSocketError = true;
        };

        socket.onclose = () => {
          if (!settled && token === asrRunToken && (asrStarting || asrRecording || asrAwaitingFinal)) {
            fail(new Error(lastSocketErrorMessage || (sawSocketError ? "ASR WebSocket 连接错误" : "ASR 连接已关闭")));
          }
        };
      });
    }

    async function startAsrCapturePipeline(token) {
      const mediaStream = await runtimeNavigator.mediaDevices.getUserMedia({ audio: true });
      if (token !== asrRunToken) {
        for (const track of mediaStream.getTracks()) {
          track.stop();
        }
        throw new Error("ASR start cancelled");
      }
      const AudioCtor = deps.AudioContext || runtimeWindow.AudioContext || runtimeWindow.webkitAudioContext;
      if (!AudioCtor) {
        for (const track of mediaStream.getTracks()) {
          track.stop();
        }
        throw new Error("\u5f53\u524d\u73af\u5883\u4e0d\u652f\u6301\u97f3\u9891\u91c7\u96c6");
      }
      const context = new AudioCtor();
      if (typeof context.resume === "function") {
        await context.resume().catch(() => {});
      }
      const source = context.createMediaStreamSource(mediaStream);
      const processor = context.createScriptProcessor(4096, 1, 1);
      const silenceGain = context.createGain();
      silenceGain.gain.value = 0;
      processor.onaudioprocess = (event) => {
        const WebSocketCtor = webSocketCtor();
        const openState = WebSocketCtor?.OPEN ?? 1;
        if (token !== asrRunToken || !asrRecording || !asrSocket || asrSocket.readyState !== openState) {
          return;
        }
        const channelData = event.inputBuffer.getChannelData(0);
        const audioBuffer = downsampleFloat32ToInt16Buffer(channelData, context.sampleRate, 16000);
        if (audioBuffer.byteLength) {
          asrSocket.send(audioBuffer);
        }
      };
      source.connect(processor);
      processor.connect(silenceGain);
      silenceGain.connect(context.destination);
      asrAudioContext = context;
      asrMediaStream = mediaStream;
      asrInputSource = source;
      asrProcessor = processor;
      asrSilenceGain = silenceGain;
    }

    async function startPushToTalk() {
      if (!canStartPushToTalk()) {
        return;
      }
      if (asrStarting || asrRecording || asrAwaitingFinal) {
        return;
      }
      if (!runtimeNavigator.mediaDevices || typeof runtimeNavigator.mediaDevices.getUserMedia !== "function") {
        setAsrStatus("\u5f53\u524d\u73af\u5883\u4e0d\u652f\u6301\u9ea6\u514b\u98ce\u91c7\u96c6", "error");
        return;
      }
      asrHotkeyPressed = true;
      const token = asrRunToken + 1;
      asrRunToken = token;
      asrStarting = true;
      syncChatInputAvailability();
      setAsrStatus(activeAsrStatusText(), "active");
      await requestAsrWarmup();
      let availability = await probeAsrAvailability();
      while (token === asrRunToken && asrHotkeyPressed && !availability.ok) {
        if (!shouldRetryAsrAvailability(availability.message)) {
          break;
        }
        setAsrStatus(availability.message || "ASR \u6b63\u5728\u52a0\u8f7d\u6a21\u578b\uff0c\u8bf7\u7ee7\u7eed\u6309\u4f4f\u70ed\u952e\u7b49\u5f85\u3002", "active");
        await new Promise((resolve) => runtimeWindow.setTimeout(resolve, 1000));
        availability = await probeAsrAvailability();
      }
      if (token !== asrRunToken || !asrHotkeyPressed) {
        cancelAsrSession({ restoreInput: true, statusMessage: defaultAsrStatusText(), tone: "idle" });
        return;
      }
      if (!availability.ok) {
        asrStarting = false;
        syncChatInputAvailability();
        setAsrStatus(availability.message || "ASR \u4e0d\u53ef\u7528", shouldRetryAsrAvailability(availability.message) ? "active" : "error");
        return;
      }
      activeAsrBaseUrl = String(availability.baseUrl || "").trim();
      createAsrDraft();
      renderAsrDraft("");
      try {
        await openAsrSocket(token);
        if (token !== asrRunToken || !asrHotkeyPressed) {
          throw new Error("ASR start cancelled");
        }
        await startAsrCapturePipeline(token);
        if (token !== asrRunToken || !asrHotkeyPressed) {
          throw new Error("ASR start cancelled");
        }
        asrStarting = false;
        asrRecording = true;
        syncChatInputAvailability();
        setAsrStatus(activeAsrStatusText(), "active");
      } catch (error) {
        if (String(error?.message || error) === "ASR start cancelled") {
          cancelAsrSession({ restoreInput: true, statusMessage: defaultAsrStatusText(), tone: "idle" });
          return;
        }
        handleAsrError(error);
      }
    }

    function stopPushToTalk() {
      asrHotkeyPressed = false;
      if (asrStarting) {
        cancelAsrSession({ restoreInput: true, statusMessage: defaultAsrStatusText(), tone: "idle" });
        return;
      }
      if (!asrRecording) {
        return;
      }
      asrRecording = false;
      asrAwaitingFinal = true;
      stopAsrCapturePipeline();
      syncChatInputAvailability();
      setAsrStatus("\u6b63\u5728\u8bc6\u522b...", "active");
      const WebSocketCtor = webSocketCtor();
      const openState = WebSocketCtor?.OPEN ?? 1;
      if (!asrSocket || asrSocket.readyState !== openState) {
        handleAsrError(new Error("ASR 连接不可用"));
        return;
      }
      try {
        asrSocket.send(JSON.stringify({ type: "stop" }));
      } catch (error) {
        handleAsrError(error);
      }
    }

    return {
      normalizeAsrConfig,
      asrConfig,
      isProbablyMacOS,
      defaultAsrStatusText,
      activeAsrStatusText,
      setAsrStatus,
      isBusy,
      matchesPushToTalkKey,
      canStartPushToTalk,
      shouldHandlePushToTalk,
      candidateAsrBaseUrls,
      buildAsrWebSocketUrl,
      buildAsrHealthUrl,
      buildAsrWarmupUrl,
      probeAsrAvailability,
      requestAsrWarmup,
      shouldRetryAsrAvailability,
      cancelAsrSession,
      downsampleFloat32ToInt16Buffer,
      startPushToTalk,
      stopPushToTalk,
    };
  }

  window.IpetAsr = {
    createAsrController,
  };
})();
