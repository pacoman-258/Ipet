from __future__ import annotations

import json
from typing import Any

from brain.decisions import BrainDecision
from brain.llm import GOOGLE_AISTUDIO_DEFAULT_MODEL, PROVIDER_CODEX, PROVIDER_GOOGLE_AISTUDIO, normalize_provider

from . import computer_use_context as _computer_use_context_helpers


def _coerce_int(value: Any, fallback: int = 0) -> int:
    return _computer_use_context_helpers._coerce_int(value, fallback)


def _looks_like_click_request(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    explicit_click = any(term in text for term in ("点击", "点一下", "点开", "click")) and any(
        target in text for target in _computer_use_context_helpers._DESKTOP_TARGET_TERMS
    )
    return explicit_click or _computer_use_context_helpers._looks_like_app_launch_request(text)


def image_resolution_from_frame(frame: dict[str, Any]) -> dict[str, int]:
    width = _coerce_int(frame.get("image_width") or frame.get("width"))
    height = _coerce_int(frame.get("image_height") or frame.get("height"))
    return {"width": width, "height": height} if width > 0 and height > 0 else {}


def screen_bounds_from_frame(frame: dict[str, Any]) -> dict[str, int]:
    layout = frame.get("display_layout") if isinstance(frame.get("display_layout"), list) else []
    bounds: list[dict[str, Any]] = [item for item in layout if isinstance(item, dict)]
    if bounds:
        try:
            min_x = min(int(item.get("x") or 0) for item in bounds)
            min_y = min(int(item.get("y") or 0) for item in bounds)
            max_x = max(int(item.get("x") or 0) + int(item.get("width") or 0) for item in bounds)
            max_y = max(int(item.get("y") or 0) + int(item.get("height") or 0) for item in bounds)
            width = max(0, max_x - min_x)
            height = max(0, max_y - min_y)
            if width > 0 and height > 0:
                return {"x": min_x, "y": min_y, "width": width, "height": height}
        except Exception:
            pass
    width = _coerce_int(frame.get("width"))
    height = _coerce_int(frame.get("height"))
    return {"x": 0, "y": 0, "width": width, "height": height} if width > 0 and height > 0 else {}


def screen_resolution_from_frame(frame: dict[str, Any]) -> dict[str, int]:
    bounds = screen_bounds_from_frame(frame)
    if not bounds:
        return {}
    return {"width": bounds["width"], "height": bounds["height"]}


def coordinate_scale_for_frame(
    screen_bounds: dict[str, int],
    image_resolution: dict[str, int],
) -> dict[str, float]:
    screen_width = _coerce_int(screen_bounds.get("width"))
    screen_height = _coerce_int(screen_bounds.get("height"))
    image_width = _coerce_int(image_resolution.get("width"))
    image_height = _coerce_int(image_resolution.get("height"))
    if screen_width <= 0 or screen_height <= 0 or image_width <= 0 or image_height <= 0:
        return {}
    return {
        "image_to_screen_x": screen_width / image_width,
        "image_to_screen_y": screen_height / image_height,
        "screen_to_image_x": image_width / screen_width,
        "screen_to_image_y": image_height / screen_height,
    }


def format_coordinate_scale(value: Any) -> str:
    try:
        return f"{float(value):.6f}".rstrip("0").rstrip(".")
    except Exception:
        return "unknown"


def observe_coordinate_context_from_frame(frame: dict[str, Any]) -> str:
    active = frame.get("active_observation") if isinstance(frame.get("active_observation"), dict) else {}
    image_resolution = active.get("image_resolution") if isinstance(active.get("image_resolution"), dict) else {}
    if not image_resolution:
        image_resolution = image_resolution_from_frame(frame)
    screen_bounds = active.get("screen_bounds") if isinstance(active.get("screen_bounds"), dict) else {}
    if not screen_bounds:
        screen_bounds = screen_bounds_from_frame(frame)
    coordinate_scale = active.get("coordinate_scale") if isinstance(active.get("coordinate_scale"), dict) else {}
    if not coordinate_scale:
        coordinate_scale = coordinate_scale_for_frame(screen_bounds, image_resolution)
    image_width = _coerce_int(image_resolution.get("width"))
    image_height = _coerce_int(image_resolution.get("height"))
    screen_x = _coerce_int(screen_bounds.get("x"))
    screen_y = _coerce_int(screen_bounds.get("y"))
    screen_width = _coerce_int(screen_bounds.get("width"))
    screen_height = _coerce_int(screen_bounds.get("height"))
    if image_width <= 0 or image_height <= 0 or screen_width <= 0 or screen_height <= 0 or not coordinate_scale:
        return ""
    image_to_screen_x = format_coordinate_scale(coordinate_scale.get("image_to_screen_x"))
    image_to_screen_y = format_coordinate_scale(coordinate_scale.get("image_to_screen_y"))
    screen_to_image_x = format_coordinate_scale(coordinate_scale.get("screen_to_image_x"))
    screen_to_image_y = format_coordinate_scale(coordinate_scale.get("screen_to_image_y"))
    return (
        "Observe coordinate context:\n"
        f"Attached image pixels: {image_width}x{image_height}.\n"
        f"macOS screen bounds: origin=({screen_x}, {screen_y}), size={screen_width}x{screen_height} points.\n"
        f"image-to-screen scale: x={image_to_screen_x}, y={image_to_screen_y}; "
        f"screen-to-image scale: x={screen_to_image_x}, y={screen_to_image_y}.\n"
        "If observe coordinates are screenshot/image pixels, convert them before propose_act: "
        f"screen_x = {screen_x} + image_x * {image_to_screen_x}; "
        f"screen_y = {screen_y} + image_y * {image_to_screen_y}. "
        "propose_act x/y must be macOS screen coordinates."
    )


def default_observe_prompt_for_request(user_text: str, target: str) -> str:
    text = str(user_text or target or "").strip()
    if _looks_like_click_request(text):
        return f"我需要找到“{text or target}”对应的可点击目标。请观看屏幕截图，用自然语言告诉我它是否可见、可见依据，以及可点击中心点的 macOS 屏幕坐标 x 和 y。"
    if _computer_use_context_helpers._looks_like_chat_reply_request(text):
        return (
            f"我需要完成“{text or target}”这个聊天回复任务。请观察当前屏幕，用自然语言说明当前是否在微信或聊天界面，"
            "目标联系人或会话是否可见，最近聊天内容是什么，是否有搜索框、联系人条目、聊天输入框或发送入口可用。"
        )
    if any(term in text for term in ("读", "文字", "内容", "写着", "显示")):
        return f"我需要读取当前屏幕中和“{text or target}”相关的可见文字和内容。请只根据截图用自然语言回答。"
    if any(term in text for term in ("是否", "有没有", "状态", "成功", "失败", "完成")):
        return f"我需要判断当前屏幕状态是否满足“{text or target}”。请根据可见界面给出结论和依据。"
    if any(term in text.lower() for term in ("dock", "程序坞", "app", "应用", "按钮", "图标", "窗口", "输入框")):
        return f"我需要找到屏幕上和“{text or target}”相关的界面目标。请描述它的位置、可见文字或图标依据；不需要输出 JSON。"
    return f"我需要了解当前屏幕和“{text or target or '当前任务'}”相关的主要可见内容。请概括窗口、文字和状态。"


def goal_text_for_observe(decision: BrainDecision) -> str:
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else {}
    parts: list[str] = []
    for key in ("objective", "stage", "evidence", "missing", "next"):
        value = goal.get(key)
        if isinstance(value, list):
            parts.extend(str(item or "").strip() for item in value)
        elif isinstance(value, dict):
            parts.append(json.dumps(value, ensure_ascii=False))
        else:
            parts.append(str(value or "").strip())
    return " ".join(part for part in parts if part)


def goal_requests_click_coordinate_followup(decision: BrainDecision) -> bool:
    text = goal_text_for_observe(decision).lower()
    if not text:
        return False
    wants_coordinates = any(term in text for term in ("坐标", "中心点", "x/y", "x 和 y", "coordinate"))
    wants_click = any(
        term in text
        for term in (
            "click",
            "点击",
            "click_to_open",
            "launch_app",
            "打开",
            "图标",
            "dock",
            "程序坞",
            "按钮",
            "输入框",
        )
    )
    return wants_coordinates and wants_click


def coordinate_followup_target_from_goal(decision: BrainDecision, user_text: str, target: str) -> str:
    goal_text = goal_text_for_observe(decision)
    app_label = _computer_use_context_helpers._infer_app_label(goal_text, user_text)
    if app_label:
        if any(term in goal_text.lower() for term in ("dock", "程序坞", "图标", "launch_app", "click_to_open")):
            return f"{app_label}图标"
        return app_label
    contact_label = _computer_use_context_helpers._infer_contact_label(goal_text, user_text)
    if contact_label:
        return f"{contact_label}联系人或会话条目"
    clean_target = str(target or "").strip()
    if clean_target and clean_target.lower() != "screen":
        return clean_target[:80]
    goal = decision.payload.get("goal") if isinstance(decision.payload, dict) and isinstance(decision.payload.get("goal"), dict) else {}
    objective = str(goal.get("objective") or user_text or "目标").strip()
    return objective[:80] or "目标"


def observe_target_hint_from_decision(decision: BrainDecision, fallback: str = "screen") -> str:
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    target = str(payload.get("target") or fallback or "screen").strip() or "screen"
    goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else {}
    objective = str(goal.get("objective") or "").strip()
    if objective and target.lower() in {"screen", "当前屏幕", "屏幕"}:
        return objective[:220]
    return (target or objective or fallback or "screen")[:220]


def observe_prompt_from_decision(decision: BrainDecision, user_text: str) -> str:
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    target = str(payload.get("target") or user_text or "screen").strip() or "screen"
    observe_prompt = str(payload.get("observe_prompt") or payload.get("question") or "").strip()
    if not observe_prompt:
        if goal_requests_click_coordinate_followup(decision):
            coordinate_target = coordinate_followup_target_from_goal(decision, user_text, target)
            observe_prompt = (
                f"请只定位“{coordinate_target}”的可点击中心点，返回完整 macOS 屏幕坐标 x 和 y，"
                "格式类似 x=123, y=456。不要描述其他内容；如果目标不可见或不确定，请明确说不可见或不确定，不要编造坐标。"
            )
        else:
            observe_prompt = default_observe_prompt_for_request(user_text, target)
    return observe_prompt[:700]


def frame_with_observe_prompt(frame: dict[str, Any], decision: BrainDecision, user_text: str) -> dict[str, Any]:
    enriched = dict(frame or {})
    active = enriched.get("active_observation") if isinstance(enriched.get("active_observation"), dict) else {}
    active = dict(active)
    target_hint = observe_target_hint_from_decision(decision, user_text)
    active["target_hint"] = target_hint
    active["observe_prompt"] = observe_prompt_from_decision(decision, target_hint)
    image_resolution = image_resolution_from_frame(enriched)
    if image_resolution:
        active["image_resolution"] = image_resolution
    screen_bounds = screen_bounds_from_frame(enriched)
    if screen_bounds:
        active["screen_bounds"] = screen_bounds
        active["screen_resolution"] = {"width": screen_bounds["width"], "height": screen_bounds["height"]}
        active["coordinate_space"] = "macos_screen_points"
    coordinate_scale = coordinate_scale_for_frame(screen_bounds, image_resolution)
    if coordinate_scale:
        active["coordinate_scale"] = coordinate_scale
    enriched["active_observation"] = active
    return enriched


def observe_model_analyzer_config(human_ops_config: dict[str, Any]) -> dict[str, Any]:
    observe_model = (
        human_ops_config.get("observe_model") if isinstance(human_ops_config.get("observe_model"), dict) else {}
    )
    if not bool(observe_model.get("enabled", False)):
        return {"enabled": False, "provider": "none"}
    provider = normalize_provider(observe_model.get("provider"))
    if provider == "ollama":
        analyzer_provider = "local_vlm"
    elif provider == "openai_compatible":
        analyzer_provider = "openai_compatible_vlm"
    elif provider == PROVIDER_GOOGLE_AISTUDIO:
        analyzer_provider = "google_aistudio_vlm"
    elif provider == PROVIDER_CODEX:
        analyzer_provider = "codex_vlm"
    else:
        return {"enabled": False, "provider": "none"}
    model_name = str(observe_model.get("model_name") or observe_model.get("model") or "").strip()
    if provider == PROVIDER_GOOGLE_AISTUDIO and not model_name:
        model_name = GOOGLE_AISTUDIO_DEFAULT_MODEL
    elif provider == PROVIDER_CODEX and not model_name:
        model_name = "default"
    try:
        timeout_sec = float(observe_model.get("timeout_sec", 90.0))
    except Exception:
        timeout_sec = 90.0
    timeout_sec = max(5.0, min(300.0, timeout_sec))
    return {
        "enabled": True,
        "provider": analyzer_provider,
        "base_url": str(observe_model.get("model_endpoint") or observe_model.get("endpoint") or "").strip(),
        "model": model_name,
        "api_key": str(observe_model.get("api_key") or "").strip(),
        "timeout_sec": timeout_sec,
        "max_observations": 4,
        "image_detail": "low",
    }
