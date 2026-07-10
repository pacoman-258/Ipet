from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from brain.decisions import BrainDecision
from brain.llm import (
    GOOGLE_AISTUDIO_DEFAULT_MODEL,
    PROVIDER_GOOGLE_AISTUDIO,
    BrainLLMError,
    list_provider_models,
    normalize_provider,
    run_brain_turn,
)
from human_ops import ReviewableProposal

from .chat_topics import DEFAULT_TOPIC_TITLE, TopicStore
from .tts import (
    cleanup_old_audio,
    synthesize_to_audio,
    tts_available,
)
from .vision_analyzer import VisionAnalyzer
from . import asr_disabled_routes as _asr_disabled_route_helpers
from . import audio_routes as _audio_route_helpers
from . import app_route_context as _app_route_context_helpers
from . import app_route_dependencies as _app_route_dependency_helpers
from . import brain_response_helpers as _brain_response_helpers
from . import brain_models_route as _brain_models_route_helpers
from . import chat_stream_routes as _chat_stream_route_helpers
from . import chat_topics_routes as _chat_topics_route_helpers
from . import desktop_command_client as _desktop_command_client_helpers
from . import app_action_adapters as _app_action_adapter_helpers
from . import app_adapters as _app_adapters
from . import health_routes as _health_route_helpers
from . import human_ops_decision_routes as _human_ops_decision_route_helpers
from . import app_proposal_adapters as _human_ops_proposal_flow_helpers
from . import settings_assets as _settings_asset_helpers
from . import app_settings_adapters as _settings_adapter_helpers
from . import settings_routes as _settings_route_helpers
from .settings_defaults import ALLOWED_CONFIG_KEYS, NEO_DEFAULTS


_app_adapters.install_app_compat_exports(globals())


