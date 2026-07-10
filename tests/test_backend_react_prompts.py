from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
import unittest
from pathlib import Path
from typing import Any

import backend.app as backend_app
from brain.decisions import BrainDecision, DecisionKind


ROOT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT_DIR / "backend" / "react_prompts.py"


class BackendReactPromptsTests(unittest.TestCase):
    def _react_prompts(self) -> Any:
        self.assertTrue(MODULE_PATH.exists(), "backend/react_prompts.py should hold ReAct prompt helpers")
        return importlib.import_module("backend.react_prompts")

    def test_module_does_not_import_backend_app(self) -> None:
        self.assertTrue(MODULE_PATH.exists(), "backend/react_prompts.py should exist")
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
                    "backend.react_prompts must not import sibling app module",
                )

    def test_app_wrappers_stay_thin(self) -> None:
        self._react_prompts()
        wrapper_names = [
            "_click_coordinate_clarification_text",
            "_click_coordinate_observe_failure_text",
            "_fallback_after_observe_brain_error",
            "_coerce_decision_for_human_ops",
            "_decision_kind",
            "_decision_goal",
            "_goal_status",
            "_goal_is_terminal",
            "_react_missing_summary",
            "_blocked_react_decision",
            "_react_followup_prompt",
            "_simple_human_action_support",
            "_has_numeric_action_argument",
            "_unsupported_simple_action_prompt",
        ]

        for name in wrapper_names:
            with self.subTest(name=name):
                source = textwrap.dedent(inspect.getsource(getattr(backend_app, name)))
                tree = ast.parse(source)
                function = tree.body[0]
                self.assertIsInstance(function, ast.FunctionDef)
                self.assertEqual(len(function.body), 1, source)
                self.assertIsInstance(function.body[0], ast.Return, source)
                call = function.body[0].value
                self.assertIsInstance(call, ast.Call, source)
                self.assertIsInstance(call.func, ast.Attribute, source)
                self.assertEqual(call.func.attr, name, source)
                self.assertIsInstance(call.func.value, ast.Name, source)
                self.assertEqual(call.func.value.id, "_react_prompt_helpers", source)

    def test_coerce_decision_keeps_non_say_decision(self) -> None:
        react_prompts = self._react_prompts()
        decision = BrainDecision.observe("screen")

        result = react_prompts._coerce_decision_for_human_ops(
            "打开微信",
            decision,
            looks_like_desktop_observe_request=lambda text: True,
            looks_like_desktop_action_request=lambda text: True,
        )

        self.assertIs(result, decision)

    def test_coerce_decision_keeps_say_with_goal(self) -> None:
        react_prompts = self._react_prompts()
        decision = BrainDecision.say(
            "正在处理",
            goal={"objective": "打开微信", "status": "in_progress"},
        )

        result = react_prompts._coerce_decision_for_human_ops(
            "打开微信",
            decision,
            looks_like_desktop_observe_request=lambda text: True,
            looks_like_desktop_action_request=lambda text: True,
        )

        self.assertIs(result, decision)

    def test_coerce_decision_turns_desktop_observe_and_action_say_into_observe(self) -> None:
        react_prompts = self._react_prompts()

        cases = [
            ("看看屏幕", True, False),
            ("打开微信", False, True),
        ]
        for user_text, observe_match, action_match in cases:
            with self.subTest(user_text=user_text):
                result = react_prompts._coerce_decision_for_human_ops(
                    user_text,
                    BrainDecision.say("好的"),
                    looks_like_desktop_observe_request=lambda text, match=observe_match: match,
                    looks_like_desktop_action_request=lambda text, match=action_match: match,
                )

                self.assertEqual(result.kind, DecisionKind.OBSERVE)
                self.assertEqual(result.payload["target"], user_text)

    def test_coerce_decision_keeps_plain_say(self) -> None:
        react_prompts = self._react_prompts()
        decision = BrainDecision.say("只聊天")

        result = react_prompts._coerce_decision_for_human_ops(
            "你好",
            decision,
            looks_like_desktop_observe_request=lambda text: False,
            looks_like_desktop_action_request=lambda text: False,
        )

        self.assertIs(result, decision)

    def test_blocked_decision_preserves_goal_and_marks_status(self) -> None:
        react_prompts = self._react_prompts()
        last_decision = BrainDecision.think(
            "还在看",
            goal={
                "objective": "打开 Chrome",
                "status": "in_progress",
                "missing": ["Dock 里 Chrome 图标的可靠坐标"],
            },
        )

        decision = react_prompts._blocked_react_decision("打开 Chrome", last_decision)

        self.assertEqual(decision.kind, DecisionKind.SAY)
        goal = decision.payload["goal"]
        self.assertEqual(goal["objective"], "打开 Chrome")
        self.assertEqual(goal["status"], "blocked")
        self.assertEqual(goal["next"], "need_more_evidence")
        self.assertEqual(goal["missing"], ["Dock 里 Chrome 图标的可靠坐标"])
        self.assertIn("还不能把", decision.payload["text"])
        self.assertIn("Dock 里 Chrome 图标的可靠坐标", decision.payload["text"])

    def test_react_followup_observation_prompt_keeps_coordinate_and_computer_context(self) -> None:
        react_prompts = self._react_prompts()
        prompt = react_prompts._react_followup_prompt(
            user_text="点一下 Dock 里的 Chrome",
            decision=BrainDecision.observe(
                "screen",
                goal={"objective": "打开 Chrome", "status": "in_progress"},
            ),
            remaining_budget=2,
            observation_text="我看到了 Dock，Chrome 图标大概在底部中间。",
            coordinate_context="Coordinate context: x=512 y=840 are macOS screen coordinates.",
            computer_use_context="Structured computer-use context\n{\"surface\":\"dock\",\"affordance\":\"Chrome\"}",
        )

        self.assertIn("用户原始请求：点一下 Dock 里的 Chrome", prompt)
        self.assertIn("我看到了 Dock", prompt)
        self.assertIn("Coordinate context", prompt)
        self.assertIn("macOS screen coordinates", prompt)
        self.assertIn("Structured computer-use context", prompt)
        self.assertIn("不要因为格式问题要求 observe 输出 JSON", prompt)
        self.assertIn("affordance", prompt)

    def test_unsupported_simple_action_prompt_keeps_contract(self) -> None:
        react_prompts = self._react_prompts()
        prompt = react_prompts._unsupported_simple_action_prompt(
            user_text="打开 Chrome",
            decision=BrainDecision.propose_act(
                "open_app",
                {"name": "Chrome"},
                goal={"objective": "打开 Chrome", "status": "in_progress"},
            ),
            unsupported_action="open_app",
            remaining_budget=1,
        )

        self.assertIn("不可执行或不符合当前简单人类动作范围", prompt)
        self.assertIn("open_app", prompt)
        self.assertIn("click、type_text、key_press enter", prompt)
        self.assertIn("打开 App 请通过 observe 找到 Dock/App 图标或搜索结果", prompt)
        self.assertIn("propose_act click", prompt)

    def test_simple_action_support_click_coordinates_and_enter_key_rules(self) -> None:
        react_prompts = self._react_prompts()

        self.assertEqual(
            react_prompts._simple_human_action_support(BrainDecision.propose_act("click", {"x": "12.5", "y": 34})),
            (True, ""),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(BrainDecision.propose_act("click", {"x": 12})),
            (False, "click missing complete x/y"),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(BrainDecision.propose_act("key_press", {"key": "Enter"})),
            (True, ""),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(BrainDecision.propose_act("key_press", {"key": "tab"})),
            (False, "key_press tab"),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(BrainDecision.propose_act("type_text", {"text": "hi"})),
            (True, ""),
        )


if __name__ == "__main__":
    unittest.main()
