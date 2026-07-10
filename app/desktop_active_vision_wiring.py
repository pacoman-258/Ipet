from __future__ import annotations

import subprocess
import time
from typing import Any, Callable

from app import active_vision_bridge as _active_vision_bridge_module
from app import desktop_screen_capture_wiring as _screen_capture_wiring
from backend.active_vision import normalize_active_observation_config
from backend.vision import normalize_vision_config
from body import active_vision as _active_vision
from body.screen_vision_controller import ScreenVisionController


ACTIVE_VISION_CONSTANT_NAMES = (
    "ACTIVE_VISION_ALLOWED_ACTIONS",
    "ACTIVE_VISION_CLICK_POLICY",
    "ACTIVE_VISION_SURVEY_MODE",
    "ACTIVE_VISION_FOCUS_MODE",
    "ACTIVE_VISION_MIN_MAX_WIDTH",
    "ACTIVE_VISION_MIN_JPEG_QUALITY",
    "ACTIVE_VISION_MAX_CANDIDATES",
    "ACTIVE_VISION_WINDOW_ENUMERATION_TIMEOUT_SEC",
    "ACTIVE_VISION_RUNNING_APP_FALLBACK_TIMEOUT_SEC",
    "ACTIVE_VISION_DOCK_ITEM_ENUMERATION_TIMEOUT_SEC",
)


ACTIVE_VISION_COMPAT_WRAPPER_NAMES = (
    "_osascript_args",
    "_active_target_id",
    "_safe_focus_point",
    "_active_vision_capture_config",
    "_parse_macos_desktop_targets",
    "discover_macos_active_vision_desktop_targets",
    "enumerate_active_vision_desktop_targets",
    "_parse_macos_dock_item_candidates",
    "enumerate_macos_dock_item_candidates",
    "_active_candidate_id",
    "_sanitize_active_candidate",
    "_active_candidates_from_desktop_targets",
    "_active_screenshot_candidate",
    "_is_active_vision_system_process_name",
    "_active_vision_name_key",
    "_app_bundle_info_from_process_path",
    "_app_bundle_name_from_process_path",
    "_running_app_process_path_score",
    "_running_app_candidate_score",
    "_parse_system_events_running_app_candidates",
    "_system_events_running_app_candidates",
    "enumerate_active_vision_running_app_candidates",
    "_merge_active_target_candidates",
    "_boost_candidates_for_target_hint",
    "_find_desktop_target",
    "_find_active_target",
    "_active_vision_focus_script",
    "_parse_active_vision_focus_result",
    "_activate_running_app_with_open",
    "_activate_running_app_candidate",
    "_can_activate_app_level_candidate",
    "run_active_vision_light_interaction",
    "_vision_inline_frame_from_payload",
    "_decode_frame_image",
    "_display_union_bounds",
    "_active_detail_frames_from_capture",
)


def create_active_vision_wrapper(
    name: str,
    *,
    active_vision_module: Any = _active_vision,
    module_name: str = __name__,
):
    target = getattr(active_vision_module, name)

    def wrapper(*args, **kwargs):
        return target(*args, **kwargs)

    wrapper.__name__ = name
    wrapper.__module__ = module_name
    wrapper.__doc__ = getattr(target, "__doc__", None)
    return wrapper


def create_active_vision_wrapper_factory(
    *,
    active_vision_module: Any = _active_vision,
    module_name: str = __name__,
):
    def active_vision_wrapper(name: str):
        return create_active_vision_wrapper(
            name,
            active_vision_module=active_vision_module,
            module_name=module_name,
        )

    active_vision_wrapper.__name__ = "_active_vision_wrapper"
    active_vision_wrapper.__module__ = module_name
    return active_vision_wrapper


def create_active_vision_compat_exports(
    *,
    active_vision_module: Any = _active_vision,
    module_name: str = __name__,
) -> dict[str, object]:
    wrapper_factory = create_active_vision_wrapper_factory(
        active_vision_module=active_vision_module,
        module_name=module_name,
    )
    exports: dict[str, object] = {"_active_vision_wrapper": wrapper_factory}
    for constant_name in ACTIVE_VISION_CONSTANT_NAMES:
        exports[constant_name] = getattr(active_vision_module, constant_name)
    for wrapper_name in ACTIVE_VISION_COMPAT_WRAPPER_NAMES:
        exports[wrapper_name] = wrapper_factory(wrapper_name)
    return exports


