(() => {
  function createChatMessagesController(deps = {}) {
    const refs = deps.refs || {};
    const runtimeDocument = deps.document || document;
    const speakerNameForRole = typeof deps.speakerNameForRole === "function" ? deps.speakerNameForRole : () => "桌宠";
    const phaseLabel = typeof deps.phaseLabel === "function" ? deps.phaseLabel : () => "";
    const resetChatTimelineState =
      typeof deps.resetChatTimelineState === "function" ? deps.resetChatTimelineState : () => {};
    const hideHumanOpsClickPreview =
      typeof deps.hideHumanOpsClickPreview === "function" ? deps.hideHumanOpsClickPreview : () => {};
    const getChatState = typeof deps.getChatState === "function" ? deps.getChatState : () => "idle";
    const getPendingRetryEdit =
      typeof deps.getPendingRetryEdit === "function" ? deps.getPendingRetryEdit : () => null;
    const setPendingRetryEdit =
      typeof deps.setPendingRetryEdit === "function" ? deps.setPendingRetryEdit : () => {};
    const getActiveApprovalId =
      typeof deps.getActiveApprovalId === "function" ? deps.getActiveApprovalId : () => "";
    const setActiveApprovalId =
      typeof deps.setActiveApprovalId === "function" ? deps.setActiveApprovalId : () => {};
    const getPendingApprovalInputTurnId =
      typeof deps.getPendingApprovalInputTurnId === "function"
        ? deps.getPendingApprovalInputTurnId
        : () => "";
    const setPendingApprovalInputTurnId =
      typeof deps.setPendingApprovalInputTurnId === "function"
        ? deps.setPendingApprovalInputTurnId
        : () => {};
    const getChatMessageSequence =
      typeof deps.getChatMessageSequence === "function" ? deps.getChatMessageSequence : () => 0;
    const setChatMessageSequence =
      typeof deps.setChatMessageSequence === "function" ? deps.setChatMessageSequence : () => {};

    const { chatMessagesEl, chatInputEl, chatInputWrapEl, chatSendEl } = refs;

    function scrollChatMessagesToBottom() {
      if (chatMessagesEl) {
        chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
      }
    }

    function findUserMessageById(messageId) {
      const targetId = String(messageId || "").trim();
      if (!targetId || !chatMessagesEl) {
        return null;
      }
      return (
        Array.from(chatMessagesEl.querySelectorAll(".msg.user")).find(
          (item) => item.dataset.chatMessageId === targetId,
        ) || null
      );
    }

    function clearPendingRetryEdit(restoreButton = true) {
      const pendingRetryEdit = getPendingRetryEdit();
      if (pendingRetryEdit?.messageId) {
        const previousEl = findUserMessageById(pendingRetryEdit.messageId);
        if (previousEl) {
          previousEl.classList.remove("is-edit-source");
        }
      }
      setPendingRetryEdit(null);
      chatInputWrapEl?.classList.remove("is-retry-editing");
      if (restoreButton && chatSendEl) {
        chatSendEl.textContent = "发送";
        chatSendEl.title = "";
      }
    }

    function beginUserMessageEditRetry(messageId) {
      const chatState = getChatState();
      if (chatState !== "idle" && chatState !== "awaiting_followup_input") {
        return;
      }
      const messageEl = findUserMessageById(messageId);
      if (!messageEl || !chatInputEl) {
        return;
      }
      const bodyEl = messageEl.querySelector(".msg-body");
      const originalText = String(bodyEl?.textContent || messageEl.dataset.chatMessageText || "");
      clearPendingRetryEdit(false);
      messageEl.classList.add("is-edit-source");
      setPendingRetryEdit({
        messageId: String(messageId || ""),
        originalText,
        assistantTurn: Number(messageEl.dataset.assistantTurn || 0) || null,
      });
      chatInputEl.value = originalText;
      chatInputWrapEl?.classList.add("is-retry-editing");
      if (chatSendEl) {
        chatSendEl.textContent = "重试";
        chatSendEl.title = "重新发送这条消息";
      }
      chatInputEl.focus();
      const end = chatInputEl.value.length;
      if (typeof chatInputEl.setSelectionRange === "function") {
        chatInputEl.setSelectionRange(end, end);
      }
    }

    function truncateChatAfterMessage(messageId) {
      const messageEl = findUserMessageById(messageId);
      if (!messageEl) {
        return { assistantTurn: null };
      }
      const assistantTurn = Number(messageEl.dataset.assistantTurn || 0) || null;
      let node = messageEl.nextElementSibling;
      while (node) {
        const next = node.nextElementSibling;
        node.remove();
        node = next;
      }
      messageEl.remove();
      resetChatTimelineState();
      hideHumanOpsClickPreview();
      setActiveApprovalId("");
      setPendingApprovalInputTurnId("");
      return { assistantTurn };
    }

    function appendMessage(role, text, options = {}) {
      const phase = String(options.phase || "").trim().toLowerCase();
      const el = runtimeDocument.createElement("article");
      const roleClass = role === "user" ? "user" : role === "error" ? "error" : "pet";
      el.className = `msg ${roleClass}${phase ? ` phase-${phase}` : ""}${options.pending ? " pending" : ""}`;
      const contentText = String(text || "");
      let messageId = String(options.messageId || "");
      if (!messageId) {
        const nextSequence = (Number(getChatMessageSequence()) || 0) + 1;
        setChatMessageSequence(nextSequence);
        messageId = `chat-message-${nextSequence}`;
      }
      const assistantTurn = Number(options.assistantTurn || 0) || null;
      if (role === "user") {
        el.dataset.chatMessageId = messageId;
        el.dataset.chatMessageRole = "user";
        el.dataset.chatMessageText = contentText;
        if (assistantTurn) {
          el.dataset.assistantTurn = String(assistantTurn);
        }
      }

      const headEl = runtimeDocument.createElement("div");
      headEl.className = "msg-head";

      const speakerEl = runtimeDocument.createElement("div");
      speakerEl.className = "speaker-pill";
      speakerEl.textContent = String(options.speakerName || speakerNameForRole(role));

      headEl.appendChild(speakerEl);
      if (phase) {
        const phaseEl = runtimeDocument.createElement("div");
        phaseEl.className = "phase-pill";
        phaseEl.textContent = phaseLabel(phase);
        headEl.appendChild(phaseEl);
      }
      if (role === "user") {
        const actionsEl = runtimeDocument.createElement("div");
        actionsEl.className = "msg-actions";
        const editButton = runtimeDocument.createElement("button");
        editButton.type = "button";
        editButton.className = "msg-edit-button";
        editButton.title = "编辑并重试";
        editButton.setAttribute("aria-label", "编辑并重试这条消息");
        editButton.textContent = "✎";
        editButton.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          beginUserMessageEditRetry(messageId);
        });
        actionsEl.appendChild(editButton);
        headEl.appendChild(actionsEl);
      }

      const bodyEl = runtimeDocument.createElement("div");
      bodyEl.className = "msg-body";
      bodyEl.textContent = contentText;

      el.appendChild(headEl);
      el.appendChild(bodyEl);
      if (chatMessagesEl) {
        chatMessagesEl.appendChild(el);
      }
      scrollChatMessagesToBottom();
      return { el, bodyEl };
    }

    function updateMessageText(messageRef, text, pending = false) {
      if (!messageRef || !messageRef.bodyEl) {
        return;
      }
      messageRef.bodyEl.textContent = String(text || "");
      messageRef.el.classList.toggle("pending", !!pending);
      scrollChatMessagesToBottom();
    }

    function appendToolList(targetEl, tools) {
      if (!targetEl || !Array.isArray(tools) || !tools.length) {
        return;
      }
      const listEl = runtimeDocument.createElement("div");
      listEl.className = "tool-list";
      for (const item of tools) {
        const chipEl = runtimeDocument.createElement("div");
        chipEl.className = "tool-chip";
        chipEl.textContent = String(item.summary || item.name || "");
        listEl.appendChild(chipEl);
      }
      targetEl.appendChild(listEl);
    }

    return {
      findUserMessageById,
      clearPendingRetryEdit,
      beginUserMessageEditRetry,
      truncateChatAfterMessage,
      appendMessage,
      updateMessageText,
      appendToolList,
    };
  }

  window.IpetChatMessages = {
    createChatMessagesController,
  };
})();
