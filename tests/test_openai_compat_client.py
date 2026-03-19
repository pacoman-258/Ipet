from __future__ import annotations

import unittest
from unittest import mock

import httpx

from backend.ollama_client import chat_once, list_models


class OpenAICompatClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_once_retries_without_tools_when_provider_rejects_tool_calling(self) -> None:
        calls: list[dict[str, object]] = []

        async def fake_post(self, url, *, headers=None, json=None, **_kwargs):
            calls.append({"url": url, "headers": headers, "json": json})
            if len(calls) == 1:
                return httpx.Response(
                    400,
                    request=httpx.Request("POST", url),
                    json={"error": {"message": "tools are not supported for this model"}},
                )
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"choices": [{"message": {"role": "assistant", "content": "fallback ok"}}]},
            )

        with mock.patch("backend.ollama_client.httpx.AsyncClient.post", new=fake_post):
            message = await chat_once(
                messages=[{"role": "user", "content": "hello"}],
                model="gemini-3-flash-preview",
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "description": "Read a file",
                            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                        },
                    }
                ],
                provider="openai_compat",
                base_url="https://gcli.ggchan.dev",
                api_key="demo-key",
            )

        self.assertEqual(message.get("content"), "fallback ok")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["url"], "https://gcli.ggchan.dev/v1/chat/completions")
        self.assertIn("tools", calls[0]["json"])
        self.assertEqual(calls[0]["json"].get("tool_choice"), "auto")
        self.assertNotIn("tools", calls[1]["json"])
        self.assertEqual(calls[1]["json"], {"model": "gemini-3-flash-preview", "messages": [{"role": "user", "content": "hello"}]})

    async def test_chat_once_normalizes_base_url_when_v1_is_already_present(self) -> None:
        seen_urls: list[str] = []

        async def fake_post(self, url, *, headers=None, json=None, **_kwargs):
            seen_urls.append(url)
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            )

        with mock.patch("backend.ollama_client.httpx.AsyncClient.post", new=fake_post):
            await chat_once(
                messages=[{"role": "user", "content": "hello"}],
                model="demo-model",
                provider="openai_compat",
                base_url="https://example.com/v1/",
                api_key="demo-key",
            )

        self.assertEqual(seen_urls, ["https://example.com/v1/chat/completions"])

    async def test_list_models_normalizes_base_url_when_v1_is_already_present(self) -> None:
        seen_urls: list[str] = []

        async def fake_get(self, url, *, headers=None, **_kwargs):
            seen_urls.append(url)
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={"data": [{"id": "alpha"}, {"id": "beta"}]},
            )

        with mock.patch("backend.ollama_client.httpx.AsyncClient.get", new=fake_get):
            models = await list_models(
                provider="openai_compat",
                base_url="https://example.com/v1",
                api_key="demo-key",
            )

        self.assertEqual(models, ["alpha", "beta"])
        self.assertEqual(seen_urls, ["https://example.com/v1/models"])


if __name__ == "__main__":
    unittest.main()
