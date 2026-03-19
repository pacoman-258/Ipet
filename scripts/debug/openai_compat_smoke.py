from __future__ import annotations

import asyncio
import json
import os

import httpx
import requests


DEFAULT_BASE_URL = os.getenv("OPENAI_COMPAT_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.getenv("OPENAI_COMPAT_MODEL", "qwen3:8b")
DEFAULT_API_KEY = os.getenv("OPENAI_COMPAT_API_KEY", "")


def _headers(api_key: str) -> dict[str, str]:
    key = str(api_key or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _chat_url(base_url: str) -> str:
    base = str(base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


async def test_openai_compatible_api(
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    api_key: str = DEFAULT_API_KEY,
) -> None:
    client = httpx.AsyncClient(timeout=60.0)
    url = _chat_url(base_url)

    try:
        print("=== Non-stream request ===")
        response = await client.post(
            url,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Explain Python in one sentence."},
                ],
                "temperature": 0.7,
                "max_tokens": 100,
            },
            headers=_headers(api_key),
        )

        if response.status_code == 200:
            data = response.json()
            print(f"status: {response.status_code}")
            print(f"model: {data.get('model')}")
            print(f"reply: {data['choices'][0]['message']['content']}")
        else:
            print(f"error: {response.status_code} - {response.text}")

        print("\n=== Stream request ===")
        async with client.stream(
            "POST",
            url,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "What is a large language model?"},
                ],
                "temperature": 0.7,
                "stream": True,
            },
            headers=_headers(api_key),
        ) as response_stream:
            if response_stream.status_code != 200:
                print(f"error: {response_stream.status_code} - {await response_stream.aread()}")
                return

            async for line in response_stream.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    chunk = json.loads(payload)
                except Exception:
                    continue
                delta = chunk["choices"][0]["delta"].get("content", "")
                if delta:
                    print(delta, end="", flush=True)
            print()
    except httpx.ConnectError:
        print(f"connection failed: cannot reach {base_url}")
    except Exception as exc:
        print(f"error: {exc}")
    finally:
        await client.aclose()


def test_openai_compatible_api_sync(
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    api_key: str = DEFAULT_API_KEY,
) -> None:
    url = _chat_url(base_url)
    try:
        print("=== Sync non-stream request ===")
        response = requests.post(
            url,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Explain Python in one sentence."},
                ],
            },
            headers=_headers(api_key),
            timeout=60,
        )
        if response.status_code == 200:
            data = response.json()
            print(f"reply: {data['choices'][0]['message']['content']}")
        else:
            print(f"error: {response.status_code} - {response.text}")
    except Exception as exc:
        print(f"error: {exc}")


if __name__ == "__main__":
    asyncio.run(test_openai_compatible_api())
