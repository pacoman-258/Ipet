from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import uuid4

from brain.decisions import BrainDecision, DecisionKind
from brain.llm import PROVIDER_GOOGLE_AISTUDIO, BrainLLMError


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
    try:
        retry_from_assistant_turn = int(request_payload.get("retry_from_assistant_turn") or 0)
    except (TypeError, ValueError):
        retry_from_assistant_turn = 0
    turn_id = uuid4().hex
    private_config = deps.normalize_private_config()
    brain_config = private_config.get("brain", {}) if isinstance(private_config.get("brain"), dict) else {}
    human_ops_config = private_config.get("human_ops", {}) if isinstance(private_config.get("human_ops"), dict) else {}
    model = str(request_payload.get("model") or brain_config.get("model_name") or "gpt-5.4")
    endpoint_configured = bool(str(brain_config.get("model_endpoint") or "").strip())
    provider_hint = deps.normalize_provider(brain_config.get("provider") or "openai_compatible")
    provider_ready = endpoint_configured or provider_hint == PROVIDER_GOOGLE_AISTUDIO
    initial_provider = provider_hint if provider_ready else "local_placeholder"

    async def resolve_reply() -> tuple[str, str, str, BrainDecision]:
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
            completion = await deps.run_brain_turn(
                brain_config,
                user_text=text,
                request_system_prompt=str(request_payload.get("system_prompt") or ""),
            )
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
        },
    )
    yield deps.sse("phase", {"name": "neo_brain", "status": "running", "text": f"Brain provider: {initial_provider}"})
    if retry_from_assistant_turn > 0:
        deps.topic_store.truncate_from_assistant_turn(session_id, retry_from_assistant_turn)
    reply, provider, used_model, decision = await resolve_reply()
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
                    completion = await deps.run_brain_turn(
                        brain_config,
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
                completion = await deps.run_brain_turn(
                    brain_config,
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
                completion = await deps.run_brain_turn(
                    brain_config,
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
                completion = await deps.run_brain_turn(
                    brain_config,
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
    yield deps.sse("token", {"text": final_text})
    yield deps.sse("segment", {"text": final_text, "expression": "normal"})
    yield deps.sse("display_segment", {"text": final_text})
    deps.topic_store.append_exchange(session_id, user_text=text, assistant_text=final_text)
    done_payload = {
        "turn_id": turn_id,
        "session_id": session_id,
        "text": final_text,
        "provider": provider,
        "model": used_model,
        "decision": decision.to_dict(),
        "retry_from_assistant_turn": retry_from_assistant_turn or None,
    }
    if last_observation is not None:
        done_payload["observation"] = last_observation
    if react_trace:
        done_payload["react_trace"] = react_trace
    yield deps.sse("done", done_payload)
