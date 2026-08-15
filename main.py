import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
from app import configuration as _configuration
from app.default_config import create_default_config
from app import click_preview as _click_preview
from app import desktop_action_wiring as _desktop_action_wiring
from app import desktop_active_vision_wiring as _active_vision_wiring
from app import desktop_screen_capture_wiring as _screen_capture_wiring
from app import window_defaults as _window_defaults
from app.body_bridge import BodyBridge
from app.context_menu import DesktopContextMenuController
from app.desktop_browser_setup import DesktopBrowserSetupDependencies, setup_desktop_browser
from app.desktop_config_actions import DesktopConfigActions
from app.desktop_command_wiring import (
    DesktopCommandRouterWiringDependencies,
    create_desktop_command_router,
)
from app.desktop_pet_visibility import DesktopPetVisibilityController
from app.desktop_bridge_wiring import create_connected_pet_bridge
from app.desktop_shutdown import DesktopShutdownController
from app.desktop_web_permissions import DesktopWebPermissionsController
from app import desktop_runtime as _desktop_runtime
from app.desktop_service_wiring import (
    DesktopServiceLifecycleWiringDependencies,
    create_desktop_service_lifecycle,
)
from app.pet_bridge import create_pet_bridge_class
from app.native_approval_notifications import NativeApprovalNotificationController
from app.qt_bindings import load_qt_bindings
from app.settings_window import SettingsWindowController
from app.window_regions import WindowRegionController
from backend.environment import DEFAULT_ENVIRONMENT_CONFIG, normalize_environment_config
from backend.game import normalize_game_config
from backend.vision import DEFAULT_VISION_CONFIG, normalize_vision_config
from body import live2d_assets as _live2d_assets
from body import macos_accessibility as _macos_accessibility
from body.environment_controller import EnvironmentController
from body.screen_vision_controller import ScreenVisionController


LOCAL_API_TOKEN_ENV = "IPET_LOCAL_API_TOKEN"
LOCAL_API_TOKEN_HEADER = "X-Ipet-Local-Token"
LOCAL_API_TOKEN = str(os.environ.get(LOCAL_API_TOKEN_ENV) or secrets.token_urlsafe(32))
os.environ.setdefault(LOCAL_API_TOKEN_ENV, LOCAL_API_TOKEN)


def _platform_name(platform_name: str | None = None) -> str:
    return _desktop_runtime.platform_name(platform_name, sys_platform=sys.platform)


def _is_macos(platform_name: str | None = None) -> bool:
    return _desktop_runtime.is_macos(platform_name, sys_platform=sys.platform)


def _default_asr_enabled(platform_name: str | None = None) -> bool:
    return _desktop_runtime.default_asr_enabled(platform_name, sys_platform=sys.platform)


def _default_asr_config(platform_name: str | None = None) -> dict[str, object]:
    return _desktop_runtime.default_asr_config(platform_name, sys_platform=sys.platform)


def _qt_runtime_env_defaults(platform_name: str | None = None) -> dict[str, str]:
    return _desktop_runtime.qt_runtime_env_defaults(platform_name, sys_platform=sys.platform)


def _is_macos_native_stderr_noise(line: str, platform_name: str | None = None) -> bool:
    return _desktop_runtime.is_macos_native_stderr_noise(line, platform_name, sys_platform=sys.platform)


def _install_macos_native_stderr_filter(platform_name: str | None = None) -> bool:
    return _desktop_runtime.install_macos_native_stderr_filter(
        platform_name,
        environ=os.environ,
        os_module=os,
        threading_module=threading,
        sys_platform=sys.platform,
    )


def _apply_qt_runtime_env(env: dict[str, str] | None = None, platform_name: str | None = None) -> dict[str, str]:
    return _desktop_runtime.apply_qt_runtime_env(
        env,
        platform_name,
        environ=os.environ,
        sys_platform=sys.platform,
    )


def _desktop_pet_window_flags(platform_name: str | None = None):
    return _window_defaults.desktop_pet_window_flags(
        Qt.WindowType,
        is_macos=_is_macos(platform_name),
    )


def _should_use_translucent_window(force_opaque: bool | None = None, platform_name: str | None = None) -> bool:
    effective_force_opaque = FORCE_OPAQUE_WINDOW if force_opaque is None else bool(force_opaque)
    return _window_defaults.should_use_translucent_window(
        force_opaque=effective_force_opaque,
        is_macos=_is_macos(platform_name),
    )


def _desktop_pet_background_color(platform_name: str | None = None) -> tuple[int, int, int, int]:
    return _window_defaults.desktop_pet_background_color(
        translucent=_should_use_translucent_window(platform_name=platform_name),
    )


def _should_enable_webgl(platform_name: str | None = None) -> bool:
    return _desktop_runtime.should_enable_webgl(platform_name, sys_platform=sys.platform)


def _should_force_software_opengl(platform_name: str | None = None) -> bool:
    return _desktop_runtime.should_force_software_opengl(platform_name, sys_platform=sys.platform)


def _should_install_python_event_filters(platform_name: str | None = None) -> bool:
    return _desktop_runtime.should_install_python_event_filters(platform_name, sys_platform=sys.platform)


def _prefer_pyqt_bindings(platform_name: str | None = None) -> bool:
    return _desktop_runtime.prefer_pyqt_bindings(platform_name, sys_platform=sys.platform)


