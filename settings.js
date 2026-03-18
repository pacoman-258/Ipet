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
    remoteModelSelect: $("remote-model-select"),
    fetchRemoteModelsBtn: $("fetch-remote-models-btn"),
    useRemoteModelBtn: $("use-remote-model-btn"),
    chatVoice: $("chat-voice"),
    chatSessionId: $("chat-session-id"),
    chatMemoryWindow: $("chat-memory-window"),
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
    petEditMode: $("pet-edit-mode"),
    petFollowMouse: $("pet-follow-mouse"),
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
  let mcpServers = [];
  let mcpServerPresets = {};
  let toastTimer = 0;

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function setStatus(message) {
    els.statusBanner.textContent = String(message || "");
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

  function renderSummary(config, health) {
    const cards = [
      ["Backend", health?.ok ? "Online" : "Offline"],
      ["Model", config?.model_path || "(none)"],
      ["TTS", config?.chat?.tts_provider || "edge_tts"],
      ["Third-party MCP", config?.chat?.tooling?.third_party?.enabled ? "Enabled" : "Disabled"],
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
      "Select TTS preset..."
    );
  }

  function populateMcpPresets(presets) {
    mcpServerPresets = presets || {};
    populateSelect(
      els.mcpPresetSelect,
      Object.entries(mcpServerPresets).map(([key, value]) => ({ value: key, label: value.label || key })),
      "Select MCP preset..."
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
    renderActionList(els.modelMotionList, model?.motion_actions || [], "Select a local model to show motions", async (item) => {
      await postPreviewAction({
        type: "play_motion",
        model_path: model.path,
        group: item.group,
        index: item.index,
      });
      setStatus(`Motion played: ${item.label}`);
      showToast(`Motion played: ${item.label}`);
    });
    renderActionList(els.modelExpressionList, model?.expression_actions || [], "Select a local model to show expressions", async (item) => {
      await postPreviewAction({
        type: "play_expression",
        model_path: model.path,
        name: item.name,
      });
      setStatus(`Expression played: ${item.label}`);
      showToast(`Expression played: ${item.label}`);
    });
  }

  function populateLocalModels() {
    populateSelect(
      els.localModelSelect,
      localModels.map((item) => ({ value: item.path, label: item.label })),
      "No local model found"
    );
    const matched = findLocalModelByPath(els.modelPath.value.trim());
    els.localModelSelect.value = matched ? matched.path : "";
    renderLocalModelMeta();
  }

  function populateRemoteModels() {
    populateSelect(
      els.remoteModelSelect,
      remoteModels.map((item) => ({ value: item, label: item })),
      "No remote model loaded"
    );
  }

  function populateForm(config) {
    els.modelPath.value = config.model_path || "";
    els.chatBackendUrl.value = config.chat.backend_url || "";
    els.chatLlmProvider.value = config.chat.llm_provider || "ollama";
    els.chatApiBaseUrl.value = config.chat.api_base_url || "";
    els.chatApiKey.value = config.chat.api_key || "";
    els.chatModel.value = config.chat.model || "";
    els.chatVoice.value = config.chat.voice || "";
    els.chatSessionId.value = config.chat.session_id || "default";
    els.chatMemoryWindow.value = Number(config.chat.memory_window || 10);
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
    els.petScale.value = Number(config.pet.scale || 0.3);
    els.petOpacity.value = Number(config.pet.opacity || 1);
    els.petOffsetX.value = Number(config.pet.offset_x || 0);
    els.petOffsetY.value = Number(config.pet.offset_y || 0);
    els.petRotation.value = Number(config.pet.rotation || 0);
    els.petEditMode.checked = !!config.pet.edit_mode;
    els.petFollowMouse.checked = !!config.pet.follow_mouse;
    els.windowX.value = Number(config.window.x || 0);
    els.windowY.value = Number(config.window.y || 0);
    els.windowWidth.value = Number(config.window.width || 420);
    els.windowHeight.value = Number(config.window.height || 640);
    els.windowLocked.checked = !!config.window.locked;
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
    next.chat.voice = els.chatVoice.value.trim();
    next.chat.session_id = els.chatSessionId.value.trim() || "default";
    next.chat.memory_window = Number(els.chatMemoryWindow.value || 10);
    next.chat.tts_provider = els.chatTtsProvider.value;
    next.chat.rate_pct = Number(els.chatRatePct.value || 0);
    next.chat.tts_provider_url = els.chatTtsProviderUrl.value.trim();
    next.chat.system_prompt = els.chatSystemPrompt.value;
    next.chat.expression_mode = !!els.chatExpressionMode.checked;
    next.chat.react_enabled = !!els.chatReactEnabled.checked;
    next.chat.react_visibility = els.chatReactVisibility.value || "inline";
    next.chat.max_reasoning_steps = Number(els.chatMaxReasoningSteps.value || 10);
    next.chat.tooling.enabled = !!els.toolingEnabled.checked;
    next.chat.tooling.third_party.enabled = !!els.thirdPartyEnabled.checked;
    next.pet.scale = Number(els.petScale.value || 0.3);
    next.pet.opacity = Number(els.petOpacity.value || 1);
    next.pet.offset_x = Number(els.petOffsetX.value || 0);
    next.pet.offset_y = Number(els.petOffsetY.value || 0);
    next.pet.rotation = Number(els.petRotation.value || 0);
    next.pet.edit_mode = !!els.petEditMode.checked;
    next.pet.follow_mouse = !!els.petFollowMouse.checked;
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
      els.mcpServerList.textContent = "No third-party MCP server yet.";
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
          <button type="button" class="pill-button" data-action="toggle">${item.enabled ? "Disable" : "Enable"}</button>
          <button type="button" class="ghost-button danger-button" data-action="delete">Delete</button>
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
    setStatus("Loading current config...");
    settingsPayload = await fetchJson("/api/settings/config");
    populateTtsPresets(settingsPayload.tts_presets);
    populateMcpPresets(settingsPayload.mcp_server_presets);
    populateForm(settingsPayload.config);
    renderSummary(settingsPayload.config, await loadHealth());
    setStatus("Config loaded.");
  }

  async function reloadEverything() {
    await loadConfig();
    await loadLocalModels();
    await loadMcpServers();
    populateRemoteModels();
  }

  async function saveConfig() {
    setStatus("Saving config...");
    const draftJson = els.mcpConfigJson.value.trim();
    const draftName = els.mcpServerName.value.trim();
    if (draftJson) {
      try {
        JSON.parse(draftJson);
      } catch (error) {
        throw new Error(`MCP JSON parse failed: ${error.message || String(error)}`);
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
    renderSummary(settingsPayload.config, await loadHealth());
    await loadLocalModels();
    await loadMcpServers();
    setStatus("Config saved.");
    showToast("Config saved.");
  }

  async function fetchRemoteModels() {
    setStatus("Fetching remote models...");
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
    setStatus(remoteModels.length ? "Remote models loaded." : "Remote model list is empty.");
    showToast(remoteModels.length ? "Remote models loaded." : "No remote model found.");
  }

  async function toggleMcpServer(name, enabled) {
    setStatus(`${enabled ? "Enabling" : "Disabling"} ${name}...`);
    await fetchJson("/api/mcp/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, enabled }),
    });
    await loadMcpServers();
    setStatus("MCP server status updated.");
    showToast(`${name} ${enabled ? "enabled" : "disabled"}.`);
  }

  async function deleteMcpServer(name) {
    if (!name) {
      throw new Error("Missing MCP server name");
    }
    setStatus(`Deleting ${name}...`);
    await fetchJson("/api/mcp/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    await reloadEverything();
    setStatus(`${name} deleted.`);
    showToast(`${name} deleted.`);
  }

  async function createMcpFromConfig() {
    const configJson = els.mcpConfigJson.value.trim();
    const name = els.mcpServerName.value.trim();
    if (!configJson) {
      throw new Error("Please fill MCP server JSON config first.");
    }
    setStatus("Creating MCP server...");
    await fetchJson("/api/mcp/create-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, config_json: configJson }),
    });
    await reloadEverything();
    setStatus("MCP server created and persisted.");
    showToast("MCP server created.");
  }

  async function reloadMcp() {
    setStatus("Reloading MCP servers...");
    await fetchJson("/api/mcp/reload", { method: "POST" });
    await loadMcpServers();
    setStatus("MCP servers reloaded.");
    showToast("MCP servers reloaded.");
  }

  function fillSelectedLocalModel() {
    const model = selectedLocalModel();
    if (!model) {
      return;
    }
    els.modelPath.value = model.path;
    renderLocalModelMeta();
    showToast("Model path filled.");
  }

  async function previewSelectedLocalModel() {
    const model = selectedLocalModel();
    if (!model) {
      throw new Error("Please select a local model first.");
    }
    els.modelPath.value = model.path;
    await postPreviewAction({ type: "load_model", model_path: model.path });
    setStatus(`Model switched to ${model.label}.`);
    showToast("Model preview applied.");
  }

  function applySelectedRemoteModel() {
    const value = els.remoteModelSelect.value;
    if (!value) {
      return;
    }
    els.chatModel.value = value;
    showToast("Remote model filled.");
  }

  function applyTtsPreset() {
    const presetKey = els.ttsPresetSelect.value;
    const preset = settingsPayload?.tts_presets?.[presetKey];
    if (!preset) {
      return;
    }
    els.chatTtsProvider.value = "custom_http";
    els.chatTtsProviderUrl.value = JSON.stringify(preset.config, null, 2);
    showToast("TTS preset applied.");
  }

  function applyMcpPreset() {
    const presetKey = els.mcpPresetSelect.value;
    const preset = mcpServerPresets[presetKey];
    if (!preset) {
      return;
    }
    els.mcpServerName.value = preset.name || presetKey;
    els.mcpConfigJson.value = JSON.stringify(preset.config, null, 2);
    setStatus(preset.notes || "MCP preset applied.");
    showToast("MCP preset applied.");
  }

  async function importSystemPromptFromJsonText(file) {
    if (!file) {
      return;
    }
    const text = await file.text();
    try {
      JSON.parse(text);
    } catch (error) {
      throw new Error(`JSON parse failed: ${error.message || String(error)}`);
    }
    els.chatSystemPrompt.value = text.trim();
    setStatus(`Imported ${file.name}. Remember to save config.`);
    showToast(`Imported ${file.name}.`);
  }

  async function wrapAction(fn) {
    try {
      await fn();
    } catch (error) {
      const message = error?.message || String(error);
      setStatus(`Action failed: ${message}`);
      showToast(message, "error");
    }
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
    els.fetchRemoteModelsBtn.addEventListener("click", () => wrapAction(fetchRemoteModels));
    els.useRemoteModelBtn.addEventListener("click", applySelectedRemoteModel);
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
      showToast("Form reset to defaults. Click save if you want to keep it.");
    });
    els.saveConfigBtn.addEventListener("click", () => wrapAction(saveConfig));
  }

  async function init() {
    bindEvents();
    await wrapAction(reloadEverything);
  }

  init();
})();
