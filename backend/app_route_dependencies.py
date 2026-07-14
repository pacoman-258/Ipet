from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from brain.decisions import BrainDecision, DecisionKind
from fastapi.responses import Response
from human_ops.approvals import ReviewableProposal

from .chat_topics import DEFAULT_TOPIC_TITLE, TopicStore, normalize_topic_id
from . import audio_routes as _audio_route_helpers
from . import brain_models_route as _brain_models_route_helpers
from . import chat_stream_routes as _chat_stream_route_helpers
from . import chat_topics_routes as _chat_topics_route_helpers
from . import health_routes as _health_route_helpers
from . import human_ops_decision_routes as _human_ops_decision_route_helpers
from . import settings_routes as _settings_route_helpers


@dataclass(frozen=True)
class AppRouteDependencyContext:
    app_version: Callable[[], str]
    topic_store: Callable[[], TopicStore]
    default_topic_title: str = DEFAULT_TOPIC_TITLE
    settings_page_response: Callable[[], Response] | None = None
    settings_css_response: Callable[[], Response] | None = None
    settings_js_response: Callable[[], Response] | None = None
    settings_form_js_response: Callable[[], Response] | None = None
    settings_model_picker_js_response: Callable[[], Response] | None = None
    settings_payload: Callable[..., dict[str, Any]] | None = None
    normalize_private_config: Callable[[], dict[str, Any]] | None = None
    apply_settings_update: Callable[..., dict[str, Any]] | None = None
    save_config: Callable[[dict[str, Any]], None] | None = None
    list_persona_prompts: Callable[[], list[dict[str, Any]]] | None = None
    list_chrome_profiles: Callable[[], Any] | None = None
    normalize_provider: Callable[[Any], str] | None = None
    list_provider_models: Callable[[dict[str, Any]], Awaitable[Any]] | None = None
    sanitize_brain_error: Callable[[Exception, dict[str, Any]], str] | None = None
    audio_cache_dir: Path | None = None
    tts_available: Callable[[str | None, str | None], bool] | None = None
    cleanup_old_audio: Callable[[Path], None] | None = None
    synthesize_to_audio: Callable[..., Awaitable[Any]] | None = None
    stream_qwen_tts_local: Callable[..., AsyncIterator[bytes]] | None = None
    sse: Callable[[str, dict[str, Any]], str] | None = None
    run_brain_turn: Callable[..., Awaitable[Any]] | None = None
    decision_from_completion: Callable[[Any], BrainDecision] | None = None
    fallback_after_observe_brain_error: Callable[[str, str], str] | None = None
    looks_like_click_request: Callable[[str], bool] | None = None
    click_coordinate_clarification_text: Callable[[str], str] | None = None
    looks_like_desktop_action_request: Callable[[str], bool] | None = None
    coerce_decision_for_human_ops: Callable[[str, BrainDecision], BrainDecision] | None = None
    decision_kind: Callable[[BrainDecision], DecisionKind] | None = None
    decision_goal: Callable[[BrainDecision], dict[str, Any]] | None = None
    simple_human_action_support: Callable[[BrainDecision], tuple[bool, str]] | None = None
    blocked_react_decision: Callable[[str, BrainDecision | None], BrainDecision] | None = None
    unsupported_simple_action_prompt: Callable[..., str] | None = None
    create_human_ops_act_proposal: Callable[..., tuple[str, ReviewableProposal]] | None = None
    proposal_event_payload: Callable[[str, ReviewableProposal], dict[str, Any]] | None = None
    perform_human_ops_observe: Callable[[BrainDecision, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None
    has_partial_coordinate_pair: Callable[[str], bool] | None = None
    observation_has_reviewable_click_affordance: Callable[[dict[str, Any]], bool] | None = None
    react_followup_prompt: Callable[..., str] | None = None
    computer_use_context_text: Callable[[dict[str, Any] | None], str] | None = None
    goal_status: Callable[..., str] | None = None
    goal_is_terminal: Callable[[str], bool] | None = None
    pending_proposals: dict[str, dict[str, Any]] | None = None
    proposal_tool_label: Callable[[ReviewableProposal], str] | None = None
    perform_human_ops_action: Callable[[ReviewableProposal], Awaitable[dict[str, Any]]] | None = None
    proposal_continue_after_approval: Callable[[ReviewableProposal], bool] | None = None
    post_approval_observe_prompt: Callable[[str, ReviewableProposal], str] | None = None
    human_ops_continuation_prompt: Callable[..., str] | None = None
    with_inherited_enter_expected_text: Callable[..., BrainDecision] | None = None
    request_native_approval: Callable[[ReviewableProposal], Awaitable[dict[str, Any]]] | None = None
    normalize_observed_click_coordinates: Callable[[BrainDecision, dict[str, Any] | None], BrainDecision] | None = None


def create_chat_topics_route_deps(context: AppRouteDependencyContext) -> _chat_topics_route_helpers.ChatTopicsRouteDependencies:
    def conversation_saving_enabled() -> bool:
        private_config = context.normalize_private_config() if context.normalize_private_config else {}
        memory_config = private_config.get("memory", {}) if isinstance(private_config.get("memory"), dict) else {}
        return memory_config.get("conversation_saving") is not False

    return _chat_topics_route_helpers.ChatTopicsRouteDependencies(
        topic_store=context.topic_store,
        default_topic_title=context.default_topic_title,
        conversation_saving_enabled=conversation_saving_enabled,
    )


def create_settings_route_deps(context: AppRouteDependencyContext) -> _settings_route_helpers.SettingsRouteDependencies:
    return _settings_route_helpers.SettingsRouteDependencies(
        settings_page_response=context.settings_page_response or (lambda: Response()),
        settings_css_response=context.settings_css_response or (lambda: Response()),
        settings_js_response=context.settings_js_response or (lambda: Response()),
        settings_form_js_response=context.settings_form_js_response or (lambda: Response()),
        settings_model_picker_js_response=context.settings_model_picker_js_response or (lambda: Response()),
        settings_payload=context.settings_payload or (lambda private_config=None: {}),
        normalize_private_config=context.normalize_private_config or (lambda: {}),
        apply_settings_update=context.apply_settings_update or (lambda incoming, *, current=None: {}),
        save_config=context.save_config or (lambda private_config: None),
        list_persona_prompts=context.list_persona_prompts or (lambda: []),
        list_chrome_profiles=context.list_chrome_profiles or (lambda: ()),
    )


def create_brain_models_route_deps(context: AppRouteDependencyContext) -> _brain_models_route_helpers.BrainModelsRouteDependencies:
    return _brain_models_route_helpers.BrainModelsRouteDependencies(
        normalize_private_config=context.normalize_private_config or (lambda: {}),
        normalize_provider=context.normalize_provider or (lambda value: str(value or "")),
        list_provider_models=context.list_provider_models or (lambda brain_config: []),
        sanitize_brain_error=context.sanitize_brain_error or (lambda error, brain_config: str(error)),
    )


def create_audio_route_deps(context: AppRouteDependencyContext) -> _audio_route_helpers.AudioRouteDependencies:
    return _audio_route_helpers.AudioRouteDependencies(
        audio_cache_dir=context.audio_cache_dir or Path("."),
        tts_available=context.tts_available or (lambda provider, provider_url: False),
        cleanup_old_audio=context.cleanup_old_audio or (lambda cache_dir: None),
        synthesize_to_audio=context.synthesize_to_audio or (lambda **kwargs: None),
        stream_qwen_tts_local=context.stream_qwen_tts_local,
    )


def create_health_route_deps(context: AppRouteDependencyContext) -> _health_route_helpers.HealthRouteDependencies:
    return _health_route_helpers.HealthRouteDependencies(
        app_version=context.app_version,
        normalize_private_config=context.normalize_private_config or (lambda: {}),
        tts_available=context.tts_available or (lambda provider, provider_url: False),
    )


def create_chat_stream_route_deps(context: AppRouteDependencyContext) -> _chat_stream_route_helpers.ChatStreamFlowDependencies:
    return _chat_stream_route_helpers.ChatStreamFlowDependencies(
        normalize_private_config=context.normalize_private_config or (lambda: {}),
        normalize_topic_id=normalize_topic_id,
        normalize_provider=context.normalize_provider or (lambda value: str(value or "")),
        topic_store=context.topic_store(),
        sse=context.sse or (lambda event, data: ""),
        run_brain_turn=context.run_brain_turn or (lambda *args, **kwargs: None),
        decision_from_completion=context.decision_from_completion or (lambda completion: BrainDecision.say("")),
        sanitize_brain_error=context.sanitize_brain_error or (lambda error, brain_config: str(error)),
        fallback_after_observe_brain_error=context.fallback_after_observe_brain_error or (lambda text, user_text: text),
        looks_like_click_request=context.looks_like_click_request or (lambda text: False),
        click_coordinate_clarification_text=context.click_coordinate_clarification_text or (lambda text: text),
        looks_like_desktop_action_request=context.looks_like_desktop_action_request or (lambda text: False),
        coerce_decision_for_human_ops=context.coerce_decision_for_human_ops or (lambda user_text, decision: decision),
        decision_kind=context.decision_kind or (lambda decision: DecisionKind.SAY),
        decision_goal=context.decision_goal or (lambda decision: {}),
        simple_human_action_support=context.simple_human_action_support or (lambda decision: (False, "")),
        blocked_react_decision=context.blocked_react_decision or (lambda user_text, last_decision=None: BrainDecision.say(user_text)),
        unsupported_simple_action_prompt=context.unsupported_simple_action_prompt or (lambda **kwargs: ""),
        create_human_ops_act_proposal=context.create_human_ops_act_proposal or (lambda **kwargs: ("", None)),
        proposal_event_payload=context.proposal_event_payload or (lambda proposal_id, proposal: {}),
        perform_human_ops_observe=context.perform_human_ops_observe or (lambda decision, human_ops_config: {}),
        has_partial_coordinate_pair=context.has_partial_coordinate_pair or (lambda text: False),
        observation_has_reviewable_click_affordance=context.observation_has_reviewable_click_affordance
        or (lambda observation: False),
        react_followup_prompt=context.react_followup_prompt or (lambda **kwargs: ""),
        computer_use_context_text=context.computer_use_context_text or (lambda observation: ""),
        goal_status=context.goal_status or (lambda decision, *, operation_request: ""),
        goal_is_terminal=context.goal_is_terminal or (lambda status: False),
        pending_proposals=context.pending_proposals if context.pending_proposals is not None else {},
        normalize_observed_click_coordinates=context.normalize_observed_click_coordinates
        or (lambda decision, observation: decision),
    )


def create_human_ops_decision_route_deps(
    context: AppRouteDependencyContext,
) -> _human_ops_decision_route_helpers.HumanOpsApprovalFlowDependencies:
    return _human_ops_decision_route_helpers.HumanOpsApprovalFlowDependencies(
        pending_proposals=context.pending_proposals if context.pending_proposals is not None else {},
        sse=context.sse or (lambda event, data: ""),
        proposal_tool_label=context.proposal_tool_label or (lambda proposal: ""),
        perform_human_ops_action=context.perform_human_ops_action or (lambda proposal: {}),
        proposal_continue_after_approval=context.proposal_continue_after_approval or (lambda proposal: False),
        normalize_private_config=context.normalize_private_config or (lambda: {}),
        perform_human_ops_observe=context.perform_human_ops_observe or (lambda decision, human_ops_config: {}),
        post_approval_observe_prompt=context.post_approval_observe_prompt or (lambda user_text, proposal: user_text),
        sanitize_brain_error=context.sanitize_brain_error or (lambda error, brain_config: str(error)),
        human_ops_continuation_prompt=context.human_ops_continuation_prompt or (lambda **kwargs: ""),
        run_brain_turn=context.run_brain_turn or (lambda *args, **kwargs: None),
        decision_from_completion=context.decision_from_completion or (lambda completion: BrainDecision.say("")),
        decision_kind=context.decision_kind or (lambda decision: DecisionKind.SAY),
        simple_human_action_support=context.simple_human_action_support or (lambda decision: (False, "")),
        blocked_react_decision=context.blocked_react_decision or (lambda user_text, last_decision=None: BrainDecision.say(user_text)),
        unsupported_simple_action_prompt=context.unsupported_simple_action_prompt or (lambda **kwargs: ""),
        with_inherited_enter_expected_text=context.with_inherited_enter_expected_text
        or (lambda decision, *, previous_proposal, execution: decision),
        create_human_ops_act_proposal=context.create_human_ops_act_proposal or (lambda **kwargs: ("", None)),
        proposal_event_payload=context.proposal_event_payload or (lambda proposal_id, proposal: {}),
        react_followup_prompt=context.react_followup_prompt or (lambda **kwargs: ""),
        computer_use_context_text=context.computer_use_context_text or (lambda observation: ""),
        goal_status=context.goal_status or (lambda decision, *, operation_request: ""),
        goal_is_terminal=context.goal_is_terminal or (lambda status: False),
        request_native_approval=context.request_native_approval,
        normalize_observed_click_coordinates=context.normalize_observed_click_coordinates
        or (lambda decision, observation: decision),
    )
