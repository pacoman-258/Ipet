from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any

from brain.decisions import BrainDecision
from human_ops.approvals import ReviewableProposal
from human_ops.approval_flow import (
    HumanOpsApprovalFlowDependencies,
    execution_verification,
    stream_human_ops_proposal_decision,
)
from human_ops.proposals import proposal_event_payload


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
        playwright = ReviewableProposal.act(
            action_type="playwright",
            summary="打开网页",
            payload={"profile": "工作", "operation": "open", "url": "https://example.com"},
        )
        playwright_result = execution_verification(
            playwright,
            {"playwright_done": True, "output": "page snapshot"},
        )

        self.assertEqual(click_result["status"], "verified")
        self.assertFalse(click_result["requires_visual"])
        self.assertEqual(send_result["status"], "insufficient")
        self.assertTrue(send_result["requires_visual"])
        self.assertIn("expected message", send_result["reason"])
        self.assertEqual(playwright_result["status"], "verified")
        self.assertFalse(playwright_result["requires_visual"])

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
        deps = self._dependencies(pending)

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
            raise AssertionError(f"structured verification should avoid visual observe: {decision}")

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
        self.assertEqual(calls["observe"], 0)
        self.assertIn("平台原生状态验证通过", calls["brain_prompt"])
        self.assertIn("本阶段未截图", calls["brain_prompt"])
        created_decision, session_id, user_text = calls["create"]
        self.assertEqual(created_decision.payload["action_type"], "key_press")
        self.assertEqual(session_id, "neo-session")
        self.assertEqual(user_text, "回复消息")
        self.assertEqual(pending["proposal-1"]["status"], "executed")
        event_names = [name for name, _data in events]
        self.assertEqual(event_names[:4], ["meta", "phase", "display_segment", "phase"])
        self.assertEqual(event_names[-2:], ["phase", "approval_required"])
        phase_payloads = [data for name, data in events if name == "phase"]
        self.assertEqual([data["category"] for data in phase_payloads], ["acting", "verifying", "waiting_approval"])
        self.assertEqual(phase_payloads[1]["name"], "human_ops_structured_verify")
        self.assertEqual(phase_payloads[1]["task"], "执行后观察 回复消息")
        self.assertEqual(phase_payloads[-1]["status_id"], "approval:next-proposal")
        self.assertEqual(events[-1][1]["proposal_id"], "next-proposal")
        self.assertEqual(events[-1][1]["action_type"], "key_press")
        self.assertNotIn("done", event_names)

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
