(() => {
  function createChatSidebarsController(deps = {}) {
    let chatSkillsController = null;
    const topicHistoryController = window.IpetChatTopicHistory.createChatTopicHistoryController({
      ...deps,
      closeOtherDrawer: () => chatSkillsController?.toggleChatSkillsDrawer(false),
    });
    chatSkillsController = window.IpetChatSkills.createChatSkillsController({
      ...deps,
      closeOtherDrawer: () => topicHistoryController.toggleChatHistoryDrawer(false),
    });

    return {
      ...topicHistoryController,
      ...chatSkillsController,
    };
  }

  window.IpetChatSidebars = {
    createChatSidebarsController,
  };
})();
