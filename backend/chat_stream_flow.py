from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import uuid4

from brain.contracts import action_scope
from brain.decisions import BrainDecision, DecisionKind
from brain.llm import ENDPOINTLESS_PROVIDERS, BrainLLMError, release_codex_task
from human_ops.approval_flow import near_match_confirmation_decision
from human_ops.authorization import AUTHORIZATION_MODE_FULL, full_authorization_enabled

from .task_control import DEFAULT_TASK_BUDGET, TASK_CONTROL, TaskBudgetExhausted


MAX_RECENT_CONVERSATION_MESSAGES = 20
MAX_CONVERSATION_HISTORY_CHARS = 12_000
MAX_SUMMARY_HISTORY_CHARS = 4_000
MAX_TEMPORARY_SESSIONS = 64
_TEMPORARY_EXCHANGES_PER_SESSION = MAX_RECENT_CONVERSATION_MESSAGES // 2
# ponytail: Process-local bounded history is enough until temporary chats need cross-worker continuity.
_TEMPORARY_HISTORIES: OrderedDict[str, deque[tuple[int, str, str]]] = OrderedDict()


def with_observation_target_app(
    decision: BrainDecision,
    observation: dict[str, Any] | None,
) -> BrainDecision:
    if decision.kind != DecisionKind.PROPOSE_ACT:
        return decision
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    action_type = str(payload.get("action_type") or "").strip()
    if action_type not in {"click", "type_text", "key_press"}:
        return decision
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    if str(arguments.get("target_app") or "").strip():
        return decision
    data = observation if isinstance(observation, dict) else {}
    surface = data.get("surface") if isinstance(data.get("surface"), dict) else {}
    frame = data.get("frame") if isinstance(data.get("frame"), dict) else {}
    desktop = frame.get("desktop_context") if isinstance(frame.get("desktop_context"), dict) else {}
    focus = frame.get("focus_verification") if isinstance(frame.get("focus_verification"), dict) else {}
    surface_kind = str(surface.get("kind") or "").strip()
    target_app = str(
        surface.get("app")
        or focus.get("target_app")
        or focus.get("frontmost_app")
        or desktop.get("foreground_app")
        or desktop.get("frontmost_process")
        or ("WeChat" if surface_kind == "wechat_gui" else "")
        or ("Finder" if surface_kind == "desktop_gui" else "")
        or ""
    ).strip()
    if not target_app:
        return decision
    next_arguments = dict(arguments)
    next_arguments["target_app"] = target_app
    goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else None
    return BrainDecision.propose_act(action_type, next_arguments, goal=goal)


