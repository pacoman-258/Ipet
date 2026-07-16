from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from brain.decisions import BrainDecision, DecisionKind
from backend.task_control import TASK_CONTROL, TaskStopped

from .approvals import ReviewableProposal
from .filesystem_actions import filesystem_action_success_field


class HumanOpsProposalNotFound(LookupError):
    """Raised when a pending Human Ops proposal cannot be resolved."""


def execution_verification(
    proposal: ReviewableProposal,
    execution: dict[str, Any] | None,
) -> dict[str, Any]:
    data = execution if isinstance(execution, dict) else {}
    action_type = str(proposal.payload.get("action_type") or "").strip()
    arguments = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    success_field = {
        "launch_app": "launched",
        "click": "clicked",
        "type_text": "typed",
        "key_press": "pressed",
        "playwright": "playwright_done",
    }.get(action_type, "")
    if action_type.startswith("file_"):
        success_field = filesystem_action_success_field(action_type)
    checks = {
        success_field: bool(data.get(success_field)) if success_field else False,
        "focused": bool(data.get("focused")) if action_type in {"launch_app", "click", "type_text", "key_press"} else True,
    }
    contradictory = any(data.get(name) is False for name in checks)
    missing = [name for name, passed in checks.items() if name and not passed]
    key = str(arguments.get("key") or "enter").strip().lower()
    expected_text = str(arguments.get("expected_text") or "").strip()
    semantic_send_pending = action_type == "key_press" and key in {"enter", "return"} and bool(expected_text)
    if contradictory:
        status = "failed"
        reason = "action result contradicts the expected native state"
    elif missing:
        status = "insufficient"
        reason = f"missing native checks: {', '.join(missing)}"
    elif semantic_send_pending:
        status = "insufficient"
        reason = "native key delivery cannot confirm that the expected message appeared"
    else:
        status = "verified"
        reason = "action result and native focus state passed"
    return {
        "status": status,
        "method": "action_result_and_native_state",
        "action_type": action_type,
        "checks": checks,
        "frontmost_app": str(data.get("frontmost_app") or "").strip(),
        "reason": reason,
        "requires_visual": status == "insufficient",
    }


