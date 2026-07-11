(() => {
  function createChatTopicHistoryController(deps = {}) {
    const helpers = deps.helpers || {};
    const state = deps.state || {};
    const refs = deps.refs || {};
    const getMemoryMode = typeof deps.getMemoryMode === "function" ? deps.getMemoryMode : () => "persistent";
    const appendMessage = typeof deps.appendMessage === "function" ? deps.appendMessage : () => null;
    const appendAssistantHistoryBlock =
      typeof deps.appendAssistantHistoryBlock === "function" ? deps.appendAssistantHistoryBlock : () => null;
    const updateShellButtons = typeof deps.updateShellButtons === "function" ? deps.updateShellButtons : () => {};
    const openChat = typeof deps.openChat === "function" ? deps.openChat : () => {};
    const clearPendingRetryEdit = typeof deps.clearPendingRetryEdit === "function" ? deps.clearPendingRetryEdit : () => {};
    const resetChatTimelineState =
      typeof deps.resetChatTimelineState === "function" ? deps.resetChatTimelineState : () => {};
    const hideHumanOpsClickPreview =
      typeof deps.hideHumanOpsClickPreview === "function" ? deps.hideHumanOpsClickPreview : () => {};
    const closeOtherDrawer = typeof deps.closeOtherDrawer === "function" ? deps.closeOtherDrawer : () => {};

    const {
      chatHistoryDrawerEl,
      chatHistoryCurrentEl,
      chatHistoryStatusEl,
      chatHistoryListEl,
      chatMessagesEl,
    } = refs;

    let topicHistoryItems = [];
    let topicHistoryLoaded = false;
    let topicHistoryLoading = false;
    let pendingDeleteTopicId = "";
    let pendingDeleteTopicTimer = null;
    let currentTopicId = String(state.chat?.session_id || "default").trim() || "default";
    let currentTopicMeta = null;

    function backendBaseUrl() {
      return String(state.chat?.backend_url || "").replace(/\/$/, "");
    }

    function topicHistoryEnabled() {
      return true;
    }

    function setChatHistoryStatus(message) {
      if (chatHistoryStatusEl) {
        chatHistoryStatusEl.textContent = String(message || "");
      }
    }

    function normalizeTopicRecord(raw) {
      if (typeof helpers.normalizeTopicRecord === "function") {
        return helpers.normalizeTopicRecord(raw);
      }
      if (!raw || typeof raw !== "object") {
        return null;
      }
      const topicId = String(raw.topic_id || raw.id || "").trim();
      if (!topicId) {
        return null;
      }
      return {
        topic_id: topicId,
        title: String(raw.title || topicId),
        preview: String(raw.preview || ""),
        created_at: String(raw.created_at || ""),
        updated_at: String(raw.updated_at || ""),
        assistant_turn_count: Number(raw.assistant_turn_count || 0) || 0,
        supports_history_detail: raw.supports_history_detail !== false,
        supports_delete: raw.supports_delete !== false,
        persisted: raw.persisted !== false,
      };
    }

    function currentTopicLabel() {
      if (getMemoryMode() === "temporary") {
        return "临时聊天";
      }
      return currentTopicMeta?.title || currentTopicId || "未创建";
    }

    function syncTopicIndicator() {
      if (chatHistoryCurrentEl) {
        chatHistoryCurrentEl.textContent = `当前话题：${currentTopicLabel()}`;
      }
    }

    function replaceChatMessages(messages) {
      if (!chatMessagesEl) {
        return;
      }
      clearPendingRetryEdit();
      chatMessagesEl.innerHTML = "";
      resetChatTimelineState();
      hideHumanOpsClickPreview();
      for (const item of Array.isArray(messages) ? messages : []) {
        if (!item || typeof item !== "object") {
          continue;
        }
        const role = String(item.role || "").trim();
        if (role !== "user" && role !== "assistant") {
          continue;
        }
        if (role === "user") {
          appendMessage("user", String(item.content || ""), {
            speakerName: "你",
            assistantTurn: Number(item.assistant_turn || 0) || null,
          });
        } else {
          appendAssistantHistoryBlock(item);
        }
      }
    }

    function clearPendingDeleteTopic(shouldRender = true) {
      const hadPending = !!pendingDeleteTopicId;
      pendingDeleteTopicId = "";
      if (pendingDeleteTopicTimer) {
        window.clearTimeout(pendingDeleteTopicTimer);
        pendingDeleteTopicTimer = null;
      }
      if (hadPending && shouldRender) {
        renderTopicHistoryList();
      }
    }

    function armPendingDeleteTopic(topicId, topicTitle = "") {
      const normalizedTopicId = String(topicId || "").trim();
      if (!normalizedTopicId) {
        return;
      }
      const displayTitle = String(topicTitle || normalizedTopicId).trim() || normalizedTopicId;
      pendingDeleteTopicId = normalizedTopicId;
      if (pendingDeleteTopicTimer) {
        window.clearTimeout(pendingDeleteTopicTimer);
      }
      pendingDeleteTopicTimer = window.setTimeout(() => {
        pendingDeleteTopicId = "";
        pendingDeleteTopicTimer = null;
        setChatHistoryStatus(`已取消删除：${displayTitle}`);
        renderTopicHistoryList();
      }, 5000);
      renderTopicHistoryList();
      setChatHistoryStatus(`再次点击“确认删除”即可删除话题：${displayTitle}`);
    }

    function renderTopicHistoryList() {
      if (!chatHistoryListEl) {
        return;
      }
      chatHistoryListEl.innerHTML = "";
      if (getMemoryMode() === "temporary") {
        setChatHistoryStatus("临时聊天不会写入历史。");
        syncTopicIndicator();
        return;
      }
      if (!topicHistoryEnabled()) {
        setChatHistoryStatus("当前未启用话题保存。你仍然可以点击“新对话”创建临时会话。");
        syncTopicIndicator();
        return;
      }
      if (!topicHistoryItems.length) {
        setChatHistoryStatus(topicHistoryLoading ? "正在加载话题列表..." : "还没有可切换的历史话题。");
        syncTopicIndicator();
        return;
      }
      setChatHistoryStatus(`共找到 ${topicHistoryItems.length} 个已保存话题。`);
      syncTopicIndicator();
      const pendingTopic = topicHistoryItems.find((item) => item.topic_id === pendingDeleteTopicId) || null;
      for (const item of topicHistoryItems) {
        const card = document.createElement("div");
        card.className = "chat-history-item";
        if (item.topic_id === currentTopicId) {
          card.classList.add("is-active");
        }
        const mainButton = document.createElement("button");
        mainButton.type = "button";
        mainButton.className = "chat-history-main";
        const titleEl = document.createElement("strong");
        titleEl.textContent = item.title;
        const previewEl = document.createElement("div");
        previewEl.className = "chat-history-preview";
        previewEl.textContent = item.preview || "暂无摘要，打开后继续聊聊吧。";
        const metaEl = document.createElement("div");
        metaEl.className = "chat-history-meta";
        metaEl.textContent = `${item.updated_at || item.created_at || "未知时间"} | AI ${item.assistant_turn_count} 轮`;
        mainButton.appendChild(titleEl);
        mainButton.appendChild(previewEl);
        mainButton.appendChild(metaEl);
        mainButton.addEventListener("click", async () => {
          await selectTopic(item.topic_id);
        });
        card.appendChild(mainButton);
        if (item.supports_delete) {
          const deleteButton = document.createElement("button");
          deleteButton.type = "button";
          const isPendingDelete = item.topic_id === pendingDeleteTopicId;
          deleteButton.className = `chat-history-delete${isPendingDelete ? " is-danger" : ""}`;
          deleteButton.textContent = isPendingDelete ? "\u786e\u8ba4\u5220\u9664" : "\u5220\u9664";
          deleteButton.title = isPendingDelete ? "\u518d\u6b21\u70b9\u51fb\u5373\u5220\u9664\u8bdd\u9898" : "\u5220\u9664\u8bdd\u9898";
          deleteButton.setAttribute("aria-label", isPendingDelete ? "\u786e\u8ba4\u5220\u9664\u8bdd\u9898" : "\u5220\u9664\u8bdd\u9898");
          deleteButton.addEventListener("click", async (event) => {
            event.preventDefault();
            event.stopPropagation();
            await deleteTopic(item.topic_id, item.title);
          });
          card.appendChild(deleteButton);
        }
        chatHistoryListEl.appendChild(card);
      }
      if (pendingTopic) {
        setChatHistoryStatus(`\u518d\u6b21\u70b9\u51fb\u201c\u786e\u8ba4\u5220\u9664\u201d\u5373\u53ef\u5220\u9664\u8bdd\u9898\uff1a${pendingTopic.title}`);
      }
    }

    async function loadTopicHistory(force = false) {
      if (getMemoryMode() === "temporary") {
        renderTopicHistoryList();
        return [];
      }
      if (!topicHistoryEnabled()) {
        topicHistoryItems = [];
        topicHistoryLoaded = false;
        renderTopicHistoryList();
        return [];
      }
      if (topicHistoryLoaded && !force) {
        renderTopicHistoryList();
        return topicHistoryItems;
      }
      const backend = backendBaseUrl();
      if (!backend) {
        setChatHistoryStatus("后端地址未配置，无法加载历史话题。");
        return [];
      }
      topicHistoryLoading = true;
      setChatHistoryStatus("正在加载话题列表...");
      try {
        const resp = await fetch(`${backend}/api/chat/topics`);
        const payload = await resp.json();
        if (!resp.ok) {
          throw new Error(payload.detail || `HTTP ${resp.status}`);
        }
        topicHistoryItems = (Array.isArray(payload.topics) ? payload.topics : [])
          .map(normalizeTopicRecord)
          .filter(Boolean);
        topicHistoryLoaded = true;
        currentTopicMeta = topicHistoryItems.find((item) => item.topic_id === currentTopicId) || currentTopicMeta;
        renderTopicHistoryList();
        updateShellButtons();
        return topicHistoryItems;
      } catch (error) {
        topicHistoryLoaded = false;
        topicHistoryItems = [];
        setChatHistoryStatus(`历史话题加载失败：${error?.message || String(error)}`);
        renderTopicHistoryList();
        return [];
      } finally {
        topicHistoryLoading = false;
      }
    }

    async function createNewTopic(shouldOpen = true) {
      if (getMemoryMode() === "temporary") {
        const temporaryId = `temporary-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
        currentTopicId = temporaryId;
        state.chat.session_id = temporaryId;
        currentTopicMeta = normalizeTopicRecord({
          topic_id: temporaryId,
          title: "临时聊天",
          persisted: false,
          supports_history_detail: false,
          supports_delete: false,
        });
        clearPendingDeleteTopic(false);
        replaceChatMessages([]);
        closeOtherDrawer();
        toggleChatHistoryDrawer(false);
        renderTopicHistoryList();
        updateShellButtons();
        if (shouldOpen) {
          openChat();
        }
        return currentTopicMeta;
      }
      const backend = backendBaseUrl();
      if (!backend) {
        const fallbackId = `topic-${Date.now()}`;
        currentTopicId = fallbackId;
        state.chat.session_id = fallbackId;
        currentTopicMeta = normalizeTopicRecord({ topic_id: fallbackId, title: "临时会话", persisted: false });
        clearPendingDeleteTopic(false);
        replaceChatMessages([]);
        topicHistoryLoaded = false;
        if (shouldOpen) {
          openChat();
        }
        renderTopicHistoryList();
        updateShellButtons();
        return currentTopicMeta;
      }
      try {
        const resp = await fetch(`${backend}/api/chat/topics`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        const payload = await resp.json();
        if (!resp.ok) {
          throw new Error(payload.detail || `HTTP ${resp.status}`);
        }
        const topic = normalizeTopicRecord(payload.topic || { topic_id: payload.topic_id, persisted: payload.persisted });
        if (topic) {
          currentTopicId = topic.topic_id;
          state.chat.session_id = topic.topic_id;
          currentTopicMeta = topic;
        }
        clearPendingDeleteTopic(false);
        replaceChatMessages([]);
        closeOtherDrawer();
        toggleChatHistoryDrawer(false);
        topicHistoryLoaded = false;
        if (topicHistoryEnabled()) {
          await loadTopicHistory(true);
        } else {
          renderTopicHistoryList();
        }
        if (shouldOpen) {
          openChat();
        }
        return topic;
      } catch (error) {
        appendMessage("error", `创建新话题失败：${error?.message || String(error)}`, { speakerName: "系统" });
        return null;
      }
    }

    async function loadTopicDetail(topicId) {
      const backend = backendBaseUrl();
      if (!backend) {
        throw new Error("后端地址未配置");
      }
      const resp = await fetch(`${backend}/api/chat/topics/${encodeURIComponent(topicId)}`);
      const payload = await resp.json();
      if (!resp.ok || payload.ok === false) {
        throw new Error(payload.detail || `HTTP ${resp.status}`);
      }
      return payload;
    }

    async function selectTopic(topicId) {
      clearPendingDeleteTopic(false);
      try {
        const payload = await loadTopicDetail(topicId);
        const meta = normalizeTopicRecord(payload.meta || { topic_id: topicId });
        currentTopicId = String(topicId || "").trim() || currentTopicId;
        state.chat.session_id = currentTopicId;
        currentTopicMeta = meta;
        replaceChatMessages(payload.history_unavailable ? [] : Array.isArray(payload.messages) ? payload.messages : []);
        if (payload.history_unavailable) {
          appendMessage("error", payload.detail || "当前运行时暂时无法同步该会话历史。", {
            speakerName: "系统",
          });
        }
        toggleChatHistoryDrawer(false);
        openChat();
        renderTopicHistoryList();
        updateShellButtons();
      } catch (error) {
        appendMessage("error", `读取历史话题失败：${error?.message || String(error)}`, { speakerName: "系统" });
      }
    }

    async function deleteTopic(topicId, topicTitle = "") {
      const normalizedTopicId = String(topicId || "").trim();
      if (!normalizedTopicId) {
        return;
      }
      const backend = backendBaseUrl();
      if (!backend) {
        setChatHistoryStatus("\u540e\u7aef\u5730\u5740\u672a\u914d\u7f6e\uff0c\u65e0\u6cd5\u5220\u9664\u5386\u53f2\u8bdd\u9898\u3002");
        return;
      }
      const displayTitle = String(topicTitle || normalizedTopicId).trim() || normalizedTopicId;
      const topicMeta = topicHistoryItems.find((item) => item.topic_id === normalizedTopicId) || null;
      if (topicMeta && !topicMeta.supports_delete) {
        setChatHistoryStatus("该话题当前不支持同步删除。");
        return;
      }
      if (pendingDeleteTopicId !== normalizedTopicId) {
        armPendingDeleteTopic(normalizedTopicId, displayTitle);
        return;
      }
      clearPendingDeleteTopic(false);
      setChatHistoryStatus(`正在删除话题：${displayTitle}...`);
      const parsePayload = async (resp) => {
        const rawText = await resp.text();
        if (!rawText) {
          return {};
        }
        try {
          return JSON.parse(rawText);
        } catch {
          return { detail: rawText };
        }
      };
      try {
        let resp = await fetch(`${backend}/api/chat/topics/${encodeURIComponent(normalizedTopicId)}`, {
          method: "DELETE",
        });
        let payload = await parsePayload(resp);
        if (resp.status === 405) {
          resp = await fetch(`${backend}/api/chat/topics/${encodeURIComponent(normalizedTopicId)}/delete`, {
            method: "POST",
          });
          payload = await parsePayload(resp);
        }
        if (Array.isArray(payload.topics)) {
          topicHistoryItems = payload.topics.map(normalizeTopicRecord).filter(Boolean);
          topicHistoryLoaded = true;
          renderTopicHistoryList();
          updateShellButtons();
        }
        if (!resp.ok || payload.ok === false) {
          throw new Error(payload.detail || `HTTP ${resp.status}`);
        }
        clearPendingDeleteTopic(false);
        if (!Array.isArray(payload.topics)) {
          topicHistoryItems = [];
          topicHistoryLoaded = true;
        }
        if (currentTopicId === normalizedTopicId) {
          if (topicHistoryItems.length) {
            await selectTopic(topicHistoryItems[0].topic_id);
          } else {
            currentTopicId = "";
            state.chat.session_id = "";
            currentTopicMeta = null;
            await createNewTopic(false);
          }
        } else {
          renderTopicHistoryList();
          updateShellButtons();
        }
        setChatHistoryStatus(`\u5df2\u5220\u9664\u8bdd\u9898\uff1a${displayTitle}`);
      } catch (error) {
        clearPendingDeleteTopic(false);
        setChatHistoryStatus(`\u5220\u9664\u5386\u53f2\u8bdd\u9898\u5931\u8d25\uff1a${error?.message || String(error)}\u3002`);
        renderTopicHistoryList();
      }
    }

    async function ensureTopicReady() {
      if (currentTopicId && currentTopicId !== "default") {
        state.chat.session_id = currentTopicId;
        return currentTopicId;
      }
      if (chatMessagesEl && chatMessagesEl.childElementCount > 0) {
        return state.chat.session_id || currentTopicId || "default";
      }
      const topic = await createNewTopic(false);
      return topic?.topic_id || state.chat.session_id || currentTopicId || "default";
    }

    function toggleChatHistoryDrawer(forceOpen = null) {
      if (!chatHistoryDrawerEl) {
        return;
      }
      if (!topicHistoryEnabled()) {
        chatHistoryDrawerEl.classList.remove("open");
        updateShellButtons();
        renderTopicHistoryList();
        return;
      }
      const shouldOpen = forceOpen === null ? !chatHistoryDrawerEl.classList.contains("open") : !!forceOpen;
      chatHistoryDrawerEl.classList.toggle("open", shouldOpen);
      if (shouldOpen) {
        closeOtherDrawer();
        loadTopicHistory(false);
      }
      updateShellButtons();
    }

    function syncTopicFromConfig() {
      currentTopicId = String(state.chat?.session_id || currentTopicId || "default").trim() || "default";
      currentTopicMeta = topicHistoryItems.find((item) => item.topic_id === currentTopicId) || currentTopicMeta;
      if (!topicHistoryEnabled() && chatHistoryDrawerEl) {
        chatHistoryDrawerEl.classList.remove("open");
      }
      syncTopicIndicator();
      return currentTopicId;
    }

    function syncTopicFromPayload(topicId) {
      if (getMemoryMode() === "temporary") {
        return currentTopicId;
      }
      const normalizedTopicId = String(topicId || "").trim();
      if (!normalizedTopicId) {
        return currentTopicId;
      }
      currentTopicId = normalizedTopicId;
      state.chat.session_id = currentTopicId;
      currentTopicMeta = topicHistoryItems.find((item) => item.topic_id === currentTopicId) || currentTopicMeta;
      syncTopicIndicator();
      return currentTopicId;
    }

    function handleDoneTopic(payload = {}) {
      return syncTopicFromPayload(payload.topic_id);
    }

    return {
      topicHistoryEnabled,
      setChatHistoryStatus,
      normalizeTopicRecord,
      currentTopicLabel,
      syncTopicIndicator,
      replaceChatMessages,
      clearPendingDeleteTopic,
      armPendingDeleteTopic,
      renderTopicHistoryList,
      loadTopicHistory,
      createNewTopic,
      loadTopicDetail,
      selectTopic,
      deleteTopic,
      ensureTopicReady,
      toggleChatHistoryDrawer,
      syncTopicFromConfig,
      syncTopicFromPayload,
      handleDoneTopic,
      currentTopicId: () => currentTopicId,
      currentTopicMeta: () => currentTopicMeta,
    };
  }

  window.IpetChatTopicHistory = {
    createChatTopicHistoryController,
  };
})();
