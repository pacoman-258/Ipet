import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from backend.active_vision import normalize_active_observation_config
from backend.hermes import DEFAULT_HERMES_CONFIG, normalize_hermes_config
from backend.runtime_config import (
    DEFAULT_RUNTIME_CONFIG,
    RUNTIME_ASTRBOT,
    RUNTIME_HERMES,
    local_service_health_url,
    mirror_runtime_compat,
    normalize_runtime_config,
    runtime_sidecar_config,
)
from backend.vision import DEFAULT_VISION_CONFIG, normalize_vision_config


LOCAL_API_TOKEN_ENV = "IPET_LOCAL_API_TOKEN"
LOCAL_API_TOKEN_HEADER = "X-Ipet-Local-Token"
LOCAL_API_TOKEN = str(os.environ.get(LOCAL_API_TOKEN_ENV) or secrets.token_urlsafe(32))
os.environ.setdefault(LOCAL_API_TOKEN_ENV, LOCAL_API_TOKEN)


def _platform_name(platform_name: str | None = None) -> str:
    return str(platform_name or sys.platform).strip().lower()


def _is_macos(platform_name: str | None = None) -> bool:
    return _platform_name(platform_name) == "darwin"


def _default_asr_enabled(platform_name: str | None = None) -> bool:
    return not _is_macos(platform_name)


def _default_asr_config(platform_name: str | None = None) -> dict[str, object]:
    return {
        "enabled": _default_asr_enabled(platform_name),
        "provider": "funasr",
        "api_base_url": "http://127.0.0.1:8012",
        "push_to_talk_key": "Alt",
        "interim_results": True,
    }


def _qt_runtime_env_defaults(platform_name: str | None = None) -> dict[str, str]:
    defaults = {
        "QTWEBENGINE_DISABLE_SANDBOX": "1",
    }
    if _is_macos(platform_name):
        defaults["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(
            [
                "--disable-logging",
                "--log-level=3",
                "--enable-webgl",
                "--ignore-gpu-blocklist",
                "--disable-features=UseSkiaRenderer,VizDisplayCompositor",
            ]
        )
        return defaults
    defaults["QT_OPENGL"] = "software"
    defaults["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(
        [
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-webgl",
            "--ignore-gpu-blocklist",
            "--disable-gpu-compositing",
            "--disable-gpu-rasterization",
            "--disable-direct-composition",
            "--disable-gpu-memory-buffer-compositor-resources",
            "--disable-features=UseSkiaRenderer,VizDisplayCompositor,CanvasOopRasterization",
            "--in-process-gpu",
        ]
    )
    return defaults


def _apply_qt_runtime_env(env: dict[str, str] | None = None, platform_name: str | None = None) -> dict[str, str]:
    target = env if env is not None else os.environ
    defaults = _qt_runtime_env_defaults(platform_name)
    for key, value in defaults.items():
        target.setdefault(key, value)
    return defaults


def _desktop_pet_window_flags(platform_name: str | None = None):
    if _is_macos(platform_name):
        return Qt.WindowType.Window
    return (
        Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.Tool
    )


def _should_use_translucent_window(force_opaque: bool | None = None, platform_name: str | None = None) -> bool:
    effective_force_opaque = FORCE_OPAQUE_WINDOW if force_opaque is None else bool(force_opaque)
    return (not effective_force_opaque) and (not _is_macos(platform_name))


def _desktop_pet_background_color(platform_name: str | None = None) -> tuple[int, int, int, int]:
    if _should_use_translucent_window(platform_name=platform_name):
        return (0, 0, 0, 0)
    return (18, 18, 18, 255)


def _should_enable_webgl(platform_name: str | None = None) -> bool:
    return True


def _should_force_software_opengl(platform_name: str | None = None) -> bool:
    return not _is_macos(platform_name)


def _should_install_python_event_filters(platform_name: str | None = None) -> bool:
    return not _is_macos(platform_name)


def _prefer_pyqt_bindings(platform_name: str | None = None) -> bool:
    return _is_macos(platform_name)


_apply_qt_runtime_env()

if _prefer_pyqt_bindings():
    try:
        from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QPoint, Qt, QEvent, QSignalBlocker, QTimer, QUrl, pyqtSignal as Signal, pyqtSlot as Slot
        from PyQt6.QtGui import QAction, QColor, QGuiApplication, QImage, QPainter, QPixmap
        from PyQt6.QtWebChannel import QWebChannel
        from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QFileDialog,
            QFormLayout,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMenu,
            QPlainTextEdit,
            QPushButton,
            QSlider,
            QSpinBox,
            QVBoxLayout,
            QWidget,
        )
    except ImportError:
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QPoint, Qt, QEvent, QSignalBlocker, QTimer, QUrl, Signal, Slot
        from PySide6.QtGui import QAction, QColor, QGuiApplication, QImage, QPainter, QPixmap
        from PySide6.QtWebChannel import QWebChannel
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QFileDialog,
            QFormLayout,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMenu,
            QPlainTextEdit,
            QPushButton,
            QSlider,
            QSpinBox,
            QVBoxLayout,
            QWidget,
        )
else:
    try:
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QPoint, Qt, QEvent, QSignalBlocker, QTimer, QUrl, Signal, Slot
        from PySide6.QtGui import QAction, QColor, QGuiApplication, QImage, QPainter, QPixmap
        from PySide6.QtWebChannel import QWebChannel
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QFileDialog,
            QFormLayout,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMenu,
            QPlainTextEdit,
            QPushButton,
            QSlider,
            QSpinBox,
            QVBoxLayout,
            QWidget,
        )
    except ImportError:
        from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QPoint, Qt, QEvent, QSignalBlocker, QTimer, QUrl, pyqtSignal as Signal, pyqtSlot as Slot
        from PyQt6.QtGui import QAction, QColor, QGuiApplication, QImage, QPainter, QPixmap
        from PyQt6.QtWebChannel import QWebChannel
        from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QFileDialog,
            QFormLayout,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMenu,
            QPlainTextEdit,
            QPushButton,
            QSlider,
            QSpinBox,
            QVBoxLayout,
            QWidget,
        )

ROOT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = ROOT_DIR / "pet_config.json"
RUNTIME_LOG_DIR = ROOT_DIR / ".runtime-logs"
FORCE_OPAQUE_WINDOW = os.environ.get("PET_FORCE_OPAQUE", "0") == "1"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8008"
DEFAULT_ASR_API_BASE_URL = "http://127.0.0.1:8012"
DEFAULT_HERMES_MODEL = "hermes-agent"
DEFAULT_ASR_CONFIG = _default_asr_config()
DEFAULT_TOOL_TIMEOUT_SEC = 180
LEGACY_TOOL_TIMEOUT_SEC = 10
RUNTIME_COMMAND_PATH = ROOT_DIR / ".pet_runtime_command.json"
RUNTIME_COMMAND_RESPONSE_PATH = ROOT_DIR / ".pet_runtime_command.response.json"
RUNTIME_HOST_HEARTBEAT_PATH = ROOT_DIR / ".pet_runtime_host.heartbeat.json"
AUTOGEN_MODEL_SUFFIX = ".autogen.model3.json"
BACKEND_VENV_DIRNAME = ".venv-py312"


def _common_exec_search_dirs(root_dir: Path | None = None) -> list[str]:
    root = root_dir or ROOT_DIR
    home = Path.home()
    candidates: list[Path] = []
    if os.name == "nt":
        candidates.extend(
            [
                root / ".venv" / "Scripts",
                root / BACKEND_VENV_DIRNAME / "Scripts",
                home / "AppData" / "Roaming" / "Python" / "Scripts",
            ]
        )
    else:
        candidates.extend(
            [
                root / ".venv" / "bin",
                root / BACKEND_VENV_DIRNAME / "bin",
                home / ".local" / "bin",
                home / ".cargo" / "bin",
                Path("/opt/homebrew/bin"),
                Path("/opt/homebrew/sbin"),
                Path("/usr/local/bin"),
                Path("/usr/local/sbin"),
            ]
        )

    seen: set[str] = set()
    resolved: list[str] = []
    for candidate in candidates:
        text = str(candidate)
        if not text or text in seen or not candidate.exists():
            continue
        seen.add(text)
        resolved.append(text)
    return resolved


def _augment_process_path(env: dict[str, str] | None = None, *, root_dir: Path | None = None) -> str:
    target = env if env is not None else os.environ
    current = [item for item in str(target.get("PATH") or "").split(os.pathsep) if item]
    prefixes: list[str] = []
    seen = set(current)
    for candidate in _common_exec_search_dirs(root_dir):
        if candidate in seen:
            continue
        seen.add(candidate)
        prefixes.append(candidate)
    target["PATH"] = os.pathsep.join(prefixes + current)
    return target["PATH"]


def _preferred_python_commands(root_dir: Path | None = None) -> list[str]:
    root = root_dir or ROOT_DIR
    commands: list[str] = []
    version_text = ""
    try:
        version_text = (root / ".python-version").read_text(encoding="utf-8").strip()
    except Exception:
        version_text = ""
    parts = [item for item in version_text.split(".") if item]
    if len(parts) >= 2:
        commands.append(f"python{parts[0]}.{parts[1]}")
    if parts:
        commands.append(f"python{parts[0]}")
    commands.extend(["python3.12", "python3"])

    seen: set[str] = set()
    ordered: list[str] = []
    for item in commands:
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


_augment_process_path()


