(() => {
  function createShellBridgeController(deps = {}) {
    const state = deps.state || {};
    const window = deps.window || globalThis.window;
    const getQtBridge = typeof deps.getQtBridge === "function" ? deps.getQtBridge : () => null;

    function callQtBridge(methodName) {
      const qtBridge = getQtBridge();
      if (!qtBridge || typeof qtBridge[methodName] !== "function") {
        return false;
      }
      qtBridge[methodName]();
      return true;
    }

    function openSettingsPage() {
      if (callQtBridge("openSettingsPage")) {
        return;
      }
      const backendBase = String(state.chat?.backend_url || "http://127.0.0.1:8008").trim() || "http://127.0.0.1:8008";
      const normalizedBase = backendBase.replace(/\/+$/, "");
      window.open(`${normalizedBase}/settings`, "_blank", "noopener");
    }

    function minimizeWindow() {
      return callQtBridge("minimizeWindow");
    }

    function closeWindow() {
      return callQtBridge("closeWindow");
    }

    return Object.freeze({
      callQtBridge,
      openSettingsPage,
      minimizeWindow,
      closeWindow,
    });
  }

  window.IpetShellBridge = Object.freeze({
    createShellBridgeController,
  });
})();