def _tail_messages(
    messages: list[dict[str, str]],
    *,
    max_chars: int,
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    remaining = max(0, max_chars)
    for message in reversed(messages):
        content = str(message.get("content") or "").strip()
        if remaining <= 0:
            break
        if not content:
            continue
        selected.append({"role": str(message.get("role") or ""), "content": content[:remaining]})
        remaining -= min(len(content), remaining)
    selected.reverse()
    return selected


def _trim_exchange(
    user_message: dict[str, str],
    assistant_message: dict[str, str],
    max_chars: int,
) -> tuple[dict[str, str], dict[str, str]] | None:
    if max_chars < 2:
        return None
    user_content = user_message["content"]
    assistant_content = assistant_message["content"]
    user_chars = min(len(user_content), max(1, max_chars // 2))
    assistant_chars = min(len(assistant_content), max_chars - user_chars)
    remaining = max_chars - user_chars - assistant_chars
    if remaining:
        extra_user = min(len(user_content) - user_chars, remaining)
        user_chars += extra_user
        remaining -= extra_user
        assistant_chars += min(len(assistant_content) - assistant_chars, remaining)
    return (
        {"role": "user", "content": user_content[:user_chars]},
        {"role": "assistant", "content": assistant_content[:assistant_chars]},
    )


def _tail_complete_exchanges(messages: list[dict[str, str]], *, max_chars: int) -> list[dict[str, str]]:
    exchanges: list[tuple[dict[str, str], dict[str, str]]] = []
    pending_user: dict[str, str] | None = None
    for message in messages:
        if message["role"] == "user":
            pending_user = message
        elif message["role"] == "assistant" and pending_user is not None:
            exchanges.append((pending_user, message))
            pending_user = None

    selected: list[tuple[dict[str, str], dict[str, str]]] = []
    remaining = max(0, max_chars)
    for user_message, assistant_message in reversed(exchanges[-_TEMPORARY_EXCHANGES_PER_SESSION:]):
        exchange_chars = len(user_message["content"]) + len(assistant_message["content"])
        if exchange_chars <= remaining:
            selected.append((user_message, assistant_message))
            remaining -= exchange_chars
            continue
        if not selected:
            trimmed = _trim_exchange(user_message, assistant_message, remaining)
            if trimmed is not None:
                selected.append(trimmed)
        break
    return [message for exchange in reversed(selected) for message in exchange]


def _bounded_conversation_history(messages: Any) -> list[dict[str, str]]:
    normalized = [
        {"role": str(message.get("role") or "").strip(), "content": str(message.get("content") or "").strip()}
        for message in (messages or ())
        if isinstance(message, dict)
        and str(message.get("role") or "").strip() in {"system", "user", "assistant"}
        and str(message.get("content") or "").strip()
    ]
    summaries = _tail_messages(
        [message for message in normalized if message["role"] == "system"],
        max_chars=MAX_SUMMARY_HISTORY_CHARS,
    )
    summary_chars = sum(len(message["content"]) for message in summaries)
    recent_messages = _tail_complete_exchanges(
        [message for message in normalized if message["role"] in {"user", "assistant"}],
        max_chars=MAX_CONVERSATION_HISTORY_CHARS - summary_chars,
    )
    return summaries + recent_messages


def _temporary_history(session_id: str) -> deque[tuple[int, str, str]]:
    history = _TEMPORARY_HISTORIES.get(session_id)
    if history is None:
        history = deque(maxlen=_TEMPORARY_EXCHANGES_PER_SESSION)
        _TEMPORARY_HISTORIES[session_id] = history
    _TEMPORARY_HISTORIES.move_to_end(session_id)
    while len(_TEMPORARY_HISTORIES) > MAX_TEMPORARY_SESSIONS:
        _TEMPORARY_HISTORIES.popitem(last=False)
    return history


def _temporary_conversation_history(session_id: str) -> list[dict[str, str]]:
    messages = [
        message
        for _assistant_turn, user_text, assistant_text in _temporary_history(session_id)
        for message in (
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        )
    ]
    return _bounded_conversation_history(messages)


def _truncate_temporary_history(session_id: str, assistant_turn: int) -> None:
    history = _temporary_history(session_id)
    retained = [exchange for exchange in history if exchange[0] < assistant_turn]
    history.clear()
    history.extend(retained)


def _append_temporary_exchange(
    session_id: str,
    *,
    user_text: str,
    assistant_text: str,
    assistant_turn: int = 0,
) -> None:
    history = _temporary_history(session_id)
    next_turn = assistant_turn or (history[-1][0] + 1 if history else 1)
    history.append(
        (
            next_turn,
            str(user_text or "")[:MAX_CONVERSATION_HISTORY_CHARS],
            str(assistant_text or "")[:MAX_CONVERSATION_HISTORY_CHARS],
        )
    )


@dataclass(frozen=True)
class ChatStreamFlowDependencies:
    normalize_private_config: Callable[[], dict[str, Any]]
    normalize_topic_id: Callable[[str], str]
    normalize_provider: Callable[[Any], str]
    topic_store: Any
    sse: Callable[[str, dict[str, Any]], str]
    run_brain_turn: Callable[..., Awaitable[Any]]
    decision_from_completion: Callable[[Any], BrainDecision]
    sanitize_brain_error: Callable[[Exception, dict[str, Any]], str]
    fallback_after_observe_brain_error: Callable[[str, bool], str]
    observe_decision_requests_click: Callable[[BrainDecision], bool]
    click_coordinate_clarification_text: Callable[[str], str]
    coerce_decision_for_human_ops: Callable[[str, BrainDecision], BrainDecision]
    decision_kind: Callable[[BrainDecision], DecisionKind]
    decision_goal: Callable[[BrainDecision], dict[str, Any]]
    simple_human_action_support: Callable[[BrainDecision], tuple[bool, str]]
    blocked_react_decision: Callable[[str, BrainDecision | None], BrainDecision]
    unsupported_simple_action_prompt: Callable[..., str]
    create_human_ops_act_proposal: Callable[..., tuple[str, Any]]
    proposal_event_payload: Callable[[str, Any], dict[str, Any]]
    perform_human_ops_observe: Callable[[BrainDecision, dict[str, Any]], Awaitable[dict[str, Any]]]
    has_partial_coordinate_pair: Callable[[str], bool]
    observation_has_reviewable_click_affordance: Callable[[dict[str, Any]], bool]
    react_followup_prompt: Callable[..., str]
    computer_use_context_text: Callable[[dict[str, Any] | None], str]
    goal_status: Callable[..., str]
    goal_is_terminal: Callable[[str], bool]
    pending_proposals: dict[str, dict[str, Any]] = field(default_factory=dict)
    normalize_observed_click_coordinates: Callable[[BrainDecision, dict[str, Any] | None], BrainDecision] = (
        lambda decision, observation: decision
    )
    create_human_ops_memory_proposal: Callable[..., tuple[str, Any]] = lambda *args, **kwargs: ("", None)
    relationship_memory_context: Callable[[str, dict[str, Any]], tuple[str, list[str]]] = (
        lambda user_text, memory_config: ("", [])
    )
    mark_memory_recalled: Callable[[str, str], None] = lambda memory_id, assistant_text: None
    build_memory_candidates: Callable[..., list[dict[str, Any]]] = lambda **kwargs: []
    consume_proactive_reply_context: Callable[[str], str] = lambda session_id: ""
    schedule_accessibility_index_refresh: Callable[[], bool] = lambda: False
    stream_authorized_proposal: Callable[[str], AsyncIterator[str]] | None = None


async def stream_chat_response(
    payload: dict[str, Any] | None,
    deps: ChatStreamFlowDependencies,
) -> AsyncIterator[str]:
    request_payload = payload if isinstance(payload, dict) else {}
    text = str(request_payload.get("text") or request_payload.get("message") or "").strip()
    session_id = deps.normalize_topic_id(str(request_payload.get("session_id") or "default"))
    requested_memory_mode = (
        "temporary" if str(request_payload.get("memory_mode") or "").strip().lower() == "temporary" else "persistent"
    )
    try:
        retry_from_assistant_turn = int(request_payload.get("retry_from_assistant_turn") or 0)
    except (TypeError, ValueError):
        retry_from_assistant_turn = 0
    turn_id = str(request_payload.get("task_id") or uuid4().hex).strip() or uuid4().hex
    TASK_CONTROL.start(
        turn_id,
        session_id,
        budget_total=request_payload.get("max_reasoning_steps", DEFAULT_TASK_BUDGET),
    )
    TASK_CONTROL.bind_current_task(turn_id)
    private_config = deps.normalize_private_config()
    raw_brain_config = private_config.get("brain", {}) if isinstance(private_config.get("brain"), dict) else {}
    human_ops_config = private_config.get("human_ops", {}) if isinstance(private_config.get("human_ops"), dict) else {}
    brain_config = dict(raw_brain_config)
    playwright_profile = str(human_ops_config.get("playwright_profile") or "").strip()
    if playwright_profile:
        brain_config["playwright_profile"] = playwright_profile
    memory_config = private_config.get("memory", {}) if isinstance(private_config.get("memory"), dict) else {}
    try:
        memory_retention_days = max(1, int(memory_config.get("retention_days") or 365))
    except (TypeError, ValueError):
        memory_retention_days = 365
    memory_mode = "temporary" if memory_config.get("conversation_saving") is False else requested_memory_mode
    chat_mode = str(request_payload.get("chat_mode") or "react").strip().lower()
    initial_prompt_profile = "chat" if chat_mode == "chat" else "agent"
    proactive_reply_context = ""
    if text:
        try:
            proactive_reply_context = deps.consume_proactive_reply_context(session_id)
        except Exception:
            proactive_reply_context = ""
    if memory_mode == "temporary":
        proactive_reply_context = ""
    model = str(request_payload.get("model") or brain_config.get("model_name") or "gpt-5.4")
    endpoint_configured = bool(str(brain_config.get("model_endpoint") or "").strip())
    provider_hint = deps.normalize_provider(brain_config.get("provider") or "openai_compatible")
    provider_ready = endpoint_configured or provider_hint in ENDPOINTLESS_PROVIDERS
    initial_provider = provider_hint if provider_ready else "local_placeholder"
    usage_totals: dict[str, int] = {}
    brain_call_succeeded = False

    async def run_brain(**kwargs: Any) -> Any:
        TASK_CONTROL.check(turn_id, next_action="Brain 调用")
        if not TASK_CONTROL.consume_budget(turn_id, "brain"):
            raise TaskBudgetExhausted("task step budget exhausted")
        completion = await deps.run_brain_turn(brain_config, task_id=turn_id, **kwargs)
        TASK_CONTROL.check(turn_id, next_action="处理 Brain 结果")
        usage = completion.usage if isinstance(getattr(completion, "usage", None), dict) else {}
        for key, value in usage.items():
            try:
                usage_totals[key] = usage_totals.get(key, 0) + max(0, int(value))
            except (TypeError, ValueError):
                continue
        return completion

    async def resolve_reply(
        conversation_history: list[dict[str, str]],
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        on_activity: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> tuple[str, str, str, BrainDecision]:
        nonlocal brain_call_succeeded
        if not provider_ready:
            reply = (
                "Neo Brain placeholder: 我已经收到你的消息。当前还没有配置 Brain 模型端点；"
                "请在设置页选择 provider 并填写模型端点。"
            )
            return (
                reply,
                initial_provider,
                model,
                BrainDecision.say(reply),
            )
        try:
            brain_kwargs: dict[str, Any] = {
                "user_text": text,
                "request_system_prompt": str(request_payload.get("system_prompt") or ""),
                "prompt_profile": initial_prompt_profile,
            }
            if conversation_history:
                brain_kwargs["conversation_history"] = conversation_history
            if on_delta is not None:
                brain_kwargs["on_delta"] = on_delta
            if on_activity is not None:
                brain_kwargs["on_activity"] = on_activity
            completion = await run_brain(**brain_kwargs)
            brain_call_succeeded = True
            decision = deps.decision_from_completion(completion)
            return str(completion.text or decision.summary), completion.provider, completion.model, decision
        except (BrainLLMError, TaskBudgetExhausted) as exc:
            detail = deps.sanitize_brain_error(exc, brain_config)
            reply = f"Brain 调用失败：{detail}"
            return reply, provider_hint, model, BrainDecision.say(reply)

    yield deps.sse(
        "meta",
        {
            "turn_id": turn_id,
            "session_id": session_id,
            "backend": "neo_aspect",
            "provider": initial_provider,
            "model": model,
            "memory_mode": memory_mode,
        },
    )
    yield deps.sse(
        "phase",
        {
            "category": "planning",
            "name": "neo_brain",
            "status": "running",
            "text": "Brain 正在理解目标并选择路线",
            "detail": f"provider: {initial_provider}",
        },
    )
    if retry_from_assistant_turn > 0 and memory_mode == "temporary":
        _truncate_temporary_history(session_id, retry_from_assistant_turn)
    elif retry_from_assistant_turn > 0:
        deps.topic_store.truncate_from_assistant_turn(session_id, retry_from_assistant_turn)
    due_memory_ids: list[str] = []
    if memory_mode == "temporary":
        conversation_history = _temporary_conversation_history(session_id)
    else:
        snapshot = deps.topic_store.get_context_snapshot(session_id)
        conversation_history = _bounded_conversation_history(snapshot.model_messages if snapshot is not None else ())
        if proactive_reply_context:
            conversation_history = [{"role": "system", "content": proactive_reply_context}, *conversation_history]
        if memory_config.get("long_term_enabled") is not False:
            try:
                memory_context, due_memory_ids = deps.relationship_memory_context(text, memory_config)
            except Exception:
                # Long-term memory is an enhancement: a damaged record must not block ordinary chat.
                memory_context, due_memory_ids = "", []
            if memory_context:
                conversation_history = [{"role": "system", "content": memory_context}, *conversation_history]
    streamed_visible = False
    if brain_config.get("streaming_enabled") is True and provider_ready:
        delta_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        async def queue_delta(delta: str) -> None:
            if delta:
                await delta_queue.put(("token", delta))

        async def queue_activity(activity: dict[str, Any]) -> None:
            if activity:
                normalized_activity = dict(activity)
                if not str(normalized_activity.get("category") or "").strip():
                    normalized_activity["category"] = (
                        "searching" if str(normalized_activity.get("phase") or "").strip() == "search" else "planning"
                    )
                await delta_queue.put(("phase", normalized_activity))

        reply_task = asyncio.create_task(resolve_reply(conversation_history, queue_delta, queue_activity))
        TASK_CONTROL.bind_task(turn_id, reply_task)
        delta_task: asyncio.Task[tuple[str, Any]] | None = None
        try:
            while not reply_task.done():
                delta_task = asyncio.create_task(delta_queue.get())
                TASK_CONTROL.bind_task(turn_id, delta_task)
                done, _pending = await asyncio.wait({reply_task, delta_task}, return_when=asyncio.FIRST_COMPLETED)
                if delta_task in done:
                    event_name, event_payload = delta_task.result()
                    if event_name == "token":
                        streamed_visible = True
                        event_payload = {"delta": event_payload}
                    yield deps.sse(event_name, event_payload)
                else:
                    delta_task.cancel()
                    await asyncio.gather(delta_task, return_exceptions=True)
            while not delta_queue.empty():
                event_name, event_payload = delta_queue.get_nowait()
                if event_name == "token":
                    streamed_visible = True
                    event_payload = {"delta": event_payload}
                yield deps.sse(event_name, event_payload)
            reply, provider, used_model, decision = await reply_task
        finally:
            pending_tasks = [
                task for task in (reply_task, delta_task) if task is not None and not task.done()
            ]
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
    else:
        reply, provider, used_model, decision = await resolve_reply(conversation_history)
    if brain_call_succeeded:
        for memory_id in due_memory_ids:
            try:
                deps.mark_memory_recalled(memory_id, str(reply or decision.summary))
            except Exception:
                continue
    correction_used = False
    react_trace: list[dict[str, Any]] = []
    last_observation: dict[str, Any] | None = None

    while True:
        TASK_CONTROL.check(turn_id, next_action="ReAct 后续步骤")
        decision = deps.coerce_decision_for_human_ops(text, decision)
        decision = with_observation_target_app(decision, last_observation)
        decision = deps.normalize_observed_click_coordinates(decision, last_observation)
        decision_kind = deps.decision_kind(decision)
        react_trace.append(
            {
                "kind": decision_kind.value,
                "summary": decision.summary,
                "goal": deps.decision_goal(decision),
            }
        )

        if decision_kind == DecisionKind.PROPOSE_REMEMBER:
            if memory_mode == "temporary":
                reply = "这次是临时对话，我不会读取或写入长期关系记忆。切回持久对话后再告诉我想记住什么吧。"
                decision = BrainDecision.say(reply)
                break
            if memory_config.get("long_term_enabled") is False:
                reply = "长期记忆现在是关闭的，所以这条内容没有进入记忆候选。你可以在设置页重新开启。"
                decision = BrainDecision.say(reply)
                break
            try:
                proposal_id, proposal = deps.create_human_ops_memory_proposal(
                    decision,
                    session_id=session_id,
                    user_text=text,
                    turn_id=turn_id,
                    origin="explicit",
                    retention_days=memory_retention_days,
                )
            except (TypeError, ValueError) as exc:
                reply = str(exc) or "这条内容不能进入长期记忆。"
                decision = BrainDecision.say(reply)
                break
            yield deps.sse(
                "phase",
                {
                    "category": "waiting_approval",
                    "name": "memory_review",
                    "status": "waiting",
                    "status_id": f"approval:{proposal_id}",
                    "text": "关系记忆正在等待你的批准",
                },
            )
            yield deps.sse("approval_required", deps.proposal_event_payload(proposal_id, proposal))
            return

        if decision_kind == DecisionKind.PROPOSE_ACT:
            supported_action, unsupported_action = deps.simple_human_action_support(decision)
            if not supported_action:
                if TASK_CONTROL.remaining_budget(turn_id) <= 0:
                    decision = deps.blocked_react_decision(text, decision)
                    reply = decision.summary
                    break
                yield deps.sse(
                    "phase",
                    {
                        "category": "planning",
                        "name": "brain_react",
                        "status": "running",
                        "text": "Brain 正在调整为可观察、可审批的动作",
                    },
                )
                followup_text = deps.unsupported_simple_action_prompt(
                    user_text=text,
                    decision=decision,
                    unsupported_action=unsupported_action,
                    remaining_budget=max(0, TASK_CONTROL.remaining_budget(turn_id) - 1),
                )
                try:
                    invalid_action_type = str(decision.payload.get("action_type") or "").strip()
                    invalid_scope = action_scope(invalid_action_type)
                    correction_profile = (
                        "file"
                        if invalid_scope == "file"
                        else "desktop"
                        if invalid_scope in {"desktop", "browser"}
                        else initial_prompt_profile
                    )
                    completion = await run_brain(
                        user_text=followup_text,
                        request_system_prompt=str(request_payload.get("system_prompt") or ""),
                        prompt_profile=correction_profile,
                    )
                    decision = deps.decision_from_completion(completion)
                    reply = str(completion.text or decision.summary)
                    provider = completion.provider
                    used_model = completion.model
                except (BrainLLMError, TaskBudgetExhausted) as exc:
                    detail = deps.sanitize_brain_error(exc, brain_config)
                    reply = f"Brain ReAct 调用失败：{detail}"
                    decision = BrainDecision.say(
                        reply,
                        goal={"objective": text, "status": "blocked", "missing": [detail], "next": "stop"},
                    )
                    break
                continue
            proposal_id, proposal = deps.create_human_ops_act_proposal(
                decision,
                session_id=session_id,
                user_text=text,
            )
            if proposal_id in deps.pending_proposals:
                deps.pending_proposals[proposal_id]["task_id"] = turn_id
            if getattr(proposal, "requires_review", True) is False and deps.stream_authorized_proposal is not None:
                if proposal_id in deps.pending_proposals:
                    deps.pending_proposals[proposal_id]["authorization_mode"] = "risk_classified_safe"
                async for event in deps.stream_authorized_proposal(proposal_id):
                    yield event
                return
            proposal_payload = proposal.payload if isinstance(getattr(proposal, "payload", None), dict) else {}
            proposal_action_type = str(proposal_payload.get("action_type") or "").strip()
            if (
                full_authorization_enabled(human_ops_config)
                and deps.stream_authorized_proposal is not None
                and proposal_action_type != "shell"
            ):
                if proposal_id in deps.pending_proposals:
                    deps.pending_proposals[proposal_id]["authorization_mode"] = AUTHORIZATION_MODE_FULL
                async for event in deps.stream_authorized_proposal(proposal_id):
                    yield event
                return
            yield deps.sse(
                "phase",
                {
                    "category": "waiting_approval",
                    "name": "human_ops_review",
                    "status": "waiting",
                    "status_id": f"approval:{proposal_id}",
                    "text": "Human Ops 正在等待你的批准",
                },
            )
            yield deps.sse("approval_required", deps.proposal_event_payload(proposal_id, proposal))
            return

        if decision_kind == DecisionKind.THINK:
            if TASK_CONTROL.remaining_budget(turn_id) <= 0:
                decision = deps.blocked_react_decision(text, decision)
                reply = decision.summary
                break
            yield deps.sse(
                "phase",
                {
                    "category": "planning",
                    "name": "brain_react",
                    "status": "running",
                    "text": "Brain 正在规划下一步",
                },
            )
            followup_text = deps.react_followup_prompt(
                user_text=text,
                decision=decision,
                remaining_budget=max(0, TASK_CONTROL.remaining_budget(turn_id) - 1),
            )
            try:
                completion = await run_brain(
                    user_text=followup_text,
                    request_system_prompt=str(request_payload.get("system_prompt") or ""),
                    prompt_profile=initial_prompt_profile,
                )
                decision = deps.decision_from_completion(completion)
                reply = str(completion.text or decision.summary)
                provider = completion.provider
                used_model = completion.model
            except (BrainLLMError, TaskBudgetExhausted) as exc:
                detail = deps.sanitize_brain_error(exc, brain_config)
                reply = f"Brain ReAct 调用失败：{detail}"
                decision = BrainDecision.say(
                    reply,
                    goal={"objective": text, "status": "blocked", "missing": [detail], "next": "stop"},
                )
                break
            continue

        if decision_kind == DecisionKind.OBSERVE:
            TASK_CONTROL.check(turn_id, next_action="Observe 分析")
            if not TASK_CONTROL.consume_budget(turn_id, "observe"):
                decision = deps.blocked_react_decision(text, decision)
                reply = decision.summary
                break
            yield deps.sse(
                "phase",
                {
                    "category": "observing",
                    "name": "human_ops_observe",
                    "status": "running",
                    "text": "Body 正在读取界面与系统状态",
                    "source": "body",
                    "task": str(
                        decision.payload.get("observe_prompt")
                        or decision.payload.get("question")
                        or decision.summary
                    ).strip(),
                },
            )
            try:
                observation = await deps.perform_human_ops_observe(decision, human_ops_config)
                TASK_CONTROL.check(turn_id, next_action="处理 Observe 结果")
            except Exception as exc:
                observation = {
                    "text": f"观察失败：{deps.sanitize_brain_error(exc, brain_config)}",
                    "observations": [],
                    "unknowns": [str(exc)],
                }
            observation = TASK_CONTROL.learn_observation_route(
                turn_id,
                observation,
            )
            last_observation = observation
            confirmation = near_match_confirmation_decision(
                observation,
                text,
            )
            if confirmation is not None:
                decision = confirmation
                reply = confirmation.summary
                break
            observation_frame = observation.get("frame") if isinstance(observation.get("frame"), dict) else {}
            brain_observed_image = str(observation.get("analysis_route") or "") == "brain"
            brain_image_data_url = (
                str(observation_frame.get("data_url") or "")
                if brain_observed_image
                else ""
            )
            observation_text = str(observation.get("text") or decision.summary or reply).strip() or "我看了一下屏幕。"
            coordinate_context = str(observation.get("coordinate_context") or "").strip()
            coordinate_status = observation.get("coordinate_status") if isinstance(observation.get("coordinate_status"), dict) else {}
            coordinate_incomplete = str(coordinate_status.get("status") or "") == "incomplete"
            require_coordinates = deps.observe_decision_requests_click(decision)
            if (
                require_coordinates
                and (coordinate_incomplete or deps.has_partial_coordinate_pair(observation_text))
                and not deps.observation_has_reviewable_click_affordance(observation)
            ):
                reply = deps.click_coordinate_clarification_text(observation_text)
                decision = BrainDecision.say(
                    reply,
                    goal={
                        "objective": text,
                        "status": "blocked",
                        "evidence": [observation_text],
                        "missing": ["完整的 x 和 y 屏幕坐标"],
                        "next": "observe",
                    },
                )
                break
            if TASK_CONTROL.remaining_budget(turn_id) <= 0:
                decision = deps.blocked_react_decision(text, decision)
                reply = decision.summary
                break
            followup_text = deps.react_followup_prompt(
                user_text=text,
                decision=decision,
                remaining_budget=max(0, TASK_CONTROL.remaining_budget(turn_id) - 1),
                observation_text=observation_text,
                coordinate_context=coordinate_context,
                computer_use_context=deps.computer_use_context_text(observation),
                brain_observed_image=bool(brain_image_data_url),
            )
            try:
                completion = await run_brain(
                    user_text=followup_text,
                    request_system_prompt=str(request_payload.get("system_prompt") or ""),
                    image_data_url=brain_image_data_url,
                    prompt_profile=initial_prompt_profile,
                )
                decision = deps.decision_from_completion(completion)
                if not str(completion.text or "").strip() and not str(decision.summary or "").strip():
                    reply = deps.fallback_after_observe_brain_error(observation_text, require_coordinates)
                    decision = BrainDecision.say(
                        reply,
                        goal={
                            "objective": text,
                            "status": "blocked",
                            "evidence": [observation_text],
                            "missing": ["Brain 后续整理结果"],
                            "next": "stop",
                        },
                    )
                    break
                reply = str(completion.text or decision.summary)
                provider = completion.provider
                used_model = completion.model
            except (BrainLLMError, TaskBudgetExhausted) as exc:
                if brain_image_data_url:
                    detail = deps.sanitize_brain_error(exc, brain_config)
                    reply = (
                        f"Brain 无法直接读取当前截图：{detail}。"
                        "请改用支持图片输入的 Brain 模型，或在 Human Ops 中启用独立 observe 模型。"
                    )
                    missing = ["支持图片输入的 Brain 模型或独立 observe 模型"]
                else:
                    reply = deps.fallback_after_observe_brain_error(observation_text, require_coordinates)
                    missing = ["Brain 后续整理结果"]
                decision = BrainDecision.say(
                    reply,
                    goal={
                        "objective": text,
                        "status": "blocked",
                        "evidence": [observation_text],
                        "missing": missing,
                        "next": "stop",
                    },
                )
                break
            except Exception as exc:
                if require_coordinates:
                    reply = deps.fallback_after_observe_brain_error(observation_text, True)
                else:
                    detail = deps.sanitize_brain_error(exc, brain_config)
                    reply = f"Brain 读取 observe 结果失败：{detail}"
                decision = BrainDecision.say(
                    reply,
                    goal={
                        "objective": text,
                        "status": "blocked",
                        "evidence": [observation_text],
                        "missing": ["Brain 后续整理结果"],
                        "next": "stop",
                    },
                )
                break
            continue

        status = deps.goal_status(decision, operation_request=False)
        if (
            decision_kind in {DecisionKind.SAY, DecisionKind.STOP}
            and status
            and not deps.goal_is_terminal(status)
        ):
            if correction_used or TASK_CONTROL.remaining_budget(turn_id) <= 0:
                decision = deps.blocked_react_decision(text, decision)
                reply = decision.summary
                break
            correction_used = True
            yield deps.sse(
                "phase",
                {
                    "category": "planning",
                    "name": "brain_react",
                    "status": "running",
                    "text": "Brain 正在补全尚未完成的任务路线",
                },
            )
            followup_text = deps.react_followup_prompt(
                user_text=text,
                decision=decision,
                remaining_budget=max(0, TASK_CONTROL.remaining_budget(turn_id) - 1),
                correction=True,
            )
            try:
                completion = await run_brain(
                    user_text=followup_text,
                    request_system_prompt=str(request_payload.get("system_prompt") or ""),
                    prompt_profile=initial_prompt_profile,
                )
                decision = deps.decision_from_completion(completion)
                reply = str(completion.text or decision.summary)
                provider = completion.provider
                used_model = completion.model
            except (BrainLLMError, TaskBudgetExhausted) as exc:
                detail = deps.sanitize_brain_error(exc, brain_config)
                reply = f"Brain ReAct 调用失败：{detail}"
                decision = BrainDecision.say(
                    reply,
                    goal={"objective": text, "status": "blocked", "missing": [detail], "next": "stop"},
                )
                break
            continue
        break

    await asyncio.sleep(0)
    final_text = str(reply or decision.summary).strip()
    if not streamed_visible:
        yield deps.sse("token", {"text": final_text})
        yield deps.sse("segment", {"text": final_text, "expression": "normal"})
        yield deps.sse("display_segment", {"text": final_text})
    if memory_mode == "temporary":
        _append_temporary_exchange(
            session_id,
            user_text=text,
            assistant_text=final_text,
            assistant_turn=retry_from_assistant_turn,
        )
    else:
        deps.topic_store.append_exchange(session_id, user_text=text, assistant_text=final_text)
    implicit_memory_candidates: list[dict[str, Any]] = []
    if memory_mode == "persistent" and memory_config.get("long_term_enabled") is not False:
        try:
            review_limit = max(1, int(memory_config.get("review_limit") or 20))
        except (TypeError, ValueError):
            review_limit = 20
        pending_memory_count = sum(
            1
            for record in deps.pending_proposals.values()
            if record.get("status") == "pending"
            and getattr(record.get("proposal"), "proposal_type", "") == "remember"
        )
        if pending_memory_count < review_limit:
            try:
                implicit_memory_candidates = deps.build_memory_candidates(
                    conversation_id=session_id,
                    turn_id=turn_id,
                    user_text=text,
                    assistant_text=final_text,
                    retention_days=memory_retention_days,
                )[:1]
            except Exception:
                implicit_memory_candidates = []
            allowed_candidates = []
            for candidate in implicit_memory_candidates:
                if not isinstance(candidate, dict):
                    continue
                kind = str(candidate.get("kind") or "general")
                if kind == "preference" and memory_config.get("preferences_enabled") is False:
                    continue
                if kind in {"person", "open_loop", "shared_moment"} and memory_config.get("relationship_enabled") is False:
                    continue
                allowed_candidates.append(candidate)
            implicit_memory_candidates = []
            for candidate in allowed_candidates:
                try:
                    deps.create_human_ops_memory_proposal(
                        candidate,
                        session_id=session_id,
                        user_text=text,
                        turn_id=turn_id,
                        origin="implicit",
                        retention_days=memory_retention_days,
                    )
                except Exception:
                    continue
                implicit_memory_candidates.append(candidate)
            if implicit_memory_candidates:
                yield deps.sse(
                    "phase",
                    {
                        "category": "planning",
                        "name": "memory_candidate",
                        "status": "completed",
                        "text": "生成了 1 条待审阅关系记忆，可在设置页查看",
                        "source": "memory",
                        "transient": True,
                    },
                )
    accessibility_refresh_scheduled = False
    if human_ops_config.get("accessibility", True) is not False:
        try:
            accessibility_refresh_scheduled = bool(deps.schedule_accessibility_index_refresh())
        except Exception:
            accessibility_refresh_scheduled = False
    done_payload = {
        "turn_id": turn_id,
        "session_id": session_id,
        "text": final_text,
        "provider": provider,
        "model": used_model,
        "decision": decision.to_dict(),
        "retry_from_assistant_turn": retry_from_assistant_turn or None,
        "memory_mode": memory_mode,
        "memory_candidate_count": len(implicit_memory_candidates),
        "accessibility_index_refresh_scheduled": accessibility_refresh_scheduled,
    }
    if last_observation is not None:
        done_payload["observation"] = last_observation
    if react_trace:
        done_payload["react_trace"] = react_trace
    if usage_totals:
        done_payload["usage"] = usage_totals
    await release_codex_task(turn_id)
    yield deps.sse("done", done_payload)
