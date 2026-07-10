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


ROOT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT_DIR / "backend" / "app_proposal_adapters.py"


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
    return module.proposal_flow_dependencies(**defaults)


class BackendAppProposalAdaptersTests(unittest.TestCase):
    def _module(self) -> Any:
        self.assertTrue(MODULE_PATH.exists(), "backend/app_proposal_adapters.py should exist")
        return importlib.import_module("backend.app_proposal_adapters")

    def test_module_does_not_import_backend_app(self) -> None:
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
                    "backend.app_proposal_adapters must not import sibling app module",
                )

    def test_backend_app_wrappers_remain_thin_through_the_adapter_module(self) -> None:
        self._module()
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

    def test_create_proposal_uses_injected_dependency_source(self) -> None:
        module = self._module()
        pending: dict[str, dict[str, Any]] = {}
        deps = _deps(
            module,
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

        proposal_id, proposal = module.create_human_ops_act_proposal(
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

    def test_representative_proposal_event_and_continuation_prompt(self) -> None:
        module = self._module()
        deps = _deps(module, computer_use_context_text=lambda observation: f"context for {observation['surface']}")
        proposal = ReviewableProposal.act(
            action_type="type_text",
            summary="Ipet 想输入到聊天框：收到",
            payload={"text": "收到", "label": "聊天框"},
        )

        event_payload = module.proposal_event_payload("proposal-1", proposal)
        prompt = module.human_ops_continuation_prompt(
            user_text="帮我回复张三",
            proposal=proposal,
            execution={"typed": True, "text": "收到"},
            observation={"text": "草稿已经在输入框里", "surface": "wechat"},
            deps=deps,
        )

        self.assertEqual(event_payload["proposal_id"], "proposal-1")
        self.assertEqual(event_payload["turn_id"], "proposal-1")
        self.assertEqual(event_payload["action_type"], "type_text")
        self.assertEqual(event_payload["tools"][0]["summary"], "输入到 聊天框: 收到")
        self.assertIsNone(event_payload["preview"])
        self.assertIn("用户原始复杂任务：帮我回复张三", prompt)
        self.assertIn("草稿已经在输入框里", prompt)
        self.assertIn("context for wechat", prompt)
        self.assertIn("请像会用电脑的人类一样继续当前任务阶段", prompt)


if __name__ == "__main__":
    unittest.main()
