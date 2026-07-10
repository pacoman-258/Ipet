(() => {
  function createSettingsModelPickerController(deps = {}) {
    const {
      els = {},
      fetchJson,
      stringValue,
      isGoogleAistudio,
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
      return { id, label: label || id };
    }

    function refreshSummary() {
      if (typeof renderSummary === "function" && typeof readForm === "function") {
        renderSummary(readForm());
      }
    }

    function renderModelOptions(models, options) {
      const { list, target, setModelStatus, emptyMessage, foundMessage } = options;
      if (!list) {
        return;
      }
      list.innerHTML = "";
      const items = Array.isArray(models) ? models : [];
      if (!items.length) {
        setModelStatus(emptyMessage);
        return;
      }
      items.forEach((item) => {
        const model = normalizeModel(item);
        if (!model.id) {
          return;
        }
        const button = document.createElement("button");
        button.type = "button";
        button.className = "model-option-button";
        button.textContent = model.label || model.id;
        button.title = `填入 ${model.id}`;
        button.addEventListener("click", () => {
          if (target) {
            target.value = model.id;
          }
          setModelStatus(`已填入 ${model.id}`);
          refreshSummary();
        });
        list.appendChild(button);
      });
      setModelStatus(foundMessage(list.children.length));
    }

    function setBrainModelStatus(message) {
      setStatus(els.brainModelStatus, message);
    }

    function renderBrainModelOptions(models) {
      renderModelOptions(models, {
        list: els.brainModelList,
        target: els.brainModelName,
        setModelStatus: setBrainModelStatus,
        emptyMessage: "没有发现可填入的模型",
        foundMessage: (count) => `发现 ${count} 个模型，点击即可填入`,
      });
    }

    async function fetchBrainModels() {
      const provider = stringValue(els.brainProvider, "openai_compatible");
      const endpoint = stringValue(els.brainModelEndpoint);
      if (!endpoint && !isGoogleAistudio(provider)) {
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
        renderBrainModelOptions(result.models || []);
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
      if (!endpoint && !isGoogleAistudio(provider)) {
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
