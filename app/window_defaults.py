from __future__ import annotations


TRANSPARENT_BACKGROUND_COLOR = (0, 0, 0, 0)
OPAQUE_BACKGROUND_COLOR = (18, 18, 18, 255)
DEFAULT_SCREEN_MARGIN = 18


def desktop_pet_window_flags(window_type, *, is_macos: bool):
    return (
        window_type.FramelessWindowHint
        | window_type.WindowStaysOnTopHint
        | window_type.Tool
    )


def always_visible_tool_window_attribute(widget_attribute, *, is_macos: bool):
    if not is_macos:
        return None
    return getattr(widget_attribute, "WA_MacAlwaysShowToolWindow", None)


def should_use_translucent_window(*, force_opaque: bool, is_macos: bool) -> bool:
    return not bool(force_opaque)


def desktop_pet_background_color(*, translucent: bool) -> tuple[int, int, int, int]:
    if translucent:
        return TRANSPARENT_BACKGROUND_COLOR
    return OPAQUE_BACKGROUND_COLOR


def visible_window_geometry(
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    available_x: int,
    available_y: int,
    available_width: int,
    available_height: int,
    margin: int = DEFAULT_SCREEN_MARGIN,
) -> tuple[int, int, int, int]:
    usable_width = max(1, int(available_width))
    usable_height = max(1, int(available_height))
    next_width = max(1, min(int(width), usable_width))
    next_height = max(1, min(int(height), usable_height))
    safe_margin = max(0, int(margin))

    min_x = int(available_x)
    min_y = int(available_y)
    max_x = min_x + usable_width - next_width
    max_y = min_y + usable_height - next_height

    requested_x = int(x)
    requested_y = int(y)
    if requested_x < 0:
        requested_x = max_x - min(safe_margin, max(0, max_x - min_x))
    if requested_y < 0:
        requested_y = max_y - min(safe_margin, max(0, max_y - min_y))

    next_x = min(max(requested_x, min_x), max_x)
    next_y = min(max(requested_y, min_y), max_y)
    return (next_x, next_y, next_width, next_height)
