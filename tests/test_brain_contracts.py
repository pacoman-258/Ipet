from __future__ import annotations

import unittest

from brain.contracts import (
    ACTION_SCHEMAS,
    GOAL_STATUSES,
    action_choices,
    action_names,
    decision_kinds,
    is_terminal_goal_status,
    render_action_contract,
    render_decision_contract,
    render_final_allowlist,
    validate_action_arguments,
    validate_decision_payload,
)
from human_ops.approvals import ReviewableProposal
from human_ops.filesystem_actions import FILESYSTEM_ACTIONS


class BrainContractTests(unittest.TestCase):
    def test_one_action_schema_drives_prompt_validator_and_approval_allowlist(self) -> None:
        names = action_names()

        self.assertEqual(names, tuple(ACTION_SCHEMAS))
        self.assertEqual(FILESYSTEM_ACTIONS, frozenset(action_names(("file",))))
        for action in names:
            with self.subTest(action=action):
                self.assertIn(action, render_action_contract("agent"))
                self.assertIn(action, render_final_allowlist("agent"))
                schema = ACTION_SCHEMAS[action]
                arguments = {
                    name: field["example"]
                    for name, field in schema["properties"].items()
                    if name in schema.get("required", ())
                }
                if schema.get("any_of"):
                    for name in schema["any_of"][0]:
                        arguments[name] = schema["properties"][name]["example"]
                self.assertEqual(validate_action_arguments(action, arguments), (True, ""))
                self.assertEqual(
                    ReviewableProposal.act(action_type=action, summary=action, payload=arguments).payload["action_type"],
                    action,
                )

    def test_file_required_fields_are_generated_and_validated(self) -> None:
        file_contract = render_action_contract("file")

        self.assertIn('file_write: {"path":"notes.txt","content":"内容"}', file_contract)
        self.assertIn('file_copy: {"source":"source.txt","destination":"copy.txt"}', file_contract)
        self.assertIn('file_move: {"source":"source.txt","destination":"moved.txt"}', file_contract)
        self.assertEqual(
            validate_action_arguments("file_write", {"path": "notes.txt"}),
            (False, "file_write missing content"),
        )
        self.assertEqual(
            validate_action_arguments("file_copy", {"source": "a.txt"}),
            (False, "file_copy missing destination"),
        )

    def test_prompt_profiles_expose_only_their_capability_slice(self) -> None:
        chat_decisions = render_decision_contract("chat")
        desktop_actions = render_action_contract("desktop")
        file_actions = render_action_contract("file")

        self.assertIn("say", chat_decisions)
        self.assertIn("stop", chat_decisions)
        self.assertNotIn("propose_act", chat_decisions)
        self.assertIn("click", desktop_actions)
        self.assertIn("playwright", desktop_actions)
        self.assertNotIn("file_write", desktop_actions)
        self.assertIn("file_write", file_actions)
        self.assertNotIn("click", file_actions)
        self.assertNotIn("think", decision_kinds("desktop"))
        self.assertNotIn("propose_remember", decision_kinds("file"))
        self.assertEqual(decision_kinds("proactive"), ("say", "stop"))

    def test_decision_and_playwright_choices_share_the_contract(self) -> None:
        self.assertIn("stop", render_decision_contract("agent"))
        self.assertIn("snapshot", action_choices("playwright", "operation"))
        self.assertEqual(
            validate_decision_payload("say", {"text": "你好"}),
            (True, ""),
        )
        self.assertEqual(
            validate_decision_payload("propose_remember", {"category": "preference", "text": ""}),
            (False, "propose_remember missing text"),
        )
        self.assertEqual(
            validate_decision_payload("propose_act", {"action_type": "file_write", "arguments": {"path": "a.txt"}}),
            (False, "file_write missing content"),
        )
        self.assertEqual(
            validate_decision_payload("say", {"text": "完成", "goal": {"status": "invented"}}),
            (False, f"say goal.status must be one of {', '.join(GOAL_STATUSES)}"),
        )
        self.assertTrue(is_terminal_goal_status("done"))
        self.assertFalse(is_terminal_goal_status("in_progress"))


if __name__ == "__main__":
    unittest.main()
