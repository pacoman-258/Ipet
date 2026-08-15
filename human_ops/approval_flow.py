from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from brain.contracts import action_scope
from brain.decisions import BrainDecision, DecisionKind
from brain.llm import release_codex_task
from backend.task_control import (
    DEFAULT_TASK_BUDGET,
    MAX_TASK_BUDGET,
    TASK_CONTROL,
    TaskStopped,
)
from body.macos_accessibility import _chat_header_matches_input_window

from .approvals import ReviewableProposal
from .authorization import AUTHORIZATION_MODE_FULL, full_authorization_enabled
from .filesystem_actions import filesystem_action_success_field


class HumanOpsProposalNotFound(LookupError):
    """Raised when a pending Human Ops proposal cannot be resolved."""


FULL_AUTH_ACTION_LIMIT = MAX_TASK_BUDGET


def near_match_confirmation_decision(
    observation: dict[str, Any] | None,
    user_text: object,
) -> BrainDecision | None:
    data = observation if isinstance(observation, dict) else {}
    ax_search = (
        data.get("ax_search")
        if isinstance(data.get("ax_search"), dict)
        else {}
    )
    if not ax_search:
        frame = data.get("frame") if isinstance(data.get("frame"), dict) else {}
        accessibility = (
            frame.get("accessibility")
            if isinstance(frame.get("accessibility"), dict)
            else {}
        )
        ax_search = (
            accessibility.get("search")
            if isinstance(accessibility.get("search"), dict)
            else {}
        )
    if (
        str(ax_search.get("insufficiency_reason") or "")
        != "near_match_requires_confirmation"
    ):
        return None
    raw_labels = (
        ax_search.get("near_match_labels")
        if isinstance(ax_search.get("near_match_labels"), list)
        else []
    )
    labels: list[str] = []
    for raw_label in raw_labels:
        label = " ".join(str(raw_label or "").replace("\x00", "").split())[:160]
        if label and label not in labels:
            labels.append(label)
        if len(labels) >= 3:
            break
    if not labels:
        return None
    candidates = "、".join(
        json.dumps(label, ensure_ascii=False)
        for label in labels
    )
    summary = (
        f"我找到名称近似但不完全一致的入口：{candidates}。"
        "为避免操作到错误对象，请确认是否就是你要找的目标。"
    )
    objective = str(user_text or "当前桌面操作目标").strip()
    return BrainDecision.say(
        summary,
        goal={
            "objective": objective,
            "status": "need_user",
            "evidence": [
                "macOS Accessibility 返回了结构化近似名称，且禁止像素猜测。"
            ],
            "missing": ["用户确认近似名称是否为目标"],
            "next": "confirm_near_match",
        },
    )


def _retry_coordinate(value: object) -> object:
    try:
        return round(float(value or 0), 1)
    except (TypeError, ValueError):
        return str(value or "").strip()


