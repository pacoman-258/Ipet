from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from brain.decisions import BrainDecision


_DESKTOP_SURFACE_TARGETS = {
    "desktop",
    "screen",
    "currentdesktop",
    "currentscreen",
    "currentapp",
    "frontmostapp",
    "activeapp",
    "macosdesktop",
    "桌面",
    "屏幕",
    "当前桌面",
    "当前屏幕",
    "当前应用",
    "前台应用",
    "整个桌面",
    "所有应用",
}


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
    enrich_observation_frame_with_local_ocr: Callable[
        [dict[str, Any], str],
        dict[str, Any],
    ] = lambda frame, _query: frame


def _accessibility_search_needs_visual_fallback(
    accessibility: dict[str, Any],
) -> bool:
    search = (
        accessibility.get("search")
        if isinstance(accessibility.get("search"), dict)
        else {}
    )
    return (
        search.get("sufficient") is False
        and search.get("visual_fallback_required") is not False
    )


def _concrete_target_app(value: object) -> str:
    target_app = " ".join(str(value or "").split())[:160]
    key = "".join(
        char
        for char in target_app.casefold()
        if char.isalnum()
    )
    return "" if key in _DESKTOP_SURFACE_TARGETS else target_app


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
    decision_payload = decision.payload if isinstance(decision.payload, dict) else {}
    target_app = _concrete_target_app(decision_payload.get("target_app"))
    ax_query = str(decision_payload.get("ax_query") or "").strip()[:240]
    initial_ax_query = str(
        decision_payload.get("observe_prompt")
        or decision_payload.get("question")
        or target
    ).strip()[:240]
    capture_payload: dict[str, Any] = {
        "mode": "desktop_survey",
        "target_hint": target,
        "target": target,
        "target_app": target_app,
        "capture_scope": "application" if target_app else "desktop",
        "accessibility_enabled": bool(human_ops_config.get("accessibility", True)),
    }
    if target_app:
        capture_payload.update(
            {
                "accessibility_query": ax_query or initial_ax_query,
                "accessibility_cache_mode": "prefer_cache" if ax_query else "refresh",
                "accessibility_result_limit": 16,
                # Music and other deep collection views regularly need just
                # over four seconds to materialize their final AX child. Six
                # seconds keeps exhaustive reads complete while the desktop
                # router still has room for one focused retry.
                "accessibility_timeout_sec": 6.0,
            }
        )
    result = await deps.send_desktop_command(
        "active_vision_capture",
        capture_payload,
        timeout_sec=18 if target_app else 14,
    )
    frame = result.get("frame") if isinstance(result.get("frame"), dict) else {}
    accessibility = frame.get("accessibility") if isinstance(frame.get("accessibility"), dict) else {}
    focus_verification = (
        frame.get("focus_verification")
        if isinstance(frame.get("focus_verification"), dict)
        else {}
    )
    if (
        target_app
        and bool(accessibility.get("usable"))
        and str(frame.get("capture_backend") or "") == "macos_accessibility"
        and _accessibility_search_needs_visual_fallback(accessibility)
        # New desktop runtimes own the focused visual fallback atomically.
        # A verified failure must not trigger a second 14-second command.
        and not focus_verification
    ):
        visual_payload = {**capture_payload, "accessibility_enabled": False}
        visual_result = await deps.send_desktop_command(
            "active_vision_capture",
            visual_payload,
            timeout_sec=18,
        )
        visual_frame = (
            visual_result.get("frame")
            if isinstance(visual_result.get("frame"), dict)
            else {}
        )
        visual_frame = dict(visual_frame)
        visual_frame["accessibility"] = accessibility
        visual_frame["accessibility_fallback"] = {
            "reason": str(
                (accessibility.get("search") or {}).get("insufficiency_reason")
                if isinstance(accessibility.get("search"), dict)
                else ""
            ).strip()
            or "insufficient_ax_search",
            "ax_snapshot_id": str(accessibility.get("snapshot_id") or ""),
        }
        result = {
            **visual_result,
            "frame": visual_frame,
            "trace": {
                "accessibility": result.get("trace")
                if isinstance(result.get("trace"), dict)
                else {},
                "visual_fallback": visual_result.get("trace")
                if isinstance(visual_result.get("trace"), dict)
                else {},
            },
        }
        frame = visual_frame
    frame = deps.frame_with_observe_prompt(frame, decision, target)
    if (
        target_app
        and str(frame.get("data_url") or "").startswith("data:image/")
        and _accessibility_search_needs_visual_fallback(
            frame.get("accessibility")
            if isinstance(frame.get("accessibility"), dict)
            else {}
        )
    ):
        frame = deps.enrich_observation_frame_with_local_ocr(
            frame,
            ax_query or initial_ax_query,
        )
    coordinate_context = deps.observe_coordinate_context_from_frame(frame)
    analyzer_config = deps.observe_model_analyzer_config(human_ops_config)
    accessibility = frame.get("accessibility") if isinstance(frame.get("accessibility"), dict) else {}
    local_ocr_search = (
        frame.get("local_ocr_search")
        if isinstance(frame.get("local_ocr_search"), dict)
        else {}
    )
    if bool(accessibility.get("usable")) and str(frame.get("capture_backend") or "") == "macos_accessibility":
        result = {**result, "frame": frame}
        text = deps.observation_text_from_result(result) or str(accessibility.get("text") or "").strip()
        if not text:
            text = "已通过 macOS Accessibility 读取当前应用结构。"
        computer_use_context = deps.infer_computer_use_context(text, frame, target)
        unknowns = list(frame.get("unknowns") if isinstance(frame.get("unknowns"), list) else [])
        return {
            "text": text,
            "frame": frame,
            "trace": result.get("trace") if isinstance(result.get("trace"), dict) else {},
            "analysis_route": "structured",
            "coordinate_context": coordinate_context,
            "surface": computer_use_context["surface"],
            "affordances": computer_use_context["affordances"],
            **({"chat_context": computer_use_context["chat_context"]} if computer_use_context.get("chat_context") else {}),
            **({"ax_search": computer_use_context["ax_search"]} if computer_use_context.get("ax_search") else {}),
            "observations": frame.get("observations") if isinstance(frame.get("observations"), list) else [],
            "unknowns": unknowns,
        }
    if bool(local_ocr_search.get("sufficient")):
        labels = [
            str(item.get("label") or "").strip()
            for item in (
                local_ocr_search.get("matches")
                if isinstance(local_ocr_search.get("matches"), list)
                else []
            )
            if isinstance(item, dict) and str(item.get("label") or "").strip()
        ][:1]
        text = (
            "本机 macOS Vision OCR 已在目标应用窗口中找到唯一关键词匹配："
            + "、".join(labels)
            + "。"
        )
        computer_use_context = deps.infer_computer_use_context(
            text,
            frame,
            target,
        )
        return {
            "text": text,
            "frame": frame,
            "trace": result.get("trace") if isinstance(result.get("trace"), dict) else {},
            "analysis_route": "structured",
            "coordinate_context": coordinate_context,
            "surface": computer_use_context["surface"],
            "affordances": computer_use_context["affordances"],
            **(
                {"chat_context": computer_use_context["chat_context"]}
                if computer_use_context.get("chat_context")
                else {}
            ),
            **(
                {"ax_search": computer_use_context["ax_search"]}
                if computer_use_context.get("ax_search")
                else {}
            ),
            **(
                {"visual_search": computer_use_context["visual_search"]}
                if computer_use_context.get("visual_search")
                else {}
            ),
            "observations": [],
            "unknowns": [],
        }
    if not analyzer_config.get("enabled"):
        result = {**result, "frame": frame}
        capture_backend = str(frame.get("capture_backend") or "screen_capture").strip() or "screen_capture"
        has_brain_image = str(frame.get("data_url") or "").startswith("data:image/")
        if has_brain_image:
            text = f"截图成功（{capture_backend}），当前截图将由 Brain 模型直接观察并决定下一步。"
            unknowns: list[str] = []
            analysis_route = "brain"
        else:
            text = f"截图成功（{capture_backend}），但截图数据不可用，Brain 还不能读取当前界面。"
            unknowns = ["captured image unavailable"]
            analysis_route = "unavailable"
        computer_use_context = deps.infer_computer_use_context(text, frame, target)
        return {
            "text": text,
            "frame": frame,
            "trace": result.get("trace") if isinstance(result.get("trace"), dict) else {},
            "analysis_route": analysis_route,
            "coordinate_context": coordinate_context,
            "surface": computer_use_context["surface"],
            "affordances": computer_use_context["affordances"],
            **({"chat_context": computer_use_context["chat_context"]} if computer_use_context.get("chat_context") else {}),
            **({"ax_search": computer_use_context["ax_search"]} if computer_use_context.get("ax_search") else {}),
            **({"visual_search": computer_use_context["visual_search"]} if computer_use_context.get("visual_search") else {}),
            "observations": frame.get("observations") if isinstance(frame.get("observations"), list) else [],
            "unknowns": unknowns,
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
        **({"ax_search": computer_use_context["ax_search"]} if computer_use_context.get("ax_search") else {}),
        **({"visual_search": computer_use_context["visual_search"]} if computer_use_context.get("visual_search") else {}),
        "observations": frame.get("observations") if isinstance(frame.get("observations"), list) else [],
        "unknowns": unknowns,
        **({"coordinate_status": coordinate_status} if coordinate_status else {}),
    }
