from __future__ import annotations

from typing import Any

from brain.decisions import BrainDecision, DecisionKind

from . import computer_use_context as _computer_use_context_helpers
from . import human_ops_observe as _human_ops_observe_helpers
from . import observe_context as _observe_context_helpers
from . import observe_result as _observe_result_helpers
from . import react_prompts as _react_prompt_helpers
from .vision_analyzer import VisionAnalyzer as _DefaultVisionAnalyzer


def _looks_like_desktop_observe_request(user_text: str) -> bool:
    return _computer_use_context_helpers._looks_like_desktop_observe_request(user_text)


def _looks_like_desktop_action_request(user_text: str) -> bool:
    return _computer_use_context_helpers._looks_like_desktop_action_request(user_text)


def _looks_like_app_launch_request(user_text: str) -> bool:
    return _computer_use_context_helpers._looks_like_app_launch_request(user_text)


def _looks_like_chat_reply_request(user_text: str) -> bool:
    return _computer_use_context_helpers._looks_like_chat_reply_request(user_text)


def _looks_like_click_request(user_text: str) -> bool:
    return _computer_use_context_helpers._looks_like_click_request(user_text)


_COORDINATE_X_RE = _computer_use_context_helpers._COORDINATE_X_RE
_COORDINATE_Y_RE = _computer_use_context_helpers._COORDINATE_Y_RE


def _has_partial_coordinate_pair(text: str) -> bool:
    return _computer_use_context_helpers._has_partial_coordinate_pair(text)


def _observe_click_coordinate_status(observation_text: str, *, require_coordinates: bool = False) -> dict[str, str]:
    return _computer_use_context_helpers._observe_click_coordinate_status(
        observation_text,
        require_coordinates=require_coordinates,
    )


def _coordinate_pair_from_text(text: str) -> dict[str, int]:
    return _computer_use_context_helpers._coordinate_pair_from_text(text)


def _coordinate_pair_near_terms(text: str, terms: tuple[str, ...]) -> dict[str, int]:
    return _computer_use_context_helpers._coordinate_pair_near_terms(text, terms)


def _has_negative_visibility_evidence(text: str, terms: tuple[str, ...]) -> bool:
    return _computer_use_context_helpers._has_negative_visibility_evidence(text, terms)


def _looks_like_visual_observation_failure(text: str) -> bool:
    return _computer_use_context_helpers._looks_like_visual_observation_failure(text)


def _has_captured_screen_frame(active_observation: dict[str, Any], frame: dict[str, Any]) -> bool:
    return _computer_use_context_helpers._has_captured_screen_frame(active_observation, frame)


def _infer_app_label(text: str, target_hint: str = "") -> str:
    return _computer_use_context_helpers._infer_app_label(text, target_hint)


def _infer_contact_label(text: str, target_hint: str = "") -> str:
    return _computer_use_context_helpers._infer_contact_label(text, target_hint)


def _infer_recent_chat_messages(text: str) -> list[dict[str, str]]:
    return _computer_use_context_helpers._infer_recent_chat_messages(text)


def _label_aliases(label: str) -> set[str]:
    return _computer_use_context_helpers._label_aliases(label)


def _candidate_matches_label(candidate: dict[str, Any], label: str) -> bool:
    return _computer_use_context_helpers._candidate_matches_label(candidate, label)


def _point_from_candidate(candidate: dict[str, Any]) -> dict[str, int]:
    return _computer_use_context_helpers._point_from_candidate(candidate)


def _candidate_location_for_app_label(frame: dict[str, Any], app_label: str) -> dict[str, int]:
    return _computer_use_context_helpers._candidate_location_for_app_label(frame, app_label)


def _infer_chat_context(text: str, target_hint: str = "", *, is_wechat_surface: bool = False) -> dict[str, Any]:
    return _computer_use_context_helpers._infer_chat_context(
        text,
        target_hint,
        is_wechat_surface=is_wechat_surface,
    )


