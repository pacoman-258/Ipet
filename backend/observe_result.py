from __future__ import annotations

import re
from typing import Any, Callable


AnalyzerFactory = Callable[[dict[str, Any]], Any]
_OCR_ACTION_TERMS = (
    "点击",
    "打开",
    "进入",
    "选择",
    "入口",
    "会话",
    "联系人",
    "click",
    "open",
    "select",
)
_OCR_GENERIC_LABELS = {
    "qq",
    "微信",
    "wechat",
    "music",
    "音乐",
    "点击",
    "点开",
    "打开",
    "进入",
    "选择",
    "搜索",
    "查找",
    "会话",
    "联系人",
    "聊天",
    "输入框",
    "编辑框",
    "入口",
    "发送",
    "click",
    "open",
    "enter",
    "select",
    "search",
    "find",
    "thread",
    "contact",
    "chat",
    "input",
    "send",
    "按钮",
    "button",
    "窗口",
    "window",
}


def _normalized_ocr_search_text(value: object) -> str:
    return "".join(
        char
        for char in str(value or "").casefold()
        if char.isalnum()
    )


def _local_ocr_screen_bounds(frame: dict[str, Any]) -> dict[str, int]:
    layout = (
        frame.get("display_layout")
        if isinstance(frame.get("display_layout"), list)
        else []
    )
    entries = [item for item in layout if isinstance(item, dict)]
    if not entries:
        return {}
    try:
        min_x = min(int(round(float(item.get("x") or 0))) for item in entries)
        min_y = min(int(round(float(item.get("y") or 0))) for item in entries)
        max_x = max(
            int(round(float(item.get("x") or 0)))
            + int(round(float(item.get("width") or 0)))
            for item in entries
        )
        max_y = max(
            int(round(float(item.get("y") or 0)))
            + int(round(float(item.get("height") or 0)))
            for item in entries
        )
    except (TypeError, ValueError):
        return {}
    width = max(0, max_x - min_x)
    height = max(0, max_y - min_y)
    if width <= 0 or height <= 0:
        return {}
    return {
        "x": min_x,
        "y": min_y,
        "width": width,
        "height": height,
    }


def _local_ocr_match_kind(query: str) -> str:
    folded = str(query or "").casefold()
    if any(term in folded for term in ("搜索", "查找", "search", "find")):
        return "search_field"
    if any(term in folded for term in ("会话", "联系人", "聊天", "contact", "thread")):
        return "chat_thread"
    if any(term in folded for term in ("输入框", "编辑框", "input", "editor")):
        return "input"
    return "visual_text"


def build_local_ocr_search(
    frame: dict[str, Any],
    query: object,
) -> dict[str, Any]:
    local_ocr = (
        frame.get("local_ocr")
        if isinstance(frame.get("local_ocr"), dict)
        else {}
    )
    items = (
        local_ocr.get("items")
        if isinstance(local_ocr.get("items"), list)
        else []
    )
    clean_query = " ".join(str(query or "").split())[:240]
    query_key = _normalized_ocr_search_text(clean_query)
    bounds = _local_ocr_screen_bounds(frame)
    if not items or not query_key or not bounds:
        return {
            "available": bool(items),
            "tool": "local_ocr_search",
            "query_source": "observe.ax_query",
            "query": clean_query,
            "matched_count": 0,
            "sufficient": False,
            "matches": [],
        }
    query_terms = {
        _normalized_ocr_search_text(term)
        for term in re.findall(
            r"[a-z0-9]{2,}|[\u3400-\u9fff]{2,}",
            clean_query.casefold(),
        )
    }
    query_terms.discard("")
    candidates: list[tuple[int, float, dict[str, Any]]] = []
    seen: set[tuple[str, int, int]] = set()
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        label = " ".join(str(raw_item.get("text") or "").split())[:180]
        label_key = _normalized_ocr_search_text(label)
        box = raw_item.get("box") if isinstance(raw_item.get("box"), dict) else {}
        if (
            len(label_key) < 2
            or label_key in _OCR_GENERIC_LABELS
            or not box
        ):
            continue
        score = 0
        if label_key in query_key:
            score = 100 + min(60, len(label_key))
        else:
            for term in query_terms:
                if (
                    len(term) >= 2
                    and term not in _OCR_GENERIC_LABELS
                    and (term in label_key or label_key in term)
                ):
                    score = max(score, 60 + min(40, min(len(term), len(label_key))))
        if score <= 0:
            continue
        try:
            normalized_x = float(box.get("x"))
            normalized_y = float(box.get("y"))
            normalized_width = float(box.get("width"))
            normalized_height = float(box.get("height"))
            confidence = max(
                0.0,
                min(1.0, float(raw_item.get("confidence") or 0.0)),
            )
        except (TypeError, ValueError):
            continue
        center_x = int(
            round(
                bounds["x"]
                + (normalized_x + normalized_width / 2) * bounds["width"]
            )
        )
        center_y = int(
            round(
                bounds["y"]
                + (1 - normalized_y - normalized_height / 2)
                * bounds["height"]
            )
        )
        identity = (label_key, center_x, center_y)
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(
            (
                score,
                confidence,
                {
                    "label": label,
                    "confidence": round(confidence, 4),
                    "location": {
                        "x": center_x,
                        "y": center_y,
                        "coordinate_space": "macos_screen_points",
                    },
                },
            )
        )
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2]["label"]))
    best_score = candidates[0][0] if candidates else 0
    best = [item for item in candidates if item[0] == best_score]
    unique = (
        len(best) == 1
        and best_score >= 100
        and best[0][1] >= 0.55
    )
    action_query = any(
        term in clean_query.casefold()
        for term in _OCR_ACTION_TERMS
    )
    kind = _local_ocr_match_kind(clean_query)
    matches: list[dict[str, Any]] = []
    for score, _confidence, item in candidates[:8]:
        match = {**item, "score": score, "kind": kind}
        if unique and score == best_score and action_query:
            match["supports"] = ["click"]
        else:
            match.pop("location", None)
        matches.append(match)
    return {
        "available": True,
        "tool": "local_ocr_search",
        "query_source": "observe.ax_query",
        "source": "macos-vision-ocr",
        "query": clean_query,
        "matched_count": len(candidates),
        "best_match_count": len(best),
        "sufficient": unique,
        "actionable": bool(unique and action_query),
        "matches": matches,
    }


def enrich_observation_frame_with_local_ocr(
    frame: dict[str, Any],
    query: object,
    *,
    analyzer_class: AnalyzerFactory,
) -> dict[str, Any]:
    original = dict(frame)
    analyzer = analyzer_class(
        {
            "enabled": True,
            "provider": "macos_vision_ocr",
            "timeout_sec": 8.0,
            "max_text_chars": 2_000,
            "max_image_bytes": 8_000_000,
        }
    )
    result = analyzer.enrich_payload(original)
    analyzed = result.payload if isinstance(result.payload, dict) else {}
    search = build_local_ocr_search(analyzed, query)
    safe = dict(original)
    safe["local_ocr_analysis"] = {
        "provider": "macos_vision_ocr",
        "status": str(result.status.get("status") or ""),
        "last_error": str(result.status.get("last_error") or "")[:160],
        "item_count": len(
            (analyzed.get("local_ocr") or {}).get("items")
            if isinstance(analyzed.get("local_ocr"), dict)
            and isinstance((analyzed.get("local_ocr") or {}).get("items"), list)
            else []
        ),
    }
    if search.get("matched_count"):
        safe["local_ocr_search"] = search
    return safe


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
