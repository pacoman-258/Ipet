from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from brain.llm import (
    BrainLLMError,
    BrainMessage,
    BrainModel,
    BrainProviderConfig,
    BrainCompletion,
    _StreamingSayTextExtractor,
    _codex_jsonl_usage,
    _codex_models_from_payload,
    complete_with_provider,
    list_provider_models,
    normalize_provider,
    run_brain_turn,
)


PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z0mAAAAAASUVORK5CYII="
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


class _FakeProcess:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout.encode("utf-8")
        self.stderr = stderr.encode("utf-8")
        self.returncode = returncode
        self.input = b""

    async def communicate(self, input_data: bytes = b""):
        self.input = input_data
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.returncode = -9


class _FakeAppServerPipe:
    def __init__(self, lines: list[dict] | None = None) -> None:
        self.lines = [f"{json.dumps(line)}\n".encode("utf-8") for line in lines or []]
        self.writes: list[bytes] = []

    async def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""

    async def read(self) -> bytes:
        return b""

    def write(self, value: bytes) -> None:
        self.writes.append(value)

    async def drain(self) -> None:
        return None


class _FakeAppServerProcess:
    def __init__(self, lines: list[dict] | None = None) -> None:
        self.stdin = _FakeAppServerPipe()
        self.stdout = _FakeAppServerPipe(
            lines or [
                {"id": 1, "result": {}},
                {"id": 2, "result": {"thread": {"id": "thread-test"}}},
                {"id": 3, "result": {}},
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "tokenUsage": {
                            "last": {"inputTokens": 1200, "outputTokens": 3},
                            "total": {"inputTokens": 1700, "outputTokens": 8, "totalTokens": 1708},
                        }
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "turn": {
                            "status": "completed",
                            "items": [{"type": "agentMessage", "text": '{"kind":"say","text":"OK"}'}],
                        }
                    },
                },
            ]
        )
        self.stderr = _FakeAppServerPipe()
        self.returncode = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return int(self.returncode or 0)


class BrainProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_provider_uses_lightweight_tool_free_app_server_thread(self) -> None:
        process = _FakeAppServerProcess()
        config = BrainProviderConfig(provider="codex", model="gpt-account")

        with mock.patch("brain.llm._codex_executable", return_value="/mock/codex"):
            with mock.patch("brain.llm.asyncio.create_subprocess_exec", new=mock.AsyncMock(return_value=process)) as spawn:
                completion = await complete_with_provider(config, [BrainMessage(role="user", content="hi")])

        self.assertEqual(completion.text, '{"kind":"say","text":"OK"}')
        self.assertEqual(completion.usage, {"input_tokens": 1700, "output_tokens": 8, "total_tokens": 1708})
        self.assertEqual(spawn.await_args.args[:3], ("/mock/codex", "app-server", "--stdio"))
        requests = [json.loads(value) for chunk in process.stdin.writes for value in chunk.decode("utf-8").splitlines()]
        thread_params = requests[1]["params"]
        self.assertIn("multimodal inference backend for Ipet", thread_params["baseInstructions"])
        self.assertEqual(thread_params["developerInstructions"], "")
        self.assertEqual(thread_params["config"]["web_search"], "disabled")
        self.assertTrue(all(value is False for value in thread_params["config"]["features"].values()))
        self.assertEqual(thread_params["config"]["tools"], {"view_image": False, "web_search": False})

    async def test_codex_provider_attaches_brain_image_and_removes_temporary_file(self) -> None:
        process = _FakeAppServerProcess()

        with mock.patch("brain.llm._codex_executable", return_value="/mock/codex"):
            with mock.patch("brain.llm.asyncio.create_subprocess_exec", new=mock.AsyncMock(return_value=process)):
                await complete_with_provider(
                    BrainProviderConfig(provider="codex", model="gpt-account"),
                    [BrainMessage(role="user", content="观察并决定下一步", image_data_url=PNG_DATA_URL)],
                )

        requests = [json.loads(value) for chunk in process.stdin.writes for value in chunk.decode("utf-8").splitlines()]
        turn_input = requests[-1]["params"]["input"]
        self.assertEqual(turn_input[0]["type"], "text")
        self.assertEqual(turn_input[1]["type"], "localImage")
        self.assertFalse(os.path.exists(turn_input[1]["path"]))

    async def test_codex_web_search_streams_native_search_and_browse_activity(self) -> None:
        process = _FakeAppServerProcess(
            [
                {"id": 1, "result": {}},
                {"id": 2, "result": {"webSearch": True, "imageGeneration": False, "namespaceTools": False}},
                {"id": 3, "result": {"thread": {"id": "thread-search"}}},
                {"id": 4, "result": {}},
                {
                    "method": "item/started",
                    "params": {
                        "item": {
                            "id": "search-1",
                            "type": "webSearch",
                            "query": "Ipet latest",
                            "action": {"type": "search", "query": "Ipet latest"},
                        }
                    },
                },
                {
                    "method": "item/started",
                    "params": {
                        "item": {
                            "id": "open-1",
                            "type": "webSearch",
                            "query": "Ipet latest",
                            "action": {"type": "openPage", "url": "https://example.com/ipet"},
                        }
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "turn": {
                            "status": "completed",
                            "items": [{"type": "agentMessage", "text": '{"kind":"say","text":"完成"}'}],
                        }
                    },
                },
            ]
        )
        activities: list[dict] = []

        async def record_activity(activity: dict) -> None:
            activities.append(activity)

        with mock.patch("brain.llm._codex_executable", return_value="/mock/codex"):
            with mock.patch("brain.llm.asyncio.create_subprocess_exec", new=mock.AsyncMock(return_value=process)):
                completion = await complete_with_provider(
                    BrainProviderConfig(provider="codex", model="gpt-account", web_search_enabled=True),
                    [BrainMessage(role="user", content="查最新信息")],
                    on_activity=record_activity,
                )

        requests = [json.loads(value) for chunk in process.stdin.writes for value in chunk.decode("utf-8").splitlines()]
        self.assertEqual(requests[1]["method"], "modelProvider/capabilities/read")
        self.assertEqual(requests[2]["params"]["config"]["web_search"], "live")
        self.assertTrue(requests[2]["params"]["config"]["tools"]["web_search"])
        self.assertEqual([item["text"] for item in activities], ["正在搜索：Ipet latest", "正在浏览：https://example.com/ipet"])
        self.assertFalse(completion.web_search_unavailable)

    async def test_enabled_search_on_unsupported_provider_falls_back_with_notice(self) -> None:
        completion = BrainCompletion(text='{"kind":"say","text":"普通回答"}', provider="ollama", model="local")
        with mock.patch("brain.llm.complete_with_provider", new=mock.AsyncMock(return_value=completion)):
            result = await run_brain_turn(
                {"provider": "ollama", "model_name": "local", "web_search_enabled": True},
                user_text="请联网搜索",
            )

        self.assertTrue(result.web_search_unavailable)
        self.assertIn("未进行联网搜索", result.text)
        self.assertIn("普通回答", result.text)

    async def test_codex_provider_uses_local_cli_account_in_read_only_ephemeral_mode(self) -> None:
        process = _FakeProcess(
            '{"type":"item.completed","item":{"type":"agent_message","text":"Codex 回复"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":12,"cached_input_tokens":2,"output_tokens":3}}\n'
        )
        config = BrainProviderConfig(provider="codex", model="gpt-account", reasoning_effort="high")

        with mock.patch("brain.llm._complete_codex_stream", new=mock.AsyncMock(side_effect=BrainLLMError("unsupported"))):
            with mock.patch("brain.llm._codex_executable", return_value="/mock/codex"):
                with mock.patch("brain.llm.asyncio.create_subprocess_exec", new=mock.AsyncMock(return_value=process)) as spawn:
                    completion = await complete_with_provider(
                        config,
                        [BrainMessage(role="system", content="persona"), BrainMessage(role="user", content="hi")],
                    )

        self.assertEqual(completion.text, "Codex 回复")
        self.assertEqual(completion.provider, "codex")
        self.assertEqual(completion.usage, {"input_tokens": 12, "cached_input_tokens": 2, "output_tokens": 3, "total_tokens": 15})
        args = spawn.await_args.args
        self.assertEqual(args[:4], ("/mock/codex", "-a", "never", "exec"))
        for option in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only", "--json"):
            self.assertIn(option, args)
        model_index = args.index("--model")
        self.assertEqual(args[model_index + 1], "gpt-account")
        config_index = args.index("--config")
        self.assertEqual(args[config_index + 1], 'model_reasoning_effort="high"')
        self.assertIn('"role": "user", "content": "hi"', process.input.decode("utf-8"))

    async def test_codex_provider_model_discovery_checks_local_login(self) -> None:
        process = _FakeProcess("", stderr="Logged in using ChatGPT\n")
        codex_models = [BrainModel(id="gpt-account", label="GPT Account（默认）", is_default=True)]
        with mock.patch("brain.llm._codex_executable", return_value="/mock/codex"):
            with mock.patch("brain.llm.asyncio.create_subprocess_exec", new=mock.AsyncMock(return_value=process)) as spawn:
                with mock.patch("brain.llm._list_codex_models", new=mock.AsyncMock(return_value=codex_models)):
                    models = await list_provider_models({"provider": "codex"})

        self.assertEqual(
            [model.to_dict() for model in models],
            [{"id": "gpt-account", "label": "GPT Account（默认）", "is_default": True}],
        )
        self.assertEqual(spawn.await_args.args[:3], ("/mock/codex", "login", "status"))
        self.assertTrue(spawn.await_args.kwargs["env"]["CODEX_HOME"].endswith("/.codex"))

    async def test_codex_provider_model_discovery_rejects_failed_login_status(self) -> None:
        process = _FakeProcess("", stderr="Not logged in", returncode=1)
        with mock.patch("brain.llm._codex_executable", return_value="/mock/codex"):
            with mock.patch("brain.llm.asyncio.create_subprocess_exec", new=mock.AsyncMock(return_value=process)):
                with self.assertRaisesRegex(BrainLLMError, "Not logged in"):
                    await list_provider_models({"provider": "codex"})

    def test_codex_provider_alias_and_default_model_are_normalized(self) -> None:
        self.assertEqual(normalize_provider("codex-cli"), "codex")
        self.assertEqual(BrainProviderConfig.from_dict({"provider": "codex", "model_name": ""}).model, "default")
        self.assertEqual(BrainProviderConfig.from_dict({"provider": "codex", "model_name": "gpt-5.4"}).model, "gpt-5.4")
        self.assertEqual(
            BrainProviderConfig.from_dict({"provider": "codex", "reasoning_effort": "XHIGH"}).reasoning_effort,
            "xhigh",
        )
        self.assertTrue(BrainProviderConfig.from_dict({"provider": "codex", "streaming_enabled": True}).streaming_enabled)

    def test_codex_stream_extracts_only_visible_say_text(self) -> None:
        extractor = _StreamingSayTextExtractor()
        chunks = ['{"kind":"say","text":"你', '好\\n世', '界"}']

        self.assertEqual([extractor.feed(chunk) for chunk in chunks], ["你", "好\n世", "界"])
        self.assertEqual(
            _codex_jsonl_usage('{"type":"turn.completed","usage":{"input_tokens":8,"output_tokens":2}}'),
            {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
        )

    async def test_codex_stream_falls_back_before_any_delta(self) -> None:
        config = BrainProviderConfig(provider="codex", model="gpt-account", streaming_enabled=True)
        fallback = BrainCompletion(text="fallback", provider="codex", model="gpt-account")

        with mock.patch("brain.llm._complete_codex_stream", new=mock.AsyncMock(side_effect=BrainLLMError("unsupported"))):
            with mock.patch("brain.llm._complete_codex", new=mock.AsyncMock(return_value=fallback)) as complete:
                result = await complete_with_provider(
                    config,
                    [BrainMessage(role="user", content="hi")],
                    on_delta=mock.AsyncMock(),
                )

        self.assertEqual(result, fallback)
        complete.assert_awaited_once()

    def test_codex_model_catalog_includes_specific_models_and_reasoning_efforts(self) -> None:
        models = _codex_models_from_payload(
            [
                {
                    "id": "catalog-id",
                    "model": "gpt-account",
                    "displayName": "GPT Account",
                    "isDefault": True,
                    "hidden": False,
                    "defaultReasoningEffort": "low",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "fast"},
                        {"reasoningEffort": "ultra", "description": "delegates"},
                    ],
                    "inputModalities": ["text", "image"],
                },
                {"model": "hidden-model", "hidden": True},
            ]
        )

        self.assertEqual(
            [model.to_dict() for model in models],
            [
                {
                    "id": "gpt-account",
                    "label": "GPT Account（默认）",
                    "reasoning_efforts": [
                        {"value": "low", "description": "fast"},
                        {"value": "ultra", "description": "delegates"},
                    ],
                    "default_reasoning_effort": "low",
                    "is_default": True,
                    "input_modalities": ["text", "image"],
                }
            ],
        )

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

    async def test_openai_compatible_attaches_image_to_last_user_message(self) -> None:
        client = _RecordingClient({"choices": [{"message": {"content": "看到了"}}]})

        await complete_with_provider(
            BrainProviderConfig(provider="openai_compatible", endpoint="https://llm.example/v1"),
            [BrainMessage(role="user", content="观察并决定", image_data_url=PNG_DATA_URL)],
            client=client,
        )

        content = client.requests[0]["json"]["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "观察并决定"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(content[1]["image_url"]["url"], PNG_DATA_URL)

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

    async def test_ollama_attaches_raw_base64_image(self) -> None:
        client = _RecordingClient({"message": {"content": "看到了"}})

        await complete_with_provider(
            BrainProviderConfig(provider="ollama", endpoint="http://127.0.0.1:11434"),
            [BrainMessage(role="user", content="观察并决定", image_data_url=PNG_DATA_URL)],
            client=client,
        )

        message = client.requests[0]["json"]["messages"][0]
        self.assertEqual(message["content"], "观察并决定")
        self.assertEqual(message["images"], [PNG_DATA_URL.split(",", 1)[1]])

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

    async def test_anthropic_compatible_attaches_base64_image_source(self) -> None:
        client = _RecordingClient({"content": [{"type": "text", "text": "看到了"}]})

        await complete_with_provider(
            BrainProviderConfig(provider="anthropic_compatible", endpoint="https://anthropic.example"),
            [BrainMessage(role="user", content="观察并决定", image_data_url=PNG_DATA_URL)],
            client=client,
        )

        content = client.requests[0]["json"]["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "观察并决定"})
        self.assertEqual(content[1]["source"]["media_type"], "image/png")
        self.assertEqual(content[1]["source"]["data"], PNG_DATA_URL.split(",", 1)[1])

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

    async def test_google_aistudio_attaches_inline_brain_image(self) -> None:
        client = _RecordingClient({"candidates": [{"content": {"parts": [{"text": "看到了"}]}}]})

        await complete_with_provider(
            BrainProviderConfig(provider="google_aistudio", model="gemini-test", api_key="secret"),
            [BrainMessage(role="user", content="观察并决定", image_data_url=PNG_DATA_URL)],
            client=client,
        )

        parts = client.requests[0]["json"]["contents"][0]["parts"]
        self.assertEqual(parts[0], {"text": "观察并决定"})
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "image/png")
        self.assertEqual(parts[1]["inline_data"]["data"], PNG_DATA_URL.split(",", 1)[1])

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
