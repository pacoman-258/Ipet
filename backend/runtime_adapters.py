from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx

from .hermes import HermesClient, HermesUnavailable, hermes_config_from_raw
from .models import ChatApprovalRequest, ChatStreamRequest
from .runtime_config import (
    RUNTIME_ASTRBOT,
    RUNTIME_HERMES,
    ephemeral_session_id,
    local_service_health_url,
    redact_runtime_config,
    resolved_astrbot_api_key,
    trust_env_for_base_url,
)


class RuntimeUnavailable(RuntimeError):
    pass


class RuntimeUnsupported(RuntimeUnavailable):
    pass


def _request_payload(req: Any) -> dict[str, Any]:
    if hasattr(req, "model_dump"):
        return req.model_dump()
    if hasattr(req, "dict"):
        return req.dict()
    return dict(req or {})


def _unsupported_payload(runtime: str, kind: str, detail: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "ok": False,
        "runtime": runtime,
        "unsupported": True,
        "detail": detail,
    }
    payload.update(extra)
    return payload


class HermesRuntimeAdapter:
    runtime_id = RUNTIME_HERMES

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.client = HermesClient(hermes_config_from_raw(config))

    async def status(self) -> dict[str, Any]:
        status = await self.client.status()
        status.setdefault("runtime", RUNTIME_HERMES)
        return status

    async def request_json(self, method: str, path: str, *, json_payload: Any | None = None) -> dict[str, Any]:
        return await self.client.request_json(method, path, json_payload=json_payload)

    async def stream_sse(self, path: str, payload: dict[str, Any]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        async for event in self.client.stream_sse(path, payload):
            yield event


class AstrBotRuntimeAdapter:
    runtime_id = RUNTIME_ASTRBOT

    def __init__(self, config: dict[str, Any], *, timeout_sec: float = 30.0) -> None:
        self.config = config
        self.timeout_sec = float(timeout_sec)

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or "").strip().rstrip("/")

    @property
    def username(self) -> str:
        return str(self.config.get("username") or "ipet").strip() or "ipet"

    def _require_enabled(self) -> None:
        if not bool(self.config.get("enabled", False)):
            raise RuntimeUnavailable("AstrBot runtime is disabled or not configured.")
        if not self.base_url:
            raise RuntimeUnavailable("AstrBot base_url is empty.")

    def _api_key(self) -> str:
        return resolved_astrbot_api_key(self.config)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json, text/event-stream"}
        key = self._api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
            headers["X-API-Key"] = key
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{str(path or '').lstrip('/')}"

    async def status(self) -> dict[str, Any]:
        public_config = redact_runtime_config({"active": RUNTIME_ASTRBOT, "adapters": {RUNTIME_ASTRBOT: self.config}})[
            "adapters"
        ][RUNTIME_ASTRBOT]
        status: dict[str, Any] = {
            "ok": False,
            "runtime": RUNTIME_ASTRBOT,
            "configured": bool(self.config.get("enabled", False)),
            "available": False,
            "api_authenticated": False,
            "api_key_configured": bool(self._api_key()),
            "username": self.username,
            "base_url": self.base_url,
            "config": public_config,
            "napcat_qq": self._napcat_status(),
            "detail": "",
        }
        if not bool(self.config.get("enabled", False)):
            status["detail"] = "AstrBot runtime is disabled or not configured."
            return status
        health_url = local_service_health_url(self.config)
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=trust_env_for_base_url(self.base_url)) as client:
                response = await client.get(health_url)
            status["available"] = 200 <= response.status_code < 500
            status["health_status_code"] = response.status_code
            status["ok"] = 200 <= response.status_code < 300
            if response.status_code >= 400:
                status["detail"] = f"AstrBot health check returned HTTP {response.status_code}."
        except Exception as exc:
            status["detail"] = f"AstrBot health check failed: {exc}"
            return status

        if not self._api_key():
            status["detail"] = status["detail"] or "AstrBot is reachable, but no API key is configured."
            return status
        try:
            await self._request_json("GET", "/api/v1/chat/sessions", params={"username": self.username})
            status["api_authenticated"] = True
            status["ok"] = bool(status["available"])
            status["detail"] = status["detail"] or "AstrBot API is reachable."
        except Exception as exc:
            status["detail"] = f"AstrBot API authentication check failed: {exc}"
        return status

    def _napcat_status(self) -> dict[str, Any]:
        cfg = self.config.get("napcat_qq") if isinstance(self.config.get("napcat_qq"), dict) else {}
        return {
            "enabled": bool(cfg.get("enabled", False)),
            "runtime": RUNTIME_ASTRBOT,
            "managed_by": "AstrBot",
            "adapter_type": str(cfg.get("adapter_type") or "OneBot v11"),
            "adapter_name": str(cfg.get("adapter_name") or "aiocqhttp"),
            "reverse_ws_url": str(cfg.get("reverse_ws_url") or "ws://127.0.0.1:6199/ws"),
            "reverse_ws_host": str(cfg.get("reverse_ws_host") or "127.0.0.1"),
            "reverse_ws_port": int(cfg.get("reverse_ws_port") or 6199),
            "webui_url": str(cfg.get("webui_url") or ""),
            "token_configured": bool(cfg.get("token_configured", False)),
            "guide_url": str(cfg.get("guide_url") or "https://docs.astrbot.app/platform/aiocqhttp.html"),
            "status": "not_verified_by_ipet",
            "detail": "NapCatQQ is managed by AstrBot; Ipet only displays the reverse WebSocket connection guide.",
        }

    async def request_json(self, method: str, path: str, *, json_payload: Any | None = None) -> dict[str, Any]:
        self._require_enabled()
        normalized = "/" + str(path or "").lstrip("/")
        method_upper = str(method or "GET").upper()
        if normalized == "/api/chat/topics" and method_upper == "GET":
            return await self._list_sessions()
        if normalized == "/api/chat/topics" and method_upper == "POST":
            return self._create_ephemeral_topic(json_payload if isinstance(json_payload, dict) else {})
        if normalized.startswith("/api/chat/topics/"):
            topic_id = normalized.rsplit("/", 1)[-1]
            if method_upper == "GET":
                return {
                    "ok": True,
                    "runtime": RUNTIME_ASTRBOT,
                    "unsupported": True,
                    "topic": {"topic_id": topic_id, "title": topic_id},
                    "messages": [],
                    "detail": "AstrBot HTTP API does not expose topic detail in Ipet v1.",
                }
            return {
                "ok": True,
                "runtime": RUNTIME_ASTRBOT,
                "unsupported": True,
                "deleted": quote(topic_id, safe=""),
                "detail": "AstrBot HTTP API does not expose topic deletion in Ipet v1.",
            }
        if normalized == "/api/skills" and method_upper == "GET":
            return self._skills_payload()
        if normalized.startswith("/api/skills/"):
            return _unsupported_payload(
                RUNTIME_ASTRBOT,
                "skills",
                "AstrBot plugins and skills are managed in AstrBot WebUI.",
                skills=[],
            )
        if normalized in {"/api/mcp/servers", "/api/mcp/health"} and method_upper == "GET":
            payload = self._mcp_payload()
            if normalized.endswith("/health"):
                payload["online"] = 0
            return payload
        if normalized.startswith("/api/mcp/"):
            return _unsupported_payload(
                RUNTIME_ASTRBOT,
                "mcp",
                "AstrBot MCP and plugin configuration are managed in AstrBot WebUI.",
                servers=[],
                online=0,
            )
        return await self._request_json(method_upper, normalized, json_payload=json_payload)

    async def stream_sse(self, path: str, payload: dict[str, Any]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        self._require_enabled()
        normalized = "/" + str(path or "").lstrip("/")
        if normalized == "/api/chat/approval":
            yield "error", {"message": "AstrBot runtime does not support Ipet approval forwarding in v1.", "runtime": RUNTIME_ASTRBOT}
            return
        if normalized != "/api/chat/stream":
            raise RuntimeUnsupported(f"AstrBot does not support SSE path: {normalized}")

        text = str(payload.get("text") or "").strip()
        if not text:
            raise RuntimeUnavailable("chat text cannot be empty.")
        session_id = ephemeral_session_id(payload.get("session_id"))
        yield "meta", {
            "runtime": RUNTIME_ASTRBOT,
            "topic_id": session_id,
            "session_id": session_id,
            "chat_mode": "chat",
        }

        body = {
            "username": self.username,
            "session_id": session_id,
            "message": text,
            "enable_streaming": True,
        }
        full = ""
        yielded_done = False
        async with httpx.AsyncClient(timeout=None, trust_env=trust_env_for_base_url(self.base_url)) as client:
            async with client.stream("POST", self._url("/api/v1/chat"), json=body, headers=self._headers()) as response:
                if response.status_code >= 400:
                    detail = await response.aread()
                    raise RuntimeUnavailable(
                        f"AstrBot chat request failed with HTTP {response.status_code}: "
                        f"{detail.decode('utf-8', errors='replace')[:500]}"
                    )
                async for event, data in _iter_event_stream(response):
                    name, mapped = _adapt_astrbot_event(event, data, session_id=session_id)
                    if name == "token":
                        delta = _normalize_stream_delta(
                            full,
                            str(mapped.get("delta") or ""),
                            cumulative=bool(mapped.get("cumulative", False)),
                        )
                        if delta:
                            full += delta
                            mapped["delta"] = delta
                            mapped.pop("cumulative", None)
                            yield name, mapped
                    elif name == "done":
                        yielded_done = True
                        text_done = str(mapped.get("text") or "") or full
                        yield "done", {"text": text_done, "topic_id": session_id, "runtime": RUNTIME_ASTRBOT}
                        return
                    elif name == "error":
                        yield name, mapped
                        return
                    elif name == "meta":
                        yield name, mapped
                    else:
                        if name == "phase" and not str(mapped.get("text") or "").strip():
                            continue
                        yield name, mapped
        if not yielded_done:
            yield "done", {"text": full, "topic_id": session_id, "runtime": RUNTIME_ASTRBOT}

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_payload: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_sec, trust_env=trust_env_for_base_url(self.base_url)) as client:
            response = await client.request(method.upper(), self._url(path), json=json_payload, params=params, headers=self._headers())
        try:
            payload = response.json()
        except Exception:
            payload = {"detail": response.text}
        if response.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise RuntimeUnavailable(str(detail or f"AstrBot request failed with HTTP {response.status_code}."))
        return payload if isinstance(payload, dict) else {"data": payload}

    async def _list_sessions(self) -> dict[str, Any]:
        payload = await self._request_json("GET", "/api/v1/chat/sessions", params={"username": self.username})
        sessions = _extract_list(payload, "sessions")
        topics: list[dict[str, Any]] = []
        for item in sessions:
            if not isinstance(item, dict):
                continue
            topic_id = str(
                item.get("session_id")
                or item.get("topic_id")
                or item.get("id")
                or item.get("uuid")
                or ""
            ).strip()
            if not topic_id:
                continue
            title = str(item.get("title") or item.get("name") or item.get("summary") or topic_id).strip() or topic_id
            topics.append(
                {
                    "topic_id": topic_id,
                    "session_id": topic_id,
                    "title": title,
                    "updated_at": item.get("updated_at") or item.get("last_active_at") or item.get("created_at") or "",
                    "message_count": item.get("message_count") or item.get("turn_count") or 0,
                    "runtime": RUNTIME_ASTRBOT,
                    "raw": item,
                }
            )
        return {"ok": True, "runtime": RUNTIME_ASTRBOT, "enabled": True, "topics": topics, "raw": payload}

    def _create_ephemeral_topic(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = ephemeral_session_id(payload.get("session_id") or payload.get("topic_id") or payload.get("title"))
        return {
            "ok": True,
            "runtime": RUNTIME_ASTRBOT,
            "ephemeral": True,
            "topic": {"topic_id": session_id, "session_id": session_id, "title": str(payload.get("title") or session_id)},
            "detail": "AstrBot will create the session on the first chat turn.",
        }

    def _skills_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "runtime": RUNTIME_ASTRBOT,
            "skills": [],
            "detail": "AstrBot plugins and skills are managed in AstrBot WebUI.",
            "storage": {"runtime": "AstrBot", "proxied": False, "legacy": False},
        }

    def _mcp_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "runtime": RUNTIME_ASTRBOT,
            "servers": [],
            "online": 0,
            "detail": "AstrBot MCP and plugin configuration are managed in AstrBot WebUI.",
            "storage": {"runtime": "AstrBot", "proxied": False, "legacy": False},
        }


