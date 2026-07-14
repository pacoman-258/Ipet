(() => {
  const GOOGLE_AISTUDIO_PROVIDER = "google_aistudio";
  const GOOGLE_AISTUDIO_DEFAULT_MODEL = "gemini-3.5-flash";
  const CODEX_PROVIDER = "codex";

  function createSettingsFormController(deps = {}) {
    const {
      els = {},
      getSettingsPayload = () => null,
      renderSummary,
      renderDiagnostics,
    } = deps;

    function clone(value) {
      return JSON.parse(JSON.stringify(value || {}));
    }

    function numberValue(input, fallback) {
      const value = Number(input?.value);
      return Number.isFinite(value) ? value : fallback;
    }

    function intValue(input, fallback) {
      const value = Number.parseInt(input?.value, 10);
      return Number.isFinite(value) ? value : fallback;
    }

    function stringValue(input, fallback = "") {
      const value = String(input?.value ?? "").trim();
      return value || fallback;
    }

    function linesValue(input) {
      return String(input?.value || "")
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
    }

    function textFromLines(lines) {
      return Array.isArray(lines) ? lines.filter(Boolean).join("\n") : "";
    }

    function neoDefaults() {
      return {
        brain: {
          provider: "openai_compatible",
          model_endpoint: "",
          model_name: "gpt-5.4",
          max_output_tokens: 1024,
          persona_prompt_file: "",
          persona: "",
          self_state: "等待用户目标，并在 act / remember / learn_skill 前请求批准。",
          response_style: "",
          decision_temperature: 0.4,
          reasoning_effort: "",
          streaming_enabled: false,
          web_search_enabled: false,
        },
        human_ops: {
          playwright_profile: "",
          observe_screen: true,
          accessibility: true,
          require_act_review: true,
          require_memory_review: true,
          require_skill_review: true,
          clipboard_write_review: true,
          click_preview: { x: 160, y: 54, label: "目标位置", size: 16 },
          observe_model: {
            enabled: false,
            provider: "openai_compatible",
            model_endpoint: "",
            model_name: "",
            max_output_tokens: 512,
            timeout_sec: 90,
          },
        },
        memory: {
          conversation_saving: true,
          long_term_enabled: true,
          preferences_enabled: true,
          relationship_enabled: true,
          retention_days: 365,
          review_limit: 20,
          review_queue: [],
        },
        skills: {
          recipes_enabled: true,
          auto_propose: true,
          review_required: true,
          recipes: [],
          proposal_queue: [],
        },
      };
    }

    function mergedNeo(config) {
      const defaults = neoDefaults();
      return {
        brain: { ...defaults.brain, ...(config.brain || {}) },
        human_ops: {
          ...defaults.human_ops,
          ...(config.human_ops || {}),
          click_preview: {
            ...defaults.human_ops.click_preview,
            ...((config.human_ops || {}).click_preview || {}),
          },
          observe_model: {
            ...defaults.human_ops.observe_model,
            ...((config.human_ops || {}).observe_model || {}),
          },
        },
        memory: { ...defaults.memory, ...(config.memory || {}) },
        skills: { ...defaults.skills, ...(config.skills || {}) },
      };
    }

    function fallbackSettings() {
      return {
        static_preview: true,
        config: {
          model_path: "",
          chat: {
            voice: "zh-CN-XiaoxiaoNeural",
            tts_provider: "edge_tts",
            rate_pct: 0,
            model: "gpt-5.4",
            backend_url: "",
            system_prompt: "",
            asr: {
              enabled: true,
              provider: "funasr",
              api_base_url: "http://127.0.0.1:8012",
              provider_url: "https://api.groq.com/openai/v1/audio/transcriptions",
              model: "whisper-large-v3-turbo",
              push_to_talk_key: "Alt",
              interim_results: true,
            },
          },
          pet: {
            scale: 0.3,
            opacity: 1,
            offset_x: 0,
            offset_y: 40,
            rotation: 0,
            background_image: "",
            edit_mode: false,
            follow_mouse: true,
            background_enabled: false,
          },
          window: { x: 120, y: 80, width: 420, height: 640, locked: false },
          ...neoDefaults(),
        },
      };
    }

    function setChecked(input, value) {
      if (input) {
        input.checked = !!value;
      }
    }

    function setValue(input, value) {
      if (input) {
        input.value = value ?? "";
      }
    }

    function isGoogleAistudio(value) {
      return String(value || "") === GOOGLE_AISTUDIO_PROVIDER;
    }

    function isCodex(value) {
      return String(value || "") === CODEX_PROVIDER;
    }

    function isEndpointlessProvider(value) {
      return isGoogleAistudio(value) || isCodex(value);
    }

    function setSecretPlaceholder(input, preview, label) {
      if (!input) {
        return;
      }
      const safeLabel = label || "API Key";
      input.placeholder = preview ? `已保存 ${preview}；留空表示保留` : `填写 ${safeLabel}`;
    }

    function syncProviderControls() {
      const brainGoogle = isGoogleAistudio(stringValue(els.brainProvider, "openai_compatible"));
      const brainCodex = isCodex(stringValue(els.brainProvider, "openai_compatible"));
      if (els.brainModelEndpoint) {
        els.brainModelEndpoint.disabled = brainGoogle || brainCodex;
        els.brainModelEndpoint.placeholder = brainGoogle
          ? "Google AI Studio 固定使用 Gemini API，可留空"
          : brainCodex
            ? "使用本机 Codex 登录，无需端点"
            : "http://127.0.0.1:11434";
        if (brainGoogle || brainCodex) {
          els.brainModelEndpoint.value = "";
        }
      }
      if (els.brainModelName) {
        const currentModel = els.brainModelName.value.trim();
        if (brainGoogle && (!currentModel || currentModel === "gpt-5.4" || currentModel === "default")) {
          els.brainModelName.value = GOOGLE_AISTUDIO_DEFAULT_MODEL;
        } else if (brainCodex && (!currentModel || currentModel === GOOGLE_AISTUDIO_DEFAULT_MODEL)) {
          els.brainModelName.value = "default";
        } else if (!brainGoogle && !brainCodex && currentModel === "default") {
          els.brainModelName.value = "gpt-5.4";
        }
      }
      if (els.brainApiKey) {
        els.brainApiKey.disabled = brainCodex;
        if (brainCodex) {
          els.brainApiKey.value = "";
          els.brainApiKey.placeholder = "复用本机 Codex 登录，无需 API Key";
        } else {
          setSecretPlaceholder(
            els.brainApiKey,
            getSettingsPayload()?.config?.brain?.api_key_preview,
            "API Key",
          );
        }
      }
      if (els.brainApiKeyClear) {
        els.brainApiKeyClear.disabled = brainCodex;
        if (brainCodex) {
          els.brainApiKeyClear.checked = false;
        }
      }
      if (els.brainReasoningEffortField) {
        els.brainReasoningEffortField.hidden = !brainCodex;
      }
      if (els.brainReasoningEffort) {
        els.brainReasoningEffort.disabled = !brainCodex;
      }
      if (els.brainModelStatus) {
        els.brainModelStatus.textContent = brainGoogle
          ? "Google AI Studio 只需要 API Key"
          : brainCodex
            ? "复用本机 Codex 登录与账户额度；点击拉取模型可检测登录"
            : "填写端点后可拉取模型列表";
      }

      const observeGoogle = isGoogleAistudio(stringValue(els.opsObserveModelProvider, "openai_compatible"));
      const observeCodex = isCodex(stringValue(els.opsObserveModelProvider, "openai_compatible"));
      if (els.opsObserveModelEndpoint) {
        els.opsObserveModelEndpoint.disabled = observeGoogle || observeCodex;
        els.opsObserveModelEndpoint.placeholder = observeGoogle
          ? "Google AI Studio 固定使用 Gemini API，可留空"
          : observeCodex
            ? "Codex 复用本机登录，无需端点"
          : "http://127.0.0.1:11434";
        if (observeGoogle || observeCodex) {
          els.opsObserveModelEndpoint.value = "";
        }
      }
      if (observeGoogle && els.opsObserveModelName && !els.opsObserveModelName.value.trim()) {
        els.opsObserveModelName.value = GOOGLE_AISTUDIO_DEFAULT_MODEL;
      } else if (observeCodex && els.opsObserveModelName && !els.opsObserveModelName.value.trim()) {
        els.opsObserveModelName.value = "default";
      }
      if (els.opsObserveApiKey) {
        els.opsObserveApiKey.disabled = observeCodex;
        if (observeCodex) {
          els.opsObserveApiKey.value = "";
        }
        els.opsObserveApiKey.placeholder = observeCodex
          ? "复用本机 Codex 登录，无需 API Key"
          : "留空表示保留已保存密钥";
      }
      if (els.opsObserveApiKeyClear) {
        els.opsObserveApiKeyClear.disabled = observeCodex;
        if (observeCodex) {
          els.opsObserveApiKeyClear.checked = false;
        }
      }
      if (els.opsObserveModelStatus) {
        els.opsObserveModelStatus.textContent = observeGoogle
          ? "Google AI Studio observe 只需要 API Key"
          : observeCodex
            ? "Codex observe 复用本机登录与账户额度"
          : "可单独配置更快的观察模型";
      }

      const asrProvider = stringValue(els.chatAsrProvider, "funasr");
      const asrOnline = asrProvider === "groq";
      if (els.chatAsrProviderUrl) {
        els.chatAsrProviderUrl.disabled = !asrOnline;
        els.chatAsrProviderUrl.placeholder = asrOnline
          ? "https://api.groq.com/openai/v1/audio/transcriptions"
          : "本地 FunASR 不需要在线接口";
      }
      if (els.chatAsrModel) {
        els.chatAsrModel.disabled = !asrOnline;
        els.chatAsrModel.placeholder = asrOnline ? "whisper-large-v3-turbo" : "本地 FunASR 使用内置模型";
      }
      if (els.chatAsrApiKey) {
        els.chatAsrApiKey.disabled = !asrOnline;
        if (!asrOnline) {
          els.chatAsrApiKey.value = "";
          els.chatAsrApiKey.placeholder = "本地 FunASR 不需要 API Key";
        } else {
          setSecretPlaceholder(
            els.chatAsrApiKey,
            getSettingsPayload()?.config?.chat?.asr?.api_key_preview,
            "Groq API Key",
          );
        }
      }
      if (els.chatAsrApiKeyClear) {
        els.chatAsrApiKeyClear.disabled = !asrOnline;
        if (!asrOnline) {
          els.chatAsrApiKeyClear.checked = false;
        }
      }
    }

    function updateClickPreview() {
      const x = intValue(els.clickPreviewX, 160);
      const y = intValue(els.clickPreviewY, 54);
      const size = Math.max(8, Math.min(36, intValue(els.clickPreviewSize, 16)));
      const label = stringValue(els.clickPreviewLabel, "目标位置");
      if (els.clickPreviewDot) {
        els.clickPreviewDot.style.left = `${Math.max(0, Math.min(100, x / 3.2))}%`;
        els.clickPreviewDot.style.top = `${Math.max(0, Math.min(100, y / 1.92))}%`;
        els.clickPreviewDot.style.width = `${size}px`;
        els.clickPreviewDot.style.height = `${size}px`;
      }
      if (els.clickPreviewCaption) {
        els.clickPreviewCaption.textContent = `我想点击这里：${label}`;
      }
    }

    function populateForm(config) {
      const source = config || {};
      const neo = mergedNeo(source);
      const chat = source.chat || {};
      const asr = chat.asr || {};
      const pet = source.pet || {};
      const win = source.window || {};

      setValue(els.modelPath, source.model_path || "");
      setValue(els.chatVoice, chat.voice || "zh-CN-XiaoxiaoNeural");
      setValue(els.chatTtsProvider, chat.tts_provider || "edge_tts");
      setValue(els.chatRatePct, Number(chat.rate_pct || 0));
      setChecked(els.chatAsrEnabled, asr.enabled !== false);
      setValue(els.chatAsrPushToTalkKey, asr.push_to_talk_key || "Alt");
      setValue(els.chatAsrProvider, asr.provider || "funasr");
      setValue(els.chatAsrProviderUrl, asr.provider_url || "https://api.groq.com/openai/v1/audio/transcriptions");
      setValue(els.chatAsrModel, asr.model || "whisper-large-v3-turbo");
      setValue(els.chatAsrApiKey, "");
      setSecretPlaceholder(els.chatAsrApiKey, asr.api_key_preview, "Groq API Key");
      setChecked(els.chatAsrApiKeyClear, false);

      setValue(els.petScale, Number(pet.scale ?? 0.3));
      setValue(els.petOpacity, Number(pet.opacity ?? 1));
      setValue(els.petOffsetX, Number(pet.offset_x || 0));
      setValue(els.petOffsetY, Number(pet.offset_y ?? 40));
      setValue(els.petRotation, Number(pet.rotation || 0));
      setValue(els.petBackgroundImage, pet.background_image || "");
      setChecked(els.petEditMode, pet.edit_mode);
      setChecked(els.petFollowMouse, pet.follow_mouse !== false);
      setChecked(els.petBackgroundEnabled, pet.background_enabled);

      setValue(els.windowX, Number(win.x || 0));
      setValue(els.windowY, Number(win.y || 0));
      setValue(els.windowWidth, Number(win.width || 420));
      setValue(els.windowHeight, Number(win.height || 640));
      setChecked(els.windowLocked, win.locked);

      setValue(els.brainProvider, neo.brain.provider || "openai_compatible");
      setValue(els.brainModelEndpoint, neo.brain.model_endpoint || "");
      setValue(els.brainModelName, neo.brain.model_name || chat.model || "gpt-5.4");
      setValue(els.brainApiKey, "");
      setSecretPlaceholder(els.brainApiKey, neo.brain.api_key_preview, "API Key");
      setChecked(els.brainApiKeyClear, false);
      setValue(els.brainPersonaPromptFile, neo.brain.persona_prompt_file || "");
      setValue(els.brainSelfState, neo.brain.self_state || "");
      setValue(els.brainDecisionTemperature, Number(neo.brain.decision_temperature ?? 0.4));
      setValue(els.brainReasoningEffort, neo.brain.reasoning_effort || "");
      setChecked(els.brainStreamingEnabled, neo.brain.streaming_enabled === true);
      setChecked(els.brainWebSearchEnabled, neo.brain.web_search_enabled === true);

      setValue(els.opsPlaywrightProfile, neo.human_ops.playwright_profile || "");
      setChecked(els.opsObserveScreen, neo.human_ops.observe_screen);
      setChecked(els.opsAccessibility, neo.human_ops.accessibility);
      setChecked(els.opsRequireActReview, neo.human_ops.require_act_review);
      setChecked(els.opsRequireMemoryReview, neo.human_ops.require_memory_review);
      setChecked(els.opsRequireSkillReview, neo.human_ops.require_skill_review);
      setChecked(els.opsClipboardReview, neo.human_ops.clipboard_write_review);
      setChecked(els.opsObserveModelEnabled, neo.human_ops.observe_model.enabled);
      setValue(els.opsObserveModelProvider, neo.human_ops.observe_model.provider || "openai_compatible");
      setValue(els.opsObserveModelEndpoint, neo.human_ops.observe_model.model_endpoint || "");
      setValue(els.opsObserveModelName, neo.human_ops.observe_model.model_name || "");
      setValue(els.opsObserveApiKey, "");
      setSecretPlaceholder(els.opsObserveApiKey, neo.human_ops.observe_model.api_key_preview, "observe API Key");
      setChecked(els.opsObserveApiKeyClear, false);
      setValue(els.clickPreviewX, Number(neo.human_ops.click_preview.x || 160));
      setValue(els.clickPreviewY, Number(neo.human_ops.click_preview.y || 54));
      setValue(els.clickPreviewLabel, neo.human_ops.click_preview.label || "目标位置");
      setValue(els.clickPreviewSize, Number(neo.human_ops.click_preview.size || 16));

      setChecked(els.memoryConversationSaving, neo.memory.conversation_saving);
      setChecked(els.memoryLongTermEnabled, neo.memory.long_term_enabled);
      setChecked(els.memoryPreferencesEnabled, neo.memory.preferences_enabled);
      setChecked(els.memoryRelationshipEnabled, neo.memory.relationship_enabled);
      setValue(els.memoryRetentionDays, Number(neo.memory.retention_days || 365));
      setValue(els.memoryReviewLimit, Number(neo.memory.review_limit || 20));
      setValue(els.memoryReviewQueue, textFromLines(neo.memory.review_queue));

      setChecked(els.skillsRecipesEnabled, neo.skills.recipes_enabled);
      setChecked(els.skillsAutoPropose, neo.skills.auto_propose);
      setChecked(els.skillsReviewRequired, neo.skills.review_required);
      setValue(els.skillsRecipeList, textFromLines(neo.skills.recipes));
      setValue(els.skillsProposalQueue, textFromLines(neo.skills.proposal_queue));

      updateClickPreview();
      syncProviderControls();
      if (typeof renderSummary === "function") {
        renderSummary(source);
      }
      if (typeof renderDiagnostics === "function") {
        renderDiagnostics(source);
      }
    }

    function readForm() {
      const next = clone(getSettingsPayload()?.config || {});
      next.model_path = stringValue(els.modelPath);
      next.chat = { ...(next.chat || {}) };
      next.chat.voice = stringValue(els.chatVoice, "zh-CN-XiaoxiaoNeural");
      next.chat.tts_provider = stringValue(els.chatTtsProvider, "edge_tts");
      next.chat.rate_pct = intValue(els.chatRatePct, 0);
      next.chat.model = stringValue(els.brainModelName, "gpt-5.4");
      next.chat.asr = {
        ...(next.chat.asr || {}),
        enabled: !!els.chatAsrEnabled?.checked,
        provider: stringValue(els.chatAsrProvider, "funasr"),
        provider_url: stringValue(
          els.chatAsrProviderUrl,
          "https://api.groq.com/openai/v1/audio/transcriptions",
        ),
        model: stringValue(els.chatAsrModel, "whisper-large-v3-turbo"),
        push_to_talk_key: stringValue(els.chatAsrPushToTalkKey, "Alt"),
      };
      if (els.chatAsrApiKey?.value) {
        next.chat.asr.api_key = els.chatAsrApiKey.value;
      }
      next.chat.asr.api_key_clear = !!els.chatAsrApiKeyClear?.checked;

      next.pet = {
        ...(next.pet || {}),
        scale: numberValue(els.petScale, 0.3),
        opacity: numberValue(els.petOpacity, 1),
        offset_x: intValue(els.petOffsetX, 0),
        offset_y: intValue(els.petOffsetY, 40),
        rotation: numberValue(els.petRotation, 0),
        background_image: stringValue(els.petBackgroundImage),
        edit_mode: !!els.petEditMode?.checked,
        follow_mouse: !!els.petFollowMouse?.checked,
        background_enabled: !!els.petBackgroundEnabled?.checked,
      };

      next.window = {
        ...(next.window || {}),
        x: intValue(els.windowX, 0),
        y: intValue(els.windowY, 0),
        width: intValue(els.windowWidth, 420),
        height: intValue(els.windowHeight, 640),
        locked: !!els.windowLocked?.checked,
      };

      const brainProvider = stringValue(els.brainProvider, "openai_compatible");
      next.brain = {
        ...(next.brain || {}),
        provider: brainProvider,
        model_endpoint: isEndpointlessProvider(brainProvider) ? "" : stringValue(els.brainModelEndpoint),
        model_name: stringValue(els.brainModelName, isGoogleAistudio(brainProvider) ? GOOGLE_AISTUDIO_DEFAULT_MODEL : isCodex(brainProvider) ? "default" : "gpt-5.4"),
        persona_prompt_file: stringValue(els.brainPersonaPromptFile),
        self_state: stringValue(els.brainSelfState),
        decision_temperature: numberValue(els.brainDecisionTemperature, 0.4),
        reasoning_effort: stringValue(els.brainReasoningEffort),
        streaming_enabled: !!els.brainStreamingEnabled?.checked,
        web_search_enabled: !!els.brainWebSearchEnabled?.checked,
      };
      if (els.brainApiKey?.value) {
        next.brain.api_key = els.brainApiKey.value;
      }
      next.brain.api_key_clear = !!els.brainApiKeyClear?.checked;

      const observeProvider = stringValue(els.opsObserveModelProvider, "openai_compatible");
      next.human_ops = {
        ...(next.human_ops || {}),
        playwright_profile: stringValue(els.opsPlaywrightProfile),
        observe_screen: !!els.opsObserveScreen?.checked,
        accessibility: !!els.opsAccessibility?.checked,
        require_act_review: !!els.opsRequireActReview?.checked,
        require_memory_review: !!els.opsRequireMemoryReview?.checked,
        require_skill_review: !!els.opsRequireSkillReview?.checked,
        clipboard_write_review: !!els.opsClipboardReview?.checked,
        observe_model: {
          ...((next.human_ops || {}).observe_model || {}),
          enabled: !!els.opsObserveModelEnabled?.checked,
          provider: observeProvider,
          model_endpoint: isEndpointlessProvider(observeProvider) ? "" : stringValue(els.opsObserveModelEndpoint),
          model_name: stringValue(els.opsObserveModelName, isGoogleAistudio(observeProvider) ? GOOGLE_AISTUDIO_DEFAULT_MODEL : isCodex(observeProvider) ? "default" : ""),
          max_output_tokens: 512,
          timeout_sec: Number((next.human_ops || {}).observe_model?.timeout_sec || 90),
        },
        click_preview: {
          x: intValue(els.clickPreviewX, 160),
          y: intValue(els.clickPreviewY, 54),
          label: stringValue(els.clickPreviewLabel, "目标位置"),
          size: intValue(els.clickPreviewSize, 16),
        },
      };
      if (els.opsObserveApiKey?.value) {
        next.human_ops.observe_model.api_key = els.opsObserveApiKey.value;
      }
      next.human_ops.observe_model.api_key_clear = !!els.opsObserveApiKeyClear?.checked;

      next.memory = {
        ...(next.memory || {}),
        conversation_saving: !!els.memoryConversationSaving?.checked,
        long_term_enabled: !!els.memoryLongTermEnabled?.checked,
        preferences_enabled: !!els.memoryPreferencesEnabled?.checked,
        relationship_enabled: !!els.memoryRelationshipEnabled?.checked,
        retention_days: intValue(els.memoryRetentionDays, 365),
        review_limit: intValue(els.memoryReviewLimit, 20),
        review_queue: linesValue(els.memoryReviewQueue),
      };

      next.skills = {
        ...(next.skills || {}),
        recipes_enabled: !!els.skillsRecipesEnabled?.checked,
        auto_propose: !!els.skillsAutoPropose?.checked,
        review_required: !!els.skillsReviewRequired?.checked,
        recipes: linesValue(els.skillsRecipeList),
        proposal_queue: linesValue(els.skillsProposalQueue),
      };

      return next;
    }

    return {
      clone,
      numberValue,
      intValue,
      stringValue,
      linesValue,
      textFromLines,
      neoDefaults,
      mergedNeo,
      fallbackSettings,
      populateForm,
      readForm,
      updateClickPreview,
      syncProviderControls,
      isGoogleAistudio,
      isCodex,
      isEndpointlessProvider,
    };
  }

  window.IpetSettingsForm = {
    ...(window.IpetSettingsForm || {}),
    createSettingsFormController,
  };
})();
