from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from brain.contracts import is_terminal_goal_status
from brain.decisions import BrainDecision, DecisionKind

from .approvals import ApprovalRequirement, ReviewableProposal
from .filesystem_actions import filesystem_action_label
from .playwright_actions import playwright_action_label
from .shell_actions import assess_shell_risk, shell_action_label
from .previews import red_dot_click_preview

TerminalPredicate = Callable[[str], bool]

_TERMINAL_NEXT_STEPS = {"", "done", "stop", "blocked", "need_user"}


def _coerce_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


def _default_goal_is_terminal(status: str) -> bool:
    return is_terminal_goal_status(status)


def _is_chat_target_app(value: object) -> bool:
    key = "".join(str(value or "").casefold().split())
    return key in {"qq", "腾讯qq", "wechat", "微信", "微信app"}


def proposal_tool_label(proposal: ReviewableProposal) -> str:
    if proposal.proposal_type == "remember":
        operation = str(proposal.payload.get("operation") or "save").strip()
        memory = proposal.payload.get("memory") if isinstance(proposal.payload.get("memory"), dict) else {}
        if operation in {"forget", "resolve", "snooze"}:
            target = proposal.payload.get("target") if isinstance(proposal.payload.get("target"), dict) else {}
            verb = {"forget": "忘记", "resolve": "完成", "snooze": "稍后回访"}[operation]
            return f"{verb}：{str(target.get('summary') or proposal.summary).strip()}"
        kind = str(memory.get("kind") or "general").strip()
        return f"保存 {kind}：{str(memory.get('summary') or proposal.summary).strip()}"
    action_type = str(proposal.payload.get("action_type") or "").strip()
    args = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    target_app = str(args.get("target_app") or "").strip()
    target_suffix = f" · {target_app}" if target_app else ""
    if action_type == "click":
        label = str(args.get("label") or args.get("target") or "目标位置").strip() or "目标位置"
        ax_ref = args.get("ax_ref") if isinstance(args.get("ax_ref"), dict) else {}
        if ax_ref:
            role = str(ax_ref.get("role") or "AXElement").strip() or "AXElement"
            return f"点击 {label}（{role} 语义目标）{target_suffix}"
        return f"点击 {label} ({_coerce_int(args.get('x'))}, {_coerce_int(args.get('y'))}){target_suffix}"
    if action_type == "type_text":
        label = str(args.get("label") or args.get("target") or "输入位置").strip() or "输入位置"
        text = str(args.get("text") or "").strip()
        preview = text[:24] + ("..." if len(text) > 24 else "")
        ax_ref = args.get("ax_ref") if isinstance(args.get("ax_ref"), dict) else {}
        verb = "清空" if args.get("replace_existing") is True and not text else (
            "替换" if args.get("replace_existing") is True else "输入到"
        )
        if ax_ref:
            role = str(ax_ref.get("role") or "AXElement").strip() or "AXElement"
            return f"{verb} {label}（{role} 语义目标）: {preview}{target_suffix}"
        return f"{verb} {label}: {preview}{target_suffix}"
    if action_type == "key_press":
        key = str(args.get("key") or "").strip().lower() or "enter"
        label = str(args.get("label") or args.get("target") or "当前焦点").strip() or "当前焦点"
        key_label = "回车" if key in {"enter", "return"} else key
        return f"按下{key_label}（{label}）{target_suffix}"
    if action_type == "launch_app":
        app_name = str(args.get("app") or args.get("name") or args.get("label") or "应用").strip() or "应用"
        return f"打开应用 {app_name}"
    if action_type == "playwright":
        operation = str(args.get("operation") or "").strip() or "browser"
        profile = str(args.get("profile") or "未选择个人资料").strip() or "未选择个人资料"
        return f"Playwright {operation}：{playwright_action_label(args)} · 个人资料：{profile}"
    if action_type == "shell":
        return shell_action_label(args)
    if action_type.startswith("file_"):
        return filesystem_action_label(action_type, args)
    return proposal.summary or action_type


