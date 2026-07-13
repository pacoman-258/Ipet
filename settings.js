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
    brainReasoningEffortField: $("brain-reasoning-effort-field"),
    brainReasoningEffort: $("brain-reasoning-effort"),
    brainStreamingEnabled: $("brain-streaming-enabled"),
    brainWebSearchEnabled: $("brain-web-search-enabled"),
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
    opsObserveModelEnabled: $("ops-observe-model-enabled"),
    opsObserveModelProvider: $("ops-observe-model-provider"),
    opsObserveModelEndpoint: $("ops-observe-model-endpoint"),
    opsObserveFetchModelsBtn: $("ops-observe-fetch-models-btn"),
    opsObserveModelStatus: $("ops-observe-model-status"),
    opsObserveModelList: $("ops-observe-model-list"),
    opsObserveModelName: $("ops-observe-model-name"),
    opsObserveApiKey: $("ops-observe-api-key"),
    opsObserveApiKeyClear: $("ops-observe-api-key-clear"),
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

  const formFactory = window.IpetSettingsForm?.createSettingsFormController;
  if (typeof formFactory !== "function") {
    throw new Error("settings_form.js must load before settings.js");
  }
  const formController = formFactory({
    els,
    getSettingsPayload: () => settingsPayload,
    renderSummary,
    renderDiagnostics,
  });

  function clone(value) {
    return formController.clone(value);
  }

  function numberValue(input, fallback) {
    return formController.numberValue(input, fallback);
  }

  function intValue(input, fallback) {
    return formController.intValue(input, fallback);
  }

  function stringValue(input, fallback = "") {
    return formController.stringValue(input, fallback);
  }

  function linesValue(input) {
    return formController.linesValue(input);
  }

  function textFromLines(lines) {
    return formController.textFromLines(lines);
  }

  function neoDefaults() {
    return formController.neoDefaults();
  }

  function mergedNeo(config) {
    return formController.mergedNeo(config);
  }

  function fallbackSettings() {
    return formController.fallbackSettings();
  }

  function populateForm(config) {
    return formController.populateForm(config);
  }

  function readForm() {
    return formController.readForm();
  }

  function updateClickPreview() {
    return formController.updateClickPreview();
  }

  function syncProviderControls() {
    return formController.syncProviderControls();
  }

  function isGoogleAistudio(value) {
    return formController.isGoogleAistudio(value);
  }

  function isCodex(value) {
    return formController.isCodex(value);
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
      [
        "Human Ops",
        neo.human_ops.observe_model.enabled
          ? `observe · ${neo.human_ops.observe_model.model_name || "独立模型"}`
          : neo.human_ops.require_act_review
            ? "Brain 直接观察 · 动作需批准"
            : "Brain 直接观察 · 动作审批关闭",
      ],
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
    if (isGoogleAistudio(provider)) {
      return "Google AI Studio";
    }
    if (isCodex(provider)) {
      return "Codex";
    }
    return "OpenAI";
  }

  const modelPickerFactory = window.IpetSettingsModelPicker?.createSettingsModelPickerController;
  if (typeof modelPickerFactory !== "function") {
    throw new Error("settings_model_picker.js must load before settings.js");
  }
  const modelPickerController = modelPickerFactory({
    els,
    fetchJson,
    stringValue,
    isGoogleAistudio,
    isCodex,
    getSettingsPayload: () => settingsPayload,
    renderSummary,
    readForm,
    showToast,
  });

  function setBrainModelStatus(message) {
    modelPickerController.setBrainModelStatus(message);
  }

  function renderBrainModelOptions(models) {
    modelPickerController.renderBrainModelOptions(models);
  }

  async function fetchBrainModels() {
    return modelPickerController.fetchBrainModels();
  }

  function setObserveModelStatus(message) {
    modelPickerController.setObserveModelStatus(message);
  }

  function renderObserveModelOptions(models) {
    modelPickerController.renderObserveModelOptions(models);
  }

  async function fetchObserveModels() {
    return modelPickerController.fetchObserveModels();
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
    els.opsObserveFetchModelsBtn?.addEventListener("click", () => wrap(fetchObserveModels));
    els.brainProvider?.addEventListener("change", syncProviderControls);
    els.opsObserveModelProvider?.addEventListener("change", syncProviderControls);
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
