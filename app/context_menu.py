from __future__ import annotations

from typing import Any


class DesktopContextMenuController:
    def __init__(
        self,
        owner: Any,
        *,
        q_menu_factory: Any,
        q_action_factory: Any,
    ) -> None:
        self.owner = owner
        self.q_menu_factory = q_menu_factory
        self.q_action_factory = q_action_factory

    def show_context_menu(self, pos: Any) -> None:
        owner = self.owner
        menu = self.q_menu_factory(owner)
        panel_action = self.q_action_factory("打开设置页", owner)
        lock_action = self.q_action_factory("解锁窗口" if owner.window_locked else "锁定窗口", owner)
        reload_action = self.q_action_factory("重新加载模型", owner)
        quit_action = self.q_action_factory("退出", owner)

        menu.addAction(panel_action)
        menu.addAction(lock_action)
        menu.addAction(reload_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        selected = menu.exec(owner.browser.mapToGlobal(pos))
        if selected == panel_action:
            owner.open_settings_page()
        elif selected == lock_action:
            owner.window_locked = not owner.window_locked
            owner.config["window"]["locked"] = owner.window_locked
        elif selected == reload_action:
            owner.apply_config_to_web()
        elif selected == quit_action:
            owner.close()
