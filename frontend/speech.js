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
    let activeStreamPlayback = null;
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
      activeStreamPlayback?.stop();
      activeStreamPlayback = null;
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
      activeStreamPlayback?.stop();
      activeStreamPlayback = null;
      primeQwenStreamAudio();
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

    function usesQwenTTSStream() {
      return state.chat?.tts_provider === "qwen_tts_local";
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

    function primeQwenStreamAudio() {
      if (!usesQwenTTSStream()) {
        return;
      }
      try {
        const AudioContextCtor = runtimeWindow.AudioContext || runtimeWindow.webkitAudioContext;
        if (!AudioContextCtor) {
          return;
        }
        let nextAudioContext = currentAudioContext();
        if (!nextAudioContext) {
          nextAudioContext = new AudioContextCtor();
          saveAudioContext(nextAudioContext);
        }
        if (nextAudioContext.state === "suspended") {
          nextAudioContext.resume?.().catch(() => {});
        }
      } catch (_) {
        // The streaming request reports a single TTS error if Web Audio is unavailable.
      }
    }

    function decodePCM16LE(base64PCM) {
      const decodeBase64 = runtimeWindow.atob || globalThis.atob;
      if (typeof decodeBase64 !== "function") {
        throw new Error("浏览器不支持 PCM 流式音频");
      }
      const raw = decodeBase64(String(base64PCM || ""));
      if (raw.length % 2) {
        throw new Error("PCM 数据长度异常");
      }
      const samples = new Float32Array(raw.length / 2);
      for (let i = 0; i < raw.length; i += 2) {
        let value = raw.charCodeAt(i) | (raw.charCodeAt(i + 1) << 8);
        if (value >= 0x8000) {
          value -= 0x10000;
        }
        samples[i / 2] = value / 32768;
      }
      return samples;
    }

    function startQwenStreamLipSync(playback) {
      const data = new Uint8Array(playback.analyser.fftSize);
      const tick = () => {
        if (activeStreamPlayback !== playback || playback.stopped) {
          stopLipSyncLoop();
          return;
        }
        playback.analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) {
          const n = (data[i] - 128) / 128;
          sum += n * n;
        }
        const rms = Math.sqrt(sum / data.length);
        const lipSyncGain = Math.max(0.5, Number(state.chat?.lip_sync_gain || 1));
        setMouthOpen(clamp(rms * (4.8 + lipSyncGain * 1.8), 0, 1));
        lipsyncRAF = requestAnimationFrameSafe(tick);
      };
      tick();
    }

    async function createQwenStreamPlayback(item, runId) {
      primeQwenStreamAudio();
      const context = currentAudioContext();
      if (!context || typeof context.createBuffer !== "function") {
        throw new Error("浏览器不支持 PCM 流式音频");
      }
      if (context.state === "suspended") {
        await context.resume?.();
      }
      if (mediaSource) {
        try {
          mediaSource.disconnect();
        } catch (_) {}
        mediaSource = null;
      }

      const streamAnalyser = context.createAnalyser();
      streamAnalyser.fftSize = 2048;
      streamAnalyser.connect(context.destination);
      analyser = streamAnalyser;

      const pending = [];
      const sources = new Set();
      let pendingDuration = 0;
      let nextStartTime = 0;
      let started = false;
      let ended = false;
      let completed = false;
      let resolvePlayback = () => {};
      const waitForPlayback = new Promise((resolve) => {
        resolvePlayback = resolve;
      });

      const playback = {
        analyser: streamAnalyser,
        stopped: false,
        enqueue(samples, sampleRate) {
          if (playback.stopped || !samples?.length) {
            return;
          }
          const chunk = { samples, sampleRate };
          if (!started) {
            pending.push(chunk);
            pendingDuration += samples.length / sampleRate;
            // Qwen currently generates slower than playback.  This small lead
            // avoids an audible gap right after the first PCM packet.
            if (pendingDuration >= 0.96) {
              startPlayback();
            }
            return;
          }
          scheduleChunk(chunk);
        },
        finish() {
          ended = true;
          if (!started && pending.length) {
            startPlayback();
          }
          if (!started && !sources.size) {
            completePlayback();
          }
        },
        stop() {
          if (playback.stopped) {
            return;
          }
          playback.stopped = true;
          ended = true;
          pending.length = 0;
          for (const source of sources) {
            try {
              source.stop();
            } catch (_) {}
          }
          sources.clear();
          completePlayback();
        },
        waitForPlayback,
      };

      function completePlayback() {
        if (completed) {
          return;
        }
        completed = true;
        try {
          streamAnalyser.disconnect();
        } catch (_) {}
        if (analyser === streamAnalyser) {
          analyser = null;
        }
        if (activeStreamPlayback === playback) {
          activeStreamPlayback = null;
          ttsPlaying = false;
          stopLipSyncLoop();
        }
        resolvePlayback();
      }

      function scheduleChunk(chunk) {
        const audioBuffer = context.createBuffer(1, chunk.samples.length, chunk.sampleRate);
        audioBuffer.copyToChannel(chunk.samples, 0);
        const source = context.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(streamAnalyser);
        const startAt = Math.max(nextStartTime, context.currentTime + 0.025);
        nextStartTime = startAt + audioBuffer.duration;
        sources.add(source);
        source.addEventListener("ended", () => {
          sources.delete(source);
          if (ended && !sources.size) {
            completePlayback();
          }
        }, { once: true });
        source.start(startAt);
      }

      function startPlayback() {
        if (started || playback.stopped || runId !== speechRunId) {
          return;
        }
        started = true;
        ttsPlaying = true;
        setChatState("speaking");
        if (state.chat.expression_mode && item.expr) {
          triggerExpressionSafe(item.expr);
        }
        startQwenStreamLipSync(playback);
        for (const chunk of pending.splice(0)) {
          scheduleChunk(chunk);
        }
        pendingDuration = 0;
      }

      return playback;
    }

    async function requestQwenStreamChunk(item, abortController) {
      const backend = String(state.chat?.backend_url || "").replace(/\/$/, "");
      if (!backend) {
        throw new Error(ttsFailureMessage("后端地址未配置"));
      }
      let reader = null;
      let playback = null;
      let sawAudio = false;
      let sampleRate = 24000;
      try {
        const resp = await fetchFn(`${backend}/api/tts/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: abortController.signal,
          body: JSON.stringify({
            text: item.text,
            voice: state.chat.voice || "zh-CN-XiaoxiaoNeural",
            rate: toRateString(state.chat.rate_pct),
            volume: "+0%",
            provider: "qwen_tts_local",
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
        if (!resp.body || typeof resp.body.getReader !== "function") {
          throw new Error(ttsFailureMessage("后端未返回流式音频"));
        }
        const TextDecoderCtor = runtimeWindow.TextDecoder || globalThis.TextDecoder;
        if (!TextDecoderCtor) {
          throw new Error(ttsFailureMessage("浏览器不支持流式响应"));
        }
        playback = await createQwenStreamPlayback(item, item.runId);
        activeStreamPlayback = playback;
        reader = resp.body.getReader();
        const decoder = new TextDecoderCtor();
        let pendingText = "";
        const consumeLine = (rawLine) => {
          const line = String(rawLine || "").trim();
          if (!line) {
            return;
          }
          const packet = JSON.parse(line);
          if (packet.type === "meta" && Number(packet.sample_rate) > 0) {
            sampleRate = Number(packet.sample_rate);
          } else if (packet.type === "audio") {
            const samples = decodePCM16LE(packet.pcm_s16le_base64);
            if (samples.length) {
              sawAudio = true;
              playback.enqueue(samples, sampleRate);
            }
          } else if (packet.type === "error") {
            throw new Error(ttsFailureMessage(packet.detail || "本地模型生成失败"));
          }
        };
        while (true) {
          const { value, done } = await reader.read();
          if (done) {
            break;
          }
          pendingText += decoder.decode(value, { stream: true });
          let splitAt = pendingText.indexOf("\n");
          while (splitAt >= 0) {
            consumeLine(pendingText.slice(0, splitAt));
            pendingText = pendingText.slice(splitAt + 1);
            splitAt = pendingText.indexOf("\n");
          }
        }
        pendingText += decoder.decode();
        consumeLine(pendingText);
        if (!sawAudio) {
          throw new Error(ttsFailureMessage("本地模型未返回音频"));
        }
        if (item.runId !== speechRunId) {
          playback.stop();
          return;
        }
        playback.finish();
        await playback.waitForPlayback;
      } catch (error) {
        playback?.stop();
        throw error;
      } finally {
        reader?.releaseLock?.();
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
        if (usesQwenTTSStream()) {
          // The model has one inference lock, so keep this awaited: it avoids
          // competing sentences and preserves their spoken order.
          await requestQwenStreamChunk(nextItem, abortController);
          return;
        }
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
