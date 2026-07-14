from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Callable
from urllib.parse import urlparse

import requests


MACOS_NATIVE_STDERR_NOISE = (
    "TSMSendMessageToUIServer: CFMessagePortSendRequest FAILED",
    "error messaging the mach port for IMKCFRunLoopWakeUpReliable",
)
_MACOS_NATIVE_STDERR_FILTER_INSTALLED = False


def platform_name(platform_name: str | None = None, *, sys_platform: str | None = None) -> str:
    return str(platform_name or sys_platform or sys.platform).strip().lower()


def is_macos(platform_name_value: str | None = None, *, sys_platform: str | None = None) -> bool:
    return platform_name(platform_name_value, sys_platform=sys_platform) == "darwin"


def default_asr_enabled(platform_name_value: str | None = None, *, sys_platform: str | None = None) -> bool:
    return True


def default_asr_config(platform_name_value: str | None = None, *, sys_platform: str | None = None) -> dict[str, object]:
    return {
        "enabled": default_asr_enabled(platform_name_value, sys_platform=sys_platform),
        "provider": "funasr",
        "api_base_url": "http://127.0.0.1:8012",
        "provider_url": "https://api.groq.com/openai/v1/audio/transcriptions",
        "model": "whisper-large-v3-turbo",
        "api_key": "",
        "push_to_talk_key": "Alt",
        "interim_results": True,
    }


def qt_runtime_env_defaults(platform_name_value: str | None = None, *, sys_platform: str | None = None) -> dict[str, str]:
    defaults = {
        "QTWEBENGINE_DISABLE_SANDBOX": "1",
    }
    if is_macos(platform_name_value, sys_platform=sys_platform):
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


def is_macos_native_stderr_noise(
    line: str,
    platform_name_value: str | None = None,
    *,
    sys_platform: str | None = None,
) -> bool:
    if not is_macos(platform_name_value, sys_platform=sys_platform):
        return False
    text = str(line or "")
    return any(token in text for token in MACOS_NATIVE_STDERR_NOISE)


def install_macos_native_stderr_filter(
    platform_name_value: str | None = None,
    *,
    environ: dict[str, str] | None = None,
    os_module: ModuleType = os,
    threading_module: ModuleType = threading,
    sys_platform: str | None = None,
) -> bool:
    global _MACOS_NATIVE_STDERR_FILTER_INSTALLED
    if _MACOS_NATIVE_STDERR_FILTER_INSTALLED or not is_macos(platform_name_value, sys_platform=sys_platform):
        return False
    env = environ if environ is not None else os_module.environ
    if env.get("IPET_DISABLE_NATIVE_STDERR_FILTER") == "1":
        return False
    try:
        original_stderr_fd = os_module.dup(2)
        read_fd, write_fd = os_module.pipe()
        os_module.dup2(write_fd, 2)
        os_module.close(write_fd)
    except Exception:
        return False

    def pump() -> None:
        pending = b""
        try:
            while True:
                chunk = os_module.read(read_fd, 4096)
                if not chunk:
                    break
                pending += chunk
                while b"\n" in pending:
                    raw_line, pending = pending.split(b"\n", 1)
                    line_text = raw_line.decode("utf-8", errors="replace")
                    if not is_macos_native_stderr_noise(
                        line_text,
                        platform_name_value,
                        sys_platform=sys_platform,
                    ):
                        os_module.write(original_stderr_fd, raw_line + b"\n")
            if pending:
                line_text = pending.decode("utf-8", errors="replace")
                if not is_macos_native_stderr_noise(line_text, platform_name_value, sys_platform=sys_platform):
                    os_module.write(original_stderr_fd, pending)
        except Exception:
            pass

    threading_module.Thread(target=pump, name="ipet-native-stderr-filter", daemon=True).start()
    _MACOS_NATIVE_STDERR_FILTER_INSTALLED = True
    return True


def apply_qt_runtime_env(
    env: dict[str, str] | None = None,
    platform_name_value: str | None = None,
    *,
    environ: dict[str, str] | None = None,
    sys_platform: str | None = None,
) -> dict[str, str]:
    target = env if env is not None else (environ if environ is not None else os.environ)
    defaults = qt_runtime_env_defaults(platform_name_value, sys_platform=sys_platform)
    for key, value in defaults.items():
        target.setdefault(key, value)
    return defaults


def should_enable_webgl(platform_name_value: str | None = None, *, sys_platform: str | None = None) -> bool:
    return True