def proposal_event_payload(proposal_id: str, proposal: ReviewableProposal) -> dict[str, Any]:
    action_type = str(proposal.payload.get("action_type") or "").strip()
    tool_name = action_type or ("memory" if proposal.proposal_type == "remember" else "")
    return {
        "turn_id": proposal_id,
        "proposal_id": proposal_id,
        "proposal_type": proposal.proposal_type,
        "action_type": action_type,
        "text": proposal.summary,
        "summary": proposal.summary,
        "tools": [{"name": tool_name, "summary": proposal_tool_label(proposal)}] if tool_name else [],
        "preview": proposal.preview.to_dict() if proposal.preview else None,
        "requires_review": proposal.requires_review,
    }


def build_human_ops_memory_proposal(
    *,
    operation: str,
    memory: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    follow_up_at: str = "",
) -> ReviewableProposal:
    normalized_operation = str(operation or "save").strip()
    if normalized_operation == "forget":
        target_record = dict(target or {})
        memory_id = str(target_record.get("id") or "").strip()
        if not memory_id:
            raise ValueError("找不到要忘记的长期记忆。")
        summary = str(target_record.get("summary") or target_record.get("title") or memory_id).strip()
        return ReviewableProposal.remember(
            summary=f"Ipet 想忘记这条关系记忆：{summary}",
            payload={"operation": "forget", "memory_id": memory_id, "target": target_record},
        )
    if normalized_operation in {"resolve", "snooze"}:
        target_record = dict(target or {})
        memory_id = str(target_record.get("id") or "").strip()
        if not memory_id:
            raise ValueError("找不到要更新的开放事项。")
        summary = str(target_record.get("summary") or target_record.get("title") or memory_id).strip()
        verb = "标记为完成" if normalized_operation == "resolve" else "稍后再回访"
        payload = {"operation": normalized_operation, "memory_id": memory_id, "target": target_record}
        if normalized_operation == "snooze":
            payload["follow_up_at"] = str(follow_up_at or "").strip()
        return ReviewableProposal.remember(
            summary=f"Ipet 想把开放事项{verb}：{summary}",
            payload=payload,
        )

    candidate = dict(memory or {})
    summary = str(candidate.get("summary") or "").strip()
    if not summary:
        raise ValueError("记忆候选不能为空。")
    return ReviewableProposal.remember(
        summary=f"Ipet 想保存这条关系记忆：{summary}",
        payload={"operation": "save", "memory": candidate},
    )


def should_default_continue_after_approval(
    goal: dict[str, Any],
    *,
    goal_is_terminal: TerminalPredicate | None = None,
) -> bool:
    if not isinstance(goal, dict) or not goal:
        return False
    is_terminal = goal_is_terminal or _default_goal_is_terminal
    status = str(goal.get("status") or "").strip()
    if is_terminal(status):
        return False
    next_step = str(goal.get("next") or "").strip().casefold()
    return next_step not in _TERMINAL_NEXT_STEPS


