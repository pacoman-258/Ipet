from __future__ import annotations

from typing import Any, Callable


AnalyzerFactory = Callable[[dict[str, Any]], Any]


def observation_text_from_result(result: dict[str, Any]) -> str:
    frame = result.get("frame") if isinstance(result.get("frame"), dict) else {}
    capture_backend = str(frame.get("capture_backend") or "").strip()
    has_screenshot = str(frame.get("data_url") or "").startswith("data:image/")
    analysis = frame.get("analysis") if isinstance(frame.get("analysis"), dict) else {}
    observations = frame.get("observations") if isinstance(frame.get("observations"), list) else []
    observe_answer = str(frame.get("observe_answer") or "").strip()
    if observe_answer:
        return observe_answer

    claims: list[str] = []
    for item in observations[:3]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("claim") or item.get("summary") or "").strip()
        if text:
            claims.append(text)
    if claims:
        return "我看到：" + "；".join(claims)

    active = frame.get("active_observation") if isinstance(frame.get("active_observation"), dict) else {}
    foreground = frame.get("foreground_app") if isinstance(frame.get("foreground_app"), dict) else {}
    app_name = str(foreground.get("name") or active.get("app_name") or "").strip()
    window_title = str(foreground.get("window_title") or active.get("window_title") or "").strip()
    if app_name or window_title:
        parts = [part for part in (app_name, window_title) if part]
        return "我看了一下屏幕，前台大概是：" + " · ".join(parts)

    unknowns = []
    for source in (frame, active, result.get("trace") if isinstance(result.get("trace"), dict) else {}):
        values = source.get("unknowns") if isinstance(source.get("unknowns"), list) else []
        unknowns.extend(str(item or "").strip() for item in values if str(item or "").strip())
    analysis_error = str(analysis.get("last_error") or "").strip()
    if analysis_error:
        unknowns.append(analysis_error)
    unknowns = [
        item
        for item in unknowns
        if "无法读取 macOS 前台应用元数据" not in item
        and "active vision metadata observation failed" not in item
        and "osascript" not in item.lower()
    ]
    if unknowns:
        return "我试着观察了屏幕，但还不能确认内容：" + "；".join(unknowns[:2])

    if has_screenshot and bool(analysis.get("enabled")):
        provider = str(analysis.get("provider") or "observe 模型").strip()
        status = str(analysis.get("status") or "").strip()
        if status == "ok":
            return f"我已经截取了当前屏幕（{capture_backend or 'screen_capture'}），也调用了 observe 模型（{provider}），但模型没有返回可见内容。"
        if status:
            return f"我已经截取了当前屏幕（{capture_backend or 'screen_capture'}），但 observe 模型（{provider}）状态为 {status}，还没有可用分析结果。"

    if has_screenshot:
        method = f"（{capture_backend}）" if capture_backend else ""
        return f"我已经截取了当前屏幕{method}，但还没有提取到明确内容。"
    return "我看了一下屏幕，但还没有提取到明确内容。"


def enrich_observation_frame_with_model(
    frame: dict[str, Any],
    analyzer_config: dict[str, Any],
    *,
    analyzer_class: AnalyzerFactory,
) -> dict[str, Any]:
    if not analyzer_config.get("enabled"):
        return frame
    result = analyzer_class(analyzer_config).enrich_payload(frame)
    return result.payload if isinstance(result.payload, dict) else frame
