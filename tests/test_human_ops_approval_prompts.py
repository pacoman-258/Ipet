import unittest
from pathlib import Path

from human_ops.approvals import ReviewableProposal
from human_ops.approval_prompts import (
    build_human_ops_continuation_prompt,
    build_post_approval_observe_prompt,
)


ROOT_DIR = Path(__file__).resolve().parents[1]


class HumanOpsApprovalPromptTests(unittest.TestCase):
    def test_type_text_observe_prompt_checks_draft_in_chat_input(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="type_text",
            summary="Ipet 想输入到聊天输入框：收到，马上处理",
            payload={"text": "收到，马上处理", "label": "聊天输入框"},
        )

        prompt = build_post_approval_observe_prompt(
            "根据张三的聊天信息回复张三",
            proposal,
            looks_like_chat_reply_request=lambda text: "回复" in text,
        )

        self.assertIn("刚才输入的草稿是“收到，马上处理”", prompt)
        self.assertIn("聊天输入框中是否已经出现这段草稿", prompt)
        self.assertIn("草稿文字是否完整", prompt)
        self.assertIn("发送入口或其他相关 affordance", prompt)
        self.assertIn("不替 Brain 决定下一步动作", prompt)

    def test_enter_observe_prompt_checks_expected_text_send_result(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="key_press",
            summary="Ipet 想按下回车：发送",
            payload={"key": "enter", "label": "发送", "expected_text": "收到，马上处理"},
        )

        prompt = build_post_approval_observe_prompt(
            "根据张三的聊天信息回复张三",
            proposal,
            looks_like_chat_reply_request=lambda text: "回复" in text,
        )

        self.assertIn("预期刚发送的回复是“收到，马上处理”", prompt)
        self.assertIn("聊天记录底部是否已经发送并出现这条回复", prompt)
        self.assertIn("输入框是否已清空或回到可继续输入状态", prompt)
        self.assertIn("重点判断这次回复是否已经发送成功", prompt)

    def test_continuation_prompt_includes_task_proposal_execution_observation_and_structured_context(self) -> None:
        proposal = ReviewableProposal.act(
            action_type="click",
            summary="Ipet 想点击：张三聊天条目",
            payload={"x": 240, "y": 310, "label": "张三聊天条目"},
        )

        prompt = build_human_ops_continuation_prompt(
            user_text="打开微信并根据张三聊天信息回复张三",
            proposal=proposal,
            execution={"ok": True, "clicked": True},
            observation={"text": "已经进入张三会话。"},
            computer_use_context_text="Structured computer-use context\n{\"surface\":\"wechat_gui\"}",
        )

        self.assertIn("用户原始复杂任务：打开微信并根据张三聊天信息回复张三", prompt)
        self.assertIn("上一项已批准并执行", prompt)
        self.assertIn("张三聊天条目", prompt)
        self.assertIn('"clicked": true', prompt)
        self.assertIn("执行后验证：", prompt)
        self.assertIn("已经进入张三会话。", prompt)
        self.assertIn("Structured computer-use context", prompt)
        self.assertIn("请独立判断原始目标是否已经完成", prompt)
        self.assertIn("不要重复获取已经足够且一致的证据", prompt)
        self.assertIn("不要把任何动作类型套进预设顺序", prompt)

    def test_long_prompt_body_lives_in_human_ops_module_not_backend_app(self) -> None:
        backend_source = (ROOT_DIR / "backend" / "app.py").read_text(encoding="utf-8")
        prompt_source = (ROOT_DIR / "human_ops" / "approval_prompts.py").read_text(encoding="utf-8")

        self.assertNotIn("请独立判断原始目标是否已经完成", backend_source)
        self.assertNotIn("聊天输入框中是否已经出现这段草稿", backend_source)
        self.assertIn("请独立判断原始目标是否已经完成", prompt_source)
        self.assertIn("聊天输入框中是否已经出现这段草稿", prompt_source)


if __name__ == "__main__":
    unittest.main()