def build_human_ops_act_proposal(
    decision: BrainDecision,
    *,
    goal_is_terminal: TerminalPredicate | None = None,
) -> ReviewableProposal:
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    action_type = str(payload.get("action_type") or "").strip()
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    proposal_payload = dict(arguments)
    goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else {}
    if "continue_after_approval" not in proposal_payload and should_default_continue_after_approval(
        goal,
        goal_is_terminal=goal_is_terminal,
    ):
        proposal_payload["continue_after_approval"] = True
    if goal:
        proposal_payload["goal"] = dict(goal)
    preview = None
    if action_type == "click":
        label = str(arguments.get("label") or arguments.get("target") or "目标位置").strip() or "目标位置"
        ax_ref = arguments.get("ax_ref") if isinstance(arguments.get("ax_ref"), dict) else {}
        if not ax_ref:
            preview = red_dot_click_preview(
                x=_coerce_int(arguments.get("x")),
                y=_coerce_int(arguments.get("y")),
                label=label,
            )
        role = str(ax_ref.get("role") or "AXElement").strip() or "AXElement"
        summary = (
            f"Ipet 想通过 macOS Accessibility 点击：{label}（{role}）"
            if ax_ref
            else f"Ipet 想点击：{label}"
        )
    elif action_type == "type_text":
        label = str(arguments.get("label") or arguments.get("target") or "输入位置").strip() or "输入位置"
        text = str(arguments.get("text") or "").strip()
        preview_text = text[:40] + ("..." if len(text) > 40 else "")
        ax_ref = arguments.get("ax_ref") if isinstance(arguments.get("ax_ref"), dict) else {}
        role = str(ax_ref.get("role") or "AXElement").strip() or "AXElement"
        summary = (
            f"Ipet 想通过 macOS Accessibility 输入到 {label}（{role}）：{preview_text}"
            if ax_ref
            else f"Ipet 想输入到 {label}：{preview_text}"
        )
    elif action_type == "key_press":
        key = str(arguments.get("key") or "").strip().lower() or "enter"
        label = str(arguments.get("label") or arguments.get("target") or "当前焦点").strip() or "当前焦点"
        key_label = "回车" if key in {"enter", "return"} else key
        summary = f"Ipet 想按下{key_label}：{label}"
    elif action_type == "launch_app":
        app_name = str(arguments.get("app") or arguments.get("name") or arguments.get("label") or "应用").strip() or "应用"
        summary = f"Ipet 想打开应用：{app_name}"
    elif action_type == "playwright":
        operation = str(arguments.get("operation") or "").strip() or "browser"
        summary = f"Ipet 想用 Playwright 执行 {operation}：{playwright_action_label(arguments)}"
    elif action_type == "shell":
        risk = assess_shell_risk(arguments)
        risk_text = "；".join(risk.reasons) if risk.reasons else str(arguments.get("risk_reason") or "低风险命令")
        local_rules = ", ".join(rule for rule in risk.matched_rules if not rule.startswith("model_")) or "无"
        summary = (
            f"Ipet 想运行命令：{shell_action_label(arguments)}"
            f" · 模型判断：{risk.model_risk} — {str(arguments.get('risk_reason') or '')}"
            f" · 本地规则：{local_rules} · 风险说明：{risk_text}"
        )
    elif action_type.startswith("file_"):
        summary = f"Ipet 想执行文件动作：{filesystem_action_label(action_type, arguments)}"
    else:
        summary = decision.summary or f"Ipet 想执行：{action_type}"
    requirement = ApprovalRequirement.REQUIRED
    if action_type == "shell" and not assess_shell_risk(arguments).dangerous:
        requirement = ApprovalRequirement.NOT_REQUIRED
    return ReviewableProposal.act(
        action_type=action_type,
        summary=summary,
        payload=proposal_payload,
        preview=preview,
        requirement=requirement,
    )


def proposal_arguments(proposal: ReviewableProposal) -> dict[str, Any]:
    args = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    return dict(args)


def proposal_continue_after_approval(proposal: ReviewableProposal) -> bool:
    return bool(proposal_arguments(proposal).get("continue_after_approval"))


