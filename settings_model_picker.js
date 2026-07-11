(() => {
  const REASONING_LABELS = {
    low: "低",
    medium: "中",
    high: "高",
    xhigh: "超高",
    max: "最大",
    ultra: "Ultra（自动委派）",
  };

  function createSettingsModelPickerController(deps = {}) {
    const {
      els = {},
      fetchJson,
      stringValue,
      isGoogleAistudio,
      isCodex,
      getSettingsPayload,
      renderSummary,
      readForm,
      showToast,
    } = deps;

    function setStatus(target, message) {
      if (target) {
        target.textContent = String(message || "");
      }
    }

    function normalizeModel(item) {
      const id = String(item?.id || item?.name || item || "").trim();
      const label = String(item?.label || item?.id || item?.name || item || "").trim();
      const reasoningEfforts = (Array.isArray(item?.reasoning_efforts) ? item.reasoning_efforts : [])
        .map((option) => ({
          value: String(option?.value || option?.reasoningEffort || option || "").trim(),
          description: String(option?.description || "").trim(),
        }))
        .filter((option) => option.value);
      return {
        id,
        label: label || id,
        reasoningEfforts,
        defaultReasoningEffort: String(item?.default_reasoning_effort || "").trim(),
        isDefault: item?.is_default === true,
      };
    }

    function refreshSummary() {
      if (typeof renderSummary === "function" && typeof readForm === "function") {
        renderSummary(readForm());
      }
    }

    function renderModelOptions(models, options) {
      const { list, target, setModelStatus, emptyMessage, foundMessage, onSelect } = options;
      if (!list) {
        return [];
      }
      list.innerHTML = "";
      const items = (Array.isArray(models) ? models : []).map(normalizeModel).filter((model) => model.id);
      if (!items.length) {
        setModelStatus(emptyMessage);
        return [];
      }
      items.forEach((model) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "model-option-button";
        button.textContent = model.label || model.id;
        button.title = `填入 ${model.id}`;
        button.addEventListener("click", () => {
          if (target) {
            target.value = model.id;
          }
          onSelect?.(model);
          setModelStatus(`已填入 ${model.id}`);
          refreshSummary();
        });
        list.appendChild(button);
      });
      setModelStatus(foundMessage(list.children.length));
      return items;
    }

    function renderBrainReasoningOptions(model) {
      const select = els.brainReasoningEffort;
      if (!select) {
        return;
      }
      const previous = String(select.value || "").trim();
      select.innerHTML = "";
      const defaultOption = document.createElement("option");
      defaultOption.value = "";
      defaultOption.textContent = model?.defaultReasoningEffort
        ? `跟随模型默认（${model.defaultReasoningEffort}）`
        : "跟随模型默认";
      select.appendChild(defaultOption);
      (model?.reasoningEfforts || []).forEach((effort) => {
        const option = document.createElement("option");
        option.value = effort.value;
        option.textContent = REASONING_LABELS[effort.value] || effort.value;
        option.title = effort.description;
        select.appendChild(option);
      });
      select.value = [...select.options].some((option) => option.value === previous) ? previous : "";
    }

    function setBrainModelStatus(message) {
      setStatus(els.brainModelStatus, message);
    }

    function renderBrainModelOptions(models) {
      return renderModelOptions(models, {
        list: els.brainModelList,
        target: els.brainModelName,
        setModelStatus: setBrainModelStatus,
        emptyMessage: "没有发现可填入的模型",
        foundMessage: (count) => `发现 ${count} 个模型，点击即可填入`,
        onSelect: renderBrainReasoningOptions,
      });
    }

    async function fetchBrainModels() {
      const provider = stringValue(els.brainProvider, "openai_compatible");
      const endpoint = stringValue(els.brainModelEndpoint);
      if (!endpoint && !isGoogleAistudio(provider) && !isCodex(provider)) {
        setBrainModelStatus("请先填写模型端点");
        return;
      }
      if (getSettingsPayload()?.static_preview) {
        setBrainModelStatus("静态预览无法连接后端拉取模型");
        return;
      }
      if (els.brainFetchModelsBtn) {
        els.brainFetchModelsBtn.disabled = true;
      }
      setBrainModelStatus("正在拉取模型...");
      try {
        const payload = {
          provider,
          model_endpoint: endpoint,
        };
        if (els.brainApiKey?.value) {
          payload.api_key = els.brainApiKey.value;
        }
        const result = await fetchJson("/api/brain/models", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        const models = renderBrainModelOptions(result.models || []);
        if (isCodex(provider)) {
          const currentModel = String(els.brainModelName?.value || "").trim();
          const selected = models.find((model) => model.id === currentModel)
            || models.find((model) => model.isDefault)
            || models[0];
          if (selected && (!currentModel || currentModel === "default" || !models.some((model) => model.id === currentModel))) {
            els.brainModelName.value = selected.id;
            setBrainModelStatus(`已填入账户默认模型 ${selected.id}；共发现 ${models.length} 个模型`);
            refreshSummary();
          }
          renderBrainReasoningOptions(selected);
        }
        if (result.fallback_reason) {
          setBrainModelStatus(result.fallback_reason);
        }
        showToast("模型列表已更新");
      } catch (error) {
        setBrainModelStatus(`拉取失败：${error.message || String(error)}`);
      } finally {
        if (els.brainFetchModelsBtn) {
          els.brainFetchModelsBtn.disabled = false;
        }
      }
    }

    function setObserveModelStatus(message) {
      setStatus(els.opsObserveModelStatus, message);
    }

    function renderObserveModelOptions(models) {
      renderModelOptions(models, {
        list: els.opsObserveModelList,
        target: els.opsObserveModelName,
        setModelStatus: setObserveModelStatus,
        emptyMessage: "没有发现可填入的 observe 模型",
        foundMessage: (count) => `发现 ${count} 个模型，点击即可填入`,
      });
    }

    async function fetchObserveModels() {
      const provider = stringValue(els.opsObserveModelProvider, "openai_compatible");
      const endpoint = stringValue(els.opsObserveModelEndpoint);
      if (!endpoint && !isGoogleAistudio(provider) && !isCodex(provider)) {
        setObserveModelStatus("请先填写 observe 模型端点");
        return;
      }
      if (getSettingsPayload()?.static_preview) {
        setObserveModelStatus("静态预览无法连接后端拉取模型");
        return;
      }
      if (els.opsObserveFetchModelsBtn) {
        els.opsObserveFetchModelsBtn.disabled = true;
      }
      setObserveModelStatus("正在拉取 observe 模型...");
      try {
        const payload = {
          scope: "observe",
          provider,
          model_endpoint: endpoint,
        };
        if (els.opsObserveApiKey?.value) {
          payload.api_key = els.opsObserveApiKey.value;
        }
        const result = await fetchJson("/api/brain/models", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        renderObserveModelOptions(result.models || []);
        if (result.fallback_reason) {
          setObserveModelStatus(result.fallback_reason);
        }
        showToast("observe 模型列表已更新");
      } catch (error) {
        setObserveModelStatus(`拉取失败：${error.message || String(error)}`);
      } finally {
        if (els.opsObserveFetchModelsBtn) {
          els.opsObserveFetchModelsBtn.disabled = false;
        }
      }
    }

    return {
      setBrainModelStatus,
      renderBrainModelOptions,
      renderBrainReasoningOptions,
      fetchBrainModels,
      setObserveModelStatus,
      renderObserveModelOptions,
      fetchObserveModels,
    };
  }

  window.IpetSettingsModelPicker = {
    ...(window.IpetSettingsModelPicker || {}),
    createSettingsModelPickerController,
  };
})();
