from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable


class NativeApprovalNotificationController:
    def __init__(self, *, source_root: Path, cache_root: Path, on_decision: Callable[[str], None]) -> None:
        self.source_root = source_root
        self.cache_root = cache_root
        self.on_decision = on_decision
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._build_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._starting: set[str] = set()
        self._cancelled: set[str] = set()
        self._stopping = False

    def show(self, raw_payload: str) -> None:
        try:
            payload = json.loads(str(raw_payload or "{}"))
            proposal_id = str(payload.get("proposal_id") or payload.get("turn_id") or "").strip()
            if not proposal_id:
                raise ValueError("approval notification requires proposal_id")
        except Exception as exc:
            self._emit_failure("", f"invalid_payload:{exc}")
            return
        with self._state_lock:
            if self._stopping or proposal_id in self._starting or proposal_id in self._processes:
                return
            self._starting.add(proposal_id)
        threading.Thread(target=self._run, args=(proposal_id, payload), daemon=True).start()

    def _bundle_executable(self) -> Path:
        with self._build_lock:
            return self._bundle_executable_locked()

    def _bundle_executable_locked(self) -> Path:
        bundle = self.cache_root / "IpetApprovalNotifier.app"
        executable = bundle / "Contents" / "MacOS" / "IpetApprovalNotifier"
        source = self.source_root / "main.m"
        if executable.exists() and executable.stat().st_mtime >= source.stat().st_mtime:
            return executable
        executable.parent.mkdir(parents=True, exist_ok=True)
        resources = bundle / "Contents" / "Resources"
        resources.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.source_root / "Info.plist", bundle / "Contents" / "Info.plist")
        env = dict(os.environ)
        module_cache = self.cache_root / "clang-module-cache"
        module_cache.mkdir(parents=True, exist_ok=True)
        env["CLANG_MODULE_CACHE_PATH"] = str(module_cache)
        result = subprocess.run(
            ["xcrun", "clang", "-fobjc-arc", "-fblocks", str(source), "-o", str(executable), "-framework", "AppKit", "-framework", "UserNotifications"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "native notification build failed")[:500])
        signed = subprocess.run(
            ["codesign", "--force", "--sign", "-", str(bundle)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if signed.returncode != 0:
            raise RuntimeError((signed.stderr or signed.stdout or "notification helper signing failed")[:500])
        return executable

    def _run(self, proposal_id: str, payload: dict[str, Any]) -> None:
        process = None
        try:
            with self._state_lock:
                if self._stopping or proposal_id in self._cancelled:
                    return
            executable = self._bundle_executable()
            request = {
                "proposal_id": proposal_id,
                "title": str(payload.get("title") or "Ipet 需要你的批准")[:120],
                "body": str(payload.get("message") or payload.get("summary") or payload.get("text") or "是否批准这一步操作？")[:800],
                "timeout_sec": max(30, min(float(payload.get("timeout_sec") or 300), 600)),
            }
            with self._state_lock:
                if self._stopping or proposal_id in self._cancelled:
                    return
            process = subprocess.Popen(
                [str(executable)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            with self._state_lock:
                if self._stopping or proposal_id in self._cancelled:
                    self._terminate_process(process)
                    return
                self._processes[proposal_id] = process
            stdout, stderr = process.communicate(json.dumps(request, ensure_ascii=False), timeout=request["timeout_sec"] + 15)
            if process.returncode != 0:
                raise RuntimeError((stderr or stdout or "notification helper failed")[:500])
            with self._state_lock:
                if self._stopping or proposal_id in self._cancelled:
                    return
            line = next((item for item in reversed(stdout.splitlines()) if item.strip().startswith("{")), "")
            result = json.loads(line)
            result["proposal_id"] = proposal_id
            self.on_decision(json.dumps(result, ensure_ascii=False))
        except subprocess.TimeoutExpired as exc:
            if process is not None:
                self._terminate_process(process)
            if not self._is_cancelled(proposal_id):
                self._emit_failure(proposal_id, f"notification_timeout:{exc}")
        except Exception as exc:
            if process is not None:
                self._terminate_process(process)
            if not self._is_cancelled(proposal_id):
                self._emit_failure(proposal_id, str(exc))
        finally:
            with self._state_lock:
                if process is not None and self._processes.get(proposal_id) is process:
                    self._processes.pop(proposal_id, None)
                self._starting.discard(proposal_id)
                self._cancelled.discard(proposal_id)

    def _emit_failure(self, proposal_id: str, reason: str) -> None:
        self.on_decision(json.dumps({"proposal_id": proposal_id, "approved": False, "reason": reason[:300]}, ensure_ascii=False))

    def _is_cancelled(self, proposal_id: str) -> bool:
        with self._state_lock:
            return self._stopping or proposal_id in self._cancelled

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        def close_streams() -> None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass

        if process.poll() is not None:
            close_streams()
            return
        try:
            process.terminate()
        except OSError:
            close_streams()
            return
        try:
            process.wait(timeout=2)
            close_streams()
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            close_streams()

    def stop_all(self) -> None:
        with self._state_lock:
            self._stopping = True
            self._cancelled.update(self._starting)
            self._cancelled.update(self._processes)
            processes = tuple(self._processes.values())
            self._processes.clear()
        for process in processes:
            self._terminate_process(process)

    def cancel(self, proposal_id: str) -> None:
        key = str(proposal_id or "").strip()
        if not key:
            return
        with self._state_lock:
            self._cancelled.add(key)
            process = self._processes.pop(key, None)
        if process is not None:
            self._terminate_process(process)
