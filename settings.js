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
    localModelSelect: $("local-model-select"),
    refreshLocalModelsBtn: $("refresh-local-models-btn"),
    useLocalModelBtn: $("use-local-model-btn"),
    previewLocalModelBtn: $("preview-local-model-btn"),
    modelPreviewStatus: $("model-preview-status"),
    modelMotionList: $("model-motion-list"),
    modelExpressionList: $("model-expression-list"),
    chatVoice: $("chat-voice"),
    chatTtsProvider: $("chat-tts-provider"),
    chatTtsVoiceId: $("chat-tts-voice-id"),
    chatTtsModel: $("chat-tts-model"),
    chatTtsApiKey: $("chat-tts-api-key"),
    chatTtsApiKeyClear: $("chat-tts-api-key-clear"),
    chatRatePct: $("chat-rate-pct"),
    chatAsrEnabled: $("chat-asr-enabled"),
    chatAsrPushToTalkKey: $("chat-asr-push-to-talk-key"),
    chatAsrProvider: $("chat-asr-provider"),
    chatAsrProviderUrl: $("chat-asr-provider-url"),
    chatAsrModel: $("chat-asr-model"),
    chatAsrApiKey: $("chat-asr-api-key"),
    chatAsrApiKeyClear: $("chat-asr-api-key-clear"),
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
    windowFollowDesktop: $("window-follow-desktop"),
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
    brainPersonaPromptSelectBtn: $("brain-persona-prompt-select-btn"),
    brainPersonaPromptClearBtn: $("brain-persona-prompt-clear-btn"),
    brainPersonaPromptFile: $("brain-persona-prompt-file"),
    brainPersonaPromptCurrent: $("brain-persona-prompt-current"),
    brainPersonaPromptPreview: $("brain-persona-prompt-preview"),
    brainSelfState: $("brain-self-state"),
    brainDecisionTemperature: $("brain-decision-temperature"),
    opsPlaywrightProfile: $("ops-playwright-profile"),
    opsPlaywrightProfileRefresh: $("ops-playwright-profile-refresh"),
    opsPlaywrightProfileStatus: $("ops-playwright-profile-status"),
    opsObserveScreen: $("ops-observe-screen"),
    opsAccessibility: $("ops-accessibility"),
    opsAxIndexRefresh: $("ops-ax-index-refresh"),
    opsAxIndexStatus: $("ops-ax-index-status"),
    opsAuthorizationMode: $("ops-authorization-mode"),
    opsRequireMemoryReview: $("ops-require-memory-review"),
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
    environmentMode: $("environment-mode"),
    environmentIntensity: $("environment-intensity"),
    environmentMetadataEnabled: $("environment-metadata-enabled"),
    environmentScreenContextEnabled: $("environment-screen-context-enabled"),
    environmentIncludeWindowTitles: $("environment-include-window-titles"),
    environmentSpeakEnabled: $("environment-speak-enabled"),
    environmentOpenChatOnSpeak: $("environment-open-chat-on-speak"),
    environmentSensorIntervalSec: $("environment-sensor-interval-sec"),
    environmentPollIntervalSec: $("environment-poll-interval-sec"),
    environmentCooldownMinutes: $("environment-cooldown-minutes"),
    environmentUnansweredBackoffMinutes: $("environment-unanswered-backoff-minutes"),
    environmentDailyLimit: $("environment-daily-limit"),
    environmentMinimumConfidence: $("environment-minimum-confidence"),
    environmentAwayMinutes: $("environment-away-minutes"),
    environmentFocusMinutes: $("environment-focus-minutes"),
    environmentQuietHoursStart: $("environment-quiet-hours-start"),
    environmentQuietHoursEnd: $("environment-quiet-hours-end"),
    environmentBlockedApps: $("environment-blocked-apps"),
    environmentRefreshBtn: $("environment-refresh-btn"),
    environmentClearBtn: $("environment-clear-btn"),
    environmentStatusCards: $("environment-status-cards"),
    environmentAuditList: $("environment-audit-list"),
    environmentStatusText: $("environment-status-text"),
    gameEnabled: $("game-enabled"),
    gameCategoryCombat: $("game-category-combat"),
    gameCategoryGrowth: $("game-category-growth"),
    gameCategoryRoute: $("game-category-route"),
    gameCategoryResources: $("game-category-resources"),
    gameCategoryOutcome: $("game-category-outcome"),
    gameMinReactionIntervalSec: $("game-min-reaction-interval-sec"),
    gameMaxReactionsPerMinute: $("game-max-reactions-per-minute"),
    gameReactionInstruction: $("game-reaction-instruction"),
    gameRefreshBtn: $("game-refresh-btn"),
    gameClearBtn: $("game-clear-btn"),
    gameStatusCards: $("game-status-cards"),
    gameAuditList: $("game-audit-list"),
    gameStatusText: $("game-status-text"),
    memoryConversationSaving: $("memory-conversation-saving"),
    memoryLongTermEnabled: $("memory-long-term-enabled"),
    memoryPreferencesEnabled: $("memory-preferences-enabled"),
    memoryRelationshipEnabled: $("memory-relationship-enabled"),
    memoryFollowUpEnabled: $("memory-follow-up-enabled"),
    memoryFollowUpCooldownHours: $("memory-follow-up-cooldown-hours"),
    memoryQuietHoursStart: $("memory-quiet-hours-start"),
    memoryQuietHoursEnd: $("memory-quiet-hours-end"),
    memoryRetentionDays: $("memory-retention-days"),
    memoryReviewLimit: $("memory-review-limit"),
    memoryRefreshBtn: $("memory-refresh-btn"),
    memoryCatalogStatus: $("memory-catalog-status"),
    memorySavedList: $("memory-saved-list"),
    memoryPendingList: $("memory-pending-list"),
    memoryOpenLoopList: $("memory-open-loop-list"),
    skillsRecipesEnabled: $("skills-recipes-enabled"),
    skillsAutoPropose: $("skills-auto-propose"),
    skillsReviewRequired: $("skills-review-required"),
    skillsRecipeList: $("skills-recipe-list"),
    skillsProposalQueue: $("skills-proposal-queue"),
  };

  let settingsPayload = null;
  let lastLoadedAt = "";
  let personaPromptCatalog = [];
  let chromeProfileCatalog = [];
  let memoryCatalog = { records: [], pending: [] };

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
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    const gameCapability = String(settingsPayload?.game_capability || "").trim();
    if (gameCapability && url.startsWith("/api/game/") && !headers["X-Ipet-Game-Capability"]) {
      headers["X-Ipet-Game-Capability"] = gameCapability;
    }
    const response = await fetch(url, {
      ...options,
      headers,
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
          : neo.human_ops.authorization_mode === "full"
            ? "Brain 直接观察 · 完全授权"
            : "Brain 直接观察 · 逐项审批",
      ],
      [
        "Environment",
        neo.environment.mode === "active"
          ? `主动 · ${neo.environment.intensity}`
          : neo.environment.mode === "shadow"
            ? "影子模式（只记录）"
            : "环境感知关闭",
      ],
      ["Game", neo.game.enabled ? "杀戮尖塔 2 · 自动检测" : "游戏陪伴关闭"],
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
      ["截图观察", neo.human_ops.observe_screen ? "允许截图" : "截图关闭"],
      ["最近授权动作", "等待 human-op 提案"],
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
      <div class="review-item"><strong>remember</strong><span>写入前进入审阅队列</span></div>
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

  function formatMemoryTime(value) {
    const text = String(value || "").trim();
    if (!text) return "未设置";
    const date = new Date(text);
    return Number.isNaN(date.getTime()) ? text : date.toLocaleString();
  }

  function formatBytes(value) {
    const bytes = Math.max(0, Number(value || 0));
    if (bytes < 1024) return `${Math.round(bytes)} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function renderAccessibilityIndexStatus(payload = null) {
    if (!els.opsAxIndexStatus) return;
    if (!payload) {
      els.opsAxIndexStatus.textContent = "尚未建立持久索引";
      return;
    }
    const apps = Array.isArray(payload.apps) ? payload.apps : [];
    const stored = apps.filter((item) => item?.stored);
    const appSummary = stored
      .map((item) => `${item.name || item.app_id} ${Number(item.element_count || 0)} 项${item.stale ? "（待更新）" : ""}`)
      .join(" · ");
    els.opsAxIndexStatus.textContent = stored.length
      ? `${stored.length} 个应用 · ${Number(payload.element_count || 0)} 项 · ${formatBytes(payload.size_bytes)}${appSummary ? ` · ${appSummary}` : ""}`
      : "尚未建立持久索引";
    els.opsAxIndexStatus.title = String(payload.storage_dir || "");
  }

  async function loadAccessibilityIndexStatus() {
    if (settingsPayload?.static_preview) {
      renderAccessibilityIndexStatus();
      return;
    }
    renderAccessibilityIndexStatus(await fetchJson("/api/settings/accessibility-index"));
  }

  async function refreshAccessibilityIndex() {
    if (settingsPayload?.static_preview) {
      setStatus("静态预览不能读取 macOS Accessibility。");
      return;
    }
    if (els.opsAxIndexRefresh) els.opsAxIndexRefresh.disabled = true;
    if (els.opsAxIndexStatus) els.opsAxIndexStatus.textContent = "正在读取全部运行中的 GUI 应用并写入完整 AX 树…";
    try {
      const payload = await fetchJson("/api/settings/accessibility-index/refresh", { method: "POST" });
      renderAccessibilityIndexStatus(payload);
      if (payload.status === "busy") {
        setStatus("AX 索引已有一次更新正在进行。");
        showToast("AX 索引正在更新");
        return;
      }
      if (payload.status && payload.status !== "success") {
        setStatus(`AX 索引未更新：${String(payload.reason || payload.status)}`);
        return;
      }
      const selfExcluded = Number(payload.self_excluded_count || 0);
      const selfExcludedText = selfExcluded > 0 ? `，安全跳过 Ipet 自身 ${selfExcluded} 个进程` : "";
      setStatus(`AX 持久索引已更新 ${Number(payload.updated_count || 0)} 个应用${selfExcludedText}。`);
      showToast("AX 索引已更新");
    } finally {
      if (els.opsAxIndexRefresh) els.opsAxIndexRefresh.disabled = false;
    }
  }

  function environmentModeLabel(mode) {
    return { off: "关闭", shadow: "影子模式", active: "主动模式" }[String(mode || "off")] || "关闭";
  }

  function renderEnvironmentStatus(payload = null) {
    const status = payload || {};
    const presence = status.presence && typeof status.presence === "object" ? status.presence : {};
    const cards = [
      ["运行模式", environmentModeLabel(status.mode)],
      ["当前应用", presence.privacy_blocked ? "隐私保护中" : presence.foreground_app || "尚无元数据"],
      ["空闲时长", `${Math.max(0, Math.round(Number(presence.idle_seconds || 0) / 60))} 分钟`],
      ["待处理机会", String(Number(status.pending_count || 0))],
    ];
    if (els.environmentStatusCards) {
      els.environmentStatusCards.innerHTML = cards.map(
        ([label, value], index) => `
          <div class="summary-card tone-${(index % 4) + 1}">
            <span class="stat-icon"></span>
            <strong class="stat-number">${escapeHtml(value)}</strong>
            <span class="stat-label">${escapeHtml(label)}</span>
          </div>
        `,
      ).join("");
    }
    const audit = Array.isArray(status.recent_audit) ? status.recent_audit.slice().reverse() : [];
    if (els.environmentAuditList) {
      els.environmentAuditList.innerHTML = audit.length
        ? audit.map((item) => `
            <div class="review-item">
              <strong>${escapeHtml(item.action || "event")}</strong>
              <span>${escapeHtml([item.kind, item.reason].filter(Boolean).join(" · ") || "已记录")}</span>
              <small>${escapeHtml(formatMemoryTime(item.timestamp ? new Date(Number(item.timestamp) * 1000).toISOString() : ""))}</small>
            </div>
          `).join("")
        : '<div class="review-item"><strong>暂无事件</strong><span>影子模式下会显示“本来会说什么类型”，但不会发送消息。</span></div>';
    }
    if (els.environmentStatusText) {
      els.environmentStatusText.textContent = status.mode === "shadow"
        ? "影子模式正在运行：只记录候选，不生成、不发送、不朗读。"
        : status.mode === "active"
          ? "主动模式正在运行；繁忙、安静时段和隐私场景会自动抑制。"
          : "环境感知已关闭，不采集元数据，也不会主动开口。";
    }
  }

  async function loadEnvironmentStatus() {
    if (settingsPayload?.static_preview) {
      renderEnvironmentStatus({ ...mergedNeo(settingsPayload.config || {}).environment, presence: {}, recent_audit: [] });
      return;
    }
    renderEnvironmentStatus(await fetchJson("/api/environment/status"));
  }

  async function clearEnvironmentStatus() {
    if (settingsPayload?.static_preview) {
      renderEnvironmentStatus({ ...mergedNeo(settingsPayload.config || {}).environment, presence: {}, recent_audit: [] });
      return;
    }
    renderEnvironmentStatus(await fetchJson("/api/environment/clear", { method: "POST", body: "{}" }));
    showToast("环境记录已清空");
  }

  function gameStateLabel(value) {
    return {
      disabled: "已关闭",
      waiting: "等待游戏",
      connected: "已连接，等待新 Run",
      active: "运行中",
      paused: "已暂停",
      incompatible: "不兼容",
    }[String(value || "waiting")] || "等待游戏";
  }

  function renderGameStatus(payload = null) {
    const status = payload || {};
    const run = status.run_summary && typeof status.run_summary === "object" ? status.run_summary : {};
    const cards = [
      ["连接状态", gameStateLabel(status.state)],
      ["游戏 / 模组", [status.game_version, status.adapter_version].filter(Boolean).join(" / ") || "尚未连接"],
      ["当前 Run", status.run_id ? `Act ${Number(run.act || 0)} · ${Number(run.floor || 0)} 层` : "尚未开始"],
      ["本地玩家", status.local_player_identified === false ? "无法识别" : status.connected ? "已识别" : "等待连接"],
      ["事件序号", `${Number(status.bridge_sequence || 0)} / ${Number(status.accepted_sequence || 0)}`],
      ["待播反应", String(Number(status.pending_count || 0))],
    ];
    if (els.gameStatusCards) {
      els.gameStatusCards.innerHTML = cards.map(
        ([label, value], index) => `
          <div class="summary-card tone-${(index % 4) + 1}">
            <span class="stat-icon"></span>
            <strong class="stat-number">${escapeHtml(value)}</strong>
            <span class="stat-label">${escapeHtml(label)}</span>
          </div>
        `,
      ).join("");
    }
    const audit = Array.isArray(status.recent_audit) ? status.recent_audit.slice().reverse() : [];
    if (els.gameAuditList) {
      els.gameAuditList.innerHTML = audit.length
        ? audit.map((item) => `
            <div class="review-item">
              <strong>${escapeHtml(item.action || "event")}</strong>
              <span>${escapeHtml([item.kind, item.reason].filter(Boolean).join(" · ") || "已记录")}</span>
              <small>${escapeHtml(formatMemoryTime(item.timestamp ? new Date(Number(item.timestamp) * 1000).toISOString() : ""))}</small>
            </div>
          `).join("")
        : '<div class="review-item"><strong>暂无游戏事件</strong><span>安装并启用 STS2 模组后，这里会显示临时事件审计。</span></div>';
    }
    if (els.gameStatusText) {
      if (status.state === "active") {
        els.gameStatusText.textContent = "游戏模式运行中：只观察本地玩家，关键事件会生成纯语音反应。";
      } else if (status.state === "connected") {
        els.gameStatusText.textContent = "已连接《杀戮尖塔 2》，正在等待新 Run。";
      } else if (status.state === "paused") {
        els.gameStatusText.textContent = "当前 Run 的反应已暂停；可在桌宠右键菜单恢复。";
      } else if (status.state === "incompatible") {
        els.gameStatusText.textContent = status.local_player_identified === false
          ? "多人局中无法可靠识别本地玩家，已停止反应。"
          : "游戏或模组版本不兼容，已安全停止反应。";
      } else if (status.state === "disabled") {
        els.gameStatusText.textContent = "游戏陪伴已关闭。";
      } else {
        els.gameStatusText.textContent = "正在等待《杀戮尖塔 2》模组心跳。";
      }
    }
  }

  async function loadGameStatus() {
    if (settingsPayload?.static_preview) {
      const game = mergedNeo(settingsPayload.config || {}).game;
      renderGameStatus({ state: game.enabled ? "waiting" : "disabled", connected: false, recent_audit: [] });
      return;
    }
    renderGameStatus(await fetchJson("/api/game/status"));
  }

  async function clearGameStatus() {
    if (settingsPayload?.static_preview) {
      await loadGameStatus();
      return;
    }
    renderGameStatus(await fetchJson("/api/game/clear", { method: "POST", body: "{}" }));
    showToast("游戏临时记录已清空");
  }

  function setMemoryCatalogStatus(message) {
    if (els.memoryCatalogStatus) {
      els.memoryCatalogStatus.textContent = String(message || "");
    }
  }

  function memoryRecordCard(record, openLoop = false) {
    const status = String(record.status || "active");
    const inactive = status !== "active";
    return `
      <article class="memory-record${inactive ? " is-inactive" : ""}" data-memory-id="${escapeHtml(record.id)}">
        <div class="memory-record-head">
          <div>
            <span class="memory-kind">${escapeHtml(record.kind || "general")}</span>
            <strong>${escapeHtml(record.title || "关系记忆")}</strong>
          </div>
          <span class="memory-status">${escapeHtml(status)}</span>
        </div>
        <p>${escapeHtml(record.summary || "")}</p>
        <small>保存原因：${escapeHtml(record.reason || "用户批准保存")} · 来源会话：${escapeHtml(record.source_conversation_id || "未知")}</small>
        <small>保留至：${escapeHtml(formatMemoryTime(record.expires_at))}${openLoop ? ` · 回访：${escapeHtml(formatMemoryTime(record.follow_up_at))}` : ""}</small>
        <div class="button-row wrap memory-record-actions">
          ${openLoop && status === "active" ? '<button class="ghost-button" type="button" data-memory-action="resolve">完成</button><button class="ghost-button" type="button" data-memory-action="snooze">稍后 1 天</button>' : ""}
          <button class="ghost-button" type="button" data-memory-action="edit">编辑</button>
          <button class="danger-button" type="button" data-memory-action="forget">忘记</button>
        </div>
      </article>
    `;
  }

  function pendingMemoryCard(candidate) {
    const memory = candidate.memory || candidate.target || {};
    const operation = ({ forget: "忘记", resolve: "完成", snooze: "稍后回访", save: "保存" })[candidate.operation] || "保存";
    const reviewActions = candidate.origin === "implicit"
      ? '<button class="primary-button" type="button" data-memory-candidate-action="approve">批准</button><button class="danger-button" type="button" data-memory-candidate-action="reject">拒绝</button>'
      : '<small>这条显式提案正在对话或系统通知中等待审批。</small>';
    return `
      <article class="memory-record" data-proposal-id="${escapeHtml(candidate.proposal_id)}">
        <div class="memory-record-head">
          <div>
            <span class="memory-kind">${escapeHtml(candidate.origin || "explicit")}</span>
            <strong>${operation}候选</strong>
          </div>
          <span class="memory-status">pending</span>
        </div>
        <p>${escapeHtml(memory.summary || candidate.summary || "")}</p>
        <small>原因：${escapeHtml(memory.reason || "等待用户审阅")} · 来源会话：${escapeHtml(candidate.source_conversation_id || "未知")}</small>
        <div class="button-row wrap memory-record-actions">
          ${reviewActions}
        </div>
      </article>
    `;
  }

  function renderMemoryCatalog() {
    const records = Array.isArray(memoryCatalog.records) ? memoryCatalog.records : [];
    const pending = Array.isArray(memoryCatalog.pending) ? memoryCatalog.pending : [];
    const saved = records.filter((record) => record.kind !== "open_loop");
    const openLoops = records.filter((record) => record.kind === "open_loop");
    if (els.memorySavedList) {
      els.memorySavedList.innerHTML = saved.length
        ? saved.map((record) => memoryRecordCard(record)).join("")
        : '<p class="memory-empty">还没有已保存的关系记忆。</p>';
    }
    if (els.memoryPendingList) {
      els.memoryPendingList.innerHTML = pending.length
        ? pending.map(pendingMemoryCard).join("")
        : '<p class="memory-empty">没有待审阅候选。</p>';
    }
    if (els.memoryOpenLoopList) {
      els.memoryOpenLoopList.innerHTML = openLoops.length
        ? openLoops.map((record) => memoryRecordCard(record, true)).join("")
        : '<p class="memory-empty">没有开放事项。</p>';
    }
    setMemoryCatalogStatus(`已保存 ${records.length} 条 · 待审阅 ${pending.length} 条 · 开放事项 ${openLoops.filter((item) => item.status === "active").length} 条`);
  }

  async function loadMemoryCatalog() {
    if (settingsPayload?.static_preview) {
      memoryCatalog = { records: [], pending: [] };
      renderMemoryCatalog();
      setMemoryCatalogStatus("静态预览不读取本地关系记忆。");
      return;
    }
    setMemoryCatalogStatus("正在读取关系记忆...");
    const payload = await fetchJson("/api/memory");
    memoryCatalog = {
      records: Array.isArray(payload.records) ? payload.records : [],
      pending: Array.isArray(payload.pending) ? payload.pending : [],
    };
    renderMemoryCatalog();
  }

  function setMemoryTab(tabName) {
    const requested = String(tabName || "saved");
    document.querySelectorAll("[data-memory-tab]").forEach((button) => {
      button.classList.toggle("active", button.dataset.memoryTab === requested);
    });
    document.querySelectorAll("[data-memory-panel]").forEach((panel) => {
      const active = panel.dataset.memoryPanel === requested;
      panel.hidden = !active;
      panel.classList.toggle("active", active);
    });
  }

  async function updateMemory(memoryId, changes) {
    await fetchJson(`/api/memory/${encodeURIComponent(memoryId)}`, {
      method: "PATCH",
      body: JSON.stringify({ changes }),
    });
    await loadMemoryCatalog();
  }

  async function handleMemoryRecordAction(event) {
    const button = event.target.closest("[data-memory-action]");
    if (!button) return;
    const card = button.closest("[data-memory-id]");
    const memoryId = String(card?.dataset.memoryId || "");
    const record = memoryCatalog.records.find((item) => item.id === memoryId);
    if (!memoryId || !record) return;
    const action = button.dataset.memoryAction;
    if (action === "edit") {
      const summary = window.prompt("编辑记忆内容", String(record.summary || ""));
      if (summary == null || !summary.trim() || summary.trim() === String(record.summary || "").trim()) return;
      await updateMemory(memoryId, { summary: summary.trim() });
      showToast("记忆已更新");
      return;
    }
    if (action === "forget") {
      if (!window.confirm("确定忘记这条长期记忆吗？原始对话不会随之删除。")) return;
      await fetchJson(`/api/memory/${encodeURIComponent(memoryId)}`, { method: "DELETE" });
      await loadMemoryCatalog();
      showToast("已经忘记");
      return;
    }
    if (action === "resolve") {
      await updateMemory(memoryId, { status: "resolved" });
      showToast("开放事项已完成");
      return;
    }
    if (action === "snooze") {
      const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
      await updateMemory(memoryId, { status: "active", follow_up_at: tomorrow });
      showToast("一天后再问");
    }
  }

  async function decideMemoryCandidate(proposalId, approved) {
    const response = await fetch(`/api/human-ops/proposals/${encodeURIComponent(proposalId)}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved: !!approved }),
    });
    const body = await response.text();
    if (!response.ok) {
      throw new Error(body || `memory review http ${response.status}`);
    }
    await loadMemoryCatalog();
    showToast(approved ? "记忆已批准" : "候选已拒绝");
  }

  async function handleMemoryCandidateAction(event) {
    const button = event.target.closest("[data-memory-candidate-action]");
    if (!button) return;
    const card = button.closest("[data-proposal-id]");
    const proposalId = String(card?.dataset.proposalId || "");
    if (!proposalId) return;
    await decideMemoryCandidate(proposalId, button.dataset.memoryCandidateAction === "approve");
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

  function renderPersonaPromptOptions(selectedName = "") {
    const select = els.brainPersonaPromptFile;
    if (!select) {
      return;
    }
    const requestedName = String(selectedName || select.value || "").trim();
    select.replaceChildren();
    const disabledOption = document.createElement("option");
    disabledOption.value = "";
    disabledOption.textContent = "未启用用户人格提示词";
    select.appendChild(disabledOption);
    personaPromptCatalog.forEach((prompt) => {
      const option = document.createElement("option");
      option.value = prompt.name;
      option.textContent = prompt.name;
      select.appendChild(option);
    });
    if (requestedName && !personaPromptCatalog.some((prompt) => prompt.name === requestedName)) {
      const missingOption = document.createElement("option");
      missingOption.value = requestedName;
      missingOption.textContent = `${requestedName}（文件不存在）`;
      select.appendChild(missingOption);
    }
    select.value = requestedName;
  }

  function renderPersonaPromptState() {
    const selectedName = String(els.brainPersonaPromptFile?.value || "").trim();
    const selectedPrompt = personaPromptCatalog.find((prompt) => prompt.name === selectedName);
    if (els.brainPersonaPromptCurrent) {
      els.brainPersonaPromptCurrent.textContent = selectedName || "未启用";
      els.brainPersonaPromptCurrent.classList.toggle("missing", !!selectedName && !selectedPrompt);
    }
    if (els.brainPersonaPromptPreview) {
      els.brainPersonaPromptPreview.value = selectedPrompt?.content || "";
      els.brainPersonaPromptPreview.placeholder = selectedName && !selectedPrompt
        ? "所选文件已不存在，请重新选择或停用。"
        : "选择 prompts 文件夹中的 .md 文件后，这里会显示预览。";
    }
  }

  async function loadPersonaPromptCatalog(selectedName = "") {
    const payload = await fetchJson("/api/settings/persona-prompts");
    personaPromptCatalog = Array.isArray(payload.prompts)
      ? payload.prompts.filter((prompt) => prompt && typeof prompt.name === "string" && typeof prompt.content === "string")
      : [];
    renderPersonaPromptOptions(selectedName);
  }

  function setChromeProfileStatus(message) {
    if (els.opsPlaywrightProfileStatus) {
      els.opsPlaywrightProfileStatus.textContent = String(message || "");
    }
  }

  function renderChromeProfileOptions(selectedDirectory = "") {
    const select = els.opsPlaywrightProfile;
    if (!select) {
      return;
    }
    const requestedDirectory = String(selectedDirectory || select.value || "").trim();
    select.replaceChildren();
    const emptyOption = document.createElement("option");
    emptyOption.value = "";
    emptyOption.textContent = "未选择（执行时询问）";
    select.appendChild(emptyOption);
    chromeProfileCatalog.forEach((profile) => {
      const option = document.createElement("option");
      option.value = profile.directory;
      option.textContent = `${profile.display_name} · ${profile.directory}${profile.active ? "（当前活动）" : ""}`;
      select.appendChild(option);
    });
    if (requestedDirectory && !chromeProfileCatalog.some((profile) => profile.directory === requestedDirectory)) {
      const missingOption = document.createElement("option");
      missingOption.value = requestedDirectory;
      missingOption.textContent = `${requestedDirectory}（资料已不存在）`;
      select.appendChild(missingOption);
    }
    select.value = requestedDirectory;
  }

  function renderChromeProfileStatus() {
    const selectedDirectory = String(els.opsPlaywrightProfile?.value || "").trim();
    const selected = chromeProfileCatalog.find((profile) => profile.directory === selectedDirectory);
    if (!selectedDirectory) {
      setChromeProfileStatus("未设置默认资料，Playwright 需要时会询问。");
    } else if (!selected) {
      setChromeProfileStatus("已保存的 Chrome 资料已不存在，请重新选择。");
    } else {
      setChromeProfileStatus(`已选择 ${selected.display_name} · ${selected.directory}${selected.active ? "（当前活动）" : ""}`);
    }
  }

  async function loadChromeProfileCatalog(selectedDirectory = "") {
    setChromeProfileStatus("正在读取 Chrome 个人资料...");
    const payload = await fetchJson("/api/settings/chrome-profiles");
    chromeProfileCatalog = Array.isArray(payload.profiles)
      ? payload.profiles.filter(
        (profile) => profile && typeof profile.directory === "string" && typeof profile.display_name === "string",
      )
      : [];
    renderChromeProfileOptions(selectedDirectory);
    if (payload.error) {
      setChromeProfileStatus(`Chrome 资料不可用：${payload.error}`);
    } else {
      renderChromeProfileStatus();
    }
  }

  function openPersonaPromptPicker() {
    const select = els.brainPersonaPromptFile;
    if (!select) {
      return;
    }
    try {
      if (typeof select.showPicker === "function") {
        select.showPicker();
        return;
      }
    } catch (_error) {
      // Qt WebEngine versions without showPicker still support focus + native selection.
    }
    select.focus();
  }

  function clearPersonaPrompt() {
    if (els.brainPersonaPromptFile) {
      els.brainPersonaPromptFile.value = "";
    }
    renderPersonaPromptState();
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

  const live2dFactory = window.IpetSettingsLive2d?.createSettingsLive2dController;
  if (typeof live2dFactory !== "function") {
    throw new Error("settings_live2d.js must load before settings.js");
  }
  const live2dController = live2dFactory({
    els,
    fetchJson,
    getSettingsPayload: () => settingsPayload,
    showToast,
    runAction: (action) => wrap(action),
  });

  function loadLocalModels() {
    return live2dController.loadLocalModels();
  }

  function populateLocalModels() {
    live2dController.populateLocalModels();
  }

  function useSelectedLocalModel() {
    return live2dController.useSelectedLocalModel();
  }

  function previewSelectedLocalModel() {
    return live2dController.previewSelectedLocalModel();
  }

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
    const selectedPrompt = settingsPayload?.config?.brain?.persona_prompt_file || "";
    const selectedChromeProfile = settingsPayload?.config?.human_ops?.playwright_profile || "";
    if (settingsPayload.static_preview) {
      personaPromptCatalog = [];
      renderPersonaPromptOptions(selectedPrompt);
      chromeProfileCatalog = [];
      renderChromeProfileOptions(selectedChromeProfile);
    } else {
      try {
        await loadPersonaPromptCatalog(selectedPrompt);
      } catch (_error) {
        personaPromptCatalog = [];
        renderPersonaPromptOptions(selectedPrompt);
      }
      try {
        await loadChromeProfileCatalog(selectedChromeProfile);
      } catch (error) {
        chromeProfileCatalog = [];
        renderChromeProfileOptions(selectedChromeProfile);
        setChromeProfileStatus(`Chrome 资料不可用：${error.message || String(error)}`);
      }
    }
    lastLoadedAt = new Date().toLocaleTimeString();
    populateForm(settingsPayload.config || {});
    renderPersonaPromptState();
    renderChromeProfileStatus();
    try {
      await loadAccessibilityIndexStatus();
    } catch (error) {
      renderAccessibilityIndexStatus();
      if (els.opsAxIndexStatus) {
        els.opsAxIndexStatus.textContent = `AX 索引不可用：${error.message || String(error)}`;
      }
    }
    try {
      await loadLocalModels();
    } catch (error) {
      populateLocalModels();
    }
    try {
      await loadMemoryCatalog();
    } catch (error) {
      memoryCatalog = { records: [], pending: [] };
      renderMemoryCatalog();
      setMemoryCatalogStatus(`关系记忆不可用：${error.message || String(error)}`);
    }
    try {
      await loadEnvironmentStatus();
    } catch (error) {
      renderEnvironmentStatus();
      if (els.environmentStatusText) {
        els.environmentStatusText.textContent = `环境状态不可用：${error.message || String(error)}`;
      }
    }
    try {
      await loadGameStatus();
    } catch (error) {
      renderGameStatus();
      if (els.gameStatusText) {
        els.gameStatusText.textContent = `游戏状态不可用：${error.message || String(error)}`;
      }
    }
    if (!settingsPayload.static_preview) {
      setStatus("设置已加载。");
    }
  }

  async function saveSettings() {
    if (settingsPayload?.static_preview) {
      settingsPayload = { ...fallbackSettings(), config: readForm() };
      lastLoadedAt = new Date().toLocaleTimeString();
      populateForm(settingsPayload.config || {});
      renderPersonaPromptState();
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
    renderPersonaPromptState();
    populateLocalModels();
    await loadMemoryCatalog();
    await loadEnvironmentStatus();
    await loadGameStatus();
    setStatus("设置已保存，Ipet 会按新配置刷新。");
    showToast("已保存");
  }

  function resetForm() {
    const current = clone(settingsPayload?.config || {});
    const defaults = neoDefaults();
    current.brain = defaults.brain;
    current.human_ops = defaults.human_ops;
    current.environment = defaults.environment;
    current.game = defaults.game;
    current.memory = defaults.memory;
    current.skills = defaults.skills;
    populateForm(current);
    renderPersonaPromptState();
    populateLocalModels();
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
    els.refreshLocalModelsBtn?.addEventListener("click", () => wrap(loadLocalModels));
    els.useLocalModelBtn?.addEventListener("click", () => wrap(useSelectedLocalModel));
    els.previewLocalModelBtn?.addEventListener("click", () => wrap(previewSelectedLocalModel));
    els.localModelSelect?.addEventListener("change", live2dController.handleModelSelectChange);
    els.modelPath?.addEventListener("input", live2dController.handleModelPathInput);
    els.brainPersonaPromptSelectBtn?.addEventListener("click", openPersonaPromptPicker);
    els.brainPersonaPromptClearBtn?.addEventListener("click", clearPersonaPrompt);
    els.brainPersonaPromptFile?.addEventListener("change", renderPersonaPromptState);
    els.opsPlaywrightProfileRefresh?.addEventListener("click", () => wrap(
      () => loadChromeProfileCatalog(els.opsPlaywrightProfile?.value || ""),
    ));
    els.opsPlaywrightProfile?.addEventListener("change", renderChromeProfileStatus);
    els.opsObserveFetchModelsBtn?.addEventListener("click", () => wrap(fetchObserveModels));
    els.opsAxIndexRefresh?.addEventListener("click", () => wrap(refreshAccessibilityIndex));
    els.memoryRefreshBtn?.addEventListener("click", () => wrap(loadMemoryCatalog));
    els.environmentRefreshBtn?.addEventListener("click", () => wrap(loadEnvironmentStatus));
    els.environmentClearBtn?.addEventListener("click", () => wrap(clearEnvironmentStatus));
    els.gameRefreshBtn?.addEventListener("click", () => wrap(loadGameStatus));
    els.gameClearBtn?.addEventListener("click", () => wrap(clearGameStatus));
    document.querySelectorAll("[data-memory-tab]").forEach((button) => {
      button.addEventListener("click", () => setMemoryTab(button.dataset.memoryTab || "saved"));
    });
    els.memorySavedList?.addEventListener("click", (event) => wrap(() => handleMemoryRecordAction(event)));
    els.memoryOpenLoopList?.addEventListener("click", (event) => wrap(() => handleMemoryRecordAction(event)));
    els.memoryPendingList?.addEventListener("click", (event) => wrap(() => handleMemoryCandidateAction(event)));
    els.brainProvider?.addEventListener("change", syncProviderControls);
    els.opsObserveModelProvider?.addEventListener("change", syncProviderControls);
    els.chatAsrProvider?.addEventListener("change", syncProviderControls);
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
