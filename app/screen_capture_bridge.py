from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ScreenCaptureBridge:
    screen_capture_module: Any
    qt_deps_factory: Callable[[], Any]
    is_macos_func: Callable[[str | None], bool]
    vision_config_normalizer: Callable[[dict], dict]

    def _qt_deps(self) -> Any:
        return self.qt_deps_factory()

    @staticmethod
    def _unwrap_default_provider(provider: Any, *, default_provider: Any, body_provider: Any) -> Any:
        if default_provider is not None and provider is default_provider:
            return body_provider
        return provider

    def unwrap_capture_screen_frame_payload_provider(self, provider: Any, *, default_provider: Any) -> Any:
        return self._unwrap_default_provider(
            provider,
            default_provider=default_provider,
            body_provider=self.screen_capture_module.capture_screen_frame_payload,
        )

    def unwrap_capture_qt_screen_frame_payload_provider(self, provider: Any, *, default_provider: Any) -> Any:
        return self._unwrap_default_provider(
            provider,
            default_provider=default_provider,
            body_provider=self.screen_capture_module.capture_qt_screen_frame_payload,
        )

    def unwrap_capture_macos_screencapture_payload_provider(self, provider: Any, *, default_provider: Any) -> Any:
        return self._unwrap_default_provider(
            provider,
            default_provider=default_provider,
            body_provider=self.screen_capture_module.capture_macos_screencapture_payload,
        )

    def unwrap_pixmap_encoder_provider(self, provider: Any, *, default_provider: Any) -> Any:
        return self._unwrap_default_provider(
            provider,
            default_provider=default_provider,
            body_provider=self.screen_capture_module._encode_pixmap_frame,
        )

    def unwrap_pixmap_composer_provider(self, provider: Any, *, default_provider: Any) -> Any:
        return self._unwrap_default_provider(
            provider,
            default_provider=default_provider,
            body_provider=self.screen_capture_module.compose_qt_screens_pixmap,
        )

    def unwrap_collect_screen_observations_provider(self, provider: Any, *, default_provider: Any) -> Any:
        return self._unwrap_default_provider(
            provider,
            default_provider=default_provider,
            body_provider=self.screen_capture_module.collect_screen_observations,
        )

    def qiodevice_write_only_mode(self) -> object:
        return self.screen_capture_module._qiodevice_write_only_mode(self._qt_deps().q_io_device)

    def qt_smooth_transformation_mode(self) -> object:
        return self.screen_capture_module._qt_smooth_transformation_mode(self._qt_deps().qt)

    def image_like_dimensions(self, image_like) -> tuple[int, int]:
        return self.screen_capture_module._image_like_dimensions(image_like)

    def encode_pixmap_frame(self, pixmap, config: dict) -> tuple[str, str, int, int]:
        return self.screen_capture_module._encode_pixmap_frame(pixmap, config, qt_deps=self._qt_deps())

    def encoded_frame_parts(self, encoded_frame) -> tuple[str, str, int, int]:
        return self.screen_capture_module._encoded_frame_parts(encoded_frame)

    def vision_hash_from_data_url(self, data_url: str) -> str:
        return self.screen_capture_module._vision_hash_from_data_url(data_url)

    def visual_hash_from_image_like(self, image_like) -> str:
        return self.screen_capture_module._visual_hash_from_image_like(image_like)

    def visual_hash_from_pixmap(self, pixmap) -> str:
        return self.screen_capture_module._visual_hash_from_pixmap(pixmap)

    def screen_layout_entry(self, screen) -> dict:
        return self.screen_capture_module._screen_layout_entry(screen)

    def screen_layout(self, screens: list) -> list[dict]:
        return self.screen_capture_module._screen_layout(screens)

    def payload_from_encoded_frame(
        self,
        mime_type: str,
        data_url: str,
        *,
        capture_backend: str,
        display_layout: list[dict] | None = None,
        visual_hash: str = "",
        image_width: int = 0,
        image_height: int = 0,
    ) -> dict:
        return self.screen_capture_module._payload_from_encoded_frame(
            mime_type,
            data_url,
            capture_backend=capture_backend,
            display_layout=display_layout,
            visual_hash=visual_hash,
            image_width=image_width,
            image_height=image_height,
        )

    def compose_qt_screens_pixmap(self, screens: list) -> tuple[object, dict]:
        return self.screen_capture_module.compose_qt_screens_pixmap(screens, qt_deps=self._qt_deps())

    def capture_qt_screen_frame_payload(
        self,
        screen,
        config: dict,
        *,
        screens_provider=None,
        pixmap_composer=None,
        pixmap_encoder=None,
        default_pixmap_composer=None,
        default_pixmap_encoder=None,
    ) -> dict:
        composer = self.unwrap_pixmap_composer_provider(
            pixmap_composer,
            default_provider=default_pixmap_composer,
        )
        encoder = self.unwrap_pixmap_encoder_provider(
            pixmap_encoder,
            default_provider=default_pixmap_encoder,
        )
        return self.screen_capture_module.capture_qt_screen_frame_payload(
            screen,
            config,
            screens_provider=screens_provider,
            pixmap_composer=composer,
            pixmap_encoder=encoder,
            qt_deps=self._qt_deps(),
        )

    def capture_macos_screencapture_payload(
        self,
        config: dict,
        *,
        runner,
        screens_provider=None,
        display_layout: list[dict] | None = None,
    ) -> dict:
        return self.screen_capture_module.capture_macos_screencapture_payload(
            config,
            runner=runner,
            screens_provider=screens_provider,
            display_layout=display_layout,
            qt_deps=self._qt_deps(),
        )

    def capture_screen_frame_payload(
        self,
        screen,
        config: dict,
        *,
        platform_name: str | None = None,
        macos_capture=None,
        qt_capture=None,
        default_macos_capture=None,
        default_qt_capture=None,
        allow_qt_fallback: bool = True,
        display_layout: list[dict] | None = None,
    ) -> dict:
        macos_capture_func = self.unwrap_capture_macos_screencapture_payload_provider(
            macos_capture,
            default_provider=default_macos_capture,
        )
        qt_capture_func = self.unwrap_capture_qt_screen_frame_payload_provider(
            qt_capture,
            default_provider=default_qt_capture,
        )
        return self.screen_capture_module.capture_screen_frame_payload(
            screen,
            config,
            platform_name=platform_name,
            macos_capture=macos_capture_func,
            qt_capture=qt_capture_func,
            allow_qt_fallback=allow_qt_fallback,
            display_layout=display_layout,
            qt_deps=self._qt_deps(),
            is_macos_func=self.is_macos_func,
        )

    def encode_screen_frame(self, screen, config: dict) -> tuple[str, str]:
        return self.screen_capture_module.encode_screen_frame(screen, config, qt_deps=self._qt_deps())

    def clean_vision_text(self, value, *, max_length: int) -> str:
        return self.screen_capture_module._clean_vision_text(value, max_length=max_length)

    def collect_screen_observations(
        self,
        config: dict | None = None,
        *,
        platform_name: str | None = None,
        runner,
    ) -> dict:
        return self.screen_capture_module.collect_screen_observations(
            config,
            platform_name=platform_name,
            runner=runner,
            vision_config_normalizer=self.vision_config_normalizer,
            is_macos_func=self.is_macos_func,
        )
