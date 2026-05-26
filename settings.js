(() => {
  const $ = (id) => document.getElementById(id);

  const els = {
    navbarTitle: $("settings-navbar-title"),
    reloadConfigBtn: $("reload-config-btn"),
    resetFormBtn: $("reset-form-btn"),
    saveConfigBtn: $("save-config-btn"),
    statusBanner: $("status-banner"),
    toast: $("toast"),
    summaryCards: $("summary-cards"),
    diagnosticCards: $("diagnostic-cards"),
    diagnosticLog: $("diagnostic-log"),
    modelPath: $("model-path"),
    chatVoice: $("chat-voice"),
    chatTtsProvider: $("chat-tts-provider"),
    chatRatePct: $("chat-rate-pct"),
    chatAsrEnabled: $("chat-asr-enabled"),
    chatAsrPushToTalkKey: $("chat-asr-push-to-talk-key"),
    petScale: $("pet-scale"),
    petOpacity: $("pet-opacity"),
    petOffsetX: $("pet-offset-x"),
    petOffsetY: $("pet-offset-y"),
    petRotation: $("pet-rotation"),
    petBackgroundImage: $("pet-background-image"),
    petEditMode: $("pet-edit-mode"),
    petFollowMouse: $("pet-follow-mouse"),
    petBackgroundEnabled: $("pet-background-enabled"),
    windowX: $("window-x"),
    windowY: $("window-y"),
    windowWidth: $("window-width"),
    windowHeight: $("window-height"),
    windowLocked: $("window-locked"),
    brainProvider: $("brain-provider"),
    brainModelEndpoint: $("brain-model-endpoint"),
    brainFetchModelsBtn: $("brain-fetch-models-btn"),
    brainModelStatus: $("brain-model-status"),
    brainModelList: $("brain-model-list"),
    brainModelName: $("brain-model-name"),
    brainApiKey: $("brain-api-key"),
    brainApiKeyClear: $("brain-api-key-clear"),
    brainPersona: $("brain-persona"),
    brainSelfState: $("brain-self-state"),
    brainResponseStyle: $("brain-response-style"),
    brainDecisionTemperature: $("brain-decision-temperature"),
    opsObserveScreen: $("ops-observe-screen"),
    opsAccessibility: $("ops-accessibility"),
    opsRequireActReview: $("ops-require-act-review"),
    opsRequireMemoryReview: $("ops-require-memory-review"),
    opsRequireSkillReview: $("ops-require-skill-review"),
    opsClipboardReview: $("ops-clipboard-review"),
    clickPreviewX: $("click-preview-x"),
    clickPreviewY: $("click-preview-y"),
    clickPreviewLabel: $("click-preview-label"),
    clickPreviewSize: $("click-preview-size"),
    clickPreviewDot: $("click-preview-dot"),
    clickPreviewCaption: $("click-preview-caption"),
    memoryConversationSaving: $("memory-conversation-saving"),
    memoryLongTermEnabled: $("memory-long-term-enabled"),
    memoryPreferencesEnabled: $("memory-preferences-enabled"),
    memoryRelationshipEnabled: $("memory-relationship-enabled"),
    memoryRetentionDays: $("memory-retention-days"),
    memoryReviewLimit: $("memory-review-limit"),
    memoryReviewQueue: $("memory-review-queue"),
    skillsRecipesEnabled: $("skills-recipes-enabled"),
    skillsAutoPropose: $("skills-auto-propose"),
    skillsReviewRequired: $("skills-review-required"),
    skillsRecipeList: $("skills-recipe-list"),
    skillsProposalQueue: $("skills-proposal-queue"),
  };

  let settingsPayload = null;
  let lastLoadedAt = "";

  function clone(value) {
    return JSON.parse(JSON.stringify(value || {}));
  }

  function setStatus(message) {
    if (els.statusBanner) {
      els.statusBanner.textContent = String(message || "");
    }
  }

  function showToast(message) {
    if (!els.toast) {
      return;
    }
    els.toast.textContent = String(message || "");
    els.toast.classList.add("show");
    window.setTimeout(() => els.toast.classList.remove("show"), 2400);
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const text = await response.text();
    const data = text ? JSON.parse(text) : {};
    if (!response.ok) {
      throw new Error(data.detail || data.message || response.statusText);
    }
    return data;
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
        persona: "你是 Ipet，一个有身体、有记忆、会一步一步操作电脑的陪伴助手。",
        self_state: "等待用户目标，并在 act / remember / learn_skill 前请求批准。",
        response_style: "lively",
        decision_temperature: 0.4,
      },
      human_ops: {
        observe_screen: true,
        accessibility: true,
        require_act_review: true,
        require_memory_review: true,
        require_skill_review: true,
        clipboard_write_review: true,
        click_preview: { x: 160, y: 54, label: "目标位置", size: 16 },
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
          system_prompt: neoDefaults().brain.persona,
          asr: { enabled: true, push_to_talk_key: "Alt" },
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

  function setActiveSection(sectionId) {
    const normalized = String(sectionId || "overview").replace(/^#/, "") || "overview";
    const title = document.querySelector(`[data-window-target="${normalized}"]`)?.dataset.title || "总览";
    document.querySelectorAll(".desktop-window, .dashboard-page").forEach((section) => {
      section.classList.toggle("active", section.id === normalized);
    });
    document.querySelectorAll("[data-window-target]").forEach((item) => {
      item.classList.toggle("active", item.dataset.windowTarget === normalized);
    });
    if (els.navbarTitle) {
      els.navbarTitle.textContent = title;
    }
  }

  function scrollToSection(sectionId) {
    const content = document.querySelector(".content-area");
    const target = document.getElementById(sectionId);
    if (!content || !target) {
      return;
    }
    const top = Math.max(0, target.offsetTop - content.offsetTop - 8);
    content.scrollTo({ top, behavior: "smooth" });
  }

  function renderSummary(config) {
    if (!els.summaryCards) {
      return;
    }
    const neo = mergedNeo(config || {});
    const cards = [
      ["Body", config?.model_path || "未选择形象"],
      ["Brain", `${providerLabel(neo.brain.provider)} · ${neo.brain.model_name || "未设置模型"}`],
      ["Human Ops", neo.human_ops.require_act_review ? "动作需批准" : "动作审批关闭"],
      ["Memory", neo.memory.long_term_enabled ? "长期记忆开启" : "长期记忆关闭"],
      ["Skills", neo.skills.review_required ? "保存前审阅" : "审阅关闭"],
      ["Diagnostics", lastLoadedAt || "等待加载"],
    ];
    els.summaryCards.innerHTML = cards
      .map(
        ([label, value], index) => `
          <div class="summary-card tone-${(index % 4) + 1}">
            <span class="stat-icon"></span>
            <strong class="stat-number">${escapeHtml(value)}</strong>
            <span class="stat-label">${escapeHtml(label)}</span>
          </div>
        `
      )
      .join("");
  }

  function renderDiagnostics(config) {
    if (!els.diagnosticCards || !els.diagnosticLog) {
      return;
    }
    const neo = mergedNeo(config || {});
    const diagnostics = [
      ["本体设置", "已加载"],
      ["最近观察", neo.human_ops.observe_screen ? "允许看屏幕" : "观察关闭"],
      ["最近批准动作", "等待 human-op 提案"],
      ["最近失败动作", "暂无记录"],
    ];
    els.diagnosticCards.innerHTML = diagnostics
      .map(
        ([label, value], index) => `
          <div class="summary-card tone-${(index % 4) + 1}">
            <span class="stat-icon"></span>
            <strong class="stat-number">${escapeHtml(value)}</strong>
            <span class="stat-label">${escapeHtml(label)}</span>
          </div>
        `
      )
      .join("");
    els.diagnosticLog.innerHTML = `
      <div class="review-item"><strong>say</strong><span>自由执行</span></div>
      <div class="review-item"><strong>observe</strong><span>自由执行</span></div>
      <div class="review-item"><strong>act</strong><span>执行前显示红点、文本或路径预览</span></div>
      <div class="review-item"><strong>remember / learn_skill</strong><span>写入前进入审阅队列</span></div>
    `;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function providerLabel(value) {
    const provider = String(value || "openai_compatible");
    if (provider === "ollama") {
      return "Ollama";
    }
    if (provider === "anthropic_compatible") {
      return "Anthropic";
    }
    return "OpenAI";
  }

  function setBrainModelStatus(message) {
    if (els.brainModelStatus) {
      els.brainModelStatus.textContent = String(message || "");
    }
  }

  function renderBrainModelOptions(models) {
    if (!els.brainModelList) {
      return;
    }
    els.brainModelList.innerHTML = "";
    const items = Array.isArray(models) ? models : [];
    if (!items.length) {
      setBrainModelStatus("没有发现可填入的模型");
      return;
    }
    items.forEach((item) => {
      const model = {
        id: String(item?.id || item?.name || item || "").trim(),
        label: String(item?.label || item?.id || item?.name || item || "").trim(),
      };
      if (!model.id) {
        return;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "model-option-button";
      button.textContent = model.label || model.id;
      button.title = `填入 ${model.id}`;
      button.addEventListener("click", () => {
        els.brainModelName.value = model.id;
        setBrainModelStatus(`已填入 ${model.id}`);
        renderSummary(readForm());
      });
      els.brainModelList.appendChild(button);
    });
    setBrainModelStatus(`发现 ${els.brainModelList.children.length} 个模型，点击即可填入`);
  }

  async function fetchBrainModels() {
    const endpoint = stringValue(els.brainModelEndpoint);
    if (!endpoint) {
      setBrainModelStatus("请先填写模型端点");
      return;
    }
    if (settingsPayload?.static_preview) {
      setBrainModelStatus("静态预览无法连接后端拉取模型");
      return;
    }
    if (els.brainFetchModelsBtn) {
      els.brainFetchModelsBtn.disabled = true;
    }
    setBrainModelStatus("正在拉取模型...");
    try {
      const payload = {
        provider: stringValue(els.brainProvider, "openai_compatible"),
        model_endpoint: endpoint,
      };
      if (els.brainApiKey?.value) {
        payload.api_key = els.brainApiKey.value;
      }
      const result = await fetchJson("/api/brain/models", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      renderBrainModelOptions(result.models || []);
      showToast("模型列表已更新");
    } catch (error) {
      setBrainModelStatus(`拉取失败：${error.message || String(error)}`);
    } finally {
      if (els.brainFetchModelsBtn) {
        els.brainFetchModelsBtn.disabled = false;
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
    setChecked(els.brainApiKeyClear, false);
    setValue(els.brainPersona, neo.brain.persona || chat.system_prompt || "");
    setValue(els.brainSelfState, neo.brain.self_state || "");
    setValue(els.brainResponseStyle, neo.brain.response_style || "lively");
    setValue(els.brainDecisionTemperature, Number(neo.brain.decision_temperature ?? 0.4));

    setChecked(els.opsObserveScreen, neo.human_ops.observe_screen);
    setChecked(els.opsAccessibility, neo.human_ops.accessibility);
    setChecked(els.opsRequireActReview, neo.human_ops.require_act_review);
    setChecked(els.opsRequireMemoryReview, neo.human_ops.require_memory_review);
    setChecked(els.opsRequireSkillReview, neo.human_ops.require_skill_review);
    setChecked(els.opsClipboardReview, neo.human_ops.clipboard_write_review);
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
    renderSummary(source);
    renderDiagnostics(source);
  }

  function readForm() {
    const next = clone(settingsPayload?.config || {});
    next.model_path = stringValue(els.modelPath);
    next.chat = { ...(next.chat || {}) };
    next.chat.voice = stringValue(els.chatVoice, "zh-CN-XiaoxiaoNeural");
    next.chat.tts_provider = stringValue(els.chatTtsProvider, "edge_tts");
    next.chat.rate_pct = intValue(els.chatRatePct, 0);
    next.chat.model = stringValue(els.brainModelName, "gpt-5.4");
    next.chat.system_prompt = stringValue(els.brainPersona);
    next.chat.asr = {
      ...(next.chat.asr || {}),
      enabled: !!els.chatAsrEnabled?.checked,
      push_to_talk_key: stringValue(els.chatAsrPushToTalkKey, "Alt"),
    };

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

    next.brain = {
      ...(next.brain || {}),
      provider: stringValue(els.brainProvider, "openai_compatible"),
      model_endpoint: stringValue(els.brainModelEndpoint),
      model_name: stringValue(els.brainModelName, "gpt-5.4"),
      persona: stringValue(els.brainPersona),
      self_state: stringValue(els.brainSelfState),
      response_style: stringValue(els.brainResponseStyle, "lively"),
      decision_temperature: numberValue(els.brainDecisionTemperature, 0.4),
    };
    if (els.brainApiKey?.value) {
      next.brain.api_key = els.brainApiKey.value;
    }
    next.brain.api_key_clear = !!els.brainApiKeyClear?.checked;

    next.human_ops = {
      ...(next.human_ops || {}),
      observe_screen: !!els.opsObserveScreen?.checked,
      accessibility: !!els.opsAccessibility?.checked,
      require_act_review: !!els.opsRequireActReview?.checked,
      require_memory_review: !!els.opsRequireMemoryReview?.checked,
      require_skill_review: !!els.opsRequireSkillReview?.checked,
      clipboard_write_review: !!els.opsClipboardReview?.checked,
      click_preview: {
        x: intValue(els.clickPreviewX, 160),
        y: intValue(els.clickPreviewY, 54),
        label: stringValue(els.clickPreviewLabel, "目标位置"),
        size: intValue(els.clickPreviewSize, 16),
      },
    };

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

  async function loadSettings() {
    setStatus("正在加载 Ipet 设置...");
    try {
      settingsPayload = await fetchJson("/api/settings/config");
    } catch (error) {
      settingsPayload = fallbackSettings();
      setStatus("当前是静态预览，已载入默认设置。");
    }
    lastLoadedAt = new Date().toLocaleTimeString();
    populateForm(settingsPayload.config || {});
    if (!settingsPayload.static_preview) {
      setStatus("设置已加载。");
    }
  }

  async function saveSettings() {
    if (settingsPayload?.static_preview) {
      settingsPayload = { ...fallbackSettings(), config: readForm() };
      lastLoadedAt = new Date().toLocaleTimeString();
      populateForm(settingsPayload.config || {});
      setStatus("静态预览不会写入磁盘；在 Ipet 后端中打开后可保存。");
      showToast("预览已更新");
      return;
    }
    setStatus("正在保存设置...");
    settingsPayload = await fetchJson("/api/settings/config", {
      method: "PUT",
      body: JSON.stringify({ config: readForm() }),
    });
    lastLoadedAt = new Date().toLocaleTimeString();
    populateForm(settingsPayload.config || {});
    setStatus("设置已保存，Ipet 会按新配置刷新。");
    showToast("已保存");
  }

  function resetForm() {
    const current = clone(settingsPayload?.config || {});
    const defaults = neoDefaults();
    current.brain = defaults.brain;
    current.human_ops = defaults.human_ops;
    current.memory = defaults.memory;
    current.skills = defaults.skills;
    populateForm(current);
    setStatus("已恢复 Neo Aspect 默认值，保存后生效。");
  }

  function bindNavigation() {
    document.querySelectorAll("[data-window-target]").forEach((item) => {
      item.addEventListener("click", () => {
        const sectionId = item.dataset.windowTarget || "overview";
        window.history.replaceState(null, "", `#${sectionId}`);
        setActiveSection(sectionId);
        scrollToSection(sectionId);
      });
    });
    setActiveSection(window.location.hash || "overview");
    scrollToSection(String(window.location.hash || "overview").replace(/^#/, "") || "overview");
  }

  function bindEvents() {
    els.reloadConfigBtn?.addEventListener("click", () => wrap(loadSettings));
    els.saveConfigBtn?.addEventListener("click", () => wrap(saveSettings));
    els.resetFormBtn?.addEventListener("click", resetForm);
    els.brainFetchModelsBtn?.addEventListener("click", () => wrap(fetchBrainModels));
    [els.clickPreviewX, els.clickPreviewY, els.clickPreviewLabel, els.clickPreviewSize].forEach((input) => {
      input?.addEventListener("input", updateClickPreview);
    });
  }

  async function wrap(action) {
    try {
      await action();
    } catch (error) {
      setStatus(`操作失败：${error.message || String(error)}`);
      showToast("操作失败");
    }
  }

  bindNavigation();
  bindEvents();
  wrap(loadSettings);
})();
