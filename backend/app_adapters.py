from __future__ import annotations

from typing import Any

from brain.decisions import BrainDecision

from . import human_ops_observe as _human_ops_observe_helpers
from . import observe_result as _observe_result_helpers
from .computer_use_context import (
    _COORDINATE_X_RE,
    _COORDINATE_Y_RE,
    _candidate_location_for_app_label,
    _candidate_matches_label,
    _computer_use_context_text,
    _coordinate_pair_from_text,
    _coordinate_pair_near_terms,
    _has_captured_screen_frame,
    _has_negative_visibility_evidence,
    _has_partial_coordinate_pair,
    _infer_app_label,
    _infer_chat_context,
    _infer_computer_use_context,
    _infer_contact_label,
    _infer_recent_chat_messages,
    _label_aliases,
    _looks_like_visual_observation_failure,
    _observation_has_reviewable_click_affordance,
    _observe_click_coordinate_status,
    _point_from_candidate,
)
from .react_prompts import (
    _blocked_react_decision,
    _click_coordinate_clarification_text,
    _click_coordinate_observe_failure_text,
    _coerce_decision_for_human_ops,
    _decision_goal,
    _decision_kind,
    _fallback_after_observe_brain_error,
    _goal_is_terminal,
    _goal_status,
    _has_numeric_action_argument,
    _react_followup_prompt,
    _react_missing_summary,
    _simple_human_action_support,
    _unsupported_simple_action_prompt,
)
from .observe_context import (
    coordinate_followup_target_from_goal as _coordinate_followup_target_from_goal,
    coordinate_scale_for_frame as _coordinate_scale_for_frame,
    default_observe_prompt_for_request as _default_observe_prompt_for_request,
    format_coordinate_scale as _format_coordinate_scale,
    frame_with_observe_prompt as _frame_with_observe_prompt,
    goal_requests_click_coordinate_followup as _goal_requests_click_coordinate_followup,
    goal_text_for_observe as _goal_text_for_observe,
    image_resolution_from_frame as _image_resolution_from_frame,
    normalize_observed_click_coordinates as _normalize_observed_click_coordinates,
    observe_coordinate_context_from_frame as _observe_coordinate_context_from_frame,
    observe_model_analyzer_config as _observe_model_analyzer_config,
    observe_prompt_from_decision as _observe_prompt_from_decision,
    observe_target_hint_from_decision as _observe_target_hint_from_decision,
    screen_bounds_from_frame as _screen_bounds_from_frame,
    screen_resolution_from_frame as _screen_resolution_from_frame,
)
from .observe_result import (
    observation_text_from_result as _observation_text_from_result,
)


def _observe_decision_requests_click(decision: BrainDecision) -> bool:
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    return payload.get("require_coordinates") is True


def _vision_analyzer_class() -> type:
    from . import app as backend_app

    return backend_app.VisionAnalyzer


def _enrich_observation_frame_with_model(frame: dict[str, Any], human_ops_config: dict[str, Any]) -> dict[str, Any]:
    analyzer_config = _observe_model_analyzer_config(human_ops_config)
    return _observe_result_helpers.enrich_observation_frame_with_model(
        frame,
        analyzer_config,
        analyzer_class=_vision_analyzer_class(),
    )


def _enrich_observation_frame_with_local_ocr(
    frame: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    return _observe_result_helpers.enrich_observation_frame_with_local_ocr(
        frame,
        query,
        analyzer_class=_vision_analyzer_class(),
    )


async def _send_desktop_command_from_app(
    command_type: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_sec: float = 8.0,
) -> dict[str, Any]:
    from . import app as backend_app

    return await backend_app._send_desktop_command(command_type, payload, timeout_sec=timeout_sec)


async def _perform_human_ops_observe(decision: BrainDecision, human_ops_config: dict[str, Any]) -> dict[str, Any]:
    return await _human_ops_observe_helpers.perform_human_ops_observe(
        decision,
        human_ops_config,
        deps=_human_ops_observe_helpers.HumanOpsObserveDependencies(
            send_desktop_command=_send_desktop_command_from_app,
            observe_target_hint_from_decision=_observe_target_hint_from_decision,
            frame_with_observe_prompt=_frame_with_observe_prompt,
            observe_coordinate_context_from_frame=_observe_coordinate_context_from_frame,
            observe_model_analyzer_config=_observe_model_analyzer_config,
            infer_computer_use_context=_infer_computer_use_context,
            enrich_observation_frame_with_model=_enrich_observation_frame_with_model,
            observation_text_from_result=_observation_text_from_result,
            observe_decision_requests_click=_observe_decision_requests_click,
            observe_click_coordinate_status=_observe_click_coordinate_status,
            goal_requests_click_coordinate_followup=_goal_requests_click_coordinate_followup,
            click_coordinate_observe_failure_text=_click_coordinate_observe_failure_text,
            enrich_observation_frame_with_local_ocr=(
                _enrich_observation_frame_with_local_ocr
            ),
        ),
    )