def with_inherited_enter_expected_text(
    decision: BrainDecision,
    *,
    previous_proposal: ReviewableProposal,
    execution: dict[str, Any],
    observation: dict[str, Any] | None = None,
    chat_send_transaction: dict[str, Any] | None = None,
) -> BrainDecision:
    if decision.kind != DecisionKind.PROPOSE_ACT:
        return decision
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    action_type = str(payload.get("action_type") or "").strip()
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    next_args = dict(arguments)
    transaction = (
        chat_send_transaction
        if isinstance(chat_send_transaction, dict)
        else {}
    )
    transaction_target_app = str(transaction.get("target_app") or "").strip()
    transaction_chat = str(transaction.get("intended_chat") or "").strip()
    transaction_ref = (
        transaction.get("input_ax_ref")
        if isinstance(transaction.get("input_ax_ref"), dict)
        else {}
    )
    transaction_text = str(transaction.get("expected_text") or "")
    requested_key = str(arguments.get("key") or "enter").strip().lower() or "enter"
    if (
        action_type == "key_press"
        and requested_key in {"enter", "return"}
        and _is_chat_target_app(transaction_target_app)
        and transaction_chat
        and transaction_ref
        and transaction_text.strip()
    ):
        next_args.update(
            {
                "target_app": transaction_target_app,
                "intended_chat": transaction_chat,
                "input_ax_ref": deepcopy(transaction_ref),
                "expected_text": transaction_text,
            }
        )
        goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else None
        return BrainDecision.propose_act(action_type, next_args, goal=goal)
    previous_args = proposal_arguments(previous_proposal)
    if action_type in {"click", "type_text", "key_press"} and not str(next_args.get("target_app") or "").strip():
        inherited_target_app = str(
            execution.get("target_app")
            or execution.get("app")
            or previous_args.get("target_app")
            or previous_args.get("app")
            or ""
        ).strip()
        if inherited_target_app:
            next_args["target_app"] = inherited_target_app
    target_app = str(next_args.get("target_app") or "").strip()
    observed = observation if isinstance(observation, dict) else {}
    chat_context = (
        observed.get("chat_context")
        if isinstance(observed.get("chat_context"), dict)
        else {}
    )
    intended_chat = str(
        previous_args.get("intended_chat")
        or execution.get("intended_chat")
        or chat_context.get("contact")
        or next_args.get("intended_chat")
        or ""
    ).strip()
    if (
        action_type in {"type_text", "key_press"}
        and _is_chat_target_app(target_app)
        and intended_chat
    ):
        next_args["intended_chat"] = intended_chat
    if action_type == "key_press" and _is_chat_target_app(target_app):
        previous_input_ref = (
            previous_args.get("input_ax_ref")
            if isinstance(previous_args.get("input_ax_ref"), dict)
            else previous_args.get("ax_ref")
            if isinstance(previous_args.get("ax_ref"), dict)
            else {}
        )
        if isinstance(previous_input_ref, dict) and previous_input_ref:
            next_args["input_ax_ref"] = deepcopy(previous_input_ref)
    if action_type != "key_press":
        if next_args == arguments:
            return decision
        goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else None
        return BrainDecision.propose_act(action_type, next_args, goal=goal)
    key = str(arguments.get("key") or "enter").strip().lower() or "enter"
    if key not in {"enter", "return"}:
        return decision
    previous_action_type = str(previous_proposal.payload.get("action_type") or "").strip()
    if previous_action_type != "type_text":
        if next_args == arguments:
            return decision
        goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else None
        return BrainDecision.propose_act(action_type, next_args, goal=goal)
    expected_text = str(execution.get("text") or previous_args.get("text") or "").strip()
    if not expected_text:
        if next_args == arguments:
            return decision
        goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else None
        return BrainDecision.propose_act(action_type, next_args, goal=goal)
    next_args["expected_text"] = expected_text
    previous_target_app = str(
        execution.get("target_app")
        or execution.get("app")
        or previous_args.get("target_app")
        or previous_args.get("app")
        or ""
    ).strip()
    if previous_target_app:
        next_args["target_app"] = previous_target_app
    if _is_chat_target_app(previous_target_app):
        previous_intended_chat = str(
            previous_args.get("intended_chat")
            or execution.get("intended_chat")
            or chat_context.get("contact")
            or ""
        ).strip()
        previous_input_ref = (
            previous_args.get("input_ax_ref")
            if isinstance(previous_args.get("input_ax_ref"), dict)
            else previous_args.get("ax_ref")
            if isinstance(previous_args.get("ax_ref"), dict)
            else {}
        )
        if previous_intended_chat:
            next_args["intended_chat"] = previous_intended_chat
        if previous_input_ref:
            next_args["input_ax_ref"] = deepcopy(previous_input_ref)
    goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else None
    return BrainDecision.propose_act(action_type, next_args, goal=goal)
