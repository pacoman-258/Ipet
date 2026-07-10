from __future__ import annotations

from collections.abc import Callable
from typing import Any


class SettingsWindowController:
    def __init__(
        self,
        owner: Any,
        *,
        default_backend_url: str = "http://127.0.0.1:8008",
        web_engine_view_factory: Callable[[], Any] | None = None,
        q_url_factory: Callable[[str], Any] | None = None,
        q_color_factory: Callable[..., Any] | None = None,
        widget_attribute: Any = None,
        web_attribute: Any = None,
        webgl_enabled: Callable[[], bool] | bool | None = None,
        window_title: str = "Ipet 设置",
        window_size: tuple[int, int] = (1180, 760),
        background_rgba: tuple[int, int, int, int] = (18, 18, 18, 255),
    ) -> None:
        self.owner = owner
        self.default_backend_url = default_backend_url
        self.web_engine_view_factory = web_engine_view_factory
        self.q_url_factory = q_url_factory
        self.q_color_factory = q_color_factory
        self.widget_attribute = widget_attribute
        self.web_attribute = web_attribute
        self.webgl_enabled = webgl_enabled
        self.window_title = window_title
        self.window_size = window_size
        self.background_rgba = background_rgba

    def settings_page_url(self) -> str:
        config = getattr(self.owner, "config", {})
        chat_cfg = config.get("chat", {}) if isinstance(config, dict) else {}
        if not isinstance(chat_cfg, dict):
            chat_cfg = {}
        backend_url = str(chat_cfg.get("backend_url", self.default_backend_url)).strip() or self.default_backend_url
        return f"{backend_url.rstrip('/')}/settings"

    def clear_settings_window(self, window: Any = None) -> None:
        if window is None or getattr(self.owner, "settings_window", None) is window:
            self.owner.settings_window = None
            self.owner._settings_window_url = ""

    def create_settings_window(self, url: str) -> Any:
        if self.web_engine_view_factory is None:
            raise RuntimeError("settings window creation requires a web_engine_view_factory")
        window = self.web_engine_view_factory()
        window.setWindowTitle(self.window_title)
        window.resize(*self.window_size)
        self._set_delete_on_close(window)
        self._set_background_color(window)
        self._configure_web_settings(window)
        self._connect_destroyed(window)
        return window

    def settings_window_is_usable(self, window: Any) -> bool:
        if window is None:
            return False
        try:
            window.isVisible()
        except Exception:
            return False
        return True

    def navigate_settings_window(self, window: Any, url: str) -> None:
        if getattr(self.owner, "_settings_window_url", "") == url:
            return
        target_url = self.q_url_factory(url) if self.q_url_factory is not None else url
        window.setUrl(target_url)
        self.owner._settings_window_url = url

    def show_or_focus_settings_window(self, url: str) -> None:
        window = getattr(self.owner, "settings_window", None)
        if not self.settings_window_is_usable(window):
            window = self.owner._create_settings_window(url)
            self.owner.settings_window = window
            self.owner._settings_window_url = ""

        self.navigate_settings_window(window, url)
        if not window.isVisible():
            window.show()
        window.raise_()
        window.activateWindow()

    def _set_delete_on_close(self, window: Any) -> None:
        attribute = getattr(self.widget_attribute, "WA_DeleteOnClose", None)
        if attribute is not None:
            window.setAttribute(attribute, True)

    def _set_background_color(self, window: Any) -> None:
        if self.q_color_factory is None:
            return
        color = self.q_color_factory(*self.background_rgba)
        window.page().setBackgroundColor(color)

    def _configure_web_settings(self, window: Any) -> None:
        settings = window.settings()
        self._set_web_attribute(settings, "LocalContentCanAccessFileUrls", True)
        self._set_web_attribute(settings, "LocalContentCanAccessRemoteUrls", True)
        self._set_web_attribute(settings, "WebGLEnabled", self._webgl_enabled())
        self._set_web_attribute(settings, "Accelerated2dCanvasEnabled", False)

    def _set_web_attribute(self, settings: Any, name: str, value: Any) -> None:
        attribute = getattr(self.web_attribute, name, None)
        if attribute is not None:
            settings.setAttribute(attribute, value)

    def _webgl_enabled(self) -> bool:
        if callable(self.webgl_enabled):
            return bool(self.webgl_enabled())
        if self.webgl_enabled is None:
            return True
        return bool(self.webgl_enabled)

    def _connect_destroyed(self, window: Any) -> None:
        try:
            window.destroyed.connect(
                lambda *_args, settings_window=window: self.owner._clear_settings_window(settings_window)
            )
        except Exception:
            pass