app = FastAPI(title="Ipet Neo Aspect Backend", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "pet_config.json"
AUDIO_CACHE_DIR = ROOT_DIR / "backend" / "audio_cache"
CHAT_TOPICS_ROOT = ROOT_DIR / "data" / "chat_topics"
DESKTOP_COMMAND_PATH = ROOT_DIR / ".pet_desktop_command.json"

TOPIC_STORE = TopicStore(CHAT_TOPICS_ROOT)
HUMAN_OPS_PENDING_PROPOSALS: dict[str, dict[str, Any]] = {}


def _human_ops_proposal_flow_deps() -> _human_ops_proposal_flow_helpers.ProposalFlowDependencies:
    return _human_ops_proposal_flow_helpers.proposal_flow_dependencies(
        pending_proposals=HUMAN_OPS_PENDING_PROPOSALS,
        uuid_factory=lambda: uuid4().hex,
        time_func=time.time,
        looks_like_desktop_action_request=_looks_like_desktop_action_request,
        looks_like_chat_reply_request=_looks_like_chat_reply_request,
        goal_is_terminal=_goal_is_terminal,
        computer_use_context_text=_computer_use_context_text,
    )


def _app_action_adapter_deps(
    *,
    send_desktop_command=_desktop_command_client_helpers.send_desktop_command,
    perform_human_ops_click=None,
    perform_human_ops_action=_desktop_command_client_helpers.perform_human_ops_action,
) -> _app_action_adapter_helpers.AppActionAdapterDependencies:
    return _app_action_adapter_helpers.AppActionAdapterDependencies(
        command_path=DESKTOP_COMMAND_PATH,
        send_desktop_command=send_desktop_command,
        perform_human_ops_click=perform_human_ops_click,
        perform_human_ops_action=perform_human_ops_action,
    )


async def _send_desktop_command(
    command_type: str,
    payload: dict[str, Any] | None = None,
    *,
    command_path: Path | None = None,
    timeout_sec: float = 8.0,
) -> dict[str, Any]:
    return await _app_action_adapter_helpers.send_desktop_command(
        command_type,
        payload,
        deps=_app_action_adapter_helpers.AppActionAdapterDependencies(
            command_path=command_path or DESKTOP_COMMAND_PATH,
            send_desktop_command=_desktop_command_client_helpers.send_desktop_command,
        ),
        timeout_sec=timeout_sec,
    )


async def _perform_human_ops_click(proposal: ReviewableProposal) -> dict[str, Any]:
    return await _app_action_adapter_helpers.perform_human_ops_click(
        proposal,
        deps=_app_action_adapter_deps(),
    )


async def _perform_human_ops_action(proposal: ReviewableProposal) -> dict[str, Any]:
    if proposal.proposal_type != "act":
        raise RuntimeError("unsupported human ops proposal")
    return await _app_action_adapter_helpers.perform_human_ops_action(
        proposal,
        deps=_app_action_adapter_deps(
            send_desktop_command=_send_desktop_command,
            perform_human_ops_click=_perform_human_ops_click,
            perform_human_ops_action=_desktop_command_client_helpers.perform_human_ops_action,
        ),
    )


def _route_dependency_context() -> _app_route_dependency_helpers.AppRouteDependencyContext:
    return _app_route_context_helpers.create_route_dependency_context(
        app=app,
        source=globals(),
        topic_store=TOPIC_STORE,
        pending_proposals=HUMAN_OPS_PENDING_PROPOSALS,
        audio_cache_dir=AUDIO_CACHE_DIR,
    )


def _chat_topics_route_deps() -> _chat_topics_route_helpers.ChatTopicsRouteDependencies:
    return _app_route_dependency_helpers.create_chat_topics_route_deps(_route_dependency_context())


_chat_topics_route_helpers.register_chat_topics_routes(app, _chat_topics_route_deps())


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    return _settings_adapter_helpers.deep_merge(base, override)


def _load_raw_config() -> dict[str, Any]:
    return _settings_adapter_helpers.load_raw_config(_settings_adapter_deps())


def _normalize_private_config(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    return _settings_adapter_helpers.normalize_private_config(raw, deps=_settings_adapter_deps())


def _secret_preview(value: str) -> str:
    return _settings_adapter_helpers.secret_preview(value)


def _public_config(private_config: dict[str, Any]) -> dict[str, Any]:
    return _settings_adapter_helpers.public_config(private_config)


def _settings_payload(private_config: dict[str, Any] | None = None) -> dict[str, Any]:
    return _settings_adapter_helpers.settings_payload(private_config, deps=_settings_adapter_deps())


def _apply_settings_update(incoming: dict[str, Any], *, current: dict[str, Any] | None = None) -> dict[str, Any]:
    return _settings_adapter_helpers.apply_settings_update(incoming, current=current, deps=_settings_adapter_deps())


def _save_config(private_config: dict[str, Any]) -> None:
    _settings_adapter_helpers.save_config(private_config, deps=_settings_adapter_deps())


def _settings_adapter_deps() -> _settings_adapter_helpers.SettingsAdapterDependencies:
    return _settings_adapter_helpers.settings_adapter_dependencies(
        config_path=CONFIG_PATH,
        defaults=NEO_DEFAULTS,
        allowed_keys=ALLOWED_CONFIG_KEYS,
    )


def _settings_route_deps() -> _settings_route_helpers.SettingsRouteDependencies:
    return _app_route_dependency_helpers.create_settings_route_deps(_route_dependency_context())


_settings_route_helpers.register_settings_routes(app, _settings_route_deps())


def _sse(event: str, data: dict[str, Any]) -> str:
    return _brain_response_helpers.sse(event, data)


def _sanitize_brain_error(error: Exception, brain_config: dict[str, Any]) -> str:
    return _brain_response_helpers.sanitize_brain_error(error, brain_config)


async def _list_provider_models_for_route(brain_config: dict[str, Any]) -> Any:
    return await list_provider_models(brain_config)


async def _synthesize_to_audio_for_route(**kwargs: Any) -> Any:
    return await synthesize_to_audio(**kwargs)


def _brain_models_route_deps() -> _brain_models_route_helpers.BrainModelsRouteDependencies:
    return _app_route_dependency_helpers.create_brain_models_route_deps(_route_dependency_context())


def _audio_route_deps() -> _audio_route_helpers.AudioRouteDependencies:
    return _app_route_dependency_helpers.create_audio_route_deps(_route_dependency_context())


_brain_models_route_helpers.register_brain_models_routes(app, _brain_models_route_deps())
_asr_disabled_route_helpers.register_asr_disabled_routes(app)
_audio_route_helpers.register_audio_routes(app, _audio_route_deps())


def _decision_from_completion(completion: Any) -> BrainDecision:
    return _brain_response_helpers.decision_from_completion(completion)


def _coerce_int(value: Any, fallback: int = 0) -> int:
    return _human_ops_proposal_flow_helpers.coerce_int(value, fallback)


def _proposal_tool_label(proposal: ReviewableProposal) -> str:
    return _human_ops_proposal_flow_helpers.proposal_tool_label(proposal)


def _proposal_event_payload(proposal_id: str, proposal: ReviewableProposal) -> dict[str, Any]:
    return _human_ops_proposal_flow_helpers.proposal_event_payload(proposal_id, proposal)


def _should_default_continue_after_approval(
    user_text: str,
    goal: dict[str, Any],
    *,
    action_type: str,
    arguments: dict[str, Any],
) -> bool:
    return _human_ops_proposal_flow_helpers.should_default_continue_after_approval(
        user_text,
        goal,
        action_type=action_type,
        arguments=arguments,
        deps=_human_ops_proposal_flow_deps(),
    )


def _create_human_ops_act_proposal(decision: BrainDecision, *, session_id: str, user_text: str) -> tuple[str, ReviewableProposal]:
    return _human_ops_proposal_flow_helpers.create_human_ops_act_proposal(
        decision,
        session_id=session_id,
        user_text=user_text,
        deps=_human_ops_proposal_flow_deps(),
    )


def _proposal_arguments(proposal: ReviewableProposal) -> dict[str, Any]:
    return _human_ops_proposal_flow_helpers.proposal_arguments(proposal)


def _proposal_continue_after_approval(proposal: ReviewableProposal) -> bool:
    return _human_ops_proposal_flow_helpers.proposal_continue_after_approval(proposal)


def _with_inherited_enter_expected_text(
    decision: BrainDecision,
    *,
    previous_proposal: ReviewableProposal,
    execution: dict[str, Any],
) -> BrainDecision:
    return _human_ops_proposal_flow_helpers.with_inherited_enter_expected_text(
        decision,
        previous_proposal=previous_proposal,
        execution=execution,
    )


def _human_ops_continuation_prompt(
    *,
    user_text: str,
    proposal: ReviewableProposal,
    execution: dict[str, Any],
    observation: dict[str, Any],
) -> str:
    return _human_ops_proposal_flow_helpers.human_ops_continuation_prompt(
        user_text=user_text,
        proposal=proposal,
        execution=execution,
        observation=observation,
        deps=_human_ops_proposal_flow_deps(),
    )


def _post_approval_observe_prompt(user_text: str, proposal: ReviewableProposal) -> str:
    return _human_ops_proposal_flow_helpers.post_approval_observe_prompt(
        user_text,
        proposal,
        deps=_human_ops_proposal_flow_deps(),
    )


def _health_route_deps() -> _health_route_helpers.HealthRouteDependencies:
    return _app_route_dependency_helpers.create_health_route_deps(_route_dependency_context())


def _chat_stream_route_deps() -> _chat_stream_route_helpers.ChatStreamFlowDependencies:
    return _app_route_dependency_helpers.create_chat_stream_route_deps(_route_dependency_context())


def _human_ops_decision_route_deps() -> _human_ops_decision_route_helpers.HumanOpsApprovalFlowDependencies:
    return _app_route_dependency_helpers.create_human_ops_decision_route_deps(_route_dependency_context())


_health_route_helpers.register_health_routes(app, _health_route_deps)
_chat_stream_route_helpers.register_chat_stream_routes(app, _chat_stream_route_deps)
_human_ops_decision_route_helpers.register_human_ops_decision_routes(app, _human_ops_decision_route_deps)
