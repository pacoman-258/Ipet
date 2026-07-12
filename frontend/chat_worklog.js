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
    const executionCategories = new Set([
      "planning",
      "searching",
      "observing",
      "acting",
      "waiting_approval",
      "verifying",
      "blocked",
      "completed",
    ]);
    const executionCategoryLabels = {
      planning: "规划",
      searching: "搜索",
      observing: "观察",
      acting: "执行",
      waiting_approval: "等待批准",
      verifying: "验证",
      blocked: "受阻",
      completed: "完成",
    };

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
      stopWorklogTimer(currentAssistantWorklog);
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
      if (value === "body") {
        return "Body";
      }
      return value || "状态";
    }

    function normalizeExecutionCategory(payload) {
      const explicit = String(payload?.category || "").trim().toLowerCase();
      if (executionCategories.has(explicit)) {
        return explicit;
      }
      const phase = String(payload?.phase || "").trim().toLowerCase();
      const name = String(payload?.name || "").trim().toLowerCase();
      const status = String(payload?.status || "").trim().toLowerCase();
      if (status === "blocked" || status === "failed") {
        return "blocked";
      }
      if (phase === "search" || name.includes("search")) {
        return "searching";
      }
      if (name.includes("review") || status === "waiting") {
        return "waiting_approval";
      }
      if (name.includes("observe")) {
        return String(payload?.verification || "").toLowerCase() === "true" ? "verifying" : "observing";
      }
      if (name.includes("click") || name.includes("action") || phase === "action") {
        return "acting";
      }
      if (phase === "done") {
        return "completed";
      }
      return "planning";
    }

    function formatWorklogDuration(elapsedMs) {
      const seconds = Math.max(0, Number(elapsedMs) || 0) / 1000;
      return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
    }

    function updateWorklogSummary(turn, now = Date.now()) {
      if (!turn?.processSummaryEl) {
        return;
      }
      const elapsed = formatWorklogDuration(now - turn.startedAt);
      const category = turn.activeStatusRef?.dataset.category || turn.terminalCategory || "planning";
      const label = executionCategoryLabels[category] || "执行过程";
      turn.processTitleEl.textContent = turn.terminalCategory
        ? `${turn.terminalCategory === "completed" ? "已完成" : "执行受阻"} · ${turn.stepCount} 步`
        : `正在${label}`;
      turn.processMetaEl.textContent = elapsed;
    }

    function stopWorklogTimer(turn) {
      if (turn?.timerId) {
        clearInterval(turn.timerId);
        turn.timerId = null;
      }
    }

    function startWorklogTimer(turn) {
      if (!turn || turn.timerId) {
        return;
      }
      turn.timerId = setInterval(() => {
        const now = Date.now();
        if (turn.activeStatusRef?._worklogDurationEl) {
          turn.activeStatusRef._worklogDurationEl.textContent = formatWorklogDuration(
            now - turn.activeStatusRef._worklogStartedAt,
          );
        }
        updateWorklogSummary(turn, now);
      }, 250);
    }

    function settleWorklogStatus(turn, category = "completed") {
      const rowEl = turn?.activeStatusRef;
      if (!rowEl) {
        return;
      }
      const now = Date.now();
      rowEl.classList.remove("is-running", "is-waiting");
      rowEl.classList.add(category === "blocked" ? "is-blocked" : "is-completed");
      rowEl.dataset.status = category;
      if (rowEl._worklogDurationEl) {
        rowEl._worklogDurationEl.textContent = formatWorklogDuration(now - rowEl._worklogStartedAt);
      }
      turn.activeStatusRef = null;
    }

    function buildAssistantWorklogTurn(sessionId = "") {
      assistantWorklogCounter += 1;
      const el = runtimeDocument.createElement("article");
      el.className = "assistant-worklog";
      el.dataset.worklogId = `assistant-worklog-${assistantWorklogCounter}`;
      el.dataset.sessionId = String(sessionId || "").trim();

      const processDetailsEl = runtimeDocument.createElement("details");
      processDetailsEl.className = "worklog-process";
      processDetailsEl.open = true;
      processDetailsEl.hidden = true;

      const processSummaryEl = runtimeDocument.createElement("summary");
      const processTitleEl = runtimeDocument.createElement("span");
      processTitleEl.className = "worklog-process-title";
      processTitleEl.textContent = "正在规划";
      const processMetaEl = runtimeDocument.createElement("span");
      processMetaEl.className = "worklog-process-meta";
      processMetaEl.textContent = "0.0s";
      processSummaryEl.appendChild(processTitleEl);
      processSummaryEl.appendChild(processMetaEl);

      const statusListEl = runtimeDocument.createElement("div");
      statusListEl.className = "worklog-status-list";
      processDetailsEl.appendChild(processSummaryEl);
      processDetailsEl.appendChild(statusListEl);

      const finalTextEl = runtimeDocument.createElement("div");
      finalTextEl.className = "worklog-final-text";

      el.appendChild(processDetailsEl);
      el.appendChild(finalTextEl);
      appendChatMessageElement(el);
      return {
        el,
        processDetailsEl,
        processSummaryEl,
        processTitleEl,
        processMetaEl,
        statusListEl,
        finalTextEl,
        sessionId: el.dataset.sessionId,
        statusRefs: new Map(),
        activeStatusRef: null,
        startedAt: Date.now(),
        stepCount: 0,
        timerId: null,
        terminalCategory: "",
      };
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
      const category = normalizeExecutionCategory(payload);
      const statusId = String(payload?.status_id || payload?.name || payload?.phase || "").trim();
      const reusable = statusId && payload?.transient !== false ? turn.statusRefs.get(statusId) : null;
      const rowEl = reusable || runtimeDocument.createElement("div");
      if (turn.activeStatusRef && turn.activeStatusRef !== rowEl) {
        settleWorklogStatus(turn);
      }
      rowEl.className = `worklog-status-row source-${source.replace(/[^a-z0-9_-]/gi, "-")} ${
        category === "waiting_approval" ? "is-waiting" : "is-running"
      }`;
      rowEl.dataset.statusId = statusId;
      rowEl.dataset.category = category;

      const contentEl = reusable?.querySelector(".worklog-status-content") || runtimeDocument.createElement("div");
      contentEl.className = "worklog-status-content";

      const labelEl = reusable?.querySelector(".worklog-status-label") || runtimeDocument.createElement("div");
      labelEl.className = "worklog-status-label";
      labelEl.textContent = executionCategoryLabels[category] || executionCategoryLabels.planning;

      const textEl = reusable?.querySelector(".worklog-status-text") || runtimeDocument.createElement("div");
      textEl.className = "worklog-status-text";
      textEl.textContent = text;

      const task = String(payload?.task || payload?.observe_prompt || "").trim();
      let taskEl = reusable?.querySelector(".worklog-status-task") || null;
      if (task) {
        taskEl = taskEl || runtimeDocument.createElement("div");
        taskEl.className = "worklog-status-task";
        taskEl.textContent = `Brain → Observe：${task}`;
      } else if (taskEl) {
        taskEl.remove();
        taskEl = null;
      }

      const sourceEl = reusable?.querySelector(".worklog-status-source") || runtimeDocument.createElement("span");
      sourceEl.className = "worklog-status-source";
      sourceEl.textContent = sourceLabel(source);

      const durationEl = reusable?.querySelector(".worklog-status-duration") || runtimeDocument.createElement("span");
      durationEl.className = "worklog-status-duration";
      durationEl.textContent = reusable?._worklogDurationEl?.textContent || "0.0s";

      if (!reusable) {
        contentEl.appendChild(labelEl);
        contentEl.appendChild(textEl);
        if (taskEl) {
          contentEl.appendChild(taskEl);
        }
        rowEl.appendChild(contentEl);
        rowEl.appendChild(sourceEl);
        rowEl.appendChild(durationEl);
        turn.statusListEl.appendChild(rowEl);
        turn.stepCount += 1;
        if (statusId) {
          turn.statusRefs.set(statusId, rowEl);
        }
      } else if (taskEl && !taskEl.parentNode) {
        contentEl.appendChild(taskEl);
      }
      rowEl._worklogStartedAt = reusable?._worklogStartedAt || Date.now();
      rowEl._worklogDurationEl = durationEl;
      turn.processDetailsEl.hidden = false;
      turn.processDetailsEl.open = true;
      turn.terminalCategory = "";
      turn.activeStatusRef = rowEl;
      startWorklogTimer(turn);
      updateWorklogSummary(turn);
      scrollChatMessagesToBottom();
      return turn;
    }

    function finishWorklogProcess(turn, category = "completed") {
      const target = turn || currentAssistantWorklog;
      if (!target) {
        return target;
      }
      settleWorklogStatus(target, category);
      target.terminalCategory = category;
      stopWorklogTimer(target);
      updateWorklogSummary(target);
      if (category === "completed") {
        target.processDetailsEl.open = false;
      }
      return target;
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
          category: "waiting_approval",
          text: `等待批准：${String(payload.text || "")}`,
          source: "human_ops",
          transient: true,
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

      const statusEl = runtimeDocument.createElement("div");
      statusEl.className = "approval-status";
      statusEl.textContent = "请在 macOS 系统弹窗中批准或拒绝。";
      approvalCardEl.appendChild(statusEl);
      message.el.appendChild(approvalCardEl);
      if (turnId) {
        Promise.resolve().then(() => continueApproval(turnId, false, "", { native: true }));
      }

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
      finishWorklogProcess,
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