def create_active_vision_bridge(
    *,
    active_vision_module: Any = _active_vision,
    screen_capture_bridge_factory: Callable[[], Any] = _screen_capture_wiring.screen_capture_bridge,
    payload_normalizer: Callable[[dict], dict] = ScreenVisionController._payload_from_frame_payload,
    vision_config_normalizer: Callable[[dict], dict] = normalize_vision_config,
    active_observation_config_normalizer: Callable[[dict], dict] = normalize_active_observation_config,
) -> _active_vision_bridge_module.ActiveVisionBridge:
    return _active_vision_bridge_module.ActiveVisionBridge(
        active_vision_module=active_vision_module,
        screen_capture_bridge_factory=screen_capture_bridge_factory,
        payload_normalizer=payload_normalizer,
        vision_config_normalizer=vision_config_normalizer,
        active_observation_config_normalizer=active_observation_config_normalizer,
    )


def create_discover_active_vision_target_candidates_entry(
    bridge_factory: Callable[[], Any],
    *,
    default_desktop_targets_provider,
    default_dock_items_provider,
    default_running_apps_provider,
    default_runner=subprocess.run,
    module_name: str = __name__,
):
    def discover_active_vision_target_candidates(
        *,
        desktop_targets: list[dict] | None = None,
        desktop_targets_provider=default_desktop_targets_provider,
        dock_items_provider=default_dock_items_provider,
        running_apps_provider=default_running_apps_provider,
        target_candidates: list[dict] | None = None,
        display_layout: list[dict] | None = None,
        target_hint: str = "",
        platform_name: str | None = None,
        runner=default_runner,
    ) -> dict:
        return bridge_factory().discover_active_vision_target_candidates(
            desktop_targets=desktop_targets,
            desktop_targets_provider=desktop_targets_provider,
            dock_items_provider=dock_items_provider,
            running_apps_provider=running_apps_provider,
            target_candidates=target_candidates,
            display_layout=display_layout,
            target_hint=target_hint,
            platform_name=platform_name,
            runner=runner,
            default_desktop_targets_provider=default_desktop_targets_provider,
            default_dock_items_provider=default_dock_items_provider,
            default_running_apps_provider=default_running_apps_provider,
        )

    discover_active_vision_target_candidates.__module__ = module_name
    return discover_active_vision_target_candidates


def create_capture_active_vision_frame_payload_entry(
    bridge_factory: Callable[[], Any],
    *,
    default_frame_encoder,
    default_observation_provider,
    default_desktop_targets_provider,
    default_dock_items_provider,
    default_running_apps_provider,
    default_interaction_runner,
    default_sleeper=time.sleep,
    module_name: str = __name__,
):
    def capture_active_vision_frame_payload(
        window,
        command_payload: dict,
        config: dict,
        *,
        screen_provider=None,
        frame_encoder=default_frame_encoder,
        observation_provider=default_observation_provider,
        desktop_targets_provider=default_desktop_targets_provider,
        dock_items_provider=default_dock_items_provider,
        running_apps_provider=default_running_apps_provider,
        interaction_runner=default_interaction_runner,
        sleeper=default_sleeper,
        platform_name: str | None = None,
    ) -> dict:
        return bridge_factory().capture_active_vision_frame_payload(
            window,
            command_payload,
            config,
            screen_provider=screen_provider,
            frame_encoder=frame_encoder,
            observation_provider=observation_provider,
            desktop_targets_provider=desktop_targets_provider,
            dock_items_provider=dock_items_provider,
            running_apps_provider=running_apps_provider,
            interaction_runner=interaction_runner,
            sleeper=sleeper,
            platform_name=platform_name,
            default_frame_encoder=default_frame_encoder,
            default_observation_provider=default_observation_provider,
            default_desktop_targets_provider=default_desktop_targets_provider,
            default_dock_items_provider=default_dock_items_provider,
            default_running_apps_provider=default_running_apps_provider,
            default_interaction_runner=default_interaction_runner,
        )

    capture_active_vision_frame_payload.__module__ = module_name
    return capture_active_vision_frame_payload
