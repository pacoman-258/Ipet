from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from brain.decisions import BrainDecision, DecisionKind


def _click_coordinate_clarification_text(observation_text: str) -> str:
    observation = str(observation_text or "").strip() or "我已经观察了屏幕，但还没有拿到可执行点击所需的坐标。"
    return (
        f"{observation}\n\n"
        "要继续点击，需要完整的 x 和 y 屏幕坐标。当前观察结果里的坐标不完整或缺失，"
        "请补充完整 x 和 y，也就是可点击中心点的完整 x/y，或让我重新观察一次。"
    )


def _click_coordinate_observe_failure_text(observation_text: str) -> str:
    observation = str(observation_text or "").strip() or "我已经观察了屏幕，但还没有拿到可执行点击所需的坐标。"
    return (
        f"{observation}\n\n"
        "这个点击结果缺少完整 x 和 y 屏幕坐标，需要重新观察或补充可点击中心点的完整 x/y 后才能继续。"
    )


def _fallback_after_observe_brain_error(
    observation_text: str,
    user_text: str,
    *,
    looks_like_click_request: Callable[[str], bool] | None = None,
) -> str:
    if looks_like_click_request is not None and looks_like_click_request(user_text):
        return _click_coordinate_clarification_text(observation_text)
    observation = str(observation_text or "").strip()
    if observation:
        return observation
    return "我已经观察了屏幕，但 Brain 后续整理暂时没有返回内容。"


def _coerce_decision_for_human_ops(
    user_text: str,
    decision: BrainDecision,
    *,
    looks_like_desktop_observe_request: Callable[[str], bool],
    looks_like_desktop_action_request: Callable[[str], bool],
) -> BrainDecision:
    if _decision_kind(decision) != DecisionKind.SAY:
        return decision
    if _decision_goal(decision):
        return decision
    if looks_like_desktop_observe_request(user_text) or looks_like_desktop_action_request(user_text):
        return BrainDecision.observe(str(user_text or "screen")[:120])
    return decision


def _decision_kind(decision: BrainDecision) -> DecisionKind:
    return decision.kind if isinstance(decision.kind, DecisionKind) else DecisionKind(str(decision.kind))


def _decision_goal(decision: BrainDecision) -> dict[str, Any]:
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    goal = payload.get("goal")
    return dict(goal) if isinstance(goal, dict) else {}


def _goal_status(decision: BrainDecision, *, operation_request: bool) -> str:
    status = str(_decision_goal(decision).get("status") or "").strip()
    if status:
        return status
    return "in_progress" if operation_request else ""


def _goal_is_terminal(status: str) -> bool:
    return status in {"done", "blocked", "need_user"}


def _react_missing_summary(goal: dict[str, Any]) -> str:
    missing = goal.get("missing") if isinstance(goal.get("missing"), list) else []
    clean_missing = [str(item or "").strip() for item in missing if str(item or "").strip()]
    if clean_missing:
        return "；".join(clean_missing[:3])
    next_step = str(goal.get("next") or "").strip()
    return next_step or "可审批动作所需的可靠依据"


def _blocked_react_decision(user_text: str, last_decision: BrainDecision | None = None) -> BrainDecision:
    goal = _decision_goal(last_decision) if isinstance(last_decision, BrainDecision) else {}
    objective = str(goal.get("objective") or user_text or "当前桌面操作目标").strip()
    blocked_goal = {
        **goal,
        "objective": objective,
        "status": "blocked",
        "next": "need_more_evidence",
    }
    missing_text = _react_missing_summary(blocked_goal)
    blocked_goal["missing"] = blocked_goal.get("missing") if isinstance(blocked_goal.get("missing"), list) else [missing_text]
    text = f"我还不能把“{objective}”推进到可审批动作：还缺少{missing_text}。"
    return BrainDecision.say(text, goal=blocked_goal)


