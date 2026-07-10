from __future__ import annotations

import os
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Any

import requests

from app import desktop_runtime as _desktop_runtime
from backend.vision import normalize_vision_config
from body import screen_capture as _screen_capture


LOCAL_API_TOKEN_ENV = "IPET_LOCAL_API_TOKEN"
LOCAL_API_TOKEN_HEADER = "X-Ipet-Local-Token"
LOCAL_API_TOKEN = str(os.environ.get(LOCAL_API_TOKEN_ENV) or secrets.token_urlsafe(32))
os.environ.setdefault(LOCAL_API_TOKEN_ENV, LOCAL_API_TOKEN)

DEFAULT_BACKEND_URL = "http://127.0.0.1:8008"


@dataclass(frozen=True)
class QtScreenVisionDependencies:
    q_byte_array: Any
    q_buffer: Any
    q_io_device: Any
    qt: Any
    q_timer: Any
    q_gui_application: Any
    q_image: Any
    q_painter: Any
    q_pixmap: Any
    q_color: Any


_QT_DEPS: QtScreenVisionDependencies | None = None


def _is_macos(platform_name: str | None = None) -> bool:
    return _desktop_runtime.is_macos(platform_name, sys_platform=sys.platform)


def load_qt_screen_vision_dependencies() -> QtScreenVisionDependencies:
    global _QT_DEPS
    if _QT_DEPS is not None:
        return _QT_DEPS

    _desktop_runtime.apply_qt_runtime_env(environ=os.environ, sys_platform=sys.platform)
    if _desktop_runtime.prefer_pyqt_bindings(sys_platform=sys.platform):
        try:
            from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt, QTimer
            from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap
        except ImportError:
            from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt, QTimer
            from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap
    else:
        try:
            from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt, QTimer
            from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap
        except ImportError:
            from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt, QTimer
            from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap

    _QT_DEPS = QtScreenVisionDependencies(
        q_byte_array=QByteArray,
        q_buffer=QBuffer,
        q_io_device=QIODevice,
        qt=Qt,
        q_timer=QTimer,
        q_gui_application=QGuiApplication,
        q_image=QImage,
        q_painter=QPainter,
        q_pixmap=QPixmap,
        q_color=QColor,
    )
    return _QT_DEPS


def _screen_capture_qt_deps() -> _screen_capture.QtScreenCaptureDependencies:
    deps = load_qt_screen_vision_dependencies()
    return _screen_capture.QtScreenCaptureDependencies(
        q_byte_array=deps.q_byte_array,
        q_buffer=deps.q_buffer,
        q_io_device=deps.q_io_device,
        qt=deps.qt,
        q_gui_application=deps.q_gui_application,
        q_image=deps.q_image,
        q_painter=deps.q_painter,
        q_pixmap=deps.q_pixmap,
        q_color=deps.q_color,
    )


def capture_screen_frame_payload(
    screen,
    config: dict,
    *,
    platform_name: str | None = None,
    macos_capture=None,
    qt_capture=None,
    allow_qt_fallback: bool = True,
    display_layout: list[dict] | None = None,
) -> dict:
    return _screen_capture.capture_screen_frame_payload(
        screen,
        config,
        platform_name=platform_name,
        macos_capture=macos_capture,
        qt_capture=qt_capture,
        allow_qt_fallback=allow_qt_fallback,
        display_layout=display_layout,
        qt_deps=_screen_capture_qt_deps(),
        is_macos_func=_is_macos,
    )


def collect_screen_observations(config: dict | None = None, *, platform_name: str | None = None, runner=None) -> dict:
    return _screen_capture.collect_screen_observations(
        config,
        platform_name=platform_name,
        runner=runner or subprocess.run,
        vision_config_normalizer=normalize_vision_config,
        is_macos_func=_is_macos,
    )