_apply_qt_runtime_env()

_QT_BINDINGS = load_qt_bindings(_prefer_pyqt_bindings())
QByteArray = _QT_BINDINGS.QByteArray
QBuffer = _QT_BINDINGS.QBuffer
QIODevice = _QT_BINDINGS.QIODevice
QObject = _QT_BINDINGS.QObject
QPoint = _QT_BINDINGS.QPoint
Qt = _QT_BINDINGS.Qt
QEvent = _QT_BINDINGS.QEvent
QTimer = _QT_BINDINGS.QTimer
QUrl = _QT_BINDINGS.QUrl
Signal = _QT_BINDINGS.Signal
Slot = _QT_BINDINGS.Slot
QAction = _QT_BINDINGS.QAction
QColor = _QT_BINDINGS.QColor
QIcon = _QT_BINDINGS.QIcon
QGuiApplication = _QT_BINDINGS.QGuiApplication
QImage = _QT_BINDINGS.QImage
QPainter = _QT_BINDINGS.QPainter
QPixmap = _QT_BINDINGS.QPixmap
QRegion = _QT_BINDINGS.QRegion
QWebChannel = _QT_BINDINGS.QWebChannel
QWebEnginePage = _QT_BINDINGS.QWebEnginePage
QWebEngineSettings = _QT_BINDINGS.QWebEngineSettings
QWebEngineView = _QT_BINDINGS.QWebEngineView
QApplication = _QT_BINDINGS.QApplication
QFileDialog = _QT_BINDINGS.QFileDialog
QMainWindow = _QT_BINDINGS.QMainWindow
QMenu = _QT_BINDINGS.QMenu
QWidget = _QT_BINDINGS.QWidget

from app.control_panel import ControlPanel
from app.window_interaction import WindowInteractionController

ROOT_DIR = Path(__file__).resolve().parent
APP_DISPLAY_NAME = "Ipet"
APP_ICON_PATH = ROOT_DIR / "assets" / "ipet-app-icon.png"
CONFIG_PATH = ROOT_DIR / "pet_config.json"
SERVICE_LOG_DIR = ROOT_DIR / ".service-logs"
FORCE_OPAQUE_WINDOW = os.environ.get("PET_FORCE_OPAQUE", "0") == "1"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8008"
DEFAULT_ASR_API_BASE_URL = "http://127.0.0.1:8012"
DEFAULT_CHAT_MODEL = "gpt-5.4"
DEFAULT_BRAIN_MODEL = DEFAULT_CHAT_MODEL
DEFAULT_ASR_CONFIG = _default_asr_config()
DESKTOP_COMMAND_PATH = ROOT_DIR / ".pet_desktop_command.json"
DESKTOP_COMMAND_RESPONSE_PATH = ROOT_DIR / ".pet_desktop_command.response.json"
DESKTOP_HOST_HEARTBEAT_PATH = ROOT_DIR / ".pet_desktop_host.heartbeat.json"
AUTOGEN_MODEL_SUFFIX = ".autogen.model3.json"
BACKEND_VENV_DIRNAME = ".venv-py312"


def _build_settings_window_controller(owner) -> SettingsWindowController:
    return SettingsWindowController(
        owner,
        default_backend_url=DEFAULT_BACKEND_URL,
        web_engine_view_factory=QWebEngineView,
        q_url_factory=QUrl,
        q_color_factory=QColor,
        widget_attribute=Qt.WidgetAttribute,
        web_attribute=QWebEngineSettings.WebAttribute,
        webgl_enabled=_should_enable_webgl,
    )


def _application_icon():
    return QIcon(str(APP_ICON_PATH))


def apply_application_identity(app) -> None:
    """Keep Qt's process, menu, Dock, and window identity aligned."""
    app.setApplicationName(APP_DISPLAY_NAME)
    set_display_name = getattr(app, "setApplicationDisplayName", None)
    if callable(set_display_name):
        set_display_name(APP_DISPLAY_NAME)
    app.setWindowIcon(_application_icon())


def configure_application_lifecycle(app) -> None:
    """Keep auxiliary windows from deciding the desktop host lifetime."""
    app.setQuitOnLastWindowClosed(False)


