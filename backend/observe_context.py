from __future__ import annotations

import json
from typing import Any

from brain.decisions import BrainDecision
from brain.llm import GOOGLE_AISTUDIO_DEFAULT_MODEL, PROVIDER_CODEX, PROVIDER_GOOGLE_AISTUDIO, normalize_provider

from . import computer_use_context as _computer_use_context_helpers


def _coerce_int(value: Any, fallback: int = 0) -> int:
    return _computer_use_context_helpers._coerce_int(value, fallback)


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


def normalize_observed_click_coordinates(
    decision: BrainDecision,
    observation: dict[str, Any] | None,
) -> BrainDecision:
    if decision.kind.value != "propose_act":
        return decision
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    if str(payload.get("action_type") or "").strip() != "click":
        return decision
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    data = observation if isinstance(observation, dict) else {}
    frame = data.get("frame") if isinstance(data.get("frame"), dict) else {}
    if not frame:
        return decision

    next_arguments = dict(arguments)
    raw_space = str(arguments.get("coordinate_space") or "").strip().lower()
    if not raw_space:
        raw_space = "image_pixels" if str(data.get("analysis_route") or "") == "brain" else "macos_screen_points"
    image_spaces = {"image", "image_pixel", "image_pixels", "screenshot", "screenshot_pixels"}
    screen_spaces = {"macos_screen_points", "screen", "screen_points"}
    try:
        x = float(arguments.get("x"))
        y = float(arguments.get("y"))
    except (TypeError, ValueError):
        return decision

    bounds = screen_bounds_from_frame(frame)
    image = image_resolution_from_frame(frame)
    if raw_space in image_spaces:
        width = _coerce_int(image.get("width"))
        height = _coerce_int(image.get("height"))
        scale = coordinate_scale_for_frame(bounds, image)
        if not scale or width <= 0 or height <= 0 or not (0 <= x < width and 0 <= y < height):
            next_arguments.pop("x", None)
            next_arguments.pop("y", None)
            next_arguments["coordinate_error"] = "image coordinates are outside the captured screenshot"
        else:
            next_arguments["x"] = int(round(_coerce_int(bounds.get("x")) + x * scale["image_to_screen_x"]))
            next_arguments["y"] = int(round(_coerce_int(bounds.get("y")) + y * scale["image_to_screen_y"]))
            next_arguments["coordinate_space"] = "macos_screen_points"
            next_arguments["source_coordinate_space"] = "image_pixels"
    elif raw_space in screen_spaces:
        origin_x = _coerce_int(bounds.get("x"))
        origin_y = _coerce_int(bounds.get("y"))
        width = _coerce_int(bounds.get("width"))
        height = _coerce_int(bounds.get("height"))
        if width > 0 and height > 0 and not (
            origin_x <= x < origin_x + width and origin_y <= y < origin_y + height
        ):
            next_arguments.pop("x", None)
            next_arguments.pop("y", None)
            next_arguments["coordinate_error"] = "screen coordinates are outside the captured display bounds"
        else:
            next_arguments["x"] = int(round(x))
            next_arguments["y"] = int(round(y))
            next_arguments["coordinate_space"] = "macos_screen_points"
    else:
        next_arguments.pop("x", None)
        next_arguments.pop("y", None)
        next_arguments["coordinate_error"] = f"unsupported coordinate space: {raw_space}"

    goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else None
    return BrainDecision.propose_act("click", next_arguments, goal=goal)


def default_observe_prompt_for_request(user_text: str, target: str) -> str:
    text = str(user_text or target or "").strip()
    return (
        f"请观察当前屏幕与“{text or target or '当前任务'}”相关的可见证据。"
        "用自然语言报告相关窗口、文字、状态和可操作入口；只报告实际可见内容，"
        "不替 Brain 选择固定操作顺序，也不输出 JSON。"
    )


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
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    return payload.get("require_coordinates") is True


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
    if goal_requests_click_coordinate_followup(decision):
        active["require_coordinates"] = True
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
