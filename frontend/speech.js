(() => {
  const DEFAULT_MOUTH_OPEN_PARAMETER_IDS = ["ParamMouthOpenY", "PARAM_MOUTH_OPEN_Y", "ParamMouthOpenX", "LipSync"];
  const DEFAULT_MOUTH_FORM_PARAMETER_IDS = ["ParamMouthForm", "PARAM_MOUTH_FORM"];

  function createSpeechController(deps = {}) {
    const helpers = deps.helpers || window.IpetIndexHelpers || {};
    const state = deps.state || {};
    const runtimeWindow = deps.window || window;
    const fetchFn = deps.fetch || ((...args) => runtimeWindow.fetch(...args));
    const appendMessage = typeof deps.appendMessage === "function" ? deps.appendMessage : () => null;
    const showError = typeof deps.showError === "function" ? deps.showError : () => {};
    const setChatState = typeof deps.setChatState === "function" ? deps.setChatState : () => {};
    const getCurrentModel = typeof deps.getCurrentModel === "function" ? deps.getCurrentModel : () => null;
    const getAudioContext = typeof deps.getAudioContext === "function" ? deps.getAudioContext : () => audioCtx;
    const setAudioContext = typeof deps.setAudioContext === "function"
      ? deps.setAudioContext
      : (nextAudioContext) => {
          audioCtx = nextAudioContext;
        };
    const clamp = (...args) => helpers.clamp(...args);
    const toRateString = (ratePct) => helpers.toRateString(ratePct);

    let activeAudio = null;
    let audioCtx = null;
    let analyser = null;
    let mediaSource = null;
    let lipsyncRAF = 0;
    let ttsQueue = [];
    let preparedAudioQueue = [];
    let ttsPlaying = false;
    let ttsSynthesizing = false;
    let ttsAbortController = null;
    let activePlaybackSettler = null;
    let speechRunId = 0;
    let streamFinished = false;
    let pendingSpeakBuffer = "";
    let pendingSpeakExpr = null;
    let speakFlushTimer = 0;

    function requestAnimationFrameSafe(callback) {
      const requestFrame = runtimeWindow.requestAnimationFrame || ((fn) => runtimeWindow.setTimeout(fn, 16));
      return requestFrame.call(runtimeWindow, callback);
    }

    function cancelAnimationFrameSafe(frameId) {
      const cancelFrame = runtimeWindow.cancelAnimationFrame || runtimeWindow.clearTimeout;
      cancelFrame.call(runtimeWindow, frameId);
    }

    function currentAudioContext() {
      return getAudioContext() || audioCtx;
    }

    function saveAudioContext(nextAudioContext) {
      audioCtx = nextAudioContext || null;
      setAudioContext(audioCtx);
    }

    function stopSpeaking(manual = false) {
      if (speakFlushTimer) {
        runtimeWindow.clearTimeout(speakFlushTimer);
        speakFlushTimer = 0;
      }
      speechRunId += 1;
      ttsQueue = [];
      discardPreparedAudio();
      ttsAbortController?.abort();
      ttsAbortController = null;
      pendingSpeakBuffer = "";
      pendingSpeakExpr = null;
      ttsPlaying = false;
      streamFinished = true;
      if (activeAudio) {
        try {
          activeAudio.pause();
          activeAudio.currentTime = 0;
        } catch (_) {}
      }
      activePlaybackSettler?.();
      activePlaybackSettler = null;
      try {
        if (runtimeWindow.speechSynthesis) {
          runtimeWindow.speechSynthesis.cancel();
        }
      } catch (_) {}
      stopLipSyncLoop();
      setChatState("idle");
      if (manual) {
        appendMessage("error", "\u8bed\u97f3\u5df2\u505c\u6b62");
      }
    }

    function beginStream() {
      speechRunId += 1;
      streamFinished = false;
      pendingSpeakBuffer = "";
      pendingSpeakExpr = null;
      ttsQueue = [];
      discardPreparedAudio();
      ttsAbortController?.abort();
      ttsAbortController = null;
    }

    function markStreamFinished() {
      streamFinished = true;
      requestNextTTSChunk();
      playNextTTSChunk().catch(() => {});
    }

    function isPlaying() {
      return ttsPlaying;
    }

    function queueLength() {
      return ttsQueue.length + preparedAudioQueue.length + (ttsSynthesizing ? 1 : 0);
    }

    function hasPendingSpeakBuffer() {
      return !!pendingSpeakBuffer.trim();
    }

    function setPendingSpeakBuffer(text) {
      pendingSpeakBuffer = String(text || "");
    }

    function ttsFailureMessage(detail) {
      if (state.chat?.tts_provider === "qwen_tts_local") {
        return "TTS异常";
      }
      return `TTS 失败：${detail}`;
    }

    function discardPreparedAudio() {
      const urlApi = runtimeWindow.URL || globalThis.URL;
      for (const item of preparedAudioQueue) {
        try {
          urlApi.revokeObjectURL(item.blobUrl);
        } catch (_) {}
      }
      preparedAudioQueue = [];
    }

    function speechPipelineIsIdle() {
      return !ttsPlaying && !ttsSynthesizing && !ttsQueue.length && !preparedAudioQueue.length;
    }

    function setMouthOpen(value) {
      const currentModel = getCurrentModel();
      if (!currentModel || !currentModel.internalModel || !currentModel.internalModel.coreModel) {
        return;
      }
      const core = currentModel.internalModel.coreModel;
      const v = clamp(Number(value) || 0, 0, 1);
      const openIds = Array.isArray(state.chat?.mouth_parameter_ids) && state.chat.mouth_parameter_ids.length
        ? state.chat.mouth_parameter_ids
        : DEFAULT_MOUTH_OPEN_PARAMETER_IDS;
      const formIds = Array.isArray(state.chat?.mouth_form_parameter_ids) && state.chat.mouth_form_parameter_ids.length
        ? state.chat.mouth_form_parameter_ids
        : DEFAULT_MOUTH_FORM_PARAMETER_IDS;
      for (const parameterId of openIds) {
        try {
          if (typeof core.setParameterValueById === "function") {
            core.setParameterValueById(parameterId, v, 0.8);
          } else if (typeof core.addParameterValueById === "function") {
            core.addParameterValueById(parameterId, v * 0.65, 0.8);
          }
        } catch (_) {
          // ignore missing mouth parameters
        }
      }
      for (const parameterId of formIds) {
        try {
          if (typeof core.setParameterValueById === "function") {
            core.setParameterValueById(parameterId, (v - 0.5) * 0.35, 0.35);
          } else if (typeof core.addParameterValueById === "function") {
            core.addParameterValueById(parameterId, (v - 0.5) * 0.15, 0.35);
          }
        } catch (_) {
          // ignore missing mouth form parameters
        }
      }
    }

    function resolveExpressionName(name) {
      const expr = String(name || "").trim();
      if (!expr) {
        return "";
      }
      const available = Array.isArray(state.chat?.available_expressions) ? state.chat.available_expressions : [];
      if (!available.length) {
        return expr;
      }
      if (available.includes(expr)) {
        return expr;
      }
      const normalized = expr.toLowerCase().replace(/[\s_-]+/g, "");
      const fallback = available.find((item) => String(item).toLowerCase().replace(/[\s_-]+/g, "") === normalized);
      return fallback || "";
    }

    function triggerExpressionSafe(name) {
      const expr = resolveExpressionName(name);
      const currentModel = getCurrentModel();
      if (!expr || !currentModel || typeof currentModel.expression !== "function") {
        return;
      }
      try {
        currentModel.expression(expr);
      } catch (_) {
        // ignore missing/invalid expression
      }
    }

    function stopLipSyncLoop() {
      if (lipsyncRAF) {
        cancelAnimationFrameSafe(lipsyncRAF);
        lipsyncRAF = 0;
      }
      setMouthOpen(0);
    }

    function startLipSync(audioEl) {
      try {
        const AudioContextCtor = runtimeWindow.AudioContext || runtimeWindow.webkitAudioContext;
        let nextAudioContext = currentAudioContext();
        if (!nextAudioContext) {
          nextAudioContext = new AudioContextCtor();
          saveAudioContext(nextAudioContext);
        }
        if (nextAudioContext.state === "suspended") {
          nextAudioContext.resume().catch(() => {});
        }
        if (mediaSource) {
          try {
            mediaSource.disconnect();
          } catch (_) {}
          mediaSource = null;
        }
        analyser = nextAudioContext.createAnalyser();
        analyser.fftSize = 2048;
        mediaSource = nextAudioContext.createMediaElementSource(audioEl);
        mediaSource.connect(analyser);
        analyser.connect(nextAudioContext.destination);
      } catch (_) {
        return;
      }

      const data = new Uint8Array(analyser.fftSize);
      const tick = () => {
        if (!analyser || audioEl.paused || audioEl.ended) {
          stopLipSyncLoop();
          return;
        }
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) {
          const n = (data[i] - 128) / 128;
          sum += n * n;
        }
        const rms = Math.sqrt(sum / data.length);
        const lipSyncGain = Math.max(0.5, Number(state.chat?.lip_sync_gain || 1));
        const mouth = clamp(rms * (4.8 + lipSyncGain * 1.8), 0, 1);
        setMouthOpen(mouth);
        lipsyncRAF = requestAnimationFrameSafe(tick);
      };
      tick();
    }

    function fallbackSpeakByBrowser(text) {
      const synth = runtimeWindow.speechSynthesis;
      const UtteranceCtor = runtimeWindow.SpeechSynthesisUtterance;
      if (!synth || !UtteranceCtor) {
        return Promise.resolve();
      }
      return new Promise((resolve) => {
        try {
          synth.cancel();
          const utt = new UtteranceCtor(text);
          utt.lang = "zh-CN";
          utt.rate = 1.0;
          utt.onstart = () => {
            let v = 0;
            const tick = () => {
              if (synth.speaking) {
                v = v > 0.4 ? 0.08 : 0.72;
                setMouthOpen(v);
                lipsyncRAF = requestAnimationFrameSafe(tick);
              } else {
                stopLipSyncLoop();
              }
            };
            tick();
          };
          utt.onend = () => {
            stopLipSyncLoop();
            resolve();
          };
          utt.onerror = () => {
            stopLipSyncLoop();
            resolve();
          };
          synth.speak(utt);
        } catch (_) {
          stopLipSyncLoop();
          resolve();
        }
      });
    }

    function enqueueTTSChunk(text, expr = null) {
      const chunk = (text || "").trim();
      if (!chunk) {
        return;
      }
      ttsQueue.push({ text: chunk, expr: expr ? String(expr).trim() : null, runId: speechRunId });
      requestNextTTSChunk();
    }

    function feedSpeakBuffer(delta, force = false, expr = null) {
      pendingSpeakBuffer += delta || "";
      if (expr) {
        pendingSpeakExpr = String(expr).trim() || pendingSpeakExpr;
      }
      const completedSentence = /[^。！？!?；;\n]*[。！？!?；;\n]+[”’」』）】]*/g;
      let match = null;
      let lastIndex = 0;
      let sentenceExpr = pendingSpeakExpr;
      while ((match = completedSentence.exec(pendingSpeakBuffer)) !== null) {
        const sentence = String(match[0] || "").trim();
        if (sentence) {
          enqueueTTSChunk(sentence, sentenceExpr);
          sentenceExpr = null;
        }
        lastIndex = completedSentence.lastIndex;
      }
      pendingSpeakBuffer = pendingSpeakBuffer.slice(lastIndex);
      pendingSpeakExpr = sentenceExpr;
      if (force) {
        const finalText = pendingSpeakBuffer.trim();
        const finalExpr = pendingSpeakExpr;
        pendingSpeakBuffer = "";
        pendingSpeakExpr = null;
        if (finalText) {
          enqueueTTSChunk(finalText, finalExpr);
        }
      }
    }

    async function requestTTSChunk(item, abortController) {
      const backend = String(state.chat?.backend_url || "").replace(/\/$/, "");
      if (!backend) {
        throw new Error(ttsFailureMessage("后端地址未配置"));
      }
      const resp = await fetchFn(`${backend}/api/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: abortController.signal,
        body: JSON.stringify({
          text: item.text,
          voice: state.chat.voice || "zh-CN-XiaoxiaoNeural",
          rate: toRateString(state.chat.rate_pct),
          volume: "+0%",
          provider: state.chat.tts_provider || "edge_tts",
          provider_url: state.chat.tts_provider_url || "",
        }),
      });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try {
          const errData = await resp.json();
          detail = errData?.detail || detail;
        } catch (_) {}
        throw new Error(ttsFailureMessage(detail));
      }

      const data = await resp.json();
      if (!data || !data.audio_url) {
        throw new Error(ttsFailureMessage("后端未返回 audio_url"));
      }
      const audioResp = await fetchFn(`${backend}${data.audio_url}`, { signal: abortController.signal });
      if (!audioResp.ok) {
        throw new Error(ttsFailureMessage(`音频下载 HTTP ${audioResp.status}`));
      }
      const audioBlob = await audioResp.blob();
      const urlApi = runtimeWindow.URL || globalThis.URL;
      return { ...item, blobUrl: urlApi.createObjectURL(audioBlob) };
    }

    async function requestNextTTSChunk() {
      if (ttsSynthesizing) {
        return;
      }
      const nextItem = ttsQueue.shift();
      if (!nextItem) {
        if (streamFinished && speechPipelineIsIdle()) {
          setChatState("idle");
        }
        return;
      }
      const runId = nextItem.runId;
      const abortController = new AbortController();
      ttsAbortController = abortController;
      ttsSynthesizing = true;
      setChatState("tts");
      try {
        const prepared = await requestTTSChunk(nextItem, abortController);
        if (runId === speechRunId) {
          preparedAudioQueue.push(prepared);
          playNextTTSChunk().catch(() => {});
        } else {
          (runtimeWindow.URL || globalThis.URL).revokeObjectURL(prepared.blobUrl);
        }
      } catch (err) {
        if (err?.name !== "AbortError" && runId === speechRunId) {
          const message = String(err?.message || ttsFailureMessage(err));
          appendMessage("error", message);
          showError(message);
          ttsQueue = [];
          discardPreparedAudio();
          stopLipSyncLoop();
        }
      } finally {
        if (ttsAbortController === abortController) {
          ttsAbortController = null;
        }
        ttsSynthesizing = false;
        requestNextTTSChunk();
      }
    }

    async function playNextTTSChunk() {
      if (ttsPlaying) {
        return;
      }
      const nextItem = preparedAudioQueue.shift();
      if (!nextItem) {
        if (streamFinished && speechPipelineIsIdle()) {
          setChatState("idle");
        }
        return;
      }
      const urlApi = runtimeWindow.URL || globalThis.URL;
      const nextExpr = String(nextItem.expr || "").trim();
      const AudioCtor = runtimeWindow.Audio || globalThis.Audio;
      const audio = new AudioCtor(nextItem.blobUrl);
      activeAudio = audio;
      audio.preload = "auto";
      ttsPlaying = true;
      if (state.chat.expression_mode && nextExpr) {
        triggerExpressionSafe(nextExpr);
      }
      try {
        await new Promise((resolve, reject) => {
          let settled = false;
          const settle = (error = null) => {
          if (settled) {
            return;
          }
          settled = true;
          if (activePlaybackSettler === stopPlayback) {
            activePlaybackSettler = null;
          }
          stopLipSyncLoop();
            urlApi.revokeObjectURL(nextItem.blobUrl);
            if (error) {
              reject(error);
            } else {
              resolve();
            }
          };
          const stopPlayback = () => settle();
          activePlaybackSettler = stopPlayback;
          audio.addEventListener("ended", () => settle(), { once: true });
          audio.addEventListener("error", () => settle(new Error("音频播放失败")), { once: true });
          setChatState("speaking");
          audio.play()
            .then(() => {
              if (activeAudio === audio && !audio.paused) {
                startLipSync(audio);
              }
            })
            .catch(settle);
        });
      } catch (err) {
        const message = ttsFailureMessage(err?.message || err);
        appendMessage("error", message);
        showError(message);
        ttsQueue = [];
        discardPreparedAudio();
      } finally {
        if (activeAudio === audio) {
          activeAudio = null;
        }
        ttsPlaying = false;
      }
      await playNextTTSChunk();
    }

    return {
      stopSpeaking,
      beginStream,
      markStreamFinished,
      isPlaying,
      queueLength,
      hasPendingSpeakBuffer,
      setPendingSpeakBuffer,
      setMouthOpen,
      resolveExpressionName,
      triggerExpressionSafe,
      stopLipSyncLoop,
      startLipSync,
      fallbackSpeakByBrowser,
      enqueueTTSChunk,
      feedSpeakBuffer,
      playNextTTSChunk,
    };
  }

  window.IpetSpeech = {
    createSpeechController,
  };
})();
