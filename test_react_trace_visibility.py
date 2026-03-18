from __future__ import annotations

import unittest
from unittest import mock

from backend import agent_orchestrator


class _FakeBridge:
    def list_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]

    def call_tool(self, name, arguments):
        return {"name": name, "arguments": arguments, "content": "demo"}


class ReactTraceVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_use_emits_rollup_action_and_observation_before_final(self):
        bridge = _FakeBridge()
        responses = [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": {"path": "测试.txt"}},
                    }
                ],
            },
            {"content": "已经整理好最终结果。"},
        ]

        async def fake_chat_once(**_kwargs):
            return responses.pop(0)

        items = []
        with mock.patch.object(agent_orchestrator, "chat_once", fake_chat_once):
            async for item in agent_orchestrator.stream_reply(
                messages=[{"role": "user", "content": "读取测试.txt"}],
                model="demo",
                tools_enabled=True,
                mcp_bridge=bridge,
                react_enabled=True,
                max_reasoning_steps=4,
            ):
                items.append(item)

        react_steps = [item for item in items if item.get("type") == "react_step"]
        action_texts = [item.get("text", "") for item in react_steps if item.get("phase") == "action"]
        observation_texts = [item.get("text", "") for item in react_steps if item.get("phase") == "observation"]
        final_text = "".join(item.get("delta", "") for item in items if item.get("type") == "final_delta")

        self.assertTrue(any("read_file" in text for text in action_texts))
        self.assertTrue(any("整理最终答复" in text for text in observation_texts))
        self.assertIn("最终结果", final_text)


if __name__ == "__main__":
    unittest.main()
