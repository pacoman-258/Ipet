from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote, urljoin, urlparse, urlunparse

import httpx
import websockets
import yaml


DEFAULT_HERMES_CONFIG: dict[str, Any] = {
    "enabled": False,
    "auto_start": False,
    "command": [],
    "cwd": "",
    "base_url": "http://127.0.0.1:9119",
    "health_path": "/api/status",
    "startup_timeout_sec": 20,
}


@dataclass(frozen=True)
class HermesConfig:
    enabled: bool
    auto_start: bool
    command: list[str]
    cwd: str
    base_url: str
    health_path: str
    startup_timeout_sec: float

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "auto_start": self.auto_start,
            "command": list(self.command),
            "cwd": self.cwd,
            "base_url": self.base_url,
            "health_path": self.health_path,
            "startup_timeout_sec": self.startup_timeout_sec,
        }


def _normalize_command(value: Any) -> list[str]:
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return []


def normalize_hermes_config(raw: Any, *, root_dir: Path | None = None) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    merged = {**DEFAULT_HERMES_CONFIG, **data}
    base_url = str(merged.get("base_url") or DEFAULT_HERMES_CONFIG["base_url"]).strip()
    health_path = str(merged.get("health_path") or DEFAULT_HERMES_CONFIG["health_path"]).strip()
    if not health_path.startswith("/"):
        health_path = f"/{health_path}"
    cwd = str(merged.get("cwd") or "").strip()
    if cwd and root_dir is not None:
        cwd_path = Path(cwd)
        if not cwd_path.is_absolute():
            cwd = str((root_dir / cwd_path).resolve())
    try:
        startup_timeout_sec = float(merged.get("startup_timeout_sec", DEFAULT_HERMES_CONFIG["startup_timeout_sec"]))
    except Exception:
        startup_timeout_sec = float(DEFAULT_HERMES_CONFIG["startup_timeout_sec"])
    startup_timeout_sec = max(0.5, startup_timeout_sec)
    return {
        "enabled": bool(merged.get("enabled", False)),
        "auto_start": bool(merged.get("auto_start", False)),
        "command": _normalize_command(merged.get("command")),
        "cwd": cwd,
        "base_url": base_url.rstrip("/") or DEFAULT_HERMES_CONFIG["base_url"],
        "health_path": health_path,
        "startup_timeout_sec": startup_timeout_sec,
    }


def hermes_config_from_raw(raw: Any, *, root_dir: Path | None = None) -> HermesConfig:
    cfg = normalize_hermes_config(raw, root_dir=root_dir)
    return HermesConfig(
        enabled=bool(cfg["enabled"]),
        auto_start=bool(cfg["auto_start"]),
        command=list(cfg["command"]),
        cwd=str(cfg["cwd"]),
        base_url=str(cfg["base_url"]),
        health_path=str(cfg["health_path"]),
        startup_timeout_sec=float(cfg["startup_timeout_sec"]),
    )


def _join_url(base_url: str, path: str) -> str:
    return urljoin(f"{str(base_url or '').rstrip('/')}/", str(path or "").lstrip("/"))


class HermesUnavailable(RuntimeError):
    pass


_TOKEN_RE = re.compile(r'window\.__HERMES_SESSION_TOKEN__="([^"]+)"')
_DASHBOARD_TOKEN_CACHE: dict[str, str] = {}
_TUI_SESSIONS: dict[tuple[str, str], "_HermesTuiSession"] = {}
_MODEL_ALIASES = {
    "hermes-agent": "gpt-5.4",
    "hermes-deep": "gpt-5.4",
    "hermes-fast": "gpt-5.4-mini",
}


def _ws_url(base_url: str, path: str, token: str) -> str:
    parsed = urlparse(_join_url(base_url, path))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    query = f"token={quote(token, safe='')}"
    return urlunparse((scheme, parsed.netloc, parsed.path, "", query, ""))


def _dashboard_headers(token: str) -> dict[str, str]:
    return {"X-Hermes-Session-Token": token}


def _trust_env_for_base_url(base_url: str) -> bool:
    host = (urlparse(str(base_url or "")).hostname or "").lower()
    return host not in {"127.0.0.1", "localhost", "::1"}


