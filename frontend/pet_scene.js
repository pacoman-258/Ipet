(() => {
  const MIN_SCALE = 0.05;
  const MAX_SCALE = 5.0;
  const DEFAULT_SCALE = 0.3;
  const MODEL_MARGIN = 18;
  const TARGET_MODEL_HEIGHT_RATIO = 0.38;

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

    function modelBoundsInClientSpace() {
      if (!app || !model || typeof model.getBounds !== "function") {
        return null;
      }
      const view = app.view || app.renderer?.view;
      const canvasRect = view?.getBoundingClientRect?.();
      const bounds = model.getBounds();
      const rendererWidth = Number(app.renderer?.width || canvasRect?.width || 0);
      const rendererHeight = Number(app.renderer?.height || canvasRect?.height || 0);
      if (!canvasRect || !rendererWidth || !rendererHeight || !bounds) {
        return null;
      }
      const scaleX = canvasRect.width / rendererWidth;
      const scaleY = canvasRect.height / rendererHeight;
      return {
        left: canvasRect.left + bounds.x * scaleX,
        top: canvasRect.top + bounds.y * scaleY,
        right: canvasRect.left + (bounds.x + bounds.width) * scaleX,
        bottom: canvasRect.top + (bounds.y + bounds.height) * scaleY,
        width: bounds.width * scaleX,
        height: bounds.height * scaleY,
      };
    }

    function isPointOnModel(clientX, clientY) {
      const bounds = modelBoundsInClientSpace();
      if (!bounds) {
        return false;
      }
      const x = Number(clientX);
      const y = Number(clientY);
      return (
        Number.isFinite(x)
        && Number.isFinite(y)
        && x >= bounds.left
        && x <= bounds.right
        && y >= bounds.top
        && y <= bounds.bottom
      );
    }

    function consumePendingModelUrl() {
      const url = pendingModelUrl;
      pendingModelUrl = "";
      return url;
    }

    function clampModelPosition(desiredX, desiredY) {
      if (!app || !model) {
        return false;
      }
      const rendererWidth = Number(app.renderer?.width || 0);
      const rendererHeight = Number(app.renderer?.height || 0);
      const scaledWidth = Math.max(1, Number(model.width || 0));
      const scaledHeight = Math.max(1, Number(model.height || 0));
      const minX = Math.min(0, rendererWidth - scaledWidth);
      const maxX = Math.max(0, rendererWidth - scaledWidth);
      const minY = Math.min(0, rendererHeight - scaledHeight);
      const maxY = Math.max(0, rendererHeight - scaledHeight);
      const nextX = clamp(Number(desiredX) || 0, minX, maxX);
      const nextY = clamp(Number(desiredY) || 0, minY, maxY);
      const changed = Math.abs(nextX - Number(desiredX || 0)) > 0.5 || Math.abs(nextY - Number(desiredY || 0)) > 0.5;
      model.x = nextX;
      model.y = nextY;
      return changed;
    }

    function defaultModelPosition() {
      if (!app || !model) {
        return { x: 0, y: 0 };
      }
      const rendererWidth = Number(app.renderer?.width || 0);
      const rendererHeight = Number(app.renderer?.height || 0);
      const scaledWidth = Math.max(1, Number(model.width || 0));
      const scaledHeight = Math.max(1, Number(model.height || 0));
      const marginX = Math.min(MODEL_MARGIN, Math.max(0, rendererWidth - scaledWidth));
      const marginY = Math.min(MODEL_MARGIN, Math.max(0, rendererHeight - scaledHeight));
      return {
        x: rendererWidth - scaledWidth - marginX,
        y: rendererHeight - scaledHeight - marginY,
      };
    }

    function preferredModelScale() {
      if (!app || !model) {
        return DEFAULT_SCALE;
      }
      const currentScale = Math.max(MIN_SCALE, Math.abs(Number(model.scale?.x || 1)));
      const naturalWidth = Math.max(1, Number(model.width || 0) / currentScale);
      const naturalHeight = Math.max(1, Number(model.height || 0) / currentScale);
      const rendererWidth = Math.max(1, Number(app.renderer?.width || 0));
      const rendererHeight = Math.max(1, Number(app.renderer?.height || 0));
      const targetHeight = clamp(rendererHeight * TARGET_MODEL_HEIGHT_RATIO, 220, 340);
      const targetWidth = rendererWidth * 0.36;
      return clamp(Math.min(targetWidth / naturalWidth, targetHeight / naturalHeight), MIN_SCALE, MAX_SCALE);
    }

    function usesDefaultModelLayout() {
      const scale = Number(state.scale);
      const offsetX = Number(state.offset_x) || 0;
      const offsetY = Number(state.offset_y) || 0;
      return Math.abs(scale - DEFAULT_SCALE) < 0.0001
        && Math.abs(offsetX) < 1
        && (Math.abs(offsetY) < 1 || Math.abs(offsetY - 40) < 1);
    }

    function applyModelTransform() {
      if (!app || !model) {
        refreshStatus();
        return;
      }

      state.scale = clamp(Number(state.scale) || DEFAULT_SCALE, MIN_SCALE, MAX_SCALE);
      state.opacity = clamp(Number(state.opacity) || 1, 0.1, 1);
      state.rotation = Number(state.rotation) || 0;

      model.scale.set(state.scale);
      model.alpha = state.opacity;
      model.rotation = (state.rotation * Math.PI) / 180;

      let basePosition = defaultModelPosition();

      const positionClamped = clampModelPosition(
        basePosition.x + (Number(state.offset_x) || 0),
        basePosition.y + (Number(state.offset_y) || 0),
      );
      if (positionClamped) {
        state.scale = preferredModelScale();
        model.scale.set(state.scale);
        basePosition = defaultModelPosition();
        clampModelPosition(basePosition.x, basePosition.y);
      }
      state.offset_x = roundInt(model.x - basePosition.x);
      state.offset_y = roundInt(model.y - basePosition.y);
      if (positionClamped) {
        scheduleNotifyState();
      }

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

          clampModelPosition(p.x + dragDx, p.y + dragDy);

          const basePosition = defaultModelPosition();
          state.offset_x = roundInt(model.x - basePosition.x);
          state.offset_y = roundInt(model.y - basePosition.y);

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

      if (!Number.isFinite(state.scale) || state.scale <= 0 || usesDefaultModelLayout()) {
        state.scale = preferredModelScale();
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
      modelBoundsInClientSpace,
      isPointOnModel,
      clampModelPosition,
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
