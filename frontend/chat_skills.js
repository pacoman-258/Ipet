(() => {
  function createChatSkillsController(deps = {}) {
    const helpers = deps.helpers || {};
    const state = deps.state || {};
    const refs = deps.refs || {};
    const getChatMode = typeof deps.getChatMode === "function" ? deps.getChatMode : () => "react";
    const updateShellButtons = typeof deps.updateShellButtons === "function" ? deps.updateShellButtons : () => {};
    const applyChatModeUI = typeof deps.applyChatModeUI === "function" ? deps.applyChatModeUI : () => {};
    const closeOtherDrawer = typeof deps.closeOtherDrawer === "function" ? deps.closeOtherDrawer : () => {};

    const { chatSkillsDrawerEl, chatSkillsStatusEl, chatSkillsListEl } = refs;

    let chatSkillCatalog = [];
    let chatSkillDefaultIds = [];
    let chatSelectedSkillIds = [];
    let chatSkillsLoaded = false;

    function dedupeSkillIds(values, allowedIds = null) {
      if (typeof helpers.dedupeSkillIds === "function") {
        return helpers.dedupeSkillIds(values, allowedIds);
      }
      const allowed = Array.isArray(allowedIds) ? new Set(allowedIds.map((item) => String(item || "").trim())) : null;
      const seen = new Set();
      const result = [];
      for (const value of Array.isArray(values) ? values : []) {
        const normalized = String(value || "").trim();
        if (!normalized || seen.has(normalized) || (allowed && !allowed.has(normalized))) {
          continue;
        }
        seen.add(normalized);
        result.push(normalized);
      }
      return result;
    }

    function backendBaseUrl() {
      return String(state.chat?.backend_url || "").replace(/\/$/, "");
    }

    function normalizeSkillRecord(raw) {
      if (typeof helpers.normalizeSkillRecord === "function") {
        return helpers.normalizeSkillRecord(raw);
      }
      if (!raw || typeof raw !== "object") {
        return null;
      }
      const id = String(raw.id || raw.name || "").trim();
      if (!id) {
        return null;
      }
      return {
        id,
        name: String(raw.name || id),
        description: String(raw.description || ""),
        builtin: !!raw.builtin,
        scriptCount: Number(raw.script_count || 0) || 0,
        allowlistCount: Number(raw.allowlist_count || 0) || 0,
      };
    }

    function setChatSkillsStatus(message) {
      if (chatSkillsStatusEl) {
        chatSkillsStatusEl.textContent = String(message || "");
      }
    }

    function activeChatSkillIds() {
      if (state.chat?.skills?.enabled === false) {
        return [];
      }
      const allowed = chatSkillCatalog.map((item) => item.id);
      if (!allowed.length) {
        return dedupeSkillIds(chatSelectedSkillIds);
      }
      return dedupeSkillIds(chatSelectedSkillIds, allowed);
    }

    function renderChatSkillDrawer() {
      if (!chatSkillsListEl) {
        return;
      }
      chatSkillsListEl.innerHTML = "";
      if (!chatSkillCatalog.length) {
        chatSkillsListEl.innerHTML = '<div class="chat-skill-card"><p>当前没有可用技能，或后端还没有提供 `/api/skills` 接口。</p></div>';
        return;
      }
      const activeSet = new Set(activeChatSkillIds());
      for (const item of chatSkillCatalog) {
        const card = document.createElement("article");
        card.className = "chat-skill-card";
        card.innerHTML = `
          <h4>${item.name}</h4>
          <p>${item.description || "暂无描述。"}</p>
          <div class="chat-skill-meta">${item.builtin ? "内置" : "第三方"} | 脚本 ${item.scriptCount} | 工具白名单 ${item.allowlistCount}</div>
        `;
        const label = document.createElement("label");
        label.className = "chat-skill-toggle";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.checked = activeSet.has(item.id);
        input.addEventListener("change", () => {
          const next = new Set(activeChatSkillIds());
          if (input.checked) {
            next.add(item.id);
          } else {
            next.delete(item.id);
          }
          chatSelectedSkillIds = dedupeSkillIds([...next], chatSkillCatalog.map((entry) => entry.id));
          applyChatModeUI();
        });
        const text = document.createElement("span");
        text.textContent = "本轮/本会话启用";
        label.appendChild(input);
        label.appendChild(text);
        card.appendChild(label);
        chatSkillsListEl.appendChild(card);
      }
    }

    async function loadChatSkills(force = false) {
      if (chatSkillsLoaded && !force) {
        return;
      }
      const backend = backendBaseUrl();
      if (!backend) {
        setChatSkillsStatus("后端地址未配置，无法加载技能。");
        return;
      }
      setChatSkillsStatus("正在加载技能列表...");
      try {
        const resp = await fetch(`${backend}/api/skills`);
        const payload = await resp.json();
        if (!resp.ok) {
          throw new Error(payload.detail || `HTTP ${resp.status}`);
        }
        const rawItems = Array.isArray(payload.skills)
          ? payload.skills
          : Array.isArray(payload.items)
            ? payload.items
            : [];
        chatSkillCatalog = rawItems.map(normalizeSkillRecord).filter(Boolean);
        chatSkillDefaultIds = dedupeSkillIds(
          payload.default_active_ids || state.chat?.skills?.default_active_ids || [],
          chatSkillCatalog.map((item) => item.id),
        );
        if (!chatSelectedSkillIds.length || force) {
          chatSelectedSkillIds = chatSkillDefaultIds.slice();
        } else {
          chatSelectedSkillIds = dedupeSkillIds(chatSelectedSkillIds, chatSkillCatalog.map((item) => item.id));
        }
        chatSkillsLoaded = true;
        renderChatSkillDrawer();
        applyChatModeUI();
      } catch (error) {
        chatSkillCatalog = [];
        chatSkillDefaultIds = [];
        chatSelectedSkillIds = [];
        chatSkillsLoaded = false;
        setChatSkillsStatus(`技能加载失败：${error?.message || String(error)}`);
        renderChatSkillDrawer();
      }
    }

    function toggleChatSkillsDrawer(forceOpen = null) {
      if (!chatSkillsDrawerEl) {
        return;
      }
      if (getChatMode() === "chat") {
        setChatSkillsStatus("\u5f53\u524d\u662f\u804a\u5929\u6a21\u5f0f\uff0c\u53ea\u5141\u8bb8\u8054\u7f51\u641c\u7d22\u5de5\u5177\uff0c\u4e0d\u4f7f\u7528\u6280\u80fd\u3002");
        chatSkillsDrawerEl.classList.remove("open");
        updateShellButtons();
        return;
      }
      const shouldOpen = forceOpen === null ? !chatSkillsDrawerEl.classList.contains("open") : !!forceOpen;
      chatSkillsDrawerEl.classList.toggle("open", shouldOpen);
      if (shouldOpen) {
        closeOtherDrawer();
        loadChatSkills(false);
      }
      updateShellButtons();
    }

    function resetChatSkillsToDefault() {
      chatSelectedSkillIds = chatSkillDefaultIds.slice();
      renderChatSkillDrawer();
      applyChatModeUI();
    }

    function setConfiguredSkillDefaults(defaultIds = []) {
      chatSkillDefaultIds = dedupeSkillIds(defaultIds);
      if (!chatSelectedSkillIds.length) {
        chatSelectedSkillIds = chatSkillDefaultIds.slice();
      }
      chatSkillsLoaded = false;
    }

    return {
      normalizeSkillRecord,
      setChatSkillsStatus,
      renderChatSkillDrawer,
      loadChatSkills,
      toggleChatSkillsDrawer,
      resetChatSkillsToDefault,
      activeChatSkillIds,
      setConfiguredSkillDefaults,
      chatSkillsLoaded: () => chatSkillsLoaded,
    };
  }

  window.IpetChatSkills = {
    createChatSkillsController,
  };
})();