def _infer_computer_use_context(
    observation_text: str,
    frame: dict[str, Any] | None = None,
    target_hint: str = "",
) -> dict[str, Any]:
    return _computer_use_context_helpers._infer_computer_use_context(observation_text, frame, target_hint)


def _computer_use_context_text(observation: dict[str, Any] | None) -> str:
    return _computer_use_context_helpers._computer_use_context_text(observation)


def _observation_has_reviewable_click_affordance(observation: dict[str, Any]) -> bool:
    return _computer_use_context_helpers._observation_has_reviewable_click_affordance(observation)


def _observe_decision_requests_click(decision: BrainDecision) -> bool:
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else {}
    text = " ".join(
        str(part or "")
        for part in (
            decision.summary,
            payload.get("target"),
            payload.get("observe_prompt"),
            payload.get("question"),
            goal.get("objective"),
            goal.get("stage"),
            goal.get("missing"),
            goal.get("next"),
        )
    )
    return _looks_like_click_request(text)


def _click_coordinate_clarification_text(observation_text: str) -> str:
    return _react_prompt_helpers._click_coordinate_clarification_text(observation_text)


def _click_coordinate_observe_failure_text(observation_text: str) -> str:
    return _react_prompt_helpers._click_coordinate_observe_failure_text(observation_text)


def _fallback_after_observe_brain_error(observation_text: str, user_text: str) -> str:
    return _react_prompt_helpers._fallback_after_observe_brain_error(
        observation_text,
        user_text,
        looks_like_click_request=_looks_like_click_request,
    )


def _coerce_decision_for_human_ops(user_text: str, decision: BrainDecision) -> BrainDecision:
    return _react_prompt_helpers._coerce_decision_for_human_ops(
        user_text,
        decision,
        looks_like_desktop_observe_request=_looks_like_desktop_observe_request,
        looks_like_desktop_action_request=_looks_like_desktop_action_request,
    )


def _decision_kind(decision: BrainDecision) -> DecisionKind:
    return _react_prompt_helpers._decision_kind(decision)


def _decision_goal(decision: BrainDecision) -> dict[str, Any]:
    return _react_prompt_helpers._decision_goal(decision)


def _goal_status(decision: BrainDecision, *, operation_request: bool) -> str:
    return _react_prompt_helpers._goal_status(decision, operation_request=operation_request)


def _goal_is_terminal(status: str) -> bool:
    return _react_prompt_helpers._goal_is_terminal(status)


def _react_missing_summary(goal: dict[str, Any]) -> str:
    return _react_prompt_helpers._react_missing_summary(goal)


def _blocked_react_decision(user_text: str, last_decision: BrainDecision | None = None) -> BrainDecision:
    return _react_prompt_helpers._blocked_react_decision(user_text, last_decision)


def _react_followup_prompt(
    *,
    user_text: str,
    decision: BrainDecision,
    remaining_budget: int,
    observation_text: str = "",
    coordinate_context: str = "",
    computer_use_context: str = "",
    correction: bool = False,
) -> str:
    return _react_prompt_helpers._react_followup_prompt(
        user_text=user_text,
        decision=decision,
        remaining_budget=remaining_budget,
        observation_text=observation_text,
        coordinate_context=coordinate_context,
        computer_use_context=computer_use_context,
        correction=correction,
    )


def _simple_human_action_support(decision: BrainDecision) -> tuple[bool, str]:
    return _react_prompt_helpers._simple_human_action_support(decision)


def _has_numeric_action_argument(arguments: dict[str, Any], key: str) -> bool:
    return _react_prompt_helpers._has_numeric_action_argument(arguments, key)


def _unsupported_simple_action_prompt(
    *,
    user_text: str,
    decision: BrainDecision,
    unsupported_action: str,
    remaining_budget: int,
) -> str:
    return _react_prompt_helpers._unsupported_simple_action_prompt(
        user_text=user_text,
        decision=decision,
        unsupported_action=unsupported_action,
        remaining_budget=remaining_budget,
    )


def _observation_text_from_result(result: dict[str, Any]) -> str:
    return _observe_result_helpers.observation_text_from_result(result)


