from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from brain.decisions import BrainDecision


@dataclass(frozen=True)
class HumanOpsObserveDependencies:
    send_desktop_command: Callable[..., Any]
    observe_target_hint_from_decision: Callable[..., str]
    frame_with_observe_prompt: Callable[..., dict[str, Any]]
    observe_coordinate_context_from_frame: Callable[[dict[str, Any]], str]
    observe_model_analyzer_config: Callable[[dict[str, Any]], dict[str, Any]]
    looks_like_click_request: Callable[[str], bool]
    infer_computer_use_context: Callable[..., dict[str, Any]]
    enrich_observation_frame_with_model: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    observation_text_from_result: Callable[[dict[str, Any]], str]
    observe_decision_requests_click: Callable[[BrainDecision], bool]
    observe_click_coordinate_status: Callable[..., dict[str, str]]
    goal_requests_click_coordinate_followup: Callable[[BrainDecision], bool]
    click_coordinate_observe_failure_text: Callable[[str], str]


async def perform_human_ops_observe(
    decision: BrainDecision,
    human_ops_config: dict[str, Any],
    *,
    deps: HumanOpsObserveDependencies,
) -> dict[str, Any]:
    if not bool(human_ops_config.get("observe_screen", True)):
        return {
            "text": "看屏幕权限已关闭，请在设置页开启 Human Ops 的观察权限。",
            "observations": [],
            "unknowns": ["observe_screen disabled"],
        }
    target = deps.observe_target_hint_from_decision(decision, "screen")
    result = await deps.send_desktop_command(
        "active_vision_capture",
        {"mode": "desktop_survey", "target_hint": target, "target": target},
        timeout_sec=14,
    )
    frame = result.get("frame") if isinstance(result.get("frame"), dict) else {}
    frame = deps.frame_with_observe_prompt(frame, decision, target)
    coordinate_context = deps.observe_coordinate_context_from_frame(frame)
    analyzer_config = deps.observe_model_analyzer_config(human_ops_config)
    if not analyzer_config.get("enabled"):
        result = {**result, "frame": frame}
        capture_backend = str(frame.get("capture_backend") or "screen_capture").strip() or "screen_capture"
        if deps.looks_like_click_request(target):
            text = "我已经截取了当前屏幕，但这一步需要配置 Human Ops observe 模型来读取图像并返回可点击坐标。"
        else:
            text = f"截图成功（{capture_backend}），但 Human Ops observe 模型未启用，所以目前只能证明截图成功，不能读取截图内容。"
        computer_use_context = deps.infer_computer_use_context(text, frame, target)
        return {
            "text": text,
            "frame": frame,
            "trace": result.get("trace") if isinstance(result.get("trace"), dict) else {},
            "coordinate_context": coordinate_context,
            "surface": computer_use_context["surface"],
            "affordances": computer_use_context["affordances"],
            **({"chat_context": computer_use_context["chat_context"]} if computer_use_context.get("chat_context") else {}),
            "observations": frame.get("observations") if isinstance(frame.get("observations"), list) else [],
            "unknowns": ["observe_model disabled"],
        }
    frame = deps.enrich_observation_frame_with_model(frame, human_ops_config)
    coordinate_context = deps.observe_coordinate_context_from_frame(frame)
    result = {**result, "frame": frame}
    text = deps.observation_text_from_result(result)
    unknowns = list(frame.get("unknowns") if isinstance(frame.get("unknowns"), list) else [])
    coordinate_status: dict[str, str] = {}
    if deps.observe_decision_requests_click(decision):
        coordinate_status = deps.observe_click_coordinate_status(
            text,
            require_coordinates=deps.goal_requests_click_coordinate_followup(decision),
        )
    if coordinate_status.get("status") == "incomplete":
        text = deps.click_coordinate_observe_failure_text(text)
        if "observe_coordinate_incomplete" not in unknowns:
            unknowns.append("observe_coordinate_incomplete")
    computer_use_context = deps.infer_computer_use_context(text, frame, target)
    return {
        "text": text,
        "frame": frame,
        "trace": result.get("trace") if isinstance(result.get("trace"), dict) else {},
        "coordinate_context": coordinate_context,
        "surface": computer_use_context["surface"],
        "affordances": computer_use_context["affordances"],
        **({"chat_context": computer_use_context["chat_context"]} if computer_use_context.get("chat_context") else {}),
        "observations": frame.get("observations") if isinstance(frame.get("observations"), list) else [],
        "unknowns": unknowns,
        **({"coordinate_status": coordinate_status} if coordinate_status else {}),
    }