def structured_verification_observation(verification: dict[str, Any]) -> dict[str, Any]:
    checks = verification.get("checks") if isinstance(verification.get("checks"), dict) else {}
    passed = ", ".join(name for name, value in checks.items() if value) or "none"
    frontmost = str(verification.get("frontmost_app") or "").strip()
    frontmost_text = f"；前台应用：{frontmost}" if frontmost else ""
    return {
        "text": f"动作返回与平台原生状态验证通过：{passed}{frontmost_text}。本阶段未截图。",
        "analysis_route": "structured",
        "verification": dict(verification),
        "observations": [
            {
                "claim": "动作返回值和平台原生焦点状态已通过",
                "source": "human_ops_execution",
            }
        ],
        "unknowns": [],
    }


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
    normalize_observed_click_coordinates: Callable[[BrainDecision, dict[str, Any] | None], BrainDecision] = (
        lambda decision, observation: decision
    )
    perform_memory_operation: Callable[[ReviewableProposal], dict[str, Any]] = lambda proposal: {
        "ok": False,
        "error": "memory_store_unavailable",
    }
    record_memory_review_exchange: Callable[..., None] = lambda **kwargs: None


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
    if record.get("status") not in {"pending", "invalidated_by_stop"}:
        raise HumanOpsProposalNotFound("Human Ops proposal is no longer pending.")
    session_id = str(record.get("session_id") or "default")
    task_id = str(record.get("task_id") or proposal_key)
    label = deps.proposal_tool_label(proposal)

    async def event_stream() -> AsyncIterator[str]:
        TASK_CONTROL.bind_current_task(task_id)
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
            if proposal.proposal_type == "remember":
                final_text = "好，这条关系记忆没有保存。" + (f" 调整说明：{user_text}" if user_text else "")
                if record.get("origin") == "explicit":
                    deps.record_memory_review_exchange(
                        session_id=session_id,
                        user_text=str(record.get("user_text") or ""),
                        assistant_text=final_text,
                    )
            else:
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

        if record.get("status") == "invalidated_by_stop":
            raise TaskStopped()
        TASK_CONTROL.check(task_id, next_action="待审批动作")
        record["status"] = "approved"

        if proposal.proposal_type == "remember":
            yield deps.sse(
                "phase",
                {
                    "category": "acting",
                    "name": "memory_review_apply",
                    "status": "running",
                    "text": label,
                    "source": "memory",
                },
            )
            try:
                TASK_CONTROL.enter_atomic(task_id)
                try:
                    execution = deps.perform_memory_operation(proposal.approve())
                finally:
                    TASK_CONTROL.exit_atomic(task_id)
            except Exception as exc:
                execution = {"ok": False, "error": str(exc)}
            operation = str(proposal.payload.get("operation") or "save").strip()
            if execution.get("ok") is False:
                record["status"] = "failed"
                final_text = f"这条关系记忆没有处理成功：{str(execution.get('error') or '未知错误')[:180]}"
            elif operation == "forget":
                record["status"] = "executed"
                final_text = "已经忘记这条关系记忆，之后不会再检索到它。"
            elif operation == "resolve":
                record["status"] = "executed"
                final_text = "好，这件事已经标记为完成，我不会再追问啦。"
            elif operation == "snooze":
                record["status"] = "executed"
                final_text = "好，我先不追问，到了新的时间再轻轻问你一次。"
            elif execution.get("duplicate"):
                record["status"] = "executed"
                final_text = "这条关系记忆已经保存过啦，没有重复写入。"
            elif execution.get("saved"):
                record["status"] = "executed"
                final_text = "记住了。以后在相关话题里，我会自然地接上这件事。"
            else:
                record["status"] = "failed"
                final_text = "这条内容不适合进入长期记忆，所以没有保存。"
            if record.get("origin") == "explicit":
                deps.record_memory_review_exchange(
                    session_id=session_id,
                    user_text=str(record.get("user_text") or ""),
                    assistant_text=final_text,
                )
            yield deps.sse("display_segment", {"text": final_text})
            yield deps.sse(
                "done",
                {
                    "turn_id": proposal_key,
                    "proposal_id": proposal_key,
                    "session_id": session_id,
                    "text": final_text,
                    "approved": True,
                    "execution": execution,
                    "memory_mode": "persistent",
                },
            )
            return

        action_type = str(proposal.payload.get("action_type") or "").strip()
        phase_name = "human_ops_click" if action_type == "click" else (
            "human_ops_filesystem" if action_type.startswith("file_") else "human_ops_action"
        )
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
            TASK_CONTROL.enter_atomic(task_id)
            try:
                execution = await deps.perform_human_ops_action(proposal.approve())
            finally:
                TASK_CONTROL.exit_atomic(task_id)
            if action_type == "click":
                final_text = f"已执行点击：{label}。"
            elif action_type == "type_text":
                final_text = f"已执行输入：{label}。"
            elif action_type == "key_press":
                final_text = f"已执行按键：{label}。"
            elif action_type == "launch_app":
                final_text = f"已打开应用：{label}。"
            elif action_type == "playwright":
                final_text = f"已执行 Playwright 操作：{label}。"
            elif action_type.startswith("file_"):
                final_text = f"已执行文件动作：{label}。"
            else:
                final_text = f"已执行操作：{label}。"
            record["status"] = "executed"
        except Exception as exc:
            execution = {"ok": False, "error": str(exc)}
            final_text = f"操作执行失败：{str(exc)[:180]}"
            record["status"] = "failed"
        current_task = TASK_CONTROL.get(task_id)
        if current_task is not None and current_task.state != "running":
            TASK_CONTROL.mark_uncertain(task_id, f"原子动作可能已经发生：{label}")
            record["status"] = "stopped_after_atomic_action"
            return
        if record.get("status") == "executed":
            TASK_CONTROL.complete_step(task_id, f"已执行：{label}")
        yield deps.sse("display_segment", {"text": final_text})
        if record.get("status") == "executed" and deps.proposal_continue_after_approval(proposal):
            TASK_CONTROL.check(task_id, next_action="动作后结构化验证")
            private_config = deps.normalize_private_config()
            brain_config = private_config.get("brain", {}) if isinstance(private_config.get("brain"), dict) else {}
            human_ops_config = (
                private_config.get("human_ops", {}) if isinstance(private_config.get("human_ops"), dict) else {}
            )
            verification_task = deps.post_approval_observe_prompt(
                str(record.get("user_text") or ""),
                proposal,
            )
            verification = execution_verification(proposal, execution)
            yield deps.sse(
                "phase",
                {
                    "category": "verifying",
                    "name": "human_ops_structured_verify",
                    "status": "running",
                    "text": "Human Ops 正在检查动作返回与系统状态",
                    "task": verification_task,
                    "source": "human_ops",
                },
            )
            if verification["status"] == "failed":
                record["status"] = "verification_failed"
                final_text = f"动作返回后的结构化验证失败：{verification['reason']}。"
                yield deps.sse("display_segment", {"text": final_text})
                yield deps.sse(
                    "done",
                    {
                        "turn_id": proposal_key,
                        "proposal_id": proposal_key,
                        "session_id": session_id,
                        "text": final_text,
                        "approved": True,
                        "execution": execution,
                        "verification": verification,
                    },
                )
                return
            if verification["requires_visual"]:
                TASK_CONTROL.check(task_id, next_action="动作后视觉验证")
                yield deps.sse(
                    "phase",
                    {
                        "category": "verifying",
                        "name": "human_ops_observe",
                        "status": "running",
                        "text": "前置验证证据不足，Body 正在检查界面状态",
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
            else:
                observation = structured_verification_observation(verification)
            followup_text = deps.human_ops_continuation_prompt(
                user_text=str(record.get("user_text") or ""),
                proposal=proposal,
                execution=execution,
                observation=observation,
            )
            observation_frame = observation.get("frame") if isinstance(observation.get("frame"), dict) else {}
            followup_image_data_url = (
                str(observation_frame.get("data_url") or "")
                if str(observation.get("analysis_route") or "") == "brain"
                else ""
            )
            if followup_image_data_url:
                followup_text += (
                    "\n\nBody 刚捕获的动作后截图已附在当前消息中。请由 Brain 直接观察图片，"
                    "并把图片、执行结果、坐标上下文和结构化系统证据作为同一份上下文决定下一步。"
                )
            try:
                continuation_budget = 3
                correction_used = False
                next_decision: BrainDecision | None = None
                continuation_text = ""
                original_user_text = str(record.get("user_text") or "")
                while True:
                    brain_kwargs: dict[str, Any] = {"user_text": followup_text}
                    if followup_image_data_url:
                        brain_kwargs["image_data_url"] = followup_image_data_url
                    completion = await deps.run_brain_turn(brain_config, **brain_kwargs)
                    followup_image_data_url = ""
                    next_decision = deps.decision_from_completion(completion)
                    next_decision = deps.normalize_observed_click_coordinates(next_decision, observation)
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
                        if next_proposal_id in deps.pending_proposals:
                            deps.pending_proposals[next_proposal_id]["task_id"] = task_id
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
                        TASK_CONTROL.check(task_id, next_action="Observe 分析")
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
                        observation_frame = (
                            observation.get("frame") if isinstance(observation.get("frame"), dict) else {}
                        )
                        followup_image_data_url = (
                            str(observation_frame.get("data_url") or "")
                            if str(observation.get("analysis_route") or "") == "brain"
                            else ""
                        )
                        followup_text = deps.react_followup_prompt(
                            user_text=original_user_text,
                            decision=next_decision,
                            remaining_budget=continuation_budget,
                            observation_text=observation_text,
                            coordinate_context=str(observation.get("coordinate_context") or "").strip(),
                            computer_use_context=deps.computer_use_context_text(observation),
                            brain_observed_image=bool(followup_image_data_url),
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
