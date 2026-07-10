(() => {
  const MIN_SCALE = 0.05;
  const MAX_SCALE = 5.0;

  function createPetSceneController(deps = {}) {
    const helpers = deps.helpers || window.IpetIndexHelpers || {};
    const state = deps.state || {};
    const runtimeWindow = deps.window || window;
    const refreshStatus = typeof deps.refreshStatus === "function" ? deps.refreshStatus : () => {};
    const scheduleNotifyState = typeof deps.scheduleNotifyState === "function" ? deps.scheduleNotifyState : () => {};
    const notifyStateNow = typeof deps.notifyStateNow === "function" ? deps.notifyStateNow : () => {};
    const clearError = typeof deps.clearError === "function" ? deps.clearError : () => {};
    const clamp = typeof helpers.clamp === "function"
      ? helpers.clamp
      : (value, min, max) => Math.min(max, Math.max(min, value));
    const roundInt = typeof helpers.roundInt === "function"
      ? helpers.roundInt
      : (value) => Math.round(Number(value) || 0);

    let app = null;
    let model = null;
    let pendingModelUrl = "";
    let dragging = false;
    let dragDx = 0;
    let dragDy = 0;
    let stageInteractionApp = null;
    let wheelInteractionInstalled = false;

    function setPixiApp(nextApp) {
      app = nextApp || null;
      if (stageInteractionApp && stageInteractionApp !== app) {
        stageInteractionApp = null;
      }
    }

    function getPixiApp() {
      return app;
    }

    function getCurrentModel() {
      return model;
    }

    function consumePendingModelUrl() {
      const url = pendingModelUrl;
      pendingModelUrl = "";
      return url;
    }

    function applyModelTransform() {
      if (!app || !model) {
        refreshStatus();
        return;
      }

      state.scale = clamp(Number(state.scale) || 0.3, MIN_SCALE, MAX_SCALE);
      state.opacity = clamp(Number(state.opacity) || 1, 0.1, 1);
      state.rotation = Number(state.rotation) || 0;

      model.scale.set(state.scale);
      model.alpha = state.opacity;
      model.rotation = (state.rotation * Math.PI) / 180;

      const scaledWidth = model.width;
      const scaledHeight = model.height;
      const baseX = (app.renderer.width - scaledWidth) * 0.5;
      const baseY = app.renderer.height - scaledHeight;

      model.x = baseX + (Number(state.offset_x) || 0);
      model.y = baseY + (Number(state.offset_y) || 0);

      refreshStatus();
    }

    function bindModelInteraction() {
      if (!app || !model) {
        return;
      }

      model.interactive = true;
      if ("eventMode" in model) {
        model.eventMode = "static";
      }
      model.cursor = "pointer";

      model.on("pointerdown", (event) => {
        if (!state.edit_mode) {
          return;
        }
        dragging = true;
        const p = event?.data?.global;
        if (p) {
          dragDx = model.x - p.x;
          dragDy = model.y - p.y;
        }
      });

      model.on("pointertap", () => {
        playMotionCompat("Tap", 0);
      });

      model.on("hit", (hitAreas) => {
        if (!Array.isArray(hitAreas) || hitAreas.length === 0) {
          return;
        }
        const hasBody = hitAreas.some((value) => String(value).toLowerCase().includes("body"));
        const group = hasBody ? "Tap@Body" : "Tap";
        playMotionCompat(group, 0);
      });
    }

    function setupStageInteraction() {
      if (!app) {
        return;
      }

      if (stageInteractionApp !== app) {
        stageInteractionApp = app;
        app.stage.interactive = true;
        if ("eventMode" in app.stage) {
          app.stage.eventMode = "static";
        }
        app.stage.hitArea = app.screen;

        app.stage.on("pointerup", () => {
          if (dragging) {
            dragging = false;
            notifyStateNow();
          }
        });

        app.stage.on("pointerupoutside", () => {
          if (dragging) {
            dragging = false;
            notifyStateNow();
          }
        });

        app.stage.on("pointermove", (event) => {
          if (!dragging || !state.edit_mode || !model) {
            return;
          }

          const p = event?.data?.global;
          if (!p) {
            return;
          }

          model.x = p.x + dragDx;
          model.y = p.y + dragDy;

          const scaledWidth = model.width;
          const scaledHeight = model.height;
          const baseX = (app.renderer.width - scaledWidth) * 0.5;
          const baseY = app.renderer.height - scaledHeight;
          state.offset_x = roundInt(model.x - baseX);
          state.offset_y = roundInt(model.y - baseY);

          refreshStatus();
          scheduleNotifyState();
        });
      }

      if (!wheelInteractionInstalled) {
        wheelInteractionInstalled = true;
        runtimeWindow.addEventListener(
          "wheel",
          (event) => {
            if (!state.edit_mode || !model) {
              return;
            }

            event.preventDefault();
            const zoomIn = event.deltaY < 0;
            const factor = zoomIn ? 1.05 : 0.95;
            state.scale = clamp(state.scale * factor, MIN_SCALE, MAX_SCALE);
            applyModelTransform();
            scheduleNotifyState();
          },
          { passive: false },
        );
      }
    }

    function isRendererReady() {
      return !!(app && app.renderer && app.stage);
    }

    function playMotionCompat(group, index = 0) {
      if (!model) {
        return false;
      }

      if (typeof model.motion === "function") {
        try {
          model.motion(group, index);
          return true;
        } catch (_) {
          // fall through to internal manager
        }
      }

      const manager = model.internalModel?.motionManager;
      if (manager && typeof manager.startMotion === "function") {
        try {
          manager.startMotion(group, index);
          return true;
        } catch (_) {
          // ignore manager errors
        }
      }

      return false;
    }

    async function loadModel(url) {
      if (!url) {
        return;
      }

      if (!runtimeWindow.PIXI || !runtimeWindow.PIXI.live2d || !runtimeWindow.PIXI.live2d.Live2DModel) {
        throw new Error("PIXI Live2D 模型组件不可用，请检查 JS 资源文件。");
      }

      clearError();

      if (!isRendererReady()) {
        pendingModelUrl = url;
        return;
      }

      if (model) {
        try {
          model.removeAllListeners();
          app.stage.removeChild(model);
          model.destroy();
        } catch (_) {
          // ignore cleanup errors
        }
        model = null;
      }

      model = await runtimeWindow.PIXI.live2d.Live2DModel.from(url);
      app.stage.addChild(model);

      bindModelInteraction();

      if (!Number.isFinite(state.scale) || state.scale <= 0) {
        const fitScaleX = (app.renderer.width * 0.65) / Math.max(1, model.width);
        const fitScaleY = (app.renderer.height * 0.92) / Math.max(1, model.height);
        state.scale = clamp(Math.min(fitScaleX, fitScaleY), MIN_SCALE, MAX_SCALE);
      }
      if (model.width > app.renderer.width * 2 || model.height > app.renderer.height * 2) {
        const fitScaleX = (app.renderer.width * 0.65) / Math.max(1, model.width);
        const fitScaleY = (app.renderer.height * 0.92) / Math.max(1, model.height);
        state.scale = clamp(state.scale * Math.min(fitScaleX, fitScaleY), MIN_SCALE, MAX_SCALE);
      }

      applyModelTransform();
      notifyStateNow();
    }

    return {
      setPixiApp,
      getPixiApp,
      getCurrentModel,
      consumePendingModelUrl,
      applyModelTransform,
      bindModelInteraction,
      setupStageInteraction,
      isRendererReady,
      playMotionCompat,
      loadModel,
    };
  }

  window.IpetPetScene = {
    createPetSceneController,
  };
})();
