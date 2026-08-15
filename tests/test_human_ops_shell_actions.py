from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain.decisions import BrainDecision
from human_ops.proposals import build_human_ops_act_proposal
from human_ops.shell_actions import assess_shell_risk, execute_shell_action, validate_shell_action


def _arguments(command: str, *, model_risk: str = "safe", risk_reason: str = "read-only inspection") -> dict:
    return {
        "command": command,
        "model_risk": model_risk,
        "risk_reason": risk_reason,
        "timeout_sec": 5,
    }


class ShellRiskTests(unittest.TestCase):
    def test_safe_command_is_not_reviewable(self) -> None:
        arguments = _arguments("pwd")
        assessment = assess_shell_risk(arguments)
        proposal = build_human_ops_act_proposal(BrainDecision.propose_act("shell", arguments))

        self.assertFalse(assessment.dangerous)
        self.assertFalse(proposal.requires_review)

    def test_model_risk_alone_requires_review(self) -> None:
        arguments = _arguments("echo hello", model_risk="uncertain", risk_reason="destination is unclear")
        assessment = assess_shell_risk(arguments)
        proposal = build_human_ops_act_proposal(BrainDecision.propose_act("shell", arguments))

        self.assertTrue(assessment.dangerous)
        self.assertIn("model_uncertain", assessment.matched_rules)
        self.assertTrue(proposal.requires_review)

    def test_local_catalog_overrides_model_safe_claim(self) -> None:
        assessment = assess_shell_risk(_arguments("rm -rf ./cache"))

        self.assertTrue(assessment.dangerous)
        self.assertIn("filesystem_delete", assessment.matched_rules)

    def test_nested_interpreter_and_redirection_require_review(self) -> None:
        nested = assess_shell_risk(_arguments("python -c 'print(1)'"))
        redirected = assess_shell_risk(_arguments("echo changed > result.txt"))

        self.assertIn("nested_interpreter", nested.matched_rules)
        self.assertIn("shell_write", redirected.matched_rules)

    def test_command_substitution_cannot_hide_catalog_match(self) -> None:
        assessment = assess_shell_risk(_arguments("echo $(rm -rf ./cache)"))

        self.assertIn("filesystem_delete", assessment.matched_rules)

    def test_unknown_command_requires_review(self) -> None:
        arguments = _arguments("some_unknown_utility --flag")
        assessment = assess_shell_risk(arguments)
        proposal = build_human_ops_act_proposal(BrainDecision.propose_act("shell", arguments))

        self.assertTrue(assessment.dangerous)
        self.assertIn("unknown_command", assessment.matched_rules)
        self.assertTrue(proposal.requires_review)

    def test_git_readonly_subcommands_are_safe(self) -> None:
        status_proposal = build_human_ops_act_proposal(BrainDecision.propose_act("shell", _arguments("git status")))
        log_proposal = build_human_ops_act_proposal(BrainDecision.propose_act("shell", _arguments("git log -n 5")))

        self.assertFalse(status_proposal.requires_review)
        self.assertFalse(log_proposal.requires_review)

    def test_sensitive_path_readers_require_review(self) -> None:
        sensitive_commands = [
            "cat ~/.ssh/id_rsa",
            "head ~/.aws/credentials",
            "grep secret .env",
            "cat /etc/passwd",
            "cat /etc/shadow",
            "tail -n 20 .env.local",
            "grep -i token id_rsa",
        ]
        for cmd in sensitive_commands:
            assessment = assess_shell_risk(_arguments(cmd))
            proposal = build_human_ops_act_proposal(BrainDecision.propose_act("shell", _arguments(cmd)))
            self.assertTrue(assessment.dangerous, f"Expected {cmd} to be dangerous")
            self.assertTrue(proposal.requires_review, f"Expected {cmd} to require review")

    def test_missing_model_assessment_is_invalid(self) -> None:
        valid, reason = validate_shell_action({"command": "pwd"})

        self.assertFalse(valid)
        self.assertIn("model_risk", reason)


class ShellExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_command_runs_in_resolved_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            proposal = build_human_ops_act_proposal(
                BrainDecision.propose_act("shell", _arguments("pwd"))
            ).approve()

            result = await execute_shell_action(proposal, default_cwd=root)

        self.assertTrue(result["shell_done"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(Path(result["stdout"].strip()).resolve(), root.resolve())
        self.assertFalse(result["stdout_truncated"])

    async def test_unapproved_command_is_rejected(self) -> None:
        proposal = build_human_ops_act_proposal(BrainDecision.propose_act("shell", _arguments("pwd")))

        with self.assertRaises(PermissionError):
            await execute_shell_action(proposal, default_cwd=Path.cwd())
