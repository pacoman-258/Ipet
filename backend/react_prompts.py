from __future__ import annotations

import json
from typing import Any

from brain.contracts import is_terminal_goal_status, validate_decision_payload
from brain.decisions import BrainDecision, DecisionKind
from human_ops.filesystem_actions import FILESYSTEM_ACTIONS, validate_filesystem_action
from human_ops.playwright_actions import playwright_command
from human_ops.shell_actions import validate_shell_action


def _prompt_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…<省略 {len(text) - limit} 字符>"


def _prompt_json(value: object, limit: int = 2_000) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= limit else f"{text[:limit]}…<省略 {len(text) - limit} 字符>"


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
    require_coordinates: bool = False,
) -> str:
    if require_coordinates:
        return _click_coordinate_clarification_text(observation_text)
    observation = str(observation_text or "").strip()
    if observation:
        return observation
    return "我已经观察了屏幕，但 Brain 后续整理暂时没有返回内容。"


def _coerce_decision_for_human_ops(
    _user_text: str,
    decision: BrainDecision,
) -> BrainDecision:
    # Product intent belongs to Brain. This boundary may validate a proposed action later,
    # but must not replace the model's chosen surface or action from user-text keywords.
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
    return is_terminal_goal_status(status)


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
    brain_observed_image: bool = False,
    correction: bool = False,
) -> str:
    goal = _decision_goal(decision)
    goal_json = _prompt_json(goal, 1_500) if goal else "{}"
    base = (
        f"用户原始请求：{_prompt_text(user_text, 1_500)}\n\n"
        f"当前 goal：{goal_json}\n"
        f"剩余任务步骤预算：{remaining_budget}\n\n"
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
        if brain_observed_image:
            observation_intro = (
                "当前用户消息附带了 Body 刚捕获的屏幕截图。请由你直接观察图片；"
                "下面的文字只有截图状态和结构化系统证据，不是另一个视觉模型的结论：\n"
            )
            decision_instruction = (
                "请把图片、坐标上下文与结构化系统证据作为同一份上下文，直接决定下一步。"
                "需要点击时，先在所附原始截图中判断目标中心，propose_act 使用截图像素 x/y，"
                '并明确写 coordinate_space="image_pixels"；后端会确定性换算为 macOS 屏幕点。'
            )
        else:
            observation_intro = "Body 刚完成一次界面观察，自然语言结果如下：\n"
            decision_instruction = "请基于这个观察结果决定下一步。不要因为格式问题要求 observe 输出 JSON。"
        return (
            f"{base}"
            f"{observation_intro}"
            f"{_prompt_text(observation_text, 3_500)}"
            f"{_prompt_text(coordinate_block, 1_000)}"
            f"{_prompt_text(computer_use_block, 2_500)}\n"
            f"{decision_instruction}"
            "根据系统合同和这份新证据，只返回一个最小下一步；看不到或不确定时如实 blocked/need_user。"
        )
    return (
        f"{base}"
        f"上一轮 Brain 决定：{_prompt_json(decision.to_dict())}\n\n"
        "这是 ReAct 的无副作用 think 进展。请继续推进到 observe、propose_act、"
        "或带 terminal goal.status 的 say/stop。"
    )


def _simple_human_action_support(decision: BrainDecision) -> tuple[bool, str]:
    if _decision_kind(decision) != DecisionKind.PROPOSE_ACT:
        return True, ""
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    action_type = str(payload.get("action_type") or "").strip()
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    valid, reason = validate_decision_payload(decision.kind.value, payload)
    if not valid:
        return False, reason
    if action_type == "click":
        target_app = str(arguments.get("target_app") or "").strip()
        if not target_app:
            return False, "click missing target_app"
        if _has_ax_action_ref(arguments):
            return True, ""
        if _has_numeric_action_argument(arguments, "x") and _has_numeric_action_argument(arguments, "y"):
            return True, ""
        return False, "click missing complete x/y"
    if action_type == "type_text":
        target_app = str(arguments.get("target_app") or "").strip()
        if not target_app:
            return False, "type_text missing target_app"
        if "ax_ref" in arguments and not _has_ax_action_ref(arguments):
            return False, "type_text invalid ax_ref"
        if arguments.get("replace_existing") is True and not _has_ax_action_ref(
            arguments
        ):
            return False, "replace text missing reviewed AX input"
        ax_ref = (
            arguments.get("ax_ref")
            if isinstance(arguments.get("ax_ref"), dict)
            else {}
        )
        chat_message_input = bool(
            _is_chat_target_app(target_app)
            and str(ax_ref.get("input_kind") or "").strip()
            != "search_field"
        )
        if chat_message_input:
            if not str(arguments.get("intended_chat") or "").strip():
                return False, "chat type_text missing intended_chat"
            if not _has_ax_action_ref(arguments):
                return False, "chat type_text missing reviewed AX input"
        return True, ""
    if action_type == "key_press":
        target_app = str(arguments.get("target_app") or "").strip()
        if not target_app:
            return False, "key_press missing target_app"
        key = str(arguments.get("key") or "enter").strip().lower() or "enter"
        if key in {"enter", "return"}:
            if _is_chat_target_app(target_app):
                if not str(arguments.get("intended_chat") or "").strip():
                    return False, "chat send missing intended_chat"
                if not str(arguments.get("expected_text") or "").strip():
                    return False, "chat send missing expected_text"
                if not _valid_ax_reference(arguments.get("input_ax_ref")):
                    return False, "chat send missing reviewed input_ax_ref"
            return True, ""
        return False, f"key_press {key}"
    if action_type == "launch_app":
        app_name = str(arguments.get("app") or "").strip()
        return (True, "") if app_name else (False, "launch_app missing app name")
    if action_type == "playwright":
        try:
            playwright_command(arguments)
        except ValueError as exc:
            return False, str(exc)
        return True, ""
    if action_type == "shell":
        return validate_shell_action(arguments)
    if action_type in FILESYSTEM_ACTIONS:
        return validate_filesystem_action(action_type, arguments)
    return False, action_type or "unknown"


def _is_chat_target_app(value: object) -> bool:
    key = "".join(str(value or "").casefold().split())
    return key in {"qq", "腾讯qq", "wechat", "微信", "微信app"}


def _valid_ax_reference(value: object) -> bool:
    ax_ref = value if isinstance(value, dict) else {}
    path = ax_ref.get("path")
    return bool(
        str(ax_ref.get("app_id") or "").strip()
        and str(ax_ref.get("role") or "").strip()
        and str(ax_ref.get("fingerprint") or "").strip()
        and isinstance(path, list)
        and all(isinstance(item, int) and item >= 0 for item in path)
    )


def _has_numeric_action_argument(arguments: dict[str, Any], key: str) -> bool:
    if key not in arguments:
        return False
    try:
        float(arguments.get(key))
        return True
    except (TypeError, ValueError):
        return False


def _has_ax_action_ref(arguments: dict[str, Any]) -> bool:
    return _valid_ax_reference(arguments.get("ax_ref"))


def _unsupported_simple_action_prompt(
    *,
    user_text: str,
    decision: BrainDecision,
    unsupported_action: str,
    remaining_budget: int,
) -> str:
    goal = _decision_goal(decision)
    goal_json = _prompt_json(goal, 1_500) if goal else "{}"
    return (
        f"用户原始请求：{_prompt_text(user_text, 1_500)}\n\n"
        f"当前 goal：{goal_json}\n"
        f"动作校验失败：{_prompt_text(unsupported_action, 500)}。\n"
        f"上一轮 Brain 决定：{_prompt_json(decision.to_dict())}\n"
        f"剩余任务步骤预算：{remaining_budget}\n\n"
        "请按系统合同中的动作 schema 修正缺失或非法字段，不改变用户目标，也不重复询问已经明确的选择。"
        "只返回一个 observe、合法 propose_act，或带终态 goal.status 的 say/stop JSON。"
    )
