from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend.agent_orchestrator import TurnDecision


class ChatDualOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        backend_app.SESSION_STORE.clear()
        backend_app.PENDING_CHAT_TURNS.clear()
        backend_app._reset_agent_graph_runtime()
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()
        backend_app.SESSION_STORE.clear()
        backend_app.PENDING_CHAT_TURNS.clear()
        backend_app._reset_agent_graph_runtime()

    def test_ndjson_segments_and_display_text_are_split(self) -> None:
        async def fake_decide_turn(**_kwargs):
            return TurnDecision(
                needs_tool=False,
                thought_summary="这次我可以直接回答。",
                action_message="",
                tool_calls=[],
            )

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": '{"expr":"happy","text":"こんにちは、主さま。"}\n'}
            yield {"type": "final_delta", "delta": '**\n主人呀，我已经用中文帮你整理好了。'}

        with mock.patch.object(backend_app, "decide_turn", fake_decide_turn), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "stream_final_reply", fake_stream_final_reply):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "打个招呼",
                    "model": "demo",
                    "expression_mode": True,
                    "expression_output_format": "ndjson_v1",
                    "react_enabled": True,
                    "system_prompt": '{"character":{"name_cn":"丛雨"}}',
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("event: segment", body)
        self.assertIn("こんにちは、主さま。", body)
        self.assertIn("event: display_segment", body)
        self.assertIn("主人呀，我已经用中文帮你整理好了。", body)
        self.assertIn("event: done", body)
        self.assertIn('"text": "主人呀，我已经用中文帮你整理好了。"', body)
        self.assertEqual(
            backend_app.SESSION_STORE["default"][-1]["content"],
            "主人呀，我已经用中文帮你整理好了。",
        )

    def test_display_delimiter_allows_same_line_chinese_text(self) -> None:
        async def fake_decide_turn(**_kwargs):
            return TurnDecision(
                needs_tool=False,
                thought_summary="这次我可以直接回答。",
                action_message="",
                tool_calls=[],
            )

        async def fake_stream_final_reply(**_kwargs):
            yield {"type": "final_delta", "delta": '{"expr":"happy","text":"こんにちは。"}\n** 主人，这一段只展示不朗读。'}

        with mock.patch.object(backend_app, "decide_turn", fake_decide_turn), mock.patch.object(
            backend_app,
            "_load_runtime_tooling_config",
            return_value={"enabled": True, "max_tool_calls_per_turn": 6},
        ), mock.patch.object(backend_app, "stream_final_reply", fake_stream_final_reply):
            resp = self.client.post(
                "/api/chat/stream",
                json={
                    "text": "继续说",
                    "model": "demo",
                    "expression_mode": True,
                    "expression_output_format": "ndjson_v1",
                    "react_enabled": True,
                },
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("event: segment", body)
        self.assertIn("こんにちは。", body)
        self.assertIn("event: display_segment", body)
        self.assertIn("主人，这一段只展示不朗读。", body)
        self.assertIn('"text": "主人，这一段只展示不朗读。"', body)


if __name__ == "__main__":
    unittest.main()
