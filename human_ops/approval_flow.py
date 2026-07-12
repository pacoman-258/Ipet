from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from brain.decisions import BrainDecision, DecisionKind

from .approvals import ReviewableProposal


class HumanOpsProposalNotFound(LookupError):
    """Raised when a pending Human Ops proposal cannot be resolved."""


@dataclass(frozen=True)
class HumanOpsApprovalFlowDependencies:
    pending_proposals: dict[str, dict[str, Any]]
    sse: Callable[[str, dict[str, Any]], str]
    proposal_tool_label: Callable[[ReviewableProposal], str]
    perform_human_ops_action: Callable[[ReviewableProposal], Awaitable[dict[str, Any]]]
    proposal_continue_after_approval: Callable[[ReviewableProposal], bool]
    normalize_private_config: Callable[[], dict[str, Any]]
    perform_human_ops_observe: Callable[[BrainDecision, dict[str, Any]], Awaitable[dict[str, Any]]]
    post_approval_observe_prompt: Callable[[str, ReviewableProposal], str]
    sanitize_brain_error: Callable[[Exception, dict[str, Any]], str]
    human_ops_continuation_prompt: Callable[..., str]
    run_brain_turn: Callable[..., Awaitable[Any]]
    decision_from_completion: Callable[[Any], BrainDecision]
    decision_kind: Callable[[BrainDecision], DecisionKind]
    simple_human_action_support: Callable[[BrainDecision], tuple[bool, str]]
    blocked_react_decision: Callable[[str, BrainDecision | None], BrainDecision]
    unsupported_simple_action_prompt: Callable[..., str]
    with_inherited_enter_expected_text: Callable[..., BrainDecision]
    create_human_ops_act_proposal: Callable[..., tuple[str, ReviewableProposal]]
    proposal_event_payload: Callable[[str, ReviewableProposal], dict[str, Any]]
    react_followup_prompt: Callable[..., str]
    computer_use_context_text: Callable[[dict[str, Any] | None], str]
    goal_status: Callable[..., str]
    goal_is_terminal: Callable[[str], bool]
    request_native_approval: Callable[[ReviewableProposal], Awaitable[dict[str, Any]]] | None = None


