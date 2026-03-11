from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


class MCPProtocolError(RuntimeError):
    pass


class StdioMCPClient:
    def __init__(
        self,
        command: list[str],
        cwd: Path,
        timeout_sec: int = 180,
        env: dict[str, str] | None = None,
    ) -> None:
        if not command:
            raise ValueError("command cannot be empty")
        self.command = list(command)
        self.cwd = cwd
        self.timeout_sec = max(1, int(timeout_sec))
        self.env = env
        self.proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._next_id = 1
        self._alive = False

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW

        self.proc = subprocess.Popen(
            self.command,
            cwd=str(self.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            bufsize=-1,
            env=self.env,
        )
        self._alive = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        self.initialize()

    def stop(self) -> None:
        self._alive = False
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if proc and proc.stdin:
            try:
                proc.stdin.close()
            except Exception:
                pass
        if proc and proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass
        self.proc = None
        self._messages = queue.Queue()

    def is_healthy(self) -> bool:
        return bool(self.proc and self.proc.poll() is None and self._alive)

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "desktop-pet", "version": "0.1.0"},
            },
        )
        self.notify("notifications/initialized", {})
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        result = self.request("tools/list", {})
        tools = result.get("tools", [])
        return tools if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        try:
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            deadline = time.time() + self.timeout_sec
            while time.time() < deadline:
                if self.proc and self.proc.poll() is not None:
                    raise MCPProtocolError(f"mcp process exited with code {self.proc.returncode}")
                remaining = max(0.1, deadline - time.time())
                try:
                    msg = self._messages.get(timeout=remaining)
                except queue.Empty:
                    break
                if not isinstance(msg, dict):
                    continue
                if msg.get("id") != req_id:
                    continue
                if "error" in msg:
                    error = msg.get("error")
                    raise MCPProtocolError(str(error))
                result = msg.get("result")
                return result if isinstance(result, dict) else {"result": result}
            raise TimeoutError(f"mcp request timeout: {method}")
        except (BrokenPipeError, OSError, TimeoutError, MCPProtocolError):
            self.stop()
            raise

    def _send(self, payload: dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise MCPProtocolError("mcp process is not running")
        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(raw)
        self.proc.stdin.flush()

    def _reader_loop(self) -> None:
        try:
            if not self.proc or not self.proc.stdout:
                return
            stream = self.proc.stdout
            while self._alive:
                first = stream.readline()
                if not first:
                    self._alive = False
                    return
                stripped = first.strip()
                if not stripped:
                    continue
                text = stripped.decode("utf-8", errors="ignore")

                # Support standard MCP JSON line protocol
                if text.startswith("{"):
                    try:
                        message = json.loads(text)
                    except Exception:
                        message = None
                    if isinstance(message, dict):
                        self._messages.put(message)
                    continue

                # Fallback: support Content-Length framed messages
                if text.lower().startswith("content-length:"):
                    headers: dict[str, str] = {}
                    if ":" in text:
                        k, v = text.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                    while True:
                        line = stream.readline()
                        if not line:
                            self._alive = False
                            return
                        if line in (b"\r\n", b"\n"):
                            break
                        h = line.decode("ascii", errors="ignore").strip()
                        if ":" in h:
                            k, v = h.split(":", 1)
                            headers[k.strip().lower()] = v.strip()
                    length = int(headers.get("content-length", "0") or "0")
                    if length <= 0:
                        continue
                    body = stream.read(length)
                    if not body:
                        self._alive = False
                        return
                    try:
                        message = json.loads(body.decode("utf-8"))
                    except Exception:
                        message = None
                    if isinstance(message, dict):
                        self._messages.put(message)
                    continue
        except Exception:
            self._alive = False