def should_force_software_opengl(platform_name_value: str | None = None, *, sys_platform: str | None = None) -> bool:
    return not is_macos(platform_name_value, sys_platform=sys_platform)


def should_install_python_event_filters(
    platform_name_value: str | None = None,
    *,
    sys_platform: str | None = None,
) -> bool:
    return not is_macos(platform_name_value, sys_platform=sys_platform)


def prefer_pyqt_bindings(platform_name_value: str | None = None, *, sys_platform: str | None = None) -> bool:
    return is_macos(platform_name_value, sys_platform=sys_platform)


def common_exec_search_dirs(
    *,
    root_dir: Path,
    backend_venv_dirname: str,
    path_home: Callable[[], Path] = Path.home,
    os_name: str | None = None,
) -> list[str]:
    root = Path(root_dir)
    home = Path(path_home())
    candidates: list[Path] = []
    effective_os_name = os_name or os.name
    if effective_os_name == "nt":
        candidates.extend(
            [
                root / ".venv" / "Scripts",
                root / backend_venv_dirname / "Scripts",
                home / "AppData" / "Roaming" / "Python" / "Scripts",
            ]
        )
    else:
        candidates.extend(
            [
                root / ".venv" / "bin",
                root / backend_venv_dirname / "bin",
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


def augment_process_path(
    env: dict[str, str] | None = None,
    *,
    root_dir: Path,
    backend_venv_dirname: str,
    environ: dict[str, str] | None = None,
    path_home: Callable[[], Path] = Path.home,
    os_name: str | None = None,
) -> str:
    target = env if env is not None else (environ if environ is not None else os.environ)
    current = [item for item in str(target.get("PATH") or "").split(os.pathsep) if item]
    prefixes: list[str] = []
    seen = set(current)
    for candidate in common_exec_search_dirs(
        root_dir=root_dir,
        backend_venv_dirname=backend_venv_dirname,
        path_home=path_home,
        os_name=os_name,
    ):
        if candidate in seen:
            continue
        seen.add(candidate)
        prefixes.append(candidate)
    target["PATH"] = os.pathsep.join(prefixes + current)
    return target["PATH"]


def preferred_python_commands(*, root_dir: Path) -> list[str]:
    root = Path(root_dir)
    version_text = ""
    try:
        version_text = (root / ".python-version").read_text(encoding="utf-8").strip()
    except Exception:
        version_text = ""
    parts = [item for item in version_text.split(".") if item]
    commands: list[str] = []
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


def python_entry_for_venv(venv_dir: Path, *, os_name: str | None = None) -> Path:
    effective_os_name = os_name or os.name
    return Path(venv_dir) / ("Scripts/python.exe" if effective_os_name == "nt" else "bin/python")


def service_log_path(*, name: str, root_dir: Path, service_log_dir: Path) -> Path:
    try:
        service_log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return Path(root_dir) / f".{name}.log"
    return Path(service_log_dir) / f"{name}.log"


def truncate_service_log(path: Path) -> Path:
    try:
        Path(path).write_text("", encoding="utf-8")
    except Exception:
        pass
    return Path(path)


def tail_service_log(path: Path, *, max_lines: int = 20) -> str:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""
    non_empty = [line.rstrip() for line in lines if str(line).strip()]
    tail = non_empty[-max(1, int(max_lines)) :]
    return "\n".join(tail)


def python_command_exists(command: str, *, shutil_module: ModuleType = shutil) -> bool:
    text = str(command or "").strip()
    if not text:
        return False
    if os.path.sep in text or (os.path.altsep and os.path.altsep in text):
        return Path(text).exists()
    return shutil_module.which(text) is not None


def python_supports_backend(
    command: str,
    *,
    root_dir: Path,
    command_exists: Callable[[str], bool] = python_command_exists,
    subprocess_module: ModuleType = subprocess,
) -> bool:
    if not command_exists(command):
        return False
    try:
        result = subprocess_module.run(
            [str(command), "-c", "import uvicorn; import backend.app"],
            cwd=str(root_dir),
            stdout=subprocess_module.DEVNULL,
            stderr=subprocess_module.DEVNULL,
            timeout=8,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def resolve_backend_python(
    *,
    root_dir: Path,
    backend_venv_dirname: str,
    environ: dict[str, str],
    sys_executable: str,
    python_entry_for_venv_func: Callable[[Path], Path],
    preferred_python_commands_func: Callable[[], list[str]],
    python_supports_backend_func: Callable[[str], bool],
    python_command_exists_func: Callable[[str], bool],
) -> str:
    override = str(environ.get("PET_BACKEND_PYTHON") or "").strip()
    candidates: list[str] = []
    if override:
        candidates.append(override)
    candidates.append(str(python_entry_for_venv_func(Path(root_dir) / ".venv")))
    candidates.append(str(python_entry_for_venv_func(Path(root_dir) / backend_venv_dirname)))
    candidates.extend(preferred_python_commands_func())
    candidates.append(sys_executable)

    seen: set[str] = set()
    fallback = sys_executable
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if python_supports_backend_func(normalized):
            return normalized
        if python_command_exists_func(normalized) and fallback == sys_executable:
            fallback = normalized
    return fallback


def resolve_asr_python(
    *,
    root_dir: Path,
    backend_venv_dirname: str,
    environ: dict[str, str],
    sys_executable: str,
    python_entry_for_venv_func: Callable[[Path], Path],
    preferred_python_commands_func: Callable[[], list[str]],
    python_command_exists_func: Callable[[str], bool],
) -> str:
    override = str(environ.get("PET_ASR_PYTHON") or "").strip()
    if override:
        return override
    root = Path(root_dir)
    for dirname in (backend_venv_dirname, ".venv"):
        candidate = python_entry_for_venv_func(root / dirname)
        if candidate.exists():
            return str(candidate)
    for command in preferred_python_commands_func():
        if python_command_exists_func(command):
            return command
    return sys_executable


def is_service_healthy(
    base_url: str,
    *,
    require_asr: bool = False,
    requests_module: ModuleType = requests,
) -> bool:
    try:
        resp = requests_module.get(f"{base_url.rstrip('/')}/api/health", timeout=1.5)
        if resp.status_code != 200:
            return False
        if not require_asr:
            return True
        payload = resp.json()
        return bool(payload.get("asr"))
    except Exception:
        return False


def is_backend_live(backend_url: str, *, service_healthy: Callable[[str], bool] = is_service_healthy) -> bool:
    return service_healthy(backend_url)


def is_backend_healthy(
    backend_url: str,
    *,
    backend_live: Callable[[str], bool] = is_backend_live,
    supports_required_routes: Callable[[str], bool] | None = None,
) -> bool:
    required_routes = supports_required_routes or backend_supports_required_routes
    return backend_live(backend_url) and required_routes(backend_url)


def is_asr_healthy(
    asr_url: str,
    *,
    service_healthy: Callable[..., bool] = is_service_healthy,
) -> bool:
    return service_healthy(asr_url, require_asr=True)


def parse_service_host_port(base_url: str, *, default_port: int) -> tuple[str, int]:
    parsed = urlparse(str(base_url or "").strip() or f"http://127.0.0.1:{default_port}")
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or default_port)
    return host, port


def backend_supports_required_routes(
    base_url: str,
    *,
    service_healthy: Callable[[str], bool] = is_service_healthy,
) -> bool:
    return service_healthy(base_url)


def is_local_service_url(base_url: str) -> bool:
    parsed = urlparse(str(base_url or "").strip() or "")
    host = (parsed.hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost"}


def is_service_port_available(host: str, port: int, *, socket_module: ModuleType = socket) -> bool:
    bind_host = "127.0.0.1" if host == "localhost" else host
    family = socket_module.AF_INET6 if ":" in bind_host else socket_module.AF_INET
    sock = socket_module.socket(family, socket_module.SOCK_STREAM)
    try:
        sock.setsockopt(socket_module.SOL_SOCKET, socket_module.SO_REUSEADDR, 1)
        sock.bind((bind_host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def pick_backend_launch_url(
    preferred_url: str,
    *,
    max_offset: int = 12,
    default_backend_url: str,
    is_local_url: Callable[[str], bool] = is_local_service_url,
    parse_host_port: Callable[..., tuple[str, int]] = parse_service_host_port,
    is_port_available: Callable[[str, int], bool] = is_service_port_available,
) -> str:
    preferred = str(preferred_url or "").strip() or default_backend_url
    if not is_local_url(preferred):
        return preferred
    host, port = parse_host_port(preferred, default_port=8008)
    for offset in range(max(1, int(max_offset)) + 1):
        candidate_port = port + offset
        if is_port_available(host, candidate_port):
            return f"http://{host}:{candidate_port}"
    return preferred
