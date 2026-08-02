from __future__ import annotations

import hashlib
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


def _route_decision(
    *,
    selected: str,
    reason: str,
    outcome: str,
    target_app: str,
    ax_query: str,
    frame: dict[str, Any],
    accessibility: dict[str, Any] | None = None,
    local_ocr_search: dict[str, Any] | None = None,
    click_requested: bool = False,
) -> dict[str, Any]:
    ax = accessibility if isinstance(accessibility, dict) else {}
    ax_search = ax.get("search") if isinstance(ax.get("search"), dict) else {}
    ocr = local_ocr_search if isinstance(local_ocr_search, dict) else {}
    attempts: list[str] = []
    if target_app and ax:
        attempts.append("ax")
    has_image = str(frame.get("data_url") or "").startswith("data:image/")
    capture_backend = str(frame.get("capture_backend") or "").strip()
    if has_image or capture_backend not in {"", "macos_accessibility", "unavailable"}:
        attempts.append("screenshot")
    if "local_ocr_analysis" in frame or ocr:
        attempts.append("local_ocr")
    if selected in {"brain_vision", "observe_model"}:
        attempts.append(selected)
    if selected == "ax" and "ax" not in attempts:
        attempts.append("ax")
    deduped_attempts = list(dict.fromkeys(attempts))
    query_text = str(ax_query or "").strip()
    result: dict[str, Any] = {
        "selected": str(selected or "unavailable"),
        "reason": str(reason or "route_unspecified")[:120],
        "outcome": str(outcome or "unknown")[:40],
        "intent": "click" if click_requested else "observe",
        "target_app": str(target_app or "")[:160],
        "attempted": deduped_attempts[:6],
        "query_fingerprint": (
            hashlib.sha256(query_text.encode("utf-8")).hexdigest()[:12]
            if query_text
            else ""
        ),
    }
    insufficiency_reason = str(ax_search.get("insufficiency_reason") or "").strip()
    if insufficiency_reason:
        result["ax_insufficiency_reason"] = insufficiency_reason[:120]
    if deduped_attempts and selected != deduped_attempts[0]:
        result["fallback_from"] = deduped_attempts[0]
    if ocr:
        result["ocr_actionable"] = bool(ocr.get("actionable"))
    return result


