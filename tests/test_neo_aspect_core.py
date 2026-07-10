from __future__ import annotations

import unittest

from app.settings_schema import neo_aspect_settings_sections
from brain.decisions import BrainDecision, DecisionKind
from human_ops.approvals import ApprovalRequirement, ReviewableProposal
from human_ops.previews import ClickPreview, PreviewKind, red_dot_click_preview
from memory.review import MemoryProposal
from skills.recipes import SkillRecipeProposal


class NeoAspectDecisionTests(unittest.TestCase):
    def test_say_and_observe_do_not_require_review(self) -> None:
        self.assertFalse(BrainDecision.say("我先看一下。").requires_review)
        self.assertFalse(BrainDecision.think("需要先观察屏幕。").requires_review)
        self.assertFalse(BrainDecision.observe("screen").requires_review)

    def test_state_changing_decisions_require_review(self) -> None:
        self.assertTrue(BrainDecision.propose_act("click", {"x": 10, "y": 20}).requires_review)
        self.assertTrue(BrainDecision.propose_act("type_text", {"text": "你好"}).requires_review)
        self.assertTrue(BrainDecision.propose_act("key_press", {"key": "enter"}).requires_review)
        self.assertTrue(BrainDecision.propose_remember("preference", "用户喜欢简洁回答").requires_review)
        self.assertTrue(BrainDecision.propose_learn_skill("Use Codex", ["打开 Codex"]).requires_review)

    def test_decision_rejects_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            BrainDecision(kind="plan_many_steps", summary="bad")

    def test_decision_kind_values_are_stable(self) -> None:
        self.assertEqual(DecisionKind.SAY.value, "say")
        self.assertEqual(DecisionKind.THINK.value, "think")
        self.assertEqual(DecisionKind.OBSERVE.value, "observe")
        self.assertEqual(DecisionKind.PROPOSE_ACT.value, "propose_act")
        self.assertEqual(DecisionKind.PROPOSE_REMEMBER.value, "propose_remember")
        self.assertEqual(DecisionKind.PROPOSE_LEARN_SKILL.value, "propose_learn_skill")
        self.assertEqual(DecisionKind.STOP.value, "stop")


class NeoAspectHumanOpsTests(unittest.TestCase):
    def test_click_preview_uses_red_dot(self) -> None:
        preview = red_dot_click_preview(x=120, y=240, label="发送按钮")

        self.assertEqual(preview.kind, PreviewKind.RED_DOT)
        self.assertEqual(preview.x, 120)
        self.assertEqual(preview.y, 240)
        self.assertEqual(preview.label, "发送按钮")
        self.assertEqual(preview.to_dict()["marker"], "red_dot")
        self.assertGreaterEqual(preview.to_dict()["size"], 24)

    def test_act_proposal_requires_approval(self) -> None:
        preview = ClickPreview(x=10, y=20, label="Codex 图标")
        proposal = ReviewableProposal.act(
            action_type="click",
            summary="点击 Codex 图标",
            payload={"x": 10, "y": 20},
            preview=preview,
        )

        self.assertEqual(proposal.requirement, ApprovalRequirement.REQUIRED)
        self.assertFalse(proposal.approved)
        self.assertEqual(proposal.preview.to_dict()["label"], "Codex 图标")

    def test_observe_proposal_is_not_reviewable_action(self) -> None:
        with self.assertRaises(ValueError):
            ReviewableProposal.act(action_type="observe", summary="观察屏幕", payload={})

    def test_text_and_enter_actions_are_reviewable(self) -> None:
        text_proposal = ReviewableProposal.act(
            action_type="type_text",
            summary="输入回复",
            payload={"text": "收到，我马上处理。"},
        )
        enter_proposal = ReviewableProposal.act(
            action_type="key_press",
            summary="按下回车",
            payload={"key": "enter"},
        )

        self.assertTrue(text_proposal.requires_review)
        self.assertEqual(text_proposal.payload["action_type"], "type_text")
        self.assertEqual(text_proposal.payload["arguments"]["text"], "收到，我马上处理。")
        self.assertTrue(enter_proposal.requires_review)
        self.assertEqual(enter_proposal.payload["arguments"]["key"], "enter")


class NeoAspectMemorySkillTests(unittest.TestCase):
    def test_memory_proposal_requires_review_before_save(self) -> None:
        proposal = MemoryProposal(category="preference", text="用户喜欢直接的技术结论")

        self.assertFalse(proposal.approved)
        self.assertTrue(proposal.requires_review)
        self.assertEqual(proposal.to_dict()["category"], "preference")
        self.assertTrue(proposal.approve().approved)

    def test_skill_recipe_contains_reviewable_operation_recipe(self) -> None:
        proposal = SkillRecipeProposal(
            name="Use Codex for project edits",
            triggers=["用户要求修改代码", "用户要求修复 bug"],
            steps=["观察 Codex 窗口", "输入任务", "等待结果", "读取 diff"],
            cautions=["不要自动提交", "删除文件前询问用户"],
            verification="确认 Codex 输出和测试结果",
        )

        self.assertFalse(proposal.approved)
        self.assertEqual(proposal.to_dict()["name"], "Use Codex for project edits")
        self.assertIn("读取 diff", proposal.steps)
        self.assertTrue(proposal.approve().approved)


class NeoAspectSettingsTests(unittest.TestCase):
    def test_settings_sections_match_new_architecture(self) -> None:
        sections = neo_aspect_settings_sections()
        section_ids = [item["id"] for item in sections]

        self.assertEqual(section_ids, ["body", "brain", "human_ops", "memory", "skills", "diagnostics"])

    def test_settings_schema_removes_runtime_management_content(self) -> None:
        text = repr(neo_aspect_settings_sections()).lower()

        self.assertNotIn("astrbot", text)
        self.assertNotIn("hermes", text)
        self.assertNotIn("mcp", text)
        self.assertNotIn("runtime sidecar", text)


if __name__ == "__main__":
    unittest.main()