async def _discover_dashboard_token(base_url: str, *, force: bool = False) -> str:
    base = str(base_url or "").rstrip("/")
    if not force and base in _DASHBOARD_TOKEN_CACHE:
        return _DASHBOARD_TOKEN_CACHE[base]
    async with httpx.AsyncClient(timeout=5.0, trust_env=_trust_env_for_base_url(base)) as client:
        response = await client.get(_join_url(base, "/"))
    if response.status_code >= 400:
        raise HermesUnavailable(f"Hermes dashboard token discovery failed with HTTP {response.status_code}.")
    match = _TOKEN_RE.search(response.text or "")
    if not match:
        raise HermesUnavailable(
            "Hermes dashboard session token was not found. Start Hermes with "
            "`hermes dashboard --no-open --tui` and point hermes.base_url at the dashboard port."
        )
    token = match.group(1)
    _DASHBOARD_TOKEN_CACHE[base] = token
    return token


def _normalize_session_id(value: Any) -> str:
    text = str(value or "").strip() or "default"
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", text)[:96] or "default"


def _normalize_tui_model(value: Any) -> str:
    model = str(value or "").strip()
    if not model:
        return ""
    return _MODEL_ALIASES.get(model, model)


def _normalize_skill_payload(raw: Any) -> dict[str, Any]:
    skills = raw if isinstance(raw, list) else (raw.get("skills") if isinstance(raw, dict) else [])
    rows: list[dict[str, Any]] = []
    for item in skills or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "").strip()
        if not name:
            continue
        row = dict(item)
        row.setdefault("id", name)
        row.setdefault("skill_id", name)
        row.setdefault("enabled", True)
        row.setdefault("storage_scope", "Hermes")
        rows.append(row)
    return {
        "ok": True,
        "runtime": "hermes",
        "skills": rows,
        "storage": {"runtime": "Hermes Agent", "proxied": True, "legacy": False},
    }


def _mcp_servers_from_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    servers = config.get("mcp_servers") if isinstance(config, dict) else {}
    if not isinstance(servers, dict):
        return []
    rows: list[dict[str, Any]] = []
    for name, server in sorted(servers.items()):
        cfg = server if isinstance(server, dict) else {}
        rows.append(
            {
                "name": str(name),
                "enabled": bool(cfg.get("enabled", True)),
                "transport": cfg.get("transport") or ("stdio" if cfg.get("command") else "http"),
                "command": cfg.get("command", ""),
                "args": cfg.get("args") or [],
                "url": cfg.get("url") or cfg.get("endpoint") or "",
                "env": cfg.get("env") or {},
                "config": cfg,
                "storage_scope": "Hermes",
            }
        )
    return rows


def _normalize_mcp_payload(config: dict[str, Any]) -> dict[str, Any]:
    servers = _mcp_servers_from_config(config)
    return {
        "ok": True,
        "runtime": "hermes",
        "servers": servers,
        "storage": {"runtime": "Hermes Agent", "proxied": True, "legacy": False},
    }


def _hermes_diagnostics_summary() -> dict[str, Any]:
    cfg_path = Path.home() / ".hermes" / "config.yaml"
    summary: dict[str, Any] = {
        "config_path": str(cfg_path),
        "config_found": cfg_path.exists(),
        "provider": "",
        "model": "",
        "api_key_configured": False,
        "base_url_host": "",
        "env_proxy_configured": any(
            bool(os.environ.get(name))
            for name in (
                "HTTPS_PROXY",
                "https_proxy",
                "HTTP_PROXY",
                "http_proxy",
                "ALL_PROXY",
                "all_proxy",
            )
        ),
    }
    if not cfg_path.exists():
        return summary
    try:
        config = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        summary["config_error"] = str(exc)
        return summary
    model_cfg = config.get("model") if isinstance(config, dict) else {}
    model_cfg = model_cfg if isinstance(model_cfg, dict) else {}
    summary["provider"] = str(model_cfg.get("provider") or "").strip()
    summary["model"] = str(model_cfg.get("default") or "").strip()
    summary["api_key_configured"] = bool(str(model_cfg.get("api_key") or "").strip())
    base_url = str(model_cfg.get("base_url") or "").strip()
    if base_url:
        summary["base_url_host"] = urlparse(base_url).netloc or urlparse(f"https://{base_url}").netloc
    return summary


