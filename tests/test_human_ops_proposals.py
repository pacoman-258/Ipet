import unittest

from brain.decisions import BrainDecision
from human_ops.approvals import ReviewableProposal
from human_ops.proposals import (
    build_human_ops_act_proposal,
    proposal_arguments,
    proposal_continue_after_approval,
    proposal_event_payload,
    with_inherited_enter_expected_text,
)


class HumanOpsProposalTests(unittest.TestCase):
    def test_build_act_proposal_defaults_continuation_and_formats_event_payload(self) -> None:
        goal = {
            "objective": "打开微信并回复消息",
            "status": "handoff_review",
            "stage": "launch_app",
            "next": "observe",
        }
        decision = BrainDecision.propose_act(
            "click",
            {"x": "12.4", "y": "30.6", "label": "Dock 微信图标"},
            goal=goal,
        )

        proposal = build_human_ops_act_proposal(
            decision,
            user_text="帮我打开微信并回复消息",
            looks_like_desktop_action_request=lambda text: "微信" in text,
            looks_like_chat_reply_request=lambda text: "回复" in text,
        )

        args = proposal_arguments(proposal)
        self.assertTrue(args["continue_after_approval"])
        self.assertEqual(args["goal"], goal)
        self.assertTrue(proposal_continue_after_approval(proposal))

        payload = proposal_event_payload("proposal-1", proposal)
        self.assertEqual(payload["proposal_id"], "proposal-1")
        self.assertEqual(payload["action_type"], "click")
        self.assertEqual(payload["tools"][0]["summary"], "点击 Dock 微信图标 (12, 31)")
        self.assertEqual(payload["preview"]["x"], 12)
        self.assertEqual(payload["preview"]["y"], 31)

    def test_enter_expected_text_inherits_previous_type_text(self) -> None:
        previous = ReviewableProposal.act(
            action_type="type_text",
            summary="Ipet 想输入到聊天输入框：收到，马上处理",
            payload={"text": "收到，马上处理", "label": "聊天输入框"},
        )
        decision = BrainDecision.propose_act("key_press", {"key": "enter", "label": "发送消息"})

        inherited = with_inherited_enter_expected_text(
            decision,
            previous_proposal=previous,
            execution={},
        )

        self.assertEqual(inherited.payload["arguments"]["expected_text"], "收到，马上处理")


if __name__ == "__main__":
    unittest.main()
