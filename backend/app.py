from __future__ import annotations

import asyncio
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
    list_persona_prompts,
    list_provider_models,
    normalize_provider,
    run_brain_turn,
)
from human_ops import ReviewableProposal
from human_ops.chrome_profiles import list_chrome_profiles

from .chat_topics import DEFAULT_TOPIC_TITLE, TopicStore
from .environment import EnvironmentService
from .ipet_memory_store import (
    IpetMemoryStore,
    build_memory_candidates_from_turn,
    build_relationship_memory_context,
)
from .tts import (
    cleanup_old_audio,
    stream_qwen_tts_local,
    synthesize_to_audio,
    tts_available as _tts_available,
    is_fish_audio_provider,
)
from .vision_analyzer import VisionAnalyzer
from .vision import VisionService
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
from . import environment_routes as _environment_route_helpers
from . import memory_routes as _memory_route_helpers
from . import proactive_context as _proactive_context_helpers
from . import app_proposal_adapters as _human_ops_proposal_flow_helpers
from . import settings_assets as _settings_asset_helpers
from . import app_settings_adapters as _settings_adapter_helpers
from . import settings_live2d as _settings_live2d_helpers
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
IPET_MEMORY_ROOT = ROOT_DIR / "data" / "ipet_memory"
DESKTOP_COMMAND_PATH = ROOT_DIR / ".pet_desktop_command.json"

