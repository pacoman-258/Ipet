from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import desktop_runtime as _desktop_runtime
from backend.vision import DEFAULT_VISION_CONFIG, normalize_vision_config


VISION_CAPTURE_SCOPE = "visible_spaces_all_displays"


@dataclass(frozen=True)
class QtScreenCaptureDependencies:
    q_byte_array: Any
    q_buffer: Any
    q_io_device: Any
    qt: Any
    q_gui_application: Any
    q_image: Any
    q_painter: Any
    q_pixmap: Any
    q_color: Any


_QT_DEPS: QtScreenCaptureDependencies | None = None


def load_qt_screen_capture_dependencies() -> QtScreenCaptureDependencies:
    global _QT_DEPS
    if _QT_DEPS is not None:
        return _QT_DEPS

    if _desktop_runtime.prefer_pyqt_bindings(sys_platform=sys.platform):
        try:
            from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt
            from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap
        except ImportError:
            from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
            from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap
    else:
        try:
            from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
            from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap
        except ImportError:
            from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt
            from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap

    _QT_DEPS = QtScreenCaptureDependencies(
        q_byte_array=QByteArray,
        q_buffer=QBuffer,
        q_io_device=QIODevice,
        qt=Qt,
        q_gui_application=QGuiApplication,
        q_image=QImage,
        q_painter=QPainter,
        q_pixmap=QPixmap,
        q_color=QColor,
    )
    return _QT_DEPS


def _qt_deps(qt_deps: QtScreenCaptureDependencies | None = None) -> QtScreenCaptureDependencies:
    return qt_deps or load_qt_screen_capture_dependencies()


def _qiodevice_write_only_mode(q_io_device) -> object:
    open_mode = getattr(q_io_device, "OpenModeFlag", None)
    if open_mode is not None:
        return open_mode.WriteOnly
    return q_io_device.WriteOnly


def _qt_smooth_transformation_mode(qt) -> object:
    transform_mode = getattr(qt, "TransformationMode", None)
    if transform_mode is not None:
        return transform_mode.SmoothTransformation
    return qt.SmoothTransformation


def _image_like_dimensions(image_like) -> tuple[int, int]:
    try:
        width = int(image_like.width())
        height = int(image_like.height())
    except Exception:
        return 0, 0
    return (width, height) if width > 0 and height > 0 else (0, 0)


def _encode_pixmap_frame(
    pixmap,
    config: dict,
    *,
    qt_deps: QtScreenCaptureDependencies | None = None,
    default_vision_config: dict | None = None,
) -> tuple[str, str, int, int]:
    deps = _qt_deps(qt_deps)
    defaults = default_vision_config or DEFAULT_VISION_CONFIG
    max_width = int(config.get("max_width", defaults["max_width"]))
    try:
        if pixmap.width() > max_width:
            pixmap = pixmap.scaledToWidth(max_width, _qt_smooth_transformation_mode(deps.qt))
    except Exception:
        pass
    image_width, image_height = _image_like_dimensions(pixmap)
    quality = int(config.get("jpeg_quality", defaults["jpeg_quality"]))
    data = deps.q_byte_array()
    buffer = deps.q_buffer(data)
    buffer.open(_qiodevice_write_only_mode(deps.q_io_device))
    try:
        if not pixmap.save(buffer, "JPEG", quality):
            raise RuntimeError("failed to encode screen frame")
    finally:
        try:
            buffer.close()
        except Exception:
            pass
    encoded = base64.b64encode(bytes(data)).decode("ascii")
    return "image/jpeg", f"data:image/jpeg;base64,{encoded}", image_width, image_height


def _encoded_frame_parts(encoded_frame) -> tuple[str, str, int, int]:
    if isinstance(encoded_frame, dict):
        mime_type = str(encoded_frame.get("mime_type") or "")
        data_url = str(encoded_frame.get("data_url") or "")
        try:
            image_width = int(encoded_frame.get("image_width") or 0)
            image_height = int(encoded_frame.get("image_height") or 0)
        except Exception:
            image_width = 0
            image_height = 0
        return mime_type, data_url, image_width, image_height
    if not isinstance(encoded_frame, (list, tuple)) or len(encoded_frame) < 2:
        raise RuntimeError("encoded screen frame must contain mime_type and data_url")
    mime_type = str(encoded_frame[0] or "")
    data_url = str(encoded_frame[1] or "")
    image_width = 0
    image_height = 0
    if len(encoded_frame) >= 4:
        try:
            image_width = int(encoded_frame[2] or 0)
            image_height = int(encoded_frame[3] or 0)
        except Exception:
            image_width = 0
            image_height = 0
    return mime_type, data_url, max(0, image_width), max(0, image_height)


def _vision_hash_from_data_url(data_url: str) -> str:
    return "sha256:" + hashlib.sha256(str(data_url or "").encode("utf-8")).hexdigest()


