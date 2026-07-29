from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any

from brain.decisions import BrainDecision
from backend.task_control import TASK_CONTROL, TaskStopped
from human_ops.approvals import ReviewableProposal
from human_ops.approval_flow import (
    HumanOpsApprovalFlowDependencies,
    _action_retry_key,
    execution_verification,
    near_match_confirmation_decision,
    observation_confirms_expected_draft,
    stream_human_ops_proposal_decision,
)
from human_ops.proposals import (
    proposal_event_payload,
    with_inherited_enter_expected_text,
)


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


def _proposal_label(proposal: ReviewableProposal) -> str:
    action_type = str(proposal.payload.get("action_type") or "").strip()
    args = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    return f"{action_type}:{args.get('label') or args.get('target') or ''}"


def _decision_kind(decision: BrainDecision):
    return decision.kind


def _goal_status(decision: BrainDecision, *, operation_request: bool) -> str:
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else {}
    return str(goal.get("status") or ("in_progress" if operation_request else "")).strip()


class HumanOpsApprovalFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_near_match_requires_explicit_user_confirmation(self) -> None:
        decision = near_match_confirmation_decision(
            {
                "ax_search": {
                    "insufficiency_reason": "near_match_requires_confirmation",
                    "near_match_labels": ["测试联系人"],
                }
            },
            "打开测试联系入会话",
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.payload["goal"]["status"], "need_user")
        self.assertEqual(
            decision.payload["goal"]["next"],
            "confirm_near_match",
        )
        self.assertIn("测试联系人", decision.summary)

    def test_type_text_retry_key_survives_ax_path_and_fingerprint_churn(self) -> None:
        first = {
            "action_type": "type_text",
            "arguments": {
                "target_app": "QQ",
                "text": "目标会话",
                "replace_existing": True,
                "ax_ref": {
                    "app_id": "qq",
                    "role": "AXTextField",
                    "input_kind": "search_field",
                    "path": [1, 2, 3],
                    "bounds": {"x": 739, "y": 154, "width": 138, "height": 20},
                    "fingerprint": "before-overlay",
                },
            },
        }
        repeated = {
            "action_type": "type_text",
            "arguments": {
                "target_app": "QQ",
                "text": "目标会话",
                "replace_existing": False,
                "ax_ref": {
                    "app_id": "qq",
                    "role": "AXTextField",
                    "input_kind": "search_field",
                    "path": [1, 4, 8],
                    "bounds": {"x": 739.0, "y": 154.0, "width": 138.0, "height": 20.0},
                    "fingerprint": "after-overlay",
                },
            },
        }

        self.assertEqual(_action_retry_key(first), _action_retry_key(repeated))
        repeated["arguments"]["text"] = "另一个目标"
        self.assertNotEqual(_action_retry_key(first), _action_retry_key(repeated))

    def test_execution_verification_uses_native_evidence_before_visual_fallback(self) -> None:
        click = ReviewableProposal.act(
            action_type="click",
            summary="点击输入框",
            payload={"target_app": "WeChat", "x": 20, "y": 30},
        )
        send = ReviewableProposal.act(
            action_type="key_press",
            summary="发送消息",
            payload={"target_app": "WeChat", "key": "enter", "expected_text": "你好"},
        )

        click_result = execution_verification(
            click,
            {"clicked": True, "focused": True, "frontmost_app": "WeChat"},
        )
        send_result = execution_verification(
            send,
            {"pressed": True, "focused": True, "frontmost_app": "WeChat"},
        )
        type_text = ReviewableProposal.act(
            action_type="type_text",
            summary="输入草稿",
            payload={"target_app": "WeChat", "text": "你好"},
        )
        type_result = execution_verification(
            type_text,
            {"typed": True, "focused": True, "frontmost_app": "WeChat"},
        )
        playwright = ReviewableProposal.act(
            action_type="playwright",
            summary="打开网页",
            payload={"profile": "工作", "operation": "open", "url": "https://example.com"},
        )
        playwright_result = execution_verification(
            playwright,
            {"playwright_done": True, "output": "page snapshot"},
        )
        file_read = ReviewableProposal.act(
            action_type="file_read",
            summary="读取说明文件",
            payload={"path": "README.md"},
        )
        file_result = execution_verification(file_read, {"read": True, "path": "README.md", "content": "ok"})
        semantic_click = ReviewableProposal.act(
            action_type="click",
            summary="点击发送",
            payload={
                "target_app": "WeChat",
                "ax_ref": {
                    "app_id": "wechat",
                    "role": "AXButton",
                    "path": [1],
                    "fingerprint": "copied",
                },
            },
        )
        semantic_result = execution_verification(
            semantic_click,
            {
                "clicked": True,
                "focused": True,
                "ax_target_verified": True,
                "ax_action_performed": True,
            },
        )
        already_satisfied_result = execution_verification(
            semantic_click,
            {
                "clicked": False,
                "already_satisfied": True,
                "postcondition_verified": True,
                "focused": True,
                "ax_target_verified": True,
                "ax_action_performed": False,
            },
        )

        self.assertEqual(click_result["status"], "insufficient")
        self.assertTrue(click_result["requires_visual"])
        self.assertIn("post-action interface state", click_result["reason"])
        self.assertEqual(send_result["status"], "insufficient")
        self.assertTrue(send_result["requires_visual"])
        self.assertIn("expected message", send_result["reason"])
        self.assertEqual(type_result["status"], "insufficient")
        self.assertTrue(type_result["requires_visual"])
        self.assertIn("expected draft", type_result["reason"])
        self.assertEqual(playwright_result["status"], "verified")
        self.assertFalse(playwright_result["requires_visual"])
        self.assertEqual(file_result["status"], "verified")
        self.assertFalse(file_result["requires_visual"])
        self.assertEqual(semantic_result["status"], "insufficient")
        self.assertTrue(semantic_result["requires_visual"])
        self.assertEqual(semantic_result["method"], "macos_accessibility_action_and_native_state")
        self.assertEqual(already_satisfied_result["status"], "verified")
        self.assertFalse(already_satisfied_result["requires_visual"])

    def test_expected_draft_requires_real_ax_or_visual_evidence(self) -> None:
        expected = "你好，明天下午见"
        self.assertTrue(
            observation_confirms_expected_draft(
                {
                    "analysis_route": "structured",
                    "frame": {
                        "accessibility": {
                            "elements": [
                                {
                                    "role": "AXTextArea",
                                    "value": expected,
                                }
                            ]
                        }
                    },
                },
                expected,
            )
        )
        self.assertTrue(
            observation_confirms_expected_draft(
                {
                    "observations": [
                        {"claim": f"聊天输入框中完整显示“{expected}”"}
                    ]
                },
                expected,
            )
        )
        self.assertFalse(
            observation_confirms_expected_draft(
                {
                    "text": f"请验证草稿“{expected}”",
                    "frame": {
                        "accessibility": {
                            "search": {"query": expected},
                            "elements": [],
                        }
                    },
                },
                expected,
            )
        )
        self.assertFalse(
            observation_confirms_expected_draft(
                {
                    "analysis_route": "brain",
                    "frame": {"data_url": "data:image/png;base64,AAAA"},
                    "chat_context": {"contact": "另一个会话"},
                    "observations": [
                        {"claim": "输入框里是另一段草稿"}
                    ],
                },
                expected,
                expected_chat="目标会话",
            )
        )

    def test_expected_draft_rejects_wrong_live_chat_header(self) -> None:
        expected_text = "准备发送的草稿"
        input_ref = {
            "app_id": "qq",
            "role": "AXTextArea",
            "identifier": "message-input",
            "path": [0, 1],
            "fingerprint": "copied",
        }
        observation = {
            "frame": {
                "accessibility": {
                    "elements": [
                        {
                            "role": "AXWindow",
                            "tree_path": [0],
                            "bounds": {
                                "x": 100,
                                "y": 80,
                                "width": 900,
                                "height": 700,
                            },
                        },
                        {
                            "role": "AXStaticText",
                            "label": "另一个会话",
                            "tree_path": [0, 0],
                            "bounds": {
                                "x": 560,
                                "y": 112,
                                "width": 120,
                                "height": 24,
                            },
                        },
                        {
                            "role": "AXTextArea",
                            "identifier": "message-input",
                            "value": expected_text,
                            "tree_path": [0, 1],
                            "bounds": {
                                "x": 390,
                                "y": 590,
                                "width": 590,
                                "height": 120,
                            },
                        },
                        {
                            "role": "AXRow",
                            "label": "目标会话",
                            "tree_path": [0, 2],
                            "bounds": {
                                "x": 110,
                                "y": 220,
                                "width": 250,
                                "height": 64,
                            },
                        },
                    ]
                }
            }
        }

        self.assertFalse(
            observation_confirms_expected_draft(
                observation,
                expected_text,
                expected_chat="目标会话",
                input_ax_ref=input_ref,
            )
        )

    def _dependencies(self, pending: dict[str, dict[str, Any]], **overrides: Any) -> HumanOpsApprovalFlowDependencies:
        async def perform_action(proposal: ReviewableProposal) -> dict[str, Any]:
            args = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
            return {"ok": True, **dict(args)}

        async def perform_observe(_decision: BrainDecision, _config: dict[str, Any]) -> dict[str, Any]:
            return {"text": "观察完成。", "observations": [], "unknowns": []}

        async def run_brain_turn(_config: dict[str, Any], *, user_text: str):
            return SimpleNamespace(text="完成。", decision=BrainDecision.say("完成。", goal={"status": "done"}))

        next_proposal = ReviewableProposal.act(
            action_type="key_press",
            summary="Ipet 想按下回车：发送",
            payload={"key": "enter", "label": "发送"},
        )

        values = {
            "pending_proposals": pending,
            "sse": _sse,
            "proposal_tool_label": _proposal_label,
            "perform_human_ops_action": perform_action,
            "proposal_continue_after_approval": lambda proposal: False,
            "normalize_private_config": lambda: {"brain": {}, "human_ops": {}},
            "perform_human_ops_observe": perform_observe,
            "post_approval_observe_prompt": lambda user_text, proposal: f"观察：{user_text}:{proposal.summary}",
            "sanitize_brain_error": lambda exc, _config: str(exc),
            "human_ops_continuation_prompt": lambda **kwargs: f"继续：{kwargs['observation']['text']}",
            "run_brain_turn": run_brain_turn,
            "decision_from_completion": lambda completion: completion.decision,
            "decision_kind": _decision_kind,
            "simple_human_action_support": lambda decision: (True, ""),
            "blocked_react_decision": lambda user_text, decision=None: BrainDecision.say(
                f"blocked:{user_text}", goal={"status": "blocked"}
            ),
            "unsupported_simple_action_prompt": lambda **kwargs: "unsupported",
            "with_inherited_enter_expected_text": lambda decision, **kwargs: decision,
            "create_human_ops_act_proposal": lambda decision, *, session_id, user_text: ("next-proposal", next_proposal),
            "proposal_event_payload": proposal_event_payload,
            "react_followup_prompt": lambda **kwargs: "react followup",
            "computer_use_context_text": lambda observation: "",
            "goal_status": _goal_status,
            "goal_is_terminal": lambda status: status in {"done", "blocked", "need_user"},
        }
        values.update(overrides)
        return HumanOpsApprovalFlowDependencies(**values)

    async def test_reject_decision_preserves_sse_shape_and_marks_record_rejected(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="click",
            summary="Ipet 想点击：确认按钮",
            payload={"x": 12, "y": 34, "label": "确认按钮"},
        )
        pending = {
            "proposal-1": {
                "proposal": proposal,
                "session_id": "neo-session",
                "user_text": "点确认按钮",
                "status": "pending",
            }
        }
        refresh_calls: list[str] = []
        deps = self._dependencies(
            pending,
            schedule_accessibility_index_refresh=lambda: refresh_calls.append("refresh") or True,
        )

        events = await _collect_events(
            stream_human_ops_proposal_decision(
                "proposal-1",
                {"approved": False, "user_text": "先不要点"},
                deps,
            )
        )

        self.assertEqual([name for name, _data in events], ["meta", "display_segment", "done"])
        self.assertEqual(pending["proposal-1"]["status"], "rejected")
        done = events[-1][1]
        self.assertEqual(done["turn_id"], "proposal-1")
        self.assertEqual(done["proposal_id"], "proposal-1")
        self.assertEqual(done["session_id"], "neo-session")
        self.assertEqual(done["approved"], False)
        self.assertIsNone(done["execution"])
        self.assertIn("调整说明：先不要点", done["text"])
        self.assertTrue(done["accessibility_index_refresh_scheduled"])
        self.assertEqual(refresh_calls, ["refresh"])

    async def test_memory_approval_uses_memory_store_callback_without_body_action(self) -> None:
        proposal = ReviewableProposal.remember(
            summary="Ipet 想保存这条关系记忆：我不喜欢被催促。",
            payload={
                "operation": "save",
                "memory": {"summary": "我不喜欢被催促。", "kind": "boundary"},
            },
        )
        pending = {
            "memory-1": {
                "proposal": proposal,
                "session_id": "friend",
                "user_text": "记住我不喜欢被催",
                "status": "pending",
                "origin": "explicit",
            }
        }
        exchanges: list[dict[str, str]] = []

        async def body_action(_proposal: ReviewableProposal) -> dict[str, Any]:
            raise AssertionError("memory approval must not execute a Body action")

        deps = self._dependencies(
            pending,
            perform_human_ops_action=body_action,
            proposal_tool_label=lambda _proposal: "保存 boundary：我不喜欢被催促。",
            perform_memory_operation=lambda approved: {
                "ok": approved.approved,
                "saved": True,
                "operation": "save",
            },
            record_memory_review_exchange=lambda **kwargs: exchanges.append(kwargs),
        )

        events = await _collect_events(
            stream_human_ops_proposal_decision("memory-1", {"approved": True}, deps)
        )

        self.assertEqual(pending["memory-1"]["status"], "executed")
        self.assertEqual(events[-1][1]["execution"]["saved"], True)
        self.assertIn("记住了", events[-1][1]["text"])
        self.assertEqual(exchanges[0]["session_id"], "friend")

    async def test_approve_continuation_uses_injected_dependencies_and_emits_next_approval(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="type_text",
            summary="Ipet 想输入到聊天框：你好",
            payload={"target_app": "WeChat", "text": "你好", "label": "聊天框", "continue_after_approval": True},
        )
        pending = {
            "proposal-1": {
                "proposal": proposal,
                "session_id": "neo-session",
                "user_text": "回复消息",
                "status": "pending",
            }
        }
        calls: dict[str, Any] = {"action": 0, "observe": 0, "brain_prompt": "", "create": None}

        async def perform_action(approved_proposal: ReviewableProposal) -> dict[str, Any]:
            calls["action"] += 1
            self.assertTrue(approved_proposal.approved)
            return {
                "typed": True,
                "focused": True,
                "frontmost_app": "WeChat",
                "target_app": "WeChat",
                "text": "你好",
            }

        async def perform_observe(decision: BrainDecision, _config: dict[str, Any]) -> dict[str, Any]:
            calls["observe"] += 1
            self.assertEqual(decision.payload["target_app"], "WeChat")
            return {
                "text": "目标会话的聊天框中可见完整草稿：你好。",
                "analysis_route": "structured",
                "frame": {
                    "accessibility": {
                        "elements": [
                            {"role": "AXTextArea", "value": "你好"}
                        ]
                    }
                },
                "observations": [],
                "unknowns": [],
            }

        async def run_brain_turn(_config: dict[str, Any], *, user_text: str):
            calls["brain_prompt"] = user_text
            return SimpleNamespace(
                text="按回车发送",
                decision=BrainDecision.propose_act(
                    "key_press",
                    {"key": "enter", "label": "发送"},
                    goal={"status": "handoff_review", "stage": "send_reply", "next": "verify_result"},
                ),
            )

        def create_next(decision: BrainDecision, *, session_id: str, user_text: str):
            calls["create"] = (decision, session_id, user_text)
            next_proposal = ReviewableProposal.act(
                action_type="key_press",
                summary="Ipet 想按下回车：发送",
                payload={"key": "enter", "label": "发送"},
            )
            return "next-proposal", next_proposal

        deps = self._dependencies(
            pending,
            perform_human_ops_action=perform_action,
            proposal_continue_after_approval=lambda _proposal: True,
            perform_human_ops_observe=perform_observe,
            post_approval_observe_prompt=lambda user_text, _proposal: f"执行后观察 {user_text}",
            human_ops_continuation_prompt=lambda **kwargs: f"继续任务：{kwargs['observation']['text']}",
            run_brain_turn=run_brain_turn,
            create_human_ops_act_proposal=create_next,
        )

        events = await _collect_events(
            stream_human_ops_proposal_decision(
                "proposal-1",
                {"approved": True},
                deps,
            )
        )

        self.assertEqual(calls["action"], 1)
        self.assertEqual(calls["observe"], 1)
        self.assertIn("目标会话的聊天框中可见完整草稿", calls["brain_prompt"])
        created_decision, session_id, user_text = calls["create"]
        self.assertEqual(created_decision.payload["action_type"], "key_press")
        self.assertEqual(session_id, "neo-session")
        self.assertEqual(user_text, "回复消息")
        self.assertEqual(pending["proposal-1"]["status"], "executed")
        event_names = [name for name, _data in events]
        self.assertEqual(event_names[:4], ["meta", "phase", "display_segment", "phase"])
        self.assertEqual(event_names[-2:], ["phase", "approval_required"])
        phase_payloads = [data for name, data in events if name == "phase"]
        self.assertEqual(
            [data["category"] for data in phase_payloads],
            ["acting", "verifying", "verifying", "waiting_approval"],
        )
        self.assertEqual(phase_payloads[1]["name"], "human_ops_structured_verify")
        self.assertEqual(phase_payloads[1]["task"], "执行后观察 回复消息")
        self.assertEqual(phase_payloads[2]["name"], "human_ops_observe")
        self.assertEqual(phase_payloads[-1]["status_id"], "approval:next-proposal")
        self.assertEqual(events[-1][1]["proposal_id"], "next-proposal")
        self.assertEqual(events[-1][1]["action_type"], "key_press")
        self.assertNotIn("done", event_names)

    async def test_approval_continuation_stops_at_structured_near_match_without_brain(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="click",
            summary="Ipet 想点击：会话入口",
            payload={
                "target_app": "QQ",
                "x": 120,
                "y": 180,
                "label": "会话入口",
                "continue_after_approval": True,
            },
        )
        pending = {
            "near-match": {
                "proposal": proposal,
                "session_id": "near-session",
                "user_text": "打开测试联系入会话",
                "status": "pending",
            }
        }

        async def perform_action(
            _proposal: ReviewableProposal,
        ) -> dict[str, Any]:
            return {
                "clicked": True,
                "focused": True,
                "target_app": "QQ",
            }

        async def perform_observe(
            _decision: BrainDecision,
            _config: dict[str, Any],
        ) -> dict[str, Any]:
            return {
                "text": "找到一个近似名称，需要用户确认。",
                "analysis_route": "structured",
                "ax_search": {
                    "insufficiency_reason": "near_match_requires_confirmation",
                    "near_match_labels": ["测试联系人"],
                    "visual_fallback_required": False,
                },
                "observations": [],
                "unknowns": [],
            }

        async def reject_brain(*_args: Any, **_kwargs: Any) -> Any:
            self.fail("a structured near match must not call Brain again")

        deps = self._dependencies(
            pending,
            perform_human_ops_action=perform_action,
            proposal_continue_after_approval=lambda _proposal: True,
            perform_human_ops_observe=perform_observe,
            run_brain_turn=reject_brain,
        )

        events = await _collect_events(
            stream_human_ops_proposal_decision(
                "near-match",
                {"approved": True},
                deps,
            )
        )

        self.assertEqual(
            pending["near-match"]["status"],
            "needs_user_confirmation",
        )
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(
            events[-1][1]["decision"]["payload"]["goal"]["status"],
            "need_user",
        )
        self.assertIn("测试联系人", events[-1][1]["text"])

    async def test_unverified_identical_action_is_not_replayed(self) -> None:
        ax_ref = {
            "app_id": "qq",
            "role": "AXGroup",
            "path": [1, 2, 3],
            "fingerprint": "same-chat-row",
        }
        proposal = ReviewableProposal.act(
            action_type="click",
            summary="进入目标会话",
            payload={
                "target_app": "QQ",
                "label": "目标会话",
                "ax_ref": ax_ref,
                "continue_after_approval": True,
            },
        )
        pending = {
            "repeat-first": {
                "proposal": proposal,
                "session_id": "repeat-session",
                "user_text": "进入目标会话",
                "status": "pending",
                "react_budget_remaining": 2,
            }
        }
        brain_prompts: list[str] = []

        async def perform_action(
            _proposal: ReviewableProposal,
        ) -> dict[str, Any]:
            return {
                "clicked": True,
                "focused": True,
                "ax_target_verified": True,
                "ax_action_performed": True,
                "frontmost_app": "QQ",
            }

        async def run_brain_turn(
            _config: dict[str, Any],
            *,
            user_text: str,
        ) -> Any:
            brain_prompts.append(user_text)
            if len(brain_prompts) == 1:
                return SimpleNamespace(
                    text="再次点击目标会话",
                    decision=BrainDecision.propose_act(
                        "click",
                        {
                            "target_app": "QQ",
                            "label": "目标会话",
                            "ax_ref": ax_ref,
                        },
                    ),
                )
            return SimpleNamespace(
                text="当前浮层状态不明，停止重复点击。",
                decision=BrainDecision.say(
                    "当前浮层状态不明，停止重复点击。",
                    goal={"status": "blocked"},
                ),
            )

        deps = self._dependencies(
            pending,
            perform_human_ops_action=perform_action,
            proposal_continue_after_approval=lambda _proposal: True,
            run_brain_turn=run_brain_turn,
            create_human_ops_act_proposal=lambda *_args, **_kwargs: self.fail(
                "an unverified identical action must not be enqueued"
            ),
        )

        events = await _collect_events(
            stream_human_ops_proposal_decision(
                "repeat-first",
                {"approved": True},
                deps,
            )
        )

        self.assertEqual(len(brain_prompts), 2)
        self.assertIn("不得原样重复该动作", brain_prompts[1])
        self.assertTrue(
            any(
                name == "phase"
                and payload.get("text")
                == "上一动作未验证生效，Brain 正在避免重复同一目标"
                for name, payload in events
            )
        )
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(
            events[-1][1]["decision"]["payload"]["goal"]["status"],
            "blocked",
        )

    async def test_continuation_failure_is_not_overwritten_by_action_success(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="click",
            summary="Ipet 想点击：目标",
            payload={
                "x": 12,
                "y": 34,
                "label": "目标",
                "continue_after_approval": True,
            },
        )
        pending = {
            "proposal-continuation-error": {
                "proposal": proposal,
                "session_id": "neo-session",
                "user_text": "继续完成任务",
                "status": "pending",
            }
        }

        async def run_brain_turn(_config: dict[str, Any], *, user_text: str):
            return SimpleNamespace(
                text="继续点击",
                decision=BrainDecision.propose_act(
                    "click",
                    {"x": 40, "y": 50, "label": "下一步"},
                    goal={"status": "handoff_review"},
                ),
            )

        def broken_adapter(_decision: BrainDecision, **_kwargs: Any) -> BrainDecision:
            raise TypeError("adapter signature mismatch")

        deps = self._dependencies(
            pending,
            proposal_continue_after_approval=lambda _proposal: True,
            run_brain_turn=run_brain_turn,
            with_inherited_enter_expected_text=broken_adapter,
        )

        events = await _collect_events(
            stream_human_ops_proposal_decision(
                "proposal-continuation-error",
                {"approved": True},
                deps,
            )
        )

        done = events[-1][1]
        failed_phase = next(
            payload
            for name, payload in events
            if name == "phase" and payload.get("name") == "human_ops_continuation"
        )
        self.assertEqual(pending["proposal-continuation-error"]["status"], "continuation_failed")
        self.assertEqual(failed_phase["status"], "failed")
        self.assertIn("adapter signature mismatch", done["text"])
        self.assertTrue(done["execution"]["ok"])
        self.assertFalse(done["execution"]["continuation_ok"])

    async def test_chat_send_transaction_survives_intermediate_action(self) -> None:
        alice_ref = {
            "app_id": "qq",
            "role": "AXTextArea",
            "path": [0, 2],
            "fingerprint": "alice",
        }
        bob_ref = {
            "app_id": "qq",
            "role": "AXTextArea",
            "path": [0, 3],
            "fingerprint": "bob",
        }
        intermediate = ReviewableProposal.act(
            action_type="click",
            summary="打开表情面板",
            payload={
                "target_app": "QQ",
                "label": "表情",
                "continue_after_approval": True,
            },
        )
        pending = {
            "intermediate": {
                "proposal": intermediate,
                "session_id": "chat-session",
                "user_text": "给 Alice 发送消息",
                "status": "pending",
                "chat_send_transaction": {
                    "target_app": "QQ",
                    "intended_chat": "Alice",
                    "input_ax_ref": alice_ref,
                    "expected_text": "只发给 Alice",
                },
            }
        }
        created_decisions: list[BrainDecision] = []

        async def perform_action(
            _proposal: ReviewableProposal,
        ) -> dict[str, Any]:
            return {
                "clicked": True,
                "focused": True,
                "postcondition_verified": True,
                "target_app": "QQ",
                "frontmost_app": "QQ",
            }

        async def run_brain_turn(_config: dict[str, Any], **_kwargs: Any):
            decision = BrainDecision.propose_act(
                "key_press",
                {
                    "key": "enter",
                    "target_app": "QQ",
                    "intended_chat": "Bob",
                    "input_ax_ref": bob_ref,
                    "expected_text": "发给 Bob",
                },
            )
            return SimpleNamespace(text="发送", decision=decision)

        def create_next(
            decision: BrainDecision,
            *,
            session_id: str,
            user_text: str,
        ) -> tuple[str, ReviewableProposal]:
            created_decisions.append(decision)
            arguments = dict(decision.payload["arguments"])
            next_proposal = ReviewableProposal.act(
                action_type="key_press",
                summary="发送",
                payload=arguments,
            )
            pending["send"] = {
                "proposal": next_proposal,
                "session_id": session_id,
                "user_text": user_text,
                "status": "pending",
            }
            return "send", next_proposal

        deps = self._dependencies(
            pending,
            perform_human_ops_action=perform_action,
            proposal_continue_after_approval=lambda _proposal: True,
            run_brain_turn=run_brain_turn,
            with_inherited_enter_expected_text=with_inherited_enter_expected_text,
            create_human_ops_act_proposal=create_next,
        )

        events = await _collect_events(
            stream_human_ops_proposal_decision(
                "intermediate",
                {"approved": True},
                deps,
            )
        )

        arguments = created_decisions[0].payload["arguments"]
        self.assertEqual(arguments["target_app"], "QQ")
        self.assertEqual(arguments["intended_chat"], "Alice")
        self.assertEqual(arguments["input_ax_ref"], alice_ref)
        self.assertEqual(arguments["expected_text"], "只发给 Alice")
        self.assertEqual(
            pending["send"]["chat_send_transaction"]["intended_chat"],
            "Alice",
        )
        self.assertEqual(events[-1][0], "approval_required")

    async def test_enter_is_held_until_a_followup_observation_proves_the_draft(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="type_text",
            summary="输入草稿",
            payload={
                "target_app": "QQ",
                "text": "测试草稿",
                "continue_after_approval": True,
            },
        )
        pending = {
            "draft-first": {
                "proposal": proposal,
                "session_id": "draft-session",
                "user_text": "在目标会话发送测试草稿",
                "status": "pending",
                "react_budget_remaining": 5,
            }
        }
        observe_calls = 0
        brain_prompts: list[str] = []

        async def perform_action(
            _proposal: ReviewableProposal,
        ) -> dict[str, Any]:
            return {
                "typed": True,
                "focused": True,
                "target_app": "QQ",
                "frontmost_app": "QQ",
            }

        async def perform_observe(
            decision: BrainDecision,
            _config: dict[str, Any],
        ) -> dict[str, Any]:
            nonlocal observe_calls
            observe_calls += 1
            if observe_calls == 1:
                return {
                    "text": "请验证测试草稿，但当前没有读到输入框值。",
                    "analysis_route": "structured",
                    "frame": {
                        "accessibility": {
                            "search": {"query": "测试草稿"},
                            "elements": [],
                        }
                    },
                    "observations": [],
                    "unknowns": ["draft value unavailable"],
                }
            self.assertEqual(decision.payload["ax_query"], "聊天输入框 测试草稿")
            return {
                "text": "重新观察已读到草稿。",
                "analysis_route": "structured",
                "frame": {
                    "accessibility": {
                        "elements": [
                            {"role": "AXTextArea", "value": "测试草稿"}
                        ]
                    }
                },
                "observations": [],
                "unknowns": [],
            }

        async def run_brain_turn(
            _config: dict[str, Any],
            *,
            user_text: str,
        ) -> Any:
            brain_prompts.append(user_text)
            if len(brain_prompts) == 2:
                return SimpleNamespace(
                    text="重新观察草稿",
                    decision=BrainDecision.observe(
                        "screen",
                        target_app="QQ",
                        ax_query="聊天输入框 测试草稿",
                    ),
                )
            return SimpleNamespace(
                text="按回车发送",
                decision=BrainDecision.propose_act(
                    "key_press",
                    {"target_app": "QQ", "key": "enter", "label": "发送"},
                ),
            )

        def create_next(
            _decision: BrainDecision,
            *,
            session_id: str,
            user_text: str,
        ) -> tuple[str, ReviewableProposal]:
            next_proposal = ReviewableProposal.act(
                action_type="key_press",
                summary="发送草稿",
                payload={"target_app": "QQ", "key": "enter"},
            )
            pending["draft-send"] = {
                "proposal": next_proposal,
                "session_id": session_id,
                "user_text": user_text,
                "status": "pending",
            }
            return "draft-send", next_proposal

        deps = self._dependencies(
            pending,
            perform_human_ops_action=perform_action,
            proposal_continue_after_approval=lambda _proposal: True,
            perform_human_ops_observe=perform_observe,
            run_brain_turn=run_brain_turn,
            create_human_ops_act_proposal=create_next,
        )

        events = await _collect_events(
            stream_human_ops_proposal_decision(
                "draft-first",
                {"approved": True},
                deps,
            )
        )

        self.assertEqual(observe_calls, 2)
        self.assertEqual(len(brain_prompts), 3)
        self.assertIn("因此不能提交 Enter/Return", brain_prompts[1])
        self.assertEqual(events[-1][0], "approval_required")
        self.assertEqual(events[-1][1]["proposal_id"], "draft-send")

    async def test_full_authorization_notifies_and_executes_each_continuation_action(self) -> None:
        first = ReviewableProposal.act(
            action_type="type_text",
            summary="Ipet 想输入到聊天框",
            payload={
                "target_app": "WeChat",
                "text": "你好",
                "label": "聊天框",
                "continue_after_approval": True,
            },
        )
        second = ReviewableProposal.act(
            action_type="key_press",
            summary="Ipet 想按下回车",
            payload={"target_app": "WeChat", "key": "enter", "label": "发送"},
        )
        pending = {
            "auto-first": {
                "proposal": first,
                "session_id": "auto-session",
                "user_text": "回复消息",
                "task_id": "auto-task",
                "status": "pending",
                "authorization_mode": "full",
                "react_budget_remaining": 7,
            }
        }
        calls: list[tuple[str, str]] = []

        async def notify(proposal: ReviewableProposal, *, task_id: str) -> dict[str, Any]:
            calls.append(("notify", str(proposal.payload.get("action_type"))))
            self.assertEqual(task_id, "auto-task")
            return {"notified": True, "method": "test"}

        async def perform_action(proposal: ReviewableProposal) -> dict[str, Any]:
            action_type = str(proposal.payload.get("action_type") or "")
            calls.append(("action", action_type))
            if action_type == "type_text":
                return {"typed": True, "focused": True, "frontmost_app": "WeChat"}
            return {"pressed": True, "focused": True, "frontmost_app": "WeChat"}

        async def run_brain_turn(_config: dict[str, Any], **_kwargs: Any):
            return SimpleNamespace(text=second.summary, decision=BrainDecision.propose_act(
                "key_press",
                {"target_app": "WeChat", "key": "enter", "label": "发送"},
            ))

        async def perform_observe(
            _decision: BrainDecision,
            _config: dict[str, Any],
        ) -> dict[str, Any]:
            return {
                "text": "聊天输入框中已显示完整草稿。",
                "analysis_route": "structured",
                "frame": {
                    "accessibility": {
                        "elements": [
                            {"role": "AXTextArea", "value": "你好"}
                        ]
                    }
                },
                "observations": [],
                "unknowns": [],
            }

        def create_next(_decision: BrainDecision, *, session_id: str, user_text: str):
            pending["auto-second"] = {
                "proposal": second,
                "session_id": session_id,
                "user_text": user_text,
                "status": "pending",
            }
            return "auto-second", second

        deps = self._dependencies(
            pending,
            notify_human_ops_action=notify,
            perform_human_ops_action=perform_action,
            proposal_continue_after_approval=lambda proposal: bool(
                proposal.payload.get("arguments", {}).get("continue_after_approval")
            ),
            normalize_private_config=lambda: {
                "brain": {},
                "human_ops": {"authorization_mode": "full", "require_act_review": False},
            },
            perform_human_ops_observe=perform_observe,
            run_brain_turn=run_brain_turn,
            create_human_ops_act_proposal=create_next,
        )

        events = await _collect_events(
            stream_human_ops_proposal_decision("auto-first", {"approved": True}, deps)
        )

        self.assertEqual(
            calls,
            [
                ("notify", "type_text"),
                ("action", "type_text"),
                ("notify", "key_press"),
                ("action", "key_press"),
            ],
        )
        self.assertEqual(pending["auto-first"]["status"], "executed")
        self.assertEqual(pending["auto-second"]["status"], "executed")
        self.assertEqual(pending["auto-second"]["authorization_mode"], "full")
        self.assertEqual(pending["auto-second"]["react_budget_remaining"], 7)
        self.assertEqual(pending["auto-first"]["action_budget_remaining"], 23)
        self.assertEqual(pending["auto-second"]["action_budget_remaining"], 22)
        self.assertEqual(pending["auto-second"]["actions_executed"], 2)
        self.assertNotIn("approval_required", [name for name, _payload in events])
        self.assertTrue(events[-1][1]["auto_authorized"])
        self.assertEqual(events[-1][1]["authorization_mode"], "full")

    async def test_full_authorization_hard_limits_unbounded_action_chain(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="launch_app",
            summary="继续打开应用",
            payload={
                "app": "Finder",
                "continue_after_approval": True,
            },
        )
        pending = {
            "loop-0": {
                "proposal": proposal,
                "session_id": "loop-session",
                "user_text": "持续执行",
                "task_id": "loop-task",
                "status": "pending",
                "authorization_mode": "full",
                "action_budget_remaining": 3,
            }
        }
        calls: list[str] = []
        next_index = 0

        async def notify(
            _proposal: ReviewableProposal,
            *,
            task_id: str,
        ) -> dict[str, Any]:
            calls.append(f"notify:{task_id}")
            return {"notified": True}

        async def perform_action(
            _proposal: ReviewableProposal,
        ) -> dict[str, Any]:
            calls.append("action")
            return {
                "launched": True,
                "focused": True,
                "frontmost_app": "Finder",
            }

        async def run_brain_turn(_config: dict[str, Any], **_kwargs: Any):
            return SimpleNamespace(
                text="继续",
                decision=BrainDecision.propose_act(
                    "launch_app",
                    {
                        "app": "Finder",
                        "continue_after_approval": True,
                    },
                ),
            )

        def create_next(
            _decision: BrainDecision,
            *,
            session_id: str,
            user_text: str,
        ):
            nonlocal next_index
            next_index += 1
            proposal_id = f"loop-{next_index}"
            pending[proposal_id] = {
                "proposal": proposal,
                "session_id": session_id,
                "user_text": user_text,
                "status": "pending",
            }
            return proposal_id, proposal

        deps = self._dependencies(
            pending,
            notify_human_ops_action=notify,
            perform_human_ops_action=perform_action,
            proposal_continue_after_approval=lambda _proposal: True,
            normalize_private_config=lambda: {
                "brain": {},
                "human_ops": {
                    "authorization_mode": "full",
                    "require_act_review": False,
                },
            },
            run_brain_turn=run_brain_turn,
            create_human_ops_act_proposal=create_next,
        )

        events = await _collect_events(
            stream_human_ops_proposal_decision(
                "loop-0",
                {"approved": True},
                deps,
            )
        )

        self.assertEqual(calls.count("action"), 3)
        self.assertEqual(
            sum(call.startswith("notify:") for call in calls),
            3,
        )
        self.assertEqual(pending["loop-3"]["status"], "action_limit_reached")
        self.assertEqual(
            events[-1][1]["execution"]["stage"],
            "action_limit",
        )
        self.assertNotIn("recursion", str(events).lower())

    async def test_full_authorization_fails_closed_when_action_notice_fails(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="click",
            summary="Ipet 想点击：确认",
            payload={"target_app": "Finder", "x": 10, "y": 20, "label": "确认"},
        )
        pending = {
            "auto-notice-failure": {
                "proposal": proposal,
                "session_id": "auto-session",
                "user_text": "点击确认",
                "task_id": "auto-notice-task",
                "status": "pending",
                "authorization_mode": "full",
            }
        }
        action_calls = 0

        async def notify(_proposal: ReviewableProposal, *, task_id: str) -> dict[str, Any]:
            raise RuntimeError(f"notice unavailable:{task_id}")

        async def perform_action(_proposal: ReviewableProposal) -> dict[str, Any]:
            nonlocal action_calls
            action_calls += 1
            return {"clicked": True}

        deps = self._dependencies(
            pending,
            notify_human_ops_action=notify,
            perform_human_ops_action=perform_action,
        )

        events = await _collect_events(
            stream_human_ops_proposal_decision(
                "auto-notice-failure",
                {"approved": True},
                deps,
            )
        )

        self.assertEqual(action_calls, 0)
        self.assertEqual(pending["auto-notice-failure"]["status"], "notification_failed")
        self.assertEqual(events[-1][1]["execution"]["stage"], "action_notification")
        self.assertIn("通知失败", events[-1][1]["text"])

    async def test_full_authorization_stop_after_notice_prevents_action_dispatch(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="click",
            summary="Ipet 想点击：确认",
            payload={"target_app": "Finder", "x": 10, "y": 20, "label": "确认"},
        )
        task_id = "auto-stop-after-notice"
        pending = {
            "auto-stop": {
                "proposal": proposal,
                "session_id": "auto-session",
                "user_text": "点击确认",
                "task_id": task_id,
                "status": "pending",
                "authorization_mode": "full",
            }
        }
        TASK_CONTROL.start(task_id, "auto-session")
        action_calls = 0

        async def notify(_proposal: ReviewableProposal, *, task_id: str) -> dict[str, Any]:
            task = TASK_CONTROL.get(task_id)
            self.assertIsNotNone(task)
            task.state = "stopped"
            return {"notified": True, "method": "test"}

        async def perform_action(_proposal: ReviewableProposal) -> dict[str, Any]:
            nonlocal action_calls
            action_calls += 1
            return {"clicked": True}

        deps = self._dependencies(
            pending,
            notify_human_ops_action=notify,
            perform_human_ops_action=perform_action,
        )

        with self.assertRaises(TaskStopped):
            await _collect_events(
                stream_human_ops_proposal_decision("auto-stop", {"approved": True}, deps)
            )

        self.assertEqual(action_calls, 0)
        self.assertEqual(pending["auto-stop"]["status"], "invalidated_by_stop")

    async def test_post_approval_capture_is_attached_to_brain_when_observe_model_is_disabled(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="click",
            summary="Ipet 想点击：设置",
            payload={
                "target_app": "System Settings",
                "x": 10,
                "y": 20,
                "label": "设置",
                "continue_after_approval": True,
            },
        )
        pending = {
            "proposal-image": {
                "proposal": proposal,
                "session_id": "neo-session",
                "user_text": "打开设置项",
                "status": "pending",
            }
        }
        brain_calls: list[dict[str, Any]] = []

        async def perform_observe(_decision: BrainDecision, _config: dict[str, Any]) -> dict[str, Any]:
            return {
                "text": "截图将由 Brain 直接观察。",
                "analysis_route": "brain",
                "frame": {"data_url": "data:image/png;base64,AA=="},
                "coordinate_context": "screen coordinates",
                "observations": [],
                "unknowns": [],
            }

        async def run_brain_turn(_config: dict[str, Any], **kwargs: Any):
            brain_calls.append(kwargs)
            decision = BrainDecision.say("设置项已打开。", goal={"status": "done"})
            return SimpleNamespace(text=decision.summary, decision=decision)

        deps = self._dependencies(
            pending,
            proposal_continue_after_approval=lambda _proposal: True,
            perform_human_ops_observe=perform_observe,
            run_brain_turn=run_brain_turn,
        )

        events = await _collect_events(
            stream_human_ops_proposal_decision("proposal-image", {"approved": True}, deps)
        )

        self.assertEqual(brain_calls[0]["image_data_url"], "data:image/png;base64,AA==")
        self.assertIn("Brain 直接观察图片", brain_calls[0]["user_text"])
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["text"], "设置项已打开。")


if __name__ == "__main__":
    unittest.main()
