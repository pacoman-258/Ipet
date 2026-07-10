from __future__ import annotations


TRANSPARENT_BACKGROUND_COLOR = (0, 0, 0, 0)
OPAQUE_BACKGROUND_COLOR = (18, 18, 18, 255)


def desktop_pet_window_flags(window_type, *, is_macos: bool):
    if is_macos:
        return window_type.Window
    return (
        window_type.FramelessWindowHint
        | window_type.WindowStaysOnTopHint
        | window_type.Tool
    )


def should_use_translucent_window(*, force_opaque: bool, is_macos: bool) -> bool:
    return (not bool(force_opaque)) and (not bool(is_macos))


def desktop_pet_background_color(*, translucent: bool) -> tuple[int, int, int, int]:
    if translucent:
        return TRANSPARENT_BACKGROUND_COLOR
    return OPAQUE_BACKGROUND_COLOR
