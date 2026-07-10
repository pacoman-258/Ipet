from __future__ import annotations

import json
import unittest
from typing import Any

from brain.decisions import BrainDecision, DecisionKind
from backend.chat_stream_flow import ChatStreamFlowDependencies, stream_chat_response


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = "message"
    data_lines: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            if data_lines:
                events.append((event_name, json.loads("\n".join(data_lines))))
            event_name = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
    if data_lines:
        events.append((event_name, json.loads("\n".join(data_lines))))
    return events


async def _collect_events(stream: Any) -> list[tuple[str, dict[str, Any]]]:
    body = ""
    async for chunk in stream:
        body += chunk
    return _sse_events(body)


class TopicStoreSpy:
    def __init__(self) -> None:
        self.truncated: list[tuple[str, int]] = []
        self.appended: list[tuple[str, str, str]] = []

    def truncate_from_assistant_turn(self, session_id: str, assistant_turn: int) -> None:
        self.truncated.append((session_id, assistant_turn))

    def append_exchange(self, session_id: str, *, user_text: str, assistant_text: str) -> None:
        self.appended.append((session_id, user_text, assistant_text))


class ChatStreamFlowTests(unittest.IsolatedAsyncioTestCase):
    def _dependencies(self, topic_store: TopicStoreSpy, **overrides: Any) -> ChatStreamFlowDependencies:
        async def run_brain_turn(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("placeholder path must not call the Brain provider")

        values = {
            "normalize_private_config": lambda: {"brain": {}, "human_ops": {}},
            "normalize_topic_id": lambda value: str(value or "default").strip() or "default",
            "normalize_provider": lambda value: str(value or "openai_compatible").strip() or "openai_compatible",
            "topic_store": topic_store,
            "sse": _sse,
            "run_brain_turn": run_brain_turn,
            "decision_from_completion": lambda completion: completion.decision,
            "sanitize_brain_error": lambda exc, _config: str(exc),
            "fallback_after_observe_brain_error": lambda observation_text, _user_text: observation_text,
            "looks_like_click_request": lambda _text: False,
            "click_coordinate_clarification_text": lambda observation_text: observation_text,
            "looks_like_desktop_action_request": lambda _text: False,
            "coerce_decision_for_human_ops": lambda _user_text, decision: decision,
            "decision_kind": lambda decision: decision.kind
            if isinstance(decision.kind, DecisionKind)
            else DecisionKind(str(decision.kind)),
            "decision_goal": lambda decision: dict(decision.payload.get("goal") or {})
            if isinstance(decision.payload.get("goal"), dict)
            else {},
            "simple_human_action_support": lambda _decision: (True, ""),
            "blocked_react_decision": lambda user_text, _decision=None: BrainDecision.say(
                f"blocked:{user_text}", goal={"status": "blocked"}
            ),
            "unsupported_simple_action_prompt": lambda **_kwargs: "unsupported",
            "create_human_ops_act_proposal": lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("placeholder path must not create proposals")
            ),
            "proposal_event_payload": lambda *_args, **_kwargs: {},
            "perform_human_ops_observe": lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("placeholder path must not observe")
            ),
            "has_partial_coordinate_pair": lambda _text: False,
            "observation_has_reviewable_click_affordance": lambda _observation: False,
            "react_followup_prompt": lambda **_kwargs: "followup",
            "computer_use_context_text": lambda _observation: "",
            "goal_status": lambda _decision, *, operation_request: "in_progress" if operation_request else "",
            "goal_is_terminal": lambda status: status in {"done", "blocked", "need_user"},
        }
        values.update(overrides)
        return ChatStreamFlowDependencies(**values)

    async def test_placeholder_path_emits_compatible_sse_and_updates_topic_store(self) -> None:
        topic_store = TopicStoreSpy()
        deps = self._dependencies(topic_store)

        events = await _collect_events(
            stream_chat_response(
                {"text": "你好", "session_id": "neo-contract", "model": "neo-model", "retry_from_assistant_turn": 2},
                deps,
            )
        )

        self.assertEqual(topic_store.truncated, [("neo-contract", 2)])
        self.assertEqual(len(topic_store.appended), 1)
        session_id, user_text, assistant_text = topic_store.appended[0]
        self.assertEqual(session_id, "neo-contract")
        self.assertEqual(user_text, "你好")
        self.assertIn("Neo Brain placeholder", assistant_text)
        event_names = [name for name, _data in events]
        self.assertEqual(event_names, ["meta", "phase", "token", "segment", "display_segment", "done"])
        meta = events[0][1]
        self.assertEqual(meta["provider"], "local_placeholder")
        self.assertEqual(meta["model"], "neo-model")
        done = events[-1][1]
        self.assertEqual(done["text"], assistant_text)
        self.assertEqual(done["provider"], "local_placeholder")
        self.assertEqual(done["model"], "neo-model")
        self.assertEqual(done["decision"]["kind"], "say")
        self.assertEqual(done["retry_from_assistant_turn"], 2)
        self.assertEqual(done["react_trace"][0]["kind"], "say")


if __name__ == "__main__":
    unittest.main()