class ScreenVisionController:
    def __init__(
        self,
        parent=None,
        *,
        timer_factory=None,
        screen_provider=None,
        frame_encoder=None,
        observation_provider=None,
        task_runner=None,
        post_frame=None,
        background_capture: bool | None = None,
    ) -> None:
        if timer_factory is None or screen_provider is None:
            qt_deps = load_qt_screen_vision_dependencies()
            timer_factory = timer_factory or qt_deps.q_timer
            screen_provider = screen_provider or qt_deps.q_gui_application.primaryScreen
        self._timer = timer_factory(parent)
        self._timer.timeout.connect(self.capture_once)
        self._screen_provider = screen_provider
        self._frame_encoder = frame_encoder or capture_screen_frame_payload
        self._observation_provider = observation_provider or collect_screen_observations
        self._task_runner = task_runner or self._run_background_task
        self._post_frame = post_frame or self._post_frame_request
        self._background_capture = _is_macos() if background_capture is None else bool(background_capture)
        self._config = normalize_vision_config({})
        self._backend_url = DEFAULT_BACKEND_URL
        self.last_error = ""
        self._capture_lock = threading.Lock()
        self._capture_in_flight = False

    def apply_config(self, config: dict) -> None:
        source = config if isinstance(config, dict) else {}
        self._config = normalize_vision_config(source.get("vision", {}))
        chat_cfg = source.get("chat", {}) if isinstance(source.get("chat", {}), dict) else {}
        self._backend_url = str(chat_cfg.get("backend_url") or DEFAULT_BACKEND_URL).strip() or DEFAULT_BACKEND_URL
        if not self._config["enabled"]:
            self.stop()
            return
        self._timer.start(int(self._config["capture_interval_ms"]))

    def stop(self) -> None:
        self._timer.stop()

    def capture_once(self) -> None:
        if not self._config["enabled"]:
            return
        if not self._begin_capture_task():
            return
        try:
            screen = self._screen_provider()
            if screen is None:
                raise RuntimeError("primary screen is unavailable")
            url = f"{self._backend_url.rstrip('/')}/api/vision/frame"
            config = dict(self._config)
            if self._background_capture:
                display_layout = self._capture_display_layout()
                self._task_runner(lambda: self._complete_capture_encode_and_post(url, screen, config, display_layout))
                return
            frame_payload = self._frame_encoder(screen, config)
            payload = self._payload_from_frame_payload(frame_payload)
            self._task_runner(lambda: self._complete_capture_post(url, payload, config))
        except Exception as exc:
            self._finish_capture_task()
            self.last_error = str(exc)
            print(f"自动视觉捕获失败: {exc}")

    def _begin_capture_task(self) -> bool:
        with self._capture_lock:
            if self._capture_in_flight:
                return False
            self._capture_in_flight = True
            return True

    def _finish_capture_task(self) -> None:
        with self._capture_lock:
            self._capture_in_flight = False

    @staticmethod
    def _payload_from_frame_payload(frame_payload) -> dict:
        if isinstance(frame_payload, dict):
            return dict(frame_payload)
        mime_type, data_url, image_width, image_height = _screen_capture._encoded_frame_parts(frame_payload)
        payload = {"mime_type": mime_type, "data_url": data_url}
        if image_width > 0 and image_height > 0:
            payload["image_width"] = image_width
            payload["image_height"] = image_height
        return payload

    @staticmethod
    def _capture_display_layout() -> list[dict]:
        try:
            qt_deps = load_qt_screen_vision_dependencies()
            return _screen_capture._screen_layout(list(qt_deps.q_gui_application.screens() or []))
        except Exception:
            return []

    def _complete_capture_encode_and_post(self, url: str, screen, config: dict, display_layout: list[dict] | None = None) -> None:
        try:
            if self._frame_encoder is capture_screen_frame_payload and self._background_capture and _is_macos():
                frame_payload = self._frame_encoder(
                    screen,
                    config,
                    allow_qt_fallback=False,
                    display_layout=display_layout,
                )
            else:
                frame_payload = self._frame_encoder(screen, config)
            payload = self._payload_from_frame_payload(frame_payload)
            self._complete_capture_post(url, payload, config)
        except Exception as exc:
            self.last_error = str(exc)
            print(f"自动视觉捕获失败: {exc}")
            self._finish_capture_task()

    def _complete_capture_post(self, url: str, payload: dict, config: dict) -> None:
        try:
            try:
                observation_payload = self._observation_provider(config) if self._observation_provider else {}
            except Exception as exc:
                observation_payload = {"unknowns": [f"本机屏幕元数据观察失败：{exc}"]}
            if isinstance(observation_payload, dict):
                for key in (
                    "change_summary",
                    "important_objects",
                    "visible_text",
                    "confidence",
                    "unknowns",
                    "desktop_context",
                    "foreground_app",
                    "window_title",
                ):
                    if observation_payload.get(key):
                        payload[key] = observation_payload[key]
            self._post_frame(url, payload, self._post_timeout_sec(config))
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            print(f"自动视觉上传失败: {exc}")
        finally:
            self._finish_capture_task()

    @staticmethod
    def _run_background_task(task) -> None:
        thread = threading.Thread(target=task, name="IpetVisionCapturePost", daemon=True)
        thread.start()

    @staticmethod
    def _post_timeout_sec(config: dict) -> float:
        analyzer = config.get("analyzer") if isinstance(config.get("analyzer"), dict) else {}
        if analyzer.get("enabled") and analyzer.get("provider") not in ("", "none", None):
            try:
                return max(2.0, float(analyzer.get("timeout_sec", 15.0)) + 1.0)
            except Exception:
                return 16.0
        return 2.0

    @staticmethod
    def _post_frame_request(url: str, payload: dict, timeout: float):
        return requests.post(
            url,
            json=payload,
            headers={LOCAL_API_TOKEN_HEADER: LOCAL_API_TOKEN},
            timeout=timeout,
        )


__all__ = ["ScreenVisionController"]
