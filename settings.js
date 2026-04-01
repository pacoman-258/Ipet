(() => {
  const $ = (id) => document.getElementById(id);

  const els = {
    toast: $("toast"),
    statusBanner: $("status-banner"),
    summaryCards: $("summary-cards"),
    modelPath: $("model-path"),
    localModelSelect: $("local-model-select"),
    refreshLocalModelsBtn: $("refresh-local-models-btn"),
    useLocalModelBtn: $("use-local-model-btn"),
    previewLocalModelBtn: $("preview-local-model-btn"),
    modelMotionList: $("model-motion-list"),
    modelExpressionList: $("model-expression-list"),
    chatBackendUrl: $("chat-backend-url"),
    chatLlmProvider: $("chat-llm-provider"),
    chatApiBaseUrl: $("chat-api-base-url"),
    chatApiKey: $("chat-api-key"),
    chatModel: $("chat-model"),
    chatRouterEnabled: $("chat-router-enabled"),
    chatRouterLlmProvider: $("chat-router-llm-provider"),
    chatRouterApiBaseUrl: $("chat-router-api-base-url"),
    chatRouterApiKey: $("chat-router-api-key"),
    chatRouterModel: $("chat-router-model"),
    remoteModelSelect: $("remote-model-select"),
    fetchRemoteModelsBtn: $("fetch-remote-models-btn"),
    useRemoteModelBtn: $("use-remote-model-btn"),
    routerRemoteModelSelect: $("router-remote-model-select"),
    fetchRouterModelsBtn: $("fetch-router-models-btn"),
    useRouterModelBtn: $("use-router-model-btn"),
    chatVoice: $("chat-voice"),
    chatSessionId: $("chat-session-id"),
    chatMemoryWindow: $("chat-memory-window"),
    chatTopicHistoryEnabled: $("chat-topic-history-enabled"),
    chatTopicHistorySummaryInterval: $("chat-topic-history-summary-interval"),
    chatAsrEnabled: $("chat-asr-enabled"),
    chatAsrPushToTalkKey: $("chat-asr-push-to-talk-key"),
    chatAsrInterimResults: $("chat-asr-interim-results"),
    chatTtsProvider: $("chat-tts-provider"),
    chatRatePct: $("chat-rate-pct"),
    chatRateLabel: $("chat-rate-label"),
    ttsPresetSelect: $("tts-preset-select"),
    applyTtsPresetBtn: $("apply-tts-preset-btn"),
    chatTtsProviderUrl: $("chat-tts-provider-url"),
    chatSystemPrompt: $("chat-system-prompt"),
    importSystemPromptBtn: $("import-system-prompt-btn"),
    systemPromptFileInput: $("system-prompt-file-input"),
    chatExpressionMode: $("chat-expression-mode"),
    chatReactEnabled: $("chat-react-enabled"),
    chatReactVisibility: $("chat-react-visibility"),
    chatMaxReasoningSteps: $("chat-max-reasoning-steps"),
    toolingEnabled: $("tooling-enabled"),
    skillsEnabled: $("skills-enabled"),
    skillsSummary: $("skills-summary"),
    skillLocalPath: $("skill-local-path"),
    skillLocalPickBtn: $("skill-local-pick-btn"),
    skillLocalImportBtn: $("skill-local-import-btn"),
    skillGitUrl: $("skill-git-url"),
    skillGitName: $("skill-git-name"),
    skillGitRef: $("skill-git-ref"),
    skillGitSubdir: $("skill-git-subdir"),
    skillGitImportBtn: $("skill-git-import-btn"),
    skillsList: $("skills-list"),
    skillsStatus: $("skills-status"),
    fileAllowlistInput: $("file-allowlist-input"),
    fileAllowlistAddBtn: $("file-allowlist-add-btn"),
    fileAllowlistPickBtn: $("file-allowlist-pick-btn"),
    fileAllowlistClearBtn: $("file-allowlist-clear-btn"),
    fileAllowlistEffectiveBtn: $("file-allowlist-effective-btn"),
    fileAllowlistList: $("file-allowlist-list"),
    fileAllowlistEffectiveList: $("file-allowlist-effective-list"),
    thirdPartyEnabled: $("third-party-enabled"),
    mcpPresetSelect: $("mcp-preset-select"),
    mcpApplyPresetBtn: $("mcp-apply-preset-btn"),
    mcpServerName: $("mcp-server-name"),
    mcpConfigJson: $("mcp-config-json"),
    mcpCreateBtn: $("mcp-create-btn"),
    mcpReloadBtn: $("mcp-reload-btn"),
    mcpServerList: $("mcp-server-list"),
    petScale: $("pet-scale"),
    petOpacity: $("pet-opacity"),
    petOffsetX: $("pet-offset-x"),
    petOffsetY: $("pet-offset-y"),
    petRotation: $("pet-rotation"),
    petBackgroundOverlayOpacity: $("pet-background-overlay-opacity"),
    petEditMode: $("pet-edit-mode"),
    petFollowMouse: $("pet-follow-mouse"),
    petBackgroundEnabled: $("pet-background-enabled"),
    petBackgroundImage: $("pet-background-image"),
    petBackgroundPickBtn: $("pet-background-pick-btn"),
    petBackgroundClearBtn: $("pet-background-clear-btn"),
    windowX: $("window-x"),
    windowY: $("window-y"),
    windowWidth: $("window-width"),
    windowHeight: $("window-height"),
    windowLocked: $("window-locked"),
    reloadConfigBtn: $("reload-config-btn"),
    resetFormBtn: $("reset-form-btn"),
    saveConfigBtn: $("save-config-btn"),
  };

  let settingsPayload = null;
  let localModels = [];
  let remoteModels = [];
  let routerRemoteModels = [];
  let mcpServers = [];
  let mcpServerPresets = {};
  let fileAllowlist = [];
  let effectiveFileAllowlist = [];
  let skillsPayload = null;
  let skillRecords = [];
  let skillDefaultActiveIds = [];
  let toastTimer = 0;
  const fileAllowlistPickerEndpoint = "/api/settings/file-allowlist/pick";
  const petBackgroundPickerEndpoint = "/api/settings/file-allowlist/pick";

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function setStatus(message) {
    els.statusBanner.textContent = String(message || "");
    els.statusBanner.classList.remove("status-error");
    els.statusBanner.classList.remove("status-warning");
  }

  function showToast(message, tone = "success") {
    if (toastTimer) {
      clearTimeout(toastTimer);
    }
    els.toast.textContent = String(message || "");
    els.toast.className = `toast show ${tone}`;
    toastTimer = window.setTimeout(() => {
      els.toast.className = "toast";
    }, 2600);
  }

  function reportStatusIssue(message, tone = "error") {
    const text = String(message || "设置页发生异常");
    els.statusBanner.textContent = text;
    els.statusBanner.classList.toggle("status-error", tone !== "warning");
    els.statusBanner.classList.toggle("status-warning", tone === "warning");
    showToast(text, tone === "warning" ? "warning" : "error");
  }

  function installGlobalErrorHandlers() {
    window.addEventListener("error", (event) => {
      const message = event?.error?.message || event?.message || "设置页脚本加载失败";
      reportStatusIssue(`设置页错误：${message}`);
    });
    window.addEventListener("unhandledrejection", (event) => {
      const reason = event?.reason;
      const message = reason?.message || String(reason || "未知 Promise 异常");
      reportStatusIssue(`设置页错误：${message}`);
    });
  }

  async function fetchJson(url, options = {}) {
    const resp = await fetch(url, options);
    let data = {};
    try {
      data = await resp.json();
    } catch (_) {
      data = {};
    }
    if (!resp.ok) {
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    return data;
  }

  function updateRateLabel() {
    const value = Number(els.chatRatePct.value || 0);
    els.chatRateLabel.textContent = `${value >= 0 ? "+" : ""}${value}%`;
  }

  function normalizeAllowlistPath(value) {
    let normalized = String(value || "").trim().replace(/\//g, "\\");
    if (!normalized) {
      return "";
    }
    normalized = normalized.replace(/[\\]+$/g, "");
    if (/^[A-Za-z]:$/.test(normalized)) {
      normalized += "\\";
    }
    return normalized;
  }

  function dedupeAllowlistPaths(values) {
    const seen = new Set();
    const next = [];
    for (const value of values || []) {
      const normalized = normalizeAllowlistPath(value);
      if (!normalized || seen.has(normalized)) {
        continue;
      }
      seen.add(normalized);
      next.push(normalized);
    }
    return next;
  }

  function renderStaticAllowlist(container, paths, emptyText) {
    container.innerHTML = "";
    if (!paths.length) {
      container.classList.add("empty-state");
      container.textContent = emptyText;
      return;
    }
    container.classList.remove("empty-state");
    paths.forEach((path, index) => {
      const item = document.createElement("article");
      item.className = "allowlist-item";
      item.innerHTML = `
        <div class="allowlist-path">
          <strong>目录 ${index + 1}</strong>
          <span>${path}</span>
        </div>
      `;
      container.appendChild(item);
    });
  }

  function renderFileAllowlist() {
    const paths = dedupeAllowlistPaths(fileAllowlist);
    fileAllowlist = paths;
    els.fileAllowlistList.innerHTML = "";
    if (!paths.length) {
      els.fileAllowlistList.classList.add("empty-state");
      els.fileAllowlistList.textContent = "当前还没有编辑中的白名单目录。";
      return;
    }
    els.fileAllowlistList.classList.remove("empty-state");
    paths.forEach((path, index) => {
      const item = document.createElement("article");
      item.className = "allowlist-item";
      item.innerHTML = `
        <div class="allowlist-path">
          <strong>目录 ${index + 1}</strong>
          <span>${path}</span>
        </div>
        <button type="button" class="ghost-button danger-button allowlist-remove-btn">移除</button>
      `;
      item.querySelector(".allowlist-remove-btn").addEventListener("click", () => {
        fileAllowlist = paths.filter((_, currentIndex) => currentIndex !== index);
        renderFileAllowlist();
        showToast("已移除目录。");
      });
      els.fileAllowlistList.appendChild(item);
    });
  }

  function renderEffectiveFileAllowlist() {
    effectiveFileAllowlist = dedupeAllowlistPaths(effectiveFileAllowlist);
    renderStaticAllowlist(
      els.fileAllowlistEffectiveList,
      effectiveFileAllowlist,
      "当前还没有生效的白名单目录。"
    );
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, (character) => {
      switch (character) {
        case "&":
          return "&amp;";
        case "<":
          return "&lt;";
        case ">":
          return "&gt;";
        case '"':
          return "&quot;";
        case "'":
          return "&#39;";
        default:
          return character;
      }
    });
  }

  function normalizeSkillId(value) {
    return String(value || "").trim();
  }

  function dedupeSkillIds(values, allowedIds = null) {
    const allowedSet = Array.isArray(allowedIds) ? new Set(allowedIds.map(normalizeSkillId).filter(Boolean)) : null;
    const seen = new Set();
    const next = [];
    for (const value of values || []) {
      const normalized = normalizeSkillId(value);
      if (!normalized || seen.has(normalized)) {
        continue;
      }
      if (allowedSet && !allowedSet.has(normalized)) {
        continue;
      }
      seen.add(normalized);
      next.push(normalized);
    }
    return next;
  }

  function normalizeSkillCount(value) {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? number : 0;
  }

  function normalizeSkillRecord(raw) {
    if (!raw || typeof raw !== "object") {
      return null;
    }
    const id = normalizeSkillId(raw.id || raw.skill_id || raw.slug || raw.name);
    if (!id) {
      return null;
    }
    const sourceValue = String(raw.source || raw.origin || raw.source_type || "").toLowerCase();
    const builtin = !!raw.builtin || sourceValue === "builtin";
    const imported = !!raw.imported || sourceValue === "imported" || (!builtin && sourceValue !== "builtin");
    const scriptList = Array.isArray(raw.scripts)
      ? raw.scripts
      : Array.isArray(raw.script_definitions)
        ? raw.script_definitions
        : [];
    const allowlistList = Array.isArray(raw.tool_allowlist)
      ? raw.tool_allowlist
      : Array.isArray(raw.allowlist)
        ? raw.allowlist
        : [];
    return {
      id,
      name: String(raw.name || raw.title || id),
      description: String(raw.description || raw.summary || raw.body_summary || ""),
      source: builtin ? "builtin" : "imported",
      builtin,
      imported,
      path: String(raw.path || raw.skill_path || raw.directory || raw.root || raw.location || raw.manifest_path || ""),
      version: String(raw.version || raw.skill_version || ""),
      valid: raw.valid !== false && raw.status !== "invalid",
      error: String(raw.error || raw.message || ""),
      scriptCount: normalizeSkillCount(raw.script_count ?? scriptList.length),
      allowlistCount: normalizeSkillCount(raw.allowlist_count ?? allowlistList.length),
      scriptList,
      allowlistList,
      sourceRepo: String(raw.source_repo || raw.repo_url || ""),
      sourceRef: String(raw.source_ref || raw.ref || ""),
      sourceSubdir: String(raw.source_subdir || raw.subdir || ""),
      compatibilityMode: String(raw.compatibility_mode || "native"),
      adapterProfile: String(raw.adapter_profile || ""),
    };
  }

  function getSkillStats() {
    const total = skillRecords.length;
    const builtinCount = skillRecords.filter((item) => item.builtin).length;
    const importedCount = skillRecords.filter((item) => item.imported).length;
    const scriptCount = skillRecords.reduce((sum, item) => sum + (item.scriptCount || 0), 0);
    const allowlistCount = skillRecords.reduce((sum, item) => sum + (item.allowlistCount || 0), 0);
    const defaultActiveCount = skillRecords.length
      ? dedupeSkillIds(skillDefaultActiveIds, skillRecords.map((item) => item.id)).length
      : dedupeSkillIds(skillDefaultActiveIds).length;
    return {
      total,
      builtinCount,
      importedCount,
      scriptCount,
      allowlistCount,
      defaultActiveCount,
      enabled: !!els.skillsEnabled?.checked,
    };
  }

  function selectedSkillDefaultActiveIds() {
    if (!skillRecords.length) {
      return dedupeSkillIds(skillDefaultActiveIds);
    }
    return dedupeSkillIds(skillDefaultActiveIds, skillRecords.map((item) => item.id));
  }

  function setSkillsPanelStatus(message) {
    if (els.skillsStatus) {
      els.skillsStatus.textContent = String(message || "");
    }
  }

  function renderSkillsSummary() {
    if (!els.skillsSummary) {
      return;
    }
    const stats = getSkillStats();
    const cards = [
      ["状态", stats.enabled ? "已启用" : "未启用"],
      ["已安装", `${stats.total} 个`],
      ["内置", `${stats.builtinCount} 个`],
      ["第三方", `${stats.importedCount} 个`],
      ["默认启用", `${stats.defaultActiveCount} 个`],
      ["脚本", `${stats.scriptCount} 个`],
      ["允许目录", `${stats.allowlistCount} 项`],
    ];
    els.skillsSummary.innerHTML = "";
    for (const [label, value] of cards) {
      const card = document.createElement("article");
      card.className = "summary-card";
      card.innerHTML = `<strong>${label}</strong><span>${value}</span>`;
      els.skillsSummary.appendChild(card);
    }
  }

  function toggleSkillDefaultActive(skillId, checked) {
    const normalized = normalizeSkillId(skillId);
    if (!normalized) {
      return;
    }
    const current = new Set(selectedSkillDefaultActiveIds());
    if (checked) {
      current.add(normalized);
    } else {
      current.delete(normalized);
    }
    skillDefaultActiveIds = dedupeSkillIds([...current], skillRecords.map((item) => item.id));
    renderSkillsSummary();
  }

  function renderSkillsList() {
    if (!els.skillsList) {
      return;
    }
    els.skillsList.innerHTML = "";
    const items = skillRecords.slice();
    if (!items.length) {
      els.skillsList.classList.add("empty-state");
      els.skillsList.textContent = "当前还没有加载到技能。";
      setSkillsPanelStatus(skillsPayload?.detail || "技能接口尚未接入，或当前还没有已安装的技能。");
      return;
    }
    els.skillsList.classList.remove("empty-state");
    const activeSet = new Set(selectedSkillDefaultActiveIds());
    items.forEach((item) => {
      const card = document.createElement("article");
      card.className = "server-card";
      const sourceLabel = item.builtin ? "内置" : "第三方";
      const scriptMeta = `${item.scriptCount || 0} 个脚本`;
      const allowlistMeta = `${item.allowlistCount || 0} 项允许工具`;
      const active = activeSet.has(item.id);
      card.innerHTML = `
        <header>
          <div class="server-title">
            <h4>${escapeHtml(item.name)}</h4>
            <span class="server-badge ${item.builtin ? "" : "disabled"}">${sourceLabel}</span>
          </div>
          <div class="server-meta">
            <span>id: ${escapeHtml(item.id)}</span>
            ${item.version ? `<span>version: ${escapeHtml(item.version)}</span>` : ""}
            ${item.path ? `<span>path: ${escapeHtml(item.path)}</span>` : ""}
            ${item.sourceRepo ? `<span>repo: ${escapeHtml(item.sourceRepo)}</span>` : ""}
            ${item.sourceSubdir ? `<span>subdir: ${escapeHtml(item.sourceSubdir)}</span>` : ""}
            <span>compat: ${escapeHtml(item.compatibilityMode || "native")}</span>
            ${item.adapterProfile ? `<span>adapter: ${escapeHtml(item.adapterProfile)}</span>` : ""}
            <span>${scriptMeta}</span>
            <span>${allowlistMeta}</span>
            ${item.error ? `<span>error: ${escapeHtml(item.error)}</span>` : ""}
          </div>
        </header>
        <p class="preview-note">${escapeHtml(item.description || "暂无描述。")}</p>
        <div class="button-row wrap">
          <label class="inline-toggle">
            <input type="checkbox" data-skill-default-toggle="${escapeHtml(item.id)}" ${active ? "checked" : ""} />
            <span>默认启用</span>
          </label>
          ${item.imported ? `<button type="button" class="ghost-button danger-button" data-skill-delete="${escapeHtml(item.id)}">删除</button>` : ""}
        </div>
      `;
      const toggleInput = card.querySelector("[data-skill-default-toggle]");
      toggleInput.addEventListener("change", () => toggleSkillDefaultActive(item.id, toggleInput.checked));
      const deleteButton = card.querySelector("[data-skill-delete]");
      if (deleteButton) {
        deleteButton.addEventListener("click", () => wrapAction(() => deleteSkill(item.id, item.name)));
      }
      els.skillsList.appendChild(card);
    });
    setSkillsPanelStatus(skillsPayload?.detail || "技能列表已加载。");
  }

  async function fetchOptionalJson(url, options = {}) {
    try {
      const resp = await fetch(url, options);
      const text = await resp.text();
      let data = {};
      if (text) {
        try {
          data = JSON.parse(text);
        } catch (_) {
          data = { detail: text };
        }
      }
      return { ok: resp.ok, status: resp.status, data };
    } catch (error) {
      return { ok: false, status: 0, data: {}, error };
    }
  }

  function syncFileAllowlistInput(value = "") {
    els.fileAllowlistInput.value = String(value || "");
    els.fileAllowlistInput.focus();
    els.fileAllowlistInput.select();
  }

  function addFileAllowlistPath(rawValue, { quiet = false, announce = true } = {}) {
    const normalized = normalizeAllowlistPath(rawValue);
    if (!normalized) {
      if (!quiet) {
        throw new Error("请先输入目录路径。");
      }
      return false;
    }
    if (fileAllowlist.includes(normalized)) {
      if (!quiet) {
        throw new Error("该目录已在白名单中。");
      }
      return false;
    }
    fileAllowlist = dedupeAllowlistPaths([...fileAllowlist, normalized]);
    renderFileAllowlist();
    syncFileAllowlistInput("");
    if (announce) {
      showToast("已添加目录。");
    }
    return true;
  }

  async function pickAllowlistDirectory() {
    setStatus("正在打开目录选择器...");
    const seed = normalizeAllowlistPath(els.fileAllowlistInput.value) || fileAllowlist[0] || "";
    const data = await fetchJson(fileAllowlistPickerEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_dir: seed }),
    });
    if (data.cancelled) {
      setStatus("已取消目录选择。");
      showToast("已取消目录选择。", "warning");
      return;
    }
    const picked = normalizeAllowlistPath(data.path || data.directory || "");
    if (!picked) {
      throw new Error(data.detail || "未获取到目录路径。");
    }
    const added = addFileAllowlistPath(picked, { quiet: true, announce: false });
    if (added) {
      setStatus("已从目录选择器添加目录。");
      showToast("已添加目录。");
    } else {
      setStatus("该目录已在白名单中。");
      showToast("该目录已在白名单中。");
    }
  }

  async function refreshEffectiveFileAllowlist() {
    setStatus("正在读取当前生效的白名单目录...");
    const data = await fetchJson("/api/settings/config");
    effectiveFileAllowlist = dedupeAllowlistPaths(data?.config?.chat?.tooling?.file_allowlist || []);
    renderEffectiveFileAllowlist();
    setStatus("已刷新当前生效的白名单目录。");
    showToast("已刷新当前生效目录。");
  }

  window.__pickFileAllowlistDirectory = () => wrapAction(pickAllowlistDirectory);
  window.__refreshEffectiveFileAllowlist = () => wrapAction(refreshEffectiveFileAllowlist);

  function normalizeBackgroundImagePath(value) {
    return String(value || "").trim();
  }

  async function pickPetBackgroundImage() {
    const seed = normalizeBackgroundImagePath(els.petBackgroundImage.value || "");
    setStatus("正在打开背景图片选择器...");
    const data = await fetchJson(petBackgroundPickerEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "image", start_path: seed }),
    });
    if (data.cancelled) {
      setStatus("已取消背景图片选择。");
      showToast("已取消背景图片选择。", "warning");
      return;
    }
    const picked = normalizeBackgroundImagePath(data.path || "");
    if (!picked) {
      throw new Error(data.detail || "未获取到背景图片路径。");
    }
    els.petBackgroundImage.value = picked;
    els.petBackgroundEnabled.checked = true;
    setStatus(`已选择背景图片：${picked}`);
    showToast("背景图片已选择。");
  }

  function clearPetBackgroundImage() {
    els.petBackgroundImage.value = "";
    els.petBackgroundEnabled.checked = false;
    setStatus("已清除桌宠背景图。");
    showToast("背景图已清除。");
  }

  function renderSummary(config, health) {
    const skillStats = getSkillStats();
    const cards = [
      ["后端", health?.ok ? "在线" : "离线"],
      ["模型", config?.model_path || "未设置"],
      ["TTS", config?.chat?.tts_provider || "edge_tts"],
      ["Router", config?.chat?.router_enabled ? "on" : "off"],
      ["第三方 MCP", config?.chat?.tooling?.third_party?.enabled ? "已启用" : "已停用"],
      ["白名单目录", `${effectiveFileAllowlist.length} 个`],
      [
        "Agent Skills",
        skillStats.total
          ? `${skillStats.defaultActiveCount}/${skillStats.total}`
          : skillStats.defaultActiveCount
            ? `${skillStats.defaultActiveCount} 个已配置`
            : "未加载",
      ],
    ];
    els.summaryCards.innerHTML = "";
    for (const [label, value] of cards) {
      const card = document.createElement("article");
      card.className = "summary-card";
      card.innerHTML = `<strong>${label}</strong><span>${value}</span>`;
      els.summaryCards.appendChild(card);
    }
  }

  function populateSelect(selectEl, items, placeholder) {
    selectEl.innerHTML = "";
    const placeholderOption = document.createElement("option");
    placeholderOption.value = "";
    placeholderOption.textContent = placeholder;
    selectEl.appendChild(placeholderOption);
    for (const item of items) {
      const option = document.createElement("option");
      option.value = item.value;
      option.textContent = item.label;
      selectEl.appendChild(option);
    }
  }

  function populateTtsPresets(presets) {
    populateSelect(
      els.ttsPresetSelect,
      Object.entries(presets || {}).map(([key, value]) => ({ value: key, label: value.label || key })),
      "选择 TTS 预设..."
    );
  }

  function populateMcpPresets(presets) {
    mcpServerPresets = presets || {};
    populateSelect(
      els.mcpPresetSelect,
      Object.entries(mcpServerPresets).map(([key, value]) => ({ value: key, label: value.label || key })),
      "选择 MCP 预设..."
    );
  }

  function findLocalModelByPath(path) {
    return localModels.find((item) => item.path === path) || null;
  }

  function selectedLocalModel() {
    return findLocalModelByPath(els.localModelSelect.value || els.modelPath.value.trim());
  }

  function renderActionList(container, items, emptyText, onClick) {
    container.innerHTML = "";
    if (!Array.isArray(items) || !items.length) {
      container.classList.add("empty-state");
      container.textContent = emptyText;
      return;
    }
    container.classList.remove("empty-state");
    for (const item of items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "action-chip";
      button.textContent = item.label || item.name || "";
      button.addEventListener("click", () => onClick(item));
      container.appendChild(button);
    }
  }

  async function postPreviewAction(payload) {
    return fetchJson("/api/settings/preview-action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  function renderLocalModelMeta() {
    const model = selectedLocalModel();
    renderActionList(els.modelMotionList, model?.motion_actions || [], "选择本地模型后显示动作", async (item) => {
      await postPreviewAction({
        type: "play_motion",
        model_path: model.path,
        group: item.group,
        index: item.index,
      });
      setStatus(`已播放动作：${item.label}`);
      showToast(`已播放动作：${item.label}`);
    });
    renderActionList(els.modelExpressionList, model?.expression_actions || [], "选择本地模型后显示表情", async (item) => {
      await postPreviewAction({
        type: "play_expression",
        model_path: model.path,
        name: item.name,
      });
      setStatus(`已播放表情：${item.label}`);
      showToast(`已播放表情：${item.label}`);
    });
  }

  function populateLocalModels() {
    populateSelect(
      els.localModelSelect,
      localModels.map((item) => ({ value: item.path, label: item.label })),
      "未找到本地模型"
    );
    const matched = findLocalModelByPath(els.modelPath.value.trim());
    els.localModelSelect.value = matched ? matched.path : "";
    renderLocalModelMeta();
  }

  function populateRemoteModels() {
    populateSelect(
      els.remoteModelSelect,
      remoteModels.map((item) => ({ value: item, label: item })),
      "尚未拉取远端模型"
    );
  }

  function populateRouterRemoteModels() {
    populateSelect(
      els.routerRemoteModelSelect,
      routerRemoteModels.map((item) => ({ value: item, label: item })),
      "Router models not loaded"
    );
  }

  function populateForm(config) {
    els.modelPath.value = config.model_path || "";
    els.chatBackendUrl.value = config.chat.backend_url || "";
    els.chatLlmProvider.value = config.chat.llm_provider || "ollama";
    els.chatApiBaseUrl.value = config.chat.api_base_url || "";
    els.chatApiKey.value = config.chat.api_key || "";
    els.chatModel.value = config.chat.model || "";
    els.chatRouterEnabled.checked = !!config.chat.router_enabled;
    els.chatRouterLlmProvider.value = config.chat.router_llm_provider || config.chat.llm_provider || "ollama";
    els.chatRouterApiBaseUrl.value = config.chat.router_api_base_url || config.chat.api_base_url || "";
    els.chatRouterApiKey.value = config.chat.router_api_key || "";
    els.chatRouterModel.value = config.chat.router_model || config.chat.model || "";
    els.chatVoice.value = config.chat.voice || "";
    els.chatSessionId.value = config.chat.session_id || "default";
    els.chatMemoryWindow.value = Number(config.chat.memory_window || 10);
    els.chatTopicHistoryEnabled.checked = config.chat?.topic_history?.enabled !== false;
    els.chatTopicHistorySummaryInterval.value = Number(config.chat?.topic_history?.summary_interval_assistant_turns || 10);
    els.chatAsrEnabled.checked = config.chat?.asr?.enabled !== false;
    els.chatAsrPushToTalkKey.value = config.chat?.asr?.push_to_talk_key || "Alt";
    els.chatAsrInterimResults.checked = config.chat?.asr?.interim_results !== false;
    els.chatTtsProvider.value = config.chat.tts_provider || "edge_tts";
    els.chatRatePct.value = Number(config.chat.rate_pct || 0);
    els.chatTtsProviderUrl.value = config.chat.tts_provider_url || "";
    els.chatSystemPrompt.value = config.chat.system_prompt || "";
    els.chatExpressionMode.checked = !!config.chat.expression_mode;
    els.chatReactEnabled.checked = config.chat.react_enabled !== false;
    els.chatReactVisibility.value = config.chat.react_visibility || "inline";
    els.chatMaxReasoningSteps.value = Number(config.chat.max_reasoning_steps || 10);
    els.toolingEnabled.checked = !!config.chat.tooling.enabled;
    els.thirdPartyEnabled.checked = !!config.chat.tooling.third_party.enabled;
    els.skillsEnabled.checked = config.chat?.skills?.enabled !== false;
    els.petScale.value = Number(config.pet.scale || 0.3);
    els.petOpacity.value = Number(config.pet.opacity || 1);
    els.petOffsetX.value = Number(config.pet.offset_x || 0);
    els.petOffsetY.value = Number(config.pet.offset_y || 0);
    els.petRotation.value = Number(config.pet.rotation || 0);
    els.petBackgroundOverlayOpacity.value = Number(config.pet.background_overlay_opacity ?? 0.42);
    els.petEditMode.checked = !!config.pet.edit_mode;
    els.petFollowMouse.checked = !!config.pet.follow_mouse;
    els.petBackgroundEnabled.checked = !!config.pet.background_enabled;
    els.petBackgroundImage.value = config.pet.background_image || "";
    els.windowX.value = Number(config.window.x || 0);
    els.windowY.value = Number(config.window.y || 0);
    els.windowWidth.value = Number(config.window.width || 420);
    els.windowHeight.value = Number(config.window.height || 640);
    els.windowLocked.checked = !!config.window.locked;
    fileAllowlist = dedupeAllowlistPaths(config.chat?.tooling?.file_allowlist || []);
    effectiveFileAllowlist = dedupeAllowlistPaths(config.chat?.tooling?.file_allowlist || []);
    skillDefaultActiveIds = dedupeSkillIds(config.chat?.skills?.default_active_ids || []);
    renderFileAllowlist();
    renderEffectiveFileAllowlist();
    renderSkillsSummary();
    renderSkillsList();
    updateRateLabel();
    populateLocalModels();
  }

  function readForm() {
    const next = clone(settingsPayload.config);
    next.model_path = els.modelPath.value.trim();
    next.chat.backend_url = els.chatBackendUrl.value.trim();
    next.chat.llm_provider = els.chatLlmProvider.value;
    next.chat.api_base_url = els.chatApiBaseUrl.value.trim();
    next.chat.api_key = els.chatApiKey.value;
    next.chat.model = els.chatModel.value.trim();
    next.chat.router_enabled = !!els.chatRouterEnabled.checked;
    next.chat.router_llm_provider = els.chatRouterLlmProvider.value;
    next.chat.router_api_base_url = els.chatRouterApiBaseUrl.value.trim();
    next.chat.router_api_key = els.chatRouterApiKey.value;
    next.chat.router_model = els.chatRouterModel.value.trim();
    next.chat.voice = els.chatVoice.value.trim();
    next.chat.session_id = els.chatSessionId.value.trim() || "default";
    next.chat.memory_window = Number(els.chatMemoryWindow.value || 10);
    next.chat.topic_history = {
      ...(next.chat.topic_history || {}),
      enabled: !!els.chatTopicHistoryEnabled.checked,
      summary_interval_assistant_turns: (() => {
        const interval = Number(els.chatTopicHistorySummaryInterval.value);
        return Number.isFinite(interval) && interval > 0 ? interval : 10;
      })(),
    };
    next.chat.asr = {
      ...(next.chat.asr || {}),
      enabled: !!els.chatAsrEnabled.checked,
      provider: "funasr",
      push_to_talk_key: els.chatAsrPushToTalkKey.value || "Alt",
      interim_results: !!els.chatAsrInterimResults.checked,
    };
    next.chat.tts_provider = els.chatTtsProvider.value;
    next.chat.rate_pct = Number(els.chatRatePct.value || 0);
    next.chat.tts_provider_url = els.chatTtsProviderUrl.value.trim();
    next.chat.system_prompt = els.chatSystemPrompt.value;
    next.chat.expression_mode = !!els.chatExpressionMode.checked;
    next.chat.react_enabled = !!els.chatReactEnabled.checked;
    next.chat.react_visibility = els.chatReactVisibility.value || "inline";
    next.chat.max_reasoning_steps = Number(els.chatMaxReasoningSteps.value || 10);
    next.chat.tooling.enabled = !!els.toolingEnabled.checked;
    next.chat.tooling.file_allowlist = dedupeAllowlistPaths(fileAllowlist);
    next.chat.tooling.third_party.enabled = !!els.thirdPartyEnabled.checked;
    next.chat.skills = {
      ...(next.chat.skills || {}),
      enabled: !!els.skillsEnabled.checked,
      default_active_ids: selectedSkillDefaultActiveIds(),
    };
    next.pet.scale = Number(els.petScale.value || 0.3);
    next.pet.opacity = Number(els.petOpacity.value || 1);
    next.pet.offset_x = Number(els.petOffsetX.value || 0);
    next.pet.offset_y = Number(els.petOffsetY.value || 0);
    next.pet.rotation = Number(els.petRotation.value || 0);
    next.pet.background_overlay_opacity = Number(els.petBackgroundOverlayOpacity.value || 0.42);
    next.pet.edit_mode = !!els.petEditMode.checked;
    next.pet.follow_mouse = !!els.petFollowMouse.checked;
    next.pet.background_enabled = !!els.petBackgroundEnabled.checked;
    next.pet.background_image = normalizeBackgroundImagePath(els.petBackgroundImage.value || "");
    next.window.x = Number(els.windowX.value || 0);
    next.window.y = Number(els.windowY.value || 0);
    next.window.width = Number(els.windowWidth.value || 420);
    next.window.height = Number(els.windowHeight.value || 640);
    next.window.locked = !!els.windowLocked.checked;
    return next;
  }

  function renderMcpServers() {
    els.mcpServerList.innerHTML = "";
    if (!mcpServers.length) {
      els.mcpServerList.classList.add("empty-state");
      els.mcpServerList.textContent = "当前还没有第三方 MCP 服务。";
      return;
    }
    els.mcpServerList.classList.remove("empty-state");
    for (const item of mcpServers) {
      const card = document.createElement("article");
      card.className = "server-card";
      const badgeClass = item.health_status === "failed" ? "failed" : item.enabled ? "" : "disabled";
      const tools = Array.isArray(item.tools) ? item.tools.map((tool) => tool?.function?.name || "").filter(Boolean) : [];
      card.innerHTML = `
        <header>
          <div class="server-title">
            <h4>${item.name || "(unnamed)"}</h4>
            <span class="server-badge ${badgeClass}">${item.health_status || "unknown"}</span>
          </div>
          <div class="server-meta">
            <span>runtime: ${item.runtime || "-"}</span>
            <span>source: ${item.source_type || "-"}</span>
            <span>install: ${item.install_status || "-"}</span>
            <span>tools: ${tools.length ? tools.join(", ") : "-"}</span>
            ${item.manifest_path ? `<span>manifest: ${item.manifest_path}</span>` : ""}
            ${item.error ? `<span>error: ${item.error}</span>` : ""}
          </div>
        </header>
        <div class="button-row wrap">
          <button type="button" class="pill-button" data-action="toggle">${item.enabled ? "停用" : "启用"}</button>
          <button type="button" class="ghost-button danger-button" data-action="delete">删除</button>
        </div>
      `;
      card.querySelector('[data-action="toggle"]').addEventListener("click", () => wrapAction(() => toggleMcpServer(item.name, !item.enabled)));
      card.querySelector('[data-action="delete"]').addEventListener("click", () => wrapAction(() => deleteMcpServer(item.name)));
      els.mcpServerList.appendChild(card);
    }
  }

  async function loadHealth() {
    try {
      return await fetchJson("/api/health");
    } catch (_) {
      return null;
    }
  }

  async function loadLocalModels() {
    const data = await fetchJson("/api/settings/models-local");
    localModels = Array.isArray(data.models) ? data.models : [];
    populateLocalModels();
  }

  async function loadMcpServers() {
    const data = await fetchJson("/api/mcp/servers");
    mcpServers = Array.isArray(data.servers) ? data.servers : [];
    renderMcpServers();
  }

  async function loadConfig() {
    setStatus("正在加载当前配置...");
    settingsPayload = await fetchJson("/api/settings/config");
    populateTtsPresets(settingsPayload.tts_presets);
    populateMcpPresets(settingsPayload.mcp_server_presets);
    populateForm(settingsPayload.config);
    renderSummary(settingsPayload.config, await loadHealth());
    setStatus("配置已加载。");
  }

  async function reloadEverything() {
    await loadConfig();
    await loadSkills();
    await loadLocalModels();
    await loadMcpServers();
    populateRemoteModels();
    populateRouterRemoteModels();
    renderSummary(settingsPayload.config, await loadHealth());
  }

  async function saveConfig() {
    setStatus("正在保存配置...");
    const draftJson = els.mcpConfigJson.value.trim();
    const draftName = els.mcpServerName.value.trim();
    if (draftJson) {
      try {
        JSON.parse(draftJson);
      } catch (error) {
        throw new Error(`MCP JSON 解析失败：${error.message || String(error)}`);
      }
    }
    settingsPayload = await fetchJson("/api/settings/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        config: readForm(),
        mcp_draft: {
          name: draftName,
          config_json: draftJson,
        },
      }),
    });
    populateTtsPresets(settingsPayload.tts_presets);
    populateMcpPresets(settingsPayload.mcp_server_presets);
    populateForm(settingsPayload.config);
    await loadSkills();
    renderSummary(settingsPayload.config, await loadHealth());
    await loadLocalModels();
    await loadMcpServers();
    setStatus("配置已保存。");
    showToast("配置已保存。");
  }

  async function fetchRemoteModels() {
    setStatus("正在拉取远端模型...");
    const data = await fetchJson("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        llm_provider: els.chatLlmProvider.value,
        api_base_url: els.chatApiBaseUrl.value.trim(),
        api_key: els.chatApiKey.value,
      }),
    });
    remoteModels = Array.isArray(data.models) ? data.models : [];
    populateRemoteModels();
    setStatus(remoteModels.length ? "远端模型已加载。" : "远端模型列表为空。");
    showToast(remoteModels.length ? "远端模型已加载。" : "没有可用的远端模型。");
  }

  async function fetchRouterModels() {
    setStatus("Fetching router models...");
    const data = await fetchJson("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        llm_provider: els.chatRouterLlmProvider.value,
        api_base_url: els.chatRouterApiBaseUrl.value.trim(),
        api_key: els.chatRouterApiKey.value,
      }),
    });
    routerRemoteModels = Array.isArray(data.models) ? data.models : [];
    populateRouterRemoteModels();
    setStatus(routerRemoteModels.length ? "Router models loaded." : "Router model list is empty.");
    showToast(routerRemoteModels.length ? "Router models loaded." : "No router models available.");
  }

  async function toggleMcpServer(name, enabled) {
    setStatus(`${enabled ? "正在启用" : "正在停用"} ${name}...`);
    await fetchJson("/api/mcp/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, enabled }),
    });
    await loadMcpServers();
    setStatus("MCP 服务状态已更新。");
    showToast(`${name} 已${enabled ? "启用" : "停用"}。`);
  }

  async function deleteMcpServer(name) {
    if (!name) {
      throw new Error("缺少 MCP 服务名称");
    }
    setStatus(`正在删除 ${name}...`);
    await fetchJson("/api/mcp/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    await reloadEverything();
    setStatus(`${name} 已删除。`);
    showToast(`${name} 已删除。`);
  }

  async function createMcpFromConfig() {
    const configJson = els.mcpConfigJson.value.trim();
    const name = els.mcpServerName.value.trim();
    if (!configJson) {
      throw new Error("请先填写 MCP server JSON 配置。");
    }
    setStatus("正在创建 MCP 服务...");
    await fetchJson("/api/mcp/create-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, config_json: configJson }),
    });
    await reloadEverything();
    setStatus("MCP 服务已创建并保存。");
    showToast("MCP 服务已创建。");
  }

  async function reloadMcp() {
    setStatus("正在重载 MCP 服务...");
    await fetchJson("/api/mcp/reload", { method: "POST" });
    await loadMcpServers();
    setStatus("MCP 服务已重载。");
    showToast("MCP 服务已重载。");
  }

  function fillSelectedLocalModel() {
    const model = selectedLocalModel();
    if (!model) {
      return;
    }
    els.modelPath.value = model.path;
    renderLocalModelMeta();
    showToast("已填入模型路径。");
  }

  async function previewSelectedLocalModel() {
    const model = selectedLocalModel();
    if (!model) {
      throw new Error("请先选择一个本地模型。");
    }
    els.modelPath.value = model.path;
    await postPreviewAction({ type: "load_model", model_path: model.path });
    setStatus(`已切换模型：${model.label}`);
    showToast("模型预览已应用。");
  }

  function applySelectedRemoteModel() {
    const value = els.remoteModelSelect.value;
    if (!value) {
      return;
    }
    els.chatModel.value = value;
    showToast("已填入远端模型。");
  }

  function applySelectedRouterRemoteModel() {
    const value = els.routerRemoteModelSelect.value;
    if (!value) {
      return;
    }
    els.chatRouterModel.value = value;
    showToast("Router model filled in.");
  }

  function applyTtsPreset() {
    const presetKey = els.ttsPresetSelect.value;
    const preset = settingsPayload?.tts_presets?.[presetKey];
    if (!preset) {
      return;
    }
    els.chatTtsProvider.value = "custom_http";
    els.chatTtsProviderUrl.value = JSON.stringify(preset.config, null, 2);
    showToast("已应用 TTS 预设。");
  }

  function applyMcpPreset() {
    const presetKey = els.mcpPresetSelect.value;
    const preset = mcpServerPresets[presetKey];
    if (!preset) {
      return;
    }
    els.mcpServerName.value = preset.name || presetKey;
    els.mcpConfigJson.value = JSON.stringify(preset.config, null, 2);
    setStatus(preset.notes || "已应用 MCP 预设。");
    showToast("已应用 MCP 预设。");
  }

  async function importSystemPromptFromJsonText(file) {
    if (!file) {
      return;
    }
    const text = await file.text();
    try {
      JSON.parse(text);
    } catch (error) {
      throw new Error(`JSON 解析失败：${error.message || String(error)}`);
    }
    els.chatSystemPrompt.value = text.trim();
    setStatus(`已导入 ${file.name}，如需保留请记得保存配置。`);
    showToast(`已导入 ${file.name}。`);
  }

  async function wrapAction(fn) {
    try {
      await fn();
    } catch (error) {
      const message = error?.message || String(error);
      reportStatusIssue(`操作失败：${message}`);
    }
  }

  async function loadSkills() {
    setSkillsPanelStatus("正在加载技能列表...");
    const result = await fetchOptionalJson("/api/skills");
    if (!result.ok) {
      skillsPayload = null;
      skillRecords = [];
      if (result.status === 404) {
        setSkillsPanelStatus("Agent Skills 接口尚未接入，当前仅显示前端面板。");
      } else {
        const detail = result.data?.detail || result.error?.message || `HTTP ${result.status || "?"}`;
        setSkillsPanelStatus(`技能列表加载失败：${detail}`);
      }
      renderSkillsSummary();
      renderSkillsList();
      return false;
    }
    skillsPayload = result.data || {};
    const rawItems = Array.isArray(skillsPayload.skills)
      ? skillsPayload.skills
      : Array.isArray(skillsPayload.items)
        ? skillsPayload.items
        : Array.isArray(skillsPayload.data)
          ? skillsPayload.data
          : [];
    skillRecords = rawItems.map(normalizeSkillRecord).filter(Boolean);
    const incomingActiveIds = skillsPayload.default_active_ids || skillsPayload.active_ids || skillsPayload.enabled_ids;
    if (Array.isArray(incomingActiveIds)) {
      skillDefaultActiveIds = dedupeSkillIds(incomingActiveIds, skillRecords.map((item) => item.id));
    } else if (skillsPayload.config?.chat?.skills?.default_active_ids) {
      skillDefaultActiveIds = dedupeSkillIds(
        skillsPayload.config.chat.skills.default_active_ids || [],
        skillRecords.map((item) => item.id)
      );
    }
    if (skillsPayload.enabled !== undefined) {
      els.skillsEnabled.checked = !!skillsPayload.enabled;
    } else if (skillsPayload.config?.chat?.skills?.enabled !== undefined) {
      els.skillsEnabled.checked = !!skillsPayload.config.chat.skills.enabled;
    }
    renderSkillsSummary();
    renderSkillsList();
    return true;
  }

  async function pickSkillLocalDirectory() {
    const seed = normalizeAllowlistPath(els.skillLocalPath.value) || fileAllowlist[0] || "";
    setStatus("正在打开技能目录选择器...");
    const data = await fetchJson(fileAllowlistPickerEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_dir: seed }),
    });
    if (data.cancelled) {
      setSkillsPanelStatus("已取消目录选择。");
      showToast("已取消目录选择。", "warning");
      return;
    }
    const picked = normalizeAllowlistPath(data.path || data.directory || "");
    if (!picked) {
      throw new Error(data.detail || "未获取到目录路径。");
    }
    els.skillLocalPath.value = picked;
    setSkillsPanelStatus(`已选择目录：${picked}`);
    showToast(`已选择目录：${picked}`);
  }

  async function importLocalSkill() {
    const path = String(els.skillLocalPath.value || "").trim();
    if (!path) {
      throw new Error("请先填写本地技能目录。");
    }
    setSkillsPanelStatus("正在导入本地技能...");
    await fetchJson("/api/skills/import-local", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, directory: path }),
    });
    await loadSkills();
    setStatus("本地技能已导入。");
    showToast("本地技能已导入。");
  }

  async function importGitSkill() {
    const url = String(els.skillGitUrl.value || "").trim();
    if (!url) {
      throw new Error("请先填写 Git 仓库地址。");
    }
    setSkillsPanelStatus("正在导入 Git 技能...");
    await fetchJson("/api/skills/import-git", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        repo_url: url,
        name: String(els.skillGitName.value || "").trim(),
        branch: String(els.skillGitRef.value || "").trim(),
        ref: String(els.skillGitRef.value || "").trim(),
        subdir: String(els.skillGitSubdir.value || "").trim(),
      }),
    });
    await loadSkills();
    setStatus("Git 技能已导入。");
    showToast("Git 技能已导入。");
  }

  async function deleteSkill(skillId, skillName) {
    const normalized = normalizeSkillId(skillId);
    if (!normalized) {
      throw new Error("缺少技能 ID。");
    }
    if (!window.confirm(`确定删除技能「${skillName || normalized}」吗？`)) {
      return;
    }
    setSkillsPanelStatus(`正在删除技能：${skillName || normalized}...`);
    await fetchJson("/api/skills/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_id: normalized, id: normalized, name: skillName || normalized }),
    });
    skillDefaultActiveIds = skillDefaultActiveIds.filter((item) => normalizeSkillId(item) !== normalized);
    await loadSkills();
    setStatus(`技能已删除：${skillName || normalized}`);
    showToast(`技能已删除：${skillName || normalized}`);
  }

  function bindEvents() {
    els.chatRatePct.addEventListener("input", updateRateLabel);
    els.modelPath.addEventListener("input", renderLocalModelMeta);
    els.localModelSelect.addEventListener("change", () => {
      if (els.localModelSelect.value) {
        els.modelPath.value = els.localModelSelect.value;
      }
      renderLocalModelMeta();
    });
    els.importSystemPromptBtn.addEventListener("click", () => els.systemPromptFileInput.click());
    els.systemPromptFileInput.addEventListener("change", () => {
      const [file] = els.systemPromptFileInput.files || [];
      wrapAction(() => importSystemPromptFromJsonText(file));
      els.systemPromptFileInput.value = "";
    });
    els.refreshLocalModelsBtn.addEventListener("click", () => wrapAction(loadLocalModels));
    els.useLocalModelBtn.addEventListener("click", fillSelectedLocalModel);
    els.previewLocalModelBtn.addEventListener("click", () => wrapAction(previewSelectedLocalModel));
    els.fileAllowlistAddBtn.addEventListener("click", () => {
      wrapAction(() => addFileAllowlistPath(els.fileAllowlistInput.value));
    });
    els.fileAllowlistPickBtn.addEventListener("click", () => wrapAction(pickAllowlistDirectory));
    els.fileAllowlistClearBtn.addEventListener("click", () => {
      fileAllowlist = [];
      renderFileAllowlist();
      syncFileAllowlistInput("");
      showToast("已清空编辑列表。");
    });
    els.fileAllowlistEffectiveBtn.addEventListener("click", () => wrapAction(refreshEffectiveFileAllowlist));
    els.skillsEnabled.addEventListener("change", () => {
      renderSkillsSummary();
    });
    els.petBackgroundPickBtn.addEventListener("click", () => wrapAction(pickPetBackgroundImage));
    els.petBackgroundClearBtn.addEventListener("click", clearPetBackgroundImage);
    els.skillLocalPickBtn.addEventListener("click", () => wrapAction(pickSkillLocalDirectory));
    els.skillLocalImportBtn.addEventListener("click", () => wrapAction(importLocalSkill));
    els.skillGitImportBtn.addEventListener("click", () => wrapAction(importGitSkill));
    els.skillLocalPath.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") {
        return;
      }
      event.preventDefault();
      wrapAction(pickSkillLocalDirectory);
    });
    els.skillGitUrl.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") {
        return;
      }
      event.preventDefault();
      wrapAction(importGitSkill);
    });
    els.fileAllowlistInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") {
        return;
      }
      event.preventDefault();
      wrapAction(() => addFileAllowlistPath(els.fileAllowlistInput.value));
    });
    els.fetchRemoteModelsBtn.addEventListener("click", () => wrapAction(fetchRemoteModels));
    els.useRemoteModelBtn.addEventListener("click", applySelectedRemoteModel);
    els.fetchRouterModelsBtn.addEventListener("click", () => wrapAction(fetchRouterModels));
    els.useRouterModelBtn.addEventListener("click", applySelectedRouterRemoteModel);
    els.applyTtsPresetBtn.addEventListener("click", applyTtsPreset);
    els.mcpApplyPresetBtn.addEventListener("click", applyMcpPreset);
    els.mcpCreateBtn.addEventListener("click", () => wrapAction(createMcpFromConfig));
    els.mcpReloadBtn.addEventListener("click", () => wrapAction(reloadMcp));
    els.reloadConfigBtn.addEventListener("click", () => wrapAction(reloadEverything));
    els.resetFormBtn.addEventListener("click", () => {
      if (!settingsPayload) {
        return;
      }
      populateForm(clone(settingsPayload.defaults));
      showToast("表单已恢复默认值，如需保留请记得保存配置。");
    });
    els.saveConfigBtn.addEventListener("click", () => wrapAction(saveConfig));
  }

  async function init() {
    installGlobalErrorHandlers();
    bindEvents();
    await wrapAction(reloadEverything);
  }

  init().catch((error) => {
    reportStatusIssue(`设置页初始化失败：${error?.message || String(error)}`);
  });
})();
