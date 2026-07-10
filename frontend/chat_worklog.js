(() => {
  function createChatWorklogController(deps = {}) {
    const helpers = deps.helpers || window.IpetIndexHelpers || {};
    const refs = deps.refs || {};
    const runtimeDocument = deps.document || document;
    const speakerNameForRole = typeof deps.speakerNameForRole === "function" ? deps.speakerNameForRole : () => "桌宠";
    const appendMessage = typeof deps.appendMessage === "function" ? deps.appendMessage : () => null;
    const appendToolList = typeof deps.appendToolList === "function" ? deps.appendToolList : () => {};
    const setChatState = typeof deps.setChatState === "function" ? deps.setChatState : () => {};
    const continueApproval = typeof deps.continueApproval === "function" ? deps.continueApproval : async () => {};
    const showHumanOpsClickPreview =
      typeof deps.showHumanOpsClickPreview === "function" ? deps.showHumanOpsClickPreview : () => {};
    const hideHumanOpsClickPreview =
      typeof deps.hideHumanOpsClickPreview === "function" ? deps.hideHumanOpsClickPreview : () => {};
    const getActiveApprovalId =
      typeof deps.getActiveApprovalId === "function" ? deps.getActiveApprovalId : () => "";
    const setActiveApprovalId =
      typeof deps.setActiveApprovalId === "function" ? deps.setActiveApprovalId : () => {};
    const getPendingApprovalInputTurnId =
      typeof deps.getPendingApprovalInputTurnId === "function" ? deps.getPendingApprovalInputTurnId : () => "";
    const setPendingApprovalInputTurnId =
      typeof deps.setPendingApprovalInputTurnId === "function" ? deps.setPendingApprovalInputTurnId : () => {};

    const { chatMessagesEl, chatInputEl } = refs;

    let currentThoughtGroup = null;
    let currentAssistantWorklog = null;
    let assistantWorklogCounter = 0;
    let activeThoughtSessionId = "";
    let thoughtSessionCounter = 0;
    const approvalBubbleRefs = new Map();
    const approvalThoughtSessionRefs = new Map();

    function scrollChatMessagesToBottom() {
      if (!chatMessagesEl) {
        return;
      }
      chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
    }

    function appendChatMessageElement(el) {
      if (chatMessagesEl && el) {
        chatMessagesEl.appendChild(el);
      }
      scrollChatMessagesToBottom();
    }

    function phaseLabel(phase) {
      const value = String(phase || "").trim().toLowerCase();
      if (value === "thought") {
        return "思考中";
      }
      if (value === "action") {
        return "行动中";
      }
      if (value === "done") {
        return "已完成";
      }
      return "";
    }

    function thoughtRoleLabel(role) {
      const value = String(role || "").trim().toLowerCase();
      if (value === "decider") {
        return "决策";
      }
      if (value === "searcher") {
        return "搜索";
      }
      if (value === "planner") {
        return "规划";
      }
      if (value === "writer") {
        return "写作";
      }
      return "思考";
    }

    function formatStepMeta(payload) {
      if (typeof helpers.formatStepMeta === "function") {
        return helpers.formatStepMeta(payload);
      }
      return "";
    }

    function removeApprovalBubble(turnId) {
      const key = String(turnId || "").trim();
      if (!key) {
        return;
      }
      const ref = approvalBubbleRefs.get(key);
      if (ref && ref.el && ref.el.parentNode) {
        ref.el.remove();
      }
      approvalBubbleRefs.delete(key);
      hideHumanOpsClickPreview();
    }

    function resetChatTimelineState() {
      currentThoughtGroup = null;
      currentAssistantWorklog = null;
      approvalBubbleRefs.clear();
      approvalThoughtSessionRefs.clear();
    }

    function createThoughtSessionId() {
      thoughtSessionCounter += 1;
      return `thought-session-${thoughtSessionCounter}`;
    }

    function beginThoughtSession(existingSessionId = "") {
      const nextSessionId = String(existingSessionId || "").trim() || createThoughtSessionId();
      activeThoughtSessionId = nextSessionId;
      return nextSessionId;
    }

    function clearActiveThoughtSession(sessionId = "") {
      const normalizedSessionId = String(sessionId || "").trim();
      if (normalizedSessionId && activeThoughtSessionId && activeThoughtSessionId !== normalizedSessionId) {
        return;
      }
      activeThoughtSessionId = "";
      currentThoughtGroup = null;
    }

    function buildThoughtGroup(round, speakerName, pending = false, sessionId = "") {
      const el = runtimeDocument.createElement("article");
      el.className = `msg pet phase-thought thought-group${pending ? " pending" : ""}`;

      const headEl = runtimeDocument.createElement("div");
      headEl.className = "msg-head";

      const speakerEl = runtimeDocument.createElement("div");
      speakerEl.className = "speaker-pill";
      speakerEl.textContent = String(speakerName || speakerNameForRole("pet"));
      headEl.appendChild(speakerEl);

      const phaseEl = runtimeDocument.createElement("div");
      phaseEl.className = "phase-pill";
      phaseEl.textContent = `思考中：当前第${round}轮`;
      headEl.appendChild(phaseEl);

      const bodyEl = runtimeDocument.createElement("div");
      bodyEl.className = "msg-body";

      const hintEl = runtimeDocument.createElement("div");
      hintEl.className = "thought-group-hint";
      hintEl.textContent = "这轮思路会整理在下面，点开就能看细节。";

      const detailsEl = runtimeDocument.createElement("details");
      detailsEl.className = "thought-details";

      const summaryEl = runtimeDocument.createElement("summary");
      summaryEl.textContent = "查看思考详情";

      const itemsEl = runtimeDocument.createElement("div");
      itemsEl.className = "thought-items";

      detailsEl.appendChild(summaryEl);
      detailsEl.appendChild(itemsEl);
      bodyEl.appendChild(hintEl);
      bodyEl.appendChild(detailsEl);

      el.appendChild(headEl);
      el.appendChild(bodyEl);
      appendChatMessageElement(el);
      return { el, bodyEl, hintEl, detailsEl, itemsEl, round, hasItems: false, sessionId: String(sessionId || "").trim() };
    }

    function ensureThoughtGroup(round = 1, pending = false, sessionId = "") {
      const normalizedRound = Math.max(1, Number(round) || 1);
      const normalizedSessionId = String(sessionId || activeThoughtSessionId || "").trim() || beginThoughtSession();
      if (
        currentThoughtGroup &&
        currentThoughtGroup.round === normalizedRound &&
        currentThoughtGroup.sessionId === normalizedSessionId
      ) {
        currentThoughtGroup.el.classList.toggle("pending", !!pending);
        return currentThoughtGroup;
      }
      if (currentThoughtGroup && !currentThoughtGroup.hasItems && currentThoughtGroup.el && currentThoughtGroup.el.parentNode) {
        currentThoughtGroup.el.remove();
      }
      currentThoughtGroup = buildThoughtGroup(normalizedRound, speakerNameForRole("pet"), pending, normalizedSessionId);
      return currentThoughtGroup;
    }

    function removeEmptyPendingThoughtGroup(sessionId = "") {
      if (!currentThoughtGroup || currentThoughtGroup.hasItems) {
        return;
      }
      const normalizedSessionId = String(sessionId || "").trim();
      if (normalizedSessionId && currentThoughtGroup.sessionId !== normalizedSessionId) {
        return;
      }
      if (currentThoughtGroup.el && currentThoughtGroup.el.parentNode) {
        currentThoughtGroup.el.remove();
      }
      currentThoughtGroup = null;
    }

    function appendThoughtPhase(payload, sessionId = "") {
      const round = Math.max(1, Number(payload?.loop_round) || (currentThoughtGroup ? currentThoughtGroup.round : 1));
      const group = ensureThoughtGroup(round, false, sessionId);
      group.el.classList.remove("pending");
      group.hintEl.textContent = `这轮已经整理出 ${group.hasItems ? "更多" : "一些"}思路啦，点开就能看细节。`;

      const itemEl = runtimeDocument.createElement("div");
      itemEl.className = "thought-item";

      const roleEl = runtimeDocument.createElement("div");
      roleEl.className = "thought-role-pill";
      roleEl.textContent = thoughtRoleLabel(payload?.thought_role);

      const textEl = runtimeDocument.createElement("div");
      textEl.className = "thought-item-text";
      const metaText = formatStepMeta(payload);
      textEl.textContent = metaText ? `${metaText}\n${String(payload?.text || "")}` : String(payload?.text || "");

      itemEl.appendChild(roleEl);
      itemEl.appendChild(textEl);
      group.itemsEl.appendChild(itemEl);
      group.hasItems = true;
      scrollChatMessagesToBottom();
      return group;
    }

    function sourceLabel(source) {
      const value = String(source || "").trim();
      if (value === "ipet_inferred") {
        return "Ipet 推断";
      }
      if (value === "brain") {
        return "Brain";
      }
      if (value === "human_ops") {
        return "Human Ops";
      }
      return value || "状态";
    }

    function buildAssistantWorklogTurn(sessionId = "") {
      assistantWorklogCounter += 1;
      const el = runtimeDocument.createElement("article");
      el.className = "assistant-worklog";
      el.dataset.worklogId = `assistant-worklog-${assistantWorklogCounter}`;
      el.dataset.sessionId = String(sessionId || "").trim();

      const statusListEl = runtimeDocument.createElement("div");
      statusListEl.className = "worklog-status-list";

      const finalTextEl = runtimeDocument.createElement("div");
      finalTextEl.className = "worklog-final-text";

      el.appendChild(statusListEl);
      el.appendChild(finalTextEl);
      appendChatMessageElement(el);
      return { el, statusListEl, finalTextEl, sessionId: el.dataset.sessionId, statusRefs: new Map() };
    }

    function ensureAssistantWorklogTurn(sessionId = "") {
      const normalizedSessionId = String(sessionId || activeThoughtSessionId || "").trim() || beginThoughtSession();
      if (
        currentAssistantWorklog &&
        currentAssistantWorklog.el &&
        currentAssistantWorklog.el.parentNode &&
        currentAssistantWorklog.sessionId === normalizedSessionId
      ) {
        return currentAssistantWorklog;
      }
      currentAssistantWorklog = buildAssistantWorklogTurn(normalizedSessionId);
      return currentAssistantWorklog;
    }

    function appendWorklogPhase(payload, sessionId = "") {
      const turn = ensureAssistantWorklogTurn(sessionId);
      const text = String(payload?.text || "").trim();
      if (!text) {
        return turn;
      }
      const source = String(payload?.source || "brain").trim();
      const statusId = String(payload?.status_id || payload?.phase || "").trim();
      const reusable = statusId && payload?.transient !== false ? turn.statusRefs.get(statusId) : null;
      const rowEl = reusable || runtimeDocument.createElement("div");
      rowEl.className = `worklog-status-row source-${source.replace(/[^a-z0-9_-]/gi, "-")}`;
      rowEl.dataset.statusId = statusId;

      const textEl = reusable?.querySelector(".worklog-status-text") || runtimeDocument.createElement("div");
      textEl.className = "worklog-status-text";
      textEl.textContent = text;

      const sourceEl = reusable?.querySelector(".worklog-status-source") || runtimeDocument.createElement("span");
      sourceEl.className = "worklog-status-source";
      sourceEl.textContent = sourceLabel(source);
      textEl.appendChild(sourceEl);

      if (!reusable) {
        rowEl.appendChild(textEl);
        turn.statusListEl.appendChild(rowEl);
        if (statusId) {
          turn.statusRefs.set(statusId, rowEl);
        }
      }
      scrollChatMessagesToBottom();
      return turn;
    }

    function updateWorklogFinalText(turn, text, pending = false) {
      const target = turn || ensureAssistantWorklogTurn();
      if (!target || !target.finalTextEl) {
        return target;
      }
      target.finalTextEl.textContent = String(text || "");
      target.el.classList.toggle("pending", !!pending);
      scrollChatMessagesToBottom();
      return target;
    }

    function appendAssistantHistoryBlock(item) {
      const previousWorklog = currentAssistantWorklog;
      const turn = buildAssistantWorklogTurn(`history-${assistantWorklogCounter + 1}`);
      updateWorklogFinalText(turn, String(item?.content || ""));
      currentAssistantWorklog = previousWorklog;
      return turn;
    }

    function setApprovalThoughtSessionId(turnId, sessionId) {
      const key = String(turnId || "").trim();
      if (!key) {
        return;
      }
      approvalThoughtSessionRefs.set(key, String(sessionId || "").trim());
    }

    function deleteApprovalThoughtSessionId(turnId) {
      const key = String(turnId || "").trim();
      if (!key) {
        return;
      }
      approvalThoughtSessionRefs.delete(key);
    }

    function resolveApprovalThoughtSessionId(turnId) {
      const key = String(turnId || "").trim();
      return String(approvalThoughtSessionRefs.get(key) || activeThoughtSessionId || createThoughtSessionId()).trim();
    }

    function appendApprovalBubble(payload, sessionId = "") {
      const message = ensureAssistantWorklogTurn(sessionId);
      appendWorklogPhase(
        {
          phase: "action",
          text: String(payload.text || ""),
          source: "human_ops",
          transient: false,
          status_id: `approval:${String(payload.turn_id || Date.now()).trim()}`,
          render: "worklog",
        },
        sessionId,
      );
      const turnId = String(payload.turn_id || "").trim();
      const approvalCardEl = runtimeDocument.createElement("div");
      approvalCardEl.className = "approval-card";
      if (turnId) {
        approvalBubbleRefs.set(turnId, { el: approvalCardEl });
        setApprovalThoughtSessionId(turnId, String(sessionId || activeThoughtSessionId || "").trim());
      }
      appendToolList(approvalCardEl, Array.isArray(payload.tools) ? payload.tools : []);
      const metaText = formatStepMeta(payload);
      if (metaText) {
        const metaEl = runtimeDocument.createElement("div");
        metaEl.className = "approval-status";
        metaEl.textContent = metaText;
        approvalCardEl.appendChild(metaEl);
      }
      if (payload.preview?.marker === "red_dot") {
        showHumanOpsClickPreview(payload.preview);
        const previewEl = runtimeDocument.createElement("div");
        previewEl.className = "approval-status";
        previewEl.textContent = `点击预览：${payload.preview.label || "目标位置"} (${payload.preview.x}, ${payload.preview.y})`;
        approvalCardEl.appendChild(previewEl);
      }

      const actionWrap = runtimeDocument.createElement("div");
      actionWrap.className = "approval-actions";

      const approveBtn = runtimeDocument.createElement("button");
      approveBtn.className = "approval-btn approve";
      approveBtn.textContent = "批准";

      const rejectBtn = runtimeDocument.createElement("button");
      rejectBtn.className = "approval-btn reject";
      rejectBtn.textContent = "拒绝并提示";

      const statusEl = runtimeDocument.createElement("div");
      statusEl.className = "approval-status";

      actionWrap.appendChild(approveBtn);
      actionWrap.appendChild(rejectBtn);
      approvalCardEl.appendChild(actionWrap);
      approvalCardEl.appendChild(statusEl);
      message.el.appendChild(approvalCardEl);

      const setResolved = (text) => {
        approveBtn.disabled = true;
        rejectBtn.disabled = true;
        statusEl.textContent = text;
        hideHumanOpsClickPreview();
      };

      approveBtn.addEventListener("click", async () => {
        if (!payload.turn_id || getActiveApprovalId()) {
          return;
        }
        setActiveApprovalId(String(payload.turn_id));
        appendMessage("user", "好，去做吧。", { speakerName: "你" });
        setResolved("已批准，正在处理。");
        await continueApproval(payload.turn_id, true);
      });

      rejectBtn.addEventListener("click", async () => {
        if (!payload.turn_id || getActiveApprovalId() || getPendingApprovalInputTurnId()) {
          return;
        }
        setActiveApprovalId(String(payload.turn_id));
        setPendingApprovalInputTurnId(String(payload.turn_id));
        setResolved("已拒绝这一步，请在输入框里告诉我接下来该怎么调整。");
        appendWorklogPhase(
          {
            phase: "action",
            text: "这一步我先暂停啦。你直接补充想法，我会沿着当前任务继续处理。",
            source: "human_ops",
            transient: false,
            status_id: `approval_rejected:${String(payload.turn_id || "").trim()}`,
            render: "worklog",
          },
          sessionId,
        );
        setChatState("awaiting_followup_input");
        if (chatInputEl && typeof chatInputEl.focus === "function") {
          chatInputEl.focus();
        }
      });

      return message;
    }

    return {
      phaseLabel,
      thoughtRoleLabel,
      removeApprovalBubble,
      resetChatTimelineState,
      createThoughtSessionId,
      beginThoughtSession,
      clearActiveThoughtSession,
      ensureThoughtGroup,
      removeEmptyPendingThoughtGroup,
      appendThoughtPhase,
      sourceLabel,
      buildAssistantWorklogTurn,
      ensureAssistantWorklogTurn,
      appendWorklogPhase,
      updateWorklogFinalText,
      appendAssistantHistoryBlock,
      setApprovalThoughtSessionId,
      deleteApprovalThoughtSessionId,
      resolveApprovalThoughtSessionId,
      appendApprovalBubble,
    };
  }

  window.IpetChatWorklog = {
    createChatWorklogController,
  };
})();
