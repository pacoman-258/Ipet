from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import requests
from app import desktop_runtime as _desktop_runtime
from body.qwen_tts import (
    QWEN_TTS_PROJECT_DIR,
    qwen_tts_health_url,
    qwen_tts_python,
)


class DesktopServiceLifecycle:
    def __init__(
        self,
        config: dict,
        *,
        root_dir: Path,
        default_backend_url: str,
        default_asr_api_base_url: str,
        local_api_token_env: str,
        local_api_token: str,
        os_module: ModuleType = os,
        subprocess_module: ModuleType = subprocess,
        requests_module: ModuleType = requests,
        threading_module: ModuleType = threading,
        time_module: ModuleType = time,
        sys_module: ModuleType = sys,
        print_func: Callable[..., None] = print,
        is_backend_healthy: Callable[[str], bool] | None = None,
        is_backend_authorized: Callable[[str], bool] | None = None,
        is_backend_live: Callable[[str], bool] | None = None,
        is_asr_healthy: Callable[[str], bool] | None = None,
        is_local_service_url: Callable[[str], bool] | None = None,
        pick_backend_launch_url: Callable[[str], str] | None = None,
        parse_service_host_port: Callable[..., tuple[str, int]] | None = None,
        resolve_backend_python: Callable[[], str] | None = None,
        resolve_asr_python: Callable[[], str] | None = None,
        service_log_path: Callable[[str], Path] | None = None,
        truncate_service_log: Callable[[Path], Path] | None = None,
        tail_service_log: Callable[..., str] | None = None,
    ) -> None:
        self.config = config
        self.root_dir = Path(root_dir)
        self.default_backend_url = str(default_backend_url)
        self.default_asr_api_base_url = str(default_asr_api_base_url)
        self.local_api_token_env = str(local_api_token_env)
        self.local_api_token = str(local_api_token)
        self.os = os_module
        self.subprocess = subprocess_module
        self.requests = requests_module
        self.threading = threading_module
        self.time = time_module
        self.sys = sys_module
        self.print = print_func
        self.is_backend_healthy = is_backend_healthy or _desktop_runtime.is_backend_healthy
        self.is_backend_authorized = is_backend_authorized or self._backend_accepts_local_token
        self.is_backend_live = is_backend_live or _desktop_runtime.is_backend_live
        self.is_asr_healthy = is_asr_healthy or _desktop_runtime.is_asr_healthy
        self.is_local_service_url = is_local_service_url or _desktop_runtime.is_local_service_url
        self.pick_backend_launch_url = pick_backend_launch_url or self._pick_backend_launch_url
        self.parse_service_host_port = parse_service_host_port or _desktop_runtime.parse_service_host_port
        self.resolve_backend_python = resolve_backend_python or (lambda: self.sys.executable)
        self.resolve_asr_python = resolve_asr_python or (lambda: self.sys.executable)
        self.service_log_path = service_log_path or self._service_log_path
        self.truncate_service_log = truncate_service_log or _desktop_runtime.truncate_service_log
        self.tail_service_log = tail_service_log or _desktop_runtime.tail_service_log
        self.backend_process = None
        self.backend_started_by_app = False
        self.asr_process = None
        self.asr_started_by_app = False
        self.qwen_tts_process = None
        self.qwen_tts_started_by_app = False
        self._asr_warmup_monitor_lock = self.threading.Lock()
        self._asr_warmup_monitor_thread = None
        self._asr_warmup_stop_event = self.threading.Event()
        self._service_start_locks_lock = self.threading.Lock()
        self._service_start_locks: dict[str, Any] = {}

    @contextmanager
    def _service_start_guard(self, name: str):
        """Serialize service startup across threads and desktop instances."""
        with self._service_start_locks_lock:
            local_lock = self._service_start_locks.setdefault(name, self.threading.Lock())
        with local_lock:
            with self._service_start_guard_locked(name):
                yield

    @contextmanager
    def _service_start_guard_locked(self, name: str):
        lock_path = self.root_dir / ".service-logs" / f"{name}.start.lock"
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = lock_path.open("a+")
        except OSError:
            yield
            return

        lock_kind = ""
        try:
            if self.os.name == "nt":
                try:
                    import msvcrt

                    lock_file.seek(0)
                    lock_file.write("0")
                    lock_file.flush()
                    start_time = self.time.monotonic() if hasattr(self.time, "monotonic") else self.time.time()
                    while True:
                        try:
                            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                            lock_kind = "msvcrt"
                            break
                        except OSError:
                            now = self.time.monotonic() if hasattr(self.time, "monotonic") else self.time.time()
                            if now - start_time >= 5.0:
                                break
                            self.time.sleep(0.05)
                except ImportError:
                    pass
            else:
                try:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                    lock_kind = "fcntl"
                except (ImportError, OSError):
                    pass
            yield
        finally:
            try:
                if lock_kind == "msvcrt":
                    import msvcrt

                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                elif lock_kind == "fcntl":
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            lock_file.close()

    def _pick_backend_launch_url(self, preferred_url: str) -> str:
        return _desktop_runtime.pick_backend_launch_url(
            preferred_url,
            default_backend_url=self.default_backend_url,
        )

    def _service_log_path(self, name: str) -> Path:
        return _desktop_runtime.service_log_path(
            name=name,
            root_dir=self.root_dir,
            service_log_dir=self.root_dir / ".service-logs",
        )

    def ensure_backend_service(self) -> None:
        with self._service_start_guard("backend"):
            self._ensure_backend_service()

    def _backend_accepts_local_token(self, backend_url: str) -> bool:
        """Return whether a local backend belongs to this desktop process."""
        try:
            response = self.requests.get(
                f"{str(backend_url).rstrip('/')}/api/vision/context",
                params={"include_image": "false", "lane": "active"},
                headers={"X-Ipet-Local-Token": self.local_api_token},
                timeout=0.75,
            )
        except Exception:
            return False
        return 200 <= int(getattr(response, "status_code", 0) or 0) < 300

    def _backend_can_be_reused(self, backend_url: str) -> bool:
        if not self.is_local_service_url(backend_url):
            return True
        return bool(self.is_backend_authorized(backend_url))

    def _ensure_backend_service(self) -> None:
        chat_cfg = self.config.get("chat", {})
        backend_url = str(chat_cfg.get("backend_url", self.default_backend_url)).strip() or self.default_backend_url
        self.config.setdefault("chat", {})["backend_url"] = backend_url
        if self.is_backend_healthy(backend_url) and self._backend_can_be_reused(backend_url):
            return

        if self.is_local_service_url(backend_url):
            launch_url = self.pick_backend_launch_url(backend_url)
            if launch_url != backend_url:
                self.config.setdefault("chat", {})["backend_url"] = launch_url
                backend_url = launch_url
            if self.is_backend_healthy(backend_url) and self._backend_can_be_reused(backend_url):
                return

        host, port = self.parse_service_host_port(backend_url, default_port=8008)
        backend_python = self.resolve_backend_python()
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
        if self.os.name == "nt":
            creationflags = getattr(self.subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                self.subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )

        backend_log_path = self.truncate_service_log(self.service_log_path("backend"))
        try:
            env = dict(self.os.environ)
            env[self.local_api_token_env] = self.local_api_token
            with Path(backend_log_path).open("a", encoding="utf-8") as backend_log:
                self.backend_process = self.subprocess.Popen(
                    cmd,
                    cwd=str(self.root_dir),
                    stdout=backend_log,
                    stderr=self.subprocess.STDOUT,
                    creationflags=creationflags,
                    env=env,
                    start_new_session=self.os.name != "nt",
                )
            self.backend_started_by_app = True
        except Exception as exc:
            self.print(f"failed to start backend with {backend_python}: {exc}")
            return

        for _ in range(60):
            if self.is_backend_live(backend_url):
                return
            if self.backend_process and self.backend_process.poll() is not None:
                detail = self.tail_service_log(backend_log_path)
                if detail:
                    self.print(f"backend exited early with code {self.backend_process.returncode}:\n{detail}")
                else:
                    self.print(f"backend exited early with code {self.backend_process.returncode}.")
                return
            self.time.sleep(0.25)
        detail = self.tail_service_log(backend_log_path)
        if detail:
            self.print(f"backend did not become ready in time; chat may be unavailable.\n{detail}")
        else:
            self.print("backend did not become ready in time; chat may be unavailable.")

    def ensure_asr_service(self) -> None:
        with self._service_start_guard("asr"):
            self._ensure_asr_service()

    def _ensure_asr_service(self) -> None:
        chat_cfg = self.config.get("chat", {})
        asr_cfg = chat_cfg.get("asr", {}) if isinstance(chat_cfg, dict) else {}
        if not isinstance(asr_cfg, dict) or not bool(asr_cfg.get("enabled", True)):
            return
        backend_url = str(chat_cfg.get("backend_url") or self.default_backend_url).strip() or self.default_backend_url
        provider = str(asr_cfg.get("provider") or "funasr").strip().lower()
        if self.is_local_service_url(backend_url) and provider != "groq":
            self.config.setdefault("chat", {}).setdefault("asr", {})["api_base_url"] = backend_url
            return
        asr_url = str(asr_cfg.get("api_base_url") or self.default_asr_api_base_url).strip() or self.default_asr_api_base_url
        self.config.setdefault("chat", {}).setdefault("asr", {})["api_base_url"] = asr_url
        if self.is_asr_healthy(asr_url):
            return

        host, port = self.parse_service_host_port(asr_url, default_port=8012)
        asr_python = self.resolve_asr_python()
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
        if self.os.name == "nt":
            creationflags = getattr(self.subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                self.subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )

        asr_log_path = self.truncate_service_log(self.service_log_path("asr"))
        try:
            with Path(asr_log_path).open("a", encoding="utf-8") as asr_log:
                self.asr_process = self.subprocess.Popen(
                    cmd,
                    cwd=str(self.root_dir),
                    stdout=asr_log,
                    stderr=self.subprocess.STDOUT,
                    creationflags=creationflags,
                    start_new_session=self.os.name != "nt",
                )
            self.asr_started_by_app = True
        except Exception as exc:
            self.print(f"启动 ASR 服务失败: {exc}")
            return

        for _ in range(40):
            if self.is_asr_healthy(asr_url):
                return
            if self.asr_process and self.asr_process.poll() is not None:
                detail = self.tail_service_log(asr_log_path)
                if detail:
                    self.print(f"ASR 服务提前退出，退出码 {self.asr_process.returncode}:\n{detail}")
                else:
                    self.print(f"ASR 服务提前退出，退出码 {self.asr_process.returncode}。")
                return
            self.time.sleep(0.2)
        detail = self.tail_service_log(asr_log_path)
        if detail:
            self.print(f"ASR 服务未在预期时间内就绪，语音输入可能不可用。\n{detail}")
        else:
            self.print("ASR 服务未在预期时间内就绪，语音输入可能不可用。")

    def _qwen_tts_is_healthy(self) -> bool:
        try:
            response = self.requests.get(qwen_tts_health_url(), timeout=0.15)
            return bool(response.ok)
        except Exception:
            return False

    def start_qwen_tts_service_async(self) -> None:
        # Popen returns without waiting for model initialization.  Starting it
        # on the desktop thread avoids losing a daemon worker during early Qt
        # shutdown, while the heavyweight model stays in its own process.
        self.ensure_qwen_tts_service()

    def ensure_qwen_tts_service(self) -> None:
        with self._service_start_guard("qwen-tts"):
            self._ensure_qwen_tts_service()

    def _ensure_qwen_tts_service(self) -> None:
        if self._qwen_tts_is_healthy():
            return
        if self.qwen_tts_process is not None and self.qwen_tts_process.poll() is None:
            return

        project_dir = QWEN_TTS_PROJECT_DIR
        qwen_python = qwen_tts_python()
        if not project_dir.is_dir() or not qwen_python.is_file():
            self.print(f"Qwen TTS 本地运行环境不可用: {project_dir}")
            return

        log_path = self.truncate_service_log(self.service_log_path("qwen-tts"))
        command = [
            str(qwen_python),
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--log-level",
            "warning",
        ]
        creationflags = 0
        if self.os.name == "nt":
            creationflags = getattr(self.subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                self.subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
        try:
            with Path(log_path).open("a", encoding="utf-8") as qwen_log:
                self.qwen_tts_process = self.subprocess.Popen(
                    command,
                    cwd=str(project_dir),
                    stdout=qwen_log,
                    stderr=self.subprocess.STDOUT,
                    creationflags=creationflags,
                    start_new_session=self.os.name != "nt",
                )
            self.qwen_tts_started_by_app = True
        except Exception as exc:
            self.print(f"启动 Qwen TTS 服务失败: {exc}")

    def request_asr_warmup(self) -> None:
        chat_cfg = self.config.get("chat", {})
        asr_cfg = chat_cfg.get("asr", {}) if isinstance(chat_cfg, dict) else {}
        if not isinstance(asr_cfg, dict) or not bool(asr_cfg.get("enabled", True)):
            self.print("[ASR] ASR 已禁用，跳过初始化。")
            return
        self.ensure_backend_service()
        self.ensure_asr_service()
        backend_url = str(self.config.get("chat", {}).get("backend_url") or self.default_backend_url).strip() or self.default_backend_url
        provider = str(asr_cfg.get("provider") or "funasr").strip().lower()
        asr_url = str(asr_cfg.get("api_base_url") or self.default_asr_api_base_url).strip() or self.default_asr_api_base_url
        warmup_base_url = (asr_url if provider == "groq" else backend_url).rstrip("/")
        try:
            resp = self.requests.post(f"{warmup_base_url}/api/asr/warmup", timeout=3)
            payload = resp.json() if resp.headers.get("content-type", "").lower().startswith("application/json") else {}
        except Exception as exc:
            self.print(f"[ASR] 初始化请求失败: {exc}")
            return

        ready = bool(payload.get("ready"))
        started = bool(payload.get("started"))
        message = str(payload.get("message") or "").strip()
        if ready:
            self.print("[ASR] 初始化已完成，此时按住 Ctrl 可以正常使用。")
            return
        if started:
            self.print("[ASR] 已开始初始化语音模型，松开 Ctrl 也不会中断。")
        elif message:
            self.print(f"[ASR] {message}")
        self.start_asr_warmup_progress_monitor(warmup_base_url)

    def start_asr_warmup_progress_monitor(self, base_url: str) -> None:
        with self._asr_warmup_monitor_lock:
            if self._asr_warmup_monitor_thread is not None and self._asr_warmup_monitor_thread.is_alive():
                return
            self._asr_warmup_stop_event.clear()
            self._asr_warmup_monitor_thread = self.threading.Thread(
                target=self.run_asr_warmup_progress_monitor,
                args=(str(base_url or "").rstrip("/"),),
                daemon=True,
            )
            self._asr_warmup_monitor_thread.start()

    def run_asr_warmup_progress_monitor(self, base_url: str) -> None:
        started_at = self.time.perf_counter()
        bar_width = 24
        self.print("[ASR] 正在初始化本地语音模型，首次加载通常需要约 1 分钟。")
        while not self._asr_warmup_stop_event.is_set():
            elapsed = self.time.perf_counter() - started_at
            ready = False
            message = "等待 ASR 后端响应..."
            try:
                resp = self.requests.get(f"{base_url}/api/health", timeout=2)
                payload = resp.json() if resp.headers.get("content-type", "").lower().startswith("application/json") else {}
                ready = bool(payload.get("asr"))
                message = str(payload.get("message") or ("ASR 已就绪" if ready else "ASR 正在初始化...")).strip() or "ASR 正在初始化..."
            except Exception as exc:
                message = f"等待 ASR 后端响应: {exc.__class__.__name__}"

            progress = 1.0 if ready else min(0.95, max(0.05, elapsed / 60.0 * 0.9))
            filled = max(1, int(bar_width * progress)) if not ready else bar_width
            bar = "#" * filled + "-" * max(0, bar_width - filled)
            line = f"\r[ASR] [{bar}] {int(progress * 100):>3}% {int(elapsed):>3}s {message[:48]}"
            self.sys.stdout.write(line.ljust(96))
            self.sys.stdout.flush()
            if ready:
                self.sys.stdout.write("\n[ASR] 初始化完成，此时按住 Ctrl 可以正常使用。\n")
                self.sys.stdout.flush()
                return
            if elapsed >= 180:
                self.sys.stdout.write("\n[ASR] 初始化超过 180 秒仍未完成，请稍后再按住 Ctrl 重试。\n")
                self.sys.stdout.flush()
                return
            if self._asr_warmup_stop_event.wait(1.0):
                return

    def stop_asr_warmup_progress_monitor(self) -> None:
        self._asr_warmup_stop_event.set()

    def _signal_managed_process(self, proc, sig: int) -> None:
        if self.os.name != "nt":
            killpg = getattr(self.os, "killpg", None)
            getpgid = getattr(self.os, "getpgid", None)
            pid = getattr(proc, "pid", None)
            if callable(killpg) and callable(getpgid) and pid:
                try:
                    killpg(getpgid(pid), sig)
                    return
                except ProcessLookupError:
                    return
                except OSError:
                    pass
        if sig == signal.SIGKILL:
            proc.kill()
        else:
            proc.terminate()

    def stop_managed_process(self, proc, *, started_by_app: bool) -> None:
        if not proc or not started_by_app:
            return
        if proc.poll() is not None:
            return
        try:
            if self.os.name == "nt":
                self.subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=self.subprocess.DEVNULL,
                    stderr=self.subprocess.DEVNULL,
                    check=False,
                    creationflags=getattr(self.subprocess, "CREATE_NO_WINDOW", 0),
                )
                proc.wait(timeout=2)
            else:
                self._signal_managed_process(proc, signal.SIGTERM)
                proc.wait(timeout=2)
        except Exception:
            try:
                self._signal_managed_process(proc, signal.SIGKILL)
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
            self.stop_managed_process(proc, started_by_app=True)
        finally:
            self.backend_process = None
            self.backend_started_by_app = False

    def stop_asr_service(self) -> None:
        self.stop_asr_warmup_progress_monitor()
        proc = self.asr_process
        if not proc or not self.asr_started_by_app:
            return
        if proc.poll() is not None:
            self.asr_process = None
            self.asr_started_by_app = False
            return
        try:
            self.stop_managed_process(proc, started_by_app=True)
        finally:
            self.asr_process = None
            self.asr_started_by_app = False

    def stop_qwen_tts_service(self) -> None:
        proc = self.qwen_tts_process
        if not proc or not self.qwen_tts_started_by_app:
            return
        if proc.poll() is not None:
            self.qwen_tts_process = None
            self.qwen_tts_started_by_app = False
            return
        try:
            self.stop_managed_process(proc, started_by_app=True)
        finally:
            self.qwen_tts_process = None
            self.qwen_tts_started_by_app = False
