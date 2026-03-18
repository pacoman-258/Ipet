from __future__ import annotations

import json
from typing import Any, AsyncGenerator

import httpx


OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OPENAI_COMPAT_BASE_URL = "https://api.openai.com/v1"


def _norm_provider(provider: str | None) -> str:
    p = str(provider or "").strip().lower()
    if p in {"openai", "openai_compat", "openai-compatible"}:
        return "openai_compat"
    return "ollama"


def _auth_headers(api_key: str | None) -> dict[str, str]:
    key = str(api_key or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _openai_base(base_url: str | None) -> str:
    base = str(base_url or "").strip() or OPENAI_COMPAT_BASE_URL
    return base.rstrip("/")


async def is_ollama_alive(base_url: str = OLLAMA_BASE_URL) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
        return resp.status_code == 200
    except Exception:
        return False


async def stream_chat(
    messages: list[dict],
    model: str,
    provider: str = "ollama",
    base_url: str = "",
    api_key: str = "",
) -> AsyncGenerator[str, None]:
    p = _norm_provider(provider)
    timeout = httpx.Timeout(connect=8.0, read=180.0, write=20.0, pool=8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if p == "ollama":
            target = str(base_url or OLLAMA_BASE_URL).rstrip("/")
            payload = {"model": model, "messages": messages, "stream": True}
            async with client.stream("POST", f"{target}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if chunk.get("done"):
                        break
                    msg = chunk.get("message", {})
                    content = msg.get("content", "")
                    if content:
                        yield content
            return

        # openai-compatible streaming
        target = _openai_base(base_url)
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        headers = _auth_headers(api_key)
        async with client.stream(
            "POST",
            f"{target}/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data_line = line[5:].strip()
                if data_line == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_line)
                except Exception:
                    continue
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content


async def chat_once(
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]] | None = None,
    provider: str = "ollama",
    base_url: str = "",
    api_key: str = "",
) -> dict[str, Any]:
    p = _norm_provider(provider)
    timeout = httpx.Timeout(connect=8.0, read=120.0, write=20.0, pool=8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if p == "ollama":
            target = str(base_url or OLLAMA_BASE_URL).rstrip("/")
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": False,
            }
            if tools:
                payload["tools"] = tools
            resp = await client.post(f"{target}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            message = data.get("message", {})
            return message if isinstance(message, dict) else {}

        target = _openai_base(base_url)
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = _auth_headers(api_key)
        resp = await client.post(
            f"{target}/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {"content": ""}
        message = choices[0].get("message", {})
        if not isinstance(message, dict):
            return {"content": ""}
        return message


async def list_models(
    provider: str = "ollama",
    base_url: str = "",
    api_key: str = "",
) -> list[str]:
    p = _norm_provider(provider)
    timeout = httpx.Timeout(connect=6.0, read=30.0, write=10.0, pool=6.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if p == "ollama":
            target = str(base_url or OLLAMA_BASE_URL).rstrip("/")
            resp = await client.get(f"{target}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = data.get("models", [])
            out: list[str] = []
            for item in models if isinstance(models, list) else []:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                    if name:
                        out.append(name)
            return sorted(set(out))

        target = _openai_base(base_url)
        headers = _auth_headers(api_key)
        resp = await client.get(f"{target}/v1/models", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        out: list[str] = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                mid = str(item.get("id") or "").strip()
                if mid:
                    out.append(mid)
        return sorted(set(out))