def _visual_hash_from_image_like(image_like) -> str:
    try:
        image = image_like.toImage() if hasattr(image_like, "toImage") else image_like
        image = image.scaled(8, 8)
        values: list[int] = []
        for y in range(8):
            for x in range(8):
                color = image.pixelColor(x, y)
                values.append((int(color.red()) * 299 + int(color.green()) * 587 + int(color.blue()) * 114) // 1000)
        if not values:
            return ""
        average = sum(values) / len(values)
        bits = 0
        for value in values:
            bits = (bits << 1) | (1 if value >= average else 0)
        return f"ahash:{bits:016x}"
    except Exception:
        return ""


def _visual_hash_from_pixmap(pixmap) -> str:
    return _visual_hash_from_image_like(pixmap)


def _clean_vision_text(value, *, max_length: int) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = " ".join(text.split())
    return text[:max_length]


def _screen_layout_entry(screen) -> dict:
    geometry = screen.geometry()
    entry = {
        "x": int(geometry.x()),
        "y": int(geometry.y()),
        "width": int(geometry.width()),
        "height": int(geometry.height()),
    }
    try:
        entry["name"] = _clean_vision_text(screen.name(), max_length=80)
    except Exception:
        entry["name"] = ""
    try:
        entry["device_pixel_ratio"] = float(screen.devicePixelRatio())
    except Exception:
        entry["device_pixel_ratio"] = 1.0
    return entry


def _screen_layout(screens: list) -> list[dict]:
    return [_screen_layout_entry(screen) for screen in screens]


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
    frame_hash = _vision_hash_from_data_url(data_url)
    layout = list(display_layout or [])
    payload = {
        "mime_type": mime_type,
        "data_url": data_url,
        "frame_hash": frame_hash,
        "visual_hash": visual_hash or frame_hash,
        "capture_backend": capture_backend,
        "capture_scope": VISION_CAPTURE_SCOPE,
        "display_count": len(layout) if layout else 0,
        "display_layout": layout,
    }
    if image_width > 0 and image_height > 0:
        payload["image_width"] = int(image_width)
        payload["image_height"] = int(image_height)
    return payload


def compose_qt_screens_pixmap(
    screens: list,
    *,
    qt_deps: QtScreenCaptureDependencies | None = None,
) -> tuple[object, dict]:
    if not screens:
        raise RuntimeError("no screens are available")
    deps = _qt_deps(qt_deps)
    layout = _screen_layout(screens)
    min_x = min(item["x"] for item in layout)
    min_y = min(item["y"] for item in layout)
    max_x = max(item["x"] + item["width"] for item in layout)
    max_y = max(item["y"] + item["height"] for item in layout)
    canvas = deps.q_pixmap(max(1, max_x - min_x), max(1, max_y - min_y))
    canvas.fill(deps.q_color(0, 0, 0))
    painter = deps.q_painter(canvas)
    try:
        for screen, item in zip(screens, layout):
            pixmap = screen.grabWindow(0)
            painter.drawPixmap(item["x"] - min_x, item["y"] - min_y, pixmap)
    finally:
        painter.end()
    return canvas, {"display_count": len(screens), "display_layout": layout}


def capture_qt_screen_frame_payload(
    screen,
    config: dict,
    *,
    screens_provider=None,
    pixmap_composer=None,
    pixmap_encoder=None,
    qt_deps: QtScreenCaptureDependencies | None = None,
) -> dict:
    deps = qt_deps
    if screens_provider is None:
        deps = _qt_deps(deps)
        screens_provider = deps.q_gui_application.screens
    screens = list(screens_provider() or [])
    if not screens and screen is not None:
        screens = [screen]

    composer = pixmap_composer or compose_qt_screens_pixmap
    if composer is compose_qt_screens_pixmap:
        deps = _qt_deps(deps)
        pixmap, metadata = composer(screens, qt_deps=deps)
    else:
        pixmap, metadata = composer(screens)

    encoder = pixmap_encoder or _encode_pixmap_frame
    if encoder is _encode_pixmap_frame:
        deps = _qt_deps(deps)
        encoded_frame = encoder(pixmap, config, qt_deps=deps)
    else:
        encoded_frame = encoder(pixmap, config)

    mime_type, data_url, image_width, image_height = _encoded_frame_parts(encoded_frame)
    payload = _payload_from_encoded_frame(
        mime_type,
        data_url,
        capture_backend="qt_fallback",
        display_layout=metadata.get("display_layout") if isinstance(metadata, dict) else [],
        visual_hash=_visual_hash_from_pixmap(pixmap),
        image_width=image_width,
        image_height=image_height,
    )
    if isinstance(metadata, dict) and metadata.get("display_count") is not None:
        payload["display_count"] = int(metadata.get("display_count") or payload["display_count"])
    return payload


def capture_macos_screencapture_payload(
    config: dict,
    *,
    runner=subprocess.run,
    screens_provider=None,
    display_layout: list[dict] | None = None,
    qt_deps: QtScreenCaptureDependencies | None = None,
    screencapture_path: str | Path = "/usr/sbin/screencapture",
    temp_file_factory=None,
) -> dict:
    binary = Path(screencapture_path)
    if not binary.exists():
        raise RuntimeError("macOS screencapture is unavailable")
    deps = _qt_deps(qt_deps)
    tmp_path = ""
    try:
        factory = temp_file_factory or tempfile.NamedTemporaryFile
        with factory(prefix="ipet-screen-", suffix=".jpg", delete=False) as tmp_file:
            tmp_path = tmp_file.name
        result = runner(
            [str(binary), "-x", "-t", "jpg", tmp_path],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        if getattr(result, "returncode", 1) != 0:
            detail = _clean_vision_text(getattr(result, "stderr", ""), max_length=160)
            raise RuntimeError(detail or "screencapture failed")
        image = deps.q_image(tmp_path)
        if image.isNull():
            raise RuntimeError("screencapture produced an unreadable image")
        mime_type, data_url, image_width, image_height = _encoded_frame_parts(
            _encode_pixmap_frame(image, config, qt_deps=deps)
        )
        if display_layout is not None:
            layout = [dict(item) for item in display_layout]
        else:
            provider = screens_provider or deps.q_gui_application.screens
            layout = _screen_layout(list(provider() or []))
        return _payload_from_encoded_frame(
            mime_type,
            data_url,
            capture_backend="macos_screencapture",
            display_layout=layout,
            visual_hash=_visual_hash_from_image_like(image),
            image_width=image_width,
            image_height=image_height,
        )
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


def capture_screen_frame_payload(
    screen,
    config: dict,
    *,
    platform_name: str | None = None,
    macos_capture=None,
    qt_capture=None,
    allow_qt_fallback: bool = True,
    display_layout: list[dict] | None = None,
    qt_deps: QtScreenCaptureDependencies | None = None,
    is_macos_func=None,
) -> dict:
    macos_capture_func = macos_capture or capture_macos_screencapture_payload
    qt_capture_func = qt_capture or capture_qt_screen_frame_payload
    is_macos = is_macos_func or (lambda value=None: _desktop_runtime.is_macos(value, sys_platform=sys.platform))
    if is_macos(platform_name):
        try:
            if macos_capture_func is capture_macos_screencapture_payload:
                return macos_capture_func(config, display_layout=display_layout, qt_deps=qt_deps)
            return macos_capture_func(config)
        except Exception:
            if not allow_qt_fallback:
                raise
    if qt_capture_func is capture_qt_screen_frame_payload:
        return qt_capture_func(screen, config, qt_deps=qt_deps)
    return qt_capture_func(screen, config)


def encode_screen_frame(
    screen,
    config: dict,
    *,
    qt_deps: QtScreenCaptureDependencies | None = None,
) -> tuple[str, str]:
    payload = capture_qt_screen_frame_payload(screen, config, qt_deps=qt_deps)
    return payload["mime_type"], payload["data_url"]


def collect_screen_observations(
    config: dict | None = None,
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    vision_config_normalizer=normalize_vision_config,
    is_macos_func=None,
) -> dict:
    vision_cfg = vision_config_normalizer(config or {})
    is_macos = is_macos_func or (lambda value=None: _desktop_runtime.is_macos(value, sys_platform=sys.platform))
    if not vision_cfg.get("include_ui_metadata") or not is_macos(platform_name):
        return {}
    script = """
tell application "System Events"
    set frontProc to first application process whose frontmost is true
    set procName to name of frontProc
    try
        set windowTitle to name of front window of frontProc
    on error
        set windowTitle to ""
    end try
    try
        set menuNames to name of every menu bar item of menu bar 1 of frontProc
        set AppleScript's text item delimiters to ", "
        set menuText to menuNames as text
    on error
        set menuText to ""
    end try
end tell
return procName & linefeed & menuText & linefeed & windowTitle
""".strip()
    try:
        result = runner(["osascript", "-e", script], capture_output=True, text=True, timeout=0.8)
    except Exception as exc:
        return {"unknowns": [f"无法读取 macOS 前台应用元数据：{exc}"]}
    if getattr(result, "returncode", 1) != 0:
        detail = _clean_vision_text(getattr(result, "stderr", ""), max_length=120)
        return {"unknowns": [f"无法读取 macOS 前台应用元数据：{detail or 'osascript failed'}"]}
    lines = str(getattr(result, "stdout", "") or "").splitlines()
    app_name = _clean_vision_text(lines[0] if lines else "", max_length=80)
    menu_text = _clean_vision_text(lines[1] if len(lines) > 1 else "", max_length=240)
    window_title = _clean_vision_text(lines[2] if len(lines) > 2 else "", max_length=160)
    if not app_name:
        return {"unknowns": ["无法确认 macOS 前台应用"]}
    menu_items = [item.strip() for item in menu_text.split(",") if item.strip()]
    app_menu = app_name
    if len(menu_items) >= 2 and menu_items[0].lower() == "apple":
        app_menu = _clean_vision_text(menu_items[1], max_length=80) or app_name
    change_summary = f"macOS 前台应用切换为：{app_menu}"
    return {
        "change_summary": change_summary,
        "important_objects": [app_menu] if app_menu else [],
        "visible_text": menu_items[:6],
        "confidence": 0.88,
        "desktop_context": {
            "foreground_app": app_menu,
            "frontmost_process": app_name,
            "window_title": window_title,
            "menu_bar_items": menu_items[:16],
        },
    }