def _action_retry_key(payload: object) -> tuple[object, ...]:
    source = payload if isinstance(payload, dict) else {}
    action_type = str(source.get("action_type") or "").strip()
    if action_type not in {"click", "type_text"}:
        return ()
    arguments = (
        source.get("arguments")
        if isinstance(source.get("arguments"), dict)
        else {}
    )
    reference = (
        arguments.get("ax_ref")
        if isinstance(arguments.get("ax_ref"), dict)
        else {}
    )
    fingerprint = str(reference.get("fingerprint") or "").strip()
    if action_type == "type_text" and reference:
        bounds = (
            reference.get("bounds")
            if isinstance(reference.get("bounds"), dict)
            else {}
        )
        locator = (
            "ax_input",
            str(reference.get("app_id") or "").casefold(),
            str(reference.get("role") or ""),
            str(reference.get("input_kind") or ""),
            str(reference.get("identifier") or ""),
            str(reference.get("title") or ""),
            str(reference.get("description") or ""),
            str(reference.get("placeholder") or ""),
            str(reference.get("url") or ""),
            tuple(
                _retry_coordinate(bounds.get(key))
                for key in ("x", "y", "width", "height")
            ),
        )
    elif fingerprint:
        locator: tuple[object, ...] = ("ax", fingerprint)
    elif arguments.get("x") is not None and arguments.get("y") is not None:
        locator = (
            "point",
            str(arguments.get("x")),
            str(arguments.get("y")),
        )
    else:
        locator = (
            "label",
            str(
                arguments.get("label")
                or arguments.get("target")
                or ""
            ).strip(),
        )
    if not any(locator[1:]):
        return ()
    return (
        action_type,
        "".join(
            str(arguments.get("target_app") or "").casefold().split()
        ),
        locator,
        (
            str(arguments.get("text") or "")
            if action_type == "type_text"
            else ""
        ),
    )


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
        "shell": "shell_done",
    }.get(action_type, "")
    if action_type.startswith("file_"):
        success_field = filesystem_action_success_field(action_type)
    already_satisfied = bool(data.get("already_satisfied"))
    checks = {
        success_field: (
            bool(data.get(success_field)) or already_satisfied
            if success_field
            else False
        ),
        "focused": bool(data.get("focused")) if action_type in {"launch_app", "click", "type_text", "key_press"} else True,
    }
    semantic_ax_action = isinstance(arguments.get("ax_ref"), dict) and action_type in {"click", "type_text"}
    if semantic_ax_action:
        checks["ax_target_verified"] = bool(data.get("ax_target_verified"))
        checks["ax_action_performed"] = (
            bool(data.get("ax_action_performed")) or already_satisfied
        )
    no_op_false_fields = (
        {success_field, "ax_action_performed"}
        if already_satisfied
        else set()
    )
    contradictory = any(
        data.get(name) is False
        for name in checks
        if name not in no_op_false_fields
    )
    missing = [name for name, passed in checks.items() if name and not passed]
    key = str(arguments.get("key") or "enter").strip().lower()
    expected_text = str(arguments.get("expected_text") or "").strip()
    semantic_send_pending = action_type == "key_press" and key in {"enter", "return"} and bool(expected_text)
    text_outcome_pending = action_type == "type_text" and not bool(
        data.get("postcondition_verified")
    )
    click_outcome_pending = action_type == "click" and not bool(data.get("postcondition_verified"))
    if contradictory:
        status = "failed"
        reason = "action result contradicts the expected native state"
    elif missing:
        status = "insufficient"
        reason = f"missing native checks: {', '.join(missing)}"
    elif semantic_send_pending:
        status = "insufficient"
        reason = "native key delivery cannot confirm that the expected message appeared"
    elif text_outcome_pending:
        status = "insufficient"
        reason = "native text delivery cannot confirm that the expected draft appeared in the intended input"
    elif click_outcome_pending:
        status = "insufficient"
        reason = "native click delivery cannot confirm the intended post-action interface state"
    else:
        status = "verified"
        reason = "action result and native focus state passed"
    return {
        "status": status,
        "method": (
            "macos_accessibility_action_and_native_state"
            if semantic_ax_action
            else "action_result_and_native_state"
        ),
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


def _rounded_observation_number(value: object) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def observation_confirms_expected_draft(
    observation: dict[str, Any] | None,
    expected_text: object,
    *,
    expected_chat: object = "",
    input_ax_ref: object = None,
) -> bool:
    data = observation if isinstance(observation, dict) else {}
    expected = "".join(str(expected_text or "").casefold().split())
    expected_chat_key = "".join(str(expected_chat or "").casefold().split())
    if not expected:
        return False

    frame = data.get("frame") if isinstance(data.get("frame"), dict) else {}
    accessibility = (
        frame.get("accessibility")
        if isinstance(frame.get("accessibility"), dict)
        else {}
    )
    elements = (
        accessibility.get("elements")
        if isinstance(accessibility.get("elements"), list)
        else []
    )
    editable_roles = {"AXTextField", "AXTextArea"}
    reviewed_input = input_ax_ref if isinstance(input_ax_ref, dict) else {}
    if expected_chat_key and not reviewed_input:
        return False
    reviewed_app_id = str(reviewed_input.get("app_id") or "").strip()
    observed_app = (
        accessibility.get("app")
        if isinstance(accessibility.get("app"), dict)
        else {}
    )
    observed_app_id = str(observed_app.get("app_id") or "").strip()
    if (
        reviewed_app_id
        and observed_app_id
        and reviewed_app_id != observed_app_id
    ):
        return False
    matching_inputs: list[dict[str, Any]] = []
    for element in elements:
        if (
            not isinstance(element, dict)
            or element.get("protected")
            or str(element.get("role") or "") not in editable_roles
        ):
            continue
        if reviewed_input:
            if str(element.get("role") or "") != str(reviewed_input.get("role") or ""):
                continue
            expected_identifier = str(reviewed_input.get("identifier") or "")
            if expected_identifier and str(element.get("identifier") or "") != expected_identifier:
                continue
            reviewed_path = reviewed_input.get("path")
            element_ref = (
                element.get("ax_ref")
                if isinstance(element.get("ax_ref"), dict)
                else {}
            )
            element_app_id = str(element_ref.get("app_id") or "").strip()
            if (
                reviewed_app_id
                and element_app_id
                and element_app_id != reviewed_app_id
            ):
                continue
            element_path = element.get("tree_path") or element_ref.get("path")
            if isinstance(reviewed_path, list) and list(element_path or []) != reviewed_path:
                continue
        value = "".join(str(element.get("value") or "").casefold().split())
        if value and expected == value:
            matching_inputs.append(element)

    observations = (
        data.get("observations")
        if isinstance(data.get("observations"), list)
        else []
    )
    evidence_items: list[str] = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        evidence = " ".join(
            str(item.get(key) or "")
            for key in ("claim", "text", "description", "value")
        )
        evidence_items.append("".join(evidence.casefold().split()))

    if not expected_chat_key:
        return bool(
            matching_inputs
            or any(expected in evidence for evidence in evidence_items)
        )

    chat_context = (
        data.get("chat_context")
        if isinstance(data.get("chat_context"), dict)
        else {}
    )
    chat_context_matches = (
        "".join(str(chat_context.get("contact") or "").casefold().split())
        == expected_chat_key
    )
    if chat_context_matches and (
        matching_inputs
        or any(expected in evidence for evidence in evidence_items)
    ):
        return True

    if any(
        expected in evidence and expected_chat_key in evidence
        for evidence in evidence_items
    ):
        return True

    return any(
        _chat_header_matches_input_window(elements, input_element, str(expected_chat))
        for input_element in matching_inputs
    )


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
    notify_human_ops_action: Callable[..., Awaitable[dict[str, Any]]] | None = None
    normalize_observed_click_coordinates: Callable[[BrainDecision, dict[str, Any] | None], BrainDecision] = (
        lambda decision, observation: decision
    )
    perform_memory_operation: Callable[[ReviewableProposal], dict[str, Any]] = lambda proposal: {
        "ok": False,
        "error": "memory_store_unavailable",
    }
    record_memory_review_exchange: Callable[..., None] = lambda **kwargs: None
    schedule_accessibility_index_refresh: Callable[[], bool] = lambda: False


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
    authorization_mode = str(record.get("authorization_mode") or "").strip().lower()
    full_auto_authorized = (
        proposal.proposal_type == "act"
        and authorization_mode == AUTHORIZATION_MODE_FULL
    )
    risk_auto_authorized = (
        proposal.proposal_type == "act"
        and authorization_mode == "risk_classified_safe"
        and not proposal.requires_review
    )
    auto_authorized = full_auto_authorized or risk_auto_authorized
    authorization_payload = {
        "auto_authorized": auto_authorized,
        "authorization_mode": authorization_mode if auto_authorized else "review",
    }

    async def event_stream() -> AsyncIterator[str]:
        async def done_event(data: dict[str, Any]) -> str:
            result = dict(data)
            try:
                result["accessibility_index_refresh_scheduled"] = bool(
                    deps.schedule_accessibility_index_refresh()
                )
            except Exception:
                result["accessibility_index_refresh_scheduled"] = False
            await release_codex_task(task_id)
            return deps.sse("done", result)

        if TASK_CONTROL.get(task_id) is None:
            TASK_CONTROL.start(task_id, session_id, budget_total=DEFAULT_TASK_BUDGET)
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
                **authorization_payload,
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
            yield await done_event(
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
        TASK_CONTROL.check(task_id, next_action="待执行动作" if auto_authorized else "待审批动作")
        if full_auto_authorized:
            record["authorized_by"] = "full_authorization"
        elif risk_auto_authorized:
            record["authorized_by"] = "local_risk_policy"
            record["status"] = "auto_authorized"
        else:
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
            yield await done_event(
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

        if full_auto_authorized:
            try:
                actions_executed = int(record.get("actions_executed") or 0)
            except (TypeError, ValueError):
                actions_executed = 0
            actions_executed = max(0, actions_executed)
            if actions_executed >= FULL_AUTH_ACTION_LIMIT:
                record["status"] = "action_limit_reached"
                final_text = (
                    f"完全授权连续动作已达到 {FULL_AUTH_ACTION_LIMIT} 步安全上限，"
                    "本次没有继续执行。请检查当前界面后再发起新的任务。"
                )
                yield deps.sse(
                    "phase",
                    {
                        "category": "blocked",
                        "name": "human_ops_action_limit",
                        "status": "blocked",
                        "text": final_text,
                        "source": "human_ops",
                    },
                )
                yield deps.sse("display_segment", {"text": final_text})
                yield await done_event(
                    {
                        "turn_id": proposal_key,
                        "proposal_id": proposal_key,
                        "session_id": session_id,
                        "text": final_text,
                        "approved": True,
                        "auto_authorized": True,
                        "authorization_mode": AUTHORIZATION_MODE_FULL,
                        "execution": {
                            "ok": False,
                            "stage": "action_limit",
                            "action_limit": FULL_AUTH_ACTION_LIMIT,
                        },
                    },
                )
                return
            record["actions_executed"] = actions_executed + 1
            yield deps.sse(
                "phase",
                {
                    "category": "acting",
                    "name": "human_ops_action_notice",
                    "status": "running",
                    "text": f"完全授权已放行，正在通知：{label}",
                    "source": "human_ops",
                },
            )
            try:
                if deps.notify_human_ops_action is None:
                    raise RuntimeError("完全授权通知通道不可用")
                notification = await deps.notify_human_ops_action(proposal, task_id=task_id)
                if notification.get("notified") is not True:
                    raise RuntimeError("完全授权通知未确认送达")
                record["notification"] = dict(notification)
            except Exception as exc:
                execution = {"ok": False, "error": str(exc), "stage": "action_notification"}
                final_text = f"完全授权操作未执行：通知失败（{str(exc)[:160]}）。"
                record["status"] = "notification_failed"
                yield deps.sse("display_segment", {"text": final_text})
                yield await done_event(
                    {
                        "turn_id": proposal_key,
                        "proposal_id": proposal_key,
                        "session_id": session_id,
                        "text": final_text,
                        "approved": True,
                        "auto_authorized": True,
                        "authorization_mode": AUTHORIZATION_MODE_FULL,
                        "execution": execution,
                    },
                )
                return
            try:
                TASK_CONTROL.check(task_id, next_action=f"通知后的动作：{label}")
            except TaskStopped:
                record["status"] = "invalidated_by_stop"
                raise
            record["status"] = "auto_authorized"

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
            if bool(execution.get("already_satisfied")):
                final_text = f"目标已处于预期状态，无需重复操作：{label}。"
            elif action_type == "click":
                final_text = f"已执行点击：{label}。"
            elif action_type == "type_text":
                final_text = f"已执行输入：{label}。"
            elif action_type == "key_press":
                final_text = f"已执行按键：{label}。"
            elif action_type == "launch_app":
                final_text = f"已打开应用：{label}。"
            elif action_type == "playwright":
                final_text = f"已执行 Playwright 操作：{label}。"
            elif action_type == "shell":
                exit_code = int(execution.get("exit_code") or 0)
                final_text = f"已执行 Shell 命令（退出码 {exit_code}）：{label}。"
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
            proposal_arguments = (
                proposal.payload.get("arguments")
                if isinstance(proposal.payload.get("arguments"), dict)
                else {}
            )
            if action_type == "type_text":
                target_app = str(proposal_arguments.get("target_app") or "").strip()
                target_key = "".join(target_app.casefold().split())
                intended_chat = str(
                    proposal_arguments.get("intended_chat") or ""
                ).strip()
                input_ref = (
                    proposal_arguments.get("input_ax_ref")
                    if isinstance(proposal_arguments.get("input_ax_ref"), dict)
                    else proposal_arguments.get("ax_ref")
                    if isinstance(proposal_arguments.get("ax_ref"), dict)
                    else {}
                )
                expected_text = str(proposal_arguments.get("text") or "")
                if (
                    target_key
                    in {"qq", "腾讯qq", "wechat", "微信", "微信app"}
                    and intended_chat
                    and input_ref
                    and expected_text.strip()
                ):
                    record["chat_send_transaction"] = {
                        "target_app": target_app,
                        "intended_chat": intended_chat,
                        "input_ax_ref": deepcopy(input_ref),
                        "expected_text": expected_text,
                    }
                else:
                    record.pop("chat_send_transaction", None)
            elif action_type == "key_press":
                key = str(proposal_arguments.get("key") or "enter").strip().lower()
                if key in {"enter", "return"}:
                    record.pop("chat_send_transaction", None)
        yield deps.sse("display_segment", {"text": final_text})
        if record.get("status") == "executed":
            continue_after_approval = deps.proposal_continue_after_approval(proposal)
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
                yield await done_event(
                    {
                        "turn_id": proposal_key,
                        "proposal_id": proposal_key,
                        "session_id": session_id,
                        "text": final_text,
                        "approved": True,
                        "execution": execution,
                        "verification": verification,
                        **authorization_payload,
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
                if not TASK_CONTROL.consume_budget(task_id, "observe:verification"):
                    observation = {
                        "text": "动作后视觉验证未执行：本任务统一步骤预算已耗尽。",
                        "observations": [],
                        "unknowns": ["task_budget_exhausted"],
                    }
                else:
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
            observation = TASK_CONTROL.learn_observation_route(
                task_id,
                observation,
            )
            record["verification"] = deepcopy(verification)
            record["verification_observation"] = deepcopy(observation)
            confirmation = near_match_confirmation_decision(
                observation,
                record.get("user_text"),
            )
            if confirmation is not None:
                continuation_text = confirmation.summary
                record["status"] = "needs_user_confirmation"
                yield deps.sse(
                    "display_segment",
                    {"text": continuation_text},
                )
                yield await done_event(
                    {
                        "turn_id": proposal_key,
                        "proposal_id": proposal_key,
                        "session_id": session_id,
                        "text": continuation_text,
                        "approved": True,
                        "execution": execution,
                        "verification": verification,
                        "decision": confirmation.to_dict(),
                        "observation": observation,
                        **authorization_payload,
                    },
                )
                return
            if not continue_after_approval:
                observation_text = str(observation.get("text") or "").strip()
                if verification["status"] == "verified":
                    final_text = observation_text or "动作已经执行，并通过结构化验证。"
                else:
                    final_text = (
                        "动作已经执行；原生证据不足，已完成动作后观察。"
                        + (f"{observation_text}" if observation_text else "")
                    )
                yield deps.sse("display_segment", {"text": final_text})
                yield await done_event(
                    {
                        "turn_id": proposal_key,
                        "proposal_id": proposal_key,
                        "session_id": session_id,
                        "text": final_text,
                        "approved": True,
                        "execution": execution,
                        "verification": verification,
                        "observation": observation,
                        **authorization_payload,
                    },
                )
                return
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
                correction_used = False
                next_decision: BrainDecision | None = None
                continuation_text = ""
                original_user_text = str(record.get("user_text") or "")
                continuation_profile = "agent"
                while True:
                    if not TASK_CONTROL.consume_budget(task_id, "brain:post_action"):
                        next_decision = deps.blocked_react_decision(
                            original_user_text,
                            next_decision,
                        )
                        continuation_text = next_decision.summary
                        break
                    brain_kwargs: dict[str, Any] = {"user_text": followup_text}
                    brain_kwargs["task_id"] = task_id
                    if followup_image_data_url:
                        brain_kwargs["image_data_url"] = followup_image_data_url
                    brain_kwargs["prompt_profile"] = continuation_profile
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
                            observation=observation,
                            chat_send_transaction=(
                                record.get("chat_send_transaction")
                                if isinstance(
                                    record.get("chat_send_transaction"),
                                    dict,
                                )
                                else None
                            ),
                        )
                        next_payload = (
                            next_decision.payload
                            if isinstance(next_decision.payload, dict)
                            else {}
                        )
                        next_arguments = (
                            next_payload.get("arguments")
                            if isinstance(next_payload.get("arguments"), dict)
                            else {}
                        )
                        previous_action = str(
                            proposal.payload.get("action_type") or ""
                        ).strip()
                        proposed_action = str(
                            next_payload.get("action_type") or ""
                        ).strip()
                        proposed_key = str(
                            next_arguments.get("key") or ""
                        ).strip().lower()
                        previous_arguments = (
                            proposal.payload.get("arguments")
                            if isinstance(proposal.payload.get("arguments"), dict)
                            else {}
                        )
                        current_retry_key = _action_retry_key(
                            proposal.payload
                        )
                        next_retry_key = _action_retry_key(
                            next_payload
                        )
                        if (
                            current_retry_key
                            and next_retry_key == current_retry_key
                            and not bool(
                                execution.get(
                                    "postcondition_verified"
                                )
                            )
                        ):
                            if TASK_CONTROL.remaining_budget(task_id) <= 0:
                                next_decision = deps.blocked_react_decision(
                                    original_user_text,
                                    next_decision,
                                )
                                continuation_text = next_decision.summary
                                break
                            yield deps.sse(
                                "phase",
                                {
                                    "category": "planning",
                                    "name": "brain_react",
                                    "status": "running",
                                    "text": (
                                        "上一动作未验证生效，Brain 正在避免"
                                        "重复同一目标"
                                    ),
                                },
                            )
                            followup_text = (
                                "安全约束：上一动作只证明本机事件已投递，"
                                "动作后的界面没有证明目标状态发生变化；"
                                "当前提议又是完全相同的点击或输入目标。"
                                "不得原样重复该动作。请先改变可能阻挡目标的"
                                "界面状态（例如关闭浮层），改用重新观察后得到的"
                                "其他语义入口，或在条件不足时返回 blocked。"
                            )
                            continue
                        chat_send_transaction = (
                            record.get("chat_send_transaction")
                            if isinstance(
                                record.get("chat_send_transaction"),
                                dict,
                            )
                            else {}
                        )
                        expected_draft = str(
                            chat_send_transaction.get("expected_text")
                            or previous_arguments.get("text")
                            or ""
                        )
                        expected_chat = str(
                            next_arguments.get("intended_chat")
                            or previous_arguments.get("intended_chat")
                            or ""
                        )
                        expected_input_ref = (
                            next_arguments.get("input_ax_ref")
                            if isinstance(next_arguments.get("input_ax_ref"), dict)
                            else previous_arguments.get("ax_ref")
                            if isinstance(previous_arguments.get("ax_ref"), dict)
                            else {}
                        )
                        if (
                            (
                                previous_action == "type_text"
                                or bool(chat_send_transaction)
                            )
                            and proposed_action == "key_press"
                            and proposed_key in {"enter", "return"}
                            and expected_draft
                            and not bool(execution.get("postcondition_verified"))
                            and not observation_confirms_expected_draft(
                                observation,
                                expected_draft,
                                expected_chat=expected_chat,
                                input_ax_ref=expected_input_ref,
                            )
                        ):
                            if TASK_CONTROL.remaining_budget(task_id) <= 0:
                                next_decision = deps.blocked_react_decision(
                                    original_user_text,
                                    next_decision,
                                )
                                continuation_text = next_decision.summary
                                break
                            yield deps.sse(
                                "phase",
                                {
                                    "category": "verifying",
                                    "name": "brain_react",
                                    "status": "running",
                                    "text": "草稿内容尚未验证，Brain 正在补充发送前证据",
                                },
                            )
                            target_app = str(
                                next_arguments.get("target_app")
                                or previous_arguments.get("target_app")
                                or execution.get("target_app")
                                or ""
                            ).strip()
                            followup_text = (
                                "安全约束：上一项只证明输入事件已投递，当前观察没有证明完整草稿"
                                f"“{expected_draft}”出现在目标输入框中，因此不能提交 Enter/Return。"
                                f"还必须同时证明当前会话是“{expected_chat}”。"
                                "请先返回 observe，在同一界面重新读取目标会话、输入框和草稿；"
                                f"target_app 保持为“{target_app}”，ax_query 应包含聊天输入框角色与草稿文字。"
                            )
                            continue
                        supported_action, unsupported_action = deps.simple_human_action_support(next_decision)
                        if not supported_action:
                            if TASK_CONTROL.remaining_budget(task_id) <= 0:
                                next_decision = deps.blocked_react_decision(original_user_text, next_decision)
                                continuation_text = next_decision.summary
                                break
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
                                remaining_budget=max(
                                    0,
                                    TASK_CONTROL.remaining_budget(task_id) - 1,
                                ),
                            )
                            proposed_scope = action_scope(proposed_action)
                            continuation_profile = (
                                "file"
                                if proposed_scope == "file"
                                else "desktop"
                                if proposed_scope in {"desktop", "browser"}
                                else "agent"
                            )
                            continue
                        next_proposal_id, next_proposal = deps.create_human_ops_act_proposal(
                            next_decision,
                            session_id=session_id,
                            user_text=original_user_text,
                        )
                        if next_proposal_id in deps.pending_proposals:
                            deps.pending_proposals[next_proposal_id]["task_id"] = task_id
                            deps.pending_proposals[next_proposal_id][
                                "actions_executed"
                            ] = int(record.get("actions_executed") or 0)
                            chat_send_transaction = record.get(
                                "chat_send_transaction"
                            )
                            if isinstance(chat_send_transaction, dict):
                                deps.pending_proposals[next_proposal_id][
                                    "chat_send_transaction"
                                ] = {
                                    **chat_send_transaction,
                                    "input_ax_ref": deepcopy(
                                        chat_send_transaction.get(
                                            "input_ax_ref"
                                        )
                                        if isinstance(
                                            chat_send_transaction.get(
                                                "input_ax_ref"
                                            ),
                                            dict,
                                        )
                                        else {}
                                    ),
                                }
                        current_private_config = deps.normalize_private_config()
                        current_human_ops_config = (
                            current_private_config.get("human_ops", {})
                            if isinstance(current_private_config.get("human_ops"), dict)
                            else {}
                        )
                        if not next_proposal.requires_review:
                            if next_proposal_id in deps.pending_proposals:
                                deps.pending_proposals[next_proposal_id]["authorization_mode"] = (
                                    "risk_classified_safe"
                                )
                            async for event in stream_human_ops_proposal_decision(
                                next_proposal_id,
                                {"approved": True},
                                deps,
                            ):
                                yield event
                            return
                        next_action_type = str(
                            next_proposal.payload.get("action_type") or ""
                        ).strip()
                        if (
                            full_authorization_enabled(current_human_ops_config)
                            and deps.notify_human_ops_action is not None
                            and next_action_type != "shell"
                        ):
                            if next_proposal_id in deps.pending_proposals:
                                deps.pending_proposals[next_proposal_id]["authorization_mode"] = (
                                    AUTHORIZATION_MODE_FULL
                                )
                            async for event in stream_human_ops_proposal_decision(
                                next_proposal_id,
                                {"approved": True},
                                deps,
                            ):
                                yield event
                            return
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
                        if TASK_CONTROL.remaining_budget(task_id) <= 0:
                            next_decision = deps.blocked_react_decision(original_user_text, next_decision)
                            continuation_text = next_decision.summary
                            break
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
                            remaining_budget=max(
                                0,
                                TASK_CONTROL.remaining_budget(task_id) - 1,
                            ),
                        )
                        continue
                    if next_kind == DecisionKind.OBSERVE:
                        TASK_CONTROL.check(task_id, next_action="Observe 分析")
                        if not TASK_CONTROL.consume_budget(task_id, "observe"):
                            next_decision = deps.blocked_react_decision(original_user_text, next_decision)
                            continuation_text = next_decision.summary
                            break
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
                        observation = TASK_CONTROL.learn_observation_route(
                            task_id,
                            observation,
                        )
                        confirmation = near_match_confirmation_decision(
                            observation,
                            original_user_text,
                        )
                        if confirmation is not None:
                            next_decision = confirmation
                            continuation_text = confirmation.summary
                            break
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
                            remaining_budget=max(
                                0,
                                TASK_CONTROL.remaining_budget(task_id) - 1,
                            ),
                            observation_text=observation_text,
                            coordinate_context=str(observation.get("coordinate_context") or "").strip(),
                            computer_use_context=deps.computer_use_context_text(observation),
                            brain_observed_image=bool(followup_image_data_url),
                        )
                        continue
                    status = deps.goal_status(next_decision, operation_request=True)
                    if next_kind in {DecisionKind.SAY, DecisionKind.STOP} and not deps.goal_is_terminal(status):
                        if correction_used or TASK_CONTROL.remaining_budget(task_id) <= 0:
                            next_decision = deps.blocked_react_decision(original_user_text, next_decision)
                            continuation_text = next_decision.summary
                            break
                        correction_used = True
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
                            remaining_budget=max(
                                0,
                                TASK_CONTROL.remaining_budget(task_id) - 1,
                            ),
                            correction=True,
                        )
                        continue
                    break
                if next_decision is not None and continuation_text:
                    yield deps.sse("display_segment", {"text": continuation_text})
                    yield await done_event(
                        {
                            "turn_id": proposal_key,
                            "proposal_id": proposal_key,
                            "session_id": session_id,
                            "text": continuation_text,
                            "approved": True,
                            "execution": execution,
                            "decision": next_decision.to_dict(),
                            "observation": observation,
                            **authorization_payload,
                        },
                    )
                    return
            except Exception as exc:
                continuation_error = deps.sanitize_brain_error(exc, brain_config)
                record["status"] = "continuation_failed"
                execution = {
                    **execution,
                    "continuation_ok": False,
                    "continuation_error": continuation_error,
                }
                final_text = (
                    "动作已经执行，但后续任务推进失败："
                    f"{continuation_error}"
                )
                yield deps.sse(
                    "phase",
                    {
                        "category": "blocked",
                        "name": "human_ops_continuation",
                        "status": "failed",
                        "text": final_text,
                        "source": "human_ops",
                    },
                )
                yield deps.sse(
                    "display_segment",
                    {"text": final_text},
                )
        yield await done_event(
            {
                "turn_id": proposal_key,
                "proposal_id": proposal_key,
                "session_id": session_id,
                "text": final_text,
                "approved": True,
                "execution": execution,
                **authorization_payload,
            },
        )

    return event_stream()
