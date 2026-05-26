from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