def _python_entry_for_venv(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _runtime_log_path(name: str) -> Path:
    try:
        RUNTIME_LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        return ROOT_DIR / f".{name}.log"
    return RUNTIME_LOG_DIR / f"{name}.log"


def _truncate_runtime_log(path: Path) -> Path:
    try:
        path.write_text("", encoding="utf-8")
    except Exception:
        pass
    return path


def _tail_runtime_log(path: Path, *, max_lines: int = 20) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""
    tail = lines[-max(1, int(max_lines)) :]
    return "\n".join(line.rstrip() for line in tail if str(line).strip())


def _python_command_exists(command: str) -> bool:
    text = str(command or "").strip()
    if not text:
        return False
    if os.path.sep in text or (os.path.altsep and os.path.altsep in text):
        return Path(text).exists()
    return shutil.which(text) is not None


def _python_supports_backend(command: str) -> bool:
    if not _python_command_exists(command):
        return False
    try:
        result = subprocess.run(
            [str(command), "-c", "import uvicorn; import backend.app"],
            cwd=str(ROOT_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def resolve_backend_python() -> str:
    override = str(os.environ.get("PET_BACKEND_PYTHON") or "").strip()
    candidates: list[str] = []
    if override:
        candidates.append(override)
    candidates.append(str(_python_entry_for_venv(ROOT_DIR / ".venv")))
    candidates.append(str(_python_entry_for_venv(ROOT_DIR / BACKEND_VENV_DIRNAME)))
    candidates.extend(_preferred_python_commands())
    candidates.append(sys.executable)

    seen: set[str] = set()
    fallback = sys.executable
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if _python_supports_backend(normalized):
            return normalized
        if _python_command_exists(normalized) and fallback == sys.executable:
            fallback = normalized
    return fallback


def resolve_asr_python() -> str:
    override = str(os.environ.get("PET_ASR_PYTHON") or "").strip()
    if override:
        return override
    for dirname in (BACKEND_VENV_DIRNAME, ".venv"):
        candidate = _python_entry_for_venv(ROOT_DIR / dirname)
        if candidate.exists():
            return str(candidate)
    for command in _preferred_python_commands():
        if _python_command_exists(command):
            return command
    return sys.executable


def _find_default_model() -> str:
    preferred = ROOT_DIR / "model" / "hiyori_free_zh" / "runtime" / "hiyori_free_t08.model3.json"
    if preferred.exists():
        return preferred.relative_to(ROOT_DIR).as_posix()

    for candidate in (ROOT_DIR / "model").rglob("*.model3.json"):
        try:
            return candidate.relative_to(ROOT_DIR).as_posix()
        except ValueError:
            return str(candidate)

    return ""


DEFAULT_CONFIG = {
    "hermes": json.loads(json.dumps(DEFAULT_HERMES_CONFIG)),
    "runtime": json.loads(json.dumps(DEFAULT_RUNTIME_CONFIG)),
    "vision": json.loads(json.dumps(DEFAULT_VISION_CONFIG)),
    "model_path": _find_default_model(),
    "window": {
        "x": 120,
        "y": 80,
        "width": 420,
        "height": 640,
        "locked": False,
    },
    "pet": {
        "scale": 0.3,
        "offset_x": 0,
        "offset_y": 40,
        "rotation": 0.0,
        "opacity": 1.0,
        "edit_mode": False,
        "follow_mouse": True,
        "background_enabled": False,
        "background_image": "",
        "background_overlay_opacity": 0.42,
    },
    "chat": {
        "backend_url": DEFAULT_BACKEND_URL,
        "model": DEFAULT_HERMES_MODEL,
        "session_id": "default",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate_pct": 0,
        "tts_provider": "edge_tts",
        "tts_provider_url": "",
        "expression_mode": True,
        "expression_output_format": "ndjson_v1",
        "react_enabled": True,
        "react_visibility": "inline",
        "max_reasoning_steps": 10,
        "tooling": {
            "enabled": True,
            "mode": "mcp_local_phase2",
            "file_allowlist": [str(ROOT_DIR)],
            "network_allow_domains": [],
            "max_tool_calls_per_turn": 6,
            "tool_timeout_sec": DEFAULT_TOOL_TIMEOUT_SEC,
            "third_party": {
                "enabled": True,
                "servers": [],
            },
        },
        "skills": {
            "enabled": True,
            "default_active_ids": [],
        },
        "asr": json.loads(json.dumps(DEFAULT_ASR_CONFIG)),
        "system_prompt": "",
    },
}

CUSTOM_HTTP_TTS_PRESETS = {
    "basic": {
        "label": "自定义 HTTP 模板",
        "config": {
            "url": "http://127.0.0.1:9880/",
            "payload": {},
            "headers": {},
            "query": {},
            "timeout_sec": 60,
        },
    },
    "gpt_sovits": {
        "label": "GPT-SoVITS 预设",
        "config": {
            "url": "http://127.0.0.1:9880/",
            "payload": {
                "text_language": "ja",
                "refer_wav_path": "tts-voice-model/reference.wav",
                "prompt_text": "请改成参考音频对应文本",
                "prompt_language": "ja",
            },
            "inject_fields": ["text"],
            "headers": {},
            "query": {},
            "timeout_sec": 300,
        },
    },
}


def build_custom_http_tts_preset(key: str) -> str:
    preset = CUSTOM_HTTP_TTS_PRESETS.get(key) or CUSTOM_HTTP_TTS_PRESETS["basic"]
    return json.dumps(preset["config"], ensure_ascii=False, indent=2)


def deep_merge(base: dict, override: dict) -> dict:
    merged = json.loads(json.dumps(base))
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _migrate_tool_timeout(tooling: dict | None) -> None:
    if not isinstance(tooling, dict):
        return
    try:
        timeout_sec = int(tooling.get("tool_timeout_sec", DEFAULT_TOOL_TIMEOUT_SEC))
    except Exception:
        timeout_sec = DEFAULT_TOOL_TIMEOUT_SEC
    if timeout_sec == LEGACY_TOOL_TIMEOUT_SEC:
        tooling["tool_timeout_sec"] = DEFAULT_TOOL_TIMEOUT_SEC


LEGACY_CHAT_CONFIG_KEYS = (
    "llm_provider",
    "api_base_url",
    "api_key",
    "router_enabled",
    "router_llm_provider",
    "router_api_base_url",
    "router_api_key",
    "router_model",
    "memory_window",
    "topic_history",
    "long_term_memory",
)


def _normalize_hermes_chat_config(config: dict) -> None:
    chat_cfg = config.get("chat")
    if not isinstance(chat_cfg, dict):
        chat_cfg = {}
        config["chat"] = chat_cfg
    for key in LEGACY_CHAT_CONFIG_KEYS:
        chat_cfg.pop(key, None)
    model = str(chat_cfg.get("model") or "").strip()
    chat_cfg["model"] = model or DEFAULT_HERMES_MODEL
    session_id = str(chat_cfg.get("session_id") or "").strip()
    chat_cfg["session_id"] = session_id or "default"


def _normalize_hermes_sidecar_config(config: dict) -> None:
    mirror_runtime_compat(config, root_dir=ROOT_DIR)


def _runtime_display_name(runtime_id: str) -> str:
    return "AstrBot" if runtime_id == RUNTIME_ASTRBOT else "Hermes Agent"


def _runtime_missing_command_message(runtime_id: str, runtime_cfg: dict, *, root_dir: Path | None = None) -> str:
    root = root_dir or ROOT_DIR
    cwd = str(runtime_cfg.get("cwd") or "").strip() or str(root)
    health = local_service_health_url(runtime_cfg)
    label = _runtime_display_name(runtime_id)
    example = (
        'Example astrbot.command: ["uv", "run", "astrbot"]'
        if runtime_id == RUNTIME_ASTRBOT
        else 'Example hermes.command: ["uv", "run", "hermes", "dashboard", "--host", "127.0.0.1", "--port", "9119", "--no-open", "--tui"]'
    )
    return "\n".join(
        [
            f"{label} auto_start is enabled but no command is configured.",
            f"cwd: {cwd}",
            f"health: {health}",
            example,
        ]
    )


def _hermes_missing_command_message(hermes_cfg: dict, *, root_dir: Path | None = None) -> str:
    root = root_dir or ROOT_DIR
    cwd = str(hermes_cfg.get("cwd") or "").strip() or str(root)
    base_url = str(hermes_cfg.get("base_url") or DEFAULT_HERMES_CONFIG["base_url"]).strip()
    health_path = str(hermes_cfg.get("health_path") or DEFAULT_HERMES_CONFIG["health_path"]).strip()
    return "\n".join(
        [
            "Hermes Agent auto_start is enabled but no command is configured.",
            f"cwd: {cwd}",
            f"health: {base_url.rstrip('/')}/{health_path.lstrip('/')}",
            "Set hermes.cwd to the real Hermes Agent checkout, not the repo-local Hermes storage directory.",
            'Example hermes.command: ["uv", "run", "hermes", "dashboard", "--host", "127.0.0.1", "--port", "9119", "--no-open", "--tui"]',
        ]
    )


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return json.loads(json.dumps(DEFAULT_CONFIG))

    config = deep_merge(DEFAULT_CONFIG, raw if isinstance(raw, dict) else {})
    config["model_path"] = normalize_model_path(config.get("model_path", ""))
    pet_cfg = config.get("pet", {})
    if not isinstance(pet_cfg, dict):
        pet_cfg = {}
        config["pet"] = pet_cfg
    pet_cfg["background_enabled"] = bool(pet_cfg.get("background_enabled", False))
    pet_cfg["background_image"] = str(pet_cfg.get("background_image") or "").strip()
    try:
        pet_cfg["background_overlay_opacity"] = float(pet_cfg.get("background_overlay_opacity", 0.42))
    except Exception:
        pet_cfg["background_overlay_opacity"] = 0.42
    pet_cfg["background_overlay_opacity"] = max(0.0, min(0.9, pet_cfg["background_overlay_opacity"]))
    _migrate_tool_timeout(config.get("chat", {}).get("tooling", {}))
    config["vision"] = normalize_vision_config(config.get("vision", {}))
    _normalize_hermes_sidecar_config(config)
    _normalize_hermes_chat_config(config)
    return config


def extract_pet_display_name(system_prompt: str) -> str:
    raw = str(system_prompt or "").strip()
    if not raw:
        return "桌宠"
    try:
        data = json.loads(raw)
    except Exception:
        return "桌宠"
    if not isinstance(data, dict):
        return "桌宠"
    character = data.get("character")
    if isinstance(character, dict):
        for key in ("name_cn", "name"):
            value = str(character.get(key) or "").strip()
            if value:
                return value
    for key in ("name", "title"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return "桌宠"


def is_service_healthy(base_url: str, *, require_asr: bool = False) -> bool:
    try:
        resp = requests.get(f"{base_url.rstrip('/')}/api/health", timeout=1.5)
        if resp.status_code != 200:
            return False
        if not require_asr:
            return True
        payload = resp.json()
        return bool(payload.get("asr"))
    except Exception:
        return False


def is_backend_live(backend_url: str) -> bool:
    return is_service_healthy(backend_url)


def is_backend_healthy(backend_url: str) -> bool:
    return is_backend_live(backend_url) and backend_supports_required_routes(backend_url)


def is_asr_healthy(asr_url: str) -> bool:
    return is_service_healthy(asr_url, require_asr=True)


def parse_service_host_port(base_url: str, *, default_port: int) -> tuple[str, int]:
    parsed = urlparse(str(base_url or "").strip() or f"http://127.0.0.1:{default_port}")
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or default_port)
    return host, port


def backend_supports_required_routes(base_url: str) -> bool:
    try:
        resp = requests.get(f"{base_url.rstrip('/')}/openapi.json", timeout=1.5)
        if resp.status_code != 200:
            return False
        payload = resp.json()
    except Exception:
        return False

    paths = payload.get("paths", {}) if isinstance(payload, dict) else {}
    if not isinstance(paths, dict):
        return False

    topic_detail = paths.get("/api/chat/topics/{topic_id}")
    if isinstance(topic_detail, dict) and "delete" in topic_detail:
        return True

    topic_delete = paths.get("/api/chat/topics/{topic_id}/delete")
    return isinstance(topic_delete, dict) and "post" in topic_delete


def is_local_service_url(base_url: str) -> bool:
    parsed = urlparse(str(base_url or "").strip() or "")
    host = (parsed.hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost"}


def is_service_port_available(host: str, port: int) -> bool:
    bind_host = "127.0.0.1" if host == "localhost" else host
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind_host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def pick_backend_launch_url(preferred_url: str, *, max_offset: int = 12) -> str:
    preferred = str(preferred_url or "").strip() or DEFAULT_BACKEND_URL
    if not is_local_service_url(preferred):
        return preferred
    host, port = parse_service_host_port(preferred, default_port=8008)
    for offset in range(max(1, int(max_offset)) + 1):
        candidate_port = port + offset
        if is_service_port_available(host, candidate_port):
            return f"http://{host}:{candidate_port}"
    return preferred


def canonicalize_model_source_path(model_path: Path) -> Path:
    original = model_path
    candidate = model_path
    while candidate.name.endswith(AUTOGEN_MODEL_SUFFIX):
        source_name = f"{candidate.name[: -len(AUTOGEN_MODEL_SUFFIX)]}.json"
        source_path = candidate.with_name(source_name)
        if source_path.exists():
            candidate = source_path
            continue
        return original
    return candidate


def resolve_model_path(path_text: str) -> Path:
    model_path = Path(path_text)
    if not model_path.is_absolute():
        model_path = ROOT_DIR / model_path
    try:
        resolved = model_path.resolve()
    except Exception:
        resolved = model_path
    return canonicalize_model_source_path(resolved)


def normalize_model_path(path_text: str) -> str:
    if not path_text:
        return ""

    resolved = resolve_model_path(path_text)
    try:
        return resolved.relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return str(resolved)


def resolve_background_image_path(path_text: str) -> Path:
    image_path = Path(str(path_text or "").strip())
    if not str(image_path):
        return ROOT_DIR
    if not image_path.is_absolute():
        image_path = ROOT_DIR / image_path
    try:
        return image_path.resolve()
    except Exception:
        return image_path


def _extract_motion_groups(model_json: dict) -> dict:
    groups = model_json.get("FileReferences", {}).get("Motions")
    if isinstance(groups, dict):
        return groups
    groups = model_json.get("Motions", {})
    return groups if isinstance(groups, dict) else {}


def _infer_motion_group(file_name: str) -> str:
    name = file_name
    if name.lower().endswith(".motion3.json"):
        name = name[: -len(".motion3.json")]
    token = re.split(r"[\d_\-\s]+", name.strip())[0]
    if not token:
        return "Auto"
    mapping = {
        "idle": "Idle",
        "tap": "Tap",
        "flick": "Flick",
    }
    return mapping.get(token.lower(), token[:1].upper() + token[1:])


def _resolve_runtime_directory_seed(start_dir_text: str) -> str:
    text = str(start_dir_text or "").strip()
    if not text:
        return str(ROOT_DIR)
    path = Path(text)
    if not path.is_absolute():
        path = ROOT_DIR / path
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    if resolved.exists() and resolved.is_dir():
        return str(resolved)
    return str(ROOT_DIR)


def _scan_motion_groups(model_path: Path) -> dict:
    model_dir = model_path.parent
    motion_files = sorted(model_dir.rglob("*.motion3.json"))
    groups: dict[str, list[dict]] = {}
    for file_path in motion_files:
        try:
            rel = file_path.relative_to(model_dir).as_posix()
        except ValueError:
            rel = file_path.name
        group = _infer_motion_group(file_path.name)
        groups.setdefault(group, []).append({"File": rel})
    return groups


def _extract_expression_defs(model_json: dict) -> list[dict]:
    exprs = model_json.get("FileReferences", {}).get("Expressions")
    return exprs if isinstance(exprs, list) else []


def _scan_expression_defs(model_path: Path) -> list[dict]:
    model_dir = model_path.parent
    expr_files = sorted(model_dir.rglob("*.exp3.json"))
    defs: list[dict] = []
    for file_path in expr_files:
        try:
            rel = file_path.relative_to(model_dir).as_posix()
        except ValueError:
            rel = file_path.name
        name = file_path.name
        if name.lower().endswith(".exp3.json"):
            name = name[: -len(".exp3.json")]
        defs.append({"Name": name, "File": rel})
    return defs


def extract_lipsync_meta(model_json: dict) -> dict:
    controllers = model_json.get("Controllers", {})
    if not isinstance(controllers, dict):
        controllers = {}

    gain = 1.0
    lipsync_cfg = controllers.get("LipSync", {})
    if isinstance(lipsync_cfg, dict):
        try:
            gain = max(0.25, float(lipsync_cfg.get("Gain", 1.0)))
        except Exception:
            gain = 1.0

    mouth_open_ids: list[str] = ["ParamMouthOpenY", "PARAM_MOUTH_OPEN_Y", "ParamMouthOpenX", "LipSync"]
    mouth_form_ids: list[str] = ["ParamMouthForm", "PARAM_MOUTH_FORM"]

    face_tracking = controllers.get("FaceTracking", {})
    if isinstance(face_tracking, dict):
        open_items = face_tracking.get("MouthOpenY", [])
        if isinstance(open_items, list):
            for item in open_items:
                if isinstance(item, dict):
                    param_id = str(item.get("Id") or "").strip()
                    if param_id and param_id not in mouth_open_ids:
                        mouth_open_ids.append(param_id)
        form_items = face_tracking.get("MouthForm", [])
        if isinstance(form_items, list):
            for item in form_items:
                if isinstance(item, dict):
                    param_id = str(item.get("Id") or "").strip()
                    if param_id and param_id not in mouth_form_ids:
                        mouth_form_ids.append(param_id)

    return {
        "gain": gain,
        "mouth_open_ids": mouth_open_ids,
        "mouth_form_ids": mouth_form_ids,
    }


def ensure_runtime_model_from_json(model_path: Path, model_json: dict) -> tuple[Path, dict, list[dict]]:
    groups = _extract_motion_groups(model_json)
    exprs = _extract_expression_defs(model_json)
    need_patch = False

    if not groups:
        groups = _scan_motion_groups(model_path)
        if groups:
            need_patch = True
    if not exprs:
        exprs = _scan_expression_defs(model_path)
        if exprs:
            need_patch = True

    if not need_patch:
        return model_path, groups, exprs

    patched = json.loads(json.dumps(model_json))
    patched.setdefault("FileReferences", {})
    if groups:
        patched["FileReferences"]["Motions"] = groups
    if exprs:
        patched["FileReferences"]["Expressions"] = exprs

    runtime_path = model_path.with_name(f"{model_path.stem}.autogen.model3.json")
    try:
        with runtime_path.open("w", encoding="utf-8") as f:
            json.dump(patched, f, ensure_ascii=False, indent=2)
        return runtime_path, groups, exprs
    except Exception:
        return model_path, groups, exprs


def ensure_runtime_model(model_path: Path) -> tuple[Path, dict, list[dict]]:
    if not model_path.exists():
        return model_path, {}, []
    try:
        with model_path.open("r", encoding="utf-8") as f:
            model_json = json.load(f)
    except Exception:
        return model_path, {}, []
    return ensure_runtime_model_from_json(model_path, model_json)


def action_items_from_defs(groups: dict, exprs: list[dict]) -> list[dict]:
    items: list[dict] = []
    for group_name, entries in groups.items():
        if not isinstance(entries, list):
            continue
        for idx, _ in enumerate(entries):
            items.append(
                {
                    "type": "motion",
                    "group": group_name,
                    "index": idx,
                    "label": f"{group_name}[{idx}]",
                }
            )
    for expr in exprs:
        if not isinstance(expr, dict):
            continue
        name = str(expr.get("Name") or "").strip()
        if not name:
            continue
        items.append(
            {
                "type": "expression",
                "name": name,
                "label": f"Expr:{name}",
            }
        )
    return items


class PetBridge(QObject):
    stateChanged = Signal(str)
    openSettingsRequested = Signal()
    startAsrWarmupRequested = Signal()
    minimizeWindowRequested = Signal()
    closeWindowRequested = Signal()

    @Slot(str)
    def petStateChanged(self, payload: str) -> None:
        self.stateChanged.emit(payload)

    @Slot(str)
    def log(self, text: str) -> None:
        print(f"[WEB] {text}")

    @Slot()
    def openSettingsPage(self) -> None:
        self.openSettingsRequested.emit()

    @Slot()
    def startAsrWarmup(self) -> None:
        self.startAsrWarmupRequested.emit()

    @Slot()
    def minimizeWindow(self) -> None:
        self.minimizeWindowRequested.emit()

    @Slot()
    def closeWindow(self) -> None:
        self.closeWindowRequested.emit()


class ControlPanel(QWidget):
    def __init__(self, pet_window: "DesktopPet"):
        super().__init__()
        self.pet_window = pet_window
        self.setWindowTitle("桌宠控制面板")
        self.resize(520, 520)

        self._build_ui()
        self._wire_events()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        model_box = QGroupBox("模型")
        model_layout = QGridLayout(model_box)

        self.model_path_input = QLineEdit()
        self.browse_button = QPushButton("浏览...")
        self.reload_button = QPushButton("重载模型")

        self.motion_combo = QComboBox()
        self.play_motion_button = QPushButton("播放动作")

        model_layout.addWidget(QLabel("model3.json 路径"), 0, 0)
        model_layout.addWidget(self.model_path_input, 0, 1)
        model_layout.addWidget(self.browse_button, 0, 2)
        model_layout.addWidget(self.reload_button, 1, 2)
        model_layout.addWidget(QLabel("动作"), 1, 0)
        model_layout.addWidget(self.motion_combo, 1, 1)
        model_layout.addWidget(self.play_motion_button, 2, 2)

        chat_box = QGroupBox("对话")
        chat_layout = QGridLayout(chat_box)
        self.chat_model_input = QLineEdit()
        self.chat_model_input.setPlaceholderText(DEFAULT_HERMES_MODEL)
        self.chat_voice_input = QLineEdit()
        self.chat_voice_input.setPlaceholderText("zh-CN-XiaoxiaoNeural")
        self.chat_tts_provider_combo = QComboBox()
        self.chat_tts_provider_combo.addItem("edge_tts")
        self.chat_tts_provider_combo.addItem("custom_http")
        self.chat_tts_preset_combo = QComboBox()
        self.chat_tts_preset_combo.addItem("选择预设...", "")
        for preset_key, preset in CUSTOM_HTTP_TTS_PRESETS.items():
            self.chat_tts_preset_combo.addItem(str(preset.get("label") or preset_key), preset_key)
        self.chat_tts_preset_apply_button = QPushButton("一键填入")
        self.chat_tts_provider_url_input = QLineEdit()
        self.chat_tts_provider_url_input.setPlaceholderText("自定义语音接口地址或配置")
        self.chat_tts_provider_url_input.setToolTip(
            '直接填 URL，或用“一键填入”生成 JSON 预设，再修改参考音频和提示文本'
        )
        self.chat_rate_slider = QSlider(Qt.Orientation.Horizontal)
        self.chat_rate_slider.setRange(-50, 100)
        self.chat_rate_slider.setValue(0)
        self.chat_rate_value_label = QLabel("+0%")
        self.expression_mode_check = QCheckBox("表情联动")
        self.expression_mode_check.setChecked(True)
        self.expression_format_value_label = QLabel("ndjson_v1")
        self.system_prompt_input = QPlainTextEdit()
        self.system_prompt_input.setPlaceholderText("系统提示词：定义桌宠人设、语气、规则等")
        self.system_prompt_input.setFixedHeight(88)
        self.backend_test_button = QPushButton("测试后端")
        self.backend_status_label = QLabel("unknown")
        self.tooling_enabled_check = QCheckBox("启用工具调用（含第三方MCP）")
        self.tooling_enabled_check.setChecked(True)
        rate_row = QWidget()
        rate_row_layout = QHBoxLayout(rate_row)
        rate_row_layout.setContentsMargins(0, 0, 0, 0)
        rate_row_layout.setSpacing(6)
        rate_row_layout.addWidget(self.chat_rate_slider, 1)
        rate_row_layout.addWidget(self.chat_rate_value_label, 0)
        chat_layout.addWidget(QLabel("Hermes 当前模型"), 0, 0)
        chat_layout.addWidget(self.chat_model_input, 0, 1)
        chat_layout.addWidget(self.backend_test_button, 0, 2)
        chat_layout.addWidget(QLabel("语音"), 1, 0)
        chat_layout.addWidget(self.chat_voice_input, 1, 1, 1, 2)
        chat_layout.addWidget(QLabel("TTS方式"), 2, 0)
        chat_layout.addWidget(self.chat_tts_provider_combo, 2, 1, 1, 2)
        chat_layout.addWidget(QLabel("TTS预设"), 3, 0)
        chat_layout.addWidget(self.chat_tts_preset_combo, 3, 1)
        chat_layout.addWidget(self.chat_tts_preset_apply_button, 3, 2)
        chat_layout.addWidget(QLabel("TTS接口"), 4, 0)
        chat_layout.addWidget(self.chat_tts_provider_url_input, 4, 1, 1, 2)
        chat_layout.addWidget(QLabel("语速"), 5, 0)
        chat_layout.addWidget(rate_row, 5, 1, 1, 2)
        chat_layout.addWidget(QLabel("表情驱动"), 6, 0)
        chat_layout.addWidget(self.expression_mode_check, 6, 1, 1, 2)
        chat_layout.addWidget(QLabel("协议版本"), 7, 0)
        chat_layout.addWidget(self.expression_format_value_label, 7, 1, 1, 2)
        chat_layout.addWidget(QLabel("系统提示词"), 8, 0)
        chat_layout.addWidget(self.system_prompt_input, 8, 1, 1, 2)
        chat_layout.addWidget(QLabel("状态"), 9, 0)
        chat_layout.addWidget(self.backend_status_label, 9, 1, 1, 2)
        chat_layout.addWidget(QLabel("工具"), 10, 0)
        chat_layout.addWidget(self.tooling_enabled_check, 10, 1, 1, 2)

        mcp_box = QGroupBox("第三方 MCP")
        mcp_layout = QGridLayout(mcp_box)
        self.third_party_enabled_check = QCheckBox("启用第三方MCP")
        self.third_party_enabled_check.setChecked(True)
        self.mcp_git_url_input = QLineEdit()
        self.mcp_git_url_input.setPlaceholderText("Git 仓库地址")
        self.mcp_install_git_button = QPushButton("从 Git 安装")
        self.mcp_register_local_button = QPushButton("注册本地目录")
        self.mcp_reload_button = QPushButton("重载 MCP")
        self.mcp_server_combo = QComboBox()
        self.mcp_toggle_button = QPushButton("启用/停用所选")
        self.mcp_status_view = QPlainTextEdit()
        self.mcp_status_view.setReadOnly(True)
        self.mcp_status_view.setFixedHeight(120)
        mcp_layout.addWidget(self.third_party_enabled_check, 0, 0, 1, 3)
        mcp_layout.addWidget(QLabel("Git 地址"), 1, 0)
        mcp_layout.addWidget(self.mcp_git_url_input, 1, 1)
        mcp_layout.addWidget(self.mcp_install_git_button, 1, 2)
        mcp_layout.addWidget(QLabel("已注册"), 2, 0)
        mcp_layout.addWidget(self.mcp_server_combo, 2, 1)
        mcp_layout.addWidget(self.mcp_toggle_button, 2, 2)
        mcp_layout.addWidget(self.mcp_register_local_button, 3, 1)
        mcp_layout.addWidget(self.mcp_reload_button, 3, 2)
        mcp_layout.addWidget(self.mcp_status_view, 4, 0, 1, 3)

        pet_box = QGroupBox("形象参数")
        pet_form = QFormLayout(pet_box)

        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.05, 5.0)
        self.scale_spin.setSingleStep(0.05)
        self.scale_spin.setDecimals(2)

        self.offset_x_spin = QSpinBox()
        self.offset_x_spin.setRange(-5000, 5000)

        self.offset_y_spin = QSpinBox()
        self.offset_y_spin.setRange(-5000, 5000)

        self.rotation_spin = QDoubleSpinBox()
        self.rotation_spin.setRange(-180.0, 180.0)
        self.rotation_spin.setSingleStep(1.0)
        self.rotation_spin.setDecimals(1)

        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0.1, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.setDecimals(2)

        self.edit_mode_check = QCheckBox("编辑模式（模型可拖拽，滚轮缩放）")
        self.follow_mouse_check = QCheckBox("视线跟随鼠标")

        pet_form.addRow("缩放", self.scale_spin)
        pet_form.addRow("偏移 X", self.offset_x_spin)
        pet_form.addRow("偏移 Y", self.offset_y_spin)
        pet_form.addRow("旋转", self.rotation_spin)
        pet_form.addRow("透明度", self.opacity_spin)
        pet_form.addRow(self.edit_mode_check)
        pet_form.addRow(self.follow_mouse_check)

        window_box = QGroupBox("窗口")
        window_form = QFormLayout(window_box)

        self.win_x_spin = QSpinBox()
        self.win_x_spin.setRange(-10000, 10000)

        self.win_y_spin = QSpinBox()
        self.win_y_spin.setRange(-10000, 10000)

        self.win_w_spin = QSpinBox()
        self.win_w_spin.setRange(120, 2000)

        self.win_h_spin = QSpinBox()
        self.win_h_spin.setRange(120, 2000)

        self.lock_window_check = QCheckBox("锁定窗口位置（仍可右键）")

        window_form.addRow("窗口 X", self.win_x_spin)
        window_form.addRow("窗口 Y", self.win_y_spin)
        window_form.addRow("窗口宽", self.win_w_spin)
        window_form.addRow("窗口高", self.win_h_spin)
        window_form.addRow(self.lock_window_check)

        button_row = QHBoxLayout()
        self.apply_button = QPushButton("应用")
        self.save_button = QPushButton("保存配置")
        self.reset_button = QPushButton("恢复默认")
        self.hide_button = QPushButton("关闭面板")
        button_row.addWidget(self.apply_button)
        button_row.addWidget(self.save_button)
        button_row.addWidget(self.reset_button)
        button_row.addWidget(self.hide_button)

        hint = QLabel("提示：按住 Alt + 左键可拖动桌宠窗口。")

        root.addWidget(model_box)
        root.addWidget(chat_box)
        root.addWidget(mcp_box)
        root.addWidget(pet_box)
        root.addWidget(window_box)
        root.addWidget(hint)
        root.addLayout(button_row)

    def _wire_events(self) -> None:
        self.browse_button.clicked.connect(self.on_browse_model)
        self.reload_button.clicked.connect(self.on_reload_model)
        self.play_motion_button.clicked.connect(self.on_play_motion)
        self.apply_button.clicked.connect(self.pet_window.apply_from_panel)
        self.save_button.clicked.connect(self.on_save)
        self.reset_button.clicked.connect(self.on_reset)
        self.hide_button.clicked.connect(self.hide)
        self.backend_test_button.clicked.connect(self.on_test_backend)
        self.chat_rate_slider.valueChanged.connect(self.on_chat_rate_changed)
        self.chat_tts_preset_apply_button.clicked.connect(self.on_apply_tts_preset)
        self.mcp_install_git_button.clicked.connect(self.on_install_mcp_from_git)
        self.mcp_register_local_button.clicked.connect(self.on_register_local_mcp)
        self.mcp_reload_button.clicked.connect(self.on_reload_mcp)
        self.mcp_toggle_button.clicked.connect(self.on_toggle_mcp)

    def on_chat_rate_changed(self, value: int) -> None:
        self.chat_rate_value_label.setText(f"{int(value):+d}%")

    def on_apply_tts_preset(self) -> None:
        preset_key = str(self.chat_tts_preset_combo.currentData() or "").strip()
        if not preset_key:
            return
        self.chat_tts_provider_combo.setCurrentText("custom_http")
        self.chat_tts_provider_url_input.setText(build_custom_http_tts_preset(preset_key))

    def on_browse_model(self) -> None:
        dialog = QFileDialog(self, "选择 Live2D 模型配置", str(ROOT_DIR))
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setNameFilters(
            [
                "Live2D 模型 (*.model3.json *.model.json)",
                "Cubism 4 (*.model3.json)",
                "Legacy (*.model.json)",
                "All Files (*)",
            ]
        )
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        if not dialog.exec():
            return
        selected = dialog.selectedFiles()
        if not selected:
            return
        path = selected[0]
        if not path:
            return

        normalized = normalize_model_path(path)
        self.model_path_input.setText(normalized)
        # Keep config in sync before refresh to avoid input being overwritten.
        self.pet_window.config["model_path"] = normalized
        self.pet_window.refresh_motion_list(prefer_reset=True)
        self.pet_window.apply_config_to_web()

    def on_reload_model(self) -> None:
        self.pet_window.apply_from_panel()
        self.pet_window.apply_config_to_web()

    def on_test_backend(self) -> None:
        self.pet_window.apply_from_panel()
        chat_cfg = self.pet_window.config.get("chat", {})
        backend_url = str(chat_cfg.get("backend_url", DEFAULT_BACKEND_URL))
        ok = is_backend_healthy(backend_url)
        self.backend_status_label.setText("online" if ok else "offline")
        self.refresh_third_party_mcp(force_reload=False)

    def refresh_third_party_mcp(self, force_reload: bool = False) -> None:
        chat_cfg = self.pet_window.config.get("chat", {})
        backend_url = str(chat_cfg.get("backend_url", DEFAULT_BACKEND_URL)).rstrip("/")
        if not is_backend_healthy(backend_url):
            self.mcp_status_view.setPlainText("后端离线，无法刷新第三方 MCP 状态。")
            return

        endpoint = "/api/mcp/reload" if force_reload else "/api/mcp/servers"
        method = requests.post if force_reload else requests.get
        try:
            resp = method(f"{backend_url}{endpoint}", timeout=30)
            resp.raise_for_status()
            data = resp.json()
            servers = data.get("servers", [])
        except Exception as exc:
            self.mcp_status_view.setPlainText(f"刷新第三方 MCP 失败：{exc}")
            return

        widgets = [self.mcp_server_combo]
        blockers = [QSignalBlocker(w) for w in widgets]
        _ = blockers
        self.mcp_server_combo.clear()

        lines: list[str] = []
        for item in servers if isinstance(servers, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            runtime = str(item.get("runtime") or "")
            install_status = str(item.get("install_status") or "")
            health_status = str(item.get("health_status") or "")
            source_type = str(item.get("source_type") or "")
            tools = item.get("tools", [])
            enabled_text = "enabled" if bool(item.get("enabled", True)) else "disabled"
            label = f"{name} [{runtime}] {health_status}"
            self.mcp_server_combo.addItem(label, item)
            tool_names = []
            if isinstance(tools, list):
                for tool in tools:
                    if isinstance(tool, dict):
                        fn = tool.get("function", {})
                        if isinstance(fn, dict):
                            tname = str(fn.get("name") or "").strip()
                            if tname:
                                tool_names.append(tname)
            lines.append(
                f"{name}\n"
                f"  runtime: {runtime}\n"
                f"  source: {source_type}\n"
                f"  install: {install_status}\n"
                f"  health: {health_status}\n"
                f"  state: {enabled_text}\n"
                f"  tools: {', '.join(tool_names) if tool_names else '-'}"
            )
            error_text = str(item.get("error") or "").strip()
            if error_text:
                lines.append(f"  error: {error_text}")
        self.mcp_status_view.setPlainText("\n\n".join(lines) if lines else "暂无第三方 MCP。")

    def on_install_mcp_from_git(self) -> None:
        self.pet_window.apply_from_panel()
        repo_url = self.mcp_git_url_input.text().strip()
        if not repo_url:
            self.mcp_status_view.setPlainText("请先填写 Git 仓库地址。")
            return
        backend_url = str(self.pet_window.config.get("chat", {}).get("backend_url", DEFAULT_BACKEND_URL)).rstrip("/")
        try:
            resp = requests.post(f"{backend_url}/api/mcp/install", json={"repo_url": repo_url, "name": ""}, timeout=180)
            resp.raise_for_status()
        except Exception as exc:
            self.mcp_status_view.setPlainText(f"Git 安装失败：{exc}")
            return
        self.pet_window.config = load_config()
        self.pet_window.sync_panel()
        self.refresh_third_party_mcp(force_reload=True)

    def on_register_local_mcp(self) -> None:
        self.pet_window.apply_from_panel()
        directory = QFileDialog.getExistingDirectory(self, "选择第三方 MCP 目录", str(ROOT_DIR))
        if not directory:
            return
        backend_url = str(self.pet_window.config.get("chat", {}).get("backend_url", DEFAULT_BACKEND_URL)).rstrip("/")
        try:
            resp = requests.post(
                f"{backend_url}/api/mcp/register-local",
                json={"path": directory, "name": ""},
                timeout=180,
            )
            resp.raise_for_status()
        except Exception as exc:
            self.mcp_status_view.setPlainText(f"注册本地目录失败：{exc}")
            return
        self.pet_window.config = load_config()
        self.pet_window.sync_panel()
        self.refresh_third_party_mcp(force_reload=True)

    def on_reload_mcp(self) -> None:
        self.pet_window.apply_from_panel()
        self.refresh_third_party_mcp(force_reload=True)

    def on_toggle_mcp(self) -> None:
        self.pet_window.apply_from_panel()
        item = self.mcp_server_combo.currentData()
        if not isinstance(item, dict):
            self.mcp_status_view.setPlainText("请先选择一个第三方 MCP。")
            return
        name = str(item.get("name") or "").strip()
        current_enabled = bool(item.get("enabled", True))
        backend_url = str(self.pet_window.config.get("chat", {}).get("backend_url", DEFAULT_BACKEND_URL)).rstrip("/")
        try:
            resp = requests.post(
                f"{backend_url}/api/mcp/toggle",
                json={"name": name, "enabled": not current_enabled},
                timeout=60,
            )
            resp.raise_for_status()
        except Exception as exc:
            self.mcp_status_view.setPlainText(f"切换 MCP 状态失败：{exc}")
            return
        self.pet_window.config = load_config()
        self.pet_window.sync_panel()
        self.refresh_third_party_mcp(force_reload=True)

    def on_play_motion(self) -> None:
        data = self.motion_combo.currentData()
        if not isinstance(data, dict):
            return
        self.pet_window.play_action(data)

    def on_save(self) -> None:
        self.pet_window.apply_from_panel()
        self.pet_window.save_config()

    def on_reset(self) -> None:
        self.pet_window.reset_to_default()

    def set_from_config(self, config: dict, motions: list[dict]) -> None:
        widgets = [
            self.model_path_input,
            self.scale_spin,
            self.offset_x_spin,
            self.offset_y_spin,
            self.rotation_spin,
            self.opacity_spin,
            self.edit_mode_check,
            self.follow_mouse_check,
            self.win_x_spin,
            self.win_y_spin,
            self.win_w_spin,
            self.win_h_spin,
            self.lock_window_check,
            self.motion_combo,
            self.chat_voice_input,
            self.chat_tts_provider_combo,
            self.chat_tts_preset_combo,
            self.chat_tts_provider_url_input,
            self.chat_rate_slider,
            self.expression_mode_check,
            self.tooling_enabled_check,
            self.third_party_enabled_check,
            self.system_prompt_input,
        ]

        blockers = [QSignalBlocker(w) for w in widgets]
        _ = blockers

        self.model_path_input.setText(config.get("model_path", ""))
        self.chat_model_input.setText(config.get("chat", {}).get("model", DEFAULT_HERMES_MODEL))
        voice_text = str(config.get("chat", {}).get("voice", "zh-CN-XiaoxiaoNeural")).strip() or "zh-CN-XiaoxiaoNeural"
        self.chat_voice_input.setText(voice_text)
        provider = str(config.get("chat", {}).get("tts_provider", "edge_tts")).strip() or "edge_tts"
        provider = provider if provider in ("edge_tts", "custom_http") else "edge_tts"
        self.chat_tts_provider_combo.setCurrentText(provider)
        self.chat_tts_preset_combo.setCurrentIndex(0)
        self.chat_tts_provider_url_input.setText(str(config.get("chat", {}).get("tts_provider_url", "")).strip())
        try:
            rate_pct = int(config.get("chat", {}).get("rate_pct", 0))
        except Exception:
            rate_pct = 0
        rate_pct = max(-50, min(100, rate_pct))
        self.chat_rate_slider.setValue(rate_pct)
        self.chat_rate_value_label.setText(f"{rate_pct:+d}%")
        self.expression_mode_check.setChecked(bool(config.get("chat", {}).get("expression_mode", True)))
        self.expression_format_value_label.setText(str(config.get("chat", {}).get("expression_output_format", "ndjson_v1")))
        self.tooling_enabled_check.setChecked(bool(config.get("chat", {}).get("tooling", {}).get("enabled", True)))
        self.third_party_enabled_check.setChecked(
            bool(config.get("chat", {}).get("tooling", {}).get("third_party", {}).get("enabled", True))
        )
        self.system_prompt_input.setPlainText(config.get("chat", {}).get("system_prompt", ""))
        self.scale_spin.setValue(float(config["pet"]["scale"]))
        self.offset_x_spin.setValue(int(config["pet"]["offset_x"]))
        self.offset_y_spin.setValue(int(config["pet"]["offset_y"]))
        self.rotation_spin.setValue(float(config["pet"]["rotation"]))
        self.opacity_spin.setValue(float(config["pet"]["opacity"]))
        self.edit_mode_check.setChecked(bool(config["pet"]["edit_mode"]))
        self.follow_mouse_check.setChecked(bool(config["pet"]["follow_mouse"]))

        self.win_x_spin.setValue(int(config["window"]["x"]))
        self.win_y_spin.setValue(int(config["window"]["y"]))
        self.win_w_spin.setValue(int(config["window"]["width"]))
        self.win_h_spin.setValue(int(config["window"]["height"]))
        self.lock_window_check.setChecked(bool(config["window"]["locked"]))

        self.motion_combo.clear()
        for item in motions:
            self.motion_combo.addItem(item["label"], item)

    def update_pet_widgets_from_web_state(self, state: dict) -> None:
        widgets = [
            self.scale_spin,
            self.offset_x_spin,
            self.offset_y_spin,
            self.rotation_spin,
            self.opacity_spin,
        ]
        blockers = [QSignalBlocker(w) for w in widgets]
        _ = blockers

        if "scale" in state:
            self.scale_spin.setValue(float(state["scale"]))
        if "offset_x" in state:
            self.offset_x_spin.setValue(int(state["offset_x"]))
        if "offset_y" in state:
            self.offset_y_spin.setValue(int(state["offset_y"]))
        if "rotation" in state:
            self.rotation_spin.setValue(float(state["rotation"]))
        if "opacity" in state:
            self.opacity_spin.setValue(float(state["opacity"]))


def _qiodevice_write_only_mode():
    open_mode = getattr(QIODevice, "OpenModeFlag", None)
    if open_mode is not None:
        return open_mode.WriteOnly
    return QIODevice.WriteOnly


def _qt_smooth_transformation_mode():
    transform_mode = getattr(Qt, "TransformationMode", None)
    if transform_mode is not None:
        return transform_mode.SmoothTransformation
    return Qt.SmoothTransformation


VISION_CAPTURE_SCOPE = "visible_spaces_all_displays"


def _encode_pixmap_frame(pixmap, config: dict) -> tuple[str, str]:
    max_width = int(config.get("max_width", DEFAULT_VISION_CONFIG["max_width"]))
    try:
        if pixmap.width() > max_width:
            pixmap = pixmap.scaledToWidth(max_width, _qt_smooth_transformation_mode())
    except Exception:
        pass
    quality = int(config.get("jpeg_quality", DEFAULT_VISION_CONFIG["jpeg_quality"]))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(_qiodevice_write_only_mode())
    try:
        if not pixmap.save(buffer, "JPEG", quality):
            raise RuntimeError("failed to encode screen frame")
    finally:
        try:
            buffer.close()
        except Exception:
            pass
    encoded = base64.b64encode(bytes(data)).decode("ascii")
    return "image/jpeg", f"data:image/jpeg;base64,{encoded}"


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
) -> dict:
    frame_hash = _vision_hash_from_data_url(data_url)
    layout = list(display_layout or [])
    return {
        "mime_type": mime_type,
        "data_url": data_url,
        "frame_hash": frame_hash,
        "visual_hash": visual_hash or frame_hash,
        "capture_backend": capture_backend,
        "capture_scope": VISION_CAPTURE_SCOPE,
        "display_count": len(layout) if layout else 0,
        "display_layout": layout,
    }


def compose_qt_screens_pixmap(screens: list) -> tuple[object, dict]:
    if not screens:
        raise RuntimeError("no screens are available")
    layout = _screen_layout(screens)
    min_x = min(item["x"] for item in layout)
    min_y = min(item["y"] for item in layout)
    max_x = max(item["x"] + item["width"] for item in layout)
    max_y = max(item["y"] + item["height"] for item in layout)
    canvas = QPixmap(max(1, max_x - min_x), max(1, max_y - min_y))
    canvas.fill(QColor(0, 0, 0))
    painter = QPainter(canvas)
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
    pixmap_composer=compose_qt_screens_pixmap,
    pixmap_encoder=_encode_pixmap_frame,
) -> dict:
    provider = screens_provider or QGuiApplication.screens
    screens = list(provider() or [])
    if not screens and screen is not None:
        screens = [screen]
    pixmap, metadata = pixmap_composer(screens)
    mime_type, data_url = pixmap_encoder(pixmap, config)
    payload = _payload_from_encoded_frame(
        mime_type,
        data_url,
        capture_backend="qt_fallback",
        display_layout=metadata.get("display_layout") if isinstance(metadata, dict) else [],
        visual_hash=_visual_hash_from_pixmap(pixmap),
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
) -> dict:
    binary = Path("/usr/sbin/screencapture")
    if not binary.exists():
        raise RuntimeError("macOS screencapture is unavailable")
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="ipet-screen-", suffix=".jpg", delete=False) as tmp_file:
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
        image = QImage(tmp_path)
        if image.isNull():
            raise RuntimeError("screencapture produced an unreadable image")
        mime_type, data_url = _encode_pixmap_frame(image, config)
        if display_layout is not None:
            layout = [dict(item) for item in display_layout]
        else:
            provider = screens_provider or QGuiApplication.screens
            layout = _screen_layout(list(provider() or []))
        return _payload_from_encoded_frame(
            mime_type,
            data_url,
            capture_backend="macos_screencapture",
            display_layout=layout,
            visual_hash=_visual_hash_from_image_like(image),
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
    macos_capture=capture_macos_screencapture_payload,
    qt_capture=capture_qt_screen_frame_payload,
    allow_qt_fallback: bool = True,
    display_layout: list[dict] | None = None,
) -> dict:
    if _is_macos(platform_name):
        try:
            if macos_capture is capture_macos_screencapture_payload:
                return macos_capture(config, display_layout=display_layout)
            return macos_capture(config)
        except Exception:
            if not allow_qt_fallback:
                raise
    return qt_capture(screen, config)


def encode_screen_frame(screen, config: dict) -> tuple[str, str]:
    payload = capture_qt_screen_frame_payload(screen, config)
    return payload["mime_type"], payload["data_url"]


def _clean_vision_text(value, *, max_length: int) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = " ".join(text.split())
    return text[:max_length]


def collect_screen_observations(config: dict | None = None, *, platform_name: str | None = None, runner=subprocess.run) -> dict:
    vision_cfg = normalize_vision_config(config or {})
    if not vision_cfg.get("include_ui_metadata") or not _is_macos(platform_name):
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


ACTIVE_VISION_ALLOWED_ACTIONS = {"focus_target"}
ACTIVE_VISION_CLICK_POLICY = "window_focus_only"
ACTIVE_VISION_SURVEY_MODE = "desktop_survey"
ACTIVE_VISION_FOCUS_MODE = "focus_target"
ACTIVE_VISION_MIN_MAX_WIDTH = 1920
ACTIVE_VISION_MIN_JPEG_QUALITY = 88
ACTIVE_VISION_MAX_CANDIDATES = 12
ACTIVE_VISION_WINDOW_ENUMERATION_TIMEOUT_SEC = 3.0
ACTIVE_VISION_RUNNING_APP_FALLBACK_TIMEOUT_SEC = 2.5


def _osascript_args(script: str) -> list[str]:
    args = ["osascript"]
    for line in str(script or "").splitlines():
        stripped = line.rstrip()
        if stripped:
            args.extend(["-e", stripped])
    return args


def _active_target_id(app: str, title: str, bounds: dict[str, int], index: int) -> str:
    seed = "\x00".join(
        [
            _clean_vision_text(app, max_length=120),
            _clean_vision_text(title, max_length=200),
            str(bounds.get("x", 0)),
            str(bounds.get("y", 0)),
            str(bounds.get("width", 0)),
            str(bounds.get("height", 0)),
            str(index),
        ]
    )
    return "macos:" + hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _safe_focus_point(bounds: dict[str, int]) -> dict[str, int]:
    x = int(bounds.get("x") or 0)
    y = int(bounds.get("y") or 0)
    width = max(0, int(bounds.get("width") or 0))
    titlebar_y = y + 12
    focus_x = x + min(max(width // 2, 24), 180)
    return {"x": focus_x, "y": titlebar_y}


def _active_vision_capture_config(vision_cfg: dict) -> dict:
    config = dict(vision_cfg or {})
    config["max_width"] = max(int(config.get("max_width") or 0), ACTIVE_VISION_MIN_MAX_WIDTH)
    config["jpeg_quality"] = max(int(config.get("jpeg_quality") or 0), ACTIVE_VISION_MIN_JPEG_QUALITY)
    return config


def _parse_macos_desktop_targets(output: str) -> list[dict]:
    targets: list[dict] = []
    for index, line in enumerate(str(output or "").splitlines()):
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        app = _clean_vision_text(parts[0], max_length=120)
        title = _clean_vision_text(parts[1], max_length=200)
        try:
            x, y, width, height = [int(float(part or 0)) for part in parts[2:6]]
        except Exception:
            continue
        if not app or width < 80 or height < 40:
            continue
        frontmost = str(parts[6]).strip().lower() == "true"
        minimized = str(parts[7]).strip().lower() == "true"
        bounds = {"x": x, "y": y, "width": width, "height": height}
        targets.append(
            {
                "target_id": _active_target_id(app, title, bounds, index),
                "app": app,
                "title": title,
                "bounds": bounds,
                "frontmost": frontmost,
                "minimized": minimized,
                "focus_point": _safe_focus_point(bounds),
            }
        )
    return targets[:12]


def discover_macos_active_vision_desktop_targets(
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
) -> tuple[list[dict], list[str]]:
    if not _is_macos(platform_name):
        return [], []
    script = r"""
tell application "System Events"
    set windowRows to {}
    repeat with proc in application processes
        try
            if background only of proc is false then
                set appName to name of proc as text
                set isFront to frontmost of proc
                repeat with w in windows of proc
                    try
                        set wTitle to name of w as text
                        set wPos to position of w
                        set wSize to size of w
                        set wMinimized to false
                        try
                            set wMinimized to value of attribute "AXMinimized" of w
                        end try
                        set rowText to appName & tab & wTitle & tab & (item 1 of wPos as integer) & tab & (item 2 of wPos as integer) & tab & (item 1 of wSize as integer) & tab & (item 2 of wSize as integer) & tab & (isFront as text) & tab & (wMinimized as text)
                        set end of windowRows to rowText
                    end try
                end repeat
            end if
        end try
    end repeat
    set AppleScript's text item delimiters to linefeed
    set joinedRows to windowRows as text
    set AppleScript's text item delimiters to ""
    return joinedRows
end tell
""".strip()
    try:
        result = runner(
            _osascript_args(script),
            capture_output=True,
            text=True,
            timeout=ACTIVE_VISION_WINDOW_ENUMERATION_TIMEOUT_SEC,
            check=False,
        )
    except Exception as exc:
        return [], [f"System Events window enumeration failed: {exc}"]
    if getattr(result, "returncode", 1) != 0:
        detail = _clean_vision_text(getattr(result, "stderr", "") or getattr(result, "stdout", ""), max_length=180)
        return [], [f"System Events window enumeration failed: {detail or 'osascript failed'}"]
    return _parse_macos_desktop_targets(str(getattr(result, "stdout", "") or "")), []


def enumerate_active_vision_desktop_targets(
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
) -> list[dict]:
    targets, _errors = discover_macos_active_vision_desktop_targets(platform_name=platform_name, runner=runner)
    return targets


def _active_candidate_id(source: str, app: str, title: str, index: int) -> str:
    seed = "\x00".join([source, app, title, str(index)])
    return f"{source}:{hashlib.sha1(seed.encode('utf-8', errors='ignore')).hexdigest()[:14]}"


def _sanitize_active_candidate(candidate: dict, *, index: int = 0, source: str = "") -> dict:
    item = candidate if isinstance(candidate, dict) else {}
    candidate_source = _clean_vision_text(item.get("source") or source or "unknown", max_length=40)
    app = _clean_vision_text(item.get("app"), max_length=120)
    title = _clean_vision_text(item.get("title") or item.get("window_title") or app, max_length=200)
    target_id = _clean_vision_text(item.get("target_id") or item.get("candidate_id"), max_length=120)
    if not target_id:
        target_id = _active_candidate_id(candidate_source, app, title, index)
    bounds_source = item.get("bounds") if isinstance(item.get("bounds"), dict) else {}
    bounds = {
        "x": int(bounds_source.get("x") or 0),
        "y": int(bounds_source.get("y") or 0),
        "width": max(0, int(bounds_source.get("width") or 0)),
        "height": max(0, int(bounds_source.get("height") or 0)),
    }
    focus_point = item.get("focus_point") if isinstance(item.get("focus_point"), dict) else {}
    focusable = bool(item.get("focusable", candidate_source in {"window_enumeration", "running_app", "desktop_context"}))
    if candidate_source == "screenshot_region":
        focusable = False
    sanitized = {
        "target_id": target_id,
        "source": candidate_source,
        "app": app,
        "title": title,
        "bounds": bounds,
        "frontmost": bool(item.get("frontmost", False)),
        "minimized": bool(item.get("minimized", False)),
        "focus_point": dict(focus_point) if focus_point else {},
        "focusable": focusable,
        "score": float(item.get("score") or (90 if candidate_source == "window_enumeration" else 70 if focusable else 20)),
    }
    for key in ("bundle_id", "pid", "reason"):
        text = _clean_vision_text(item.get(key), max_length=160)
        if text:
            sanitized[key] = text
    return sanitized


def _active_candidates_from_desktop_targets(desktop_targets: list[dict]) -> list[dict]:
    candidates: list[dict] = []
    for index, target in enumerate(desktop_targets or []):
        if not isinstance(target, dict):
            continue
        candidates.append(
            _sanitize_active_candidate(
                {
                    **target,
                    "source": target.get("source") or "window_enumeration",
                    "focusable": not bool(target.get("minimized", False)),
                    "score": 90 if target.get("frontmost") else 85,
                },
                index=index,
                source="window_enumeration",
            )
        )
    return candidates


def _active_screenshot_candidate(display_layout: list[dict] | None = None) -> dict:
    bounds = {"x": 0, "y": 0, "width": 0, "height": 0}
    if display_layout:
        try:
            min_x = min(int(item.get("x") or 0) for item in display_layout)
            min_y = min(int(item.get("y") or 0) for item in display_layout)
            max_x = max(int(item.get("x") or 0) + int(item.get("width") or 0) for item in display_layout)
            max_y = max(int(item.get("y") or 0) + int(item.get("height") or 0) for item in display_layout)
            bounds = {"x": min_x, "y": min_y, "width": max(0, max_x - min_x), "height": max(0, max_y - min_y)}
        except Exception:
            bounds = {"x": 0, "y": 0, "width": 0, "height": 0}
    return _sanitize_active_candidate(
        {
            "target_id": "screenshot:full_desktop",
            "source": "screenshot_region",
            "title": "active full desktop screenshot",
            "bounds": bounds,
            "focusable": False,
            "score": 15,
        },
        source="screenshot_region",
    )


_ACTIVE_VISION_SYSTEM_PROCESS_NAMES = {
    "airportd",
    "backupd",
    "accessibilityuiserver",
    "accessoryupdaterd",
    "amfid",
    "cfprefsd",
    "controlcenter",
    "configd",
    "coreaudiod",
    "corelocationagent",
    "coreservicesuiagent",
    "corespeechd_system",
    "dasd",
    "diskarbitrationd",
    "distnoted",
    "dock",
    "duetexpertd",
    "endpointsecurityd",
    "fseventsd",
    "iomfb_bics_daemon",
    "keybagd",
    "kernel_task",
    "launchd",
    "liquiddetectiond",
    "logd",
    "loginwindow",
    "lsd",
    "mediaremoted",
    "mds",
    "mdworker",
    "notifyd",
    "osascript",
    "powerd",
    "reportcrash",
    "remoted",
    "runningboardd",
    "secd",
    "smd",
    "software update",
    "softwareupdated",
    "systemstats",
    "systemuiserver",
    "syslogd",
    "tccd",
    "trustd",
    "uarpassetmanagerd",
    "usereventagent",
    "usbmuxd",
    "wallpaperagent",
    "windowserver",
    "windowmanager",
    "xprotect",
}

_ACTIVE_VISION_LOW_PRIORITY_APP_NAMES = {
    "activity monitor",
    "codex",
    "console",
    "finder",
    "system settings",
    "terminal",
}


def _is_active_vision_system_process_name(name: str) -> bool:
    cleaned = _clean_vision_text(name, max_length=120)
    if not cleaned or cleaned.startswith("."):
        return True
    return cleaned.lower() in _ACTIVE_VISION_SYSTEM_PROCESS_NAMES


def _active_vision_name_key(name: str) -> str:
    return _clean_vision_text(name, max_length=120).casefold()


def _app_bundle_name_from_process_path(process_path: str) -> str:
    raw_path = str(process_path or "").strip()
    if not raw_path:
        return ""
    for part in Path(raw_path).parts:
        if part.lower().endswith(".app") and len(part) > 4:
            return _clean_vision_text(part[:-4], max_length=120)
    return ""


def _running_app_candidate_score(name: str, *, frontmost: bool = False, from_process_path: bool = False) -> float:
    if _active_vision_name_key(name) in _ACTIVE_VISION_LOW_PRIORITY_APP_NAMES:
        return 45.0 if frontmost else 35.0
    if frontmost:
        return 75.0
    return 65.0 if from_process_path else 70.0


def _parse_system_events_running_app_candidates(output: str, *, start_index: int = 0) -> list[dict]:
    candidates: list[dict] = []
    for line in str(output or "").splitlines():
        parts = line.split("\t")
        name = _clean_vision_text(parts[0] if parts else "", max_length=120)
        if _is_active_vision_system_process_name(name):
            continue
        bundle_id = _clean_vision_text(parts[1] if len(parts) > 1 else "", max_length=160)
        pid = _clean_vision_text(parts[2] if len(parts) > 2 else "", max_length=80)
        frontmost = str(parts[3] if len(parts) > 3 else "").strip().lower() == "true"
        candidate = {
            "target_id": _active_candidate_id("running_app", bundle_id or name, name, start_index + len(candidates)),
            "source": "running_app",
            "app": name,
            "title": name,
            "frontmost": frontmost,
            "focusable": True,
            "score": _running_app_candidate_score(name, frontmost=frontmost),
        }
        if bundle_id:
            candidate["bundle_id"] = bundle_id
        if pid:
            candidate["pid"] = pid
        candidates.append(_sanitize_active_candidate(candidate, index=start_index + len(candidates), source="running_app"))
        if len(candidates) >= ACTIVE_VISION_MAX_CANDIDATES:
            break
    return candidates


def _system_events_running_app_candidates(*, runner=subprocess.run) -> tuple[list[dict], list[str]]:
    script = r"""
tell application "System Events"
    set appRows to {}
    repeat with proc in (application processes whose background only is false)
        try
            set appName to name of proc as text
            set bundleId to ""
            set unixId to ""
            set isFront to frontmost of proc
            try
                set bundleId to bundle identifier of proc as text
            end try
            try
                set unixId to unix id of proc as text
            end try
            set rowText to appName & tab & bundleId & tab & unixId & tab & (isFront as text)
            set end of appRows to rowText
        end try
    end repeat
    set AppleScript's text item delimiters to linefeed
    set joinedRows to appRows as text
    set AppleScript's text item delimiters to ""
    return joinedRows
end tell
""".strip()
    try:
        result = runner(
            _osascript_args(script),
            capture_output=True,
            text=True,
            timeout=ACTIVE_VISION_RUNNING_APP_FALLBACK_TIMEOUT_SEC,
            check=False,
        )
    except Exception as exc:
        return [], [f"System Events running app fallback failed: {exc}"]
    if getattr(result, "returncode", 1) != 0:
        detail = _clean_vision_text(getattr(result, "stderr", "") or getattr(result, "stdout", ""), max_length=180)
        return [], [f"System Events running app fallback failed: {detail or 'osascript failed'}"]
    return _parse_system_events_running_app_candidates(str(getattr(result, "stdout", "") or "")), []


def enumerate_active_vision_running_app_candidates(
    *,
    platform_name: str | None = None,
    runner=subprocess.run,
    include_errors: bool = False,
) -> list[dict] | tuple[list[dict], list[str]]:
    if not _is_macos(platform_name):
        return ([], []) if include_errors else []
    candidates: list[dict] = []
    errors: list[str] = []
    try:
        import AppKit  # type: ignore

        workspace = AppKit.NSWorkspace.sharedWorkspace()
        for index, app in enumerate(list(workspace.runningApplications() or [])):
            try:
                name = _clean_vision_text(app.localizedName(), max_length=120)
                bundle_id = _clean_vision_text(app.bundleIdentifier(), max_length=160)
                pid = str(int(app.processIdentifier()))
                frontmost = bool(app.isActive())
                hidden = bool(app.isHidden())
                activation_policy = int(app.activationPolicy())
            except Exception:
                continue
            if not name or hidden or activation_policy != 0:
                continue
            candidates.append(
                _sanitize_active_candidate(
                    {
                        "target_id": _active_candidate_id("running_app", bundle_id or name, name, index),
                        "source": "running_app",
                        "app": name,
                        "title": name,
                        "bundle_id": bundle_id,
                        "pid": pid,
                        "frontmost": frontmost,
                        "focusable": True,
                        "score": _running_app_candidate_score(name, frontmost=frontmost),
                    },
                    index=index,
                    source="running_app",
                )
            )
            if len(candidates) >= ACTIVE_VISION_MAX_CANDIDATES:
                return (candidates, errors) if include_errors else candidates
    except Exception as exc:
        errors.append(f"AppKit running app enumeration unavailable: {exc}")
    if not candidates:
        system_events_candidates, system_events_errors = _system_events_running_app_candidates(runner=runner)
        errors.extend(system_events_errors)
        if system_events_candidates:
            return (system_events_candidates[:ACTIVE_VISION_MAX_CANDIDATES], errors) if include_errors else system_events_candidates[
                :ACTIVE_VISION_MAX_CANDIDATES
            ]
    try:
        result = runner(["/bin/ps", "-axo", "comm="], capture_output=True, text=True, timeout=0.8, check=False)
    except Exception:
        return (candidates[:ACTIVE_VISION_MAX_CANDIDATES], errors) if include_errors else candidates[:ACTIVE_VISION_MAX_CANDIDATES]
    if getattr(result, "returncode", 1) != 0:
        detail = _clean_vision_text(getattr(result, "stderr", "") or getattr(result, "stdout", ""), max_length=180)
        errors.append(f"running process fallback failed: {detail or 'ps failed'}")
        return (candidates[:ACTIVE_VISION_MAX_CANDIDATES], errors) if include_errors else candidates[:ACTIVE_VISION_MAX_CANDIDATES]
    seen: set[str] = {_active_vision_name_key(str(item.get("app") or "")) for item in candidates}
    ps_lines = str(getattr(result, "stdout", "") or "").splitlines()
    app_bundle_candidates: list[dict] = []
    for line in ps_lines:
        app_name = _app_bundle_name_from_process_path(line)
        key = _active_vision_name_key(app_name)
        if not app_name or key in seen:
            continue
        if _is_active_vision_system_process_name(app_name):
            continue
        seen.add(key)
        app_bundle_candidates.append(
            _sanitize_active_candidate(
                {
                    "target_id": _active_candidate_id("running_app", app_name, app_name, len(candidates) + len(app_bundle_candidates)),
                    "source": "running_app",
                    "app": app_name,
                    "title": app_name,
                    "focusable": True,
                    "score": _running_app_candidate_score(app_name, from_process_path=True),
                },
                source="running_app",
            )
        )
        if len(candidates) + len(app_bundle_candidates) >= ACTIVE_VISION_MAX_CANDIDATES:
            break
    if app_bundle_candidates:
        combined = sorted(
            candidates + app_bundle_candidates,
            key=lambda value: float(value.get("score") or 0.0),
            reverse=True,
        )[:ACTIVE_VISION_MAX_CANDIDATES]
        return (combined, errors) if include_errors else combined
    for line in str(getattr(result, "stdout", "") or "").splitlines():
        name = Path(line.strip()).name
        key = _active_vision_name_key(name)
        if not name or key in seen or name.startswith("."):
            continue
        if _is_active_vision_system_process_name(name):
            continue
        seen.add(key)
        candidates.append(
            _sanitize_active_candidate(
                {
                    "target_id": _active_candidate_id("running_process", name, name, len(candidates)),
                    "source": "running_process",
                    "app": name,
                    "title": name,
                    "focusable": False,
                    "score": 25,
                },
                source="running_process",
            )
        )
        if len(candidates) >= ACTIVE_VISION_MAX_CANDIDATES:
            break
    return (candidates[:ACTIVE_VISION_MAX_CANDIDATES], errors) if include_errors else candidates[:ACTIVE_VISION_MAX_CANDIDATES]


def _merge_active_target_candidates(*candidate_groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen_ids: set[str] = set()
    for group in candidate_groups:
        for index, item in enumerate(group or []):
            if not isinstance(item, dict):
                continue
            candidate = _sanitize_active_candidate(item, index=index)
            target_id = str(candidate.get("target_id") or "")
            if not target_id or target_id in seen_ids:
                continue
            seen_ids.add(target_id)
            merged.append(candidate)
            if len(merged) >= ACTIVE_VISION_MAX_CANDIDATES:
                return sorted(merged, key=lambda value: float(value.get("score") or 0.0), reverse=True)
    return sorted(merged, key=lambda value: float(value.get("score") or 0.0), reverse=True)[:ACTIVE_VISION_MAX_CANDIDATES]


def discover_active_vision_target_candidates(
    *,
    desktop_targets: list[dict] | None = None,
    desktop_targets_provider=enumerate_active_vision_desktop_targets,
    running_apps_provider=enumerate_active_vision_running_app_candidates,
    target_candidates: list[dict] | None = None,
    display_layout: list[dict] | None = None,
    platform_name: str | None = None,
) -> dict:
    discovery_errors: list[str] = []
    discovered_targets = list(desktop_targets or [])
    if not discovered_targets and desktop_targets_provider:
        try:
            if desktop_targets_provider is enumerate_active_vision_desktop_targets:
                discovered_targets, errors = discover_macos_active_vision_desktop_targets(platform_name=platform_name or sys.platform)
                discovery_errors.extend(errors)
            else:
                raw_targets = desktop_targets_provider(platform_name=platform_name or sys.platform)
                if isinstance(raw_targets, dict):
                    discovered_targets = list(raw_targets.get("desktop_targets") or [])
                    discovery_errors.extend(str(item) for item in raw_targets.get("discovery_errors") or [])
                    target_candidates = list(target_candidates or []) + list(raw_targets.get("target_candidates") or [])
                else:
                    discovered_targets = list(raw_targets or [])
        except TypeError:
            try:
                discovered_targets = list(desktop_targets_provider() or [])
            except Exception as exc:
                discovery_errors.append(f"desktop target discovery failed: {exc}")
        except Exception as exc:
            discovery_errors.append(str(exc))
    running_candidates: list[dict] = []
    if running_apps_provider:
        try:
            if running_apps_provider is enumerate_active_vision_running_app_candidates:
                running_result = running_apps_provider(platform_name=platform_name or sys.platform, include_errors=True)
                if isinstance(running_result, tuple):
                    running_candidates = list(running_result[0] or [])
                    discovery_errors.extend(str(item) for item in (running_result[1] or []))
                else:
                    running_candidates = list(running_result or [])
            else:
                running_candidates = list(running_apps_provider(platform_name=platform_name or sys.platform) or [])
        except TypeError:
            try:
                running_candidates = list(running_apps_provider() or [])
            except Exception as exc:
                discovery_errors.append(f"running app fallback failed: {exc}")
        except Exception as exc:
            discovery_errors.append(f"running app fallback failed: {exc}")
    candidates = _merge_active_target_candidates(
        _active_candidates_from_desktop_targets(discovered_targets),
        list(target_candidates or []),
        running_candidates,
        [_active_screenshot_candidate(display_layout)],
    )
    return {
        "desktop_targets": discovered_targets[:12],
        "target_candidates": candidates,
        "discovery_errors": [_clean_vision_text(item, max_length=180) for item in discovery_errors if str(item or "").strip()][:6],
    }


def _find_desktop_target(desktop_targets: list[dict] | tuple[dict, ...] | None, target_id: str) -> dict:
    wanted = _clean_vision_text(target_id, max_length=120)
    for target in desktop_targets or []:
        if not isinstance(target, dict):
            continue
        if _clean_vision_text(target.get("target_id"), max_length=120) == wanted:
            return dict(target)
    return {}


def _find_active_target(
    desktop_targets: list[dict] | tuple[dict, ...] | None,
    target_candidates: list[dict] | tuple[dict, ...] | None,
    target_id: str,
) -> dict:
    target = _find_desktop_target(desktop_targets, target_id)
    if target:
        target.setdefault("source", "window_enumeration")
        target.setdefault("focusable", True)
        return target
    wanted = _clean_vision_text(target_id, max_length=120)
    for candidate in target_candidates or []:
        if not isinstance(candidate, dict):
            continue
        if _clean_vision_text(candidate.get("target_id"), max_length=120) == wanted:
            return dict(candidate)
    return {}


def _active_vision_focus_script(target: dict, click_policy: str) -> str:
    if click_policy != ACTIVE_VISION_CLICK_POLICY:
        return ""
    app = json.dumps(_clean_vision_text(target.get("app"), max_length=120))
    title = json.dumps(_clean_vision_text(target.get("title"), max_length=200))
    focus_point = target.get("focus_point") if isinstance(target.get("focus_point"), dict) else {}
    try:
        click_x = int(focus_point.get("x"))
        click_y = int(focus_point.get("y"))
    except Exception:
        return ""
    # This path is intentionally limited to window focus: Accessibility raise
    # plus one System Events click at the surveyed title-bar/window-top point.
    return f"""
tell application "System Events"
    set targetApp to {app}
    set targetTitle to {title}
    set accessibilityRaiseStatus to "unknown"
    set windowFocusClickStatus to "unknown"
    if targetApp is "" then return "accessibility_raise=unknown:missing_app" & linefeed & "window_focus_click=unknown:missing_app"
    if not (exists application process targetApp) then return "accessibility_raise=unknown:app_not_found" & linefeed & "window_focus_click=unknown:app_not_found"
    tell application process targetApp
        set frontmost to true
        try
            if targetTitle is not "" then
                perform action "AXRaise" of first window whose name is targetTitle
            else
                perform action "AXRaise" of first window
            end if
            set accessibilityRaiseStatus to "success"
        on error errMsg
            set accessibilityRaiseStatus to "unknown:" & errMsg
        end try
    end tell
    try
        click at {{{click_x}, {click_y}}}
        set windowFocusClickStatus to "success"
    on error errMsg
        set windowFocusClickStatus to "unknown:" & errMsg
    end try
    return "accessibility_raise=" & accessibilityRaiseStatus & linefeed & "window_focus_click=" & windowFocusClickStatus
end tell
""".strip()


def _parse_active_vision_focus_result(output: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for line in str(output or "").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in {"accessibility_raise", "window_focus_click"}:
            statuses[key] = _clean_vision_text(value, max_length=160) or "unknown"
    return statuses


def _activate_running_app_with_open(target: dict, *, runner=subprocess.run) -> tuple[bool, str]:
    app_name = _clean_vision_text(target.get("app") or target.get("title"), max_length=120)
    if not app_name:
        return False, "running app activation unavailable: missing app name"
    try:
        result = runner(["/usr/bin/open", "-a", app_name], capture_output=True, text=True, timeout=1.2, check=False)
    except Exception as exc:
        return False, f"open -a activation failed: {exc}"
    if getattr(result, "returncode", 1) == 0:
        return True, "success"
    detail = _clean_vision_text(getattr(result, "stderr", "") or getattr(result, "stdout", ""), max_length=160)
    return False, f"open -a activation failed: {detail or 'open returned non-zero exit'}"


def _activate_running_app_candidate(target: dict, *, runner=subprocess.run) -> tuple[bool, str]:
    native_detail = ""
    try:
        import AppKit  # type: ignore

        app = None
        pid_text = str(target.get("pid") or "").strip()
        if pid_text:
            try:
                app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid_text))
            except Exception:
                app = None
        bundle_id = str(target.get("bundle_id") or "").strip()
        if app is None and bundle_id:
            matches = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
            app = list(matches or [None])[0]
        if app is None:
            native_detail = "native running app activation unavailable"
        else:
            options = getattr(AppKit, "NSApplicationActivateIgnoringOtherApps", 1)
            ok = bool(app.activateWithOptions_(options))
            if ok:
                return True, "success"
            native_detail = "native running app activation returned false"
    except Exception as exc:
        native_detail = f"native running app activation unavailable: {exc}"
    fallback_ok, fallback_detail = _activate_running_app_with_open(target, runner=runner)
    if fallback_ok:
        return True, fallback_detail
    if native_detail:
        return False, f"{native_detail}; {fallback_detail}"
    return False, fallback_detail


def _can_activate_app_level_candidate(target: dict) -> bool:
    if target.get("focus_point"):
        return False
    if not bool(target.get("focusable")):
        return False
    return bool(_clean_vision_text(target.get("app") or target.get("title"), max_length=120))


def run_active_vision_light_interaction(
    *,
    mode: str = ACTIVE_VISION_SURVEY_MODE,
    target_id: str = "",
    target_hint: str = "",
    desktop_targets: list[dict] | tuple[dict, ...] | None = None,
    target_candidates: list[dict] | tuple[dict, ...] | None = None,
    click_policy: str = ACTIVE_VISION_CLICK_POLICY,
    actions: list[str] | tuple[str, ...] | None = None,
    platform_name: str | None = None,
    runner=subprocess.run,
) -> dict:
    requested = [str(item or "").strip() for item in (actions or []) if str(item or "").strip()]
    normalized_mode = _clean_vision_text(mode, max_length=40) or ACTIVE_VISION_SURVEY_MODE
    if normalized_mode != ACTIVE_VISION_FOCUS_MODE:
        requested = []
    allowed = [action for action in requested if action in ACTIVE_VISION_ALLOWED_ACTIONS]
    blocked = [action for action in requested if action not in ACTIVE_VISION_ALLOWED_ACTIONS]
    target = _find_active_target(desktop_targets, target_candidates, target_id)
    trace = {
        "status": "success",
        "mode": normalized_mode,
        "target_id": _clean_vision_text(target_id, max_length=120),
        "target_hint": _clean_vision_text(target_hint, max_length=80),
        "click_policy": ACTIVE_VISION_CLICK_POLICY,
        "desktop_targets": list(desktop_targets or [])[:12],
        "target_candidates": list(target_candidates or [])[:12],
        "selected_candidate": dict(target) if target else {},
        "focused_target": {},
        "click_point": {},
        "action_trace": [],
        "actions": [],
        "blocked_actions": blocked,
        "unknowns": [],
        "focus_result": {},
    }
    if normalized_mode == ACTIVE_VISION_SURVEY_MODE:
        return trace
    if click_policy and click_policy != ACTIVE_VISION_CLICK_POLICY:
        trace["blocked_actions"].append(f"click_policy:{_clean_vision_text(click_policy, max_length=40)}")
    if not _is_macos(platform_name):
        trace["unknowns"].append("active vision light interaction is only implemented on macOS")
        return trace
    if not target:
        trace["status"] = "error"
        trace["unknowns"].append("active vision target not found")
        trace["focus_result"] = {"status": "error", "method": "target_lookup", "reason": "active vision target not found"}
        return trace
    for action in allowed:
        if action == ACTIVE_VISION_FOCUS_MODE and _can_activate_app_level_candidate(target):
            ok, detail = _activate_running_app_candidate(target, runner=runner)
            entry = {
                "action": "activate_running_app",
                "status": "success" if ok else "unknown",
                "target_id": trace["target_id"],
                "app": _clean_vision_text(target.get("app"), max_length=120),
                "title": _clean_vision_text(target.get("title"), max_length=200),
            }
            if not ok:
                entry["detail"] = detail
                trace["unknowns"].append(f"activate_running_app failed: {detail}")
            trace["action_trace"].append(entry)
            trace["focus_result"] = {
                "status": "success" if ok else "error",
                "method": "activate_running_app",
                "reason": "" if ok else detail,
            }
            if ok:
                trace["actions"].append(action)
                trace["focused_target"] = dict(target)
            continue
        script = _active_vision_focus_script(target, click_policy) if action == ACTIVE_VISION_FOCUS_MODE else ""
        if not script:
            trace["blocked_actions"].append(action)
            continue
        try:
            result = runner(["osascript", "-e", script], capture_output=True, text=True, timeout=1.2, check=False)
        except Exception as exc:
            trace["unknowns"].append(f"{action} failed: {exc}")
            continue
        if getattr(result, "returncode", 1) == 0:
            step_statuses = _parse_active_vision_focus_result(getattr(result, "stdout", ""))
            focus_point = target.get("focus_point") if isinstance(target.get("focus_point"), dict) else {}
            app_name = _clean_vision_text(target.get("app"), max_length=120)
            title = _clean_vision_text(target.get("title"), max_length=200)
            for step in ("accessibility_raise", "window_focus_click"):
                status = step_statuses.get(step) or "unknown"
                entry = {
                    "action": step,
                    "status": "success" if status == "success" else "unknown",
                    "target_id": trace["target_id"],
                    "app": app_name,
                    "title": title,
                }
                if step == "window_focus_click" and status == "success":
                    entry["click_policy"] = ACTIVE_VISION_CLICK_POLICY
                    entry["click_point"] = dict(focus_point)
                    trace["click_point"] = dict(focus_point)
                if status != "success":
                    entry["detail"] = status
                    trace["unknowns"].append(f"{step} failed: {status}")
                trace["action_trace"].append(entry)
            if step_statuses.get("accessibility_raise") == "success" and step_statuses.get("window_focus_click") == "success":
                trace["actions"].append(action)
                trace["focused_target"] = dict(target)
                trace["focus_result"] = {"status": "success", "method": ACTIVE_VISION_CLICK_POLICY}
        else:
            detail = _clean_vision_text(getattr(result, "stderr", ""), max_length=120)
            trace["unknowns"].append(f"{action} failed: {detail or 'osascript failed'}")
    if trace["unknowns"]:
        trace["status"] = "partial" if trace["actions"] else "error"
        if not trace.get("focus_result"):
            trace["focus_result"] = {
                "status": trace["status"],
                "method": ACTIVE_VISION_CLICK_POLICY,
                "reason": "; ".join(trace["unknowns"][:3]),
            }
    elif trace["actions"] and not trace.get("focus_result"):
        trace["focus_result"] = {"status": "success", "method": ACTIVE_VISION_CLICK_POLICY}
    return trace


def _runtime_inline_frame_from_payload(frame_payload: dict, *, purpose: str = "main") -> dict:
    mime_type = str(frame_payload.get("mime_type") or "").strip().lower()
    data_url = str(frame_payload.get("data_url") or "").strip()
    if mime_type not in {"image/jpeg", "image/png"} or not data_url.startswith("data:image/"):
        return {}
    inline = {
        "mime_type": mime_type,
        "data_url": data_url,
        "frame_id": _clean_vision_text(frame_payload.get("frame_id"), max_length=80),
        "frame_hash": _clean_vision_text(frame_payload.get("frame_hash"), max_length=120),
        "purpose": _clean_vision_text(purpose, max_length=40),
    }
    return {key: value for key, value in inline.items() if value}


def _decode_frame_image(data_url: str):
    try:
        header, encoded = str(data_url or "").split(",", 1)
    except ValueError:
        return None
    if not header.startswith("data:image/"):
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception:
        return None
    image = QImage()
    try:
        if not image.loadFromData(raw):
            return None
    except Exception:
        return None
    return image


def _display_union_bounds(display_layout: list[dict] | None) -> dict[str, int]:
    layout = display_layout if isinstance(display_layout, list) else []
    if not layout:
        return {"x": 0, "y": 0, "width": 0, "height": 0}
    try:
        min_x = min(int(item.get("x") or 0) for item in layout if isinstance(item, dict))
        min_y = min(int(item.get("y") or 0) for item in layout if isinstance(item, dict))
        max_x = max(int(item.get("x") or 0) + int(item.get("width") or 0) for item in layout if isinstance(item, dict))
        max_y = max(int(item.get("y") or 0) + int(item.get("height") or 0) for item in layout if isinstance(item, dict))
    except Exception:
        return {"x": 0, "y": 0, "width": 0, "height": 0}
    return {"x": min_x, "y": min_y, "width": max(0, max_x - min_x), "height": max(0, max_y - min_y)}


def _active_detail_frames_from_capture(frame_payload: dict, selected_candidate: dict, config: dict) -> list[dict]:
    bounds = selected_candidate.get("bounds") if isinstance(selected_candidate.get("bounds"), dict) else {}
    width = int(bounds.get("width") or 0)
    height = int(bounds.get("height") or 0)
    if width < 80 or height < 40:
        return []
    image = _decode_frame_image(str(frame_payload.get("data_url") or ""))
    if image is None or image.isNull():
        return []
    display_bounds = _display_union_bounds(frame_payload.get("display_layout") if isinstance(frame_payload.get("display_layout"), list) else None)
    if display_bounds["width"] <= 0 or display_bounds["height"] <= 0:
        display_bounds = {"x": 0, "y": 0, "width": image.width(), "height": image.height()}
    scale_x = image.width() / max(1, display_bounds["width"])
    scale_y = image.height() / max(1, display_bounds["height"])
    pad_x = max(20, int(width * 0.06))
    pad_y = max(20, int(height * 0.06))
    crop_x = int((int(bounds.get("x") or 0) - display_bounds["x"] - pad_x) * scale_x)
    crop_y = int((int(bounds.get("y") or 0) - display_bounds["y"] - pad_y) * scale_y)
    crop_w = int((width + pad_x * 2) * scale_x)
    crop_h = int((height + pad_y * 2) * scale_y)
    crop_x = max(0, min(image.width() - 1, crop_x))
    crop_y = max(0, min(image.height() - 1, crop_y))
    crop_w = max(1, min(image.width() - crop_x, crop_w))
    crop_h = max(1, min(image.height() - crop_y, crop_h))
    try:
        crop = image.copy(crop_x, crop_y, crop_w, crop_h)
        mime_type, data_url = _encode_pixmap_frame(crop, config)
    except Exception:
        return []
    frame_hash = _vision_hash_from_data_url(data_url)
    detail_payload = {
        "mime_type": mime_type,
        "data_url": data_url,
        "frame_id": f"{_clean_vision_text(frame_payload.get('frame_id'), max_length=60) or 'active'}-detail-1",
        "frame_hash": frame_hash,
        "purpose": "detail_crop",
        "source_frame_id": _clean_vision_text(frame_payload.get("frame_id"), max_length=80),
        "target_id": _clean_vision_text(selected_candidate.get("target_id"), max_length=120),
        "crop_bounds": {"x": crop_x, "y": crop_y, "width": crop_w, "height": crop_h},
    }
    return [detail_payload]


def capture_active_vision_frame_payload(
    window,
    command_payload: dict,
    config: dict,
    *,
    screen_provider=None,
    frame_encoder=capture_screen_frame_payload,
    observation_provider=collect_screen_observations,
    desktop_targets_provider=enumerate_active_vision_desktop_targets,
    running_apps_provider=enumerate_active_vision_running_app_candidates,
    interaction_runner=run_active_vision_light_interaction,
    sleeper=time.sleep,
    platform_name: str | None = None,
) -> dict:
    vision_cfg = normalize_vision_config(config or {})
    active_capture_cfg = _active_vision_capture_config(vision_cfg)
    active_cfg = normalize_active_observation_config(vision_cfg.get("active_observation", {}))
    payload = command_payload if isinstance(command_payload, dict) else {}
    mode = _clean_vision_text(payload.get("mode") or ACTIVE_VISION_SURVEY_MODE, max_length=40)
    if mode not in {ACTIVE_VISION_SURVEY_MODE, ACTIVE_VISION_FOCUS_MODE}:
        mode = ACTIVE_VISION_SURVEY_MODE
    target_id = _clean_vision_text(payload.get("target_id"), max_length=120)
    target_hint = _clean_vision_text(payload.get("target_hint"), max_length=80)
    requested_actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    if mode == ACTIVE_VISION_FOCUS_MODE and not requested_actions:
        requested_actions = [ACTIVE_VISION_FOCUS_MODE]
    if mode == ACTIVE_VISION_SURVEY_MODE:
        requested_actions = []
    if active_cfg.get("allowed_interaction") == "none":
        requested_actions = []
    click_policy = _clean_vision_text(payload.get("click_policy") or ACTIVE_VISION_CLICK_POLICY, max_length=80)
    settle_ms = int(payload.get("settle_ms") or active_cfg["settle_ms"])
    desktop_targets = payload.get("desktop_targets") if isinstance(payload.get("desktop_targets"), list) else []
    target_candidates = payload.get("target_candidates") if isinstance(payload.get("target_candidates"), list) else []
    discovery = discover_active_vision_target_candidates(
        desktop_targets=desktop_targets,
        desktop_targets_provider=desktop_targets_provider,
        running_apps_provider=running_apps_provider,
        target_candidates=target_candidates,
        platform_name=platform_name or sys.platform,
    )
    desktop_targets = discovery["desktop_targets"]
    target_candidates = discovery["target_candidates"]
    discovery_errors = discovery["discovery_errors"]
    was_visible = False
    try:
        was_visible = bool(window.isVisible()) if hasattr(window, "isVisible") else False
    except Exception:
        was_visible = False
    trace = {
        "enabled": True,
        "status": "success",
        "mode": mode,
        "target_id": target_id or (ACTIVE_VISION_SURVEY_MODE if mode == ACTIVE_VISION_SURVEY_MODE else ""),
        "target_hint": target_hint,
        "click_policy": ACTIVE_VISION_CLICK_POLICY,
        "desktop_targets": list(desktop_targets or [])[:12],
        "target_candidates": list(target_candidates or [])[:12],
        "discovery_errors": list(discovery_errors or [])[:6],
        "focused_target": {},
        "selected_candidate": {},
        "action_trace": [],
        "click_point": {},
        "actions": [],
        "blocked_actions": [],
        "unknowns": [],
        "focus_result": {},
        "verify_result": {},
        "detail_frames_count": 0,
    }
    try:
        if was_visible and hasattr(window, "hide"):
            window.hide()
        if mode == ACTIVE_VISION_FOCUS_MODE:
            interaction_trace = interaction_runner(
                mode=mode,
                target_id=target_id,
                target_hint=target_hint,
                desktop_targets=desktop_targets,
                target_candidates=target_candidates,
                click_policy=click_policy,
                actions=requested_actions,
                platform_name=platform_name or sys.platform,
            )
            if isinstance(interaction_trace, dict):
                trace.update({key: value for key, value in interaction_trace.items() if key != "data_url"})
        try:
            sleeper(max(0.0, min(2.0, settle_ms / 1000.0)))
        except Exception:
            pass
        provider = screen_provider or QGuiApplication.primaryScreen
        screen = provider()
        if screen is None:
            raise RuntimeError("primary screen is unavailable")
        if frame_encoder is capture_screen_frame_payload and _is_macos(platform_name):
            frame_payload = frame_encoder(screen, active_capture_cfg, allow_qt_fallback=False)
        else:
            frame_payload = frame_encoder(screen, active_capture_cfg)
        frame_payload = ScreenVisionController._payload_from_frame_payload(frame_payload)
        if not target_candidates:
            discovery = discover_active_vision_target_candidates(
                desktop_targets=desktop_targets,
                desktop_targets_provider=None,
                running_apps_provider=None,
                target_candidates=[],
                display_layout=frame_payload.get("display_layout") if isinstance(frame_payload.get("display_layout"), list) else None,
                platform_name=platform_name or sys.platform,
            )
            target_candidates = discovery["target_candidates"]
            trace["target_candidates"] = list(target_candidates or [])[:12]
        selected_candidate = trace.get("selected_candidate") if isinstance(trace.get("selected_candidate"), dict) else {}
        if not selected_candidate and target_id:
            selected_candidate = _find_active_target(desktop_targets, target_candidates, target_id)
            trace["selected_candidate"] = dict(selected_candidate) if selected_candidate else {}
        detail_frames = _active_detail_frames_from_capture(frame_payload, selected_candidate, active_capture_cfg) if selected_candidate else []
        runtime_frames = [_runtime_inline_frame_from_payload(frame_payload, purpose="main")]
        runtime_frames.extend(_runtime_inline_frame_from_payload(item, purpose="detail_crop") for item in detail_frames)
        runtime_frames = [item for item in runtime_frames if item]
        if runtime_frames:
            frame_payload["vision_frames"] = runtime_frames[:3]
        trace["detail_frames_count"] = len(detail_frames)
        trace["verify_result"] = {
            "status": "captured",
            "method": str(frame_payload.get("capture_backend") or "screen_capture")[:80],
            "frame_hash": str(frame_payload.get("frame_hash") or "")[:120],
        }
        try:
            observation_payload = observation_provider(vision_cfg, platform_name=platform_name) if observation_provider else {}
        except TypeError:
            observation_payload = observation_provider(vision_cfg) if observation_provider else {}
        except Exception as exc:
            observation_payload = {"unknowns": [f"active vision metadata observation failed: {exc}"]}
        if isinstance(observation_payload, dict):
            for key in ("unknowns", "desktop_context", "foreground_app", "window_title"):
                if observation_payload.get(key) and not frame_payload.get(key):
                    frame_payload[key] = observation_payload[key]
        frame_payload["active_observation"] = trace
        return frame_payload
    except Exception:
        trace["status"] = "error"
        raise
    finally:
        if was_visible and hasattr(window, "show"):
            try:
                window.show()
            except Exception:
                pass
            if hasattr(window, "raise_"):
                try:
                    window.raise_()
                except Exception:
                    pass


class ScreenVisionController:
    def __init__(
        self,
        parent=None,
        *,
        timer_factory=QTimer,
        screen_provider=None,
        frame_encoder=capture_screen_frame_payload,
        observation_provider=collect_screen_observations,
        task_runner=None,
        post_frame=None,
        background_capture: bool | None = None,
    ) -> None:
        self._timer = timer_factory(parent)
        self._timer.timeout.connect(self.capture_once)
        self._screen_provider = screen_provider or QGuiApplication.primaryScreen
        self._frame_encoder = frame_encoder
        self._observation_provider = observation_provider
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
        mime_type, data_url = frame_payload
        return {"mime_type": mime_type, "data_url": data_url}

    @staticmethod
    def _capture_display_layout() -> list[dict]:
        try:
            return _screen_layout(list(QGuiApplication.screens() or []))
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


class DesktopPet(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config = load_config()
        self._config_mtime = self._config_mtime_token()
        self._runtime_command_mtime = self._runtime_command_mtime_token()
        self._last_runtime_command_nonce = ""
        self.drag_offset = QPoint()
        self.window_locked = bool(self.config["window"]["locked"])
        self.motion_items: list[dict] = []
        self.expression_names: list[str] = []
        self.lipsync_meta: dict = {"gain": 1.0, "mouth_open_ids": [], "mouth_form_ids": []}
        self.runtime_model_path: Path | None = None
        self.backend_process: subprocess.Popen | None = None
        self.backend_started_by_app = False
        self.hermes_process: subprocess.Popen | None = None
        self.hermes_started_by_app = False
        self.runtime_processes: dict[str, subprocess.Popen] = {}
        self.runtime_started_by_app: dict[str, bool] = {}
        self.asr_process: subprocess.Popen | None = None
        self.asr_started_by_app = False
        self._asr_warmup_monitor_lock = threading.Lock()
        self._asr_warmup_monitor_thread: threading.Thread | None = None
        self._shutdown_in_progress = False
        self.control_panel = None
        self.settings_window = None
        self._settings_window_url = ""
        self.resize_margin = 8
        self._window_dragging = False
        self._window_resizing = False
        self._resize_edges: tuple[bool, bool, bool, bool] = (False, False, False, False)  # left, top, right, bottom
        self._drag_start_global = QPoint()
        self._drag_start_geometry = self.geometry()
        self._python_event_filters_installed = False
        self.vision_controller = ScreenVisionController(self)

        self.bridge = PetBridge()
        self.bridge.stateChanged.connect(self.on_web_state_changed)
        self.bridge.openSettingsRequested.connect(self.open_settings_page)
        self.bridge.startAsrWarmupRequested.connect(self.request_asr_warmup)
        self.bridge.minimizeWindowRequested.connect(self.showMinimized)
        self.bridge.closeWindowRequested.connect(self.close)

        self.browser = QWebEngineView(self)
        self.browser.setMouseTracking(True)
        self.browser.page().setBackgroundColor(QColor(*_desktop_pet_background_color()))

        settings = self.browser.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, _should_enable_webgl())
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, False)

        self.browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.browser.customContextMenuRequested.connect(self.show_context_menu)
        if _should_install_python_event_filters():
            self.browser.installEventFilter(self)
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self)
            self._python_event_filters_installed = True

        if hasattr(self.browser.page(), "featurePermissionRequested"):
            self.browser.page().featurePermissionRequested.connect(self.on_feature_permission_requested)

        self.channel = QWebChannel(self.browser.page())
        self.channel.registerObject("qtBridge", self.bridge)
        self.browser.page().setWebChannel(self.channel)

        self.setCentralWidget(self.browser)

        self.setWindowFlags(_desktop_pet_window_flags())
        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            _should_use_translucent_window(),
        )

        self.apply_window_geometry_from_config()

        self.refresh_motion_list(prefer_reset=False)
        self.ensure_active_runtime_sidecar()
        self.ensure_backend_service()
        self.ensure_asr_service()
        self.vision_controller.apply_config(self.config)

        self.browser.loadFinished.connect(self.on_web_loaded)
        self.browser.setUrl(QUrl.fromLocalFile(str((ROOT_DIR / "index.html").resolve())))

        self._config_poll_timer = QTimer(self)
        self._config_poll_timer.timeout.connect(self.on_config_poll)
        self._config_poll_timer.start(1000)
        self._write_runtime_host_heartbeat()

    def _feature_permission_policy(self, granted: bool):
        policy_enum = getattr(QWebEnginePage, "PermissionPolicy", None)
        if policy_enum is None:
            return None
        name = "PermissionGrantedByUser" if granted else "PermissionDeniedByUser"
        return getattr(policy_enum, name, None)

    def _is_local_security_origin(self, security_origin) -> bool:
        try:
            if security_origin.isLocalFile():
                return True
        except Exception:
            pass
        try:
            return str(security_origin.scheme() or "").lower() == "file"
        except Exception:
            return False

    def on_feature_permission_requested(self, security_origin, feature) -> None:
        page = self.browser.page()
        grant_policy = self._feature_permission_policy(True)
        deny_policy = self._feature_permission_policy(False)
        if grant_policy is None or deny_policy is None:
            return
        audio_feature = getattr(getattr(QWebEnginePage, "Feature", object), "MediaAudioCapture", None)
        video_feature = getattr(getattr(QWebEnginePage, "Feature", object), "MediaVideoCapture", None)
        av_feature = getattr(getattr(QWebEnginePage, "Feature", object), "MediaAudioVideoCapture", None)
        if feature == audio_feature and self._is_local_security_origin(security_origin):
            page.setFeaturePermission(security_origin, feature, grant_policy)
            return
        if feature in {audio_feature, video_feature, av_feature}:
            page.setFeaturePermission(security_origin, feature, deny_policy)

    def apply_window_geometry_from_config(self) -> None:
        geom = self.config["window"]
        self.setGeometry(
            int(geom["x"]),
            int(geom["y"]),
            int(geom["width"]),
            int(geom["height"]),
        )

    def sync_panel(self) -> None:
        if self.control_panel is not None:
            self.control_panel.set_from_config(self.config, self.motion_items)

    def refresh_motion_list(self, prefer_reset: bool) -> None:
        model_path_text = (
            self.control_panel.model_path_input.text().strip()
            if self.control_panel is not None
            else self.config.get("model_path", "")
        )
        model_path = resolve_model_path(model_path_text or self.config.get("model_path", ""))
        self.runtime_model_path, motion_groups, expr_defs = ensure_runtime_model(model_path)
        self.lipsync_meta = {"gain": 1.0, "mouth_open_ids": [], "mouth_form_ids": []}
        if model_path.exists():
            try:
                with model_path.open("r", encoding="utf-8") as f:
                    model_json = json.load(f)
                self.lipsync_meta = extract_lipsync_meta(model_json)
            except Exception:
                pass
        self.motion_items = action_items_from_defs(motion_groups, expr_defs)
        self.expression_names = [
            str(item.get("Name") or "").strip()
            for item in expr_defs
            if isinstance(item, dict) and str(item.get("Name") or "").strip()
        ]
        if not self.motion_items:
            self.motion_items = [{"type": "motion", "group": "Idle", "index": 0, "label": "Idle[0]"}]

        if self.control_panel is not None:
            self.sync_panel()
            if prefer_reset and self.control_panel.motion_combo.count() > 0:
                self.control_panel.motion_combo.setCurrentIndex(0)

    def _config_mtime_token(self):
        try:
            stat = CONFIG_PATH.stat()
        except Exception:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _runtime_command_mtime_token(self):
        try:
            stat = RUNTIME_COMMAND_PATH.stat()
        except Exception:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _write_runtime_host_heartbeat(self) -> None:
        payload = {
            "pid": os.getpid(),
            "timestamp_ns": time.time_ns(),
        }
        try:
            RUNTIME_HOST_HEARTBEAT_PATH.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _response_path_for_command(self, command: dict) -> Path:
        payload = command.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        response_path_text = str(
            payload.get("response_path")
            or command.get("response_path")
            or RUNTIME_COMMAND_RESPONSE_PATH
        ).strip()
        path = Path(response_path_text)
        if not path.is_absolute():
            path = ROOT_DIR / path
        try:
            return path.resolve()
        except Exception:
            return path

    def _write_runtime_command_response(self, command: dict, status: str, result: dict[str, object] | None = None) -> None:
        nonce = str(command.get("nonce") or "").strip()
        if not nonce:
            return
        payload = command.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        response = {
            "nonce": nonce,
            "type": str(command.get("type") or "").strip(),
            "status": status,
            "ok": status == "success",
            "result": result or {},
            "timestamp_ns": time.time_ns(),
        }
        if status == "error" and not response["result"]:
            response["result"] = {"error": "runtime command failed"}
        response_path = self._response_path_for_command(command)
        try:
            response_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = response_path.with_name(f"{response_path.name}.tmp")
            tmp_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(response_path)
        except Exception as exc:
            print(f"写入 runtime command 响应失败: {exc}")
        finally:
            self._write_runtime_host_heartbeat()

    def on_config_poll(self) -> None:
        self._write_runtime_host_heartbeat()
        token = self._config_mtime_token()
        if token is not None and token != self._config_mtime:
            self._config_mtime = token
            self.reload_config_from_disk()
        self.on_runtime_command_poll()

    def on_runtime_command_poll(self) -> None:
        token = self._runtime_command_mtime_token()
        if token is None or token == self._runtime_command_mtime:
            return
        self._runtime_command_mtime = token
        try:
            command = json.loads(RUNTIME_COMMAND_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(command, dict):
            return
        nonce = str(command.get("nonce") or "").strip()
        if not nonce:
            return
        if nonce == self._last_runtime_command_nonce:
            return
        self._last_runtime_command_nonce = nonce
        self._write_runtime_host_heartbeat()
        self.process_runtime_command(command)

    def process_runtime_command(self, command: dict) -> None:
        command_type = str(command.get("type") or "").strip()
        payload = command.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        model_path = str(payload.get("model_path") or "").strip()
        if model_path:
            normalized = normalize_model_path(model_path)
            if normalized:
                self.config["model_path"] = normalized
                self.refresh_motion_list(prefer_reset=False)

        if command_type == "load_model":
            self.apply_config_to_web()
            self._write_runtime_command_response(command, "success", {"model_path": self.config.get("model_path", "")})
            return

        if command_type == "play_motion":
            group = str(payload.get("group") or "").strip()
            try:
                index = max(0, int(payload.get("index", 0)))
            except Exception:
                index = 0
            if group:
                self.play_motion(group, index, reload_model=bool(model_path))
                self._write_runtime_command_response(
                    command,
                    "success",
                    {"group": group, "index": index, "model_path": self.config.get("model_path", "")},
                )
            else:
                self._write_runtime_command_response(command, "error", {"error": "group is required"})
            return

        if command_type == "play_expression":
            name = str(payload.get("name") or "").strip()
            if name:
                self.play_expression(name, reload_model=bool(model_path))
                self._write_runtime_command_response(
                    command,
                    "success",
                    {"name": name, "model_path": self.config.get("model_path", "")},
                )
            else:
                self._write_runtime_command_response(command, "error", {"error": "name is required"})
            return

        if command_type == "pick_directory":
            start_dir = _resolve_runtime_directory_seed(str(payload.get("start_dir") or ""))
            try:
                self.raise_()
                self.activateWindow()
                selected = QFileDialog.getExistingDirectory(self, "选择目录", start_dir)
                if selected:
                    self._write_runtime_command_response(
                        command,
                        "success",
                        {"directory": selected, "start_dir": start_dir},
                    )
                else:
                    self._write_runtime_command_response(
                        command,
                        "cancelled",
                        {"directory": "", "start_dir": start_dir},
                    )
            except Exception as exc:
                self._write_runtime_command_response(
                    command,
                    "error",
                    {"error": str(exc), "start_dir": start_dir},
                )
            self._write_runtime_host_heartbeat()
            return

        if command_type == "pick_image_file":
            start_path = str(payload.get("start_path") or payload.get("start_dir") or "").strip()
            seed_path = resolve_background_image_path(start_path) if start_path else ROOT_DIR
            start_dir = str(seed_path.parent if seed_path.exists() and seed_path.is_file() else seed_path)
            try:
                self.raise_()
                self.activateWindow()
                selected, _ = QFileDialog.getOpenFileName(
                    self,
                    "选择背景图片",
                    start_dir,
                    "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All Files (*)",
                )
                if selected:
                    self._write_runtime_command_response(
                        command,
                        "success",
                        {"path": selected, "start_path": start_path},
                    )
                else:
                    self._write_runtime_command_response(
                        command,
                        "cancelled",
                        {"path": "", "start_path": start_path},
                    )
            except Exception as exc:
                self._write_runtime_command_response(
                    command,
                    "error",
                    {"error": str(exc), "start_path": start_path},
                )
            self._write_runtime_host_heartbeat()
            return

        if command_type == "active_vision_capture":
            try:
                frame = capture_active_vision_frame_payload(self, payload, self.config.get("vision", {}))
                trace = frame.get("active_observation") if isinstance(frame.get("active_observation"), dict) else {}
                self._write_runtime_command_response(command, "success", {"frame": frame, "trace": trace})
            except Exception as exc:
                self._write_runtime_command_response(
                    command,
                    "error",
                    {
                        "error": str(exc),
                        "trace": {
                            "status": "error",
                            "target_hint": _clean_vision_text(payload.get("target_hint"), max_length=80),
                            "actions": [],
                            "unknowns": [str(exc)],
                        },
                    },
                )
            self._write_runtime_host_heartbeat()
            return

        self._write_runtime_command_response(command, "error", {"error": f"unsupported command: {command_type}"})
        self._write_runtime_host_heartbeat()

    def reload_config_from_disk(self) -> None:
        self.config = load_config()
        self.window_locked = bool(self.config["window"]["locked"])
        self.apply_window_geometry_from_config()
        self.refresh_motion_list(prefer_reset=False)
        self.ensure_active_runtime_sidecar()
        asr_cfg = self.config.get("chat", {}).get("asr", {}) if isinstance(self.config.get("chat", {}), dict) else {}
        if isinstance(asr_cfg, dict) and bool(asr_cfg.get("enabled", True)):
            self.ensure_asr_service()
        else:
            self.stop_asr_service()
        self.vision_controller.apply_config(self.config)
        self.apply_config_to_web()

    def _settings_page_url(self) -> str:
        chat_cfg = self.config.get("chat", {}) if isinstance(self.config, dict) else {}
        if not isinstance(chat_cfg, dict):
            chat_cfg = {}
        backend_url = str(chat_cfg.get("backend_url", DEFAULT_BACKEND_URL)).strip() or DEFAULT_BACKEND_URL
        return f"{backend_url.rstrip('/')}/settings"

    def _clear_settings_window(self, window=None) -> None:
        if window is None or getattr(self, "settings_window", None) is window:
            self.settings_window = None
            self._settings_window_url = ""

    def _create_settings_window(self, url: str):
        window = QWebEngineView()
        window.setWindowTitle("Ipet 设置")
        window.resize(1180, 760)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        window.page().setBackgroundColor(QColor(18, 18, 18, 255))

        settings = window.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, _should_enable_webgl())
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, False)

        try:
            window.destroyed.connect(lambda *_args, settings_window=window: self._clear_settings_window(settings_window))
        except Exception:
            pass
        return window

    def _settings_window_is_usable(self, window) -> bool:
        if window is None:
            return False
        try:
            window.isVisible()
        except Exception:
            return False
        return True

    def _navigate_settings_window(self, window, url: str) -> None:
        if self._settings_window_url == url:
            return
        window.setUrl(QUrl(url))
        self._settings_window_url = url

    def _show_or_focus_settings_window(self, url: str) -> None:
        window = getattr(self, "settings_window", None)
        if not self._settings_window_is_usable(window):
            window = self._create_settings_window(url)
            self.settings_window = window
            self._settings_window_url = ""

        self._navigate_settings_window(window, url)
        if not window.isVisible():
            window.show()
        window.raise_()
        window.activateWindow()

    def open_settings_page(self) -> None:
        self.ensure_backend_service()
        url = self._settings_page_url()
        try:
            self._show_or_focus_settings_window(url)
        except Exception as exc:
            print(f"打开设置页失败: {exc}")

    def show_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        panel_action = QAction("打开设置页", self)
        lock_action = QAction("解锁窗口" if self.window_locked else "锁定窗口", self)
        reload_action = QAction("重新加载模型", self)
        quit_action = QAction("退出", self)

        menu.addAction(panel_action)
        menu.addAction(lock_action)
        menu.addAction(reload_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        selected = menu.exec(self.browser.mapToGlobal(pos))
        if selected == panel_action:
            self.open_settings_page()
        elif selected == lock_action:
            self.window_locked = not self.window_locked
            self.config["window"]["locked"] = self.window_locked
        elif selected == reload_action:
            self.apply_config_to_web()
        elif selected == quit_action:
            self.close()

    def ensure_backend_service(self) -> None:
        chat_cfg = self.config.get("chat", {})
        backend_url = str(chat_cfg.get("backend_url", DEFAULT_BACKEND_URL)).strip() or DEFAULT_BACKEND_URL
        self.config.setdefault("chat", {})["backend_url"] = backend_url
        if is_backend_healthy(backend_url):
            return

        if is_local_service_url(backend_url):
            launch_url = pick_backend_launch_url(backend_url)
            if launch_url != backend_url:
                self.config.setdefault("chat", {})["backend_url"] = launch_url
                backend_url = launch_url
            if is_backend_healthy(backend_url):
                return

        host, port = parse_service_host_port(backend_url, default_port=8008)
        backend_python = resolve_backend_python()
        cmd = [
            backend_python,
            "-m",
            "uvicorn",
            "backend.app:app",
            "--host",
            host,
            "--port",
            str(port),
            "--log-level",
            "warning",
        ]
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        backend_log_path = _truncate_runtime_log(_runtime_log_path("backend"))
        try:
            env = os.environ.copy()
            env[LOCAL_API_TOKEN_ENV] = LOCAL_API_TOKEN
            with backend_log_path.open("a", encoding="utf-8") as backend_log:
                self.backend_process = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT_DIR),
                    stdout=backend_log,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                    env=env,
                )
            self.backend_started_by_app = True
        except Exception as exc:
            print(f"failed to start backend with {backend_python}: {exc}")
            return

        for _ in range(60):
            if is_backend_live(backend_url):
                return
            if self.backend_process and self.backend_process.poll() is not None:
                detail = _tail_runtime_log(backend_log_path)
                if detail:
                    print(f"backend exited early with code {self.backend_process.returncode}:\n{detail}")
                else:
                    print(f"backend exited early with code {self.backend_process.returncode}.")
                return
            time.sleep(0.25)
        detail = _tail_runtime_log(backend_log_path)
        if detail:
            print(f"backend did not become ready in time; chat may be unavailable.\n{detail}")
        else:
            print("backend did not become ready in time; chat may be unavailable.")

    def _hermes_health_url(self, hermes_cfg: dict) -> str:
        return local_service_health_url(hermes_cfg)

    def is_hermes_healthy(self, hermes_cfg: dict) -> bool:
        return self.is_runtime_healthy(hermes_cfg)

    def is_runtime_healthy(self, runtime_cfg: dict) -> bool:
        if not isinstance(runtime_cfg, dict) or not bool(runtime_cfg.get("enabled", False)):
            return False
        try:
            resp = requests.get(local_service_health_url(runtime_cfg), timeout=2)
            return 200 <= resp.status_code < 300
        except Exception:
            return False

    def ensure_hermes_sidecar(self) -> None:
        self.ensure_active_runtime_sidecar()

    def ensure_active_runtime_sidecar(self) -> None:
        mirror_runtime_compat(self.config, root_dir=ROOT_DIR)
        runtime_id, runtime_cfg = runtime_sidecar_config(self.config)
        if not bool(runtime_cfg.get("enabled", False)):
            self.stop_runtime_sidecar(runtime_id)
            return
        if self.is_runtime_healthy(runtime_cfg):
            return
        if not bool(runtime_cfg.get("auto_start", False)):
            print(f"{_runtime_display_name(runtime_id)} is enabled but not reachable; auto_start is disabled.")
            return
        command = list(runtime_cfg.get("command") or [])
        if not command:
            if runtime_id == RUNTIME_HERMES:
                print(_hermes_missing_command_message(runtime_cfg, root_dir=ROOT_DIR))
            else:
                print(_runtime_missing_command_message(runtime_id, runtime_cfg, root_dir=ROOT_DIR))
            return
        current = self.runtime_processes.get(runtime_id)
        if current is not None and current.poll() is None:
            return

        cwd = str(runtime_cfg.get("cwd") or ROOT_DIR).strip() or str(ROOT_DIR)
        if not Path(cwd).exists():
            print(f"failed to start {_runtime_display_name(runtime_id)} sidecar: cwd does not exist: {cwd}")
            return
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        runtime_log_path = _truncate_runtime_log(_runtime_log_path(runtime_id))
        try:
            with runtime_log_path.open("a", encoding="utf-8") as runtime_log:
                proc = subprocess.Popen(
                    command,
                    cwd=cwd,
                    stdout=runtime_log,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
            self.runtime_processes[runtime_id] = proc
            self.runtime_started_by_app[runtime_id] = True
            if runtime_id == RUNTIME_HERMES:
                self.hermes_process = proc
                self.hermes_started_by_app = True
        except Exception as exc:
            print(f"failed to start {_runtime_display_name(runtime_id)} sidecar: {exc}")
            return

        deadline = time.monotonic() + float(runtime_cfg.get("startup_timeout_sec") or 20)
        while time.monotonic() < deadline:
            if self.is_runtime_healthy(runtime_cfg):
                return
            proc = self.runtime_processes.get(runtime_id)
            if proc and proc.poll() is not None:
                detail = _tail_runtime_log(runtime_log_path)
                if detail:
                    print(f"{_runtime_display_name(runtime_id)} exited early with code {proc.returncode}:\n{detail}")
                else:
                    print(f"{_runtime_display_name(runtime_id)} exited early with code {proc.returncode}.")
                return
            time.sleep(0.25)
        detail = _tail_runtime_log(runtime_log_path)
        if detail:
            print(f"{_runtime_display_name(runtime_id)} did not become ready in time; chat may be unavailable.\n{detail}")
        else:
            print(f"{_runtime_display_name(runtime_id)} did not become ready in time; chat may be unavailable.")

    def ensure_asr_service(self) -> None:
        chat_cfg = self.config.get("chat", {})
        asr_cfg = chat_cfg.get("asr", {}) if isinstance(chat_cfg, dict) else {}
        if not isinstance(asr_cfg, dict) or not bool(asr_cfg.get("enabled", True)):
            return
        backend_url = str(chat_cfg.get("backend_url") or DEFAULT_BACKEND_URL).strip() or DEFAULT_BACKEND_URL
        if is_local_service_url(backend_url):
            self.config.setdefault("chat", {}).setdefault("asr", {})["api_base_url"] = backend_url
            return
        asr_url = str(asr_cfg.get("api_base_url") or DEFAULT_ASR_API_BASE_URL).strip() or DEFAULT_ASR_API_BASE_URL
        self.config.setdefault("chat", {}).setdefault("asr", {})["api_base_url"] = asr_url
        if is_asr_healthy(asr_url):
            return

        host, port = parse_service_host_port(asr_url, default_port=8012)
        asr_python = resolve_asr_python()
        cmd = [
            asr_python,
            "-m",
            "uvicorn",
            "backend.asr_server:app",
            "--host",
            host,
            "--port",
            str(port),
            "--log-level",
            "warning",
        ]
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        asr_log_path = _truncate_runtime_log(_runtime_log_path("asr"))
        try:
            with asr_log_path.open("a", encoding="utf-8") as asr_log:
                self.asr_process = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT_DIR),
                    stdout=asr_log,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
            self.asr_started_by_app = True
        except Exception as exc:
            print(f"启动 ASR 服务失败: {exc}")
            return

        for _ in range(40):
            if is_asr_healthy(asr_url):
                return
            if self.asr_process and self.asr_process.poll() is not None:
                detail = _tail_runtime_log(asr_log_path)
                if detail:
                    print(f"ASR 服务提前退出，退出码 {self.asr_process.returncode}:\n{detail}")
                else:
                    print(f"ASR 服务提前退出，退出码 {self.asr_process.returncode}。")
                return
            time.sleep(0.2)
        detail = _tail_runtime_log(asr_log_path)
        if detail:
            print(f"ASR 服务未在预期时间内就绪，语音输入可能不可用。\n{detail}")
        else:
            print("ASR 服务未在预期时间内就绪，语音输入可能不可用。")

    def request_asr_warmup(self) -> None:
        chat_cfg = self.config.get("chat", {})
        asr_cfg = chat_cfg.get("asr", {}) if isinstance(chat_cfg, dict) else {}
        if not isinstance(asr_cfg, dict) or not bool(asr_cfg.get("enabled", True)):
            print("[ASR] ASR 已禁用，跳过初始化。")
            return
        self.ensure_backend_service()
        self.ensure_asr_service()
        backend_url = str(self.config.get("chat", {}).get("backend_url") or DEFAULT_BACKEND_URL).strip() or DEFAULT_BACKEND_URL
        warmup_base_url = backend_url.rstrip("/")
        try:
            resp = requests.post(f"{warmup_base_url}/api/asr/warmup", timeout=3)
            payload = resp.json() if resp.headers.get("content-type", "").lower().startswith("application/json") else {}
        except Exception as exc:
            print(f"[ASR] 初始化请求失败: {exc}")
            return

        ready = bool(payload.get("ready"))
        started = bool(payload.get("started"))
        message = str(payload.get("message") or "").strip()
        if ready:
            print("[ASR] 初始化已完成，此时按住 Ctrl 可以正常使用。")
            return
        if started:
            print("[ASR] 已开始初始化语音模型，松开 Ctrl 也不会中断。")
        elif message:
            print(f"[ASR] {message}")
        self._start_asr_warmup_progress_monitor(warmup_base_url)

    def _start_asr_warmup_progress_monitor(self, base_url: str) -> None:
        with self._asr_warmup_monitor_lock:
            if self._asr_warmup_monitor_thread is not None and self._asr_warmup_monitor_thread.is_alive():
                return
            self._asr_warmup_monitor_thread = threading.Thread(
                target=self._run_asr_warmup_progress_monitor,
                args=(str(base_url or "").rstrip("/"),),
                daemon=True,
            )
            self._asr_warmup_monitor_thread.start()

    def _run_asr_warmup_progress_monitor(self, base_url: str) -> None:
        started_at = time.perf_counter()
        bar_width = 24
        print("[ASR] 正在初始化本地语音模型，首次加载通常需要约 1 分钟。")
        while True:
            elapsed = time.perf_counter() - started_at
            ready = False
            message = "等待 ASR 后端响应..."
            try:
                resp = requests.get(f"{base_url}/api/health", timeout=2)
                payload = resp.json() if resp.headers.get("content-type", "").lower().startswith("application/json") else {}
                ready = bool(payload.get("asr"))
                message = str(payload.get("message") or ("ASR 已就绪" if ready else "ASR 正在初始化...")).strip() or "ASR 正在初始化..."
            except Exception as exc:
                message = f"等待 ASR 后端响应: {exc.__class__.__name__}"

            progress = 1.0 if ready else min(0.95, max(0.05, elapsed / 60.0 * 0.9))
            filled = max(1, int(bar_width * progress)) if not ready else bar_width
            bar = "#" * filled + "-" * max(0, bar_width - filled)
            line = f"\r[ASR] [{bar}] {int(progress * 100):>3}% {int(elapsed):>3}s {message[:48]}"
            sys.stdout.write(line.ljust(96))
            sys.stdout.flush()
            if ready:
                sys.stdout.write("\n[ASR] 初始化完成，此时按住 Ctrl 可以正常使用。\n")
                sys.stdout.flush()
                return
            if elapsed >= 180:
                sys.stdout.write("\n[ASR] 初始化超过 180 秒仍未完成，请稍后再按住 Ctrl 重试。\n")
                sys.stdout.flush()
                return
            time.sleep(1.0)

    def _stop_managed_process(self, proc: subprocess.Popen | None, *, started_by_app: bool) -> None:
        if not proc or not started_by_app:
            return
        if proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                proc.wait(timeout=2)
            else:
                proc.terminate()
                proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass

    def stop_backend_service(self) -> None:
        proc = self.backend_process
        if not proc or not self.backend_started_by_app:
            return
        if proc.poll() is not None:
            self.backend_process = None
            self.backend_started_by_app = False
            return
        try:
            self._stop_managed_process(proc, started_by_app=True)
        finally:
            self.backend_process = None
            self.backend_started_by_app = False

    def stop_asr_service(self) -> None:
        proc = self.asr_process
        if not proc or not self.asr_started_by_app:
            return
        if proc.poll() is not None:
            self.asr_process = None
            self.asr_started_by_app = False
            return
        try:
            self._stop_managed_process(proc, started_by_app=True)
        finally:
            self.asr_process = None
            self.asr_started_by_app = False

    def stop_hermes_sidecar(self) -> None:
        self.stop_runtime_sidecar(RUNTIME_HERMES)

    def stop_runtime_sidecar(self, runtime_id: str) -> None:
        proc = self.runtime_processes.get(runtime_id)
        if runtime_id == RUNTIME_HERMES and proc is None:
            proc = self.hermes_process
        started_by_app = bool(self.runtime_started_by_app.get(runtime_id))
        if runtime_id == RUNTIME_HERMES:
            started_by_app = started_by_app or self.hermes_started_by_app
        if not proc or not started_by_app:
            return
        if proc.poll() is not None:
            self.runtime_processes.pop(runtime_id, None)
            self.runtime_started_by_app[runtime_id] = False
            if runtime_id == RUNTIME_HERMES:
                self.hermes_process = None
                self.hermes_started_by_app = False
            return
        try:
            self._stop_managed_process(proc, started_by_app=True)
        finally:
            self.runtime_processes.pop(runtime_id, None)
            self.runtime_started_by_app[runtime_id] = False
            if runtime_id == RUNTIME_HERMES:
                self.hermes_process = None
                self.hermes_started_by_app = False

    def stop_all_runtime_sidecars(self) -> None:
        for runtime_id in list(self.runtime_processes):
            self.stop_runtime_sidecar(runtime_id)
        if self.hermes_process is not None:
            self.stop_runtime_sidecar(RUNTIME_HERMES)

    def shutdown_runtime(self) -> None:
        if self._shutdown_in_progress:
            return
        self._shutdown_in_progress = True

        self.vision_controller.stop()

        try:
            if hasattr(self, "_config_poll_timer") and self._config_poll_timer.isActive():
                self._config_poll_timer.stop()
        except Exception:
            pass

        try:
            app = QApplication.instance()
            if app is not None and self._python_event_filters_installed:
                app.removeEventFilter(self)
        except Exception:
            pass

        try:
            if self.control_panel is not None:
                self.control_panel.close()
                self.control_panel.deleteLater()
                self.control_panel = None
        except Exception:
            pass

        try:
            self.browser.loadFinished.disconnect(self.on_web_loaded)
        except Exception:
            pass

        try:
            self.browser.stop()
        except Exception:
            pass

        try:
            if self._python_event_filters_installed:
                self.browser.removeEventFilter(self)
        except Exception:
            pass

        try:
            page = self.browser.page()
            if page is not None:
                page.setWebChannel(None)
        except Exception:
            pass

        try:
            self.browser.close()
            self.browser.deleteLater()
        except Exception:
            pass

        try:
            self.channel.deleteLater()
        except Exception:
            pass

        self.stop_asr_service()
        self.stop_all_runtime_sidecars()
        self.stop_backend_service()

    def eventFilter(self, watched, event):
        def _is_browser_related(obj: QObject) -> bool:
            if obj is self.browser:
                return True
            if hasattr(obj, "parent"):
                p = obj.parent()
                while p is not None:
                    if p is self.browser:
                        return True
                    p = p.parent() if hasattr(p, "parent") else None
            return False

        def _event_pos_in_browser(ev):
            if not hasattr(ev, "position"):
                return QPoint(0, 0)
            local = ev.position().toPoint()
            if watched is self.browser:
                return local
            if hasattr(watched, "mapTo"):
                try:
                    return watched.mapTo(self.browser, local)
                except Exception:
                    return local
            return local

        def _global_point(ev):
            return ev.globalPosition().toPoint()

        def _local_point(ev):
            return _event_pos_in_browser(ev)

        def _hit_edges(p: QPoint) -> tuple[bool, bool, bool, bool]:
            rect = self.rect()
            left = p.x() <= self.resize_margin
            right = p.x() >= rect.width() - self.resize_margin
            top = p.y() <= self.resize_margin
            bottom = p.y() >= rect.height() - self.resize_margin
            return (left, top, right, bottom)

        def _hit_titlebar_button_exclusion(p: QPoint) -> bool:
            if p.y() > 40:
                return False
            return p.x() >= max(0, self.width() - 96)

        def _cursor_from_edges(edges: tuple[bool, bool, bool, bool]):
            left, top, right, bottom = edges
            if (left and top) or (right and bottom):
                return Qt.CursorShape.SizeFDiagCursor
            if (right and top) or (left and bottom):
                return Qt.CursorShape.SizeBDiagCursor
            if left or right:
                return Qt.CursorShape.SizeHorCursor
            if top or bottom:
                return Qt.CursorShape.SizeVerCursor
            return Qt.CursorShape.ArrowCursor

        def _apply_resize(edges: tuple[bool, bool, bool, bool], delta: QPoint) -> None:
            left, top, right, bottom = edges
            g = self._drag_start_geometry
            min_w = max(self.minimumWidth(), 120)
            min_h = max(self.minimumHeight(), 120)

            new_left = g.left()
            new_top = g.top()
            new_right = g.right()
            new_bottom = g.bottom()

            if left:
                new_left = g.left() + delta.x()
                if new_right - new_left + 1 < min_w:
                    new_left = new_right - min_w + 1
            if right:
                new_right = g.right() + delta.x()
                if new_right - new_left + 1 < min_w:
                    new_right = new_left + min_w - 1
            if top:
                new_top = g.top() + delta.y()
                if new_bottom - new_top + 1 < min_h:
                    new_top = new_bottom - min_h + 1
            if bottom:
                new_bottom = g.bottom() + delta.y()
                if new_bottom - new_top + 1 < min_h:
                    new_bottom = new_top + min_h - 1

            self.setGeometry(new_left, new_top, new_right - new_left + 1, new_bottom - new_top + 1)

        if _is_browser_related(watched):
            edit_mode = bool(self.config.get("pet", {}).get("edit_mode", False))

            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                if edit_mode:
                    local = _local_point(event)
                    self._drag_start_global = _global_point(event)
                    self._drag_start_geometry = self.geometry()
                    self._resize_edges = _hit_edges(local)
                    self._window_resizing = any(self._resize_edges)
                    move_zone = local.y() <= 40
                    self._window_dragging = (not self._window_resizing) and move_zone and not _hit_titlebar_button_exclusion(local)
                    if self._window_resizing:
                        self.browser.setCursor(_cursor_from_edges(self._resize_edges))
                        return True
                    if self._window_dragging:
                        self.browser.setCursor(Qt.CursorShape.ClosedHandCursor)
                        return True

                alt_pressed = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier)
                if alt_pressed and not self.window_locked:
                    self.drag_offset = _global_point(event) - self.frameGeometry().topLeft()
                    return True

            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                if self._window_dragging or self._window_resizing:
                    self._window_dragging = False
                    self._window_resizing = False
                    self._resize_edges = (False, False, False, False)
                    self.browser.setCursor(Qt.CursorShape.ArrowCursor if not edit_mode else Qt.CursorShape.OpenHandCursor)
                    self.config["window"]["x"] = self.x()
                    self.config["window"]["y"] = self.y()
                    self.config["window"]["width"] = self.width()
                    self.config["window"]["height"] = self.height()
                    self.sync_panel()
                    return True

            if event.type() == QEvent.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton:
                if edit_mode and (self._window_dragging or self._window_resizing):
                    gp = _global_point(event)
                    delta = gp - self._drag_start_global
                    if self._window_resizing:
                        _apply_resize(self._resize_edges, delta)
                    else:
                        self.move(self._drag_start_geometry.topLeft() + delta)

                    self.config["window"]["x"] = self.x()
                    self.config["window"]["y"] = self.y()
                    self.config["window"]["width"] = self.width()
                    self.config["window"]["height"] = self.height()
                    self.sync_panel()
                    return True

                alt_pressed = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier)
                if alt_pressed and not self.window_locked:
                    self.move(_global_point(event) - self.drag_offset)
                    self.config["window"]["x"] = self.x()
                    self.config["window"]["y"] = self.y()
                    self.sync_panel()
                    return True
            if event.type() == QEvent.Type.MouseMove and edit_mode and not (event.buttons() & Qt.MouseButton.LeftButton):
                local = _local_point(event)
                edges = _hit_edges(local)
                if any(edges):
                    self.browser.setCursor(_cursor_from_edges(edges))
                elif local.y() <= 40 and not _hit_titlebar_button_exclusion(local):
                    self.browser.setCursor(Qt.CursorShape.OpenHandCursor)
                else:
                    self.browser.setCursor(Qt.CursorShape.ArrowCursor)
                return False
            if event.type() == QEvent.Type.Leave and edit_mode and not (self._window_dragging or self._window_resizing):
                self.browser.setCursor(Qt.CursorShape.ArrowCursor)
                return False

        return super().eventFilter(watched, event)

    def current_runtime_payload(self) -> dict:
        model_path = resolve_model_path(self.config.get("model_path", ""))
        if not model_path.exists() and DEFAULT_CONFIG["model_path"]:
            model_path = resolve_model_path(DEFAULT_CONFIG["model_path"])
        runtime_path, _, _ = ensure_runtime_model(model_path)
        self.runtime_model_path = runtime_path
        pet_cfg = dict(self.config.get("pet", {}))
        background_path = resolve_background_image_path(pet_cfg.get("background_image", ""))
        if pet_cfg.get("background_enabled") and background_path.exists() and background_path.is_file():
            pet_cfg["background_image_url"] = QUrl.fromLocalFile(str(background_path)).toString()
        else:
            pet_cfg["background_image_url"] = ""

        return {
            "model_url": QUrl.fromLocalFile(str(runtime_path)).toString(),
            "pet": pet_cfg,
            "chat": {
                **self.config.get("chat", {}),
                "pet_display_name": extract_pet_display_name(self.config.get("chat", {}).get("system_prompt", "")),
                "available_expressions": list(self.expression_names),
                "lip_sync_gain": float(self.lipsync_meta.get("gain", 1.0) or 1.0),
                "mouth_parameter_ids": list(self.lipsync_meta.get("mouth_open_ids", [])),
                "mouth_form_parameter_ids": list(self.lipsync_meta.get("mouth_form_ids", [])),
            },
            "vision": normalize_vision_config(self.config.get("vision", {})),
        }

    def on_web_loaded(self, ok: bool) -> None:
        if not ok:
            return
        self.apply_config_to_web()

    def apply_config_to_web(self, after_script: str | None = None) -> None:
        payload = json.dumps(self.current_runtime_payload(), ensure_ascii=False)
        if after_script:
            script = (
                "window.PET_APP && window.PET_APP.applyConfig("
                f"{payload}).then(() => {{ {after_script} }});"
            )
        else:
            script = f"window.PET_APP && window.PET_APP.applyConfig({payload});"
        self.browser.page().runJavaScript(script)

    def play_motion(self, group: str, index: int = 0, reload_model: bool = False) -> None:
        action_script = (
            "window.PET_APP && window.PET_APP.playMotion("
            f"{json.dumps(group, ensure_ascii=False)}, {int(index)});"
        )
        if reload_model:
            self.apply_config_to_web(after_script=action_script)
            return
        self.browser.page().runJavaScript(action_script)

    def play_expression(self, name: str, reload_model: bool = False) -> None:
        action_script = (
            "window.PET_APP && window.PET_APP.playExpression("
            f"{json.dumps(name, ensure_ascii=False)});"
        )
        if reload_model:
            self.apply_config_to_web(after_script=action_script)
            return
        self.browser.page().runJavaScript(action_script)

    def play_action(self, action: dict) -> None:
        action_type = str(action.get("type", "motion"))
        if action_type == "expression":
            name = str(action.get("name", "")).strip()
            if name:
                self.play_expression(name)
            return
        self.play_motion(str(action.get("group", "Idle")), int(action.get("index", 0)))

    @Slot(str)
    def on_web_state_changed(self, payload: str) -> None:
        try:
            state = json.loads(payload)
        except Exception:
            return

        for key in ["scale", "offset_x", "offset_y", "rotation", "opacity"]:
            if key in state:
                self.config["pet"][key] = state[key]
        if self.control_panel is not None:
            self.control_panel.update_pet_widgets_from_web_state(state)

    def apply_from_panel(self) -> None:
        panel = self.control_panel

        self.config["model_path"] = normalize_model_path(panel.model_path_input.text().strip())

        self.config["pet"] = {
            "scale": float(panel.scale_spin.value()),
            "offset_x": int(panel.offset_x_spin.value()),
            "offset_y": int(panel.offset_y_spin.value()),
            "rotation": float(panel.rotation_spin.value()),
            "opacity": float(panel.opacity_spin.value()),
            "edit_mode": bool(panel.edit_mode_check.isChecked()),
            "follow_mouse": bool(panel.follow_mouse_check.isChecked()),
            "background_enabled": bool(self.config.get("pet", {}).get("background_enabled", False)),
            "background_image": str(self.config.get("pet", {}).get("background_image", "")),
            "background_overlay_opacity": float(self.config.get("pet", {}).get("background_overlay_opacity", 0.42) or 0.42),
        }
        chat_cfg = self.config.get("chat", {})
        tooling_cfg = chat_cfg.get("tooling", {}) if isinstance(chat_cfg, dict) else {}
        self.config["chat"] = {
            "backend_url": str(chat_cfg.get("backend_url", DEFAULT_BACKEND_URL)),
            "model": panel.chat_model_input.text().strip() or DEFAULT_HERMES_MODEL,
            "session_id": str(chat_cfg.get("session_id", "default")),
            "voice": panel.chat_voice_input.text().strip() or "zh-CN-XiaoxiaoNeural",
            "rate_pct": max(-50, min(100, int(panel.chat_rate_slider.value()))),
            "tts_provider": panel.chat_tts_provider_combo.currentText().strip() or "edge_tts",
            "tts_provider_url": panel.chat_tts_provider_url_input.text().strip(),
            "expression_mode": bool(panel.expression_mode_check.isChecked()),
            "expression_output_format": str(chat_cfg.get("expression_output_format", "ndjson_v1")),
            "tooling": {
                "enabled": bool(panel.tooling_enabled_check.isChecked()),
                "mode": "mcp_local_phase2",
                "file_allowlist": list(tooling_cfg.get("file_allowlist", [str(ROOT_DIR)])),
                "network_allow_domains": list(tooling_cfg.get("network_allow_domains", [])),
                "max_tool_calls_per_turn": int(tooling_cfg.get("max_tool_calls_per_turn", 6)),
                "tool_timeout_sec": int(tooling_cfg.get("tool_timeout_sec", DEFAULT_TOOL_TIMEOUT_SEC)),
                "third_party": {
                    "enabled": bool(panel.third_party_enabled_check.isChecked()),
                    "servers": list(tooling_cfg.get("third_party", {}).get("servers", [])),
                },
            },
            "skills": {
                "enabled": bool(chat_cfg.get("skills", {}).get("enabled", True)),
                "default_active_ids": list(chat_cfg.get("skills", {}).get("default_active_ids", [])),
            },
            "asr": json.loads(json.dumps(chat_cfg.get("asr", DEFAULT_ASR_CONFIG))),
            "system_prompt": panel.system_prompt_input.toPlainText().strip(),
        }
        _normalize_hermes_chat_config(self.config)
        _normalize_hermes_sidecar_config(self.config)

        self.config["window"] = {
            "x": int(panel.win_x_spin.value()),
            "y": int(panel.win_y_spin.value()),
            "width": int(panel.win_w_spin.value()),
            "height": int(panel.win_h_spin.value()),
            "locked": bool(panel.lock_window_check.isChecked()),
        }
        self.window_locked = self.config["window"]["locked"]

        self.setGeometry(
            self.config["window"]["x"],
            self.config["window"]["y"],
            self.config["window"]["width"],
            self.config["window"]["height"],
        )

        self.refresh_motion_list(prefer_reset=False)
        self.apply_config_to_web()

    def save_config(self) -> None:
        self.config["model_path"] = normalize_model_path(self.config.get("model_path", ""))
        self.config["vision"] = normalize_vision_config(self.config.get("vision", {}))
        _normalize_hermes_sidecar_config(self.config)
        _normalize_hermes_chat_config(self.config)
        self.config["window"]["x"] = self.x()
        self.config["window"]["y"] = self.y()
        self.config["window"]["width"] = self.width()
        self.config["window"]["height"] = self.height()

        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)
        self._config_mtime = self._config_mtime_token()

    def reset_to_default(self) -> None:
        self.config = json.loads(json.dumps(DEFAULT_CONFIG))

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.config["window"]["x"] = available.right() - self.config["window"]["width"] - 40
            self.config["window"]["y"] = available.bottom() - self.config["window"]["height"] - 60

        self.window_locked = bool(self.config["window"]["locked"])
        self.apply_window_geometry_from_config()
        self.refresh_motion_list(prefer_reset=True)
        self.apply_config_to_web()

    def closeEvent(self, event) -> None:
        event.accept()
        try:
            self.save_config()
        except Exception as exc:
            print(f"保存配置失败: {exc}")
        try:
            if self.settings_window is not None:
                self.settings_window.close()
        except Exception:
            pass
        self.shutdown_runtime()
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, lambda: app.exit(0))


if __name__ == "__main__":
    if _should_force_software_opengl():
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    pet = DesktopPet()
    pet.show()

    sys.exit(app.exec())