def _react_followup_prompt(
    *,
    user_text: str,
    decision: BrainDecision,
    remaining_budget: int,
    observation_text: str = "",
    coordinate_context: str = "",
    computer_use_context: str = "",
    correction: bool = False,
) -> str:
    goal = _decision_goal(decision)
    goal_json = json.dumps(goal, ensure_ascii=False) if goal else "{}"
    base = (
        f"用户原始请求：{user_text}\n\n"
        f"当前 goal：{goal_json}\n"
        f"剩余 ReAct 自动继续预算：{remaining_budget}\n\n"
    )
    if correction:
        return (
            f"{base}"
            "你刚才返回了 say 或 stop，但目标还没有完成，不能把它作为最终答复。\n"
            "请只选择一个下一步 JSON：observe、propose_act、带 goal.status=need_user 的 say、"
            "或带 goal.status=blocked 的 say/stop。"
        )
    if observation_text:
        coordinate_block = f"\n\n{coordinate_context.strip()}\n" if str(coordinate_context or "").strip() else ""
        computer_use_block = f"\n\n{computer_use_context.strip()}\n" if str(computer_use_context or "").strip() else ""
        return (
            f"{base}"
            "你刚才让 observe 模型查看了屏幕。observe 用自然语言回答如下：\n"
            f"{observation_text}"
            f"{coordinate_block}"
            f"{computer_use_block}\n"
            "请基于这个观察结果决定下一步。不要因为格式问题要求 observe 输出 JSON。"
            "如果用户只是问屏幕内容，请用 say 直接回答。"
            "如果用户要求点击，请确保 propose_act 的 x/y 是 macOS screen coordinates。"
            "如果 observe 的坐标像是截图/图像像素，先按上面的 image-to-screen scale 换算。"
            "如果 Structured computer-use context 里已有 surface 和 affordance，优先用 affordance 选择下一步最小动作。"
            "如果观察结果说看不到或不确定，请用 goal.status=blocked 或 need_user 的 say 如实告诉用户。"
        )
    return (
        f"{base}"
        f"上一轮 Brain 决定：{decision.to_dict()}\n\n"
        "这是 ReAct 的无副作用 think 进展。请继续推进到 observe、propose_act、"
        "或带 terminal goal.status 的 say/stop。"
    )


def _simple_human_action_support(decision: BrainDecision) -> tuple[bool, str]:
    if _decision_kind(decision) != DecisionKind.PROPOSE_ACT:
        return True, ""
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    action_type = str(payload.get("action_type") or "").strip()
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    if action_type == "click":
        if _has_numeric_action_argument(arguments, "x") and _has_numeric_action_argument(arguments, "y"):
            return True, ""
        return False, "click missing complete x/y"
    if action_type == "type_text":
        return True, ""
    if action_type == "key_press":
        key = str(arguments.get("key") or "enter").strip().lower() or "enter"
        if key in {"enter", "return"}:
            return True, ""
        return False, f"key_press {key}"
    return False, action_type or "unknown"


def _has_numeric_action_argument(arguments: dict[str, Any], key: str) -> bool:
    if key not in arguments:
        return False
    try:
        float(arguments.get(key))
        return True
    except (TypeError, ValueError):
        return False


def _unsupported_simple_action_prompt(
    *,
    user_text: str,
    decision: BrainDecision,
    unsupported_action: str,
    remaining_budget: int,
) -> str:
    goal = _decision_goal(decision)
    goal_json = json.dumps(goal, ensure_ascii=False) if goal else "{}"
    return (
        f"用户原始请求：{user_text}\n\n"
        f"当前 goal：{goal_json}\n"
        f"上一轮 Brain 返回了不可执行或不符合当前简单人类动作范围的 propose_act：{unsupported_action}。\n"
        f"上一轮 Brain 决定：{decision.to_dict()}\n"
        f"剩余 ReAct 自动继续预算：{remaining_budget}\n\n"
        "当前 Human Ops 只能审批并执行这些简单人类动作：click、type_text、key_press enter。"
        "打开 App 请通过 observe 找到 Dock/App 图标或搜索结果，再 propose_act click；"
        "聚焦输入框也用 click；输入文本用 type_text；发送/确认用 key_press enter。"
        "请重新选择一个下一步 JSON：observe、propose_act click、propose_act type_text、propose_act key_press enter，"
        "或带 terminal goal.status 的 say/stop。"
    )
