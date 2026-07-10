from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
import unittest
from pathlib import Path
from typing import Any

import backend.app as backend_app
from brain.decisions import BrainDecision
from human_ops.approvals import ReviewableProposal
from human_ops.proposals import should_default_continue_after_approval as pure_default_continue


ROOT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT_DIR / "backend" / "human_ops_proposal_flow.py"


def _deps(module: Any, **overrides: Any) -> Any:
    defaults = {
        "pending_proposals": {},
        "uuid_factory": lambda: "proposal-fixed",
        "time_func": lambda: 123.5,
        "looks_like_desktop_action_request": lambda text: "微信" in text or "打开" in text,
        "looks_like_chat_reply_request": lambda text: "回复" in text or "聊天" in text,
        "goal_is_terminal": lambda status: status in {"done", "blocked", "need_user"},
        "computer_use_context_text": lambda observation: f"Structured context: {observation.get('surface', 'unknown')}",
    }
    defaults.update(overrides)
    return module.HumanOpsProposalFlowDependencies(**defaults)


class BackendHumanOpsProposalFlowTests(unittest.TestCase):
    def _flow(self) -> Any:
        self.assertTrue(MODULE_PATH.exists(), "backend/human_ops_proposal_flow.py should hold backend proposal glue")
        return importlib.import_module("backend.human_ops_proposal_flow")

    def test_module_does_not_import_backend_app(self) -> None:
        self.assertTrue(MODULE_PATH.exists(), "backend/human_ops_proposal_flow.py should exist")
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name for alias in node.names}
                self.assertNotIn("backend.app", imported)
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "backend.app")
                self.assertFalse(
                    node.level == 1 and node.module is None and any(alias.name == "app" for alias in node.names),
                    "backend.human_ops_proposal_flow must not import sibling app module",
                )

    def test_app_wrappers_stay_thin(self) -> None:
        self._flow()
        wrapper_names = {
            "_coerce_int": "coerce_int",
            "_proposal_tool_label": "proposal_tool_label",
            "_proposal_event_payload": "proposal_event_payload",
            "_should_default_continue_after_approval": "should_default_continue_after_approval",
            "_create_human_ops_act_proposal": "create_human_ops_act_proposal",
            "_proposal_arguments": "proposal_arguments",
            "_proposal_continue_after_approval": "proposal_continue_after_approval",
            "_with_inherited_enter_expected_text": "with_inherited_enter_expected_text",
            "_human_ops_continuation_prompt": "human_ops_continuation_prompt",
            "_post_approval_observe_prompt": "post_approval_observe_prompt",
        }

        for wrapper_name, helper_name in wrapper_names.items():
            with self.subTest(name=wrapper_name):
                source = textwrap.dedent(inspect.getsource(getattr(backend_app, wrapper_name)))
                tree = ast.parse(source)
                function = tree.body[0]
                self.assertIsInstance(function, ast.FunctionDef)
                self.assertEqual(len(function.body), 1, source)
                self.assertIsInstance(function.body[0], ast.Return, source)
                call = function.body[0].value
                self.assertIsInstance(call, ast.Call, source)
                self.assertIsInstance(call.func, ast.Attribute, source)
                self.assertEqual(call.func.attr, helper_name, source)
                self.assertIsInstance(call.func.value, ast.Name, source)
                self.assertEqual(call.func.value.id, "_human_ops_proposal_flow_helpers", source)

    def test_create_proposal_writes_pending_store_with_injected_id_time_and_shape(self) -> None:
        flow = self._flow()
        pending: dict[str, dict[str, Any]] = {}
        deps = _deps(
            flow,
            pending_proposals=pending,
            uuid_factory=lambda: "proposal-123",
            time_func=lambda: 456.75,
        )
        decision = BrainDecision.propose_act(
            "click",
            {"x": "12.4", "y": "30.6", "label": "Dock 微信图标"},
            goal={
                "objective": "打开微信并回复消息",
                "status": "handoff_review",
                "stage": "launch_app",
                "next": "observe",
            },
        )

        proposal_id, proposal = flow.create_human_ops_act_proposal(
            decision,
            session_id="session-1",
            user_text="帮我打开微信并回复消息",
            deps=deps,
        )

        self.assertEqual(proposal_id, "proposal-123")
        self.assertEqual(set(pending), {"proposal-123"})
        self.assertIs(pending["proposal-123"]["proposal"], proposal)
        self.assertEqual(pending["proposal-123"]["session_id"], "session-1")
        self.assertEqual(pending["proposal-123"]["user_text"], "帮我打开微信并回复消息")
        self.assertEqual(pending["proposal-123"]["created_at"], 456.75)
        self.assertEqual(pending["proposal-123"]["status"], "pending")
        self.assertTrue(proposal.payload["arguments"]["continue_after_approval"])

    def test_default_continue_matches_human_ops_semantics_with_injected_predicates(self) -> None:
        flow = self._flow()
        deps = _deps(flow)
        goal = {"objective": "回复张三", "status": "in_progress", "stage": "verify_result", "next": "done"}
        arguments = {"key": "enter", "label": "发送消息", "expected_text": "收到"}

        expected = pure_default_continue(
            "帮我回复张三",
            goal,
            action_type="key_press",
            arguments=arguments,
            looks_like_desktop_action_request=deps.looks_like_desktop_action_request,
            looks_like_chat_reply_request=deps.looks_like_chat_reply_request,
            goal_is_terminal=deps.goal_is_terminal,
        )
        actual = flow.should_default_continue_after_approval(
            "帮我回复张三",
            goal,
            action_type="key_press",
            arguments=arguments,
            deps=deps,
        )

        self.assertTrue(actual)
        self.assertEqual(actual, expected)
        self.assertFalse(
            flow.should_default_continue_after_approval(
                "帮我回复张三",
                {"objective": "回复张三", "status": "done", "stage": "verify_result"},
                action_type="key_press",
                arguments=arguments,
                deps=deps,
            )
        )

    def test_continuation_prompt_injects_computer_use_context(self) -> None:
        flow = self._flow()
        deps = _deps(flow, computer_use_context_text=lambda observation: f"context for {observation['surface']}")
        proposal = ReviewableProposal.act(
            action_type="type_text",
            summary="Ipet 想输入到聊天框：收到",
            payload={"text": "收到", "label": "聊天框"},
        )

        prompt = flow.human_ops_continuation_prompt(
            user_text="帮我回复张三",
            proposal=proposal,
            execution={"typed": True, "text": "收到"},
            observation={"text": "草稿已经在输入框里", "surface": "wechat"},
            deps=deps,
        )

        self.assertIn("用户原始复杂任务：帮我回复张三", prompt)
        self.assertIn("草稿已经在输入框里", prompt)
        self.assertIn("context for wechat", prompt)
        self.assertIn("请像会用电脑的人类一样继续当前任务阶段", prompt)

    def test_post_approval_observe_prompt_uses_injected_chat_intent(self) -> None:
        flow = self._flow()
        deps = _deps(flow, looks_like_chat_reply_request=lambda text: "张三" in text)
        proposal = ReviewableProposal.act(
            action_type="type_text",
            summary="Ipet 想输入到聊天框：收到",
            payload={"text": "收到，马上处理", "label": "聊天框"},
        )

        prompt = flow.post_approval_observe_prompt("帮我回复张三", proposal, deps=deps)

        self.assertIn("刚才输入的草稿是“收到，马上处理”", prompt)
        self.assertIn("聊天输入框中是否已经出现这段草稿", prompt)
        self.assertIn("回车发送", prompt)


if __name__ == "__main__":
    unittest.main()
