(() => {
  function createHumanOpsPreviewController(deps = {}) {
    const window = deps.window || globalThis.window;
    const document = deps.document || globalThis.document;
    const getQtBridge = typeof deps.getQtBridge === "function" ? deps.getQtBridge : () => null;
    let humanOpsClickPreviewEl = null;

    function hideHumanOpsClickPreview() {
      if (humanOpsClickPreviewEl && humanOpsClickPreviewEl.parentNode) {
        humanOpsClickPreviewEl.remove();
      }
      humanOpsClickPreviewEl = null;
      const qtBridge = getQtBridge();
      if (qtBridge && typeof qtBridge.hideClickPreview === "function") {
        qtBridge.hideClickPreview();
      }
    }

    function showHumanOpsClickPreview(preview) {
      hideHumanOpsClickPreview();
      if (!preview || preview.marker !== "red_dot") {
        return;
      }
      const x = Number(preview.x);
      const y = Number(preview.y);
      if (!Number.isFinite(x) || !Number.isFinite(y)) {
        return;
      }
      const size = Math.max(10, Math.min(48, Number(preview.size) || 24));
      const viewportX = x - (Number(window.screenX) || 0);
      const viewportY = y - (Number(window.screenY) || 0);
      const qtBridge = getQtBridge();
      if (qtBridge && typeof qtBridge.showClickPreview === "function") {
        qtBridge.showClickPreview(JSON.stringify(preview));
        return;
      }
      const previewInsideWindow =
        viewportX >= size / 2 &&
        viewportY >= size / 2 &&
        viewportX <= window.innerWidth - size / 2 &&
        viewportY <= window.innerHeight - size / 2;
      if (!previewInsideWindow) {
        return;
      }
      const dot = document.createElement("span");
      dot.id = "human-ops-click-preview-dot";
      dot.className = "human-ops-click-preview-dot";
      dot.style.width = `${size}px`;
      dot.style.height = `${size}px`;
      dot.style.left = `${viewportX}px`;
      dot.style.top = `${viewportY}px`;
      dot.title = String(preview.label || "目标位置");
      document.body.appendChild(dot);
      humanOpsClickPreviewEl = dot;
    }

    return Object.freeze({
      showHumanOpsClickPreview,
      hideHumanOpsClickPreview,
    });
  }

  window.IpetHumanOpsPreview = Object.freeze({
    createHumanOpsPreviewController,
  });
})();
