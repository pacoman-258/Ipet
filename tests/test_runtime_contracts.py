from __future__ import annotations

import unittest

import backend.app as backend_app
from backend.runtime_adapters import AstrBotRuntimeAdapter, HermesRuntimeAdapter, MockRuntimeAdapter, create_runtime_adapter
from backend.runtime_contracts import RuntimeAdapter


async def _collect_stream(adapter: RuntimeAdapter, path: str, payload: dict):
    events = []
    async for event in adapter.stream_sse(path, payload):
        events.append(event)
    return events


class RuntimeContractsTests(unittest.IsolatedAsyncioTestCase):
    def test_existing_adapters_satisfy_runtime_adapter_protocol(self) -> None:
        self.assertIsInstance(AstrBotRuntimeAdapter({}), RuntimeAdapter)
        self.assertIsInstance(HermesRuntimeAdapter({}), RuntimeAdapter)

    async def test_mock_runtime_adapter_status_request_and_stream_are_stable(self) -> None:
        adapter = MockRuntimeAdapter()

        status = await adapter.status()
        self.assertEqual(status["runtime"], "mock")
        self.assertTrue(status["ok"])
        self.assertTrue(status["available"])

        listed = await adapter.request_json("GET", "/api/skills")
        self.assertEqual(listed["runtime"], "mock")
        self.assertTrue(listed["ok"])
        self.assertIn("skills", listed)

        unsupported = await adapter.request_json("POST", "/api/unknown", json_payload={"x": 1})
        self.assertEqual(unsupported["runtime"], "mock")
        self.assertTrue(unsupported["unsupported"])
        self.assertEqual(unsupported["path"], "/api/unknown")

        events = await _collect_stream(adapter, "/api/chat/stream", {"message": "hello"})
        self.assertEqual(events[0][0], "meta")
        self.assertEqual(events[-1][0], "done")
        self.assertTrue(any(name in {"token", "segment"} for name, _payload in events))
        self.assertTrue(all(payload.get("runtime") == "mock" for _name, payload in events))
        token_payload = next(payload for name, payload in events if name == "token")
        done_payload = events[-1][1]
        self.assertEqual(token_payload["delta"], "Mock response: hello")
        self.assertEqual(done_payload["text"], "Mock response: hello")
        self.assertEqual(done_payload["topic_id"], "mock")

    def test_create_runtime_adapter_supports_explicit_and_active_mock(self) -> None:
        self.assertIsInstance(create_runtime_adapter({}, runtime_id="mock"), MockRuntimeAdapter)
        self.assertIsInstance(create_runtime_adapter({"active": "mock", "adapters": {"mock": {}}}), MockRuntimeAdapter)

    def test_app_runtime_client_resolves_active_mock_from_raw_config(self) -> None:
        client = backend_app._get_runtime_client({"runtime": {"active": "mock", "adapters": {"mock": {}}}})
        self.assertIsInstance(client, MockRuntimeAdapter)


if __name__ == "__main__":
    unittest.main()
