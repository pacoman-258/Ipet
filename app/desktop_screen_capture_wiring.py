from __future__ import annotations

import subprocess
import sys

from app import desktop_runtime as _desktop_runtime
from app import screen_capture_bridge as _screen_capture_bridge_module
from backend.vision import normalize_vision_config
from body import screen_capture as _screen_capture


def _is_macos(platform_name: str | None = None) -> bool:
    return _desktop_runtime.is_macos(platform_name, sys_platform=sys.platform)


def screen_capture_bridge() -> _screen_capture_bridge_module.ScreenCaptureBridge:
    return _screen_capture_bridge_module.ScreenCaptureBridge(
        screen_capture_module=_screen_capture,
        qt_deps_factory=_screen_capture_qt_deps,
        is_macos_func=_is_macos,
        vision_config_normalizer=normalize_vision_config,
    )


def create_screen_capture_compat_exports(
    *,
    module_globals: dict[str, object] | None = None,
    module_name: str = __name__,
    bridge_factory=screen_capture_bridge,
    bridge_lookup_name: str = "_screen_capture_bridge",
    install_bridge_entry: bool = True,
) -> dict[str, object]:
    globals_for_lookup = module_globals if module_globals is not None else {}

    def _screen_capture_qt_deps() -> _screen_capture.QtScreenCaptureDependencies:
        return _screen_capture.load_qt_screen_capture_dependencies()

    def _screen_capture_bridge():
        return bridge_factory()

    bridge_entry = _screen_capture_bridge

    def installed_bridge():
        return globals_for_lookup.get(bridge_lookup_name, bridge_entry)()

    def _qiodevice_write_only_mode():
        return installed_bridge().qiodevice_write_only_mode()

    def _qt_smooth_transformation_mode():
        return installed_bridge().qt_smooth_transformation_mode()

    def _image_like_dimensions(image_like) -> tuple[int, int]:
        return installed_bridge().image_like_dimensions(image_like)

    def _encode_pixmap_frame(pixmap, config: dict) -> tuple[str, str, int, int]:
        return installed_bridge().encode_pixmap_frame(pixmap, config)

    def _encoded_frame_parts(encoded_frame) -> tuple[str, str, int, int]:
        return installed_bridge().encoded_frame_parts(encoded_frame)

    def _vision_hash_from_data_url(data_url: str) -> str:
        return installed_bridge().vision_hash_from_data_url(data_url)

    def _visual_hash_from_image_like(image_like) -> str:
        return installed_bridge().visual_hash_from_image_like(image_like)

    def _visual_hash_from_pixmap(pixmap) -> str:
        return installed_bridge().visual_hash_from_pixmap(pixmap)

    def _screen_layout_entry(screen) -> dict:
        return installed_bridge().screen_layout_entry(screen)

    def _screen_layout(screens: list) -> list[dict]:
        return installed_bridge().screen_layout(screens)

    def _payload_from_encoded_frame(
        mime_type: str,
        data_url: str,
        *,
        capture_backend: str,
        display_layout: list[dict] | None = None,
        visual_hash: str = "",
        image_width: int = 0,
        image_height: int = 0,
    ) -> dict:
        return installed_bridge().payload_from_encoded_frame(
            mime_type,
            data_url,
            capture_backend=capture_backend,
            display_layout=display_layout,
            visual_hash=visual_hash,
            image_width=image_width,
            image_height=image_height,
        )

    def compose_qt_screens_pixmap(screens: list) -> tuple[object, dict]:
        return installed_bridge().compose_qt_screens_pixmap(screens)

    def capture_qt_screen_frame_payload(
        screen,
        config: dict,
        *,
        screens_provider=None,
        pixmap_composer=compose_qt_screens_pixmap,
        pixmap_encoder=_encode_pixmap_frame,
    ) -> dict:
        return installed_bridge().capture_qt_screen_frame_payload(
            screen,
            config,
            screens_provider=screens_provider,
            pixmap_composer=pixmap_composer,
            pixmap_encoder=pixmap_encoder,
            default_pixmap_composer=globals_for_lookup.get(
                "compose_qt_screens_pixmap",
                compose_qt_screens_pixmap,
            ),
            default_pixmap_encoder=globals_for_lookup.get("_encode_pixmap_frame", _encode_pixmap_frame),
        )

    def capture_macos_screencapture_payload(
        config: dict,
        *,
        runner=subprocess.run,
        screens_provider=None,
        display_layout: list[dict] | None = None,
    ) -> dict:
        return installed_bridge().capture_macos_screencapture_payload(
            config,
            runner=runner,
            screens_provider=screens_provider,
            display_layout=display_layout,
        )

    def capture_screen_frame_payload(
        screen,
        config: dict,
        *,
        platform_name: str | None = None,
        macos_capture=capture_macos_screencapture_payload,
        qt_capture=capture_qt_screen_frame_payload,
        allow_qt_fallback: bool = True,
        display_layout: list[dict] | None = None,
    ) -> dict:
        return installed_bridge().capture_screen_frame_payload(
            screen,
            config,
            platform_name=platform_name,
            macos_capture=macos_capture,
            qt_capture=qt_capture,
            default_macos_capture=globals_for_lookup.get(
                "capture_macos_screencapture_payload",
                capture_macos_screencapture_payload,
            ),
            default_qt_capture=globals_for_lookup.get(
                "capture_qt_screen_frame_payload",
                capture_qt_screen_frame_payload,
            ),
            allow_qt_fallback=allow_qt_fallback,
            display_layout=display_layout,
        )

    def encode_screen_frame(screen, config: dict) -> tuple[str, str]:
        return installed_bridge().encode_screen_frame(screen, config)

    def _clean_vision_text(value, *, max_length: int) -> str:
        return installed_bridge().clean_vision_text(value, max_length=max_length)

    def collect_screen_observations(
        config: dict | None = None,
        *,
        platform_name: str | None = None,
        runner=subprocess.run,
    ) -> dict:
        return installed_bridge().collect_screen_observations(
            config,
            platform_name=platform_name,
            runner=runner,
        )

    entries = {
        "_screen_capture_qt_deps": _screen_capture_qt_deps,
        "_qiodevice_write_only_mode": _qiodevice_write_only_mode,
        "_qt_smooth_transformation_mode": _qt_smooth_transformation_mode,
        "VISION_CAPTURE_SCOPE": _screen_capture.VISION_CAPTURE_SCOPE,
        "_image_like_dimensions": _image_like_dimensions,
        "_encode_pixmap_frame": _encode_pixmap_frame,
        "_encoded_frame_parts": _encoded_frame_parts,
        "_vision_hash_from_data_url": _vision_hash_from_data_url,
        "_visual_hash_from_image_like": _visual_hash_from_image_like,
        "_visual_hash_from_pixmap": _visual_hash_from_pixmap,
        "_screen_layout_entry": _screen_layout_entry,
        "_screen_layout": _screen_layout,
        "_payload_from_encoded_frame": _payload_from_encoded_frame,
        "compose_qt_screens_pixmap": compose_qt_screens_pixmap,
        "capture_qt_screen_frame_payload": capture_qt_screen_frame_payload,
        "capture_macos_screencapture_payload": capture_macos_screencapture_payload,
        "capture_screen_frame_payload": capture_screen_frame_payload,
        "encode_screen_frame": encode_screen_frame,
        "_clean_vision_text": _clean_vision_text,
        "collect_screen_observations": collect_screen_observations,
    }
    if install_bridge_entry:
        bridge_entry.__name__ = bridge_lookup_name
        entries[bridge_lookup_name] = bridge_entry
    for name, entry in entries.items():
        if callable(entry):
            entry.__module__ = module_name
    return entries


globals().update(
    create_screen_capture_compat_exports(
        module_globals=globals(),
        bridge_lookup_name="screen_capture_bridge",
        install_bridge_entry=False,
    )
)
