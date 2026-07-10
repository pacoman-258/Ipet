(() => {
  const graph = window.IpetControllerGraph.createIpetControllerGraph({
    window,
    document,
  });

  graph.facade.init();
})();
