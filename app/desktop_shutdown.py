from __future__ import annotations

from typing import Any


class DesktopShutdownController:
    def __init__(self, owner: Any, *, q_application: Any) -> None:
        self.owner = owner
        self.q_application = q_application

    def shutdown_desktop(self) -> None:
        owner = self.owner
        if owner._shutdown_in_progress:
            return
        owner._shutdown_in_progress = True

        owner.vision_controller.stop()
        self._stop_config_poll_timer()
        self._remove_application_event_filter()
        self._dispose_control_panel()
        self._disconnect_browser_loaded()
        self._stop_browser()
        self._remove_browser_event_filter()
        self._clear_browser_web_channel()
        self._dispose_browser()
        self._dispose_channel()

        owner.stop_asr_service()
        owner.stop_qwen_tts_service()
        owner.stop_backend_service()

    def _stop_config_poll_timer(self) -> None:
        owner = self.owner
        try:
            if hasattr(owner, "_config_poll_timer") and owner._config_poll_timer.isActive():
                owner._config_poll_timer.stop()
        except Exception:
            pass

    def _remove_application_event_filter(self) -> None:
        owner = self.owner
        try:
            app = self.q_application.instance()
            if app is not None and owner._python_event_filters_installed:
                app.removeEventFilter(owner)
        except Exception:
            pass

    def _dispose_control_panel(self) -> None:
        owner = self.owner
        try:
            if owner.control_panel is not None:
                owner.control_panel.close()
                owner.control_panel.deleteLater()
                owner.control_panel = None
        except Exception:
            pass

    def _disconnect_browser_loaded(self) -> None:
        owner = self.owner
        try:
            owner.browser.loadFinished.disconnect(owner.on_web_loaded)
        except Exception:
            pass

    def _stop_browser(self) -> None:
        try:
            self.owner.browser.stop()
        except Exception:
            pass

    def _remove_browser_event_filter(self) -> None:
        owner = self.owner
        try:
            if owner._python_event_filters_installed:
                owner.browser.removeEventFilter(owner)
        except Exception:
            pass

    def _clear_browser_web_channel(self) -> None:
        try:
            page = self.owner.browser.page()
            if page is not None:
                page.setWebChannel(None)
        except Exception:
            pass

    def _dispose_browser(self) -> None:
        try:
            self.owner.browser.close()
            self.owner.browser.deleteLater()
        except Exception:
            pass

    def _dispose_channel(self) -> None:
        try:
            self.owner.channel.deleteLater()
        except Exception:
            pass
