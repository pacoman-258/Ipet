(() => {
  function createSettingsLive2dController(deps = {}) {
    const {
      els = {},
      fetchJson,
      getSettingsPayload,
      showToast,
      runAction,
    } = deps;
    const execute = typeof runAction === "function" ? runAction : (action) => action();
    let localModels = [];

    function setModelPreviewStatus(message) {
      if (els.modelPreviewStatus) {
        els.modelPreviewStatus.textContent = String(message || "");
      }
    }

    function findLocalModelByPath(path) {
      const value = String(path || "").trim();
      return localModels.find((item) => String(item?.path || "").trim() === value) || null;
    }

    function selectedLocalModel() {
      return findLocalModelByPath(els.localModelSelect?.value || els.modelPath?.value || "");
    }

    function renderActionList(container, items, emptyText, onClick, kind) {
      if (!container) {
        return;
      }
      container.innerHTML = "";
      if (!Array.isArray(items) || !items.length) {
        container.classList.add("empty-state");
        container.textContent = emptyText;
        return;
      }
      container.classList.remove("empty-state");
      items.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `action-chip ${kind || ""}`.trim();
        button.textContent = item.label || item.name || "";
        button.addEventListener("click", () => {
          void execute(() => onClick(item));
        });
        container.appendChild(button);
      });
    }

    async function postPreviewAction(payload) {
      if (getSettingsPayload()?.static_preview) {
        throw new Error("静态预览无法连接桌宠宿主");
      }
      return fetchJson("/api/settings/preview-action", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    }

    function renderModelMeta() {
      const model = selectedLocalModel();
      renderActionList(
        els.modelMotionList,
        model?.motion_actions || [],
        "选择项目形象后显示动作",
        async (item) => {
          await postPreviewAction({
            type: "play_motion",
            model_path: model.path,
            group: item.group,
            index: item.index,
          });
          setModelPreviewStatus(`已播放动作：${item.label}`);
          showToast?.(`已播放动作：${item.label}`);
        },
        "motion",
      );
      renderActionList(
        els.modelExpressionList,
        model?.expression_actions || [],
        "选择项目形象后显示表情",
        async (item) => {
          await postPreviewAction({
            type: "play_expression",
            model_path: model.path,
            name: item.name,
          });
          setModelPreviewStatus(`已播放表情：${item.label}`);
          showToast?.(`已播放表情：${item.label}`);
        },
        "expression",
      );
    }

    function populateLocalModels() {
      if (!els.localModelSelect) {
        return;
      }
      els.localModelSelect.innerHTML = "";
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = localModels.length ? "选择项目内形象" : "未找到项目内形象";
      els.localModelSelect.appendChild(placeholder);
      localModels.forEach((item) => {
        const option = document.createElement("option");
        option.value = String(item.path || "");
        option.textContent = String(item.label || item.path || "");
        els.localModelSelect.appendChild(option);
      });
      const currentPath = String(els.modelPath?.value || "").trim();
      els.localModelSelect.value = findLocalModelByPath(currentPath) ? currentPath : "";
      renderModelMeta();
    }

    async function loadLocalModels() {
      if (getSettingsPayload()?.static_preview) {
        localModels = [];
        populateLocalModels();
        setModelPreviewStatus("静态预览不会扫描或切换桌宠形象。");
        return;
      }
      if (els.refreshLocalModelsBtn) {
        els.refreshLocalModelsBtn.disabled = true;
      }
      setModelPreviewStatus("正在读取项目内 Live2D 形象...");
      try {
        const data = await fetchJson("/api/settings/models-local");
        localModels = Array.isArray(data.models) ? data.models : [];
        populateLocalModels();
        setModelPreviewStatus(`已发现 ${localModels.length} 个项目内形象。`);
      } catch (error) {
        localModels = [];
        populateLocalModels();
        setModelPreviewStatus(`形象列表不可用：${error.message || String(error)}`);
        throw error;
      } finally {
        if (els.refreshLocalModelsBtn) {
          els.refreshLocalModelsBtn.disabled = false;
        }
      }
    }

    function useSelectedLocalModel() {
      const model = selectedLocalModel();
      if (!model) {
        throw new Error("请先选择项目内形象");
      }
      els.modelPath.value = model.path;
      renderModelMeta();
      setModelPreviewStatus(`已填入形象路径：${model.path}`);
    }

    async function previewSelectedLocalModel() {
      const model = selectedLocalModel();
      const modelPath = String(model?.path || els.modelPath?.value || "").trim();
      if (!modelPath) {
        throw new Error("请先选择或填写 Live2D 形象路径");
      }
      els.modelPath.value = modelPath;
      await postPreviewAction({ type: "load_model", model_path: modelPath });
      renderModelMeta();
      setModelPreviewStatus(`已切换形象预览：${model?.label || modelPath}`);
      showToast?.("形象预览已应用；点击“应用并保存”可持久化选择。");
    }

    function handleModelSelectChange() {
      if (els.localModelSelect?.value && els.modelPath) {
        els.modelPath.value = els.localModelSelect.value;
      }
      renderModelMeta();
    }

    function handleModelPathInput() {
      if (els.localModelSelect) {
        const path = String(els.modelPath?.value || "").trim();
        els.localModelSelect.value = findLocalModelByPath(path) ? path : "";
      }
      renderModelMeta();
    }

    return {
      loadLocalModels,
      populateLocalModels,
      useSelectedLocalModel,
      previewSelectedLocalModel,
      handleModelSelectChange,
      handleModelPathInput,
    };
  }

  window.IpetSettingsLive2d = {
    createSettingsLive2dController,
  };
})();
