from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ActiveVisionBridge:
    active_vision_module: Any
    screen_capture_bridge_factory: Callable[[], Any]
    payload_normalizer: Callable[[dict], dict]
    vision_config_normalizer: Callable[[dict], dict]
    active_observation_config_normalizer: Callable[[dict], dict]

    @staticmethod
    def _unwrap_default_provider(provider: Any, *, default_provider: Any, body_provider: Any) -> Any:
        if default_provider is not None and provider is default_provider:
            return body_provider
        return provider

    def unwrap_desktop_targets_provider(self, provider: Any, *, default_provider: Any) -> Any:
        return self._unwrap_default_provider(
            provider,
            default_provider=default_provider,
            body_provider=self.active_vision_module.enumerate_active_vision_desktop_targets,
        )

    def unwrap_dock_items_provider(self, provider: Any, *, default_provider: Any) -> Any:
        return self._unwrap_default_provider(
            provider,
            default_provider=default_provider,
            body_provider=self.active_vision_module.enumerate_macos_dock_item_candidates,
        )

    def unwrap_running_apps_provider(self, provider: Any, *, default_provider: Any) -> Any:
        return self._unwrap_default_provider(
            provider,
            default_provider=default_provider,
            body_provider=self.active_vision_module.enumerate_active_vision_running_app_candidates,
        )

    def unwrap_interaction_runner(self, provider: Any, *, default_provider: Any) -> Any:
        return self._unwrap_default_provider(
            provider,
            default_provider=default_provider,
            body_provider=self.active_vision_module.run_active_vision_light_interaction,
        )

    def discover_active_vision_target_candidates(
        self,
        *,
        desktop_targets: list[dict] | None = None,
        desktop_targets_provider=None,
        dock_items_provider=None,
        running_apps_provider=None,
        target_candidates: list[dict] | None = None,
        display_layout: list[dict] | None = None,
        target_hint: str = "",
        platform_name: str | None = None,
        runner=subprocess.run,
        default_desktop_targets_provider=None,
        default_dock_items_provider=None,
        default_running_apps_provider=None,
    ) -> dict:
        return self.active_vision_module.discover_active_vision_target_candidates(
            desktop_targets=desktop_targets,
            desktop_targets_provider=self.unwrap_desktop_targets_provider(
                desktop_targets_provider,
                default_provider=default_desktop_targets_provider,
            ),
            dock_items_provider=self.unwrap_dock_items_provider(
                dock_items_provider,
                default_provider=default_dock_items_provider,
            ),
            running_apps_provider=self.unwrap_running_apps_provider(
                running_apps_provider,
                default_provider=default_running_apps_provider,
            ),
            target_candidates=target_candidates,
            display_layout=display_layout,
            target_hint=target_hint,
            platform_name=platform_name,
            runner=runner,
        )

    def capture_active_vision_frame_payload(
        self,
        window,
        command_payload: dict,
        config: dict,
        *,
        screen_provider=None,
        frame_encoder=None,
        observation_provider=None,
        desktop_targets_provider=None,
        dock_items_provider=None,
        running_apps_provider=None,
        interaction_runner=None,
        sleeper=time.sleep,
        platform_name: str | None = None,
        default_frame_encoder=None,
        default_observation_provider=None,
        default_desktop_targets_provider=None,
        default_dock_items_provider=None,
        default_running_apps_provider=None,
        default_interaction_runner=None,
    ) -> dict:
        screen_bridge = self.screen_capture_bridge_factory()
        selected_frame_encoder = screen_bridge.unwrap_capture_screen_frame_payload_provider(
            frame_encoder,
            default_provider=default_frame_encoder,
        )
        selected_observation_provider = screen_bridge.unwrap_collect_screen_observations_provider(
            observation_provider,
            default_provider=default_observation_provider,
        )
        selected_default_frame_encoder = (
            selected_frame_encoder if default_frame_encoder is not None and frame_encoder is default_frame_encoder else default_frame_encoder
        )
        return self.active_vision_module.capture_active_vision_frame_payload(
            window,
            command_payload,
            config,
            screen_provider=screen_provider,
            frame_encoder=selected_frame_encoder,
            observation_provider=selected_observation_provider,
            desktop_targets_provider=self.unwrap_desktop_targets_provider(
                desktop_targets_provider,
                default_provider=default_desktop_targets_provider,
            ),
            dock_items_provider=self.unwrap_dock_items_provider(
                dock_items_provider,
                default_provider=default_dock_items_provider,
            ),
            running_apps_provider=self.unwrap_running_apps_provider(
                running_apps_provider,
                default_provider=default_running_apps_provider,
            ),
            interaction_runner=self.unwrap_interaction_runner(
                interaction_runner,
                default_provider=default_interaction_runner,
            ),
            sleeper=sleeper,
            platform_name=platform_name,
            payload_normalizer=self.payload_normalizer,
            default_frame_encoder=selected_default_frame_encoder,
            vision_config_normalizer=self.vision_config_normalizer,
            active_observation_config_normalizer=self.active_observation_config_normalizer,
        )
