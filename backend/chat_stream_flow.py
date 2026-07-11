from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import uuid4

from brain.decisions import BrainDecision, DecisionKind
from brain.llm import ENDPOINTLESS_PROVIDERS, BrainLLMError


MAX_RECENT_CONVERSATION_MESSAGES = 20
MAX_CONVERSATION_HISTORY_CHARS = 12_000
MAX_SUMMARY_HISTORY_CHARS = 4_000
MAX_TEMPORARY_SESSIONS = 64
_TEMPORARY_EXCHANGES_PER_SESSION = MAX_RECENT_CONVERSATION_MESSAGES // 2
# ponytail: Process-local bounded history is enough until temporary chats need cross-worker continuity.
_TEMPORARY_HISTORIES: OrderedDict[str, deque[tuple[int, str, str]]] = OrderedDict()


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
    fallback_after_observe_brain_error: Callable[[str, str], str]
    looks_like_click_request: Callable[[str], bool]
    click_coordinate_clarification_text: Callable[[str], str]
    looks_like_desktop_action_request: Callable[[str], bool]
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
    turn_id = uuid4().hex
    private_config = deps.normalize_private_config()
    brain_config = private_config.get("brain", {}) if isinstance(private_config.get("brain"), dict) else {}
    human_ops_config = private_config.get("human_ops", {}) if isinstance(private_config.get("human_ops"), dict) else {}
    memory_config = private_config.get("memory", {}) if isinstance(private_config.get("memory"), dict) else {}
    memory_mode = "temporary" if memory_config.get("conversation_saving") is False else requested_memory_mode
    model = str(request_payload.get("model") or brain_config.get("model_name") or "gpt-5.4")
    endpoint_configured = bool(str(brain_config.get("model_endpoint") or "").strip())
    provider_hint = deps.normalize_provider(brain_config.get("provider") or "openai_compatible")
    provider_ready = endpoint_configured or provider_hint in ENDPOINTLESS_PROVIDERS
    initial_provider = provider_hint if provider_ready else "local_placeholder"
    usage_totals: dict[str, int] = {}

    async def run_brain(**kwargs: Any) -> Any:
        completion = await deps.run_brain_turn(brain_config, **kwargs)
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
            }
            if conversation_history:
                brain_kwargs["conversation_history"] = conversation_history
            if on_delta is not None:
                brain_kwargs["on_delta"] = on_delta
            if on_activity is not None:
                brain_kwargs["on_activity"] = on_activity
            completion = await run_brain(**brain_kwargs)
            decision = deps.decision_from_completion(completion)
            return str(completion.text or decision.summary), completion.provider, completion.model, decision
        except BrainLLMError as exc:
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
    yield deps.sse("phase", {"name": "neo_brain", "status": "running", "text": f"Brain provider: {initial_provider}"})
    if retry_from_assistant_turn > 0 and memory_mode == "temporary":
        _truncate_temporary_history(session_id, retry_from_assistant_turn)
    elif retry_from_assistant_turn > 0:
        deps.topic_store.truncate_from_assistant_turn(session_id, retry_from_assistant_turn)
    if memory_mode == "temporary":
        conversation_history = _temporary_conversation_history(session_id)
    else:
        snapshot = deps.topic_store.get_context_snapshot(session_id)
        conversation_history = _bounded_conversation_history(snapshot.model_messages if snapshot is not None else ())
    streamed_visible = False
    if brain_config.get("streaming_enabled") is True and provider_ready:
        delta_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        async def queue_delta(delta: str) -> None:
            if delta:
                await delta_queue.put(("token", delta))

        async def queue_activity(activity: dict[str, Any]) -> None:
            if activity:
                await delta_queue.put(("phase", activity))

        reply_task = asyncio.create_task(resolve_reply(conversation_history, queue_delta, queue_activity))
        while not reply_task.done():
            delta_task = asyncio.create_task(delta_queue.get())
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
    else:
        reply, provider, used_model, decision = await resolve_reply(conversation_history)
    react_budget = 3
    correction_used = False
    react_trace: list[dict[str, Any]] = []
    last_observation: dict[str, Any] | None = None
    operation_request = deps.looks_like_desktop_action_request(text)

    while True:
        decision = deps.coerce_decision_for_human_ops(text, decision)
        decision_kind = deps.decision_kind(decision)
        react_trace.append(
            {
                "kind": decision_kind.value,
                "summary": decision.summary,
                "goal": deps.decision_goal(decision),
            }
        )

        if decision_kind == DecisionKind.PROPOSE_ACT:
            supported_action, unsupported_action = deps.simple_human_action_support(decision)
            if not supported_action:
                if react_budget <= 0:
                    decision = deps.blocked_react_decision(text, decision)
                    reply = decision.summary
                    break
                react_budget -= 1
                yield deps.sse(
                    "phase",
                    {
                        "name": "brain_react",
                        "status": "running",
                        "text": "Brain ReAct: unsupported action correction",
                    },
                )
                followup_text = deps.unsupported_simple_action_prompt(
                    user_text=text,
                    decision=decision,
                    unsupported_action=unsupported_action,
                    remaining_budget=react_budget,
                )
                try:
                    completion = await run_brain(
                        user_text=followup_text,
                        request_system_prompt=str(request_payload.get("system_prompt") or ""),
                    )
                    decision = deps.decision_from_completion(completion)
                    reply = str(completion.text or decision.summary)
                    provider = completion.provider
                    used_model = completion.model
                except BrainLLMError as exc:
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
            yield deps.sse("phase", {"name": "human_ops_review", "status": "waiting", "text": "Human Ops: waiting for review"})
            yield deps.sse("approval_required", deps.proposal_event_payload(proposal_id, proposal))
            return

        if decision_kind == DecisionKind.THINK:
            if react_budget <= 0:
                decision = deps.blocked_react_decision(text, decision)
                reply = decision.summary
                break
            yield deps.sse(
                "phase",
                {
                    "name": "brain_react",
                    "status": "running",
                    "text": "Brain ReAct: think",
                },
            )
            react_budget -= 1
            followup_text = deps.react_followup_prompt(
                user_text=text,
                decision=decision,
                remaining_budget=react_budget,
            )
            try:
                completion = await run_brain(
                    user_text=followup_text,
                    request_system_prompt=str(request_payload.get("system_prompt") or ""),
                )
                decision = deps.decision_from_completion(completion)
                reply = str(completion.text or decision.summary)
                provider = completion.provider
                used_model = completion.model
            except BrainLLMError as exc:
                detail = deps.sanitize_brain_error(exc, brain_config)
                reply = f"Brain ReAct 调用失败：{detail}"
                decision = BrainDecision.say(
                    reply,
                    goal={"objective": text, "status": "blocked", "missing": [detail], "next": "stop"},
                )
                break
            continue

        if decision_kind == DecisionKind.OBSERVE:
            yield deps.sse("phase", {"name": "human_ops_observe", "status": "running", "text": "Human Ops: observe"})
            try:
                observation = await deps.perform_human_ops_observe(decision, human_ops_config)
            except Exception as exc:
                observation = {
                    "text": f"观察失败：{deps.sanitize_brain_error(exc, brain_config)}",
                    "observations": [],
                    "unknowns": [str(exc)],
                }
            last_observation = observation
            observation_text = str(observation.get("text") or decision.summary or reply).strip() or "我看了一下屏幕。"
            coordinate_context = str(observation.get("coordinate_context") or "").strip()
            coordinate_status = observation.get("coordinate_status") if isinstance(observation.get("coordinate_status"), dict) else {}
            coordinate_incomplete = str(coordinate_status.get("status") or "") == "incomplete"
            if (
                deps.looks_like_click_request(text)
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
            if react_budget <= 0:
                decision = deps.blocked_react_decision(text, decision)
                reply = decision.summary
                break
            react_budget -= 1
            followup_text = deps.react_followup_prompt(
                user_text=text,
                decision=decision,
                remaining_budget=react_budget,
                observation_text=observation_text,
                coordinate_context=coordinate_context,
                computer_use_context=deps.computer_use_context_text(observation),
            )
            try:
                completion = await run_brain(
                    user_text=followup_text,
                    request_system_prompt=str(request_payload.get("system_prompt") or ""),
                )
                decision = deps.decision_from_completion(completion)
                reply = str(completion.text or decision.summary)
                provider = completion.provider
                used_model = completion.model
            except BrainLLMError:
                reply = deps.fallback_after_observe_brain_error(observation_text, text)
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
            except Exception as exc:
                if deps.looks_like_click_request(text):
                    reply = deps.fallback_after_observe_brain_error(observation_text, text)
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

        status = deps.goal_status(decision, operation_request=operation_request)
        if decision_kind in {DecisionKind.SAY, DecisionKind.STOP} and operation_request and not deps.goal_is_terminal(status):
            if correction_used or react_budget <= 0:
                decision = deps.blocked_react_decision(text, decision)
                reply = decision.summary
                break
            correction_used = True
            react_budget -= 1
            yield deps.sse(
                "phase",
                {
                    "name": "brain_react",
                    "status": "running",
                    "text": "Brain ReAct: unfinished goal correction",
                },
            )
            followup_text = deps.react_followup_prompt(
                user_text=text,
                decision=decision,
                remaining_budget=react_budget,
                correction=True,
            )
            try:
                completion = await run_brain(
                    user_text=followup_text,
                    request_system_prompt=str(request_payload.get("system_prompt") or ""),
                )
                decision = deps.decision_from_completion(completion)
                reply = str(completion.text or decision.summary)
                provider = completion.provider
                used_model = completion.model
            except BrainLLMError as exc:
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
    done_payload = {
        "turn_id": turn_id,
        "session_id": session_id,
        "text": final_text,
        "provider": provider,
        "model": used_model,
        "decision": decision.to_dict(),
        "retry_from_assistant_turn": retry_from_assistant_turn or None,
        "memory_mode": memory_mode,
    }
    if last_observation is not None:
        done_payload["observation"] = last_observation
    if react_trace:
        done_payload["react_trace"] = react_trace
    if usage_totals:
        done_payload["usage"] = usage_totals
    yield deps.sse("done", done_payload)