def _image_resolution_from_frame(frame: dict[str, Any]) -> dict[str, int]:
    return _observe_context_helpers.image_resolution_from_frame(frame)


def _screen_bounds_from_frame(frame: dict[str, Any]) -> dict[str, int]:
    return _observe_context_helpers.screen_bounds_from_frame(frame)


def _screen_resolution_from_frame(frame: dict[str, Any]) -> dict[str, int]:
    return _observe_context_helpers.screen_resolution_from_frame(frame)


def _coordinate_scale_for_frame(
    screen_bounds: dict[str, int],
    image_resolution: dict[str, int],
) -> dict[str, float]:
    return _observe_context_helpers.coordinate_scale_for_frame(screen_bounds, image_resolution)


def _format_coordinate_scale(value: Any) -> str:
    return _observe_context_helpers.format_coordinate_scale(value)


def _observe_coordinate_context_from_frame(frame: dict[str, Any]) -> str:
    return _observe_context_helpers.observe_coordinate_context_from_frame(frame)


def _default_observe_prompt_for_request(user_text: str, target: str) -> str:
    return _observe_context_helpers.default_observe_prompt_for_request(user_text, target)


def _goal_text_for_observe(decision: BrainDecision) -> str:
    return _observe_context_helpers.goal_text_for_observe(decision)


def _goal_requests_click_coordinate_followup(decision: BrainDecision) -> bool:
    return _observe_context_helpers.goal_requests_click_coordinate_followup(decision)


def _coordinate_followup_target_from_goal(decision: BrainDecision, user_text: str, target: str) -> str:
    return _observe_context_helpers.coordinate_followup_target_from_goal(decision, user_text, target)


def _observe_target_hint_from_decision(decision: BrainDecision, fallback: str = "screen") -> str:
    return _observe_context_helpers.observe_target_hint_from_decision(decision, fallback)


def _observe_prompt_from_decision(decision: BrainDecision, user_text: str) -> str:
    return _observe_context_helpers.observe_prompt_from_decision(decision, user_text)


def _frame_with_observe_prompt(frame: dict[str, Any], decision: BrainDecision, user_text: str) -> dict[str, Any]:
    return _observe_context_helpers.frame_with_observe_prompt(frame, decision, user_text)


def _observe_model_analyzer_config(human_ops_config: dict[str, Any]) -> dict[str, Any]:
    return _observe_context_helpers.observe_model_analyzer_config(human_ops_config)


def _vision_analyzer_class() -> type:
    try:
        from . import app as backend_app

        return getattr(backend_app, "VisionAnalyzer")
    except Exception:
        return _DefaultVisionAnalyzer


def _enrich_observation_frame_with_model(frame: dict[str, Any], human_ops_config: dict[str, Any]) -> dict[str, Any]:
    analyzer_config = _observe_model_analyzer_config(human_ops_config)
    return _observe_result_helpers.enrich_observation_frame_with_model(
        frame,
        analyzer_config,
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
            looks_like_click_request=_looks_like_click_request,
            infer_computer_use_context=_infer_computer_use_context,
            enrich_observation_frame_with_model=_enrich_observation_frame_with_model,
            observation_text_from_result=_observation_text_from_result,
            observe_decision_requests_click=_observe_decision_requests_click,
            observe_click_coordinate_status=_observe_click_coordinate_status,
            goal_requests_click_coordinate_followup=_goal_requests_click_coordinate_followup,
            click_coordinate_observe_failure_text=_click_coordinate_observe_failure_text,
        ),
    )


APP_COMPAT_EXPORTS: dict[str, Any] = {
    name: globals()[name]
    for name in (
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
        "_looks_like_app_launch_request",
        "_looks_like_chat_reply_request",
        "_looks_like_click_request",
        "_looks_like_desktop_action_request",
        "_looks_like_desktop_observe_request",
        "_looks_like_visual_observation_failure",
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
    )
}


def install_app_compat_exports(namespace: dict[str, Any]) -> None:
    namespace.update(APP_COMPAT_EXPORTS)
