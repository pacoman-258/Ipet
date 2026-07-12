from __future__ import annotations

import subprocess
from typing import Any, Callable

from app.desktop_action_bridge import create_desktop_action_bridge as _create_desktop_action_bridge


BridgeFactory = Callable[[], Any]


def create_desktop_action_bridge(*, qapplication=None, desktop_actions_module=None):
    if desktop_actions_module is None:
        return _create_desktop_action_bridge(qapplication=qapplication)
    return _create_desktop_action_bridge(
        qapplication=qapplication,
        desktop_actions_module=desktop_actions_module,
    )


def create_desktop_action_bridge_entry(
    *,
    qapplication=None,
    bridge_factory=create_desktop_action_bridge,
    module_name: str = __name__,
):
    def _desktop_action_bridge():
        return bridge_factory(qapplication=qapplication)

    _desktop_action_bridge.__module__ = module_name
    return _desktop_action_bridge


def _name_entry(entry, name: str, module_name: str):
    entry.__name__ = name
    entry.__module__ = module_name
    return entry


def create_desktop_action_entries(
    bridge_factory: BridgeFactory,
    *,
    default_runner=subprocess.run,
    default_event_clicker_provider: Callable[[], Any] | None = None,
    module_name: str = __name__,
) -> dict[str, object]:
    def _screen_coordinate(value, fallback: int = 0) -> int:
        return bridge_factory().screen_coordinate(value, fallback=fallback)

    def _post_core_graphics_click(x: int, y: int) -> None:
        return bridge_factory().post_core_graphics_click(x, y)

    initial_default_event_clicker = _post_core_graphics_click

    def execute_human_ops_click(
        payload: dict | None,
        *,
        platform_name: str | None = None,
        runner=default_runner,
        event_clicker=initial_default_event_clicker,
    ) -> dict[str, object]:
        if event_clicker is initial_default_event_clicker and default_event_clicker_provider is not None:
            event_clicker = default_event_clicker_provider()
        return bridge_factory().execute_human_ops_click(
            payload,
            platform_name=platform_name,
            runner=runner,
            event_clicker=event_clicker,
        )

    def _applescript_string(value: object) -> str:
        return bridge_factory().applescript_string(value)

    def execute_human_ops_type_text(
        payload: dict | None,
        *,
        platform_name: str | None = None,
        runner=default_runner,
    ) -> dict[str, object]:
        return bridge_factory().execute_human_ops_type_text(
            payload,
            platform_name=platform_name,
            runner=runner,
        )

    def execute_human_ops_launch_app(
        payload: dict | None,
        *,
        platform_name: str | None = None,
        runner=default_runner,
    ) -> dict[str, object]:
        return bridge_factory().execute_human_ops_launch_app(
            payload,
            platform_name=platform_name,
            runner=runner,
        )

    def execute_human_ops_key_press(
        payload: dict | None,
        *,
        platform_name: str | None = None,
        runner=default_runner,
    ) -> dict[str, object]:
        return bridge_factory().execute_human_ops_key_press(
            payload,
            platform_name=platform_name,
            runner=runner,
        )

    def _process_pending_qt_events() -> None:
        bridge_factory().process_pending_qt_events()

    def _hide_window_for_desktop_click(window) -> bool:
        return bridge_factory().hide_window_for_desktop_click(window)

    def _restore_window_after_desktop_click(window, was_hidden: bool) -> None:
        bridge_factory().restore_window_after_desktop_click(window, was_hidden)

    entries = {
        "_screen_coordinate": _screen_coordinate,
        "_CGPoint": bridge_factory().cg_point_type,
        "_post_core_graphics_click": _post_core_graphics_click,
        "execute_human_ops_click": execute_human_ops_click,
        "_applescript_string": _applescript_string,
        "execute_human_ops_type_text": execute_human_ops_type_text,
        "execute_human_ops_launch_app": execute_human_ops_launch_app,
        "execute_human_ops_key_press": execute_human_ops_key_press,
        "_process_pending_qt_events": _process_pending_qt_events,
        "_hide_window_for_desktop_click": _hide_window_for_desktop_click,
        "_restore_window_after_desktop_click": _restore_window_after_desktop_click,
    }
    for name in (
        "_screen_coordinate",
        "_post_core_graphics_click",
        "execute_human_ops_click",
        "_applescript_string",
        "execute_human_ops_type_text",
        "execute_human_ops_launch_app",
        "execute_human_ops_key_press",
        "_process_pending_qt_events",
        "_hide_window_for_desktop_click",
        "_restore_window_after_desktop_click",
    ):
        _name_entry(entries[name], name, module_name)
    return entries


def create_desktop_action_exports(
    *,
    qapplication=None,
    module_globals: dict[str, object] | None = None,
    module_name: str = __name__,
) -> dict[str, object]:
    bridge_entry = create_desktop_action_bridge_entry(
        qapplication=qapplication,
        module_name=module_name,
    )
    globals_for_lookup = module_globals if module_globals is not None else {}

    def installed_bridge_factory():
        bridge_factory = globals_for_lookup.get("_desktop_action_bridge", bridge_entry)
        return bridge_factory()

    entries = create_desktop_action_entries(
        installed_bridge_factory,
        default_event_clicker_provider=lambda: globals_for_lookup["_post_core_graphics_click"],
        module_name=module_name,
    )
    return {"_desktop_action_bridge": bridge_entry, **entries}
