from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class PreviewKind(str, Enum):
    RED_DOT = "red_dot"
    TEXT = "text"
    HOTKEY = "hotkey"
    PATH = "path"


@dataclass(frozen=True)
class ClickPreview:
    x: int
    y: int
    label: str = ""
    kind: PreviewKind = PreviewKind.RED_DOT
    size: int = 24

    def to_dict(self) -> dict[str, Any]:
        size = max(10, min(48, int(self.size)))
        return {
            "kind": self.kind.value,
            "marker": "red_dot",
            "x": int(self.x),
            "y": int(self.y),
            "label": str(self.label or ""),
            "size": size,
        }


def red_dot_click_preview(*, x: int, y: int, label: str = "") -> ClickPreview:
    return ClickPreview(x=int(x), y=int(y), label=str(label or ""))