def stream_human_ops_proposal_decision(
    proposal_id: str,
    payload: dict[str, Any] | None,
    deps: HumanOpsApprovalFlowDependencies,
) -> AsyncIterator[str]:
    decision_payload = payload if isinstance(payload, dict) else {}
    approved = bool(decision_payload.get("approved"))
    proposal_key = str(proposal_id or "").strip()
    record = deps.pending_proposals.get(proposal_key)
    if not record:
        raise HumanOpsProposalNotFound("Human Ops proposal not found.")
    proposal = record.get("proposal")
    if not isinstance(proposal, ReviewableProposal):
        raise HumanOpsProposalNotFound("Human Ops proposal not found.")
    session_id = str(record.get("session_id") or "default")
    label = deps.proposal_tool_label(proposal)

    async def event_stream() -> AsyncIterator[str]:
        yield deps.sse(
            "meta",
            {
                "turn_id": proposal_key,
                "proposal_id": proposal_key,
                "session_id": session_id,
                "backend": "neo_aspect",
                "provider": "human_ops",
                "model": "desktop",
            },
        )
        if not approved:
            record["status"] = "rejected"
            user_text = str(decision_payload.get("user_text") or "").strip()
            final_text = "已拒绝这次操作。" + (f" 调整说明：{user_text}" if user_text else "")
            yield deps.sse("display_segment", {"text": final_text})
            yield deps.sse(
                "done",
                {
                    "turn_id": proposal_key,
                    "proposal_id": proposal_key,
                    "session_id": session_id,
                    "text": final_text,
                    "approved": False,
                    "execution": None,
                },
            )
            return

        record["status"] = "approved"
        action_type = str(proposal.payload.get("action_type") or "").strip()
        phase_name = "human_ops_click" if action_type == "click" else "human_ops_action"
        yield deps.sse(
            "phase",
            {
                "category": "acting",
                "name": phase_name,
                "status": "running",
                "text": label,
                "source": "human_ops",
            },
        )
        try:
            execution = await deps.perform_human_ops_action(proposal.approve())
            if action_type == "click":
                final_text = f"已执行点击：{label}。"
            elif action_type == "type_text":
                final_text = f"已执行输入：{label}。"
            elif action_type == "key_press":
                final_text = f"已执行按键：{label}。"
            elif action_type == "launch_app":
                final_text = f"已打开应用：{label}。"
            else:
                final_text = f"已执行操作：{label}。"
            record["status"] = "executed"
        except Exception as exc:
            execution = {"ok": False, "error": str(exc)}
            final_text = f"操作执行失败：{str(exc)[:180]}"
            record["status"] = "failed"
        yield deps.sse("display_segment", {"text": final_text})
        if record.get("status") == "executed" and deps.proposal_continue_after_approval(proposal):
            private_config = deps.normalize_private_config()
            brain_config = private_config.get("brain", {}) if isinstance(private_config.get("brain"), dict) else {}
            human_ops_config = (
                private_config.get("human_ops", {}) if isinstance(private_config.get("human_ops"), dict) else {}
            )
            verification_task = deps.post_approval_observe_prompt(
                str(record.get("user_text") or ""),
                proposal,
            )
            yield deps.sse(
                "phase",
                {
                    "category": "verifying",
                    "name": "human_ops_observe",
                    "status": "running",
                    "text": "Body 正在检查动作后的界面状态",
                    "task": verification_task,
                    "source": "body",
                },
            )
            try:
                proposal_args = (
                    proposal.payload.get("arguments")
                    if isinstance(proposal.payload.get("arguments"), dict)
                    else {}
                )
                target_app = str(
                    proposal_args.get("target_app")
                    or (proposal_args.get("app") if action_type == "launch_app" else "")
                    or execution.get("target_app")
                    or execution.get("app")
                    or ""
                ).strip()
                observation = await deps.perform_human_ops_observe(
                    BrainDecision.observe(
                        "screen",
                        observe_prompt=verification_task,
                        target_app=target_app,
                    ),
                    human_ops_config,
                )
            except Exception as exc:
                observation = {
                    "text": f"观察失败：{deps.sanitize_brain_error(exc, brain_config)}",
                    "observations": [],
                    "unknowns": [str(exc)],
                }
            followup_text = deps.human_ops_continuation_prompt(
                user_text=str(record.get("user_text") or ""),
                proposal=proposal,
                execution=execution,
                observation=observation,
            )
            try:
                continuation_budget = 3
                correction_used = False
                next_decision: BrainDecision | None = None
                continuation_text = ""
                original_user_text = str(record.get("user_text") or "")
                while True:
                    completion = await deps.run_brain_turn(brain_config, user_text=followup_text)
                    next_decision = deps.decision_from_completion(completion)
                    continuation_text = str(getattr(completion, "text", None) or next_decision.summary).strip()
                    next_kind = deps.decision_kind(next_decision)
                    if next_kind == DecisionKind.PROPOSE_ACT:
                        next_decision = deps.with_inherited_enter_expected_text(
                            next_decision,
                            previous_proposal=proposal,
                            execution=execution,
                        )
                        supported_action, unsupported_action = deps.simple_human_action_support(next_decision)
                        if not supported_action:
                            if continuation_budget <= 0:
                                next_decision = deps.blocked_react_decision(original_user_text, next_decision)
                                continuation_text = next_decision.summary
                                break
                            continuation_budget -= 1
                            yield deps.sse(
                                "phase",
                                {
                                    "category": "planning",
                                    "name": "brain_react",
                                    "status": "running",
                                    "text": "Brain 正在调整后续动作",
                                },
                            )
                            followup_text = deps.unsupported_simple_action_prompt(
                                user_text=original_user_text,
                                decision=next_decision,
                                unsupported_action=unsupported_action,
                                remaining_budget=continuation_budget,
                            )
                            continue
                        next_proposal_id, next_proposal = deps.create_human_ops_act_proposal(
                            next_decision,
                            session_id=session_id,
                            user_text=original_user_text,
                        )
                        yield deps.sse(
                            "phase",
                            {
                                "category": "waiting_approval",
                                "name": "human_ops_review",
                                "status": "waiting",
                                "status_id": f"approval:{next_proposal_id}",
                                "text": "Human Ops 正在等待你的批准",
                                "source": "human_ops",
                            },
                        )
                        yield deps.sse("approval_required", deps.proposal_event_payload(next_proposal_id, next_proposal))
                        return
                    if next_kind == DecisionKind.THINK:
                        if continuation_budget <= 0:
                            next_decision = deps.blocked_react_decision(original_user_text, next_decision)
                            continuation_text = next_decision.summary
                            break
                        continuation_budget -= 1
                        yield deps.sse(
                            "phase",
                            {
                                "category": "planning",
                                "name": "brain_react",
                                "status": "running",
                                "text": "Brain 正在根据执行结果规划下一步",
                            },
                        )
                        followup_text = deps.react_followup_prompt(
                            user_text=original_user_text,
                            decision=next_decision,
                            remaining_budget=continuation_budget,
                        )
                        continue
                    if next_kind == DecisionKind.OBSERVE:
                        if continuation_budget <= 0:
                            next_decision = deps.blocked_react_decision(original_user_text, next_decision)
                            continuation_text = next_decision.summary
                            break
                        continuation_budget -= 1
                        yield deps.sse(
                            "phase",
                            {
                                "category": "verifying",
                                "name": "human_ops_observe",
                                "status": "running",
                                "text": "Body 正在验证当前界面是否符合预期",
                                "task": str(
                                    next_decision.payload.get("observe_prompt")
                                    or next_decision.payload.get("question")
                                    or next_decision.summary
                                ).strip(),
                                "source": "body",
                            },
                        )
                        try:
                            observation = await deps.perform_human_ops_observe(next_decision, human_ops_config)
                        except Exception as exc:
                            observation = {
                                "text": f"观察失败：{deps.sanitize_brain_error(exc, brain_config)}",
                                "observations": [],
                                "unknowns": [str(exc)],
                            }
                        observation_text = (
                            str(observation.get("text") or next_decision.summary or continuation_text).strip()
                            or "我看了一下屏幕。"
                        )
                        followup_text = deps.react_followup_prompt(
                            user_text=original_user_text,
                            decision=next_decision,
                            remaining_budget=continuation_budget,
                            observation_text=observation_text,
                            coordinate_context=str(observation.get("coordinate_context") or "").strip(),
                            computer_use_context=deps.computer_use_context_text(observation),
                        )
                        continue
                    status = deps.goal_status(next_decision, operation_request=True)
                    if next_kind in {DecisionKind.SAY, DecisionKind.STOP} and not deps.goal_is_terminal(status):
                        if correction_used or continuation_budget <= 0:
                            next_decision = deps.blocked_react_decision(original_user_text, next_decision)
                            continuation_text = next_decision.summary
                            break
                        correction_used = True
                        continuation_budget -= 1
                        yield deps.sse(
                            "phase",
                            {
                                "category": "planning",
                                "name": "brain_react",
                                "status": "running",
                                "text": "Brain 正在补全尚未完成的后续步骤",
                            },
                        )
                        followup_text = deps.react_followup_prompt(
                            user_text=original_user_text,
                            decision=next_decision,
                            remaining_budget=continuation_budget,
                            correction=True,
                        )
                        continue
                    break
                if next_decision is not None and continuation_text:
                    yield deps.sse("display_segment", {"text": continuation_text})
                    yield deps.sse(
                        "done",
                        {
                            "turn_id": proposal_key,
                            "proposal_id": proposal_key,
                            "session_id": session_id,
                            "text": continuation_text,
                            "approved": True,
                            "execution": execution,
                            "decision": next_decision.to_dict(),
                            "observation": observation,
                        },
                    )
                    return
            except Exception as exc:
                yield deps.sse(
                    "display_segment",
                    {"text": f"动作已执行，但继续推进任务失败：{deps.sanitize_brain_error(exc, brain_config)}"},
                )
        yield deps.sse(
            "done",
            {
                "turn_id": proposal_key,
                "proposal_id": proposal_key,
                "session_id": session_id,
                "text": final_text,
                "approved": True,
                "execution": execution,
            },
        )

    return event_stream()
