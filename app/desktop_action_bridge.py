from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

import human_ops.desktop_actions as _desktop_actions


_DEFAULT_EVENT_CLICKER = object()


@dataclass(frozen=True)
class DesktopActionBridge:
    desktop_actions_module: Any
    qapplication: Any = None

    @property
    def cg_point_type(self):
        return self.desktop_actions_module._CGPoint

    def screen_coordinate(self, value, fallback: int = 0) -> int:
        return self.desktop_actions_module._screen_coordinate(value, fallback=fallback)

    def post_core_graphics_click(self, x: int, y: int) -> None:
        return self.desktop_actions_module._post_core_graphics_click(x, y)

    def execute_human_ops_click(
        self,
        payload: dict | None,
        *,
        platform_name: str | None = None,
        runner=subprocess.run,
        event_clicker=_DEFAULT_EVENT_CLICKER,
    ) -> dict[str, object]:
        if event_clicker is _DEFAULT_EVENT_CLICKER:
            event_clicker = self.desktop_actions_module._post_core_graphics_click
        return self.desktop_actions_module.execute_human_ops_click(
            payload,
            platform_name=platform_name,
            runner=runner,
            event_clicker=event_clicker,
        )

    def applescript_string(self, value: object) -> str:
        return self.desktop_actions_module._applescript_string(value)

    def execute_human_ops_type_text(
        self,
        payload: dict | None,
        *,
        platform_name: str | None = None,
        runner=subprocess.run,
    ) -> dict[str, object]:
        return self.desktop_actions_module.execute_human_ops_type_text(
            payload,
            platform_name=platform_name,
            runner=runner,
        )

    def execute_human_ops_key_press(
        self,
        payload: dict | None,
        *,
        platform_name: str | None = None,
        runner=subprocess.run,
    ) -> dict[str, object]:
        return self.desktop_actions_module.execute_human_ops_key_press(
            payload,
            platform_name=platform_name,
            runner=runner,
        )

    def process_pending_qt_events(self) -> None:
        self.desktop_actions_module.process_pending_qt_events(qapplication=self.qapplication)

    def hide_window_for_desktop_click(self, window) -> bool:
        return self.desktop_actions_module.hide_window_for_desktop_click(
            window,
            qapplication=self.qapplication,
        )

    def restore_window_after_desktop_click(self, window, was_hidden: bool) -> None:
        self.desktop_actions_module.restore_window_after_desktop_click(
            window,
            was_hidden,
            qapplication=self.qapplication,
        )


def create_desktop_action_bridge(*, qapplication=None, desktop_actions_module=_desktop_actions) -> DesktopActionBridge:
    return DesktopActionBridge(desktop_actions_module, qapplication=qapplication)
