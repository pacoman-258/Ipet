from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest import mock

import backend.app as backend_app
from backend import app_adapters


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_PATH = ROOT_DIR / "backend" / "app.py"

EXPECTED_COMPAT_EXPORTS = {
    "_COORDINATE_X_RE",
    "_COORDINATE_Y_RE",
    "_blocked_react_decision",
    "_candidate_location_for_app_label",
    "_candidate_matches_label",
    "_click_coordinate_clarification_text",
    "_click_coordinate_observe_failure_text",
    "_computer_use_context_text",
    "_coordinate_followup_target_from_goal",
    "_coordinate_pair_from_text",
    "_coordinate_pair_near_terms",
    "_coordinate_scale_for_frame",
    "_coerce_decision_for_human_ops",
    "_decision_goal",
    "_decision_kind",
    "_default_observe_prompt_for_request",
    "_enrich_observation_frame_with_model",
    "_fallback_after_observe_brain_error",
    "_format_coordinate_scale",
    "_frame_with_observe_prompt",
    "_goal_is_terminal",
    "_goal_requests_click_coordinate_followup",
    "_goal_status",
    "_goal_text_for_observe",
    "_has_captured_screen_frame",
    "_has_negative_visibility_evidence",
    "_has_numeric_action_argument",
    "_has_partial_coordinate_pair",
    "_image_resolution_from_frame",
    "_infer_app_label",
    "_infer_chat_context",
    "_infer_computer_use_context",
    "_infer_contact_label",
    "_infer_recent_chat_messages",
    "_label_aliases",
    "_looks_like_visual_observation_failure",
    "_normalize_observed_click_coordinates",
    "_observation_has_reviewable_click_affordance",
    "_observation_text_from_result",
    "_observe_click_coordinate_status",
    "_observe_coordinate_context_from_frame",
    "_observe_decision_requests_click",
    "_observe_model_analyzer_config",
    "_observe_prompt_from_decision",
    "_observe_target_hint_from_decision",
    "_perform_human_ops_observe",
    "_point_from_candidate",
    "_react_followup_prompt",
    "_react_missing_summary",
    "_screen_bounds_from_frame",
    "_screen_resolution_from_frame",
    "_simple_human_action_support",
    "_unsupported_simple_action_prompt",
}


class BackendAppAdapterExportsTests(unittest.TestCase):
    def test_backend_app_installs_one_explicit_compat_export_map(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_names = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "app_adapters"
            for alias in node.names
        }

        self.assertEqual(imported_names, set())
        self.assertEqual(set(app_adapters.APP_COMPAT_EXPORTS), EXPECTED_COMPAT_EXPORTS)
        for name, exported in app_adapters.APP_COMPAT_EXPORTS.items():
            with self.subTest(name=name):
                self.assertIs(getattr(backend_app, name), exported)
                self.assertIs(exported, getattr(app_adapters, name))

    def test_installer_updates_only_compat_names(self) -> None:
        namespace = {"sentinel": mock.sentinel.existing}

        app_adapters.install_app_compat_exports(namespace)

        self.assertIs(namespace["sentinel"], mock.sentinel.existing)
        self.assertEqual(set(namespace) - {"sentinel"}, EXPECTED_COMPAT_EXPORTS)
        for name, exported in app_adapters.APP_COMPAT_EXPORTS.items():
            self.assertIs(namespace[name], exported)

    def test_route_dependencies_keep_reading_patched_backend_app_globals(self) -> None:
        with mock.patch.object(
            backend_app,
            "_observe_decision_requests_click",
            return_value=mock.sentinel.result,
        ) as patched:
            deps = backend_app._chat_stream_route_deps()
            decision = mock.sentinel.decision
            result = deps.observe_decision_requests_click(decision)

        self.assertIs(result, mock.sentinel.result)
        patched.assert_called_once_with(decision)


if __name__ == "__main__":
    unittest.main()
