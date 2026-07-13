from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any

from brain.decisions import BrainDecision, DecisionKind
from brain.llm import BrainLLMError
from backend.chat_stream_flow import (
    MAX_CONVERSATION_HISTORY_CHARS,
    MAX_RECENT_CONVERSATION_MESSAGES,
    ChatStreamFlowDependencies,
    _TEMPORARY_HISTORIES,
    _bounded_conversation_history,
    stream_chat_response,
    with_observation_target_app,
)
from backend.observe_context import normalize_observed_click_coordinates


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
    def __init__(self, model_messages: list[dict[str, str]] | None = None) -> None:
        self.truncated: list[tuple[str, int]] = []
        self.appended: list[tuple[str, str, str]] = []
        self.context_reads: list[str] = []
        self.operations: list[str] = []
        self.model_messages = list(model_messages or [])

    def truncate_from_assistant_turn(self, session_id: str, assistant_turn: int) -> None:
        self.truncated.append((session_id, assistant_turn))
        self.operations.append("truncate")

    def get_context_snapshot(self, session_id: str) -> Any:
        self.context_reads.append(session_id)
        self.operations.append("snapshot")
        return SimpleNamespace(model_messages=tuple(self.model_messages))

    def append_exchange(self, session_id: str, *, user_text: str, assistant_text: str) -> None:
        self.appended.append((session_id, user_text, assistant_text))
        self.operations.append("append")


class ChatStreamFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _TEMPORARY_HISTORIES.clear()

    def test_generic_chat_surface_uses_observed_app_without_assuming_wechat(self) -> None:
        decision = BrainDecision.propose_act("click", {"x": 20, "y": 30, "label": "私信入口"})

        enriched = with_observation_target_app(
            decision,
            {"surface": {"kind": "chat_gui", "app": "Google Chrome"}},
        )
        unresolved = with_observation_target_app(
            decision,
            {"surface": {"kind": "chat_gui"}},
        )

        self.assertEqual(enriched.payload["arguments"]["target_app"], "Google Chrome")
        self.assertNotIn("target_app", unresolved.payload["arguments"])

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

    async def test_streaming_brain_emits_deltas_and_exact_usage(self) -> None:
        topic_store = TopicStoreSpy()

        async def run_brain_turn(_config: dict[str, Any], **kwargs: Any) -> Any:
            on_delta = kwargs.get("on_delta")
            on_activity = kwargs.get("on_activity")
            await on_activity(
                {
                    "phase": "search",
                    "source": "brain",
                    "status_id": "codex-web-search:test",
                    "text": "正在搜索：Ipet latest",
                }
            )
            await on_delta("你")
            await on_delta("好")
            return SimpleNamespace(
                text="你好",
                decision=BrainDecision.say("你好"),
                provider="codex",
                model="gpt-test",
                usage={"input_tokens": 21, "output_tokens": 2, "total_tokens": 23},
            )

        deps = self._dependencies(
            topic_store,
            normalize_private_config=lambda: {
                "brain": {"provider": "codex", "model_name": "gpt-test", "streaming_enabled": True},
                "human_ops": {},
            },
            normalize_provider=lambda _value: "codex",
            run_brain_turn=run_brain_turn,
        )

        events = await _collect_events(stream_chat_response({"text": "hi", "session_id": "stream"}, deps))

        self.assertEqual([data["delta"] for name, data in events if name == "token"], ["你", "好"])
        self.assertEqual(
            [data["text"] for name, data in events if name == "phase" and data.get("phase") == "search"],
            ["正在搜索：Ipet latest"],
        )
        self.assertEqual(
            [data["category"] for name, data in events if name == "phase" and data.get("phase") == "search"],
            ["searching"],
        )
        self.assertNotIn("display_segment", [name for name, _data in events])
        self.assertEqual(events[-1][1]["usage"], {"input_tokens": 21, "output_tokens": 2, "total_tokens": 23})

    def test_history_character_budget_never_splits_an_exchange(self) -> None:
        history = _bounded_conversation_history(
            [
                {"role": "assistant", "content": "orphan"},
                {"role": "user", "content": "u" * 9_000},
                {"role": "assistant", "content": "a" * 9_000},
                {"role": "user", "content": "dangling"},
            ]
        )

        self.assertEqual([message["role"] for message in history], ["user", "assistant"])
        self.assertTrue(all(message["content"] for message in history))
        self.assertEqual(sum(len(message["content"]) for message in history), MAX_CONVERSATION_HISTORY_CHARS)

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

    async def test_persistent_history_keeps_summaries_and_recent_complete_exchanges(self) -> None:
        model_messages = [
            {"role": "assistant", "content": "orphan assistant"},
            {"role": "system", "content": "summary:" + ("s" * 5_000)},
        ]
        for index in range(12):
            model_messages.extend(
                [
                    {"role": "user", "content": f"user-{index}"},
                    {"role": "assistant", "content": f"assistant-{index}"},
                ]
            )
        model_messages.append({"role": "user", "content": "dangling user"})
        topic_store = TopicStoreSpy(model_messages)
        calls: list[dict[str, Any]] = []

        async def run_brain_turn(_config: dict[str, Any], **kwargs: Any) -> Any:
            calls.append(kwargs)
            return SimpleNamespace(
                text="完成。",
                decision=BrainDecision.say("完成。"),
                provider="openai_compatible",
                model="neo-model",
            )

        deps = self._dependencies(
            topic_store,
            normalize_private_config=lambda: {
                "brain": {"model_endpoint": "https://llm.example", "model_name": "neo-model"},
                "human_ops": {},
                "memory": {"conversation_saving": True},
            },
            run_brain_turn=run_brain_turn,
        )

        await _collect_events(
            stream_chat_response(
                {"text": "current", "session_id": "persistent-history", "memory_mode": "persistent"},
                deps,
            )
        )

        history = calls[0]["conversation_history"]
        summaries = [message for message in history if message["role"] == "system"]
        turns = [message for message in history if message["role"] != "system"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(len(turns), MAX_RECENT_CONVERSATION_MESSAGES)
        self.assertEqual(turns[0], {"role": "user", "content": "user-2"})
        self.assertEqual(turns[-1], {"role": "assistant", "content": "assistant-11"})
        self.assertEqual([message["role"] for message in turns], ["user", "assistant"] * 10)
        self.assertLessEqual(sum(len(message["content"]) for message in history), MAX_CONVERSATION_HISTORY_CHARS)
        self.assertEqual(topic_store.context_reads, ["persistent-history"])
        self.assertEqual(topic_store.appended, [("persistent-history", "current", "完成。")])

    async def test_react_followup_does_not_repeat_persistent_history(self) -> None:
        topic_store = TopicStoreSpy(
            [
                {"role": "user", "content": "prior question"},
                {"role": "assistant", "content": "prior answer"},
            ]
        )
        calls: list[dict[str, Any]] = []

        async def run_brain_turn(_config: dict[str, Any], **kwargs: Any) -> Any:
            calls.append(kwargs)
            decision = BrainDecision.think("再想一下") if len(calls) == 1 else BrainDecision.say("最终答案")
            return SimpleNamespace(
                text=decision.summary,
                decision=decision,
                provider="openai_compatible",
                model="neo-model",
            )

        deps = self._dependencies(
            topic_store,
            normalize_private_config=lambda: {
                "brain": {"model_endpoint": "https://llm.example", "model_name": "neo-model"},
                "human_ops": {},
            },
            run_brain_turn=run_brain_turn,
        )

        await _collect_events(stream_chat_response({"text": "继续", "session_id": "react-history"}, deps))

        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[0]["conversation_history"],
            [
                {"role": "user", "content": "prior question"},
                {"role": "assistant", "content": "prior answer"},
            ],
        )
        self.assertNotIn("conversation_history", calls[1])

    async def test_disabled_observe_model_sends_captured_image_directly_to_brain(self) -> None:
        topic_store = TopicStoreSpy()
        calls: list[dict[str, Any]] = []
        prompt_calls: list[dict[str, Any]] = []

        async def run_brain_turn(_config: dict[str, Any], **kwargs: Any) -> Any:
            calls.append(kwargs)
            decision = (
                BrainDecision.observe("screen", observe_prompt="请读取当前窗口")
                if len(calls) == 1
                else BrainDecision.say("当前窗口是设置页。")
            )
            return SimpleNamespace(
                text=decision.summary,
                decision=decision,
                provider="openai_compatible",
                model="vision-model",
            )

        async def perform_observe(_decision: BrainDecision, _config: dict[str, Any]) -> dict[str, Any]:
            return {
                "text": "截图将由 Brain 直接观察。",
                "analysis_route": "brain",
                "frame": {"data_url": "data:image/png;base64,AA=="},
                "coordinate_context": "screen coordinates",
                "surface": {"kind": "desktop_gui"},
                "affordances": [],
            }

        def react_followup_prompt(**kwargs: Any) -> str:
            prompt_calls.append(kwargs)
            return "请直接查看附图并决定下一步"

        deps = self._dependencies(
            topic_store,
            normalize_private_config=lambda: {
                "brain": {"model_endpoint": "https://llm.example", "model_name": "vision-model"},
                "human_ops": {"observe_model": {"enabled": False}},
            },
            run_brain_turn=run_brain_turn,
            perform_human_ops_observe=perform_observe,
            react_followup_prompt=react_followup_prompt,
        )

        await _collect_events(stream_chat_response({"text": "看看屏幕", "session_id": "brain-vision"}, deps))

        self.assertEqual(len(calls), 2)
        self.assertNotIn("image_data_url", calls[0])
        self.assertEqual(calls[1]["image_data_url"], "data:image/png;base64,AA==")
        self.assertTrue(prompt_calls[0]["brain_observed_image"])
        self.assertNotIn("conversation_history", calls[1])

    async def test_direct_brain_click_image_pixels_are_normalized_before_approval(self) -> None:
        topic_store = TopicStoreSpy()
        calls = 0
        captured_decisions: list[BrainDecision] = []

        async def run_brain_turn(_config: dict[str, Any], **_kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            decision = (
                BrainDecision.observe("screen", observe_prompt="定位按钮中心")
                if calls == 1
                else BrainDecision.propose_act(
                    "click",
                    {
                        "target_app": "System Settings",
                        "x": 960,
                        "y": 624,
                        "coordinate_space": "image_pixels",
                        "label": "确认按钮",
                    },
                )
            )
            return SimpleNamespace(text=decision.summary, decision=decision, provider="test", model="vision")

        async def perform_observe(_decision: BrainDecision, _config: dict[str, Any]) -> dict[str, Any]:
            return {
                "text": "截图将由 Brain 直接观察。",
                "analysis_route": "brain",
                "frame": {
                    "data_url": "data:image/png;base64,AA==",
                    "image_width": 1920,
                    "image_height": 1248,
                    "display_layout": [{"x": 0, "y": 0, "width": 1470, "height": 956}],
                },
            }

        def create_proposal(decision: BrainDecision, **_kwargs: Any) -> tuple[str, object]:
            captured_decisions.append(decision)
            return "coordinate-proposal", object()

        deps = self._dependencies(
            topic_store,
            normalize_private_config=lambda: {
                "brain": {"model_endpoint": "https://llm.example", "model_name": "vision"},
                "human_ops": {"observe_model": {"enabled": False}},
            },
            run_brain_turn=run_brain_turn,
            perform_human_ops_observe=perform_observe,
            looks_like_desktop_action_request=lambda _text: True,
            create_human_ops_act_proposal=create_proposal,
            proposal_event_payload=lambda proposal_id, _proposal: {"proposal_id": proposal_id},
            normalize_observed_click_coordinates=normalize_observed_click_coordinates,
        )

        events = await _collect_events(stream_chat_response({"text": "点击确认", "session_id": "coords"}, deps))

        arguments = captured_decisions[0].payload["arguments"]
        self.assertEqual((arguments["x"], arguments["y"]), (735, 478))
        self.assertEqual(arguments["coordinate_space"], "macos_screen_points")
        self.assertEqual(events[-1], ("approval_required", {"proposal_id": "coordinate-proposal"}))

    async def test_direct_brain_image_failure_is_blocked_with_actionable_model_guidance(self) -> None:
        topic_store = TopicStoreSpy()
        calls = 0

        async def run_brain_turn(_config: dict[str, Any], **_kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                decision = BrainDecision.observe("screen")
                return SimpleNamespace(
                    text=decision.summary,
                    decision=decision,
                    provider="openai_compatible",
                    model="text-model",
                )
            raise BrainLLMError("model does not support image input")

        async def perform_observe(_decision: BrainDecision, _config: dict[str, Any]) -> dict[str, Any]:
            return {
                "text": "截图将由 Brain 直接观察。",
                "analysis_route": "brain",
                "frame": {"data_url": "data:image/png;base64,AA=="},
                "observations": [],
                "unknowns": [],
            }

        deps = self._dependencies(
            topic_store,
            normalize_private_config=lambda: {
                "brain": {"model_endpoint": "https://llm.example", "model_name": "text-model"},
                "human_ops": {"observe_model": {"enabled": False}},
            },
            run_brain_turn=run_brain_turn,
            perform_human_ops_observe=perform_observe,
            sanitize_brain_error=lambda exc, _config: str(exc),
        )

        events = await _collect_events(stream_chat_response({"text": "看看屏幕"}, deps))

        done = events[-1][1]
        self.assertIn("Brain 无法直接读取当前截图", done["text"])
        self.assertIn("model does not support image input", done["text"])
        self.assertIn("启用独立 observe 模型", done["text"])
        self.assertEqual(done["decision"]["payload"]["goal"]["status"], "blocked")

    async def test_temporary_mode_keeps_only_recent_in_process_history_without_topic_store_io(self) -> None:
        topic_store = TopicStoreSpy()
        calls: list[dict[str, Any]] = []

        async def run_brain_turn(_config: dict[str, Any], **kwargs: Any) -> Any:
            calls.append(kwargs)
            reply = f"reply-{len(calls) - 1}"
            return SimpleNamespace(
                text=reply,
                decision=BrainDecision.say(reply),
                provider="openai_compatible",
                model="neo-model",
            )

        deps = self._dependencies(
            topic_store,
            normalize_private_config=lambda: {
                "brain": {"model_endpoint": "https://llm.example", "model_name": "neo-model"},
                "human_ops": {},
                "memory": {"conversation_saving": True},
            },
            run_brain_turn=run_brain_turn,
        )

        last_events: list[tuple[str, dict[str, Any]]] = []
        for index in range(12):
            last_events = await _collect_events(
                stream_chat_response(
                    {"text": f"temp-{index}", "session_id": "temporary-history", "memory_mode": "temporary"},
                    deps,
                )
            )

        self.assertNotIn("conversation_history", calls[0])
        self.assertEqual(len(calls[-1]["conversation_history"]), MAX_RECENT_CONVERSATION_MESSAGES)
        self.assertEqual(calls[-1]["conversation_history"][0], {"role": "user", "content": "temp-1"})
        self.assertEqual(calls[-1]["conversation_history"][-1], {"role": "assistant", "content": "reply-10"})
        self.assertEqual(topic_store.context_reads, [])
        self.assertEqual(topic_store.truncated, [])
        self.assertEqual(topic_store.appended, [])
        self.assertEqual(last_events[0][1]["memory_mode"], "temporary")
        self.assertEqual(last_events[-1][1]["memory_mode"], "temporary")

    async def test_disabled_conversation_saving_forces_temporary_and_preserves_legacy_empty_call(self) -> None:
        topic_store = TopicStoreSpy()
        calls: list[str] = []

        async def run_brain_turn(
            _config: dict[str, Any],
            *,
            user_text: str,
            request_system_prompt: str = "",
        ) -> Any:
            calls.append(f"{request_system_prompt}:{user_text}")
            return SimpleNamespace(
                text="不保存。",
                decision=BrainDecision.say("不保存。"),
                provider="openai_compatible",
                model="neo-model",
            )

        deps = self._dependencies(
            topic_store,
            normalize_private_config=lambda: {
                "brain": {"model_endpoint": "https://llm.example", "model_name": "neo-model"},
                "human_ops": {},
                "memory": {"conversation_saving": False},
            },
            run_brain_turn=run_brain_turn,
        )

        events = await _collect_events(
            stream_chat_response(
                {
                    "text": "隐私对话",
                    "session_id": "saving-disabled",
                    "memory_mode": "persistent",
                    "retry_from_assistant_turn": 1,
                },
                deps,
            )
        )

        self.assertEqual(calls, [":隐私对话"])
        self.assertEqual(topic_store.operations, [])
        self.assertEqual(events[0][1]["memory_mode"], "temporary")
        self.assertEqual(events[-1][1]["memory_mode"], "temporary")


if __name__ == "__main__":
    unittest.main()