def create_runtime_adapter(runtime_config: dict[str, Any], runtime_id: str | None = None):
    adapters = runtime_config.get("adapters") if isinstance(runtime_config.get("adapters"), dict) else {}
    active = str(runtime_id or runtime_config.get("active") or RUNTIME_HERMES).strip().lower() or RUNTIME_HERMES
    if active == RUNTIME_ASTRBOT:
        return AstrBotRuntimeAdapter(adapters.get(RUNTIME_ASTRBOT) if isinstance(adapters.get(RUNTIME_ASTRBOT), dict) else {})
    return HermesRuntimeAdapter(adapters.get(RUNTIME_HERMES) if isinstance(adapters.get(RUNTIME_HERMES), dict) else {})


async def _iter_event_stream(response: httpx.Response) -> AsyncIterator[tuple[str, dict[str, Any]]]:
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
            raw_event = str(item.get("event") or item.get("type") or event or "message").strip() or "message"
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


def _extract_list(payload: Any, fallback_key: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in (fallback_key, "items", "list", "data", "records", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_list(value, fallback_key)
            if nested:
                return nested
    return []


def _adapt_astrbot_event(event: str, data: dict[str, Any], *, session_id: str) -> tuple[str, dict[str, Any]]:
    name = str(event or data.get("event") or data.get("type") or "message").strip().lower()
    payload = dict(data or {})
    if name in {"error", "failed", "exception"} or payload.get("error"):
        return "error", {
            "message": str(payload.get("message") or payload.get("detail") or payload.get("error") or "AstrBot stream error."),
            "runtime": RUNTIME_ASTRBOT,
        }
    for key in ("session_id", "conversation_id", "topic_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            session_id = value
            break
    if name in {"done", "finish", "finished", "complete", "completed", "final"} or payload.get("done") is True:
        return "done", {
            "text": str(payload.get("text") or payload.get("content") or payload.get("message") or payload.get("answer") or ""),
            "topic_id": session_id,
            "runtime": RUNTIME_ASTRBOT,
        }
    delta, cumulative = _extract_text_update(payload)
    if delta:
        return "token", {"delta": delta, "runtime": RUNTIME_ASTRBOT, "cumulative": cumulative}
    if name in {"meta", "metadata"}:
        payload.setdefault("runtime", RUNTIME_ASTRBOT)
        payload.setdefault("topic_id", session_id)
        return "meta", payload
    text = str(payload.get("text") or payload.get("message") or payload.get("detail") or "")
    return "phase", {"phase": name or "event", "text": text, "speaker": "pet", "runtime": RUNTIME_ASTRBOT}


def _extract_text_update(payload: dict[str, Any]) -> tuple[str, bool]:
    for key in ("delta", "chunk"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value, False
    data = payload.get("data")
    if isinstance(data, str):
        return data, False
    if isinstance(data, dict):
        return _extract_text_update(data)
    for key in ("content", "text", "message", "answer"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value, True
    return "", False


def _normalize_stream_delta(previous_text: str, update_text: str, *, cumulative: bool) -> str:
    previous = str(previous_text or "")
    update = str(update_text or "")
    if not update:
        return ""
    if update == previous:
        return ""
    if update.startswith(previous):
        return update[len(previous) :]
    if cumulative:
        if update in previous:
            return ""
        return update
    if previous and update.startswith(previous):
        return update[len(previous) :]
    return update
