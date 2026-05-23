from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import unquote
from uuid import uuid4

import httpx

from .hermes import HermesClient, HermesUnavailable, hermes_config_from_raw
from .models import ChatApprovalRequest, ChatStreamRequest
from .runtime_contracts import RuntimeAdapter
from .runtime_config import (
    DEFAULT_RUNTIME_ID,
    RUNTIME_ASTRBOT,
    RUNTIME_HERMES,
    ephemeral_session_id,
    local_service_health_url,
    normalize_runtime_id,
    redact_runtime_config,
    resolved_astrbot_api_key,
    resolved_astrbot_dashboard_password,
    trust_env_for_base_url,
)


_LOG = logging.getLogger(__name__)
_RESERVED_ASTRBOT_VISION_ID = "ipet-vision-analysis"
_SAFE_ASTRBOT_CHAT_SESSION_ID = "ipet-chat-default"
_SAFE_ASTRBOT_CHAT_USERNAME = "ipet"
RUNTIME_MOCK = "mock"


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
        "kind": kind,
        "unsupported": True,
        "detail": detail,
    }
    payload.update(extra)
    return payload


def _is_reserved_astrbot_vision_identity(value: Any) -> bool:
    return str(value or "").strip().casefold() == _RESERVED_ASTRBOT_VISION_ID


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
        self._dashboard_tokens: dict[str, str] = {}

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or "").strip().rstrip("/")

    @property
    def username(self) -> str:
        return str(self.config.get("username") or "ipet").strip() or "ipet"

    def _safe_chat_username(self) -> str:
        username = self.username
        if _is_reserved_astrbot_vision_identity(username):
            return _SAFE_ASTRBOT_CHAT_USERNAME
        return username

    def _ordinary_chat_identity(self, requested_session_id: Any) -> tuple[str, str, dict[str, Any] | None]:
        original_username = self.username
        original_session_id = ephemeral_session_id(requested_session_id)
        username = self._safe_chat_username()
        session_id = original_session_id
        rewrote_username = username != original_username
        rewrote_session = False
        if _is_reserved_astrbot_vision_identity(original_session_id):
            session_id = _SAFE_ASTRBOT_CHAT_SESSION_ID
            rewrote_session = True
        if not rewrote_username and not rewrote_session:
            return username, session_id, None
        guard = {
            "kind": "reserved_astrbot_vision_identity",
            "reserved_identity": _RESERVED_ASTRBOT_VISION_ID,
            "rewrote_username": rewrote_username,
            "rewrote_session_id": rewrote_session,
            "safe_username": username,
            "safe_session_id": session_id,
        }
        if rewrote_session:
            guard["original_session_id"] = original_session_id
        if rewrote_username:
            guard["original_username"] = original_username
        _LOG.warning(
            "Rewriting reserved AstrBot vision identity for ordinary chat: "
            "rewrote_username=%s rewrote_session_id=%s safe_username=%s safe_session_id=%s",
            rewrote_username,
            rewrote_session,
            username,
            session_id,
        )
        return username, session_id, guard

    def _require_enabled(self) -> None:
        if not bool(self.config.get("enabled", False)):
            raise RuntimeUnavailable("AstrBot runtime is disabled or not configured.")
        if not self.base_url:
            raise RuntimeUnavailable("AstrBot base_url is empty.")

    def _api_key(self) -> str:
        return resolved_astrbot_api_key(self.config)

    @property
    def dashboard_username(self) -> str:
        return str(self.config.get("dashboard_username") or "ipet").strip() or "ipet"

    def _dashboard_password(self) -> str:
        return resolved_astrbot_dashboard_password(self.config)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json, text/event-stream"}
        key = self._api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
            headers["X-API-Key"] = key
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{str(path or '').lstrip('/')}"

    async def _upload_inline_vision_frame(self, frame: dict[str, Any]) -> str:
        max_bytes = int(frame.get("max_image_bytes") or 8_000_000)
        mime_type, image_bytes = _decode_runtime_frame_data_url(frame, max_bytes=max_bytes)
        filename = "ipet-screen.png" if mime_type == "image/png" else "ipet-screen.jpg"
        async with httpx.AsyncClient(timeout=self.timeout_sec, trust_env=trust_env_for_base_url(self.base_url)) as client:
            upload = await client.post(
                self._url("/api/v1/file"),
                files={"file": (filename, image_bytes, mime_type)},
                headers=self._headers(),
            )
        upload.raise_for_status()
        attachment_id = _extract_astrbot_attachment_id(upload.json())
        if not attachment_id:
            raise RuntimeUnavailable("AstrBot file upload did not return an attachment id")
        return attachment_id

    async def _chat_message_with_optional_inline_vision(self, text: str, payload: dict[str, Any]) -> Any:
        raw_frames = payload.get("vision_frames") if isinstance(payload.get("vision_frames"), list) else []
        frames: list[dict[str, Any]] = [item for item in raw_frames[:3] if isinstance(item, dict)]
        single_frame = payload.get("vision_frame") if isinstance(payload.get("vision_frame"), dict) else None
        if single_frame:
            single_data_url = str(single_frame.get("data_url") or "")
            existing_urls = {str(item.get("data_url") or "") for item in frames}
            if single_data_url and single_data_url not in existing_urls:
                frames.insert(0, single_frame)
        if not frames:
            return text
        message: list[dict[str, str]] = [{"type": "plain", "text": text}]
        try:
            for frame in frames[:3]:
                attachment_id = await self._upload_inline_vision_frame(frame)
                message.append({"type": "image", "attachment_id": attachment_id})
        except Exception as exc:
            return f"{text}\n\n[视觉图片上传失败：{exc}]"
        return message

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
            await self._request_json("GET", "/api/v1/chat/sessions", params={"username": self._safe_chat_username()})
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
            topic_path = normalized[len("/api/chat/topics/") :]
            if topic_path.endswith("/delete"):
                topic_path = topic_path[: -len("/delete")]
            topic_id = unquote(topic_path)
            if method_upper == "GET":
                return await self._get_session_detail(topic_id)
            if method_upper in {"DELETE", "POST"}:
                return await self._delete_session(topic_id)
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

    async def delete_runtime_session(self, session_id: str) -> dict[str, Any]:
        self._require_enabled()
        return await self._delete_session(session_id)

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
        username, session_id, runtime_guard = self._ordinary_chat_identity(payload.get("session_id"))
        meta = {
            "runtime": RUNTIME_ASTRBOT,
            "topic_id": session_id,
            "session_id": session_id,
            "chat_mode": "chat",
        }
        if runtime_guard:
            meta["runtime_guard"] = runtime_guard
        yield "meta", meta

        message = await self._chat_message_with_optional_inline_vision(text, payload)
        body = {
            "username": username,
            "session_id": session_id,
            "message": message,
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

    async def analyze_vision_frame(
        self,
        payload: dict[str, Any],
        analyzer_config: dict[str, Any] | None = None,
        *,
        http_client_factory: Any | None = None,
    ):
        from .vision_analyzer import (
            merge_analysis_into_payload,
            normalize_analyzer_config,
        )

        analyzer = normalize_analyzer_config(
            {
                "enabled": True,
                "provider": "active_runtime_vlm",
                **(analyzer_config if isinstance(analyzer_config, dict) else {}),
            }
        )
        analyzer["provider"] = "active_runtime_vlm"
        message = (
            "AstrBot chat-based vision fallback is disabled; "
            "Ipet-owned VLM/OCR analysis must provide visual evidence."
        )
        _LOG.warning(message)
        source_payload = payload if isinstance(payload, dict) else {}
        return merge_analysis_into_payload(
            source_payload,
            analyzer,
            {"observations": [], "unknowns": [message], "last_error": message},
        )

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

    async def _dashboard_headers(self, *, force_login: bool = False, username: str | None = None) -> dict[str, str]:
        token = await self._dashboard_jwt(force=force_login, username=username)
        return {"Accept": "application/json", "Authorization": f"Bearer {token}"}

    async def _dashboard_jwt(self, *, force: bool = False, username: str | None = None) -> str:
        effective_username = str(username or self.dashboard_username).strip() or self.dashboard_username
        cache_key = f"dashboard:{effective_username}"
        if self._dashboard_tokens.get(cache_key) and not force:
            return self._dashboard_tokens[cache_key]

        local_token = self._local_dashboard_jwt(effective_username)
        if local_token:
            self._dashboard_tokens[cache_key] = local_token
            return local_token

        password = self._dashboard_password()
        if not password:
            raise RuntimeUnavailable("AstrBot Dashboard password is not configured.")
        if effective_username != self.dashboard_username:
            raise RuntimeUnavailable(
                f"AstrBot Dashboard password login is configured for {self.dashboard_username}, not {effective_username}."
            )
        body = {"username": effective_username, "password": password}
        async with httpx.AsyncClient(timeout=self.timeout_sec, trust_env=trust_env_for_base_url(self.base_url)) as client:
            response = await client.post(self._url("/api/auth/login"), json=body, headers={"Accept": "application/json"})
        try:
            payload = response.json()
        except Exception:
            payload = {"detail": response.text}
        if response.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise RuntimeUnavailable(str(detail or f"AstrBot Dashboard login failed with HTTP {response.status_code}."))
        token = _extract_astrbot_dashboard_token(payload)
        if not token:
            raise RuntimeUnavailable("AstrBot Dashboard login did not return a token.")
        self._dashboard_tokens[cache_key] = token
        return token

    def _dashboard_config_path_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        configured = str(self.config.get("dashboard_config_path") or "").strip()
        if configured:
            candidates.append(Path(configured).expanduser())
        env_root = str(os.environ.get("ASTRBOT_ROOT") or "").strip()
        if env_root:
            candidates.append(Path(env_root).expanduser() / "data" / "cmd_config.json")
        cwd = str(self.config.get("cwd") or "").strip()
        if cwd:
            root = Path(cwd).expanduser()
            candidates.append(root / "data" / "cmd_config.json")
            candidates.append(root / "cmd_config.json")
        return candidates

    def _local_dashboard_jwt_secret(self) -> str:
        if not self._base_url_is_loopback():
            return ""
        for path in self._dashboard_config_path_candidates():
            try:
                if not path.exists() or not path.is_file():
                    continue
                with path.open("r", encoding="utf-8-sig") as fh:
                    payload = json.load(fh)
            except Exception:
                continue
            dashboard = payload.get("dashboard") if isinstance(payload, dict) else None
            if not isinstance(dashboard, dict):
                continue
            secret = str(dashboard.get("jwt_secret") or "").strip()
            if secret:
                return secret
        return ""

    def _base_url_is_loopback(self) -> bool:
        parsed = httpx.URL(self.base_url)
        host = str(parsed.host or "").strip().lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def _local_dashboard_jwt(self, username: str) -> str:
        secret = self._local_dashboard_jwt_secret()
        if not secret:
            return ""
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {"username": username, "exp": int(time.time()) + 7 * 24 * 60 * 60}
        signing_input = ".".join(
            [
                _jwt_b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
                _jwt_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
            ]
        )
        digest = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
        return f"{signing_input}.{_jwt_b64encode(digest)}"

    def _dashboard_auth_available_for_username(self, username: str) -> bool:
        effective_username = str(username or "").strip() or self.dashboard_username
        if self._local_dashboard_jwt_secret():
            return True
        return bool(self._dashboard_password()) and effective_username == self.dashboard_username

    def _topic_dashboard_username(self, topic: dict[str, Any]) -> str:
        return str(topic.get("creator") or self.dashboard_username).strip() or self.dashboard_username

    def _topic_history_dashboard_username(self, topic: dict[str, Any]) -> str:
        if self._local_dashboard_jwt_secret():
            return self._topic_dashboard_username(topic)
        return self.dashboard_username

    async def _dashboard_json(
        self,
        method: str,
        path: str,
        *,
        json_payload: Any | None = None,
        params: dict[str, Any] | None = None,
        dashboard_username: str | None = None,
    ) -> dict[str, Any]:
        last_payload: Any = None
        for force_login in (False, True):
            headers = await self._dashboard_headers(force_login=force_login, username=dashboard_username)
            async with httpx.AsyncClient(timeout=self.timeout_sec, trust_env=trust_env_for_base_url(self.base_url)) as client:
                response = await client.request(
                    method.upper(),
                    self._url(path),
                    json=json_payload,
                    params=params,
                    headers=headers,
                )
            try:
                payload = response.json()
            except Exception:
                payload = {"detail": response.text}
            last_payload = payload
            if response.status_code == 401 and not force_login:
                effective_username = str(dashboard_username or self.dashboard_username).strip() or self.dashboard_username
                self._dashboard_tokens.pop(f"dashboard:{effective_username}", None)
                continue
            if _astrbot_payload_status_is_error(payload):
                detail = _astrbot_payload_error_detail(payload)
                raise RuntimeUnavailable(detail or "AstrBot Dashboard returned status=error.")
            if response.status_code >= 400:
                detail = payload.get("detail") if isinstance(payload, dict) else None
                raise RuntimeUnavailable(str(detail or f"AstrBot Dashboard request failed with HTTP {response.status_code}."))
            return payload if isinstance(payload, dict) else {"data": payload}
        return last_payload if isinstance(last_payload, dict) else {"data": last_payload}

    async def _session_topics(self, *, include_ineligible: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payload = await self._request_json("GET", "/api/v1/chat/sessions", params={"username": self._safe_chat_username()})
        sessions = _extract_list(payload, "sessions")
        topics: list[dict[str, Any]] = []
        history_available = self._dashboard_auth_available_for_username(self.dashboard_username)
        for item in sessions:
            if not isinstance(item, dict):
                continue
            if not _is_astrbot_webchat_session(item):
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
            title = str(
                item.get("display_name")
                or item.get("title")
                or item.get("name")
                or item.get("summary")
                or topic_id
            ).strip() or topic_id
            creator = str(item.get("creator") or self.username).strip() or self.username
            topic_history_available = history_available or self._dashboard_auth_available_for_username(creator)
            supports_delete = self._dashboard_auth_available_for_username(creator)
            topics.append(
                {
                    "topic_id": topic_id,
                    "session_id": topic_id,
                    "title": title,
                    "preview": str(item.get("preview") or item.get("summary") or "").strip(),
                    "updated_at": item.get("updated_at") or item.get("last_active_at") or item.get("created_at") or "",
                    "created_at": item.get("created_at") or "",
                    "message_count": item.get("message_count") or item.get("turn_count") or 0,
                    "assistant_turn_count": item.get("assistant_turn_count") or item.get("turn_count") or 0,
                    "platform_id": item.get("platform_id") or "webchat",
                    "creator": creator,
                    "is_group": bool(item.get("is_group", False)),
                    "persisted": True,
                    "runtime": RUNTIME_ASTRBOT,
                    "source": RUNTIME_ASTRBOT,
                    "supports_history_detail": topic_history_available,
                    "supports_delete": supports_delete,
                    "history_unavailable": not topic_history_available,
                    **(
                        {}
                        if topic_history_available
                        else {"history_detail": "AstrBot Dashboard authentication is not configured."}
                    ),
                    "raw": item,
                }
            )
        return topics, payload

    async def _list_sessions(self) -> dict[str, Any]:
        topics, payload = await self._session_topics()
        await self._enrich_session_topics(topics)
        return {"ok": True, "runtime": RUNTIME_ASTRBOT, "enabled": True, "topics": topics, "raw": payload}

    async def _enrich_session_topics(self, topics: list[dict[str, Any]]) -> None:
        for topic in topics:
            topic_id = str(topic.get("topic_id") or "").strip()
            if not topic_id or not bool(topic.get("supports_history_detail")):
                continue
            try:
                session_payload = await self._dashboard_json(
                    "GET",
                    "/api/chat/get_session",
                    params={"session_id": topic_id},
                    dashboard_username=self._topic_history_dashboard_username(topic),
                )
            except Exception as exc:
                topic["history_unavailable"] = True
                topic["history_detail"] = f"AstrBot Dashboard history is unavailable: {exc}"
                continue
            messages = _map_astrbot_dashboard_history(_extract_astrbot_history(session_payload))
            _merge_astrbot_topic_history_summary(topic, messages)

    async def _get_session_detail(self, topic_id: str) -> dict[str, Any]:
        topic_id = str(topic_id or "").strip()
        topics, _payload = await self._session_topics(include_ineligible=True)
        topic = next((item for item in topics if isinstance(item, dict) and str(item.get("topic_id") or "") == topic_id), None)
        if topic is None:
            return _unsupported_payload(
                RUNTIME_ASTRBOT,
                "chat_topic_not_found",
                "AstrBot session is not in Ipet's webchat session list for the configured username.",
                topic_id=topic_id,
                supports_history_detail=False,
                supports_delete=False,
            )
        try:
            session_payload = await self._dashboard_json(
                "GET",
                "/api/chat/get_session",
                params={"session_id": topic_id},
                dashboard_username=self._topic_history_dashboard_username(topic),
            )
        except Exception as exc:
            return {
                "ok": True,
                "runtime": RUNTIME_ASTRBOT,
                "meta": topic,
                "topic": topic,
                "messages": [],
                "history_unavailable": True,
                "supports_history_detail": False,
                "supports_delete": bool(topic.get("supports_delete")),
                "detail": f"AstrBot Dashboard history is unavailable: {exc}",
            }
        messages = _map_astrbot_dashboard_history(_extract_astrbot_history(session_payload))
        return {
            "ok": True,
            "runtime": RUNTIME_ASTRBOT,
            "meta": topic,
            "topic": topic,
            "messages": messages,
            "supports_history_detail": True,
            "supports_delete": bool(topic.get("supports_delete")),
        }

    async def _delete_session(self, topic_id: str) -> dict[str, Any]:
        topic_id = str(topic_id or "").strip()
        topics, _payload = await self._session_topics(include_ineligible=True)
        topic = next((item for item in topics if isinstance(item, dict) and str(item.get("topic_id") or "") == topic_id), None)
        if topic is None:
            return _unsupported_payload(
                RUNTIME_ASTRBOT,
                "chat_topic_not_found",
                "AstrBot session is not in Ipet's webchat session list for the configured username.",
                topic_id=topic_id,
                supports_delete=False,
            )
        delete_username = self._topic_dashboard_username(topic)
        if not bool(topic.get("supports_delete")):
            return _unsupported_payload(
                RUNTIME_ASTRBOT,
                "chat_topic_owner_mismatch",
                "AstrBot Dashboard authentication cannot act as this session creator; delete sync is disabled for this session.",
                topic_id=topic_id,
                supports_delete=False,
            )
        await self._dashboard_json(
            "GET",
            "/api/chat/delete_session",
            params={"session_id": topic_id},
            dashboard_username=delete_username,
        )
        refreshed = await self._list_sessions()
        refreshed["deleted_topic_id"] = topic_id
        remaining = [
            item
            for item in (refreshed.get("topics") if isinstance(refreshed.get("topics"), list) else [])
            if isinstance(item, dict) and str(item.get("topic_id") or "") == topic_id
        ]
        if remaining:
            return _unsupported_payload(
                RUNTIME_ASTRBOT,
                "chat_topic_delete_not_confirmed",
                "AstrBot accepted the delete request, but the session still exists after refresh.",
                topic_id=topic_id,
                deleted=False,
                supports_delete=True,
                topics=refreshed.get("topics") if isinstance(refreshed.get("topics"), list) else [],
            )
        return refreshed

    def _create_ephemeral_topic(self, payload: dict[str, Any]) -> dict[str, Any]:
        seed = payload.get("session_id") or payload.get("topic_id") or payload.get("title")
        session_id = ephemeral_session_id(seed) if str(seed or "").strip() else f"ipet-{uuid4().hex}"
        if _is_reserved_astrbot_vision_identity(session_id):
            session_id = _SAFE_ASTRBOT_CHAT_SESSION_ID
        return {
            "ok": True,
            "runtime": RUNTIME_ASTRBOT,
            "ephemeral": True,
            "topic": {
                "topic_id": session_id,
                "session_id": session_id,
                "title": str(payload.get("title") or session_id),
                "persisted": False,
                "runtime": RUNTIME_ASTRBOT,
                "source": RUNTIME_ASTRBOT,
                "supports_history_detail": False,
                "supports_delete": False,
                "history_unavailable": True,
            },
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


class MockRuntimeAdapter:
    runtime_id = RUNTIME_MOCK

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    async def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "runtime": RUNTIME_MOCK,
            "configured": True,
            "available": True,
            "detail": "Mock runtime is available for local development and tests.",
        }

    async def request_json(self, method: str, path: str, *, json_payload: Any | None = None) -> dict[str, Any]:
        normalized = f"/{str(path or '').lstrip('/')}"
        base = {"ok": True, "runtime": RUNTIME_MOCK, "path": normalized}
        if normalized in {"/", "/health", "/api/health", "/api/status", "/api/runtime/status"}:
            return {**base, "available": True, "configured": True, "detail": "Mock runtime is healthy."}
        if "skill" in normalized:
            return {**base, "skills": [], "detail": "Mock runtime has no external skills."}
        if "mcp" in normalized or "server" in normalized:
            return {**base, "servers": [], "online": 0, "detail": "Mock runtime has no MCP servers."}
        if "topic" in normalized or "session" in normalized:
            return {**base, "topics": [], "sessions": [], "detail": "Mock runtime has no persisted chat history."}
        if "model" in normalized:
            return {**base, "models": ["mock-model"], "detail": "Mock runtime exposes a stable mock model."}
        return _unsupported_payload(
            RUNTIME_MOCK,
            "request_json",
            "Mock runtime does not implement this proxy path.",
            method=str(method or "GET").upper(),
            path=normalized,
            json_payload=json_payload,
        )

    async def stream_sse(self, path: str, payload: dict[str, Any]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        normalized = f"/{str(path or '').lstrip('/')}"
        if normalized != "/api/chat/stream":
            yield "error", {
                "runtime": RUNTIME_MOCK,
                "message": f"Mock runtime does not support SSE path: {normalized}",
            }
            return
        message = str(payload.get("message") or payload.get("text") or "").strip()
        text = self.config.get("response_text") or (f"Mock response: {message}" if message else "Mock response.")
        session_id = str(payload.get("session_id") or "mock")
        yield "meta", {"runtime": RUNTIME_MOCK, "session_id": session_id, "topic_id": session_id}
        yield "token", {"runtime": RUNTIME_MOCK, "delta": str(text), "text": str(text)}
        yield "done", {"runtime": RUNTIME_MOCK, "ok": True, "text": str(text), "topic_id": session_id}


def create_runtime_adapter(runtime_config: dict[str, Any], runtime_id: str | None = None) -> RuntimeAdapter:
    adapters = runtime_config.get("adapters") if isinstance(runtime_config.get("adapters"), dict) else {}
    requested = str(runtime_id if runtime_id is not None else runtime_config.get("active", DEFAULT_RUNTIME_ID)).strip().lower()
    if requested == RUNTIME_MOCK:
        return MockRuntimeAdapter(adapters.get(RUNTIME_MOCK) if isinstance(adapters.get(RUNTIME_MOCK), dict) else {})
    active = normalize_runtime_id(requested)
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


def _jwt_b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


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


def _extract_astrbot_dashboard_token(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    candidates = [payload.get("token"), payload.get("access_token")]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("token"), data.get("access_token"), data.get("jwt")])
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _astrbot_payload_status_is_error(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    status = str(payload.get("status") or payload.get("code") or "").strip().lower()
    if status == "error":
        return True
    data = payload.get("data")
    if isinstance(data, dict):
        nested_status = str(data.get("status") or "").strip().lower()
        return nested_status == "error"
    return False


def _astrbot_payload_error_detail(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("detail", "message", "error"):
        text = str(payload.get(key) or "").strip()
        if text:
            return text
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("detail", "message", "error"):
            text = str(data.get(key) or "").strip()
            if text:
                return text
    return ""


def _is_astrbot_webchat_session(item: dict[str, Any]) -> bool:
    platform = str(
        item.get("platform_id")
        or item.get("platform")
        or item.get("platform_name")
        or item.get("adapter_type")
        or ""
    ).strip().lower()
    if not platform:
        return True
    return platform == "webchat"


def _astrbot_session_owner_matches(item: dict[str, Any], username: str) -> bool:
    expected = str(username or "ipet").strip()
    creator = str(item.get("creator") or item.get("username") or item.get("user_name") or "").strip()
    return not creator or creator == expected


def _extract_astrbot_history(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("history"), list):
            return data["history"]
        if isinstance(payload.get("history"), list):
            return payload["history"]
    return []


def _map_astrbot_dashboard_history(history: list[Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        content_type = str(content.get("type") or item.get("type") or "").strip().lower()
        if content_type == "user":
            role = "user"
        elif content_type in {"bot", "assistant"}:
            role = "assistant"
        else:
            continue
        text = _astrbot_parts_to_text(content.get("message"))
        if not text:
            continue
        message = {"role": role, "content": text}
        for key in ("created_at", "updated_at", "timestamp"):
            if item.get(key):
                message[key] = item.get(key)
                break
        messages.append(message)
    return messages


def _merge_astrbot_topic_history_summary(topic: dict[str, Any], messages: list[dict[str, Any]]) -> None:
    if not messages:
        topic["message_count"] = int(topic.get("message_count") or 0)
        topic["assistant_turn_count"] = int(topic.get("assistant_turn_count") or 0)
        return
    topic["message_count"] = len(messages)
    topic["assistant_turn_count"] = sum(1 for item in messages if str(item.get("role") or "") == "assistant")
    if not str(topic.get("preview") or "").strip():
        for item in reversed(messages):
            text = str(item.get("content") or "").strip()
            if text:
                topic["preview"] = text[:160]
                break


def _astrbot_parts_to_text(parts: Any) -> str:
    if isinstance(parts, str):
        return parts.strip()
    if isinstance(parts, dict):
        parts = [parts]
    if not isinstance(parts, list):
        return ""
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, str):
            if part.strip():
                chunks.append(part.strip())
            continue
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or part.get("kind") or "").strip().lower()
        if part_type in {"plain", "text"}:
            text = str(part.get("text") or part.get("content") or part.get("message") or "").strip()
            if text:
                chunks.append(text)
            continue
        label = _astrbot_non_text_part_label(part_type, part)
        if label:
            chunks.append(label)
    return "\n".join(chunks).strip()


def _astrbot_non_text_part_label(part_type: str, part: dict[str, Any]) -> str:
    label_map = {
        "image": "图片",
        "file": "文件",
        "tool": "工具",
        "function": "工具",
        "record": "语音",
        "audio": "语音",
        "video": "视频",
    }
    label = label_map.get(part_type) or (part_type or "非文本")
    name = str(
        part.get("name")
        or part.get("filename")
        or part.get("file_name")
        or part.get("title")
        or ""
    ).strip()
    return f"[{label}: {name}]" if name else f"[{label}]"


def _decode_runtime_frame_data_url(payload: dict[str, Any], *, max_bytes: int) -> tuple[str, bytes]:
    mime_type = str(payload.get("mime_type") or "").strip().lower()
    data_url = str(payload.get("data_url") or "")
    if mime_type not in {"image/png", "image/jpeg"}:
        raise ValueError("unsupported frame mime_type")
    prefix = f"data:{mime_type};base64,"
    if not data_url.startswith(prefix):
        raise ValueError("frame data_url mime type mismatch")
    image_bytes = base64.b64decode(data_url[len(prefix) :], validate=True)
    if not image_bytes:
        raise ValueError("frame image is empty")
    if len(image_bytes) > max_bytes:
        raise ValueError("frame image is too large")
    return mime_type, image_bytes


def _extract_astrbot_attachment_id(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    candidates = [data.get("attachment_id"), data.get("id"), data.get("file_id"), data.get("uuid")]
    nested = data.get("data")
    if isinstance(nested, dict):
        candidates.extend([nested.get("attachment_id"), nested.get("id"), nested.get("file_id"), nested.get("uuid")])
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _extract_astrbot_response_text(response: httpx.Response) -> str:
    content_type = str(response.headers.get("content-type") or "").lower()
    raw_text = response.text or ""
    if "text/event-stream" in content_type or raw_text.lstrip().startswith(("event:", "data:")):
        return _extract_text_from_sse(raw_text)
    try:
        data = response.json()
    except Exception:
        return raw_text
    return _extract_text_from_json(data)


def _extract_text_from_json(data: Any) -> str:
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""
    for key in ("content", "text", "message", "answer", "result", "response"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    nested = data.get("data")
    if isinstance(nested, (dict, str)):
        nested_text = _extract_text_from_json(nested)
        if nested_text:
            return nested_text
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return str(message.get("content") or "")
    return ""


def _extract_text_from_sse(text: str) -> str:
    session_id = "ipet-vision-analysis"
    full = ""
    for line in str(text or "").splitlines():
        if not line.startswith("data:"):
            continue
        data = _loads_data(line[len("data:") :].lstrip())
        if not isinstance(data, dict):
            continue
        name, mapped = _adapt_astrbot_event("message", data, session_id=session_id)
        if name == "token":
            delta = _normalize_stream_delta(
                full,
                str(mapped.get("delta") or ""),
                cumulative=bool(mapped.get("cumulative", False)),
            )
            full += delta
        elif name == "done":
            return str(mapped.get("text") or "") or full
    return full


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
    if name in {"meta", "metadata"}:
        payload.setdefault("runtime", RUNTIME_ASTRBOT)
        payload.setdefault("topic_id", session_id)
        return "meta", payload
    payload_type = str(payload.get("type") or payload.get("event") or "").strip().lower()
    text = str(payload.get("text") or payload.get("message") or payload.get("detail") or "")
    if name not in {"message", "data", "delta", "chunk", "content", "token", "stream", "response"} or payload_type in {
        "phase",
        "progress",
        "status",
        "tool",
        "action",
    }:
        return "phase", _runtime_phase_payload(name or payload_type or "event", text)
    delta, cumulative = _extract_text_update(payload)
    if delta:
        return "token", {"delta": delta, "runtime": RUNTIME_ASTRBOT, "cumulative": cumulative}
    return "phase", _runtime_phase_payload(name or "event", text)


def _runtime_phase_payload(phase: str, text: str) -> dict[str, Any]:
    phase_name = str(phase or "event").strip() or "event"
    return {
        "phase": phase_name,
        "text": text,
        "speaker": "pet",
        "runtime": RUNTIME_ASTRBOT,
        "source": "runtime",
        "transient": False,
        "status_id": f"runtime:{phase_name}",
        "confidence": 1.0,
        "render": "worklog",
    }


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
