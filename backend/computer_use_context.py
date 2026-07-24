from __future__ import annotations

import json
import re
from typing import Any


_DESKTOP_ACTION_TERMS = (
    "点击",
    "点一下",
    "点开",
    "打开",
    "启动",
    "切到",
    "选择",
    "按下",
    "播放",
    "暂停",
    "下一首",
    "上一首",
)
_DESKTOP_TARGET_TERMS = (
    "dock",
    "程序坞",
    "app",
    "应用",
    "窗口",
    "按钮",
    "图标",
    "输入框",
    "设置",
    "音乐",
    "歌曲",
    "歌单",
    "访达",
    "文件",
)
_DESKTOP_EXPLANATION_TERMS = ("怎么", "如何", "为什么", "原理", "介绍", "解释")
_APP_LAUNCH_TERMS = ("打开", "启动", "切到", "open ", "launch ")
_CHAT_REPLY_ACTION_TERMS = ("回复", "回消息", "发送", "发给", "输入", "键入", "打字")
_CHAT_SURFACE_TERMS = ("微信", "wechat", "qq", "聊天", "联系人", "会话", "消息", "私信")
_COMMON_APP_LABELS = (
    "微信",
    "wechat",
    "qq",
    "音乐",
    "music",
    "chrome",
    "safari",
    "系统设置",
    "设置",
    "finder",
    "访达",
)
_APP_LAUNCH_ALIASES = {
    "微信": "WeChat",
    "wechat": "WeChat",
    "qq": "QQ",
    "腾讯qq": "QQ",
    "音乐": "Music",
    "苹果音乐": "Music",
    "apple music": "Music",
    "music": "Music",
    "网易云音乐": "NeteaseMusic",
    "网易云": "NeteaseMusic",
    "腾讯会议": "TencentMeeting",
    "库乐队": "GarageBand",
    "keynote讲演": "Keynote",
    "numbers表格": "Numbers",
    "pages文稿": "Pages",
    "safari浏览器": "Safari",
    "imovie 剪辑": "iMovie",
    "imovie剪辑": "iMovie",
    "谷歌浏览器": "Google Chrome",
    "google chrome": "Google Chrome",
    "chrome": "Google Chrome",
    "苹果浏览器": "Safari",
    "safari": "Safari",
    "系统设置": "System Settings",
    "设置": "System Settings",
    "访达": "Finder",
    "finder": "Finder",
}
_DESKTOP_OBSERVE_TERMS = (
    "看屏幕",
    "观察屏幕",
    "看看屏幕",
    "读屏幕",
    "当前屏幕",
    "屏幕上",
)

_COORDINATE_X_RE = re.compile(r"(?i)(?:^|[^\w])x\s*[:=：=]\s*(-?\d+(?:\.\d+)?)?")
_COORDINATE_Y_RE = re.compile(r"(?i)(?:^|[^\w])y\s*[:=：=]\s*(-?\d+(?:\.\d+)?)?")


def _coerce_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


