from __future__ import annotations

import unittest

from brain import BrainDecision, DecisionKind
from brain.llm import (
    BrainProviderConfig,
    build_turn_messages,
    parse_brain_reply,
    run_brain_turn,
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


class BrainStructuredReplyTests(unittest.TestCase):
    def test_plain_text_reply_is_classified_as_say(self) -> None:
        decision = parse_brain_reply("你好，我在。")

        self.assertEqual(decision.kind, DecisionKind.SAY)
        self.assertEqual(decision.payload["text"], "你好，我在。")
        self.assertFalse(decision.requires_review)

    def test_structured_say_reply_is_unwrapped(self) -> None:
        decision = parse_brain_reply('{"kind":"say","text":"当然可以。"}')

        self.assertEqual(decision.kind, DecisionKind.SAY)
        self.assertEqual(decision.summary, "当然可以。")
        self.assertEqual(decision.payload["text"], "当然可以。")

    def test_reserved_observe_reply_is_parsed_but_not_reviewable(self) -> None:
        decision = parse_brain_reply('{"kind":"observe","target":"screen"}')

        self.assertEqual(decision.kind, DecisionKind.OBSERVE)
        self.assertEqual(decision.payload["target"], "screen")
        self.assertFalse(decision.requires_review)

    def test_reserved_act_reply_maps_to_reviewable_proposal(self) -> None:
        decision = parse_brain_reply('{"kind":"act","action_type":"click","arguments":{"x":10,"y":20}}')

        self.assertEqual(decision.kind, DecisionKind.PROPOSE_ACT)
        self.assertTrue(decision.requires_review)
        self.assertEqual(decision.payload["action_type"], "click")
        self.assertEqual(decision.payload["arguments"]["x"], 10)

    def test_turn_prompt_contains_structured_reply_contract(self) -> None:
        messages = build_turn_messages(brain_config={"persona": "你是 Ipet。"}, user_text="你好")

        system_text = messages[0].content
        self.assertIn('"kind"', system_text)
        self.assertIn("say", system_text)
        self.assertIn("observe", system_text)
        self.assertIn("act", system_text)


class BrainStructuredTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_brain_turn_unwraps_structured_say_for_chat(self) -> None:
        client = _RecordingClient({"choices": [{"message": {"content": '{"kind":"say","text":"聊天正常。"}'}}]})

        completion = await run_brain_turn(
            {
                "provider": "openai_compatible",
                "model_endpoint": "https://llm.example/v1",
                "model_name": "neo-model",
            },
            user_text="只聊天",
            client=client,
        )

        self.assertEqual(completion.text, "聊天正常。")
        self.assertEqual(completion.raw_text, '{"kind":"say","text":"聊天正常。"}')
        self.assertIsInstance(completion.decision, BrainDecision)
        self.assertEqual(completion.decision.kind, DecisionKind.SAY)
        sent_messages = client.requests[0]["json"]["messages"]
        self.assertIn('"kind"', sent_messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
