import unittest

from brain.decisions import BrainDecision
from human_ops.approvals import ReviewableProposal
from human_ops.proposals import (
    build_human_ops_act_proposal,
    proposal_arguments,
    proposal_continue_after_approval,
    proposal_event_payload,
    should_default_continue_after_approval,
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

        proposal = build_human_ops_act_proposal(decision)

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

    def test_structured_goal_controls_continuation_without_keyword_routing(self) -> None:
        self.assertTrue(
            should_default_continue_after_approval(
                {"status": "handoff_review", "next": "inspect_result"}
            )
        )
        self.assertFalse(
            should_default_continue_after_approval(
                {"objective": "回复并发送消息", "status": "in_progress", "next": "done"}
            )
        )

    def test_explicit_continuation_false_overrides_nonterminal_next_step(self) -> None:
        proposal = build_human_ops_act_proposal(
            BrainDecision.propose_act(
                "launch_app",
                {"app": "WeChat", "continue_after_approval": False},
                goal={"status": "handoff_review", "next": "observe"},
            )
        )

        self.assertFalse(proposal_continue_after_approval(proposal))
        self.assertIs(proposal.payload["arguments"]["continue_after_approval"], False)

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

    def test_chat_enter_inherits_verified_recipient_and_input_reference(self) -> None:
        input_ref = {
            "app_id": "wechat",
            "role": "AXTextArea",
            "path": [0, 2],
            "fingerprint": "copied",
        }
        previous = ReviewableProposal.act(
            action_type="type_text",
            summary="输入回复",
            payload={
                "target_app": "WeChat",
                "ax_ref": input_ref,
                "text": "收到",
            },
        )
        decision = BrainDecision.propose_act(
            "key_press",
            {"key": "enter", "target_app": "WeChat"},
        )

        inherited = with_inherited_enter_expected_text(
            decision,
            previous_proposal=previous,
            execution={},
            observation={"chat_context": {"contact": "目标会话"}},
        )

        arguments = inherited.payload["arguments"]
        self.assertEqual(arguments["intended_chat"], "目标会话")
        self.assertEqual(arguments["input_ax_ref"], input_ref)
        self.assertEqual(arguments["expected_text"], "收到")

    def test_chat_enter_cannot_override_previous_type_transaction(self) -> None:
        previous_ref = {
            "app_id": "qq",
            "role": "AXTextArea",
            "path": [0, 2],
            "fingerprint": "alice",
        }
        next_ref = {
            "app_id": "qq",
            "role": "AXTextArea",
            "path": [0, 3],
            "fingerprint": "bob",
        }
        previous = ReviewableProposal.act(
            action_type="type_text",
            summary="输入 Alice 的回复",
            payload={
                "target_app": "QQ",
                "intended_chat": "Alice",
                "ax_ref": previous_ref,
                "text": "只发给 Alice",
            },
        )
        decision = BrainDecision.propose_act(
            "key_press",
            {
                "key": "enter",
                "target_app": "WeChat",
                "intended_chat": "Bob",
                "input_ax_ref": next_ref,
                "expected_text": "另一段文字",
            },
        )

        inherited = with_inherited_enter_expected_text(
            decision,
            previous_proposal=previous,
            execution={},
        )

        arguments = inherited.payload["arguments"]
        self.assertEqual(arguments["target_app"], "QQ")
        self.assertEqual(arguments["intended_chat"], "Alice")
        self.assertEqual(arguments["input_ax_ref"], previous_ref)
        self.assertEqual(arguments["expected_text"], "只发给 Alice")

    def test_chat_enter_uses_persisted_transaction_after_intermediate_action(self) -> None:
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
            payload={"target_app": "QQ", "label": "表情"},
        )
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

        inherited = with_inherited_enter_expected_text(
            decision,
            previous_proposal=intermediate,
            execution={"clicked": True, "target_app": "QQ"},
            chat_send_transaction={
                "target_app": "QQ",
                "intended_chat": "Alice",
                "input_ax_ref": alice_ref,
                "expected_text": "只发给 Alice",
            },
        )

        arguments = inherited.payload["arguments"]
        self.assertEqual(arguments["target_app"], "QQ")
        self.assertEqual(arguments["intended_chat"], "Alice")
        self.assertEqual(arguments["input_ax_ref"], alice_ref)
        self.assertEqual(arguments["expected_text"], "只发给 Alice")

    def test_launch_app_proposal_is_reviewable_without_click_preview(self) -> None:
        proposal = build_human_ops_act_proposal(
            BrainDecision.propose_act("launch_app", {"app": "WeChat", "label": "WeChat"}),
        )

        payload = proposal_event_payload("launch-1", proposal)
        self.assertTrue(proposal.requires_review)
        self.assertEqual(payload["action_type"], "launch_app")
        self.assertEqual(payload["tools"][0]["summary"], "打开应用 WeChat")
        self.assertIsNone(payload["preview"])

    def test_semantic_click_names_ax_target_without_coordinate_preview(self) -> None:
        ax_ref = {"app_id": "music", "role": "AXButton", "path": [3], "fingerprint": "copied"}
        proposal = build_human_ops_act_proposal(
            BrainDecision.propose_act(
                "click",
                {"target_app": "Music", "ax_ref": ax_ref, "label": "播放"},
            ),
        )

        payload = proposal_event_payload("ax-click-1", proposal)

        self.assertIsNone(payload["preview"])
        self.assertEqual(payload["tools"][0]["summary"], "点击 播放（AXButton 语义目标） · Music")
        self.assertIn("macOS Accessibility", proposal.summary)

    def test_playwright_proposal_names_operation_and_target(self) -> None:
        proposal = build_human_ops_act_proposal(
            BrainDecision.propose_act(
                "playwright",
                {"profile": "个人", "operation": "open", "url": "https://www.youtube.com", "label": "YouTube"},
            ),
        )

        payload = proposal_event_payload("pw-1", proposal)
        self.assertTrue(proposal.requires_review)
        self.assertEqual(payload["action_type"], "playwright")
        self.assertEqual(payload["tools"][0]["summary"], "Playwright open：YouTube · 个人资料：个人")
        self.assertIsNone(payload["preview"])


if __name__ == "__main__":
    unittest.main()