def _looks_like_desktop_action_request(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    if not text:
        return False
    if any(term in text for term in _DESKTOP_EXPLANATION_TERMS) and not any(prefix in text for prefix in ("帮我", "请", "试试")):
        return False
    has_action = any(term in text for term in _DESKTOP_ACTION_TERMS)
    has_target = any(term in text for term in _DESKTOP_TARGET_TERMS)
    return (has_action and (has_target or _looks_like_app_launch_request(text))) or _looks_like_chat_reply_request(text)


def _looks_like_desktop_observe_request(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    return bool(text) and any(term in text for term in _DESKTOP_OBSERVE_TERMS)


def _looks_like_app_launch_request(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    if not text:
        return False
    if any(term in text for term in _DESKTOP_EXPLANATION_TERMS) and not any(prefix in text for prefix in ("帮我", "请", "试试")):
        return False
    if not any(term in text for term in _APP_LAUNCH_TERMS):
        return False
    if any(label in text for label in _COMMON_APP_LABELS):
        return True
    for term in ("打开", "启动", "切到"):
        if term in text:
            tail = text.split(term, 1)[1].strip(" ：:，,。 ")
            return 0 < len(tail) <= 80
    return any(text.startswith(term) and len(text.replace(term, "").strip()) > 0 for term in ("open ", "launch "))


def _app_launch_target(user_text: str) -> str:
    text = str(user_text or "").strip()
    match = re.search(r"(?i)(?:打开|启动|切到|open\s+|launch\s+)([^，,。；;\n]{1,80})", text)
    if not match:
        return ""
    target = re.split(r"(?:然后|并且|并|再|后)", match.group(1), maxsplit=1)[0]
    target = re.sub(r"(?i)(?:这个|一下|应用程序|应用|app)$", "", target.strip(" \t：:‘’“”\"'"))
    normalized = target.strip().lower()
    return _APP_LAUNCH_ALIASES.get(normalized, target.strip())


def _looks_like_chat_reply_request(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    if not text:
        return False
    if any(term in text for term in _DESKTOP_EXPLANATION_TERMS) and not any(prefix in text for prefix in ("帮我", "请", "试试")):
        return False
    return any(term in text for term in _CHAT_REPLY_ACTION_TERMS) and any(term in text for term in _CHAT_SURFACE_TERMS)


def _looks_like_click_request(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    explicit_click = any(term in text for term in ("点击", "点一下", "点开", "click")) and any(
        target in text for target in _DESKTOP_TARGET_TERMS
    )
    return explicit_click


def _has_partial_coordinate_pair(text: str) -> bool:
    value = str(text or "")
    x_matches = list(_COORDINATE_X_RE.finditer(value))
    y_matches = list(_COORDINATE_Y_RE.finditer(value))
    if not x_matches and not y_matches:
        return False
    has_x_value = any(match.group(1) for match in x_matches)
    has_y_value = any(match.group(1) for match in y_matches)
    return not (has_x_value and has_y_value)


def _observe_click_coordinate_status(observation_text: str, *, require_coordinates: bool = False) -> dict[str, str]:
    text = str(observation_text or "").strip()
    if not text:
        return {"status": "incomplete", "reason": "empty observe answer"}
    if _has_partial_coordinate_pair(text):
        return {"status": "incomplete", "reason": "missing complete x/y"}
    has_x = any(match.group(1) for match in _COORDINATE_X_RE.finditer(text))
    has_y = any(match.group(1) for match in _COORDINATE_Y_RE.finditer(text))
    if has_x and has_y:
        return {"status": "complete"}
    if any(term in text for term in ("坐标", "中心点", "可点击")) and not any(term in text for term in ("看不到", "不可见", "没有找到")):
        return {"status": "incomplete", "reason": "missing complete x/y"}
    if require_coordinates and not any(term in text for term in ("看不到", "不可见", "没有找到", "不确定", "无法确认")):
        return {"status": "incomplete", "reason": "missing complete x/y"}
    return {}


def _coordinate_pair_from_text(text: str) -> dict[str, int]:
    value = str(text or "")
    x_value = next((match.group(1) for match in _COORDINATE_X_RE.finditer(value) if match.group(1)), "")
    y_value = next((match.group(1) for match in _COORDINATE_Y_RE.finditer(value) if match.group(1)), "")
    if not x_value or not y_value:
        return {}
    return {"x": _coerce_int(x_value), "y": _coerce_int(y_value)}


def _coordinate_pair_near_terms(text: str, terms: tuple[str, ...]) -> dict[str, int]:
    value = str(text or "")
    clean_terms = [str(term or "").strip().lower() for term in terms if str(term or "").strip()]
    if not value or not clean_terms:
        return {}
    for fragment in re.split(r"[。！？!?；;\n]+", value):
        lowered = fragment.lower()
        if any(term in lowered for term in clean_terms):
            coordinates = _coordinate_pair_from_text(fragment)
            if coordinates:
                return coordinates
    return {}


def _has_negative_visibility_evidence(text: str, terms: tuple[str, ...]) -> bool:
    value = str(text or "").lower()
    clean_terms = [str(term or "").strip().lower() for term in terms if str(term or "").strip()]
    if not value or not clean_terms:
        return False
    negative_terms = ("没有显示", "未显示", "看不到", "没有看到", "不可见", "未看到", "没有找到", "不存在")
    for term in clean_terms:
        start = 0
        while True:
            index = value.find(term, start)
            if index < 0:
                break
            window = value[max(0, index - 18) : index + len(term) + 18]
            if any(negative in window for negative in negative_terms):
                return True
            start = index + len(term)
    return False


def _looks_like_visual_observation_failure(text: str) -> bool:
    value = str(text or "").lower()
    return bool(value) and any(
        term in value
        for term in (
            "vlm analyzer failed",
            "read operation timed out",
            "不能确认内容",
            "没看清",
            "无法确认内容",
        )
    )


def _has_captured_screen_frame(active_observation: dict[str, Any], frame: dict[str, Any]) -> bool:
    verify_result = active_observation.get("verify_result") if isinstance(active_observation.get("verify_result"), dict) else {}
    return (
        str(verify_result.get("status") or "") == "captured"
        or bool(frame.get("mime_type"))
        or bool(frame.get("data_url"))
    )


def _infer_app_label(text: str, target_hint: str = "") -> str:
    combined = f"{target_hint} {text}".lower()
    labels = {
        "微信": "微信",
        "wechat": "微信",
        "qq": "QQ",
        "音乐": "音乐",
        "music": "音乐",
        "chrome": "Chrome",
        "safari": "Safari",
        "系统设置": "系统设置",
        "设置": "系统设置",
        "finder": "Finder",
        "访达": "访达",
    }
    for key, label in labels.items():
        if key in combined:
            return label
    for term in ("打开", "启动", "切到"):
        if term in target_hint:
            tail = str(target_hint.split(term, 1)[1] or "").strip(" ：:，,。 ")
            if tail:
                return tail[:32]
    return ""


def _infer_contact_label(text: str, target_hint: str = "") -> str:
    combined = f"{target_hint} {text}"
    patterns = (
        r"联系人[“\"']?([^”\"'，,。；;\s]+)",
        r"回复[“\"']?([^”\"'，,。；;\s]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, combined)
        if match:
            label = str(match.group(1) or "").strip("“”\"'：: ")
            if label:
                return label[:32]
    return ""


def _infer_recent_chat_messages(text: str) -> list[dict[str, str]]:
    value = str(text or "")
    marker = re.search(r"(?:最近)?聊天内容\s*[:：]\s*(.+)", value)
    if not marker:
        return []
    segment = re.split(r"[。！？!?]\s*", str(marker.group(1) or ""), maxsplit=1)[0]
    messages: list[dict[str, str]] = []
    for part in re.split(r"[；;\n]+", segment):
        match = re.match(r"\s*([^:：]{1,24})[:：]\s*(.+?)\s*$", part)
        if not match:
            continue
        speaker = str(match.group(1) or "").strip(" “”\"'")
        message = str(match.group(2) or "").strip(" “”\"'，,。；; ")
        if speaker and message:
            messages.append({"speaker": speaker[:24], "text": message[:240]})
        if len(messages) >= 6:
            break
    return messages


def _label_aliases(label: str) -> set[str]:
    normalized = str(label or "").strip().lower()
    aliases = {normalized} if normalized else set()
    if normalized in {"微信", "wechat", "weixin"}:
        aliases.update({"微信", "wechat", "weixin"})
    if normalized in {"chrome", "google chrome", "谷歌浏览器"}:
        aliases.update({"chrome", "google chrome", "谷歌浏览器"})
    if normalized in {"qq", "腾讯qq"}:
        aliases.update({"qq", "腾讯qq"})
    if normalized in {"音乐", "music", "apple music"}:
        aliases.update({"音乐", "music", "apple music"})
    return {item for item in aliases if item}


def _candidate_matches_label(candidate: dict[str, Any], label: str) -> bool:
    aliases = _label_aliases(label)
    if not aliases:
        return False
    names = [
        str(candidate.get("app") or "").strip().lower(),
        str(candidate.get("title") or "").strip().lower(),
        str(candidate.get("bundle_id") or "").strip().lower(),
    ]
    return any(any(alias in name or name in alias for alias in aliases) for name in names if name)


def _point_from_candidate(candidate: dict[str, Any]) -> dict[str, int]:
    focus_point = candidate.get("focus_point") if isinstance(candidate.get("focus_point"), dict) else {}
    if "x" in focus_point and "y" in focus_point:
        return {"x": _coerce_int(focus_point.get("x")), "y": _coerce_int(focus_point.get("y"))}
    bounds = candidate.get("bounds") if isinstance(candidate.get("bounds"), dict) else {}
    width = _coerce_int(bounds.get("width"))
    height = _coerce_int(bounds.get("height"))
    if width > 0 and height > 0:
        return {"x": _coerce_int(bounds.get("x")) + width // 2, "y": _coerce_int(bounds.get("y")) + height // 2}
    return {}


def _candidate_location_for_app_label(frame: dict[str, Any], app_label: str) -> dict[str, int]:
    active = frame.get("active_observation") if isinstance(frame.get("active_observation"), dict) else {}
    trace = frame.get("trace") if isinstance(frame.get("trace"), dict) else {}
    groups = (
        active.get("target_candidates") if isinstance(active.get("target_candidates"), list) else [],
        trace.get("target_candidates") if isinstance(trace.get("target_candidates"), list) else [],
        frame.get("target_candidates") if isinstance(frame.get("target_candidates"), list) else [],
    )
    for group in groups:
        for candidate in group:
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("source") or "") != "dock_item":
                continue
            if not _candidate_matches_label(candidate, app_label):
                continue
            point = _point_from_candidate(candidate)
            if point:
                return point
    return {}


def _screen_bounds_from_frame(frame: dict[str, Any]) -> dict[str, int]:
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


def _infer_chat_context(text: str, target_hint: str = "", *, is_chat_surface: bool = False) -> dict[str, Any]:
    if not is_chat_surface:
        return {}
    combined = f"{target_hint} {text}".lower()
    contact = _infer_contact_label(text, target_hint)
    recent_messages = _infer_recent_chat_messages(text)
    input_visible = "输入框" in combined or "input" in combined
    focus_negative = any(term in combined for term in ("没有聚焦", "未聚焦", "没有光标", "无光标", "焦点不在", "不能直接输入"))
    input_focused = input_visible and not focus_negative and any(
        term in combined for term in ("已聚焦", "有光标", "光标", "焦点在", "可以直接输入", "可直接输入")
    )
    input_ready = input_visible and any(term in combined for term in ("可见", "可输入", "可用", "已聚焦", "光标", "可以直接输入", "可直接输入"))
    send_ready = any(term in combined for term in ("发送入口", "发送按钮", "回车发送", "enter", "发送可用"))
    if not any((contact, recent_messages, input_ready, input_focused, send_ready)):
        return {}
    context: dict[str, Any] = {
        "input_ready": bool(input_ready),
        "input_focused": bool(input_focused),
        "send_ready": bool(send_ready),
        "confidence": 0.72,
    }
    if contact:
        context["contact"] = contact
    if recent_messages:
        context["recent_messages"] = recent_messages
    return context


def _ax_affordance_kind(app_id: str, role: str, label: str, identifier: str) -> str:
    combined = f"{label} {identifier}".lower()
    if role in {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"}:
        if role == "AXSearchField" or any(term in combined for term in ("搜索", "search", "查找")):
            return "search_field"
        if app_id in {"wechat", "qq"}:
            return "chat_input"
        return "input"
    if role in {"AXButton", "AXMenuButton", "AXPopUpButton"}:
        return "button"
    if role in {"AXCheckBox", "AXRadioButton"}:
        return "toggle"
    if role in {"AXRow", "AXCell", "AXOutlineRow", "AXListItem"}:
        if app_id in {"wechat", "qq"}:
            return "chat_thread"
        if app_id == "music":
            return "music_item"
        if app_id == "finder":
            return "file_item"
        return "row"
    if role == "AXLink":
        return "link"
    if role == "AXSlider":
        return "slider"
    return "control"


def _accessibility_computer_use_context(frame: dict[str, Any]) -> dict[str, Any]:
    accessibility = frame.get("accessibility") if isinstance(frame.get("accessibility"), dict) else {}
    if not bool(accessibility.get("usable")):
        return {}
    app = accessibility.get("app") if isinstance(accessibility.get("app"), dict) else {}
    app_id = str(app.get("app_id") or "").strip()
    app_name = str(app.get("name") or "").strip()
    surface_kind = str(app.get("surface") or "desktop_gui").strip() or "desktop_gui"
    elements = accessibility.get("elements") if isinstance(accessibility.get("elements"), list) else []
    search = accessibility.get("search") if isinstance(accessibility.get("search"), dict) else {}
    search_query = str(search.get("query") or "").strip().casefold()
    requested_roles = (
        search.get("requested_roles")
        if isinstance(search.get("requested_roles"), list)
        else []
    )
    allow_hidden_menu = (
        "axmenuitem" in {str(item or "").casefold() for item in requested_roles}
        or "菜单" in search_query
        or "menu" in search_query
    )
    affordances: list[dict[str, Any]] = []
    editable_elements: list[dict[str, Any]] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        ax_ref = element.get("ax_ref") if isinstance(element.get("ax_ref"), dict) else {}
        raw_supports = element.get("supports") if isinstance(element.get("supports"), list) else []
        support_set = {str(item or "").strip() for item in raw_supports}
        supports: list[str] = []
        if support_set.intersection({"press", "focus", "select"}):
            supports.append("click")
        if "type_text" in support_set:
            supports.append("type_text")
        if element.get("focused") and "type_text" in support_set:
            supports.append("key_press")
        if not ax_ref or not supports:
            continue
        role = str(element.get("role") or "").strip()
        bounds = element.get("bounds") if isinstance(element.get("bounds"), dict) else {}
        if role == "AXMenuItem" and not bounds and not allow_hidden_menu:
            continue
        label = str(
            element.get("label")
            or element.get("title")
            or element.get("description")
            or element.get("identifier")
            or role
        ).strip()[:180]
        identifier = str(element.get("identifier") or "").strip()
        item: dict[str, Any] = {
            "kind": _ax_affordance_kind(app_id, role, label, identifier),
            "label": label,
            "role": role,
            "supports": supports,
            "ax_ref": dict(ax_ref),
            "evidence": ["macOS Accessibility hierarchy"],
            "confidence": 1.0,
        }
        semantic_path = str(element.get("semantic_path") or "").strip()[:260]
        if semantic_path and semantic_path != label:
            item["semantic_path"] = semantic_path
        relation = str(element.get("context_relation") or "").strip()[:60]
        if relation:
            item["context_relation"] = relation
            anchor = str(element.get("context_anchor") or "").strip()[:100]
            if anchor:
                item["context_anchor"] = anchor
        if element.get("focused"):
            item["focused"] = True
        if element.get("selected"):
            item["selected"] = True
        affordances.append(item)
        if "type_text" in supports:
            editable_elements.append(item)
        if len(affordances) >= 16:
            break
    result: dict[str, Any] = {
        "surface": {
            "kind": surface_kind,
            "app": app_name,
            "app_id": app_id,
            "bundle_id": str(app.get("bundle_id") or ""),
            "source": "macos_accessibility",
            "confidence": 1.0,
        },
        "affordances": affordances,
    }
    if search:
        index = search.get("index") if isinstance(search.get("index"), dict) else {}
        result["ax_search"] = {
            "available": True,
            "tool": "observe.ax_query",
            "snapshot_id": str(accessibility.get("snapshot_id") or search.get("snapshot_id") or ""),
            "query": str(search.get("query") or "").strip()[:240],
            "cache_hit": bool(search.get("cache_hit")),
            "persistent": bool(search.get("persistent")),
            "source": str(search.get("source") or ""),
            "stale": bool(search.get("stale")),
            "stale_reason": str(search.get("stale_reason") or "")[:120],
            "age_ms": max(0, int(search.get("age_ms") or 0)),
            "total_element_count": max(0, int(accessibility.get("total_element_count") or 0)),
            "returned_count": max(0, int(search.get("returned_count") or len(elements))),
            "matched_count": max(0, int(search.get("matched_count") or 0)),
            "actionable_match_count": max(0, int(search.get("actionable_match_count") or 0)),
            "visible_match_count": max(0, int(search.get("visible_match_count") or 0)),
            "requested_roles": [
                str(item)[:80]
                for item in (
                    search.get("requested_roles")
                    if isinstance(search.get("requested_roles"), list)
                    else []
                )
            ][:8],
            "role_match_count": max(0, int(search.get("role_match_count") or 0)),
            "contextual_count": max(0, int(search.get("contextual_count") or 0)),
            "sufficient": bool(search.get("sufficient", True)),
            "insufficiency_reason": str(search.get("insufficiency_reason") or "")[:120],
            "more_available": bool(search.get("search_truncated") or search.get("capture_truncated")),
            "index": {
                "roles": dict(index.get("roles") or {}) if isinstance(index.get("roles"), dict) else {},
                "supports": dict(index.get("supports") or {}) if isinstance(index.get("supports"), dict) else {},
                "actionable": max(0, int(index.get("actionable") or 0)),
                "focused": max(0, int(index.get("focused") or 0)),
                "selected": max(0, int(index.get("selected") or 0)),
            },
        }
    if app_id in {"wechat", "qq"} and editable_elements:
        focused = any(bool(item.get("focused")) for item in editable_elements)
        result["chat_context"] = {
            "input_ready": True,
            "input_focused": focused,
            "send_ready": any("发送" in str(item.get("label") or "") for item in affordances),
            "confidence": 1.0,
        }
    return result


def _infer_computer_use_context(observation_text: str, frame: dict[str, Any] | None = None, target_hint: str = "") -> dict[str, Any]:
    text = str(observation_text or "")
    frame_data = frame if isinstance(frame, dict) else {}
    accessibility_context = _accessibility_computer_use_context(frame_data)
    if accessibility_context:
        return accessibility_context
    active = frame_data.get("active_observation") if isinstance(frame_data.get("active_observation"), dict) else {}
    raw_hint = str(target_hint or "").strip()
    active_hint = str(active.get("target_hint") or "").strip()
    hint = active_hint if raw_hint.lower() in {"screen", "当前屏幕", "屏幕"} and active_hint else str(raw_hint or active_hint).strip()
    text_surface = text.lower()
    combined = f"{hint} {text}".lower()
    surface: dict[str, Any] = {"kind": "unknown", "confidence": 0.2}
    desktop_context = frame_data.get("desktop_context") if isinstance(frame_data.get("desktop_context"), dict) else {}
    foreground_app = str(
        desktop_context.get("foreground_app")
        or desktop_context.get("frontmost_process")
        or frame_data.get("foreground_app")
        or ""
    ).strip()
    dock_negative = _has_negative_visibility_evidence(text, ("dock", "dock 栏", "程序坞"))
    wechat_negative = _has_negative_visibility_evidence(text, ("微信", "wechat"))
    is_explicit_wechat = ("微信" in text_surface or "wechat" in text_surface) and not wechat_negative
    is_generic_chat = any(term in text_surface for term in ("聊天", "会话", "联系人", "私信"))
    if ("dock" in text_surface or "程序坞" in text_surface) and not dock_negative:
        surface = {"kind": "desktop_gui", "region": "dock", "confidence": 0.86}
    elif is_explicit_wechat:
        surface = {"kind": "wechat_gui", "region": "chat" if "聊天" in text_surface else "app", "confidence": 0.74}
    elif is_generic_chat:
        surface = {"kind": "chat_gui", "region": "chat", "confidence": 0.68}
    elif "浏览器" in text_surface or "网页" in text_surface or "地址栏" in text_surface:
        surface = {"kind": "browser_page", "confidence": 0.7}
    elif "终端" in text_surface or "terminal" in text_surface or "prompt" in text_surface:
        surface = {"kind": "terminal_shell", "confidence": 0.7}
    if foreground_app:
        surface["app"] = foreground_app

    coordinates = _coordinate_pair_from_text(text)
    affordances: list[dict[str, Any]] = []
    app_label = _infer_app_label(text, hint)
    app_negative = _has_negative_visibility_evidence(text, (app_label, "图标")) if app_label else False
    if surface.get("kind") == "desktop_gui" and surface.get("region") == "dock" and app_label and not app_negative:
        app_coordinates = (
            _candidate_location_for_app_label(frame_data, app_label)
            or _coordinate_pair_near_terms(text, (app_label, "图标", "dock", "程序坞"))
            or coordinates
        )
        item: dict[str, Any] = {
            "kind": "app_icon",
            "label": app_label,
            "supports": ["click_to_open"],
            "evidence": [text[:220]] if text.strip() else [],
            "confidence": 0.82,
        }
        if app_coordinates:
            item["location"] = app_coordinates
        affordances.append(item)
    can_try_reveal_dock = dock_negative or (
        _looks_like_visual_observation_failure(text) and _has_captured_screen_frame(active, frame_data)
    )
    if can_try_reveal_dock and app_label and _looks_like_desktop_action_request(hint) and not any(
        item.get("kind") == "app_icon" for item in affordances
    ):
        screen_bounds = active.get("screen_bounds") if isinstance(active.get("screen_bounds"), dict) else {}
        if not screen_bounds:
            screen_bounds = _screen_bounds_from_frame(frame_data)
        screen_width = _coerce_int(screen_bounds.get("width"))
        screen_height = _coerce_int(screen_bounds.get("height"))
        if screen_width > 0 and screen_height > 0:
            origin_x = _coerce_int(screen_bounds.get("x"))
            origin_y = _coerce_int(screen_bounds.get("y"))
            affordances.append(
                {
                    "kind": "screen_edge",
                    "label": "屏幕底边（显示隐藏 Dock）",
                    "purpose": "reveal_hidden_dock",
                    "target_app": app_label,
                    "supports": ["click"],
                    "location": {"x": origin_x + screen_width // 2, "y": origin_y + max(0, screen_height - 2)},
                    "evidence": [text[:220]] if text.strip() else [],
                    "confidence": 0.58,
                }
            )
    is_chat_surface = surface.get("kind") in {"wechat_gui", "chat_gui"}
    if is_chat_surface and ("搜索" in combined or "查找" in combined):
        item = {
            "kind": "search_field",
            "label": "微信搜索框" if surface.get("kind") == "wechat_gui" else "搜索框",
            "supports": ["click", "type_text", "key_press"],
            "evidence": [text[:220]] if text.strip() else [],
            "confidence": 0.74,
        }
        search_coordinates = _coordinate_pair_near_terms(text, ("搜索框", "搜索", "查找"))
        if search_coordinates:
            item["location"] = search_coordinates
        affordances.append(item)
    contact_label = _infer_contact_label(text, hint)
    thread_evidence = any(
        term in text_surface for term in ("聊天条目", "会话列表", "会话条目", "聊天项", "联系人列表")
    )
    target_thread_evidence = bool(
        contact_label
        and contact_label in text
        and any(term in text_surface for term in ("可点击", "中心点", "坐标", "列表", "条目", "会话"))
    )
    if is_chat_surface and (thread_evidence or target_thread_evidence):
        item = {
            "kind": "chat_thread",
            "label": contact_label or "聊天条目",
            "supports": ["click"],
            "evidence": [text[:220]] if text.strip() else [],
            "confidence": 0.74,
        }
        thread_coordinates = _coordinate_pair_near_terms(text, (contact_label, "联系人", "聊天条目", "会话列表", "会话"))
        if thread_coordinates:
            item["location"] = thread_coordinates
        affordances.append(item)
    if "输入框" in combined or "input" in combined:
        input_coordinates = _coordinate_pair_near_terms(text, ("聊天输入框", "消息输入", "输入框", "input", "底部")) or coordinates
        item = {
            "kind": "chat_input" if "聊天" in combined or "微信" in combined else "input",
            "label": "聊天输入框" if "聊天" in combined or "微信" in combined else "输入框",
            "supports": ["click", "type_text", "key_press"],
            "evidence": [text[:220]] if text.strip() else [],
            "confidence": 0.76,
        }
        if input_coordinates:
            item["location"] = input_coordinates
        affordances.append(item)
    if "发送" in combined and ("按钮" in combined or "回车" in combined or "enter" in combined):
        item = {
            "kind": "button",
            "label": "发送",
            "supports": ["click", "key_press"],
            "evidence": [text[:220]] if text.strip() else [],
            "confidence": 0.72,
        }
        send_coordinates = _coordinate_pair_near_terms(text, ("发送", "按钮")) or coordinates
        if send_coordinates:
            item["location"] = send_coordinates
        affordances.append(item)
    if "地址栏" in combined:
        item = {
            "kind": "address_bar",
            "label": "浏览器地址栏",
            "supports": ["type_text", "key_press"],
            "evidence": [text[:220]] if text.strip() else [],
            "confidence": 0.72,
        }
        address_coordinates = _coordinate_pair_near_terms(text, ("地址栏",))
        if address_coordinates:
            item["location"] = address_coordinates
        affordances.append(item)
    if surface.get("kind") == "terminal_shell":
        affordances.append(
            {
                "kind": "terminal_prompt",
                "label": "终端提示符",
                "supports": ["type_text", "key_press"],
                "evidence": [text[:220]] if text.strip() else [],
                "confidence": 0.7,
            }
        )
    result: dict[str, Any] = {"surface": surface, "affordances": affordances}
    chat_context = _infer_chat_context(text, hint, is_chat_surface=is_chat_surface)
    if chat_context:
        result["chat_context"] = chat_context
    return result


def _computer_use_context_text(observation: dict[str, Any] | None) -> str:
    data = observation if isinstance(observation, dict) else {}
    surface = data.get("surface") if isinstance(data.get("surface"), dict) else {}
    affordances = data.get("affordances") if isinstance(data.get("affordances"), list) else []
    chat_context = data.get("chat_context") if isinstance(data.get("chat_context"), dict) else {}
    ax_search = data.get("ax_search") if isinstance(data.get("ax_search"), dict) else {}
    if not surface and not affordances and not chat_context and not ax_search:
        return ""
    return "Structured computer-use context:\n" + json.dumps(
        {
            "surface": surface,
            "affordances": affordances,
            "chat_context": chat_context,
            "ax_search": ax_search,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _has_numeric_action_argument(arguments: dict[str, Any], key: str) -> bool:
    if key not in arguments:
        return False
    try:
        float(arguments.get(key))
        return True
    except (TypeError, ValueError):
        return False


def _observation_has_reviewable_click_affordance(observation: dict[str, Any]) -> bool:
    affordances = observation.get("affordances") if isinstance(observation.get("affordances"), list) else []
    for item in affordances:
        if not isinstance(item, dict):
            continue
        supports = item.get("supports") if isinstance(item.get("supports"), list) else []
        support_set = {str(value or "").strip() for value in supports}
        location = item.get("location") if isinstance(item.get("location"), dict) else {}
        if (
            ("click" in support_set or "click_to_open" in support_set)
            and (
                (
                    _has_numeric_action_argument(location, "x")
                    and _has_numeric_action_argument(location, "y")
                )
                or isinstance(item.get("ax_ref"), dict)
            )
        ):
            return True
    return False