async def perform_human_ops_observe(
    decision: BrainDecision,
    human_ops_config: dict[str, Any],
    *,
    deps: HumanOpsObserveDependencies,
) -> dict[str, Any]:
    screen_capture_enabled = bool(human_ops_config.get("observe_screen", True))
    accessibility_enabled = bool(human_ops_config.get("accessibility", True))
    target = deps.observe_target_hint_from_decision(decision, "screen")
    decision_payload = decision.payload if isinstance(decision.payload, dict) else {}
    target_app = _concrete_target_app(decision_payload.get("target_app"))
    ax_query = str(decision_payload.get("ax_query") or "").strip()[:240]
    initial_ax_query = str(
        decision_payload.get("observe_prompt")
        or decision_payload.get("question")
        or target
    ).strip()[:240]
    if not screen_capture_enabled and not accessibility_enabled:
        return {
            "text": "截图观察和 Accessibility 读取权限均已关闭，请在设置页至少开启一项。",
            "observations": [],
            "unknowns": ["screen capture and accessibility disabled"],
            "route_decision": {
                "selected": "unavailable",
                "reason": "observation_permissions_disabled",
                "outcome": "unavailable",
                "intent": "observe",
                "target_app": "",
                "attempted": [],
                "query_fingerprint": "",
            },
        }
    if not screen_capture_enabled and not target_app:
        return {
            "text": "截图观察权限已关闭；纯 Accessibility 观察需要指定一个明确的目标应用。",
            "observations": [],
            "unknowns": ["accessibility observation requires target_app"],
            "route_decision": {
                "selected": "unavailable",
                "reason": "accessibility_target_app_required",
                "outcome": "needs_target",
                "intent": "observe",
                "target_app": "",
                "attempted": ["ax"],
                "query_fingerprint": "",
            },
        }
    capture_payload: dict[str, Any] = {
        "mode": "desktop_survey",
        "target_hint": target,
        "target": target,
        "target_app": target_app,
        "capture_scope": "application" if target_app else "desktop",
        "accessibility_enabled": accessibility_enabled,
    }
    if not screen_capture_enabled:
        capture_payload["screen_capture_enabled"] = False
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
        screen_capture_enabled
        and target_app
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
        ax_search = accessibility.get("search") if isinstance(accessibility.get("search"), dict) else {}
        ax_sufficient = ax_search.get("sufficient") is not False
        route_reason = (
            "usable_accessibility"
            if ax_sufficient
            else str(ax_search.get("insufficiency_reason") or "ax_requires_narrowing")
        )
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
            "route_decision": _route_decision(
                selected="ax",
                reason=route_reason,
                outcome="usable" if ax_sufficient else "needs_narrowing",
                target_app=target_app,
                ax_query=ax_query or initial_ax_query,
                frame=frame,
                accessibility=accessibility,
                click_requested=deps.observe_decision_requests_click(decision),
            ),
        }
    click_requested = deps.observe_decision_requests_click(decision)
    if bool(local_ocr_search.get("sufficient")) and (
        not click_requested or bool(local_ocr_search.get("actionable"))
    ):
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
            "route_decision": _route_decision(
                selected="local_ocr",
                reason="unique_local_ocr_match",
                outcome="actionable" if click_requested else "usable",
                target_app=target_app,
                ax_query=ax_query or initial_ax_query,
                frame=frame,
                accessibility=accessibility,
                local_ocr_search=local_ocr_search,
                click_requested=click_requested,
            ),
        }
    if not screen_capture_enabled:
        result = {**result, "frame": frame}
        text = "截图观察权限已关闭，Accessibility 未返回可用的目标应用结构。"
        unknowns = list(frame.get("unknowns") if isinstance(frame.get("unknowns"), list) else [])
        if "accessibility unavailable without screen fallback" not in unknowns:
            unknowns.append("accessibility unavailable without screen fallback")
        computer_use_context = deps.infer_computer_use_context(text, frame, target)
        return {
            "text": text,
            "frame": frame,
            "trace": result.get("trace") if isinstance(result.get("trace"), dict) else {},
            "analysis_route": "unavailable",
            "coordinate_context": coordinate_context,
            "surface": computer_use_context["surface"],
            "affordances": computer_use_context["affordances"],
            **({"chat_context": computer_use_context["chat_context"]} if computer_use_context.get("chat_context") else {}),
            **({"ax_search": computer_use_context["ax_search"]} if computer_use_context.get("ax_search") else {}),
            "observations": frame.get("observations") if isinstance(frame.get("observations"), list) else [],
            "unknowns": unknowns,
            "route_decision": _route_decision(
                selected="unavailable",
                reason="accessibility_unavailable_without_screen_fallback",
                outcome="unavailable",
                target_app=target_app,
                ax_query=ax_query or initial_ax_query,
                frame=frame,
                accessibility=accessibility,
                click_requested=click_requested,
            ),
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
            "route_decision": _route_decision(
                selected="brain_vision" if has_brain_image else "unavailable",
                reason=(
                    "screenshot_requires_brain_vision"
                    if has_brain_image
                    else "captured_image_unavailable"
                ),
                outcome="pending_model" if has_brain_image else "unavailable",
                target_app=target_app,
                ax_query=ax_query or initial_ax_query,
                frame=frame,
                accessibility=accessibility,
                local_ocr_search=local_ocr_search,
                click_requested=click_requested,
            ),
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
        "route_decision": _route_decision(
            selected="observe_model",
            reason="observe_model_analysis",
            outcome=(
                "insufficient"
                if coordinate_status.get("status") == "incomplete" or unknowns
                else "usable"
            ),
            target_app=target_app,
            ax_query=ax_query or initial_ax_query,
            frame=frame,
            accessibility=accessibility,
            local_ocr_search=local_ocr_search,
            click_requested=click_requested,
        ),
    }
