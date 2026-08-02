(() => {
  const STORAGE_KEY = "desktopPet.chatPanel.v2";

  function createChatPanelController(deps = {}) {
    const refs = deps.refs || {};
    const runtimeWindow = deps.window || window;
    const setChatStateValue = typeof deps.setChatStateValue === "function" ? deps.setChatStateValue : () => {};
    const syncChatInputAvailability =
      typeof deps.syncChatInputAvailability === "function" ? deps.syncChatInputAvailability : () => {};
    const updateShellButtons = typeof deps.updateShellButtons === "function" ? deps.updateShellButtons : () => {};
    const loadChatSkills = typeof deps.loadChatSkills === "function" ? deps.loadChatSkills : () => {};
    const renderTopicHistoryList =
      typeof deps.renderTopicHistoryList === "function" ? deps.renderTopicHistoryList : () => {};
    const setAsrStatus = typeof deps.setAsrStatus === "function" ? deps.setAsrStatus : () => {};
    const defaultAsrStatusText =
      typeof deps.defaultAsrStatusText === "function" ? deps.defaultAsrStatusText : () => "";
    const isAsrBusy = typeof deps.isAsrBusy === "function" ? deps.isAsrBusy : () => false;
    const cancelAsrSession = typeof deps.cancelAsrSession === "function" ? deps.cancelAsrSession : () => {};
    const toggleChatSkillsDrawer =
      typeof deps.toggleChatSkillsDrawer === "function" ? deps.toggleChatSkillsDrawer : () => {};
    const getPetBounds = typeof deps.getPetBounds === "function" ? deps.getPetBounds : () => null;

    const {
      chatPanelEl,
      chatInputEl,
      chatHistoryDrawerEl,
      chatHeaderEl,
      chatCloseEl,
      chatResizerRightEl,
      chatResizerBottomEl,
      chatResizerCornerEl,
    } = refs;

    let chatPanelPositioned = false;
    let chatDragging = false;
    let chatResizing = false;
    let chatResizeMode = "";
    let dragStartX = 0;
    let dragStartY = 0;
    let panelStartLeft = 0;
    let panelStartTop = 0;
    let panelStartW = 0;
    let panelStartH = 0;
    let interactionsInstalled = false;

    function numberOrDefault(value, fallback) {
      const next = Number(value);
      return Number.isFinite(next) ? next : fallback;
    }

    function viewportWidth() {
      return Math.max(Number(runtimeWindow.innerWidth || 0) || 0, 0);
    }

    function viewportHeight() {
      return Math.max(Number(runtimeWindow.innerHeight || 0) || 0, 0);
    }

    function capturePointer(target, pointerId) {
      if (target && typeof target.setPointerCapture === "function") {
        target.setPointerCapture(pointerId);
      }
    }

    function loadChatPanelState() {
      try {
        const raw = runtimeWindow.localStorage.getItem(STORAGE_KEY);
        if (!raw) {
          return null;
        }
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === "object" ? parsed : null;
      } catch (_) {
        return null;
      }
    }

    function saveChatPanelState() {
      if (!chatPanelPositioned || !chatPanelEl) {
        return;
      }
      const payload = {
        left: chatPanelEl.offsetLeft,
        top: chatPanelEl.offsetTop,
        width: chatPanelEl.offsetWidth,
        height: chatPanelEl.offsetHeight,
      };
      try {
        runtimeWindow.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
      } catch (_) {}
    }

    function panelRectFromState(saved) {
      const width = viewportWidth();
      const height = viewportHeight();
      const petBounds = getPetBounds();
      const w = Math.min(
        Math.max(numberOrDefault(saved?.width, Math.min(480, width - 24)), 300),
        Math.max(300, width - 12),
      );
      const h = Math.min(
        Math.max(numberOrDefault(saved?.height, Math.min(440, Math.floor(height * 0.62))), 280),
        Math.max(280, height - 12),
      );
      const defaultLeft = petBounds
        ? petBounds.left + petBounds.width * 0.5 - w * 0.5
        : (width - w) * 0.5;
      const defaultTop = petBounds ? petBounds.top - h - 16 : 12;
      const left = Math.min(
        Math.max(numberOrDefault(saved?.left, defaultLeft), 8),
        Math.max(8, width - w - 8),
      );
      const top = Math.min(
        Math.max(numberOrDefault(saved?.top, defaultTop), 8),
        Math.max(8, height - h - 8),
      );
      return { left, top, width: w, height: h };
    }

    function ensureChatPanelPositioned() {
      if (chatPanelPositioned || !chatPanelEl) {
        return;
      }
      const rect = panelRectFromState(loadChatPanelState());
      chatPanelEl.style.width = `${rect.width}px`;
      chatPanelEl.style.height = `${rect.height}px`;
      chatPanelEl.style.left = `${rect.left}px`;
      chatPanelEl.style.top = `${rect.top}px`;
      chatPanelPositioned = true;
    }

    function setChatState(next) {
      setChatStateValue(next);
      syncChatInputAvailability();
    }

    function openChat() {
      if (!chatPanelEl) {
        return;
      }
      ensureChatPanelPositioned();
      chatPanelEl.classList.add("open");
      updateShellButtons();
      loadChatSkills(false);
      renderTopicHistoryList();
      if (chatInputEl && typeof chatInputEl.focus === "function") {
        chatInputEl.focus();
      }
      setAsrStatus(defaultAsrStatusText(), "idle");
      syncChatInputAvailability();
    }

    function closeChat() {
      if (!chatPanelEl) {
        return;
      }
      if (isAsrBusy()) {
        cancelAsrSession({ restoreInput: true, statusMessage: defaultAsrStatusText(), tone: "idle" });
      }
      chatPanelEl.classList.remove("open");
      if (chatHistoryDrawerEl) {
        chatHistoryDrawerEl.classList.remove("open");
      }
      updateShellButtons();
      toggleChatSkillsDrawer(false);
      saveChatPanelState();
    }

    function beginResize(event, mode) {
      chatResizing = true;
      chatResizeMode = mode;
      dragStartX = event.clientX;
      dragStartY = event.clientY;
      panelStartW = chatPanelEl.offsetWidth;
      panelStartH = chatPanelEl.offsetHeight;
      panelStartLeft = chatPanelEl.offsetLeft;
      panelStartTop = chatPanelEl.offsetTop;
      capturePointer(event.target, event.pointerId);
      event.preventDefault();
    }

    function handlePointerMove(event) {
      if (chatDragging) {
        const dx = event.clientX - dragStartX;
        const dy = event.clientY - dragStartY;
        const left = Math.min(
          Math.max(panelStartLeft + dx, 0),
          Math.max(0, viewportWidth() - chatPanelEl.offsetWidth),
        );
        const top = Math.min(
          Math.max(panelStartTop + dy, 0),
          Math.max(0, viewportHeight() - chatPanelEl.offsetHeight),
        );
        chatPanelEl.style.left = `${left}px`;
        chatPanelEl.style.top = `${top}px`;
      } else if (chatResizing) {
        const dx = event.clientX - dragStartX;
        const dy = event.clientY - dragStartY;
        const baseLeft = panelStartLeft;
        const baseTop = panelStartTop;
        let w = panelStartW;
        let h = panelStartH;
        if (chatResizeMode === "right" || chatResizeMode === "corner") {
          w = Math.min(Math.max(panelStartW + dx, 300), Math.max(300, viewportWidth() - baseLeft));
        }
        if (chatResizeMode === "bottom" || chatResizeMode === "corner") {
          h = Math.min(Math.max(panelStartH + dy, 280), Math.max(280, viewportHeight() - baseTop));
        }
        chatPanelEl.style.width = `${w}px`;
        chatPanelEl.style.height = `${h}px`;
      }
    }

    function handlePointerUp() {
      if (chatDragging || chatResizing) {
        saveChatPanelState();
      }
      chatDragging = false;
      chatResizing = false;
      chatResizeMode = "";
    }

    function installChatPanelInteractions() {
      if (interactionsInstalled || !chatPanelEl) {
        return;
      }
      interactionsInstalled = true;

      if (chatHeaderEl) {
        chatHeaderEl.addEventListener("pointerdown", (event) => {
          if (event.target === chatCloseEl) {
            return;
          }
          chatDragging = true;
          dragStartX = event.clientX;
          dragStartY = event.clientY;
          panelStartLeft = chatPanelEl.offsetLeft;
          panelStartTop = chatPanelEl.offsetTop;
          capturePointer(chatHeaderEl, event.pointerId);
          event.preventDefault();
        });
      }

      if (chatResizerRightEl) {
        chatResizerRightEl.addEventListener("pointerdown", (event) => beginResize(event, "right"));
      }
      if (chatResizerBottomEl) {
        chatResizerBottomEl.addEventListener("pointerdown", (event) => beginResize(event, "bottom"));
      }
      if (chatResizerCornerEl) {
        chatResizerCornerEl.addEventListener("pointerdown", (event) => beginResize(event, "corner"));
      }
      runtimeWindow.addEventListener("pointermove", handlePointerMove);
      runtimeWindow.addEventListener("pointerup", handlePointerUp);
    }

    return {
      loadChatPanelState,
      saveChatPanelState,
      setChatState,
      openChat,
      closeChat,
      installChatPanelInteractions,
    };
  }

  window.IpetChatPanel = {
    createChatPanelController,
  };
})();
