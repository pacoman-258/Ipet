from __future__ import annotations

import ctypes
import os
import re
import secrets
import subprocess
import sys
import threading
from typing import Any, Callable

import requests

from backend.environment import normalize_environment_config
from body.screen_vision_controller import collect_screen_observations


LOCAL_API_TOKEN_ENV = "IPET_LOCAL_API_TOKEN"
LOCAL_API_TOKEN_HEADER = "X-Ipet-Local-Token"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8008"
_MAC_IDLE_RE = re.compile(r'"?HIDIdleTime"?\s*=\s*(\d+)')


def _ensure_local_api_token() -> str:
    token = str(os.environ.get(LOCAL_API_TOKEN_ENV) or "").strip()
    if token:
        return token
    token = secrets.token_urlsafe(32)
    os.environ[LOCAL_API_TOKEN_ENV] = token
    return token


def load_qtimer():
    try:
        from PySide6.QtCore import QTimer
    except ImportError:
        from PyQt6.QtCore import QTimer
    return QTimer


def read_idle_seconds(
    *,
    platform_name: str | None = None,
    runner: Callable[..., Any] | None = None,
) -> int:
    platform_value = str(platform_name or sys.platform).lower()
    if platform_value.startswith("darwin"):
        run = runner or subprocess.run
        try:
            result = run(
                ["/usr/sbin/ioreg", "-c", "IOHIDSystem"],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
            match = _MAC_IDLE_RE.search(str(getattr(result, "stdout", "") or ""))
            return max(0, int(match.group(1)) // 1_000_000_000) if match else 0
        except Exception:
            return 0
    if platform_value.startswith("win"):
        try:
            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(info)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return 0
            elapsed_ms = int(ctypes.windll.kernel32.GetTickCount()) - int(info.dwTime)
            return max(0, elapsed_ms // 1000)
        except Exception:
            return 0
    return 0


def desktop_metadata_payload(
    config: dict[str, Any],
    *,
    observation_provider: Callable[..., dict[str, Any]] = collect_screen_observations,
    idle_provider: Callable[[], int] = read_idle_seconds,
) -> dict[str, Any]:
    environment_config = normalize_environment_config(config)
    try:
        observation = observation_provider({"include_ui_metadata": True}) or {}
    except Exception:
        observation = {}
    desktop_context = observation.get("desktop_context") if isinstance(observation.get("desktop_context"), dict) else {}
    foreground_app = str(
        observation.get("foreground_app")
        or desktop_context.get("foreground_app")
        or desktop_context.get("frontmost_process")
        or ""
    ).strip()[:80]
    payload: dict[str, Any] = {
        "event_type": "desktop_state",
        "foreground_app": foreground_app,
        "idle_seconds": max(0, int(idle_provider() or 0)),
    }
    if environment_config["include_window_titles"]:
        payload["window_title"] = str(
            observation.get("window_title") or desktop_context.get("window_title") or ""
        ).strip()[:160]
    return payload


class EnvironmentController:
    def __init__(
        self,
        parent=None,
        *,
        timer_factory=None,
        metadata_provider=None,
        idle_provider=None,
        task_runner=None,
        post_event=None,
    ) -> None:
        timer_factory = timer_factory or load_qtimer()
        self._timer = timer_factory(parent)
        self._timer.timeout.connect(self.collect_once)
        self._metadata_provider = metadata_provider or collect_screen_observations
        self._idle_provider = idle_provider or read_idle_seconds
        self._task_runner = task_runner or self._run_background_task
        self._post_event = post_event or self._post_event_request
        self._config = normalize_environment_config({})
        self._backend_url = DEFAULT_BACKEND_URL
        self._lock = threading.Lock()
        self._in_flight = False
        self._stop_event = threading.Event()
        self.last_error = ""

    def apply_config(self, config: dict[str, Any]) -> None:
        source = config if isinstance(config, dict) else {}
        self._config = normalize_environment_config(source.get("environment", {}))
        chat_config = source.get("chat") if isinstance(source.get("chat"), dict) else {}
        self._backend_url = str(chat_config.get("backend_url") or DEFAULT_BACKEND_URL).strip() or DEFAULT_BACKEND_URL
        if self._config["mode"] == "off" or not self._config["metadata_enabled"]:
            self.stop()
            return
        self._stop_event.clear()
        self._timer.start(int(self._config["sensor_interval_sec"]) * 1000)

    def stop(self) -> None:
        self._stop_event.set()
        self._timer.stop()

    def collect_once(self) -> None:
        if self._config["mode"] == "off" or not self._config["metadata_enabled"]:
            return
        with self._lock:
            if self._in_flight:
                return
            self._in_flight = True
        self._task_runner(self._collect_and_post)

    def _collect_and_post(self) -> None:
        try:
            if self._stop_event.is_set():
                return
            payload = desktop_metadata_payload(
                self._config,
                observation_provider=self._metadata_provider,
                idle_provider=self._idle_provider,
            )
            url = f"{self._backend_url.rstrip('/')}/api/environment/events"
            self._post_event(url, payload, 2.0)
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            print(f"环境元数据上传失败: {exc}")
        finally:
            with self._lock:
                self._in_flight = False

    @staticmethod
    def _run_background_task(task) -> None:
        threading.Thread(target=task, name="IpetEnvironmentPost", daemon=True).start()

    @staticmethod
    def _post_event_request(url: str, payload: dict[str, Any], timeout: float):
        response = requests.post(
            url,
            json=payload,
            headers={LOCAL_API_TOKEN_HEADER: _ensure_local_api_token()},
            timeout=timeout,
        )
        response.raise_for_status()
        return response


__all__ = ["EnvironmentController", "desktop_metadata_payload", "read_idle_seconds"]
