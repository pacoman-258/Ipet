from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import app_route_dependencies as _route_dependency_helpers
from .chat_topics import DEFAULT_TOPIC_TITLE, TopicStore


_MISSING = object()


def _source_value(source: Mapping[str, Any] | Any, name: str, default: Any = _MISSING) -> Any:
    if isinstance(source, Mapping):
        if name in source:
            return source[name]
    elif hasattr(source, name):
        return getattr(source, name)

    if default is not _MISSING:
        return default
    raise AttributeError(f"route dependency source does not expose {name!r}")


class _LiveRouteDependencyContext:
    def __init__(
        self,
        *,
        app: Any,
        source: Mapping[str, Any] | Any,
        topic_store: TopicStore,
        pending_proposals: dict[str, dict[str, Any]],
        audio_cache_dir: Path,
    ) -> None:
        self._app = app
        self._source = source
        self._topic_store = topic_store
        self._pending_proposals = pending_proposals
        self.audio_cache_dir = audio_cache_dir

        self.app_version = lambda: self._app.version
        self.topic_store = lambda: self._read_or_default("TOPIC_STORE", self._topic_store)
        self.settings_page_response = lambda: self._read("_settings_asset_helpers").settings_page_response()
        self.settings_css_response = lambda: self._read("_settings_asset_helpers").settings_css_response()
        self.settings_js_response = lambda: self._read("_settings_asset_helpers").settings_js_response()
        self.settings_form_js_response = lambda: self._read("_settings_asset_helpers").settings_form_js_response()
        self.settings_model_picker_js_response = (
            lambda: self._read("_settings_asset_helpers").settings_model_picker_js_response()
        )
        self.settings_payload = lambda private_config=None: self._call("_settings_payload", private_config)
        self.normalize_private_config = lambda: self._call("_normalize_private_config")
        self.apply_settings_update = (
            lambda incoming, *, current=None: self._call("_apply_settings_update", incoming, current=current)
        )
        self.save_config = lambda private_config: self._call("_save_config", private_config)
        self.list_persona_prompts = lambda: self._call("list_persona_prompts")
        self.list_chrome_profiles = lambda: self._call("list_chrome_profiles")
        self.normalize_provider = lambda value: self._call("normalize_provider", value)
        self.list_provider_models = lambda brain_config: self._call("list_provider_models", brain_config)
        self.sanitize_brain_error = lambda error, brain_config: self._call(
            "_sanitize_brain_error",
            error,
            brain_config,
        )
        self.cleanup_old_audio = lambda cache_dir: self._call("cleanup_old_audio", cache_dir)
        self.synthesize_to_audio = lambda **kwargs: self._call("_synthesize_to_audio_for_route", **kwargs)
        self.stream_qwen_tts_local = lambda **kwargs: self._call("_stream_qwen_tts_local_for_route", **kwargs)
        self.sse = lambda event, data: self._call("_sse", event, data)
        self.decision_from_completion = lambda completion: self._call("_decision_from_completion", completion)
        self.fallback_after_observe_brain_error = lambda observation_text, user_text: self._call(
            "_fallback_after_observe_brain_error",
            observation_text,
            user_text,
        )
        self.looks_like_click_request = lambda text: self._call("_looks_like_click_request", text)
        self.click_coordinate_clarification_text = lambda observation_text: self._call(
            "_click_coordinate_clarification_text",
            observation_text,
        )
        self.looks_like_desktop_action_request = lambda text: self._call("_looks_like_desktop_action_request", text)
        self.coerce_decision_for_human_ops = lambda user_text, decision: self._call(
            "_coerce_decision_for_human_ops",
            user_text,
            decision,
        )
        self.decision_kind = lambda decision: self._call("_decision_kind", decision)
        self.decision_goal = lambda decision: self._call("_decision_goal", decision)
        self.simple_human_action_support = lambda decision: self._call("_simple_human_action_support", decision)
        self.blocked_react_decision = (
            lambda user_text, last_decision=None: self._call("_blocked_react_decision", user_text, last_decision)
        )
        self.unsupported_simple_action_prompt = lambda **kwargs: self._call(
            "_unsupported_simple_action_prompt",
            **kwargs,
        )
        self.create_human_ops_act_proposal = lambda decision, *, session_id, user_text: self._call(
            "_create_human_ops_act_proposal",
            decision,
            session_id=session_id,
            user_text=user_text,
        )
        self.create_human_ops_memory_proposal = lambda decision_or_candidate, **kwargs: self._call(
            "_create_human_ops_memory_proposal",
            decision_or_candidate,
            **kwargs,
        )
        self.proposal_event_payload = lambda proposal_id, proposal: self._call(
            "_proposal_event_payload",
            proposal_id,
            proposal,
        )
        self.perform_human_ops_observe = lambda decision, human_ops_config: self._call(
            "_perform_human_ops_observe",
            decision,
            human_ops_config,
        )
        self.has_partial_coordinate_pair = lambda text: self._call("_has_partial_coordinate_pair", text)
        self.observation_has_reviewable_click_affordance = lambda observation: self._call(
            "_observation_has_reviewable_click_affordance",
            observation,
        )
        self.normalize_observed_click_coordinates = lambda decision, observation: self._call(
            "_normalize_observed_click_coordinates",
            decision,
            observation,
        )
        self.react_followup_prompt = lambda **kwargs: self._call("_react_followup_prompt", **kwargs)
        self.computer_use_context_text = lambda observation: self._call("_computer_use_context_text", observation)
        self.goal_status = lambda decision, *, operation_request: self._call(
            "_goal_status",
            decision,
            operation_request=operation_request,
        )
        self.goal_is_terminal = lambda status: self._call("_goal_is_terminal", status)
        self.proposal_tool_label = lambda proposal: self._call("_proposal_tool_label", proposal)
        self.proposal_continue_after_approval = lambda proposal: self._call("_proposal_continue_after_approval", proposal)
        self.post_approval_observe_prompt = lambda user_text, proposal: self._call(
            "_post_approval_observe_prompt",
            user_text,
            proposal,
        )
        self.human_ops_continuation_prompt = lambda **kwargs: self._call("_human_ops_continuation_prompt", **kwargs)
        self.with_inherited_enter_expected_text = lambda decision, *, previous_proposal, execution: self._call(
            "_with_inherited_enter_expected_text",
            decision,
            previous_proposal=previous_proposal,
            execution=execution,
        )
        self.request_native_approval = lambda proposal: self._call("_request_native_human_ops_approval", proposal)
        self.perform_memory_operation = lambda proposal: self._call("_perform_memory_operation", proposal)
        self.record_memory_review_exchange = lambda **kwargs: self._call("_record_memory_review_exchange", **kwargs)
        self.relationship_memory_context = lambda user_text, memory_config: self._call(
            "_relationship_memory_context",
            user_text,
            memory_config,
        )
        self.mark_memory_recalled = lambda memory_id, assistant_text: self._call(
            "_mark_memory_recalled",
            memory_id,
            assistant_text,
        )
        self.build_memory_candidates = lambda **kwargs: self._call("_build_memory_candidates", **kwargs)
        self.consume_proactive_reply_context = lambda session_id: self._call(
            "_consume_proactive_reply_context",
            session_id,
        )

    @property
    def default_topic_title(self) -> str:
        return self._read_or_default("DEFAULT_TOPIC_TITLE", DEFAULT_TOPIC_TITLE)

    @property
    def tts_available(self) -> Any:
        return self._read("tts_available")

    @property
    def run_brain_turn(self) -> Any:
        return self._read("run_brain_turn")

    @property
    def pending_proposals(self) -> dict[str, dict[str, Any]]:
        return self._read_or_default("HUMAN_OPS_PENDING_PROPOSALS", self._pending_proposals)

    @property
    def perform_human_ops_action(self) -> Any:
        return self._read("_perform_human_ops_action")

    def _read(self, name: str) -> Any:
        return _source_value(self._source, name)

    def _read_or_default(self, name: str, default: Any) -> Any:
        return _source_value(self._source, name, default)

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return self._read(name)(*args, **kwargs)


def create_route_dependency_context(
    *,
    app: Any,
    source: Mapping[str, Any] | Any,
    topic_store: TopicStore,
    pending_proposals: dict[str, dict[str, Any]],
    audio_cache_dir: Path,
) -> _route_dependency_helpers.AppRouteDependencyContext:
    return _LiveRouteDependencyContext(
        app=app,
        source=source,
        topic_store=topic_store,
        pending_proposals=pending_proposals,
        audio_cache_dir=audio_cache_dir,
    )
