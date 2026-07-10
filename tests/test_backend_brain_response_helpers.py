from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

from brain.decisions import BrainDecision, DecisionKind
from backend import brain_response_helpers as helpers


class EmptyMessageError(Exception):
    def __str__(self) -> str:
        return ""


class BrainResponseHelpersTests(unittest.TestCase):
    def test_sse_preserves_unicode_json_payload(self) -> None:
        result = helpers.sse("token", {"text": "你好", "symbol": "星"})

        self.assertEqual(result, 'event: token\ndata: {"text": "你好", "symbol": "星"}\n\n')

    def test_sanitize_brain_error_redacts_saved_values_and_inline_credentials(self) -> None:
        result = helpers.sanitize_brain_error(
            RuntimeError(
                "401 saved-secret via https://saved.example/v1 "
                "Bearer bearer-secret "
                "https://proxy.example/chat?api_key=query-secret&token=query-token&key=query-key"
            ),
            {
                "api_key": "saved-secret",
                "model_endpoint": "https://saved.example/v1",
            },
        )

        self.assertNotIn("saved-secret", result)
        self.assertNotIn("https://saved.example/v1", result)
        self.assertNotIn("bearer-secret", result)
        self.assertNotIn("query-secret", result)
        self.assertNotIn("query-token", result)
        self.assertNotIn("query-key", result)
        self.assertIn("Bearer [redacted]", result)
        self.assertIn("api_key=[redacted]", result)
        self.assertIn("token=[redacted]", result)
        self.assertIn("key=[redacted]", result)

    def test_sanitize_brain_error_uses_exception_class_name_for_empty_text(self) -> None:
        self.assertEqual(helpers.sanitize_brain_error(EmptyMessageError(), {}), "EmptyMessageError")

    def test_sanitize_brain_error_truncates_long_text(self) -> None:
        result = helpers.sanitize_brain_error(RuntimeError("x" * 300), {})

        self.assertEqual(result, ("x" * 240) + "...")

    def test_decision_from_completion_passes_through_existing_brain_decision(self) -> None:
        decision = BrainDecision.observe("screen")

        result = helpers.decision_from_completion(SimpleNamespace(text="ignored", decision=decision))

        self.assertIs(result, decision)

    def test_decision_from_completion_falls_back_to_say_text(self) -> None:
        result = helpers.decision_from_completion(SimpleNamespace(text="fallback text", decision={"kind": "say"}))

        self.assertEqual(result.kind, DecisionKind.SAY)
        self.assertEqual(result.summary, "fallback text")
        self.assertEqual(result.payload["text"], "fallback text")


class BackendAppBrainResponseWrapperTests(unittest.TestCase):
    def test_app_wrappers_delegate_to_helper_module(self) -> None:
        import backend.app as backend_app

        delegated_decision = BrainDecision.say("delegated")
        completion = SimpleNamespace(text="completion")
        error = RuntimeError("boom")
        config = {"api_key": "secret"}

        with mock.patch.object(helpers, "sse", return_value="wrapped-sse") as sse_mock:
            self.assertEqual(backend_app._sse("token", {"text": "hi"}), "wrapped-sse")
            sse_mock.assert_called_once_with("token", {"text": "hi"})

        with mock.patch.object(helpers, "sanitize_brain_error", return_value="wrapped-error") as sanitize_mock:
            self.assertEqual(backend_app._sanitize_brain_error(error, config), "wrapped-error")
            sanitize_mock.assert_called_once_with(error, config)

        with mock.patch.object(
            helpers,
            "decision_from_completion",
            return_value=delegated_decision,
        ) as decision_mock:
            self.assertIs(backend_app._decision_from_completion(completion), delegated_decision)
            decision_mock.assert_called_once_with(completion)


if __name__ == "__main__":
    unittest.main()
