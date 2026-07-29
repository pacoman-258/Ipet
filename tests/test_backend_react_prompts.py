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
        )

        self.assertIs(result, decision)

    def test_coerce_decision_preserves_model_say_for_desktop_request(self) -> None:
        react_prompts = self._react_prompts()

        for user_text in ("看看屏幕", "打开微信", "打开 B 站并观看视频"):
            with self.subTest(user_text=user_text):
                decision = BrainDecision.say("我需要先判断合适的交互表面。")
                result = react_prompts._coerce_decision_for_human_ops(
                    user_text,
                    decision,
                )

                self.assertIs(result, decision)

    def test_coerce_decision_keeps_plain_say(self) -> None:
        react_prompts = self._react_prompts()
        decision = BrainDecision.say("只聊天")

        result = react_prompts._coerce_decision_for_human_ops(
            "你好",
            decision,
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

    def test_react_followup_direct_brain_image_does_not_claim_an_observe_model_answered(self) -> None:
        react_prompts = self._react_prompts()

        prompt = react_prompts._react_followup_prompt(
            user_text="看看设置窗口",
            decision=BrainDecision.observe("screen"),
            remaining_budget=2,
            observation_text="截图成功，当前截图将由 Brain 模型直接观察并决定下一步。",
            coordinate_context="Coordinate context: macOS screen coordinates.",
            brain_observed_image=True,
        )

        self.assertIn("当前用户消息附带了 Body 刚捕获的屏幕截图", prompt)
        self.assertIn("请由你直接观察图片", prompt)
        self.assertIn('coordinate_space="image_pixels"', prompt)
        self.assertIn("后端会确定性换算", prompt)
        self.assertNotIn("observe 用自然语言回答如下", prompt)

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
        self.assertIn("playwright、launch_app、click、type_text、key_press enter", prompt)
        self.assertIn("默认使用 Playwright", prompt)
        self.assertIn("不为执行方式询问", prompt)
        self.assertIn("两者都缺少时才用 goal.status=need_user", prompt)
        self.assertIn("设置页传入的 Playwright 默认资料", prompt)
        self.assertIn("不要重复询问已经明确的选择", prompt)
        self.assertIn("Brain 已判断目标是本地应用", prompt)
        self.assertIn("arguments.target_app", prompt)

    def test_simple_action_support_click_coordinates_and_enter_key_rules(self) -> None:
        react_prompts = self._react_prompts()

        self.assertEqual(
            react_prompts._simple_human_action_support(BrainDecision.propose_act("click", {"target_app": "Chrome", "x": "12.5", "y": 34})),
            (True, ""),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(
                BrainDecision.propose_act(
                    "click",
                    {
                        "target_app": "WeChat",
                        "ax_ref": {
                            "app_id": "wechat",
                            "role": "AXButton",
                            "path": [0, 2],
                            "fingerprint": "copied",
                        },
                    },
                )
            ),
            (True, ""),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(BrainDecision.propose_act("click", {"target_app": "Chrome", "x": 12})),
            (False, "click missing complete x/y"),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(BrainDecision.propose_act("key_press", {"target_app": "Chrome", "key": "Enter"})),
            (True, ""),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(BrainDecision.propose_act("key_press", {"target_app": "Chrome", "key": "tab"})),
            (False, "key_press tab"),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(BrainDecision.propose_act("type_text", {"target_app": "Chrome", "text": "hi"})),
            (True, ""),
        )
        chat_input_ref = {
            "app_id": "wechat",
            "role": "AXTextArea",
            "path": [0, 2],
            "fingerprint": "copied",
        }
        self.assertEqual(
            react_prompts._simple_human_action_support(
                BrainDecision.propose_act(
                    "type_text",
                    {
                        "target_app": "WeChat",
                        "intended_chat": "目标会话",
                        "ax_ref": chat_input_ref,
                        "text": "hi",
                    },
                )
            ),
            (True, ""),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(
                BrainDecision.propose_act(
                    "type_text",
                    {
                        "target_app": "Music",
                        "text": "",
                        "replace_existing": True,
                    },
                )
            ),
            (False, "replace text missing reviewed AX input"),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(
                BrainDecision.propose_act(
                    "type_text",
                    {
                        "target_app": "Music",
                        "ax_ref": {
                            "app_id": "music",
                            "role": "AXTextField",
                            "path": [1, 1, 2, 0],
                            "fingerprint": "copied",
                        },
                        "text": "",
                        "replace_existing": True,
                    },
                )
            ),
            (True, ""),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(
                BrainDecision.propose_act(
                    "type_text",
                    {
                        "target_app": "WeChat",
                        "ax_ref": chat_input_ref,
                        "text": "hi",
                    },
                )
            ),
            (False, "chat type_text missing intended_chat"),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(
                BrainDecision.propose_act(
                    "type_text",
                    {
                        "target_app": "WeChat",
                        "ax_ref": {
                            "app_id": "wechat",
                            "role": "AXTextField",
                            "path": [0, 1],
                            "input_kind": "search_field",
                            "fingerprint": "copied",
                        },
                        "text": "文件传输助手",
                    },
                )
            ),
            (True, ""),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(
                BrainDecision.propose_act(
                    "key_press",
                    {
                        "target_app": "WeChat",
                        "key": "enter",
                        "intended_chat": "目标会话",
                        "expected_text": "hi",
                        "input_ax_ref": chat_input_ref,
                    },
                )
            ),
            (True, ""),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(BrainDecision.propose_act("launch_app", {"app": "WeChat"})),
            (True, ""),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(BrainDecision.propose_act("launch_app", {})),
            (False, "launch_app missing app name"),
        )
        self.assertEqual(
            react_prompts._simple_human_action_support(
                BrainDecision.propose_act("playwright", {"profile": "工作", "operation": "click", "ref": "e8"})
            ),
            (True, ""),
        )
        supported, reason = react_prompts._simple_human_action_support(
            BrainDecision.propose_act("playwright", {"profile": "工作", "operation": "eval", "text": "document.cookie"})
        )
        self.assertFalse(supported)
        self.assertIn("Unsupported Playwright operation", reason)
        supported, reason = react_prompts._simple_human_action_support(
            BrainDecision.propose_act("playwright", {"operation": "snapshot"})
        )
        self.assertFalse(supported)
        self.assertIn("profile must be explicitly selected", reason)

    def test_production_coercion_preserves_model_surface_choice(self) -> None:
        model_decision = BrainDecision.observe("screen")

        decision = backend_app._coerce_decision_for_human_ops("打开 B 站并观看视频", model_decision)

        self.assertIs(decision, model_decision)

    def test_production_coercion_does_not_rewrite_model_launch_app_name(self) -> None:
        model_decision = BrainDecision.propose_act(
            "launch_app",
            {"app": "网易云音乐", "label": "网易云音乐"},
            goal={"objective": "打开网易云音乐", "status": "handoff_review"},
        )

        decision = backend_app._coerce_decision_for_human_ops("打开网易云音乐", model_decision)

        self.assertIs(decision, model_decision)


if __name__ == "__main__":
    unittest.main()