def _parse_mcp_create_payload(payload: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    data = payload if isinstance(payload, dict) else {}
    raw = str(data.get("config_json") or "").strip()
    if not raw:
        raise HermesUnavailable("MCP config_json is required.")
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        raise HermesUnavailable(f"Invalid MCP JSON config: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HermesUnavailable("MCP JSON config must be an object.")

    candidates = parsed.get("mcpServers") or parsed.get("mcp_servers")
    explicit_name = str(data.get("name") or "").strip()
    if isinstance(candidates, dict) and candidates:
        if explicit_name and explicit_name in candidates and isinstance(candidates[explicit_name], dict):
            return explicit_name, dict(candidates[explicit_name])
        first_name, first_cfg = next(iter(candidates.items()))
        return explicit_name or str(first_name), dict(first_cfg if isinstance(first_cfg, dict) else {})
    if explicit_name:
        return explicit_name, parsed
    name = str(parsed.get("name") or parsed.get("id") or "").strip()
    if not name:
        raise HermesUnavailable("MCP server name is required.")
    cfg = dict(parsed)
    cfg.pop("name", None)
    cfg.pop("id", None)
    return name, cfg


class _HermesTuiSession:
    def __init__(self, client: "HermesClient", session_key: str) -> None:
        self.client = client
        self.session_key = session_key
        self.hermes_session_id = ""
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._rpc_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._connect_lock = asyncio.Lock()
        self.awaiting_approval = False

    async def ensure_connected(self) -> None:
        async with self._connect_lock:
            if self._ws is not None and not getattr(self._ws, "closed", False):
                return
            token = await self.client.dashboard_token()
            self._ws = await websockets.connect(
                _ws_url(self.client.config.base_url, "/api/ws", token),
                ping_interval=20,
                ping_timeout=20,
                proxy=None if not _trust_env_for_base_url(self.client.config.base_url) else True,
            )
            self._reader = asyncio.create_task(self._read_loop())
            if not self.hermes_session_id:
                result = await self.rpc("session.create", {"cols": 96})
                sid = str(result.get("session_id") or "").strip()
                if not sid:
                    raise HermesUnavailable("Hermes dashboard did not create a chat session.")
                self.hermes_session_id = sid

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("id") is not None:
                    try:
                        rid = int(msg.get("id"))
                    except Exception:
                        rid = -1
                    future = self._pending.pop(rid, None)
                    if future is not None and not future.done():
                        if msg.get("error"):
                            err = msg["error"]
                            future.set_exception(HermesUnavailable(str(err.get("message") or err)))
                        else:
                            future.set_result(msg.get("result") or {})
                    continue
                if msg.get("method") == "event":
                    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
                    sid = str(params.get("session_id") or "")
                    if not sid or not self.hermes_session_id or sid == self.hermes_session_id:
                        await self._events.put(params)
        except Exception as exc:
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(HermesUnavailable(f"Hermes dashboard WebSocket closed: {exc}"))
            self._pending.clear()
        finally:
            self._ws = None
            self.hermes_session_id = ""

    async def rpc(self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> dict[str, Any]:
        if self._ws is None:
            await self.ensure_connected()
        self._rpc_id += 1
        rid = self._rpc_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[rid] = future
        await self._ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}))
        return await asyncio.wait_for(future, timeout=timeout or self.client.timeout_sec)

    async def drain_events(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def submit(self, payload: dict[str, Any]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        await self.ensure_connected()
        await self.drain_events()
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HermesUnavailable("chat text cannot be empty.")
        model = _normalize_tui_model(payload.get("model"))
        if model:
            result = await self.rpc(
                "config.set",
                {"session_id": self.hermes_session_id, "key": "model", "value": model},
                timeout=10.0,
            )
            warning = str(result.get("warning") or "").strip()
            if warning:
                yield "phase", {"phase": "status", "text": warning, "speaker": "pet"}
        yield "meta", {"runtime": "hermes", "topic_id": self.session_key, "turn_id": self.hermes_session_id}
        await self.rpc("prompt.submit", {"session_id": self.hermes_session_id, "text": text}, timeout=5.0)
        async for event in self._consume_until_terminal():
            yield event

    async def approve(self, approved: bool, user_text: str = "") -> AsyncIterator[tuple[str, dict[str, Any]]]:
        await self.ensure_connected()
        choice = "once" if approved else "deny"
        await self.rpc(
            "approval.respond",
            {"session_id": self.hermes_session_id, "choice": choice, "all": False},
            timeout=5.0,
        )
        self.awaiting_approval = False
        if user_text.strip():
            yield "phase", {"phase": "action", "text": user_text.strip(), "speaker": "user"}
        async for event in self._consume_until_terminal():
            yield event

    async def _consume_until_terminal(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        while True:
            params = await self._events.get()
            event = _adapt_tui_event(params, topic_id=self.session_key, turn_id=self.hermes_session_id)
            if event is None:
                continue
            name, data = event
            yield name, data
            if name in {"done", "error"}:
                return
            if name == "approval_required":
                self.awaiting_approval = True
                return


def _adapt_tui_event(params: dict[str, Any], *, topic_id: str, turn_id: str) -> tuple[str, dict[str, Any]] | None:
    kind = str(params.get("type") or "").strip()
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    if kind in {"gateway.ready", "session.info"}:
        return None
    if kind == "message.start":
        return "phase", {"phase": "action", "text": "Hermes is working.", "speaker": "pet"}
    if kind == "message.delta":
        return "token", {"delta": str(payload.get("text") or "")}
    if kind == "message.complete":
        return "done", {"text": str(payload.get("text") or ""), "topic_id": topic_id, "turn_id": turn_id}
    if kind == "approval.request":
        command = str(payload.get("command") or "")
        description = str(payload.get("description") or "Hermes wants to run a protected tool action.")
        tool = {"name": command or "approval", "description": description, "arguments": dict(payload)}
        return "approval_required", {
            "turn_id": turn_id,
            "phase": "action",
            "text": description,
            "tools": [tool],
            "speaker": "pet",
        }
    if kind in {"thinking.delta", "reasoning.delta", "reasoning.available"}:
        return "phase", {"phase": "thought", "text": str(payload.get("text") or ""), "speaker": "pet"}
    if kind in {"tool.start", "tool.generating", "tool.progress"}:
        text = str(payload.get("context") or payload.get("preview") or payload.get("name") or "")
        return "phase", {"phase": "action", "text": text, "speaker": "pet", "tool": payload}
    if kind == "tool.complete":
        text = str(payload.get("summary") or payload.get("name") or "Tool completed.")
        return "phase", {"phase": "action", "text": text, "speaker": "pet", "tool": payload}
    if kind == "status.update":
        return "phase", {"phase": str(payload.get("kind") or "status"), "text": str(payload.get("text") or ""), "speaker": "pet"}
    if kind == "error":
        return "error", {"message": str(payload.get("message") or "Hermes dashboard error."), "runtime": "hermes"}
    return "phase", {"phase": kind or "event", "text": str(payload.get("text") or payload.get("message") or ""), "speaker": "pet"}


class HermesClient:
    def __init__(self, config: HermesConfig, *, timeout_sec: float = 30.0) -> None:
        self.config = config
        self.timeout_sec = float(timeout_sec)

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise HermesUnavailable("Hermes Agent is disabled or not configured.")
        if not self.config.base_url:
            raise HermesUnavailable("Hermes Agent base_url is empty.")

    async def dashboard_token(self, *, force: bool = False) -> str:
        self._require_enabled()
        return await _discover_dashboard_token(self.config.base_url, force=force)

    async def status(self) -> dict[str, Any]:
        config = self.config.to_public_dict()
        if not self.config.enabled:
            return {
                "ok": False,
                "configured": False,
                "available": False,
                "detail": "Hermes Agent is disabled or not configured.",
                "config": config,
                "diagnostics": _hermes_diagnostics_summary(),
            }
        url = _join_url(self.config.base_url, self.config.health_path)
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=_trust_env_for_base_url(self.config.base_url)) as client:
                response = await client.get(url)
            payload: Any
            try:
                payload = response.json()
            except Exception:
                payload = {"text": response.text[:500]}
            ok = 200 <= response.status_code < 300
            return {
                "ok": ok,
                "configured": True,
                "available": ok,
                "status_code": response.status_code,
                "detail": "" if ok else f"Hermes health check returned HTTP {response.status_code}.",
                "config": config,
                "health": payload if isinstance(payload, dict) else {"value": payload},
                "diagnostics": _hermes_diagnostics_summary(),
            }
        except Exception as exc:
            return {
                "ok": False,
                "configured": True,
                "available": False,
                "detail": f"Hermes health check failed: {exc}",
                "config": config,
                "diagnostics": _hermes_diagnostics_summary(),
            }

    async def request_json(self, method: str, path: str, *, json_payload: Any | None = None) -> dict[str, Any]:
        self._require_enabled()
        normalized_path = "/" + str(path or "").lstrip("/")
        method_upper = method.upper()
        if normalized_path == "/api/skills" and method_upper == "GET":
            return _normalize_skill_payload(await self._dashboard_json("GET", "/api/skills"))
        if normalized_path == "/api/skills/import-git" and method_upper == "POST":
            query = ""
            if isinstance(json_payload, dict):
                query = str(json_payload.get("url") or json_payload.get("repo_url") or json_payload.get("name") or "").strip()
            if not query:
                raise HermesUnavailable("Hermes skill install requires a URL, repo_url, or skill name.")
            result = await self._rpc_once("skills.manage", {"action": "install", "query": query}, timeout=max(45.0, self.timeout_sec))
            return {"ok": True, "runtime": "hermes", "skill": result}
        if normalized_path == "/api/skills/import-local" and method_upper == "POST":
            raise HermesUnavailable("Hermes dashboard does not expose local skill import; install a hub/Git skill instead.")
        if normalized_path == "/api/skills/delete" and method_upper == "POST":
            name = ""
            if isinstance(json_payload, dict):
                name = str(json_payload.get("skill_id") or json_payload.get("id") or json_payload.get("name") or "").strip()
            if not name:
                raise HermesUnavailable("Hermes skill delete requires a skill name.")
            result = await self._rpc_once("slash.exec", {"command": f"/skills uninstall {name} --now"}, timeout=max(45.0, self.timeout_sec))
            return {"ok": True, "runtime": "hermes", "deleted": name, "result": result}
        if normalized_path in {"/api/mcp/servers", "/api/mcp/health"} and method_upper == "GET":
            config = await self._get_dashboard_config()
            payload = _normalize_mcp_payload(config)
            if normalized_path.endswith("/health"):
                payload["online"] = len(payload["servers"])
            return payload
        if normalized_path == "/api/mcp/create-config" and method_upper == "POST":
            name, server_cfg = _parse_mcp_create_payload(json_payload if isinstance(json_payload, dict) else {})
            config = await self._get_dashboard_config()
            config.setdefault("mcp_servers", {})[name] = server_cfg
            await self._put_dashboard_config(config)
            return {"ok": True, "runtime": "hermes", "name": name, "server": server_cfg}
        if normalized_path == "/api/mcp/delete" and method_upper == "POST":
            name = str((json_payload or {}).get("name") if isinstance(json_payload, dict) else "").strip()
            config = await self._get_dashboard_config()
            removed = None
            if name and isinstance(config.get("mcp_servers"), dict):
                removed = config["mcp_servers"].pop(name, None)
            await self._put_dashboard_config(config)
            return {"ok": True, "runtime": "hermes", "deleted": name, "removed": removed is not None}
        if normalized_path == "/api/mcp/toggle" and method_upper == "POST":
            name = str((json_payload or {}).get("name") if isinstance(json_payload, dict) else "").strip()
            enabled = bool((json_payload or {}).get("enabled") if isinstance(json_payload, dict) else True)
            config = await self._get_dashboard_config()
            servers = config.setdefault("mcp_servers", {})
            if not name or name not in servers or not isinstance(servers[name], dict):
                raise HermesUnavailable(f"Hermes MCP server not found: {name}")
            servers[name]["enabled"] = enabled
            await self._put_dashboard_config(config)
            return {"ok": True, "runtime": "hermes", "name": name, "enabled": enabled}
        if normalized_path == "/api/mcp/reload" and method_upper == "POST":
            result = await self._rpc_once("reload.mcp", {}, timeout=max(45.0, self.timeout_sec))
            return {"ok": True, "runtime": "hermes", "result": result}
        return await self._dashboard_json(method_upper, normalized_path, json_payload=json_payload)

    async def stream_sse(self, path: str, payload: dict[str, Any]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        self._require_enabled()
        normalized_path = "/" + str(path or "").lstrip("/")
        if normalized_path == "/api/chat/stream":
            session = self._tui_session(_normalize_session_id(payload.get("session_id")))
            async for event in session.submit(payload):
                yield event
            return
        if normalized_path == "/api/chat/approval":
            turn_id = str(payload.get("turn_id") or "").strip()
            session = self._session_for_turn(turn_id)
            if session is None:
                raise HermesUnavailable("pending Hermes approval turn not found or already handled.")
            async for event in session.approve(bool(payload.get("approved", True)), str(payload.get("user_text") or "")):
                yield event
            return
        url = _join_url(self.config.base_url, path)
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, json=payload) as response:
                if response.status_code >= 400:
                    text = await response.aread()
                    raise HermesUnavailable(
                        f"Hermes stream request failed with HTTP {response.status_code}: "
                        f"{text.decode('utf-8', errors='replace')[:500]}"
                    )
                async for event, data in _iter_sse_response(response):
                    yield _adapt_hermes_event(event, data)

    def _tui_session(self, session_id: str) -> _HermesTuiSession:
        key = (self.config.base_url, session_id)
        session = _TUI_SESSIONS.get(key)
        if session is None:
            session = _HermesTuiSession(self, session_id)
            _TUI_SESSIONS[key] = session
        else:
            session.client = self
        return session

    def _session_for_turn(self, turn_id: str) -> _HermesTuiSession | None:
        for session in _TUI_SESSIONS.values():
            if session.hermes_session_id == turn_id:
                session.client = self
                return session
        return None

    async def _rpc_once(self, method: str, params: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        session = self._tui_session("_ipet_admin")
        await session.ensure_connected()
        rpc_params = dict(params)
        if method in {"reload.mcp", "slash.exec"}:
            rpc_params.setdefault("session_id", session.hermes_session_id)
        return await session.rpc(method, rpc_params, timeout=timeout or self.timeout_sec)

    async def _dashboard_json(
        self,
        method: str,
        path: str,
        *,
        json_payload: Any | None = None,
        retry_token: bool = True,
    ) -> Any:
        token = await self.dashboard_token()
        url = _join_url(self.config.base_url, path)
        async with httpx.AsyncClient(timeout=self.timeout_sec, trust_env=_trust_env_for_base_url(self.config.base_url)) as client:
            response = await client.request(method.upper(), url, json=json_payload, headers=_dashboard_headers(token))
        if response.status_code == 401 and retry_token:
            token = await self.dashboard_token(force=True)
            async with httpx.AsyncClient(timeout=self.timeout_sec, trust_env=_trust_env_for_base_url(self.config.base_url)) as client:
                response = await client.request(method.upper(), url, json=json_payload, headers=_dashboard_headers(token))
        try:
            payload = response.json()
        except Exception:
            payload = {"detail": response.text}
        if response.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise HermesUnavailable(str(detail or f"Hermes request failed with HTTP {response.status_code}."))
        return payload

    async def _get_dashboard_config(self) -> dict[str, Any]:
        raw = await self._dashboard_json("GET", "/api/config/raw")
        yaml_text = str(raw.get("yaml") or "") if isinstance(raw, dict) else ""
        config = yaml.safe_load(yaml_text) if yaml_text.strip() else {}
        return config if isinstance(config, dict) else {}

    async def _put_dashboard_config(self, config: dict[str, Any]) -> None:
        text = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        await self._dashboard_json("PUT", "/api/config/raw", json_payload={"yaml_text": text})


async def _iter_sse_response(response: httpx.Response) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    event = "message"
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                yield event, _loads_data("\n".join(data_lines))
            event = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip() or "message"
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue
        item = _loads_data(line)
        if isinstance(item, dict):
            raw_event = str(item.get("event") or item.get("type") or "message").strip() or "message"
            yield raw_event, item
    if data_lines:
        yield event, _loads_data("\n".join(data_lines))


def _loads_data(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {"delta": text}
    return data if isinstance(data, dict) else {"data": data}


def _adapt_hermes_event(event: str, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    name = str(event or data.get("event") or data.get("type") or "message").strip()
    payload = dict(data or {})
    payload.pop("event", None)
    if name in {"meta", "phase", "approval_required", "segment", "display_segment", "token", "done", "error"}:
        return name, payload
    if name in {"final_delta", "delta", "message"}:
        delta = str(payload.get("delta") or payload.get("text") or payload.get("content") or "")
        return "token", {"delta": delta}
    if name in {"final", "final_reply"}:
        text = str(payload.get("text") or payload.get("content") or payload.get("message") or "")
        return "done", {"text": text}
    if name in {"approval", "tool_approval"}:
        return "approval_required", payload
    return "phase", {
        "phase": str(payload.get("phase") or name),
        "text": str(payload.get("text") or payload.get("message") or ""),
        "speaker": str(payload.get("speaker") or "pet"),
    }
