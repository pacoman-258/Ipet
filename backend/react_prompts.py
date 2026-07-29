from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from brain.decisions import BrainDecision, DecisionKind
from human_ops.filesystem_actions import FILESYSTEM_ACTIONS, validate_filesystem_action
from human_ops.playwright_actions import playwright_command


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
    brain_observed_image: bool = False,
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
            observation_intro = "你刚才让 observe 模型查看了屏幕。observe 用自然语言回答如下：\n"
            decision_instruction = "请基于这个观察结果决定下一步。不要因为格式问题要求 observe 输出 JSON。"
        return (
            f"{base}"
            f"{observation_intro}"
            f"{observation_text}"
            f"{coordinate_block}"
            f"{computer_use_block}\n"
            f"{decision_instruction}"
            "如果用户只是问屏幕内容，请用 say 直接回答。"
            "如果 Structured computer-use context 的目标带 ax_ref，请把该 ax_ref 原样复制到 click 或 type_text arguments，"
            "不要自行改写字段，也不要同时猜坐标；若没有可用 ax_ref，click 才使用 macOS screen coordinates 的 x/y，"
            '并写 coordinate_space="macos_screen_points"。'
            "Structured computer-use context 里的 surface 和 affordance 是当前证据，请与用户目标和观察结果一起判断。"
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
        app_name = str(arguments.get("app") or arguments.get("name") or arguments.get("label") or "").strip()
        return (True, "") if app_name else (False, "launch_app missing app name")
    if action_type == "playwright":
        try:
            playwright_command(arguments)
        except ValueError as exc:
            return False, str(exc)
        return True, ""
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
    goal_json = json.dumps(goal, ensure_ascii=False) if goal else "{}"
    return (
        f"用户原始请求：{user_text}\n\n"
        f"当前 goal：{goal_json}\n"
        f"上一轮 Brain 返回了不可执行或不符合当前简单人类动作范围的 propose_act：{unsupported_action}。\n"
        f"上一轮 Brain 决定：{decision.to_dict()}\n"
        f"剩余 ReAct 自动继续预算：{remaining_budget}\n\n"
        "当前 Human Ops 能审批并执行这些动作：playwright、launch_app、click、type_text、key_press enter，以及受允许根目录约束的 file_list、file_read、file_write、file_mkdir、file_copy、file_move、file_delete。"
        "浏览器任务应遵循用户明确选择的 Playwright 或人类操作方式；若用户没有明确选择，默认使用 Playwright，不为执行方式询问。"
        "显式或默认使用 playwright 时，它支持 attach、open、snapshot、click、fill、type、press、导航和标签页操作，"
        "Chrome 个人资料优先使用当前任务中用户明确指定的名称，否则使用设置页传入的 Playwright 默认资料，并在每个 playwright proposal 的 arguments.profile 中原样携带；两者都缺少时才用 goal.status=need_user 的 say 询问，不得猜测资料。"
        "open 会精确解析真实 Chrome 资料：可验证时复用唯一活跃且允许远程调试的窗口，否则从原资料只读初始化 Ipet 私有登录态快照；找不到时使用执行器返回的可选名称让用户重选。"
        "用户明确只要已打开的 Chrome 原窗口时使用 attach；attach 要求所选资料唯一活跃并允许远程调试，失败必须如实报告。"
        "click/fill 的 ref 必须来自同一 Playwright 会话的最新 snapshot。"
        "用户选择人类操作后，再使用桌面观察、点击、输入或回车；当前任务内不要重复询问已经明确的选择。"
        "launch_app 只用于 Brain 已判断目标是本地应用并明确给出 arguments.app 的情况；"
        "click、type_text、key_press 都必须在 arguments.target_app 中写明要切换并操作的应用；"
        "QQ/微信的消息输入 type_text 必须同时携带经过观察确认的 intended_chat 与输入框 ax_ref；"
        "只有 ax_ref 内签名 input_kind=search_field 的搜索框可不带 intended_chat，"
        "清空或替换输入框使用同一个 type_text 并设置 replace_existing=true，且必须携带最新 ax_ref；"
        "发送 Enter 必须携带同一 intended_chat、expected_text 与从输入阶段继承的 input_ax_ref；"
        "普通 macOS GUI 应用会优先尝试返回 Accessibility affordance；目标带 ax_ref 时必须原样复制，"
        "click 不再需要 x/y，type_text 可携带输入元素的 ax_ref 并由执行器先聚焦；没有 ax_ref 时才使用坐标点击。"
        "发送/确认可使用 key_press enter。"
        "文件动作只能使用相对项目根目录或允许根目录内的路径；file_read 和 file_list 也要审批，file_delete 不递归，file_copy/file_move 不覆盖已有目标，file_write 覆盖已有文件时必须显式写 overwrite=true。"
        "请重新选择一个下一步 JSON：observe、propose_act playwright、propose_act launch_app、propose_act click、propose_act type_text、propose_act key_press enter、propose_act 文件动作，"
        "或带 terminal goal.status 的 say/stop。"
    )
