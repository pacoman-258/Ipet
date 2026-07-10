from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from body.screen_capture import _clean_vision_text


@dataclass(frozen=True)
class ClickPreviewQtDependencies:
    qt: Any
    widget_base: type
    q_painter: type
    q_color: Callable[..., Any]


def normalize_click_preview_payload(payload: dict | None) -> dict[str, object]:
    source = payload if isinstance(payload, dict) else {}
    try:
        x = int(round(float(source.get("x"))))
        y = int(round(float(source.get("y"))))
    except Exception:
        return {}
    try:
        size = int(round(float(source.get("size", 20))))
    except Exception:
        size = 20
    size = max(10, min(48, size))
    label = _clean_vision_text(source.get("label") or source.get("target") or "目标位置", max_length=120) or "目标位置"
    return {"x": x, "y": y, "size": size, "label": label}


def show_click_preview(
    owner: Any,
    payload: str,
    *,
    overlay_factory: Callable[[], Any],
    json_loads: Callable[[str], Any] = json.loads,
    print_func: Callable[[str], None] = print,
) -> None:
    try:
        data = json_loads(str(payload or "{}"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        if owner.click_preview_overlay is None:
            owner.click_preview_overlay = overlay_factory()
        owner.click_preview_overlay.show_preview(data)
    except Exception as exc:
        print_func(f"点击预览显示失败: {exc}")


def hide_click_preview(owner: Any) -> None:
    overlay = getattr(owner, "click_preview_overlay", None)
    if overlay is not None:
        try:
            overlay.hide()
        except Exception:
            pass


def create_click_preview_overlay_class(deps: ClickPreviewQtDependencies):
    qt = deps.qt
    widget_base = deps.widget_base
    q_painter = deps.q_painter
    q_color = deps.q_color

    class ClickPreviewOverlay(widget_base):
        def __init__(self):
            super().__init__(None)
            flags = (
                qt.WindowType.FramelessWindowHint
                | qt.WindowType.WindowStaysOnTopHint
                | qt.WindowType.ToolTip
            )
            no_focus_flag = getattr(qt.WindowType, "WindowDoesNotAcceptFocus", None)
            if no_focus_flag is not None:
                flags |= no_focus_flag
            self.setWindowFlags(flags)
            self.setAttribute(qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            show_without_activating = getattr(qt.WidgetAttribute, "WA_ShowWithoutActivating", None)
            if show_without_activating is not None:
                self.setAttribute(show_without_activating, True)

        def paintEvent(self, event) -> None:
            painter = q_painter(self)
            try:
                painter.setRenderHint(q_painter.RenderHint.Antialiasing, True)
                painter.setBrush(q_color(255, 59, 48, 245))
                painter.setPen(q_color(255, 255, 255, 245))
                margin = 3
                painter.drawEllipse(
                    margin,
                    margin,
                    max(1, self.width() - margin * 2),
                    max(1, self.height() - margin * 2),
                )
            finally:
                painter.end()

        def show_preview(self, payload: dict | None) -> bool:
            preview = normalize_click_preview_payload(payload)
            if not preview:
                self.hide()
                return False
            size = int(preview["size"])
            self.setFixedSize(size, size)
            self.setToolTip(str(preview["label"]))
            self.move(int(preview["x"]) - size // 2, int(preview["y"]) - size // 2)
            self.show()
            self.raise_()
            self.update()
            return True

    return ClickPreviewOverlay
