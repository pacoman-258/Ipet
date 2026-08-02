from __future__ import annotations

import json
import math
from typing import Any


class WindowRegionController:
    MAX_REGIONS = 8

    def __init__(
        self,
        owner: Any,
        *,
        q_region_factory: Any,
        mouse_transparent_attribute: Any = None,
    ) -> None:
        self.owner = owner
        self.q_region_factory = q_region_factory
        self.mouse_transparent_attribute = mouse_transparent_attribute

    def apply_interactive_regions(self, payload: str) -> None:
        regions = self._parse_regions(payload)
        if not regions:
            self.clear_interactive_regions()
            return

        window_width = max(0, int(self.owner.width()))
        window_height = max(0, int(self.owner.height()))
        combined = None
        for item in regions:
            rect = self._clamped_rect(item, window_width=window_width, window_height=window_height)
            if rect is None:
                continue
            region = self.q_region_factory(*rect)
            combined = region if combined is None else combined.united(region)

        if combined is None:
            self.clear_interactive_regions()
            return
        self.owner.setMask(combined)
        self._set_mouse_transparent(False)

    def clear_interactive_regions(self) -> None:
        self.owner.clearMask()
        self._set_mouse_transparent(True)

    def _set_mouse_transparent(self, enabled: bool) -> None:
        if self.mouse_transparent_attribute is not None:
            self.owner.setAttribute(self.mouse_transparent_attribute, bool(enabled))

    def _parse_regions(self, payload: str) -> list[dict[str, Any]]:
        try:
            decoded = json.loads(str(payload or ""))
        except Exception:
            return []
        raw_regions = decoded.get("regions") if isinstance(decoded, dict) else None
        if not isinstance(raw_regions, list):
            return []
        return [item for item in raw_regions[: self.MAX_REGIONS] if isinstance(item, dict)]

    @staticmethod
    def _clamped_rect(
        item: dict[str, Any],
        *,
        window_width: int,
        window_height: int,
    ) -> tuple[int, int, int, int] | None:
        try:
            x = float(item.get("x"))
            y = float(item.get("y"))
            width = float(item.get("width"))
            height = float(item.get("height"))
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            return None
        if width <= 0 or height <= 0:
            return None

        left = max(0, min(window_width, math.floor(x)))
        top = max(0, min(window_height, math.floor(y)))
        right = max(0, min(window_width, math.ceil(x + width)))
        bottom = max(0, min(window_height, math.ceil(y + height)))
        if right <= left or bottom <= top:
            return None
        return (left, top, right - left, bottom - top)
