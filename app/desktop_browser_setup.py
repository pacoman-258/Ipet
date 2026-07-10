from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DesktopBrowserSetupDependencies:
    web_engine_view_factory: Callable[[Any], Any]
    web_channel_factory: Callable[[Any], Any]
    q_url_factory: Any
    q_color_factory: Callable[..., Any]
    q_application: Any
    qt: Any
    web_attribute: Any
    background_color: Callable[[], tuple[int, int, int, int]] | tuple[int, int, int, int]
    webgl_enabled: Callable[[], bool] | bool
    install_python_event_filters: Callable[[], bool] | bool


@dataclass(frozen=True)
class DesktopBrowserSetupResult:
    browser: Any
    channel: Any
    event_filter_installed: bool


def setup_desktop_browser(
    owner: Any,
    *,
    bridge: Any,
    root_dir: Path,
    dependencies: DesktopBrowserSetupDependencies,
) -> DesktopBrowserSetupResult:
    browser = dependencies.web_engine_view_factory(owner)
    page = browser.page()

    browser.setMouseTracking(True)
    page.setBackgroundColor(dependencies.q_color_factory(*_background_color(dependencies)))

    settings = browser.settings()
    _set_web_attribute(settings, dependencies.web_attribute, "LocalContentCanAccessFileUrls", True)
    _set_web_attribute(settings, dependencies.web_attribute, "LocalContentCanAccessRemoteUrls", True)
    _set_web_attribute(settings, dependencies.web_attribute, "WebGLEnabled", _bool_value(dependencies.webgl_enabled))
    _set_web_attribute(settings, dependencies.web_attribute, "Accelerated2dCanvasEnabled", False)

    browser.setContextMenuPolicy(dependencies.qt.ContextMenuPolicy.CustomContextMenu)
    browser.customContextMenuRequested.connect(owner.show_context_menu)
    event_filter_installed = _install_event_filters(owner, browser, dependencies)

    if hasattr(page, "featurePermissionRequested"):
        page.featurePermissionRequested.connect(owner.on_feature_permission_requested)

    channel = dependencies.web_channel_factory(page)
    channel.registerObject("qtBridge", bridge)
    page.setWebChannel(channel)

    owner.setCentralWidget(browser)
    browser.loadFinished.connect(owner.on_web_loaded)
    browser.setUrl(dependencies.q_url_factory.fromLocalFile(str((Path(root_dir) / "index.html").resolve())))

    return DesktopBrowserSetupResult(
        browser=browser,
        channel=channel,
        event_filter_installed=event_filter_installed,
    )


def _set_web_attribute(settings: Any, web_attribute: Any, name: str, value: Any) -> None:
    attribute = getattr(web_attribute, name, None)
    if attribute is not None:
        settings.setAttribute(attribute, value)


def _install_event_filters(
    owner: Any,
    browser: Any,
    dependencies: DesktopBrowserSetupDependencies,
) -> bool:
    if not _bool_value(dependencies.install_python_event_filters):
        return False

    browser.installEventFilter(owner)
    app = dependencies.q_application.instance() if dependencies.q_application is not None else None
    if app is not None:
        app.installEventFilter(owner)
    return True


def _background_color(dependencies: DesktopBrowserSetupDependencies) -> tuple[int, int, int, int]:
    background_color = dependencies.background_color
    if callable(background_color):
        return background_color()
    return background_color


def _bool_value(value: Callable[[], bool] | bool) -> bool:
    if callable(value):
        return bool(value())
    return bool(value)