class DesktopApplication(QApplication):
    """Relay macOS Dock/application reactivation to a hidden desktop pet."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.desktop_reopen_callback = None

    def event(self, event):
        activated = event.type() == QEvent.Type.ApplicationActivate
        result = super().event(event)
        if activated and callable(self.desktop_reopen_callback):
            self.desktop_reopen_callback()
        return result


def _build_desktop_browser_setup_dependencies() -> DesktopBrowserSetupDependencies:
    return DesktopBrowserSetupDependencies(
        web_engine_view_factory=QWebEngineView,
        web_channel_factory=QWebChannel,
        q_url_factory=QUrl,
        q_color_factory=QColor,
        q_application=QApplication,
        qt=Qt,
        web_attribute=QWebEngineSettings.WebAttribute,
        background_color=_desktop_pet_background_color,
        webgl_enabled=_should_enable_webgl,
        install_python_event_filters=_should_install_python_event_filters,
    )


def _build_window_interaction_controller(owner) -> WindowInteractionController:
    return WindowInteractionController(
        owner,
        qt=Qt,
        q_event=QEvent,
        q_application=QApplication,
        q_point=QPoint,
    )


def _build_window_region_controller(owner) -> WindowRegionController:
    return WindowRegionController(
        owner,
        q_region_factory=QRegion,
        mouse_transparent_attribute=Qt.WidgetAttribute.WA_TransparentForMouseEvents,
    )


def _build_desktop_shutdown_controller(owner) -> DesktopShutdownController:
    return DesktopShutdownController(
        owner,
        q_application=QApplication,
    )


def _build_web_permissions_controller(owner) -> DesktopWebPermissionsController:
    return DesktopWebPermissionsController(
        owner,
        q_web_engine_page=QWebEnginePage,
    )


def _build_context_menu_controller(owner) -> DesktopContextMenuController:
    return DesktopContextMenuController(owner)


def _settings_window_controller_for(owner) -> SettingsWindowController:
    controller = getattr(owner, "_settings_window_controller", None)
    if controller is None:
        controller = _build_settings_window_controller(owner)
        owner._settings_window_controller = controller
    return controller


def _context_menu_controller_for(owner) -> DesktopContextMenuController:
    controller = getattr(owner, "_context_menu_controller", None)
    if controller is None:
        controller = _build_context_menu_controller(owner)
        owner._context_menu_controller = controller
    return controller


def _desktop_shutdown_controller_for(owner) -> DesktopShutdownController:
    controller = getattr(owner, "_desktop_shutdown_controller", None)
    if controller is None:
        controller = _build_desktop_shutdown_controller(owner)
        owner._desktop_shutdown_controller = controller
    return controller


def _web_permissions_controller_for(owner) -> DesktopWebPermissionsController:
    controller = getattr(owner, "_web_permissions_controller", None)
    if controller is None:
        controller = _build_web_permissions_controller(owner)
        owner._web_permissions_controller = controller
    return controller


def _build_desktop_service_lifecycle_dependencies() -> DesktopServiceLifecycleWiringDependencies:
    return DesktopServiceLifecycleWiringDependencies(
        root_dir=ROOT_DIR,
        default_backend_url=DEFAULT_BACKEND_URL,
        default_asr_api_base_url=DEFAULT_ASR_API_BASE_URL,
        local_api_token_env=LOCAL_API_TOKEN_ENV,
        local_api_token=LOCAL_API_TOKEN,
        os_module=os,
        subprocess_module=subprocess,
        requests_module=requests,
        threading_module=threading,
        time_module=time,
        sys_module=sys,
        print_func=print,
        is_backend_healthy=is_backend_healthy,
        is_backend_live=is_backend_live,
        is_asr_healthy=is_asr_healthy,
        is_local_service_url=is_local_service_url,
        pick_backend_launch_url=pick_backend_launch_url,
        parse_service_host_port=parse_service_host_port,
        resolve_backend_python=resolve_backend_python,
        resolve_asr_python=resolve_asr_python,
        service_log_path=_service_log_path,
        truncate_service_log=_truncate_service_log,
        tail_service_log=_tail_service_log,
    )


def _common_exec_search_dirs(root_dir: Path | None = None) -> list[str]:
    return _desktop_runtime.common_exec_search_dirs(
        root_dir=root_dir or ROOT_DIR,
        backend_venv_dirname=BACKEND_VENV_DIRNAME,
        path_home=Path.home,
        os_name=os.name,
    )


def _augment_process_path(env: dict[str, str] | None = None, *, root_dir: Path | None = None) -> str:
    return _desktop_runtime.augment_process_path(
        env,
        root_dir=root_dir or ROOT_DIR,
        backend_venv_dirname=BACKEND_VENV_DIRNAME,
        environ=os.environ,
        path_home=Path.home,
        os_name=os.name,
    )


def _preferred_python_commands(root_dir: Path | None = None) -> list[str]:
    return _desktop_runtime.preferred_python_commands(root_dir=root_dir or ROOT_DIR)


_augment_process_path()


def _python_entry_for_venv(venv_dir: Path) -> Path:
    return _desktop_runtime.python_entry_for_venv(venv_dir, os_name=os.name)


def _service_log_path(name: str) -> Path:
    return _desktop_runtime.service_log_path(
        name=name,
        root_dir=ROOT_DIR,
        service_log_dir=SERVICE_LOG_DIR,
    )


def _truncate_service_log(path: Path) -> Path:
    return _desktop_runtime.truncate_service_log(path)


def _tail_service_log(path: Path, *, max_lines: int = 20) -> str:
    return _desktop_runtime.tail_service_log(path, max_lines=max_lines)


def _python_command_exists(command: str) -> bool:
    return _desktop_runtime.python_command_exists(command, shutil_module=shutil)


def _python_supports_backend(command: str) -> bool:
    return _desktop_runtime.python_supports_backend(
        command,
        root_dir=ROOT_DIR,
        command_exists=_python_command_exists,
        subprocess_module=subprocess,
    )


def resolve_backend_python() -> str:
    return _desktop_runtime.resolve_backend_python(
        root_dir=ROOT_DIR,
        backend_venv_dirname=BACKEND_VENV_DIRNAME,
        environ=os.environ,
        sys_executable=sys.executable,
        python_entry_for_venv_func=_python_entry_for_venv,
        preferred_python_commands_func=_preferred_python_commands,
        python_supports_backend_func=_python_supports_backend,
        python_command_exists_func=_python_command_exists,
    )


def resolve_asr_python() -> str:
    return _desktop_runtime.resolve_asr_python(
        root_dir=ROOT_DIR,
        backend_venv_dirname=BACKEND_VENV_DIRNAME,
        environ=os.environ,
        sys_executable=sys.executable,
        python_entry_for_venv_func=_python_entry_for_venv,
        preferred_python_commands_func=_preferred_python_commands,
        python_command_exists_func=_python_command_exists,
    )


def _find_default_model() -> str:
    return _live2d_assets.find_default_model(ROOT_DIR)


DEFAULT_CONFIG = create_default_config(
    default_vision_config=DEFAULT_VISION_CONFIG,
    default_asr_config=DEFAULT_ASR_CONFIG,
    default_backend_url=DEFAULT_BACKEND_URL,
    default_chat_model=DEFAULT_CHAT_MODEL,
    default_brain_model=DEFAULT_BRAIN_MODEL,
    default_model_path=_find_default_model(),
    default_environment_config=DEFAULT_ENVIRONMENT_CONFIG,
)

CUSTOM_HTTP_TTS_PRESETS = _configuration.CUSTOM_HTTP_TTS_PRESETS


def build_custom_http_tts_preset(key: str) -> str:
    return _configuration.build_custom_http_tts_preset(key)


def deep_merge(base: dict, override: dict) -> dict:
    return _configuration.deep_merge(base, override)


def _keep_neo_config_shape(config: dict) -> None:
    _configuration.keep_neo_config_shape(config, default_config=DEFAULT_CONFIG)


def _normalize_neo_chat_config(config: dict) -> None:
    _configuration.normalize_neo_chat_config(
        config,
        default_config=DEFAULT_CONFIG,
        default_brain_model=DEFAULT_BRAIN_MODEL,
    )


def load_config() -> dict:
    return _configuration.load_config(
        config_path=CONFIG_PATH,
        default_config=DEFAULT_CONFIG,
        default_brain_model=DEFAULT_BRAIN_MODEL,
        normalize_model_path_func=normalize_model_path,
        normalize_vision_config_func=normalize_vision_config,
        normalize_environment_config_func=normalize_environment_config,
        normalize_game_config_func=normalize_game_config,
    )


def extract_pet_display_name(system_prompt: str) -> str:
    return _configuration.extract_pet_display_name(system_prompt)


def is_service_healthy(base_url: str, *, require_asr: bool = False) -> bool:
    return _desktop_runtime.is_service_healthy(
        base_url,
        require_asr=require_asr,
        requests_module=requests,
    )


def is_backend_live(backend_url: str) -> bool:
    return _desktop_runtime.is_backend_live(
        backend_url,
        service_healthy=is_service_healthy,
    )


def is_backend_healthy(backend_url: str) -> bool:
    return _desktop_runtime.is_backend_healthy(
        backend_url,
        backend_live=is_backend_live,
        supports_required_routes=backend_supports_required_routes,
    )


def is_asr_healthy(asr_url: str) -> bool:
    return _desktop_runtime.is_asr_healthy(
        asr_url,
        service_healthy=is_service_healthy,
    )


def parse_service_host_port(base_url: str, *, default_port: int) -> tuple[str, int]:
    return _desktop_runtime.parse_service_host_port(base_url, default_port=default_port)


def backend_supports_required_routes(base_url: str) -> bool:
    return _desktop_runtime.backend_supports_required_routes(
        base_url,
        service_healthy=is_service_healthy,
    )


def is_local_service_url(base_url: str) -> bool:
    return _desktop_runtime.is_local_service_url(base_url)


def is_service_port_available(host: str, port: int) -> bool:
    return _desktop_runtime.is_service_port_available(host, port, socket_module=socket)


def pick_backend_launch_url(preferred_url: str, *, max_offset: int = 12) -> str:
    return _desktop_runtime.pick_backend_launch_url(
        preferred_url,
        max_offset=max_offset,
        default_backend_url=DEFAULT_BACKEND_URL,
        is_local_url=is_local_service_url,
        parse_host_port=parse_service_host_port,
        is_port_available=is_service_port_available,
    )


def canonicalize_model_source_path(model_path: Path) -> Path:
    return _live2d_assets.canonicalize_model_source_path(
        model_path,
        autogen_model_suffix=AUTOGEN_MODEL_SUFFIX,
    )


def resolve_model_path(path_text: str) -> Path:
    return _live2d_assets.resolve_model_path(path_text, root_dir=ROOT_DIR)


def normalize_model_path(path_text: str) -> str:
    return _live2d_assets.normalize_model_path(path_text, root_dir=ROOT_DIR)


def resolve_background_image_path(path_text: str) -> Path:
    return _live2d_assets.resolve_background_image_path(path_text, root_dir=ROOT_DIR)


def _extract_motion_groups(model_json: dict) -> dict:
    return _live2d_assets.extract_motion_groups(model_json)


def _infer_motion_group(file_name: str) -> str:
    return _live2d_assets.infer_motion_group(file_name)


def _resolve_desktop_directory_seed(start_dir_text: str) -> str:
    return _live2d_assets.resolve_desktop_directory_seed(start_dir_text, root_dir=ROOT_DIR)


def _scan_motion_groups(model_path: Path) -> dict:
    return _live2d_assets.scan_motion_groups(model_path)


def _extract_expression_defs(model_json: dict) -> list[dict]:
    return _live2d_assets.extract_expression_defs(model_json)


def _scan_expression_defs(model_path: Path) -> list[dict]:
    return _live2d_assets.scan_expression_defs(model_path)


def extract_lipsync_meta(model_json: dict) -> dict:
    return _live2d_assets.extract_lipsync_meta(model_json)


def ensure_live2d_model_from_json(model_path: Path, model_json: dict) -> tuple[Path, dict, list[dict]]:
    return _live2d_assets.ensure_live2d_model_from_json(model_path, model_json)


def ensure_live2d_model(model_path: Path) -> tuple[Path, dict, list[dict]]:
    return _live2d_assets.ensure_live2d_model(model_path)


def action_items_from_defs(groups: dict, exprs: list[dict]) -> list[dict]:
    return _live2d_assets.action_items_from_defs(groups, exprs)


def _body_local_file_url(path: Path) -> str:
    return QUrl.fromLocalFile(str(path)).toString()


def _run_body_bridge_javascript(owner, script: str) -> None:
    owner.browser.page().runJavaScript(script)


def _build_body_bridge() -> BodyBridge:
    return BodyBridge(
        resolve_model_path=resolve_model_path,
        default_model_path_text=str(DEFAULT_CONFIG.get("model_path", "")),
        ensure_live2d_model=ensure_live2d_model,
        resolve_background_image_path=resolve_background_image_path,
        local_file_url=_body_local_file_url,
        extract_pet_display_name=extract_pet_display_name,
        normalize_vision_config=normalize_vision_config,
        normalize_environment_config=normalize_environment_config,
        normalize_game_config=normalize_game_config,
        json_dumps=json.dumps,
        run_javascript=_run_body_bridge_javascript,
        extract_lipsync_meta=extract_lipsync_meta,
        action_items_from_defs=action_items_from_defs,
        json_loads=json.loads,
    )


def _body_bridge_for(owner) -> BodyBridge:
    bridge = getattr(owner, "_body_bridge", None)
    if bridge is None:
        bridge = _build_body_bridge()
        owner._body_bridge = bridge
    return bridge


def _build_desktop_config_actions(owner) -> DesktopConfigActions:
    return DesktopConfigActions(
        owner,
        json_module=json,
        config_path=CONFIG_PATH,
        default_config=DEFAULT_CONFIG,
        default_asr_config=DEFAULT_ASR_CONFIG,
        default_backend_url=DEFAULT_BACKEND_URL,
        default_brain_model=DEFAULT_BRAIN_MODEL,
        normalize_model_path=normalize_model_path,
        normalize_vision_config=normalize_vision_config,
        normalize_environment_config=normalize_environment_config,
        normalize_game_config=normalize_game_config,
        keep_neo_config_shape=_keep_neo_config_shape,
        normalize_neo_chat_config=_normalize_neo_chat_config,
        screen_provider=QGuiApplication.primaryScreen,
        load_config=lambda: load_config(),
    )


def _desktop_config_actions_for(owner) -> DesktopConfigActions:
    actions = getattr(owner, "_desktop_config_actions", None)
    if actions is None:
        actions = _build_desktop_config_actions(owner)
        owner._desktop_config_actions = actions
    return actions


def _build_desktop_command_router_dependencies() -> DesktopCommandRouterWiringDependencies:
    return DesktopCommandRouterWiringDependencies(
        root_dir=ROOT_DIR,
        command_path=DESKTOP_COMMAND_PATH,
        default_response_path=DESKTOP_COMMAND_RESPONSE_PATH,
        heartbeat_path=DESKTOP_HOST_HEARTBEAT_PATH,
        normalize_model_path=normalize_model_path,
        resolve_desktop_directory_seed=_resolve_desktop_directory_seed,
        resolve_background_image_path=resolve_background_image_path,
        clean_vision_text=_clean_vision_text,
        qfiledialog=QFileDialog,
        execute_human_ops_click=execute_human_ops_click,
        execute_human_ops_type_text=execute_human_ops_type_text,
        execute_human_ops_launch_app=execute_human_ops_launch_app,
        execute_human_ops_key_press=execute_human_ops_key_press,
        focus_macos_application=focus_macos_application,
        restore_macos_application_focus=restore_macos_application_focus,
        execute_human_ops_native_approval=execute_human_ops_native_approval,
        hide_window_for_desktop_click=_hide_window_for_desktop_click,
        restore_window_after_desktop_click=_restore_window_after_desktop_click,
        capture_active_vision_frame_payload=capture_active_vision_frame_payload,
        accessibility_index_status=_macos_accessibility.macos_accessibility_index_status,
        refresh_accessibility_index=_macos_accessibility.refresh_macos_accessibility_index,
        sleep=time.sleep,
        time_module=time,
        os_module=os,
        print_func=print,
    )


PetBridge = create_pet_bridge_class(QObject, Signal, Slot, print_func=print)


normalize_click_preview_payload = _click_preview.normalize_click_preview_payload


def _click_preview_qt_deps() -> _click_preview.ClickPreviewQtDependencies:
    return _click_preview.ClickPreviewQtDependencies(
        qt=Qt,
        widget_base=QWidget,
        q_painter=QPainter,
        q_color=QColor,
    )


ClickPreviewOverlay = _click_preview.create_click_preview_overlay_class(_click_preview_qt_deps())


globals().update(
    _screen_capture_wiring.create_screen_capture_compat_exports(
        module_globals=globals(),
        module_name=__name__,
    )
)


globals().update(
    _desktop_action_wiring.create_desktop_action_exports(
        qapplication=QApplication,
        module_globals=globals(),
        module_name=__name__,
    )
)


globals().update(_active_vision_wiring.create_active_vision_compat_exports(module_name=__name__))


def _active_vision_bridge():
    return _active_vision_wiring.create_active_vision_bridge()


discover_active_vision_target_candidates = _active_vision_wiring.create_discover_active_vision_target_candidates_entry(
    lambda: _active_vision_bridge(),
    default_desktop_targets_provider=enumerate_active_vision_desktop_targets,
    default_dock_items_provider=enumerate_macos_dock_item_candidates,
    default_running_apps_provider=enumerate_active_vision_running_app_candidates,
    module_name=__name__,
)


capture_active_vision_frame_payload = _active_vision_wiring.create_capture_active_vision_frame_payload_entry(
    lambda: _active_vision_bridge(),
    default_frame_encoder=capture_screen_frame_payload,
    default_observation_provider=collect_screen_observations,
    default_desktop_targets_provider=enumerate_active_vision_desktop_targets,
    default_dock_items_provider=enumerate_macos_dock_item_candidates,
    default_running_apps_provider=enumerate_active_vision_running_app_candidates,
    default_interaction_runner=run_active_vision_light_interaction,
    module_name=__name__,
)


class DesktopPet(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(_desktop_pet_window_flags())
        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            _should_use_translucent_window(),
        )
        always_visible_attribute = _window_defaults.always_visible_tool_window_attribute(
            Qt.WidgetAttribute,
            is_macos=_is_macos(),
        )
        if always_visible_attribute is not None:
            self.setAttribute(always_visible_attribute, True)
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowIcon(_application_icon())
        self.config = load_config()
        self.apply_window_geometry_from_config()
        self.desktop_command_router = self._build_desktop_command_router()
        self._config_mtime = self._config_mtime_token()
        self._desktop_command_mtime = self._desktop_command_mtime_token()
        self._last_desktop_command_nonce = ""
        self.drag_offset = QPoint()
        self.window_locked = bool(self.config["window"]["locked"])
        self.motion_items: list[dict] = []
        self.expression_names: list[str] = []
        self.lipsync_meta: dict = {"gain": 1.0, "mouth_open_ids": [], "mouth_form_ids": []}
        self.live2d_model_path: Path | None = None
        self._body_bridge = _build_body_bridge()
        self._desktop_config_actions = _build_desktop_config_actions(self)
        self.service_lifecycle = create_desktop_service_lifecycle(
            self.config,
            _build_desktop_service_lifecycle_dependencies(),
        )
        self._shutdown_in_progress = False
        self.control_panel = None
        self.settings_window = None
        self._settings_window_controller = _build_settings_window_controller(self)
        self._context_menu_controller = _build_context_menu_controller(self)
        self._window_interaction_controller = _build_window_interaction_controller(self)
        self._window_region_controller = _build_window_region_controller(self)
        self._web_permissions_controller = _build_web_permissions_controller(self)
        self.click_preview_overlay = None
        self._settings_window_url = ""
        self.resize_margin = 8
        self._window_dragging = False
        self._window_resizing = False
        self._resize_edges: tuple[bool, bool, bool, bool] = (False, False, False, False)  # left, top, right, bottom
        self._drag_start_global = QPoint()
        self._drag_start_geometry = self.geometry()
        self._python_event_filters_installed = False
        self.vision_controller = ScreenVisionController(self)
        self.environment_controller = EnvironmentController(self, timer_factory=QTimer)
        self._desktop_pet_visibility = DesktopPetVisibilityController(self)

        self.bridge = create_connected_pet_bridge(self, PetBridge)
        self.approval_notification_controller = NativeApprovalNotificationController(
            source_root=ROOT_DIR / "app" / "native_approval_notifier",
            cache_root=ROOT_DIR / "data" / "native_helpers",
            on_decision=self.bridge.approvalNotificationDecision.emit,
        )

        browser_setup = setup_desktop_browser(
            self,
            bridge=self.bridge,
            root_dir=ROOT_DIR,
            dependencies=_build_desktop_browser_setup_dependencies(),
        )
        self.browser = browser_setup.browser
        self.channel = browser_setup.channel
        self._python_event_filters_installed = browser_setup.event_filter_installed

        self._window_region_controller.clear_interactive_regions()

        self.refresh_motion_list(prefer_reset=False)
        self.start_qwen_tts_service_async()
        self.ensure_backend_service()
        self.ensure_asr_service()
        self.vision_controller.apply_config(self.config)
        self.environment_controller.apply_config(self.config)

        self._config_poll_timer = QTimer(self)
        self._config_poll_timer.timeout.connect(self.on_config_poll)
        self._config_poll_timer.start(1000)
        self._write_desktop_host_heartbeat()

    def _build_desktop_command_router(
        self,
        *,
        write_response_func=None,
        heartbeat_func=None,
    ) -> object:
        return create_desktop_command_router(
            self,
            _build_desktop_command_router_dependencies(),
            write_response_func=write_response_func,
            heartbeat_func=heartbeat_func,
        )

    def _ensure_desktop_command_router(self):
        router = getattr(self, "desktop_command_router", None)
        if router is not None:
            return router

        write_response_func = None
        host_write_response = getattr(self, "_write_desktop_command_response", None)
        if (
            callable(host_write_response)
            and getattr(type(self), "_write_desktop_command_response", None) is not DesktopPet._write_desktop_command_response
        ):
            write_response_func = host_write_response

        heartbeat_func = None
        host_heartbeat = getattr(self, "_write_desktop_host_heartbeat", None)
        if (
            callable(host_heartbeat)
            and getattr(type(self), "_write_desktop_host_heartbeat", None) is not DesktopPet._write_desktop_host_heartbeat
        ):
            heartbeat_func = host_heartbeat

        router = DesktopPet._build_desktop_command_router(
            self,
            write_response_func=write_response_func,
            heartbeat_func=heartbeat_func,
        )
        setattr(self, "desktop_command_router", router)
        return router

    @property
    def backend_process(self):
        return self.service_lifecycle.backend_process

    @backend_process.setter
    def backend_process(self, value) -> None:
        self.service_lifecycle.backend_process = value

    @property
    def backend_started_by_app(self) -> bool:
        return self.service_lifecycle.backend_started_by_app

    @backend_started_by_app.setter
    def backend_started_by_app(self, value: bool) -> None:
        self.service_lifecycle.backend_started_by_app = bool(value)

    @property
    def asr_process(self):
        return self.service_lifecycle.asr_process

    @asr_process.setter
    def asr_process(self, value) -> None:
        self.service_lifecycle.asr_process = value

    @property
    def asr_started_by_app(self) -> bool:
        return self.service_lifecycle.asr_started_by_app

    @asr_started_by_app.setter
    def asr_started_by_app(self, value: bool) -> None:
        self.service_lifecycle.asr_started_by_app = bool(value)

    def on_feature_permission_requested(self, security_origin, feature) -> None:
        _web_permissions_controller_for(self).on_feature_permission_requested(security_origin, feature)

    def apply_window_geometry_from_config(self) -> None:
        geom = self.config["window"]
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            resolved = (
                int(geom["x"]),
                int(geom["y"]),
                int(geom["width"]),
                int(geom["height"]),
            )
        else:
            available = screen.availableGeometry()
            resolved = _window_defaults.visible_window_geometry(
                x=int(geom["x"]),
                y=int(geom["y"]),
                width=int(geom["width"]),
                height=int(geom["height"]),
                available_x=int(available.x()),
                available_y=int(available.y()),
                available_width=int(available.width()),
                available_height=int(available.height()),
            )
        self.setGeometry(*resolved)
        geom.update(
            {
                "x": resolved[0],
                "y": resolved[1],
                "width": resolved[2],
                "height": resolved[3],
            }
        )

    def sync_panel(self) -> None:
        if self.control_panel is not None:
            self.control_panel.set_from_config(self.config, self.motion_items)

    def refresh_motion_list(self, prefer_reset: bool) -> None:
        _body_bridge_for(self).refresh_motion_list(self, prefer_reset=prefer_reset)

    def _config_mtime_token(self):
        return _desktop_config_actions_for(self).config_mtime_token()

    def _desktop_command_mtime_token(self):
        return DesktopPet._ensure_desktop_command_router(self).desktop_command_mtime_token()

    def _write_desktop_host_heartbeat(self) -> None:
        DesktopPet._ensure_desktop_command_router(self).write_desktop_host_heartbeat()

    def _response_path_for_command(self, command: dict) -> Path:
        return DesktopPet._ensure_desktop_command_router(self).response_path_for_command(command)

    def _write_desktop_command_response(self, command: dict, status: str, result: dict[str, object] | None = None) -> None:
        DesktopPet._ensure_desktop_command_router(self).write_desktop_command_response(command, status, result)

    def on_config_poll(self) -> None:
        self._write_desktop_host_heartbeat()
        token = self._config_mtime_token()
        if token is not None and token != self._config_mtime:
            self._config_mtime = token
            self.reload_config_from_disk()
        self.on_desktop_command_poll()

    def on_desktop_command_poll(self) -> None:
        DesktopPet._ensure_desktop_command_router(self).on_desktop_command_poll()

    def process_desktop_command(self, command: dict) -> None:
        DesktopPet._ensure_desktop_command_router(self).process_desktop_command(command)

    def reload_config_from_disk(self) -> None:
        _desktop_config_actions_for(self).reload_config_from_disk()

    def _settings_page_url(self) -> str:
        return _settings_window_controller_for(self).settings_page_url()

    def _clear_settings_window(self, window=None) -> None:
        _settings_window_controller_for(self).clear_settings_window(window)

    def _create_settings_window(self, url: str):
        return _settings_window_controller_for(self).create_settings_window(url)

    def _settings_window_is_usable(self, window) -> bool:
        return _settings_window_controller_for(self).settings_window_is_usable(window)

    def _navigate_settings_window(self, window, url: str) -> None:
        _settings_window_controller_for(self).navigate_settings_window(window, url)

    def _show_or_focus_settings_window(self, url: str) -> None:
        _settings_window_controller_for(self).show_or_focus_settings_window(url)

    def show_click_preview(self, payload: str) -> None:
        _click_preview.show_click_preview(
            self,
            payload,
            overlay_factory=lambda: ClickPreviewOverlay(),
            json_loads=json.loads,
            print_func=print,
        )

    def hide_click_preview(self) -> None:
        _click_preview.hide_click_preview(self)

    def show_approval_notification(self, payload: str) -> None:
        self.approval_notification_controller.show(payload)

    def cancel_approval_notification(self, proposal_id: str) -> None:
        self.approval_notification_controller.cancel(proposal_id)

    def open_settings_page(self) -> None:
        self.ensure_backend_service()
        url = self._settings_page_url()
        try:
            self._show_or_focus_settings_window(url)
        except Exception as exc:
            print(f"打开设置页失败: {exc}")

    @Slot(str)
    def apply_interactive_regions(self, payload: str) -> None:
        self._window_region_controller.apply_interactive_regions(payload)

    def show_context_menu(self, pos: QPoint) -> None:
        _context_menu_controller_for(self).show_context_menu(pos)

    def ensure_backend_service(self) -> None:
        self.service_lifecycle.ensure_backend_service()

    def ensure_asr_service(self) -> None:
        self.service_lifecycle.ensure_asr_service()

    def start_qwen_tts_service_async(self) -> None:
        self.service_lifecycle.start_qwen_tts_service_async()

    def request_asr_warmup(self) -> None:
        self.service_lifecycle.request_asr_warmup()

    def _start_asr_warmup_progress_monitor(self, base_url: str) -> None:
        self.service_lifecycle.start_asr_warmup_progress_monitor(base_url)

    def _run_asr_warmup_progress_monitor(self, base_url: str) -> None:
        self.service_lifecycle.run_asr_warmup_progress_monitor(base_url)

    def _stop_managed_process(self, proc: subprocess.Popen | None, *, started_by_app: bool) -> None:
        self.service_lifecycle.stop_managed_process(proc, started_by_app=started_by_app)

    def stop_backend_service(self) -> None:
        self.service_lifecycle.stop_backend_service()

    def stop_asr_service(self) -> None:
        self.service_lifecycle.stop_asr_service()

    def stop_qwen_tts_service(self) -> None:
        self.service_lifecycle.stop_qwen_tts_service()

    def shutdown_desktop(self) -> None:
        _desktop_shutdown_controller_for(self).shutdown_desktop()

    def hide_desktop_pet(self) -> None:
        self._desktop_pet_visibility.hide()

    def restore_desktop_pet(self) -> None:
        self._desktop_pet_visibility.restore()

    def _ensure_window_interaction_controller(self) -> WindowInteractionController:
        controller = getattr(self, "_window_interaction_controller", None)
        if controller is None:
            controller = _build_window_interaction_controller(self)
            self._window_interaction_controller = controller
        return controller

    def eventFilter(self, watched, event):
        result = DesktopPet._ensure_window_interaction_controller(self).event_filter(watched, event)
        if result is not None:
            return result
        return super().eventFilter(watched, event)

    def current_body_payload(self) -> dict:
        return _body_bridge_for(self).current_body_payload(self)

    def on_web_loaded(self, ok: bool) -> None:
        if not ok:
            return
        self.apply_config_to_web()

    def apply_config_to_web(self, after_script: str | None = None) -> None:
        _body_bridge_for(self).apply_config_to_web(self, after_script=after_script)

    def play_motion(self, group: str, index: int = 0, reload_model: bool = False) -> None:
        _body_bridge_for(self).play_motion(self, group, index, reload_model=reload_model)

    def play_expression(self, name: str, reload_model: bool = False) -> None:
        _body_bridge_for(self).play_expression(self, name, reload_model=reload_model)

    def play_action(self, action: dict) -> None:
        _body_bridge_for(self).play_action(self, action)

    @Slot(str)
    def on_web_state_changed(self, payload: str) -> None:
        _body_bridge_for(self).on_web_state_changed(self, payload)

    def apply_from_panel(self) -> None:
        _desktop_config_actions_for(self).apply_from_panel()

    def save_config(self) -> None:
        _desktop_config_actions_for(self).save_config()

    def reset_to_default(self) -> None:
        _desktop_config_actions_for(self).reset_to_default()

    def closeEvent(self, event) -> None:
        event.accept()
        controller = getattr(self, "approval_notification_controller", None)
        if controller is not None:
            controller.stop_all()
        try:
            self.save_config()
        except Exception as exc:
            print(f"保存配置失败: {exc}")
        try:
            if self.settings_window is not None:
                self.settings_window.close()
        except Exception:
            pass
        try:
            if self.click_preview_overlay is not None:
                self.click_preview_overlay.close()
        except Exception:
            pass
        self.shutdown_desktop()
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, lambda: app.exit(0))


if __name__ == "__main__":
    _install_macos_native_stderr_filter()
    if _should_force_software_opengl():
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = DesktopApplication(sys.argv)
    apply_application_identity(app)
    configure_application_lifecycle(app)
    pet = DesktopPet()
    app.desktop_reopen_callback = pet.restore_desktop_pet
    app.aboutToQuit.connect(pet.shutdown_desktop)
    pet.show()

    sys.exit(app.exec())
