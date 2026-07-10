from __future__ import annotations

import unittest
from unittest import mock

from brain.llm import (
    BrainMessage,
    BrainProviderConfig,
    complete_with_provider,
    list_provider_models,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _RecordingClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests: list[dict] = []

    async def post(self, url: str, **kwargs):
        self.requests.append({"url": url, **kwargs})
        return _FakeResponse(self.payload)

    async def get(self, url: str, **kwargs):
        self.requests.append({"method": "GET", "url": url, **kwargs})
        return _FakeResponse(self.payload)


class _PagedRecordingClient:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.requests: list[dict] = []

    async def get(self, url: str, **kwargs):
        self.requests.append({"method": "GET", "url": url, **kwargs})
        index = min(len(self.requests) - 1, len(self.payloads) - 1)
        return _FakeResponse(self.payloads[index])


class _FakeAsyncClient:
    init_kwargs: dict = {}

    def __init__(self, **kwargs) -> None:
        type(self).init_kwargs = kwargs

    async def __aenter__(self):
        return _RecordingClient({"models": [{"name": "llama3.1"}]})

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class BrainProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_compatible_uses_chat_completions_shape(self) -> None:
        client = _RecordingClient({"choices": [{"message": {"content": "你好，人类。"}}]})
        config = BrainProviderConfig(
            provider="openai_compatible",
            endpoint="https://llm.example/v1",
            model="gpt-test",
            api_key="secret",
            temperature=0.25,
            max_tokens=2048,
        )

        completion = await complete_with_provider(
            config,
            [BrainMessage(role="system", content="persona"), BrainMessage(role="user", content="hi")],
            client=client,
        )

        self.assertEqual(completion.text, "你好，人类。")
        request = client.requests[0]
        self.assertEqual(request["url"], "https://llm.example/v1/chat/completions")
        self.assertEqual(request["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(request["json"]["model"], "gpt-test")
        self.assertEqual(request["json"]["messages"][0], {"role": "system", "content": "persona"})
        self.assertFalse(request["json"]["stream"])
        self.assertEqual(request["json"]["temperature"], 0.25)
        self.assertEqual(request["json"]["max_tokens"], 2048)

    async def test_ollama_uses_api_chat_shape(self) -> None:
        client = _RecordingClient({"message": {"content": "本地模型回复"}})
        config = BrainProviderConfig(
            provider="ollama",
            endpoint="http://127.0.0.1:11434/api",
            model="llama3.1",
            temperature=0.4,
            max_tokens=512,
        )

        completion = await complete_with_provider(
            config,
            [BrainMessage(role="system", content="persona"), BrainMessage(role="user", content="hi")],
            client=client,
        )

        self.assertEqual(completion.text, "本地模型回复")
        request = client.requests[0]
        self.assertEqual(request["url"], "http://127.0.0.1:11434/api/chat")
        self.assertNotIn("Authorization", request["headers"])
        self.assertEqual(request["json"]["model"], "llama3.1")
        self.assertEqual(request["json"]["messages"][1], {"role": "user", "content": "hi"})
        self.assertEqual(request["json"]["options"]["temperature"], 0.4)
        self.assertEqual(request["json"]["options"]["num_predict"], 512)
        self.assertFalse(request["json"]["stream"])

    async def test_anthropic_compatible_uses_messages_shape(self) -> None:
        client = _RecordingClient({"content": [{"type": "text", "text": "Claude 风格回复"}]})
        config = BrainProviderConfig(
            provider="anthropic_compatible",
            endpoint="https://anthropic.example",
            model="claude-test",
            api_key="anthropic-secret",
            temperature=0.1,
        )

        completion = await complete_with_provider(
            config,
            [BrainMessage(role="system", content="persona"), BrainMessage(role="user", content="hi")],
            client=client,
        )

        self.assertEqual(completion.text, "Claude 风格回复")
        request = client.requests[0]
        self.assertEqual(request["url"], "https://anthropic.example/v1/messages")
        self.assertEqual(request["headers"]["x-api-key"], "anthropic-secret")
        self.assertEqual(request["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(request["json"]["model"], "claude-test")
        self.assertEqual(request["json"]["system"], "persona")
        self.assertEqual(request["json"]["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(request["json"]["temperature"], 0.1)

    async def test_google_aistudio_uses_generate_content_shape_with_only_api_key(self) -> None:
        client = _RecordingClient({"candidates": [{"content": {"parts": [{"text": "Gemini 回复"}]}}]})
        config = BrainProviderConfig(
            provider="google_aistudio",
            model="gemini-test",
            api_key="gemini-secret",
            temperature=0.2,
            max_tokens=768,
        )

        completion = await complete_with_provider(
            config,
            [BrainMessage(role="system", content="persona"), BrainMessage(role="user", content="hi")],
            client=client,
        )

        self.assertEqual(completion.text, "Gemini 回复")
        request = client.requests[0]
        self.assertEqual(
            request["url"],
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent?key=gemini-secret",
        )
        self.assertEqual(request["headers"]["x-goog-api-key"], "gemini-secret")
        self.assertEqual(request["json"]["system_instruction"]["parts"][0]["text"], "persona")
        self.assertEqual(request["json"]["contents"], [{"role": "user", "parts": [{"text": "hi"}]}])
        self.assertEqual(request["json"]["generationConfig"]["temperature"], 0.2)
        self.assertEqual(request["json"]["generationConfig"]["maxOutputTokens"], 768)

    async def test_google_aistudio_accepts_host_only_endpoint_for_generate_content(self) -> None:
        client = _RecordingClient({"candidates": [{"content": {"parts": [{"text": "Gemini 回复"}]}}]})
        config = BrainProviderConfig(
            provider="google_aistudio",
            endpoint="https://generativelanguage.googleapis.com",
            model="gemini-test",
            api_key="gemini-secret",
        )

        completion = await complete_with_provider(
            config,
            [BrainMessage(role="user", content="hi")],
            client=client,
        )

        self.assertEqual(completion.text, "Gemini 回复")
        request = client.requests[0]
        self.assertEqual(
            request["url"],
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent?key=gemini-secret",
        )

    async def test_openai_compatible_lists_models(self) -> None:
        client = _RecordingClient({"data": [{"id": "gpt-a"}, {"id": "gpt-b"}]})
        config = BrainProviderConfig(provider="openai_compatible", endpoint="https://llm.example/v1", api_key="secret")

        models = await list_provider_models(config, client=client)

        self.assertEqual([model.id for model in models], ["gpt-a", "gpt-b"])
        request = client.requests[0]
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["url"], "https://llm.example/v1/models")
        self.assertEqual(request["headers"]["Authorization"], "Bearer secret")

    async def test_ollama_lists_models_from_tags(self) -> None:
        client = _RecordingClient({"models": [{"name": "llama3.1:latest"}, {"model": "qwen2.5"}]})
        config = BrainProviderConfig(provider="ollama", endpoint="http://127.0.0.1:11434/api")

        models = await list_provider_models(config, client=client)

        self.assertEqual([model.id for model in models], ["llama3.1:latest", "qwen2.5"])
        request = client.requests[0]
        self.assertEqual(request["url"], "http://127.0.0.1:11434/api/tags")
        self.assertNotIn("Authorization", request["headers"])

    async def test_anthropic_compatible_lists_models(self) -> None:
        client = _RecordingClient({"data": [{"id": "claude-a"}]})
        config = BrainProviderConfig(provider="anthropic_compatible", endpoint="https://anthropic.example", api_key="secret")

        models = await list_provider_models(config, client=client)

        self.assertEqual([model.id for model in models], ["claude-a"])
        request = client.requests[0]
        self.assertEqual(request["url"], "https://anthropic.example/v1/models")
        self.assertEqual(request["headers"]["x-api-key"], "secret")

    async def test_google_aistudio_lists_generate_content_models_without_endpoint(self) -> None:
        client = _RecordingClient(
            {
                "models": [
                    {
                        "name": "models/gemini-a",
                        "baseModelId": "gemini-a",
                        "displayName": "Gemini A",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/embed-only",
                        "baseModelId": "embed-only",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            }
        )
        config = BrainProviderConfig(provider="google_aistudio", api_key="gemini-secret")

        models = await list_provider_models(config, client=client)

        self.assertEqual([model.to_dict() for model in models], [{"id": "gemini-a", "label": "Gemini A"}])
        request = client.requests[0]
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["url"], "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000&key=gemini-secret")
        self.assertEqual(request["headers"]["x-goog-api-key"], "gemini-secret")

    async def test_google_aistudio_lists_models_with_host_only_endpoint(self) -> None:
        client = _RecordingClient(
            {
                "models": [
                    {
                        "name": "models/gemini-3.5-flash",
                        "baseModelId": "gemini-3.5-flash",
                        "displayName": "Gemini 3.5 Flash",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ]
            }
        )
        config = BrainProviderConfig(
            provider="google_aistudio",
            endpoint="https://generativelanguage.googleapis.com",
            api_key="gemini-secret",
        )

        models = await list_provider_models(config, client=client)

        self.assertEqual([model.id for model in models], ["gemini-3.5-flash"])
        request = client.requests[0]
        self.assertEqual(
            request["url"],
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000&key=gemini-secret",
        )

    async def test_google_aistudio_lists_models_across_pages(self) -> None:
        client = _PagedRecordingClient(
            [
                {
                    "models": [
                        {
                            "name": "models/gemini-3.5-flash",
                            "baseModelId": "gemini-3.5-flash",
                            "displayName": "Gemini 3.5 Flash",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ],
                    "nextPageToken": "page-2",
                },
                {
                    "models": [
                        {
                            "name": "models/gemini-2.5-pro",
                            "baseModelId": "gemini-2.5-pro",
                            "displayName": "Gemini 2.5 Pro",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            ]
        )
        config = BrainProviderConfig(provider="google_aistudio", api_key="gemini-secret")

        models = await list_provider_models(config, client=client)

        self.assertEqual([model.id for model in models], ["gemini-3.5-flash", "gemini-2.5-pro"])
        self.assertEqual(
            client.requests[0]["url"],
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000&key=gemini-secret",
        )
        self.assertEqual(
            client.requests[1]["url"],
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000&pageToken=page-2&key=gemini-secret",
        )

    async def test_brain_http_client_ignores_environment_proxy_by_default(self) -> None:
        config = BrainProviderConfig(provider="ollama", endpoint="http://127.0.0.1:11434")

        with mock.patch("brain.llm.httpx.AsyncClient", _FakeAsyncClient):
            models = await list_provider_models(config)

        self.assertEqual([model.id for model in models], ["llama3.1"])
        self.assertFalse(_FakeAsyncClient.init_kwargs["trust_env"])
        self.assertNotIn("proxy", _FakeAsyncClient.init_kwargs)

    async def test_remote_openai_compatible_http_client_uses_https_proxy_from_environment(self) -> None:
        config = BrainProviderConfig(provider="openai_compatible", endpoint="https://llm.example/v1", api_key="secret")

        with mock.patch.dict(
            "os.environ",
            {
                "HTTPS_PROXY": "http://127.0.0.1:10808",
                "ALL_PROXY": "socks5://127.0.0.1:20808",
            },
            clear=False,
        ):
            with mock.patch("brain.llm.httpx.AsyncClient", _FakeAsyncClient):
                models = await list_provider_models(config)

        self.assertEqual([model.id for model in models], ["llama3.1"])
        self.assertFalse(_FakeAsyncClient.init_kwargs["trust_env"])
        self.assertEqual(_FakeAsyncClient.init_kwargs["proxy"], "http://127.0.0.1:10808")

    async def test_local_openai_compatible_http_client_does_not_use_proxy(self) -> None:
        config = BrainProviderConfig(provider="openai_compatible", endpoint="http://127.0.0.1:11434/v1")

        with mock.patch.dict("os.environ", {"HTTPS_PROXY": "http://127.0.0.1:10808"}, clear=False):
            with mock.patch("brain.llm.httpx.AsyncClient", _FakeAsyncClient):
                models = await list_provider_models(config)

        self.assertEqual([model.id for model in models], ["llama3.1"])
        self.assertFalse(_FakeAsyncClient.init_kwargs["trust_env"])
        self.assertNotIn("proxy", _FakeAsyncClient.init_kwargs)

    async def test_google_aistudio_http_client_uses_https_proxy_from_environment(self) -> None:
        config = BrainProviderConfig(provider="google_aistudio", api_key="gemini-secret")

        with mock.patch.dict(
            "os.environ",
            {
                "HTTPS_PROXY": "http://127.0.0.1:10808",
                "ALL_PROXY": "socks5://127.0.0.1:20808",
            },
            clear=False,
        ):
            with mock.patch("brain.llm.httpx.AsyncClient", _FakeAsyncClient):
                models = await list_provider_models(config)

        self.assertEqual([model.id for model in models], ["llama3.1"])
        self.assertFalse(_FakeAsyncClient.init_kwargs["trust_env"])
        self.assertEqual(_FakeAsyncClient.init_kwargs["proxy"], "http://127.0.0.1:10808")


if __name__ == "__main__":
    unittest.main()