TOPIC_STORE = TopicStore(CHAT_TOPICS_ROOT)
MEMORY_STORE = IpetMemoryStore(IPET_MEMORY_ROOT)
ENVIRONMENT_SERVICE = EnvironmentService(NEO_DEFAULTS.get("environment", {}))
VISION_SERVICE = VisionService(NEO_DEFAULTS.get("vision", {}))
HUMAN_OPS_PENDING_PROPOSALS: dict[str, dict[str, Any]] = {}
_AX_INDEX_REFRESH_TASK: asyncio.Task[Any] | None = None


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
    private_config = _normalize_private_config()
    human_ops_config = private_config.get("human_ops", {}) if isinstance(private_config.get("human_ops"), dict) else {}
    filesystem_config = human_ops_config.get("filesystem", {}) if isinstance(human_ops_config.get("filesystem"), dict) else {}
    configured_roots = filesystem_config.get("allowed_roots") if isinstance(filesystem_config.get("allowed_roots"), list) else []
    filesystem_enabled = filesystem_config.get("enabled", True) is not False
    filesystem_roots = [ROOT_DIR] if filesystem_enabled else []
    filesystem_roots.extend(Path(str(root)).expanduser() for root in configured_roots if str(root or "").strip())
    def _filesystem_limit(name: str, fallback: int) -> int:
        try:
            return max(1, int(filesystem_config.get(name, fallback)))
        except (TypeError, ValueError):
            return fallback
    return _app_action_adapter_helpers.AppActionAdapterDependencies(
        command_path=DESKTOP_COMMAND_PATH,
        send_desktop_command=send_desktop_command,
        perform_human_ops_click=perform_human_ops_click,
        perform_human_ops_action=perform_human_ops_action,
        filesystem_roots=tuple(filesystem_roots),
        filesystem_max_read_bytes=_filesystem_limit("max_read_bytes", 1_000_000),
        filesystem_max_write_bytes=_filesystem_limit("max_write_bytes", 1_000_000),
        filesystem_max_list_entries=_filesystem_limit("max_list_entries", 200),
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


async def _request_native_human_ops_approval(proposal: ReviewableProposal) -> dict[str, Any]:
    return await _app_action_adapter_helpers.request_native_human_ops_approval(
        proposal,
        deps=_app_action_adapter_deps(send_desktop_command=_send_desktop_command),
    )


async def _notify_human_ops_action(proposal: ReviewableProposal, *, task_id: str) -> dict[str, Any]:
    return await _app_action_adapter_helpers.notify_human_ops_action(
        proposal,
        task_id=task_id,
        deps=_app_action_adapter_deps(send_desktop_command=_send_desktop_command),
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


def _chat_tts_config() -> dict[str, Any]:
    private_config = _normalize_private_config()
    chat_config = private_config.get("chat") if isinstance(private_config.get("chat"), dict) else {}
    return chat_config


def tts_available(provider: str | None = None, provider_url: str | None = None) -> bool:
    if not is_fish_audio_provider(provider):
        return _tts_available(provider, provider_url)
    chat_config = _chat_tts_config()
    return _tts_available(
        provider,
        provider_url,
        api_key=str(chat_config.get("tts_api_key") or ""),
        reference_id=str(chat_config.get("tts_voice_id") or ""),
    )


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


def _list_local_models() -> list[dict[str, Any]]:
    return _settings_live2d_helpers.list_local_models(root_dir=ROOT_DIR)


async def _preview_settings_action(payload: dict[str, Any]) -> dict[str, Any]:
    command = _settings_live2d_helpers.preview_command(payload, root_dir=ROOT_DIR)
    return await _send_desktop_command(
        command["type"],
        command["payload"],
        timeout_sec=8,
    )


async def _accessibility_index_status() -> dict[str, Any]:
    return await _send_desktop_command(
        "accessibility_index_status",
        {},
        timeout_sec=8,
    )


async def _refresh_accessibility_index() -> dict[str, Any]:
    private_config = _normalize_private_config()
    human_ops = private_config.get("human_ops", {}) if isinstance(private_config.get("human_ops"), dict) else {}
    if human_ops.get("accessibility", True) is False:
        raise ValueError("请先开启“允许读取可访问性结构”")
    return await _send_desktop_command(
        "accessibility_index_refresh",
        {},
        timeout_sec=600,
    )


def _schedule_accessibility_index_refresh() -> bool:
    global _AX_INDEX_REFRESH_TASK
    private_config = _normalize_private_config()
    human_ops = private_config.get("human_ops", {}) if isinstance(private_config.get("human_ops"), dict) else {}
    if human_ops.get("accessibility", True) is False:
        return False
    if _AX_INDEX_REFRESH_TASK is not None and not _AX_INDEX_REFRESH_TASK.done():
        return False

    async def refresh() -> None:
        try:
            await _refresh_accessibility_index()
        except Exception:
            return

    _AX_INDEX_REFRESH_TASK = asyncio.create_task(refresh())
    return True


def _settings_adapter_deps() -> _settings_adapter_helpers.SettingsAdapterDependencies:
    return _settings_adapter_helpers.settings_adapter_dependencies(
        config_path=CONFIG_PATH,
        defaults=NEO_DEFAULTS,
        allowed_keys=ALLOWED_CONFIG_KEYS,
    )


def _settings_route_deps() -> _settings_route_helpers.SettingsRouteDependencies:
    return _app_route_dependency_helpers.create_settings_route_deps(_route_dependency_context())


_settings_route_helpers.register_settings_routes(app, _settings_route_deps())


def _memory_route_deps() -> _memory_route_helpers.MemoryRouteDependencies:
    return _memory_route_helpers.MemoryRouteDependencies(
        memory_store=MEMORY_STORE,
        pending_proposals=HUMAN_OPS_PENDING_PROPOSALS,
        normalize_private_config=_normalize_private_config,
    )


_memory_route_helpers.register_memory_routes(app, _memory_route_deps)


def _sse(event: str, data: dict[str, Any]) -> str:
    return _brain_response_helpers.sse(event, data)


def _sanitize_brain_error(error: Exception, brain_config: dict[str, Any]) -> str:
    return _brain_response_helpers.sanitize_brain_error(error, brain_config)


async def _list_provider_models_for_route(brain_config: dict[str, Any]) -> Any:
    return await list_provider_models(brain_config)


async def _synthesize_to_audio_for_route(**kwargs: Any) -> Any:
    if is_fish_audio_provider(kwargs.get("provider")):
        chat_config = _chat_tts_config()
        kwargs = {
            **kwargs,
            "api_key": str(chat_config.get("tts_api_key") or ""),
            "reference_id": str(chat_config.get("tts_voice_id") or ""),
            "model": str(chat_config.get("tts_model") or ""),
        }
    return await synthesize_to_audio(**kwargs)


async def _stream_qwen_tts_local_for_route(**kwargs: Any):
    async for chunk in stream_qwen_tts_local(**kwargs):
        yield chunk


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


def _create_human_ops_memory_proposal(
    decision_or_candidate: BrainDecision | dict[str, Any],
    *,
    session_id: str,
    user_text: str,
    turn_id: str,
    origin: str,
    retention_days: int,
) -> tuple[str, ReviewableProposal]:
    return _human_ops_proposal_flow_helpers.create_human_ops_memory_proposal(
        decision_or_candidate,
        session_id=session_id,
        user_text=user_text,
        turn_id=turn_id,
        origin=origin,
        retention_days=retention_days,
        memory_store=MEMORY_STORE,
        deps=_human_ops_proposal_flow_deps(),
    )


def _perform_memory_operation(proposal: ReviewableProposal) -> dict[str, Any]:
    if proposal.proposal_type != "remember":
        return {"ok": False, "error": "unsupported_memory_proposal"}
    return MEMORY_STORE.apply_operation(proposal.payload)


def _record_memory_review_exchange(*, session_id: str, user_text: str, assistant_text: str) -> None:
    TOPIC_STORE.append_exchange(session_id, user_text=user_text, assistant_text=assistant_text)


def _relationship_memory_context(user_text: str, memory_config: dict[str, Any]) -> tuple[str, list[str]]:
    allowed_kinds = {"profile", "preference", "boundary", "person", "open_loop", "shared_moment", "general"}
    if memory_config.get("preferences_enabled") is False:
        allowed_kinds.discard("preference")
    if memory_config.get("relationship_enabled") is False:
        allowed_kinds.difference_update({"person", "open_loop", "shared_moment"})
    def memory_int(name: str, fallback: int) -> int:
        try:
            return int(memory_config.get(name, fallback))
        except (TypeError, ValueError):
            return fallback

    return build_relationship_memory_context(
        MEMORY_STORE,
        user_text,
        allowed_kinds=allowed_kinds,
        limit=5,
        follow_up_enabled=memory_config.get("follow_up_enabled") is not False,
        follow_up_cooldown_hours=max(1, memory_int("follow_up_cooldown_hours", 24)),
        quiet_hours_start=max(0, min(23, memory_int("quiet_hours_start", 22))),
        quiet_hours_end=max(0, min(23, memory_int("quiet_hours_end", 8))),
    )


def _mark_memory_recalled(memory_id: str, assistant_text: str) -> None:
    mentioned_ids = {str(record.get("id") or "") for record in MEMORY_STORE.search(assistant_text, limit=5)}
    if memory_id in mentioned_ids:
        MEMORY_STORE.mark_recalled(memory_id)


def _build_memory_candidates(**kwargs: Any) -> list[dict[str, Any]]:
    return build_memory_candidates_from_turn(**kwargs)


def _consume_proactive_reply_context(session_id: str) -> str:
    return ENVIRONMENT_SERVICE.consume_user_reply_context(session_id)


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


def _proactive_conversation_history(session_id: str, opportunity: dict[str, Any]) -> list[dict[str, str]]:
    return _proactive_context_helpers.build_proactive_conversation_history(
        topic_store=TOPIC_STORE,
        session_id=session_id,
        opportunity=opportunity,
        private_config=_normalize_private_config(),
        relationship_memory_context=_relationship_memory_context,
    )


def _environment_route_deps() -> _environment_route_helpers.EnvironmentRouteDependencies:
    return _environment_route_helpers.EnvironmentRouteDependencies(
        environment_service=ENVIRONMENT_SERVICE,
        vision_service=VISION_SERVICE,
        normalize_private_config=_normalize_private_config,
        run_brain_turn=run_brain_turn,
        decision_from_completion=_decision_from_completion,
        proactive_conversation_history=_proactive_conversation_history,
    )


def _human_ops_decision_route_deps() -> _human_ops_decision_route_helpers.HumanOpsApprovalFlowDependencies:
    return _app_route_dependency_helpers.create_human_ops_decision_route_deps(_route_dependency_context())


_health_route_helpers.register_health_routes(app, _health_route_deps)
_chat_stream_route_helpers.register_chat_stream_routes(app, _chat_stream_route_deps)
_human_ops_decision_route_helpers.register_human_ops_decision_routes(app, _human_ops_decision_route_deps)
_environment_route_helpers.register_environment_routes(app, _environment_route_deps())
