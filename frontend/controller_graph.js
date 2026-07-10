(() => {
  function createIpetControllerGraph(deps = {}) {
    const runtimeWindow = deps.window || window;
    const runtimeDocument = deps.document || runtimeWindow.document || document;
    const helpers = deps.helpers || runtimeWindow.IpetIndexHelpers || {};
    const controllerRegistry = deps.controllers || {};
    const facade = runtimeWindow.IpetControllerFacade.createControllerFacade({
      helpers,
      controllers: controllerRegistry,
    });

    const state = deps.state || runtimeWindow.IpetAppState.createDefaultAppState();
    const refs = deps.refs || runtimeWindow.IpetAppRefs.collectAppRefs(runtimeDocument);

    runtimeWindow.IpetControllerGraphSections.createControllerGraphSections({
      runtimeWindow,
      runtimeDocument,
      helpers,
      state,
      refs,
      facade,
      controllers: controllerRegistry,
    });

    return { facade, controllers: controllerRegistry, state, refs };
  }

  window.IpetControllerGraph = Object.freeze({
    createIpetControllerGraph,
  });
})();
