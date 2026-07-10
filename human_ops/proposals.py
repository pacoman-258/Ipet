from __future__ import annotations

from typing import Any, Callable

from brain.decisions import BrainDecision, DecisionKind

from .approvals import ReviewableProposal
from .previews import red_dot_click_preview

IntentPredicate = Callable[[str], bool]
TerminalPredicate = Callable[[str], bool]


def _coerce_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


def _default_goal_is_terminal(status: str) -> bool:
    return str(status or "").strip() in {"done", "blocked", "need_user"}


def _default_intent_predicate(_text: str) -> bool:
    return False


def proposal_tool_label(proposal: ReviewableProposal) -> str:
    action_type = str(proposal.payload.get("action_type") or "").strip()
    args = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    if action_type == "click":
        label = str(args.get("label") or args.get("target") or "目标位置").strip() or "目标位置"
        return f"点击 {label} ({_coerce_int(args.get('x'))}, {_coerce_int(args.get('y'))})"
    if action_type == "type_text":
        label = str(args.get("label") or args.get("target") or "输入位置").strip() or "输入位置"
        text = str(args.get("text") or "").strip()
        preview = text[:24] + ("..." if len(text) > 24 else "")
        return f"输入到 {label}: {preview}"
    if action_type == "key_press":
        key = str(args.get("key") or "").strip().lower() or "enter"
        label = str(args.get("label") or args.get("target") or "当前焦点").strip() or "当前焦点"
        key_label = "回车" if key in {"enter", "return"} else key
        return f"按下{key_label}（{label}）"
    return proposal.summary or action_type


def proposal_event_payload(proposal_id: str, proposal: ReviewableProposal) -> dict[str, Any]:
    action_type = str(proposal.payload.get("action_type") or "").strip()
    return {
        "turn_id": proposal_id,
        "proposal_id": proposal_id,
        "proposal_type": proposal.proposal_type,
        "action_type": action_type,
        "text": proposal.summary,
        "summary": proposal.summary,
        "tools": [{"name": action_type, "summary": proposal_tool_label(proposal)}] if action_type else [],
        "preview": proposal.preview.to_dict() if proposal.preview else None,
        "requires_review": True,
    }


def should_default_continue_after_approval(
    user_text: str,
    goal: dict[str, Any],
    *,
    action_type: str,
    arguments: dict[str, Any],
    looks_like_desktop_action_request: IntentPredicate | None = None,
    looks_like_chat_reply_request: IntentPredicate | None = None,
    goal_is_terminal: TerminalPredicate | None = None,
) -> bool:
    if not isinstance(goal, dict) or not goal:
        return False
    is_terminal = goal_is_terminal or _default_goal_is_terminal
    status = str(goal.get("status") or "").strip()
    if is_terminal(status):
        return False
    action = str(action_type or "").strip()
    args = arguments if isinstance(arguments, dict) else {}
    key = str(args.get("key") or "").strip().lower()
    label = str(args.get("label") or args.get("target") or "").strip().lower()
    objective = str(goal.get("objective") or "")
    intent_text = f"{user_text} {objective}"
    looks_like_chat = looks_like_chat_reply_request or _default_intent_predicate
    looks_like_desktop = looks_like_desktop_action_request or _default_intent_predicate
    if (
        action == "key_press"
        and key in {"enter", "return"}
        and looks_like_chat(intent_text)
        and (str(args.get("expected_text") or "").strip() or "发送" in label or "send" in label)
    ):
        return True
    stage = str(goal.get("stage") or "").strip()
    next_step = str(goal.get("next") or "").strip()
    if stage in {"verify_result", "done"} or next_step in {"stop", "done"}:
        return False
    if not stage and not next_step:
        return False
    return looks_like_desktop(intent_text) or looks_like_chat(intent_text)


def build_human_ops_act_proposal(
    decision: BrainDecision,
    *,
    user_text: str,
    looks_like_desktop_action_request: IntentPredicate | None = None,
    looks_like_chat_reply_request: IntentPredicate | None = None,
    goal_is_terminal: TerminalPredicate | None = None,
) -> ReviewableProposal:
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    action_type = str(payload.get("action_type") or "").strip()
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    proposal_payload = dict(arguments)
    goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else {}
    if "continue_after_approval" not in proposal_payload and should_default_continue_after_approval(
        user_text,
        goal,
        action_type=action_type,
        arguments=arguments,
        looks_like_desktop_action_request=looks_like_desktop_action_request,
        looks_like_chat_reply_request=looks_like_chat_reply_request,
        goal_is_terminal=goal_is_terminal,
    ):
        proposal_payload["continue_after_approval"] = True
    if goal:
        proposal_payload["goal"] = dict(goal)
    preview = None
    if action_type == "click":
        label = str(arguments.get("label") or arguments.get("target") or "目标位置").strip() or "目标位置"
        preview = red_dot_click_preview(x=_coerce_int(arguments.get("x")), y=_coerce_int(arguments.get("y")), label=label)
        summary = f"Ipet 想点击：{label}"
    elif action_type == "type_text":
        label = str(arguments.get("label") or arguments.get("target") or "输入位置").strip() or "输入位置"
        text = str(arguments.get("text") or "").strip()
        preview_text = text[:40] + ("..." if len(text) > 40 else "")
        summary = f"Ipet 想输入到 {label}：{preview_text}"
    elif action_type == "key_press":
        key = str(arguments.get("key") or "").strip().lower() or "enter"
        label = str(arguments.get("label") or arguments.get("target") or "当前焦点").strip() or "当前焦点"
        key_label = "回车" if key in {"enter", "return"} else key
        summary = f"Ipet 想按下{key_label}：{label}"
    else:
        summary = decision.summary or f"Ipet 想执行：{action_type}"
    return ReviewableProposal.act(
        action_type=action_type,
        summary=summary,
        payload=proposal_payload,
        preview=preview,
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
) -> BrainDecision:
    if decision.kind != DecisionKind.PROPOSE_ACT:
        return decision
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    action_type = str(payload.get("action_type") or "").strip()
    if action_type != "key_press":
        return decision
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    key = str(arguments.get("key") or "enter").strip().lower() or "enter"
    if key not in {"enter", "return"}:
        return decision
    if str(arguments.get("expected_text") or "").strip():
        return decision
    previous_action_type = str(previous_proposal.payload.get("action_type") or "").strip()
    if previous_action_type != "type_text":
        return decision
    previous_args = proposal_arguments(previous_proposal)
    expected_text = str(execution.get("text") or previous_args.get("text") or "").strip()
    if not expected_text:
        return decision
    next_args = dict(arguments)
    next_args["expected_text"] = expected_text
    goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else None
    return BrainDecision.propose_act(action_type, next_args, goal=goal)
