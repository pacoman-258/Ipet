from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from collections import Counter, OrderedDict
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from uuid import uuid4


AX_CACHE_DEFAULT_TTL_SEC = 30.0
AX_CACHE_MAX_APPS = 8
AX_SEARCH_DEFAULT_LIMIT = 10
AX_SEARCH_MAX_LIMIT = 16
AX_COLLECTION_READ_MAX_ITEMS = 64
AX_SEARCH_MAX_ELEMENT_CHARS = 14_000
AX_STORE_SCHEMA_VERSION = 1
AX_STORE_CHUNK_SIZE = 500
AX_STORE_MAX_ELEMENTS = 50_000
AX_PERSISTENT_ROOT = Path(__file__).resolve().parents[1] / "data" / "ax_trees"

_EDITABLE_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"}
_ROW_ROLES = {"AXRow", "AXCell", "AXOutlineRow", "AXListItem"}
_BUTTON_ROLES = {
    "AXButton",
    "AXCheckBox",
    "AXRadioButton",
    "AXPopUpButton",
    "AXMenuButton",
    "AXMenuItem",
    "AXLink",
}
_REVIEWABLE_SUPPORTS = {
    "press",
    "open",
    "show_menu",
    "focus",
    "select",
    "type_text",
    "hit_test",
}
_TEXT_KEYS = (
    "label",
    "title",
    "description",
    "placeholder",
    "identifier",
    "value",
    "url",
)
_COMPACT_KEYS = (
    "role",
    "subrole",
    "tree_path",
    "label",
    "label_source",
    "title",
    "description",
    "placeholder",
    "identifier",
    "value",
    "url",
    "enabled",
    "focused",
    "selected",
    "protected",
    "supports",
    "bounds",
    "ax_ref",
    "ax_ref_source",
    "activation",
    "activation_effect",
    "semantic_path",
    "context_relation",
    "context_anchor",
)
_COLLECTION_ROLES = {
    "AXGrid",
    "AXList",
    "AXOutline",
    "AXTable",
    "AXScrollArea",
    "AXGroup",
}
_NATIVE_COLLECTION_ROLES = {"AXGrid", "AXList", "AXOutline", "AXTable"}
_COLLECTION_LABEL_MARKERS = (
    "列表",
    "网格",
    "表格",
    "侧栏",
    "目录",
    "list",
    "grid",
    "table",
    "outline",
    "sidebar",
)
_SIDEBAR_LABEL_MARKERS = ("边栏", "侧栏", "sidebar")
_GEOMETRY_CLICK_ROLES = {"AXGroup", "AXStaticText", "AXImage", "AXWebArea"}
_COLLECTION_QUERY_TERMS = (
    "有哪些",
    "列表",
    "名称",
    "名字",
    "可见",
    "读取",
    "列出",
    "项目",
    "内容",
    "list",
    "names",
    "items",
    "visible",
)
_EXHAUSTIVE_COLLECTION_QUERY_TERMS = (
    "所有",
    "全部",
    "有哪些",
    "列出",
    "名称",
    "名字",
    "可见",
    "all",
    "every",
    "names",
    "visible",
    # Brain may ask the search tool to prove its own completeness fields
    # instead of repeating "all". Treat those explicit contracts as an
    # exhaustive read so the collection anchor does not consume one of the
    # bounded item slots.
    "collection_complete",
    "collection_labels_complete",
    "collection_items",
)
_IMPLICIT_ACTION_COLLECTION_TERMS = (
    "会话",
    "联系人",
    "聊天",
    "好友",
    "专辑",
    "歌单",
    "歌曲",
    "文件",
    "文件夹",
    "资料夹",
    "目录",
    "conversation",
    "contact",
    "chat",
    "album",
    "playlist",
    "song",
    "file",
    "folder",
    "directory",
)
_ACTION_QUERY_TERMS = (
    "点击",
    "双击",
    "打开",
    "选择",
    "选中",
    "进入",
    "按下",
    "激活",
    "聚焦",
    "播放",
    "暂停",
    "click",
    "double click",
    "open",
    "select",
    "press",
    "activate",
    "focus",
    "play",
    "pause",
)
_MENU_ACTION_QUERY_TERMS = (
    "上下文菜单",
    "右键菜单",
    "显示菜单",
    "打开菜单",
    "右键",
    "context menu",
    "show menu",
)
_MEDIA_PLAYBACK_QUERY_TERMS = (
    "播放",
    "继续播放",
    "暂停",
    "play",
    "resume",
    "pause",
)
_ACTIVE_CHAT_VERIFICATION_TERMS = (
    "当前是否在目标",
    "目标联系人/会话",
    "目标联系人或会话",
    "当前会话",
    "目标会话",
    "会话里",
    "聊天记录",
    "最近聊天",
    "active conversation",
    "current chat",
    "target conversation",
    "chat history",
)
_COLLECTION_SUBJECT_TERMS = (
    "专辑",
    "歌单",
    "歌曲",
    "艺人",
    "播放列表",
    "文件",
    "文件夹",
    "目录",
    "album",
    "playlist",
    "song",
    "artist",
    "file",
    "folder",
    "directory",
)


def _clean_text(value: object, *, max_length: int = 360) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:max_length]


def _normalized_text(value: object) -> str:
    return "".join(char for char in _clean_text(value).casefold() if char.isalnum())


def _query_terms(query: str) -> tuple[str, ...]:
    chunks = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", str(query or "").casefold())
    terms: list[str] = []
    for chunk in chunks:
        normalized = _normalized_text(chunk)
        if normalized and normalized not in terms:
            terms.append(normalized)
    return tuple(terms[:24])


def _bounded_edit_distance(
    left: str,
    right: str,
    *,
    limit: int,
) -> int:
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        row_minimum = current[0]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
            row_minimum = min(row_minimum, current[-1])
        if row_minimum > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _near_query_label(query: str, label: str) -> bool:
    query_text = _normalized_text(query)
    label_text = _normalized_text(label)
    if (
        len(label_text) < 3
        or len(label_text) > 32
        or label_text in query_text
        or len(query_text) < len(label_text) - 1
    ):
        return False
    limit = 1 if len(label_text) < 12 else 2
    for size in range(
        max(1, len(label_text) - limit),
        min(len(query_text), len(label_text) + limit) + 1,
    ):
        for offset in range(0, len(query_text) - size + 1):
            if (
                _bounded_edit_distance(
                    label_text,
                    query_text[offset : offset + size],
                    limit=limit,
                )
                <= limit
            ):
                return True
    return False


def _near_collection_labels(
    query: str,
    items: list[tuple[int, dict[str, Any]]],
    *,
    limit: int = 5,
) -> list[str]:
    """Find typo candidates across the full collection without returning its tree."""
    query_text = _normalized_text(query)
    if not query_text:
        return []
    query_characters = Counter(query_text)
    matches: list[str] = []
    seen: set[str] = set()
    for _index, item in items:
        label = _element_label(item)
        label_text = _normalized_text(label)
        if (
            not label
            or label in seen
            or len(label_text) < 3
            or len(label_text) > 32
            or label_text in query_text
        ):
            continue
        edit_limit = 1 if len(label_text) < 12 else 2
        # A candidate within the edit bound must retain at least this many
        # characters. This cheap multiset filter avoids quadratic distance
        # checks for unrelated rows in very large Finder/chat collections.
        overlap = sum((Counter(label_text) & query_characters).values())
        if overlap < len(label_text) - edit_limit:
            continue
        if not _near_query_label(query_text, label_text):
            continue
        seen.add(label)
        matches.append(label)
        if len(matches) >= max(1, int(limit)):
            break
    return matches


def _element_search_text(element: dict[str, Any]) -> str:
    # Identifier is still matched exactly in _semantic_score. Excluding it from
    # loose term matching prevents an app namespace such as "Music.shelfItem"
    # from making every element look relevant to the query "music".
    values = [
        _clean_text(element.get(key))
        for key in ("label", "title", "description", "value", "role", "subrole")
    ]
    supports = element.get("supports") if isinstance(element.get("supports"), list) else []
    values.extend(_clean_text(item) for item in supports)
    return _normalized_text(" ".join(value for value in values if value))


def _element_tree_path(element: dict[str, Any]) -> tuple[int, ...]:
    raw_path = element.get("tree_path")
    if not isinstance(raw_path, list):
        ax_ref = element.get("ax_ref") if isinstance(element.get("ax_ref"), dict) else {}
        raw_path = ax_ref.get("path")
    if not isinstance(raw_path, list):
        return ()
    if not all(isinstance(item, int) and item >= 0 for item in raw_path):
        return ()
    return tuple(raw_path[:96])


def _window_paths(
    elements: list[dict[str, Any]],
    *,
    focused_only: bool = False,
) -> tuple[tuple[int, ...], ...]:
    paths = {
        path
        for element in elements
        if str(element.get("role") or "") == "AXWindow"
        and (not focused_only or bool(element.get("focused")))
        and (path := _element_tree_path(element))
    }
    return tuple(sorted(paths))


def _element_window_path(
    element: dict[str, Any],
    window_paths: tuple[tuple[int, ...], ...],
) -> tuple[int, ...]:
    path = _element_tree_path(element)
    matches = [
        window_path
        for window_path in window_paths
        if len(path) >= len(window_path)
        and path[: len(window_path)] == window_path
    ]
    return max(matches, key=len) if matches else ()


def _common_path_depth(
    left: tuple[int, ...],
    right: tuple[int, ...],
) -> int:
    depth = 0
    for left_item, right_item in zip(left, right):
        if left_item != right_item:
            break
        depth += 1
    return depth


def _element_label(element: dict[str, Any]) -> str:
    return _clean_text(
        element.get("label")
        or element.get("title")
        or element.get("description")
        or element.get("value")
        or element.get("identifier"),
        max_length=180,
    )


def _has_screen_bounds(element: dict[str, Any]) -> bool:
    bounds = element.get("bounds") if isinstance(element.get("bounds"), dict) else {}
    try:
        return float(bounds.get("width") or 0) > 0 and float(bounds.get("height") or 0) > 0
    except (TypeError, ValueError):
        return False


def _reviewable_supports(element: dict[str, Any]) -> set[str]:
    supports = element.get("supports") if isinstance(element.get("supports"), list) else []
    return {
        _clean_text(item, max_length=40)
        for item in supports
        if _clean_text(item, max_length=40) in _REVIEWABLE_SUPPORTS
    }


def _action_support_allowed(
    element: dict[str, Any],
    query: str,
) -> bool:
    activation_effect = str(element.get("activation_effect") or "")
    intent_text = str(query or "").casefold()
    if activation_effect == "track_info_dialog":
        return not any(term in intent_text for term in ("专辑", "album"))
    if activation_effect != "media_playback":
        return True
    if _menu_action_requested(query) and "show_menu" in _reviewable_supports(element):
        return True
    return _media_playback_requested(intent_text)


def _media_playback_requested(query: object) -> bool:
    intent_text = str(query or "").casefold()
    # “播放列表” names a playlist; it is not a request to start playback.
    # Remove that noun phrase before looking for Chinese playback verbs, and
    # use word boundaries so English "playlist" cannot match "play".
    chinese_intent = re.sub(
        r"播放\s*(?:列表|清单|队列|按钮|控件|状态|记录|历史)",
        " ",
        intent_text,
    )
    if any(term in chinese_intent for term in ("继续播放", "播放", "暂停")):
        return True
    english_intent = re.sub(
        r"\bplay\s+(?:list|queue|button|control|state|history)\b",
        " ",
        intent_text,
    )
    return bool(re.search(r"\b(?:play|resume|pause)\b", english_intent))


def _menu_action_requested(query: str) -> bool:
    intent_text = str(query or "").casefold()
    return any(term in intent_text for term in _MENU_ACTION_QUERY_TERMS)


def _semantic_path(
    element: dict[str, Any],
    *,
    hierarchy_labels: tuple[str, ...] = (),
) -> str:
    ax_ref = element.get("ax_ref") if isinstance(element.get("ax_ref"), dict) else {}
    ancestors = ax_ref.get("ancestors") if isinstance(ax_ref.get("ancestors"), list) else []
    ax_labels = [
        _clean_text(item.get("label"), max_length=80)
        for item in ancestors
        if isinstance(item, dict) and _clean_text(item.get("label"), max_length=80)
    ]
    labels = ax_labels or [
        _clean_text(item, max_length=80)
        for item in hierarchy_labels
        if _clean_text(item, max_length=80)
    ]
    label = _element_label(element)
    if label and (not labels or labels[-1] != label):
        labels.append(label)
    deduplicated: list[str] = []
    for item in labels[-4:]:
        if not deduplicated or deduplicated[-1] != item:
            deduplicated.append(item)
    return " > ".join(deduplicated)


def _snapshot_geometry_ax_ref(app_id: str, element: dict[str, Any]) -> dict[str, Any]:
    path = _element_tree_path(element)
    role = _clean_text(element.get("role"), max_length=80)
    if not app_id or not role or not path or not _has_screen_bounds(element):
        return {}
    payload: dict[str, Any] = {
        "app_id": app_id,
        "role": role,
        "path": list(path),
    }
    for key in (
        "subrole",
        "identifier",
        "title",
        "description",
        "placeholder",
        "url",
    ):
        value = _clean_text(
            element.get(key),
            max_length=600 if key == "url" else 160,
        )
        if value:
            payload[key] = value
    if not any(payload.get(key) for key in ("identifier", "title", "description", "url")):
        value = _clean_text(element.get("value"), max_length=160)
        if value and role not in _EDITABLE_ROLES and not element.get("protected"):
            payload["value"] = value
    bounds = element.get("bounds") if isinstance(element.get("bounds"), dict) else {}
    payload["bounds"] = dict(bounds)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return payload


def _signed_ax_ref(ax_ref: dict[str, Any], **changes: object) -> dict[str, Any]:
    payload = {
        key: deepcopy(value)
        for key, value in ax_ref.items()
        if key != "fingerprint"
    }
    for key, value in changes.items():
        if value in ("", None, [], {}):
            payload.pop(key, None)
        else:
            payload[key] = deepcopy(value)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return payload


def _geometry_hit_test_element(
    app_id: str,
    element: dict[str, Any],
    *,
    descendant_label: str,
    descendant_url: str = "",
    ignore_native_supports: bool = False,
) -> dict[str, Any]:
    candidate = dict(element)
    normalized_app_id = _normalized_text(app_id)
    hierarchy = _normalized_text(
        " ".join(
            str(candidate.get(key) or "")
            for key in ("context_anchor", "semantic_path")
        )
    )
    qq_chat_thread = bool(
        normalized_app_id == "qq"
        and any(
            term in hierarchy
            for term in ("会话", "聊天", "conversation", "chat")
        )
    )
    native_supports = _reviewable_supports(candidate) - {"show_menu"}
    if (
        not descendant_label
        or candidate.get("enabled") is False
        or str(candidate.get("role") or "") not in _GEOMETRY_CLICK_ROLES
        or not _has_screen_bounds(candidate)
        or (
            bool(native_supports)
            and not ignore_native_supports
            and not (
                qq_chat_thread
                and native_supports.issubset({"press"})
            )
        )
    ):
        return candidate
    ax_ref = (
        dict(candidate["ax_ref"])
        if isinstance(candidate.get("ax_ref"), dict)
        else _snapshot_geometry_ax_ref(app_id, candidate)
    )
    if not ax_ref:
        return candidate
    candidate["supports"] = (
        [
            *(
                support
                for support in (
                    candidate.get("supports")
                    if isinstance(candidate.get("supports"), list)
                    else []
                )
                if support == "select"
            ),
            "hit_test",
        ]
        if ignore_native_supports
        else [
            *(
                candidate.get("supports")
                if isinstance(candidate.get("supports"), list)
                else []
            ),
            "hit_test",
        ]
    )
    candidate["activation"] = "hit_test"
    semantic_kind = ""
    if normalized_app_id in {"qq", "wechat"}:
        if any(term in hierarchy for term in ("会话", "聊天", "conversation", "chat")):
            semantic_kind = "chat_thread"
    candidate["ax_ref"] = _signed_ax_ref(
        ax_ref,
        activation="hit_test",
        descendant_label=_clean_text(descendant_label, max_length=160),
        descendant_url=_clean_text(descendant_url, max_length=600),
        semantic_kind=semantic_kind,
    )
    candidate["ax_ref_source"] = "verified_geometry"
    return candidate


def _menu_action_element(
    element: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    candidate = dict(element)
    ax_ref = candidate.get("ax_ref")
    if (
        not _menu_action_requested(query)
        or "show_menu" not in _reviewable_supports(candidate)
        or not isinstance(ax_ref, dict)
    ):
        return candidate
    candidate["activation"] = "show_menu"
    candidate["activation_effect"] = "context_menu"
    candidate["ax_ref"] = _signed_ax_ref(
        ax_ref,
        activation="show_menu",
    )
    candidate["ax_ref_source"] = (
        _clean_text(candidate.get("ax_ref_source"), max_length=80)
        or "native_action"
    )
    return candidate


def _hierarchy_enriched_elements(
    elements: tuple[dict[str, Any], ...],
    *,
    app_id: str,
) -> tuple[list[dict[str, Any]], dict[tuple[int, ...], int]]:
    enriched = [dict(element) for element in elements]
    for element in enriched:
        raw_actions = (
            element.get("actions")
            if isinstance(element.get("actions"), list)
            else []
        )
        supports = (
            list(element.get("supports"))
            if isinstance(element.get("supports"), list)
            else []
        )
        if "AXOpen" in raw_actions and "open" not in supports:
            supports.append("open")
            element["supports"] = supports
        if "AXShowMenu" in raw_actions and "show_menu" not in supports:
            supports.append("show_menu")
            element["supports"] = supports
        if _reviewable_supports(element) and not isinstance(element.get("ax_ref"), dict):
            ax_ref = _snapshot_geometry_ax_ref(app_id, element)
            if ax_ref:
                element["ax_ref"] = ax_ref
                element["ax_ref_source"] = "snapshot_geometry"
    path_index = {
        path: index
        for index, element in enumerate(enriched)
        if (path := _element_tree_path(element)) or element.get("tree_path") == []
    }
    descendant_labels: dict[int, list[tuple[int, int, tuple[int, ...], str]]] = {}
    for child in enriched:
        if child.get("protected"):
            continue
        child_label = _element_label(child)
        child_path = _element_tree_path(child)
        if not child_label or not child_path:
            continue
        role_priority = 0 if str(child.get("role") or "") in {"AXStaticText", "AXHeading"} else 1
        for depth in range(1, min(3, len(child_path)) + 1):
            ancestor_index = path_index.get(child_path[:-depth])
            if ancestor_index is None:
                continue
            ancestor = enriched[ancestor_index]
            if (
                not _reviewable_supports(ancestor)
                or _element_label(ancestor)
                or str(ancestor.get("role") or "") in _NATIVE_COLLECTION_ROLES
            ):
                continue
            descendant_labels.setdefault(ancestor_index, []).append(
                (role_priority, depth, child_path, child_label)
            )
    for ancestor_index, candidates in descendant_labels.items():
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        label = candidates[0][3]
        if label:
            ancestor = enriched[ancestor_index]
            ancestor["label"] = label
            ancestor["label_source"] = "descendant"
            ax_ref = (
                ancestor.get("ax_ref")
                if isinstance(ancestor.get("ax_ref"), dict)
                else {}
            )
            if ax_ref:
                ancestor["ax_ref"] = _signed_ax_ref(
                    dict(ax_ref),
                    descendant_label=label,
                )
                ancestor["ax_ref_source"] = (
                    _clean_text(
                        ancestor.get("ax_ref_source"),
                        max_length=80,
                    )
                    or "descendant_identity"
                )
    for element in enriched:
        if str(element.get("role") or "") != "AXCell":
            continue
        path = _element_tree_path(element)
        parent_index = path_index.get(path[:-1]) if path else None
        if parent_index is None:
            continue
        parent = enriched[parent_index]
        if (
            str(parent.get("role") or "") in {"AXRow", "AXOutlineRow", "AXListItem"}
            and _reviewable_supports(parent)
            and _element_label(parent) == _element_label(element)
        ):
            element["nested_action_proxy"] = True
    for element in enriched:
        path = _element_tree_path(element)
        hierarchy_labels: list[str] = []
        for prefix_length in range(max(0, len(path) - 5), len(path)):
            ancestor_index = path_index.get(path[:prefix_length])
            if ancestor_index is None:
                continue
            ancestor_label = _element_label(enriched[ancestor_index])
            if ancestor_label:
                hierarchy_labels.append(ancestor_label)
        semantic_path = _semantic_path(
            element,
            hierarchy_labels=tuple(hierarchy_labels),
        )
        if semantic_path and semantic_path != _element_label(element):
            element["semantic_path"] = semantic_path
    if _normalized_text(app_id) == "music":
        for element in enriched:
            if (
                str(element.get("role") or "") == "AXMenuItem"
                and any(
                    marker in _normalized_text(_element_label(element))
                    for marker in ("显示简介", "getinfo")
                )
            ):
                element["activation_effect"] = "track_info_dialog"
            if (
                str(element.get("role") or "") != "AXGroup"
                or "press" not in _reviewable_supports(element)
            ):
                continue
            path = _element_tree_path(element)
            for prefix_length in range(len(path) - 1, -1, -1):
                ancestor_index = path_index.get(path[:prefix_length])
                if ancestor_index is None:
                    continue
                ancestor = enriched[ancestor_index]
                if str(ancestor.get("role") or "") != "AXGrid":
                    continue
                anchor_key = _normalized_text(_element_label(ancestor))
                if "专辑" in anchor_key or "album" in anchor_key:
                    element["activation_effect"] = "media_playback"
                break
    return enriched, path_index


def _semantic_score(element: dict[str, Any], query: str, terms: tuple[str, ...]) -> tuple[int, bool]:
    role = _clean_text(element.get("role"), max_length=80)
    supports = {
        _clean_text(item, max_length=40)
        for item in (element.get("supports") if isinstance(element.get("supports"), list) else [])
    }
    query_text = _normalized_text(query)
    searchable = _element_search_text(element)
    score = 0
    matched = False
    bounds = element.get("bounds") if isinstance(element.get("bounds"), dict) else {}
    try:
        width = float(bounds.get("width") or 0)
        height = float(bounds.get("height") or 0)
    except (TypeError, ValueError):
        width = 0.0
        height = 0.0
    editable = bool(
        role in _EDITABLE_ROLES
        and not _clean_text(element.get("url"), max_length=600)
        and supports & {"focus", "type_text"}
    )

    seen_field_values: set[str] = set()
    for key in _TEXT_KEYS:
        field = _normalized_text(element.get(key))
        if not field or field in seen_field_values:
            continue
        seen_field_values.add(field)
        if len(field) >= 2 and field in query_text:
            score += 260 if key in {"label", "title", "identifier"} else 180
            matched = True
        if query_text and len(query_text) <= 80 and query_text in field:
            score += 220
            matched = True
    for term in terms:
        if term.isascii() and len(term) < 3:
            continue
        if len(term) >= 2 and term in searchable:
            score += min(120, 35 + len(term) * 8)
            matched = True

    intent_text = str(query or "").casefold()
    if any(term in intent_text for term in ("搜索", "查找", "search", "find")):
        if role == "AXSearchField":
            score += 190
            matched = True
        elif editable:
            score += 55
            if role in {"AXTextField", "AXComboBox"} and 0 < height <= 100 and 0 < width <= 600:
                score += 70
                matched = True
    if any(term in intent_text for term in ("输入", "回复", "编辑", "消息框", "type", "reply")):
        if editable:
            score += 170
            if role == "AXTextArea" or bool(element.get("focused")):
                matched = True
    if any(term in intent_text for term in ("发送", "确认", "按钮", "点击", "send", "confirm", "button", "click")):
        if role in _BUTTON_ROLES or "press" in supports:
            score += 80
    if any(term in intent_text for term in ("联系人", "会话", "聊天", "好友", "contact", "conversation", "chat")):
        if role in _ROW_ROLES:
            score += 75
    if any(term in intent_text for term in ("歌曲", "播放", "暂停", "歌单", "song", "play", "pause", "playlist")):
        if role in _ROW_ROLES or role in _BUTTON_ROLES:
            score += 55
    if any(term in intent_text for term in ("文件", "文件夹", "目录", "file", "folder")):
        if role in _ROW_ROLES:
            score += 55

    if element.get("focused"):
        score += 70
    if element.get("selected"):
        score += 35
    if _has_screen_bounds(element):
        score += 45
    elif role == "AXMenuItem":
        # Closed application menus commonly expose text and AXPress but no
        # geometry. They must not outrank a visible page/sidebar control with
        # the same label.
        score -= 240
    if supports:
        score += 18
    if role in {"AXRow", "AXOutlineRow"}:
        score += 25
    elif role in {"AXCell", "AXListItem"}:
        score += 10
    if element.get("label_source") == "descendant":
        score += 35
    if element.get("nested_action_proxy"):
        score -= 120
    if element.get("label") or element.get("value"):
        score += 5
    return score, matched


def _element_identity(element: dict[str, Any]) -> str:
    ax_ref = element.get("ax_ref") if isinstance(element.get("ax_ref"), dict) else {}
    fingerprint = _clean_text(ax_ref.get("fingerprint"), max_length=80)
    if fingerprint:
        return f"ref:{fingerprint}"
    bounds = element.get("bounds") if isinstance(element.get("bounds"), dict) else {}
    payload = {
        "role": element.get("role"),
        "label": element.get("label") or element.get("value"),
        "bounds": bounds,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _compact_element(element: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: deepcopy(element[key])
        for key in _COMPACT_KEYS
        if key in element and element[key] not in ("", None, [], {})
    }
    if result.get("enabled") is True:
        result.pop("enabled", None)
    for key in ("focused", "selected", "protected"):
        if result.get(key) is not True:
            result.pop(key, None)
    return result


def _effective_collection_child_count(
    elements: list[dict[str, Any]],
    collection_index: int,
) -> int:
    container_path = _element_tree_path(elements[collection_index])
    children = _collection_direct_child_indices(elements, container_path)
    if len(children) == 1:
        nested_path = _element_tree_path(elements[children[0]])
        nested_children = _collection_direct_child_indices(elements, nested_path)
        if len(nested_children) > 1:
            children = nested_children
    return len(
        [
            index
            for index in children
            if str(elements[index].get("role") or "")
            not in {"AXColumn", "AXScrollBar", "AXSplitter", "AXValueIndicator"}
        ]
    )


def _collection_label_relevance(label: str, query: str) -> int:
    normalized_label = _normalized_text(label)
    normalized_query = _normalized_text(query)
    if not normalized_label:
        return 0
    score = 0
    if any(marker in normalized_label for marker in _COLLECTION_LABEL_MARKERS):
        score += 150
    if not normalized_query:
        return score
    if normalized_label in normalized_query or normalized_query in normalized_label:
        score += 220
    base_label = normalized_label
    for marker in _COLLECTION_LABEL_MARKERS:
        base_label = base_label.replace(marker, "")
    if (
        base_label != normalized_label
        and len(base_label) >= 2
        and base_label in normalized_query
    ):
        score += 180
    return score


def _declared_collection_item_count(collection: dict[str, Any]) -> int:
    values = " ".join(
        _clean_text(collection.get(key), max_length=240)
        for key in ("value", "description", "title", "label")
        if _clean_text(collection.get(key), max_length=240)
    )
    counts = [
        int(match)
        for match in re.findall(
            r"(?i)(?<!\d)(\d{1,9})\s*(?:total\s+)?"
            r"(?:items?|entries|rows|项|条|个(?:项目|条目|文件|专辑|歌曲)?)",
            values,
        )
    ]
    return max(counts, default=0)


def _collection_subject_mismatch(label: str, query: str) -> bool:
    normalized_label = _normalized_text(label)
    normalized_query = _normalized_text(query)
    requested = {
        _normalized_text(term)
        for term in _COLLECTION_SUBJECT_TERMS
        if _normalized_text(term) in normalized_query
    }
    return bool(
        requested
        and not any(term in normalized_label for term in requested)
    )


def _active_chat_identity_evidence(
    elements: list[dict[str, Any]],
    *,
    app_id: str,
    query: str,
) -> dict[str, Any]:
    if _normalized_text(app_id) not in {"qq", "wechat"}:
        return {}
    query_key = _normalized_text(query)
    if not query_key:
        return {}
    window_paths = _window_paths(elements)
    generic_identity_keys = {
        _normalized_text(value)
        for value in (
            "消息输入框",
            "聊天输入框",
            "输入消息",
            "message input",
            "message-input",
            "chat input",
            "chat-input",
            "QQ",
            "微信",
            "WeChat",
        )
    }
    for input_index, input_element in enumerate(elements):
        role = str(input_element.get("role") or "")
        supports = _reviewable_supports(input_element)
        if (
            role not in {"AXTextArea", "AXTextField"}
            or "type_text" not in supports
            or bool(input_element.get("protected"))
        ):
            continue
        input_semantics = " ".join(
            _clean_text(input_element.get(key), max_length=180)
            for key in (
                "label",
                "title",
                "description",
                "placeholder",
                "identifier",
            )
        ).casefold()
        if any(term in input_semantics for term in ("搜索", "查找", "search", "find")):
            continue
        identity_labels = list(
            dict.fromkeys(
                _clean_text(input_element.get(key), max_length=160)
                for key in ("label", "title", "description")
                if _clean_text(input_element.get(key), max_length=160)
            )
        )
        identity_labels = [
            label
            for label in identity_labels
            if (
                (label_key := _normalized_text(label))
                and len(label_key) >= 2
                and label_key not in generic_identity_keys
            )
        ]
        if not identity_labels:
            continue
        input_path = _element_tree_path(input_element)
        input_window = _element_window_path(input_element, window_paths)
        input_bounds = (
            input_element.get("bounds")
            if isinstance(input_element.get("bounds"), dict)
            else {}
        )
        input_x = int(round(float(input_bounds.get("x") or 0)))
        input_y = int(round(float(input_bounds.get("y") or 0)))
        input_width = int(round(float(input_bounds.get("width") or 0)))
        if input_width <= 2:
            continue
        header_candidates: list[
            tuple[int, int, int, int, dict[str, Any], str]
        ] = []
        for header_index, header in enumerate(elements):
            if header_index == input_index or not _has_screen_bounds(header):
                continue
            header_label = _element_label(header)
            if header_label not in identity_labels:
                continue
            header_role = str(header.get("role") or "")
            if header_role not in {
                "AXButton",
                "AXHeading",
                "AXStaticText",
                "AXGroup",
            }:
                continue
            if (
                input_window
                and _element_window_path(header, window_paths) != input_window
            ):
                continue
            header_bounds = (
                header.get("bounds")
                if isinstance(header.get("bounds"), dict)
                else {}
            )
            header_x = int(round(float(header_bounds.get("x") or 0)))
            header_y = int(round(float(header_bounds.get("y") or 0)))
            if (
                header_x < input_x - 80
                or header_y <= 0
                or header_y >= input_y - 160
            ):
                continue
            shared_depth = _common_path_depth(
                input_path,
                _element_tree_path(header),
            )
            minimum_shared_depth = len(input_window) + 1 if input_window else 2
            if shared_depth < minimum_shared_depth:
                continue
            role_priority = (
                0
                if header_role in {"AXButton", "AXHeading", "AXStaticText"}
                else 1
            )
            header_candidates.append(
                (
                    -shared_depth,
                    role_priority,
                    header_y,
                    header_index,
                    header,
                    header_label,
                )
            )
        if not header_candidates:
            continue
        header_candidates.sort(key=lambda item: item[:4])
        (
            negative_shared_depth,
            _role_priority,
            header_y,
            header_index,
            header,
            chat_label,
        ) = header_candidates[0]
        pane_shared_depth = -negative_shared_depth
        message_candidates: list[tuple[int, int, dict[str, Any]]] = []
        for message_index, message in enumerate(elements):
            if (
                message_index in {input_index, header_index}
                or str(message.get("role") or "") != "AXStaticText"
                or not _has_screen_bounds(message)
            ):
                continue
            if (
                input_window
                and _element_window_path(message, window_paths) != input_window
            ):
                continue
            if (
                _common_path_depth(
                    input_path,
                    _element_tree_path(message),
                )
                < pane_shared_depth
            ):
                continue
            message_label = _element_label(message)
            message_key = _normalized_text(message_label)
            if (
                not message_key
                or message_label == chat_label
                or len(message_key) < 2
                or re.fullmatch(
                    r"\d{2,4}[/:\-\s]\d{1,2}(?:[/:\-\s]\d{1,2})?"
                    r"(?:\s+\d{1,2}:\d{2})?",
                    message_label,
                )
            ):
                continue
            bounds = (
                message.get("bounds")
                if isinstance(message.get("bounds"), dict)
                else {}
            )
            message_x = int(round(float(bounds.get("x") or 0)))
            message_y = int(round(float(bounds.get("y") or 0)))
            message_width = int(round(float(bounds.get("width") or 0)))
            if (
                message_width < 20
                or message_x < input_x - 40
                or message_x > input_x + input_width + 40
                or message_y <= header_y
                or message_y >= input_y - 40
            ):
                continue
            message_candidates.append((-message_y, message_index, message))
        message_candidates.sort(key=lambda item: (item[0], item[1]))
        return {
            "chat_label": chat_label,
            "header_index": header_index,
            "input_index": input_index,
            "message_index": (
                message_candidates[0][1]
                if message_candidates
                else None
            ),
            "input_focused": bool(input_element.get("focused")),
        }
    return {}


def _collection_branch_has_subject(
    elements: list[dict[str, Any]],
    collection_index: int,
    query: str,
) -> bool:
    container_path = _element_tree_path(elements[collection_index])
    for element in elements:
        path = _element_tree_path(element)
        if (
            len(path) <= len(container_path)
            or len(path) - len(container_path) > 2
            or path[: len(container_path)] != container_path
            or str(element.get("role") or "")
            not in {"AXStaticText", "AXHeading"}
        ):
            continue
        label = _element_label(element)
        if label and not _collection_subject_mismatch(label, query):
            return True
    return False


def _collection_candidate_quality(
    elements: list[dict[str, Any]],
    collection_index: int,
    *,
    query: str,
) -> int:
    collection = elements[collection_index]
    role = str(collection.get("role") or "")
    child_count = _effective_collection_child_count(elements, collection_index)
    label_relevance = _collection_label_relevance(
        _element_label(collection),
        query,
    )
    if role in _NATIVE_COLLECTION_ROLES:
        quality = 420
    elif role == "AXScrollArea":
        quality = 170
    elif role == "AXGroup":
        if child_count <= 1 and label_relevance <= 0:
            return -1
        quality = 80
    else:
        return -1
    quality += label_relevance
    quality += min(20, child_count) * 5
    normalized_label = _normalized_text(_element_label(collection))
    normalized_query = _normalized_text(query)
    if (
        any(marker in normalized_label for marker in _SIDEBAR_LABEL_MARKERS)
        and not any(marker in normalized_query for marker in _SIDEBAR_LABEL_MARKERS)
    ):
        quality -= 300
    if str(collection.get("subrole") or "") == "AXWebApplication":
        quality += 35
    if _has_screen_bounds(collection):
        quality += 10
    return quality


def _nearest_collection_index(
    elements: list[dict[str, Any]],
    path_index: dict[tuple[int, ...], int],
    seed_index: int,
) -> int | None:
    seed = elements[seed_index]
    seed_path = _element_tree_path(seed)
    if not seed_path and str(seed.get("role") or "") not in _COLLECTION_ROLES:
        return None
    candidates: list[tuple[int, int, int]] = []
    for depth in range(0, min(10, len(seed_path)) + 1):
        candidate_index = (
            seed_index
            if depth == 0
            else path_index.get(seed_path[:-depth])
        )
        if candidate_index is None:
            continue
        quality = _collection_candidate_quality(
            elements,
            candidate_index,
            query="",
        )
        if quality >= 0:
            candidates.append((-quality, depth, candidate_index))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def _best_collection_index(
    elements: list[dict[str, Any]],
    path_index: dict[tuple[int, ...], int],
    direct_matches: list[tuple[int, bool, int, dict[str, Any]]],
    *,
    query: str,
    terms: tuple[str, ...],
    window_path: tuple[int, ...] = (),
) -> int | None:
    candidates: list[tuple[int, int, int, int, int]] = []
    seen: set[int] = set()
    for _score, _matched, seed_index, seed in direct_matches:
        if (
            window_path
            and _element_tree_path(seed)[: len(window_path)] != window_path
        ):
            continue
        seed_path = _element_tree_path(seed)
        for depth in range(0, min(10, len(seed_path)) + 1):
            collection_index = (
                seed_index
                if depth == 0
                else path_index.get(seed_path[:-depth])
            )
            if collection_index is None or collection_index in seen:
                continue
            if (
                window_path
                and _element_tree_path(elements[collection_index])[
                    : len(window_path)
                ]
                != window_path
            ):
                continue
            quality = _collection_candidate_quality(
                elements,
                collection_index,
                query=query,
            )
            if quality < 0:
                continue
            seen.add(collection_index)
            collection_score, collection_matched = _semantic_score(
                elements[collection_index],
                query,
                terms,
            )
            candidates.append(
                (
                    -quality,
                    -int(collection_matched),
                    -collection_score,
                    depth,
                    collection_index,
                )
            )
    # A misspelled item label may leave only an unrelated toolbar as a direct
    # match. Still consider globally visible collection labels whose semantic
    # base (for example “会话” in “会话列表”) is present in the query.
    for collection_index, collection in enumerate(elements):
        if collection_index in seen:
            continue
        if (
            window_path
            and _element_tree_path(collection)[: len(window_path)]
            != window_path
        ):
            continue
        label_relevance = _collection_label_relevance(
            _element_label(collection),
            query,
        )
        native_fallback = bool(
            str(collection.get("role") or "") in _NATIVE_COLLECTION_ROLES
            and _effective_collection_child_count(elements, collection_index) > 0
            and _has_screen_bounds(collection)
        )
        if label_relevance < 180 and not native_fallback:
            continue
        quality = _collection_candidate_quality(
            elements,
            collection_index,
            query=query,
        )
        if quality < 0:
            continue
        seen.add(collection_index)
        collection_score, collection_matched = _semantic_score(
            collection,
            query,
            terms,
        )
        candidates.append(
            (
                -quality,
                -int(collection_matched),
                -collection_score,
                99,
                collection_index,
            )
        )
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][4]


def _collection_direct_child_indices(
    elements: list[dict[str, Any]],
    container_path: tuple[int, ...],
) -> list[int]:
    children = [
        index
        for index, element in enumerate(elements)
        if (
            (path := _element_tree_path(element))
            and len(path) == len(container_path) + 1
            and path[:-1] == container_path
        )
    ]
    children.sort(key=lambda index: _element_tree_path(elements[index]))
    return children


def _collection_branch_labels(
    elements: list[dict[str, Any]],
    root_indices: list[int],
    *,
    container_path: tuple[int, ...],
    max_depth: int = 4,
) -> dict[int, str]:
    root_depth = len(container_path) + 1
    root_by_path = {
        _element_tree_path(elements[root_index]): root_index
        for root_index in root_indices
    }
    labels = {
        root_index: label
        for root_index in root_indices
        if (label := _element_label(elements[root_index]))
    }
    best_candidates: dict[
        int,
        tuple[int, int, tuple[int, ...], str],
    ] = {}
    for element in elements:
        if element.get("protected"):
            continue
        path = _element_tree_path(element)
        if (
            not path
            or len(path) <= root_depth
            or len(path) - root_depth > max_depth
        ):
            continue
        root_index = root_by_path.get(path[:root_depth])
        if root_index is None or root_index in labels:
            continue
        label = _element_label(element)
        if not label:
            continue
        role = str(element.get("role") or "")
        role_priority = 0 if role in {"AXStaticText", "AXHeading"} else 1
        visible_priority = 0 if _has_screen_bounds(element) else 1
        candidate = (
            role_priority,
            visible_priority,
            path,
            label,
        )
        current = best_candidates.get(root_index)
        if current is None or candidate[:3] < current[:3]:
            best_candidates[root_index] = candidate
    labels.update(
        {
            root_index: candidate[3]
            for root_index, candidate in best_candidates.items()
        }
    )
    return labels


def _collection_items(
    elements: list[dict[str, Any]],
    collection_index: int,
    *,
    app_id: str,
    anchor_label: str = "",
    query: str = "",
) -> list[tuple[int, dict[str, Any]]]:
    collection = elements[collection_index]
    container_path = _element_tree_path(collection)
    root_indices = _collection_direct_child_indices(elements, container_path)
    logical_container_path = container_path
    # Some apps wrap the logical rows in one extra collection/group node.
    # Descend only when that wrapper is the sole direct child and it exposes
    # multiple branches; this keeps one stable root per logical item.
    if len(root_indices) == 1:
        nested_index = root_indices[0]
        nested_path = _element_tree_path(elements[nested_index])
        nested_roots = _collection_direct_child_indices(elements, nested_path)
        if len(nested_roots) > 1:
            root_indices = nested_roots
            logical_container_path = nested_path
    anchor = _clean_text(anchor_label, max_length=180) or _element_label(collection)
    branch_labels = _collection_branch_labels(
        elements,
        root_indices,
        container_path=logical_container_path,
    )
    branch_urls: dict[int, str] = {}
    if _normalized_text(app_id) == "finder":
        root_depth = len(logical_container_path) + 1
        root_by_path = {
            _element_tree_path(elements[root_index]): root_index
            for root_index in root_indices
        }
        for child in elements:
            child_path = _element_tree_path(child)
            if len(child_path) < root_depth:
                continue
            root_index = root_by_path.get(child_path[:root_depth])
            if root_index is None:
                continue
            child_url = _clean_text(child.get("url"), max_length=600)
            if child_url.casefold().startswith("file:"):
                branch_urls.setdefault(root_index, child_url)
    open_proxies: dict[
        int,
        tuple[tuple[int, int, tuple[int, ...]], dict[str, Any]],
    ] = {}
    if (
        _normalized_text(app_id) == "finder"
        and not _menu_action_requested(query)
        and any(
            term in str(query or "").casefold()
            for term in ("打开", "进入", "open")
        )
    ):
        root_depth = len(logical_container_path) + 1
        root_by_path = {
            _element_tree_path(elements[root_index]): root_index
            for root_index in root_indices
        }
        for element in elements:
            path = _element_tree_path(element)
            if len(path) <= root_depth:
                continue
            root_index = root_by_path.get(path[:root_depth])
            if root_index is None:
                continue
            supports = _reviewable_supports(element)
            ax_ref = element.get("ax_ref")
            if "open" not in supports or not isinstance(ax_ref, dict):
                continue
            url = _clean_text(element.get("url"), max_length=600).casefold()
            priority = (
                0 if url.startswith("file:") else 1,
                0 if str(element.get("role") or "") == "AXTextField" else 1,
                path,
            )
            current = open_proxies.get(root_index)
            if current is None or priority < current[0]:
                open_proxies[root_index] = (priority, element)
    menu_proxies: dict[
        int,
        tuple[tuple[int, int, int, tuple[int, ...]], dict[str, Any]],
    ] = {}
    if _normalized_text(app_id) == "finder" and _menu_action_requested(query):
        root_depth = len(logical_container_path) + 1
        root_by_path = {
            _element_tree_path(elements[root_index]): root_index
            for root_index in root_indices
        }
        for element in elements:
            path = _element_tree_path(element)
            if len(path) <= root_depth:
                continue
            root_index = root_by_path.get(path[:root_depth])
            if root_index is None:
                continue
            supports = _reviewable_supports(element)
            ax_ref = element.get("ax_ref")
            if "show_menu" not in supports or not isinstance(ax_ref, dict):
                continue
            url = _clean_text(element.get("url"), max_length=600).casefold()
            priority = (
                0 if url.startswith("file:") else 1,
                0 if str(element.get("role") or "") == "AXTextField" else 1,
                0 if _has_screen_bounds(element) else 1,
                path,
            )
            current = menu_proxies.get(root_index)
            if current is None or priority < current[0]:
                menu_proxies[root_index] = (priority, element)
    selection_requested = bool(
        _normalized_text(app_id) == "finder"
        and not _menu_action_requested(query)
        and any(
            term in str(query or "").casefold()
            for term in ("选择", "选中", "select")
        )
    )
    selection_proxies: dict[
        int,
        tuple[tuple[int, int, tuple[int, ...]], dict[str, Any]],
    ] = {}
    if selection_requested:
        root_depth = len(logical_container_path) + 1
        root_by_path = {
            _element_tree_path(elements[root_index]): root_index
            for root_index in root_indices
        }
        for element in elements:
            path = _element_tree_path(element)
            if len(path) <= root_depth:
                continue
            root_index = root_by_path.get(path[:root_depth])
            if root_index is None:
                continue
            if (
                element.get("enabled") is False
                or str(element.get("role") or "")
                not in _GEOMETRY_CLICK_ROLES
                or not _has_screen_bounds(element)
            ):
                continue
            url = _clean_text(element.get("url"), max_length=600).casefold()
            priority = (
                0 if url.startswith("file:") else 1,
                0 if str(element.get("role") or "") == "AXImage" else 1,
                path,
            )
            current = selection_proxies.get(root_index)
            if current is None or priority < current[0]:
                selection_proxies[root_index] = (priority, element)
    items: list[tuple[int, dict[str, Any]]] = []
    for root_index in root_indices:
        root = elements[root_index]
        role = str(root.get("role") or "")
        if role in {"AXColumn", "AXScrollBar", "AXSplitter", "AXValueIndicator"}:
            continue
        if not _has_screen_bounds(root) and not _reviewable_supports(root):
            continue
        label = branch_labels.get(root_index, "")
        if not label:
            continue
        open_proxy = open_proxies.get(root_index)
        menu_proxy = menu_proxies.get(root_index)
        root_geometry_eligible = bool(
            root.get("enabled") is not False
            and role in _GEOMETRY_CLICK_ROLES
            and _has_screen_bounds(root)
        )
        selection_proxy = (
            selection_proxies.get(root_index)
            if selection_requested and not root_geometry_eligible
            else None
        )
        item = dict(
            menu_proxy[1]
            if menu_proxy is not None
            else open_proxy[1]
            if open_proxy is not None
            else selection_proxy[1]
            if selection_proxy is not None
            else root
        )
        if not _element_label(item):
            item["label"] = label
            item["label_source"] = "descendant"
        if anchor:
            item["semantic_path"] = f"{anchor} > {label}"
        item["context_relation"] = "collection_item"
        item["context_anchor"] = anchor
        if _menu_action_requested(query):
            item = _menu_action_element(item, query)
        elif open_proxy is not None:
            item["activation"] = "open"
            item["ax_ref"] = _signed_ax_ref(
                dict(item["ax_ref"]),
                activation="open",
            )
            item["ax_ref_source"] = (
                _clean_text(item.get("ax_ref_source"), max_length=80)
                or "native_action"
            )
        else:
            item = _geometry_hit_test_element(
                app_id,
                item,
                descendant_label=label,
                descendant_url=branch_urls.get(root_index, ""),
                ignore_native_supports=bool(selection_proxy),
            )
        items.append((root_index, item))
    return items


def _collection_context_indices(
    elements: list[dict[str, Any]],
    path_index: dict[tuple[int, ...], int],
    seed_index: int,
    *,
    limit: int,
) -> list[int]:
    seed_path = _element_tree_path(elements[seed_index])
    if not seed_path:
        return []
    container_index = _nearest_collection_index(elements, path_index, seed_index)
    container_path = (
        _element_tree_path(elements[container_index])
        if container_index is not None
        else seed_path[:-1]
    )
    candidates: list[tuple[int, tuple[int, ...], int]] = []
    for index, element in enumerate(elements):
        if index == seed_index or not _element_label(element):
            continue
        path = _element_tree_path(element)
        if (
            not path
            or len(path) <= len(container_path)
            or path[: len(container_path)] != container_path
            or len(path) - len(container_path) > 4
        ):
            continue
        supports = _reviewable_supports(element)
        role = str(element.get("role") or "")
        priority = 0 if supports and role in _ROW_ROLES else 1 if supports else 2
        candidates.append((priority, path, index))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in candidates[: max(0, limit)]]


def _sanitize_element_for_storage(element: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(element)
    protected = bool(result.get("protected")) or str(result.get("subrole") or "") == "AXSecureTextField"
    if not protected:
        return result
    result.pop("value", None)
    ax_ref = result.get("ax_ref")
    if isinstance(ax_ref, dict):
        safe_ref = dict(ax_ref)
        safe_ref.pop("value", None)
        result["ax_ref"] = safe_ref
    return result


def _snapshot_index(elements: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    role_counts: Counter[str] = Counter()
    support_counts: Counter[str] = Counter()
    actionable = 0
    focused = 0
    selected = 0
    for element in elements:
        role = _clean_text(element.get("role"), max_length=80)
        if role:
            role_counts[role] += 1
        supports = element.get("supports") if isinstance(element.get("supports"), list) else []
        clean_supports = [_clean_text(item, max_length=40) for item in supports if _clean_text(item, max_length=40)]
        support_counts.update(clean_supports)
        actionable += bool(set(clean_supports).intersection(_REVIEWABLE_SUPPORTS))
        focused += bool(element.get("focused"))
        selected += bool(element.get("selected"))
    return {
        "roles": dict(sorted(role_counts.items())),
        "supports": dict(sorted(support_counts.items())),
        "actionable": actionable,
        "focused": focused,
        "selected": selected,
        "window_signatures": snapshot_window_signatures(elements),
    }


def snapshot_window_signatures(
    elements: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> list[str]:
    signatures: list[str] = []
    for element in elements:
        if str(element.get("role") or "") != "AXWindow":
            continue
        bounds = (
            element.get("bounds")
            if isinstance(element.get("bounds"), dict)
            else {}
        )
        payload = {
            "subrole": _clean_text(element.get("subrole"), max_length=80),
            "identifier": _clean_text(element.get("identifier"), max_length=160),
            "title": _clean_text(
                element.get("title") or element.get("label"),
                max_length=160,
            ),
            "bounds": {
                key: int(round(float(bounds.get(key) or 0)))
                for key in ("x", "y", "width", "height")
            },
        }
        signatures.append(
            hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:20]
        )
    return sorted(set(signatures))


@dataclass(frozen=True)
class AXSnapshot:
    app_id: str
    app_name: str
    bundle_id: str
    aliases: tuple[str, ...]
    pid: int
    snapshot_id: str
    created_at: float
    elements: tuple[dict[str, Any], ...]
    visited_count: int
    capture_truncated: bool
    index: dict[str, Any]
    stale_reason: str = ""
    source: str = "live"


class AXSnapshotCache:
    """Chunked persistent AX snapshots with bounded semantic top-k retrieval."""

    def __init__(
        self,
        *,
        ttl_sec: float | None = None,
        max_apps: int = AX_CACHE_MAX_APPS,
        clock: Callable[[], float] = time.time,
        storage_dir: str | Path | None = None,
        chunk_size: int = AX_STORE_CHUNK_SIZE,
    ) -> None:
        self._ttl_sec = None if ttl_sec is None else max(1.0, min(86_400.0, float(ttl_sec)))
        self._max_apps = max(1, min(64, int(max_apps)))
        self._clock = clock
        self._storage_dir = Path(storage_dir).resolve() if storage_dir is not None else None
        self._chunk_size = max(50, min(5_000, int(chunk_size)))
        self._entries: OrderedDict[str, AXSnapshot] = OrderedDict()
        self._lock = RLock()

    @property
    def ttl_sec(self) -> float | None:
        return self._ttl_sec

    @property
    def storage_dir(self) -> Path | None:
        return self._storage_dir

    @staticmethod
    def _safe_app_id(app_id: object) -> str:
        return re.sub(r"[^a-z0-9_.-]+", "-", _clean_text(app_id, max_length=80).casefold()).strip("-")

    def _app_dir(self, app_id: str) -> Path | None:
        return self._storage_dir / app_id if self._storage_dir is not None else None

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _snapshot_meta(snapshot: AXSnapshot, chunks: list[str]) -> dict[str, Any]:
        return {
            "schema_version": AX_STORE_SCHEMA_VERSION,
            "app_id": snapshot.app_id,
            "app_name": snapshot.app_name,
            "bundle_id": snapshot.bundle_id,
            "aliases": list(snapshot.aliases),
            "pid": snapshot.pid,
            "snapshot_id": snapshot.snapshot_id,
            "created_at": snapshot.created_at,
            "element_count": len(snapshot.elements),
            "visited_count": snapshot.visited_count,
            "capture_truncated": snapshot.capture_truncated,
            "full_tree": not snapshot.capture_truncated,
            "index": deepcopy(snapshot.index),
            "stale_reason": snapshot.stale_reason,
            "chunks": list(chunks),
        }

    def _persist_snapshot(self, snapshot: AXSnapshot) -> None:
        app_dir = self._app_dir(snapshot.app_id)
        if app_dir is None:
            return
        app_dir.mkdir(parents=True, exist_ok=True)
        temporary_dir = app_dir / f".tmp-{uuid4().hex}"
        final_dir = app_dir / snapshot.snapshot_id
        temporary_dir.mkdir(parents=False, exist_ok=False)
        chunks: list[str] = []
        try:
            for offset in range(0, len(snapshot.elements), self._chunk_size):
                name = f"elements-{offset // self._chunk_size:05d}.jsonl"
                chunk_path = temporary_dir / name
                lines = [
                    json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    for item in snapshot.elements[offset : offset + self._chunk_size]
                ]
                chunk_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
                chunks.append(name)
            self._write_json_atomic(temporary_dir / "meta.json", self._snapshot_meta(snapshot, chunks))
            temporary_dir.replace(final_dir)
            self._write_json_atomic(
                app_dir / "current.json",
                {
                    "schema_version": AX_STORE_SCHEMA_VERSION,
                    "app_id": snapshot.app_id,
                    "snapshot_id": snapshot.snapshot_id,
                },
            )
            for child in app_dir.iterdir():
                if (
                    child.is_dir()
                    and child.name.startswith("axs-")
                    and child.name != snapshot.snapshot_id
                ):
                    shutil.rmtree(child, ignore_errors=True)
        except Exception:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise

    def _persist_snapshot_meta(self, snapshot: AXSnapshot) -> None:
        app_dir = self._app_dir(snapshot.app_id)
        if app_dir is None:
            return
        meta_path = app_dir / snapshot.snapshot_id / "meta.json"
        if not meta_path.exists():
            return
        try:
            current = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return
        chunks = current.get("chunks") if isinstance(current, dict) else []
        safe_chunks = [str(item) for item in chunks if Path(str(item)).name == str(item)]
        self._write_json_atomic(meta_path, self._snapshot_meta(snapshot, safe_chunks))

    def _load_persisted_meta(self, app_id: str) -> tuple[dict[str, Any], Path] | None:
        app_dir = self._app_dir(app_id)
        if app_dir is None:
            return None
        try:
            pointer = json.loads((app_dir / "current.json").read_text(encoding="utf-8"))
            snapshot_id = str(pointer.get("snapshot_id") or "").strip()
            if not snapshot_id.startswith("axs-") or Path(snapshot_id).name != snapshot_id:
                return None
            snapshot_dir = app_dir / snapshot_id
            meta = json.loads((snapshot_dir / "meta.json").read_text(encoding="utf-8"))
            if (
                int(meta.get("schema_version") or 0) != AX_STORE_SCHEMA_VERSION
                or str(meta.get("app_id") or "") != app_id
                or str(meta.get("snapshot_id") or "") != snapshot_id
            ):
                return None
            return meta, snapshot_dir
        except Exception:
            return None

    def _load_persisted_snapshot(self, app_id: str) -> AXSnapshot | None:
        persisted = self._load_persisted_meta(app_id)
        if persisted is None:
            return None
        meta, snapshot_dir = persisted
        try:
            elements: list[dict[str, Any]] = []
            chunks = meta.get("chunks") if isinstance(meta.get("chunks"), list) else []
            for raw_name in chunks:
                name = str(raw_name or "")
                if not name or Path(name).name != name:
                    return None
                chunk_path = snapshot_dir / name
                for line in chunk_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    if isinstance(item, dict):
                        elements.append(item)
                    if len(elements) > AX_STORE_MAX_ELEMENTS:
                        return None
            if len(elements) != max(0, int(meta.get("element_count") or 0)):
                return None
            return AXSnapshot(
                app_id=app_id,
                app_name=_clean_text(meta.get("app_name"), max_length=160),
                bundle_id=_clean_text(meta.get("bundle_id"), max_length=200),
                aliases=tuple(
                    _clean_text(item, max_length=160)
                    for item in (meta.get("aliases") if isinstance(meta.get("aliases"), list) else [])
                    if _clean_text(item, max_length=160)
                ),
                pid=max(0, int(meta.get("pid") or 0)),
                snapshot_id=str(meta.get("snapshot_id") or ""),
                created_at=float(meta.get("created_at") or 0.0),
                elements=tuple(elements),
                visited_count=max(0, int(meta.get("visited_count") or 0)),
                capture_truncated=bool(meta.get("capture_truncated")),
                index=deepcopy(meta.get("index") if isinstance(meta.get("index"), dict) else {}),
                stale_reason=_clean_text(meta.get("stale_reason"), max_length=120),
                source="disk",
            )
        except Exception:
            return None

    def _remember(self, snapshot: AXSnapshot) -> AXSnapshot:
        self._entries.pop(snapshot.app_id, None)
        self._entries[snapshot.app_id] = snapshot
        while len(self._entries) > self._max_apps:
            self._entries.popitem(last=False)
        return snapshot

    def _snapshot(self, app_id: str) -> AXSnapshot | None:
        snapshot = self._entries.get(app_id)
        if snapshot is not None:
            self._entries.move_to_end(app_id)
            return snapshot
        snapshot = self._load_persisted_snapshot(app_id)
        return self._remember(snapshot) if snapshot is not None else None

    def put(
        self,
        app_id: str,
        *,
        app_name: str = "",
        bundle_id: str = "",
        aliases: tuple[str, ...] | list[str] = (),
        pid: int,
        elements: list[dict[str, Any]],
        visited_count: int,
        capture_truncated: bool,
    ) -> AXSnapshot:
        key = self._safe_app_id(app_id)
        if not key:
            raise ValueError("app_id is required")
        captured = tuple(
            _sanitize_element_for_storage(item)
            for item in elements[:AX_STORE_MAX_ELEMENTS]
            if isinstance(item, dict)
        )
        created_at = self._clock()
        seed = json.dumps(
            {
                "app_id": key,
                "pid": int(pid),
                "created_at": created_at,
                "nonce": uuid4().hex,
                "fingerprints": [
                    (item.get("ax_ref") or {}).get("fingerprint")
                    for item in captured
                    if isinstance(item.get("ax_ref"), dict)
                ],
                "count": len(captured),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot = AXSnapshot(
            app_id=key,
            app_name=_clean_text(app_name, max_length=160),
            bundle_id=_clean_text(bundle_id, max_length=200),
            aliases=tuple(
                dict.fromkeys(
                    _clean_text(item, max_length=160)
                    for item in aliases
                    if _clean_text(item, max_length=160)
                )
            ),
            pid=int(pid),
            snapshot_id=f"axs-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}",
            created_at=created_at,
            elements=captured,
            visited_count=max(0, int(visited_count)),
            capture_truncated=bool(capture_truncated),
            index=_snapshot_index(captured),
            stale_reason="capture_truncated" if capture_truncated else "",
            source="live",
        )
        with self._lock:
            self._persist_snapshot(snapshot)
            self._remember(snapshot)
        return snapshot

    def invalidate(self, app_id: str, *, reason: str = "ui_changed") -> None:
        key = self._safe_app_id(app_id)
        stale_reason = _clean_text(reason, max_length=120) or "ui_changed"
        with self._lock:
            snapshot = self._entries.get(key)
            if snapshot is None:
                persisted = self._load_persisted_meta(key)
                if persisted is not None:
                    meta, snapshot_dir = persisted
                    self._write_json_atomic(
                        snapshot_dir / "meta.json",
                        {**meta, "stale_reason": stale_reason},
                    )
                return
            stale = replace(
                snapshot,
                stale_reason=stale_reason,
            )
            self._remember(stale)
            self._persist_snapshot_meta(stale)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            app_ids = set(self._entries)
            if self._storage_dir is not None and self._storage_dir.exists():
                app_ids.update(
                    child.name
                    for child in self._storage_dir.iterdir()
                    if child.is_dir() and self._safe_app_id(child.name) == child.name
                )
            apps: list[dict[str, Any]] = []
            total_bytes = 0
            for app_id in sorted(app_ids):
                snapshot = self._entries.get(app_id)
                persisted = self._load_persisted_meta(app_id)
                meta = persisted[0] if persisted is not None else {}
                snapshot_dir = persisted[1] if persisted is not None else None
                if snapshot is None and not meta:
                    continue
                size_bytes = 0
                if snapshot_dir is not None and snapshot_dir.exists():
                    try:
                        size_bytes = sum(
                            path.stat().st_size
                            for path in snapshot_dir.iterdir()
                            if path.is_file()
                        )
                    except Exception:
                        size_bytes = 0
                total_bytes += size_bytes
                element_count = (
                    len(snapshot.elements)
                    if snapshot is not None
                    else max(0, int(meta.get("element_count") or 0))
                )
                aliases = (
                    list(snapshot.aliases)
                    if snapshot is not None
                    else [
                        _clean_text(item, max_length=160)
                        for item in (meta.get("aliases") if isinstance(meta.get("aliases"), list) else [])
                        if _clean_text(item, max_length=160)
                    ]
                )
                created_at = (
                    snapshot.created_at
                    if snapshot is not None
                    else float(meta.get("created_at") or 0.0)
                )
                stored_stale_reason = (
                    snapshot.stale_reason
                    if snapshot is not None
                    else _clean_text(meta.get("stale_reason"), max_length=120)
                )
                age_sec = max(0.0, now - created_at)
                stale_reason = (
                    stored_stale_reason
                    or (
                        "snapshot_expired"
                        if self._ttl_sec is not None and age_sec > self._ttl_sec
                        else ""
                    )
                )
                apps.append(
                    {
                        "app_id": app_id,
                        "app_name": (
                            snapshot.app_name
                            if snapshot is not None
                            else _clean_text(meta.get("app_name"), max_length=160)
                        ),
                        "bundle_id": (
                            snapshot.bundle_id
                            if snapshot is not None
                            else _clean_text(meta.get("bundle_id"), max_length=200)
                        ),
                        "aliases": aliases,
                        "pid": snapshot.pid if snapshot is not None else max(0, int(meta.get("pid") or 0)),
                        "snapshot_id": (
                            snapshot.snapshot_id
                            if snapshot is not None
                            else str(meta.get("snapshot_id") or "")
                        ),
                        "created_at": created_at,
                        "age_ms": int(age_sec * 1000),
                        "element_count": element_count,
                        "visited_count": (
                            snapshot.visited_count
                            if snapshot is not None
                            else max(0, int(meta.get("visited_count") or 0))
                        ),
                        "capture_truncated": (
                            snapshot.capture_truncated
                            if snapshot is not None
                            else bool(meta.get("capture_truncated"))
                        ),
                        "stale": bool(stale_reason),
                        "stale_reason": stale_reason,
                        "size_bytes": size_bytes,
                    }
                )
            return {
                "schema_version": AX_STORE_SCHEMA_VERSION,
                "storage_dir": str(self._storage_dir or ""),
                "persistent": self._storage_dir is not None,
                "app_count": len(apps),
                "element_count": sum(int(item["element_count"]) for item in apps),
                "size_bytes": total_bytes,
                "apps": apps,
            }

    def find_app_id(self, *identities: object) -> str:
        keys = {
            _normalized_text(value)
            for value in identities
            if _normalized_text(value)
        }
        if not keys:
            return ""
        with self._lock:
            for identity in identities:
                app_id = self._safe_app_id(identity)
                app_dir = self._app_dir(app_id)
                if app_id and (
                    app_id in self._entries
                    or (app_dir is not None and (app_dir / "current.json").is_file())
                ):
                    return app_id
        matches: list[str] = []
        for item in self.status()["apps"]:
            candidate_keys = {
                _normalized_text(item.get("app_id")),
                _normalized_text(item.get("app_name")),
                _normalized_text(item.get("bundle_id")),
                *(
                    _normalized_text(alias)
                    for alias in (item.get("aliases") if isinstance(item.get("aliases"), list) else [])
                ),
            }
            if keys & {key for key in candidate_keys if key}:
                matches.append(str(item.get("app_id") or ""))
        return matches[0] if len(set(matches)) == 1 else ""

    def search(
        self,
        app_id: str,
        *,
        pid: int = 0,
        query: str = "",
        limit: int = AX_SEARCH_DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        key = self._safe_app_id(app_id)
        now = self._clock()
        with self._lock:
            snapshot = self._snapshot(key)
            if snapshot is None:
                return {"status": "miss", "reason": "snapshot_not_found", "app_id": key}
            age_sec = max(0.0, now - snapshot.created_at)
            self._entries.move_to_end(key)
        requested_pid = max(0, int(pid or 0))
        process_changed = bool(requested_pid and snapshot.pid and snapshot.pid != requested_pid)
        ttl_expired = bool(
            self._ttl_sec is not None
            and age_sec > self._ttl_sec
        )
        stale_reason = (
            snapshot.stale_reason
            or ("process_changed" if process_changed else "")
            or ("snapshot_expired" if ttl_expired else "")
        )

        try:
            requested_limit = int(limit)
        except (TypeError, ValueError):
            requested_limit = AX_SEARCH_DEFAULT_LIMIT
        result_limit = max(1, min(AX_SEARCH_MAX_LIMIT, requested_limit))
        clean_query = _clean_text(query, max_length=240)
        intent_text = clean_query.casefold()
        view_title_query = any(
            term in intent_text
            for term in (
                "页面标题",
                "视图标题",
                "窗口标题",
                "页面名称",
                "视图名称",
                "窗口名称",
                "page title",
                "view title",
                "window title",
                "axtitle",
            )
        )
        ax_tokens = tuple(
            dict.fromkeys(re.findall(r"ax[a-z0-9]+", intent_text))
        )
        ax_attribute_tokens = {
            "axactions",
            "axcancel",
            "axchildren",
            "axconfirm",
            "axdecrement",
            "axdescription",
            "axenabled",
            "axfocused",
            "axidentifier",
            "axincrement",
            "axopen",
            "axplaceholdervalue",
            "axposition",
            "axpress",
            "axraise",
            "axrole",
            "axselected",
            "axshowmenu",
            "axsize",
            "axsubrole",
            "axtitle",
            "axurl",
            "axvalue",
            "axwindows",
        }
        requested_roles = tuple(
            token
            for token in ax_tokens
            if token not in ax_attribute_tokens
        )
        indexed_roles = {
            str(role).casefold()
            for role in (
                snapshot.index.get("roles", {})
                if isinstance(snapshot.index, dict)
                else {}
            )
        }
        finder_desktop_role_adaptation = bool(
            key == "finder"
            and requested_roles
            and not indexed_roles.intersection(requested_roles)
            and indexed_roles.issuperset({"axscrollarea", "aximage"})
            and any(
                role in requested_roles
                for role in ("axwindow", "axlist", "axoutline", "axrow")
            )
            and any(
                term in intent_text
                for term in (
                    "桌面",
                    "文件",
                    "项目",
                    "可见",
                    "desktop",
                    "file",
                    "item",
                    "visible",
                )
            )
            and not any(
                term in intent_text
                for term in _ACTION_QUERY_TERMS
            )
        )
        virtual_collection_role_adaptation = (
            "axcollection" in requested_roles
        )
        base_requested_roles = (
            ("axscrollarea", "aximage")
            if finder_desktop_role_adaptation
            else (
                tuple(
                    dict.fromkeys(
                        (
                            *(
                                role
                                for role in requested_roles
                                if role != "axcollection"
                            ),
                            "axgrid",
                            "axlist",
                            "axoutline",
                            "axtable",
                            "axscrollarea",
                            "axgroup",
                        )
                    )
                )
                if virtual_collection_role_adaptation
                else requested_roles
            )
        )
        descriptive_context_roles: tuple[str, ...] = tuple(
            dict.fromkeys(
                (
                    *(
                        ("axwindow",)
                        if view_title_query
                        else ()
                    ),
                    *(
                        ("axstatictext",)
                        if any(
                            term in intent_text
                            for term in (
                                "账号提示",
                                "账户提示",
                                "登录用户提示",
                                "当前账号",
                                "当前账户",
                                "account prompt",
                                "login user",
                                "current account",
                            )
                        )
                        else ()
                    ),
                )
            )
        )
        effective_requested_roles = tuple(
            dict.fromkeys(
                (*base_requested_roles, *descriptive_context_roles)
            )
        )
        role_free_query = re.sub(r"ax[a-z0-9]+", " ", intent_text)
        role_only_query = bool(
            requested_roles
            and not _normalized_text(role_free_query)
        )
        role_listing_residual = _normalized_text(role_free_query)
        for identity in sorted(
            {
                _normalized_text(snapshot.app_id),
                _normalized_text(snapshot.app_name),
                _normalized_text(snapshot.bundle_id),
                *(
                    _normalized_text(alias)
                    for alias in snapshot.aliases
                ),
            },
            key=len,
            reverse=True,
        ):
            if len(identity) >= 2:
                role_listing_residual = role_listing_residual.replace(
                    identity,
                    "",
                )
        for filler in sorted(
            (
            "可见",
            "可用",
            "可操作",
            "已启用",
            "按钮",
            "名称",
            "名字",
            "所有",
            "全部",
            "列出",
            "读取",
            "获取",
            "返回",
            "当前",
            "页面",
            "页面类型",
            "账号提示",
            "账户提示",
            "登录用户提示",
            "账号",
            "账户",
            "提示",
            "主要",
            "元素",
            "控件",
            "完整",
            "结果",
            "信息",
            "文字",
            "结构",
            "结构化",
            "以及",
            "和",
            "的",
            "visible",
            "available",
            "usable",
            "enabled",
            "button",
            "buttons",
            "name",
            "names",
            "all",
            "list",
            "read",
            "get",
            "return",
            "current",
            "page",
            "type",
            "login",
            "user",
            "prompt",
            "element",
            "elements",
            "control",
            "controls",
            "complete",
            "result",
            "results",
            "information",
            "text",
            "structure",
            "structured",
            "and",
            ),
            key=lambda value: len(_normalized_text(value)),
            reverse=True,
        ):
            role_listing_residual = role_listing_residual.replace(
                _normalized_text(filler),
                "",
            )
        role_listing_query = bool(
            requested_roles
            and not role_listing_residual
        )
        terms = tuple(
            term
            for term in _query_terms(clean_query)
            if term not in ax_tokens
        )
        contextual_elements, path_index = _hierarchy_enriched_elements(
            snapshot.elements,
            app_id=key,
        )
        ranked: list[tuple[int, bool, int, dict[str, Any]]] = []
        for index, element in enumerate(contextual_elements):
            score, matched = _semantic_score(element, clean_query, terms)
            if (
                (role_only_query or role_listing_query)
                and str(element.get("role") or "").casefold()
                in effective_requested_roles
            ):
                score += 200
                matched = True
            elif (
                finder_desktop_role_adaptation
                and str(element.get("role") or "").casefold()
                in effective_requested_roles
            ):
                score += 200
                matched = True
            ranked.append((score, matched, index, element))
        ranked.sort(key=lambda item: (-int(item[1]), -item[0], item[2]))

        matched_identities = {
            _element_identity(element)
            for _score, matched, _index, element in ranked
            if matched
        }
        semantic_matches = [item for item in ranked if item[1]]
        direct_matches = [
            item
            for item in semantic_matches
            if (
                not requested_roles
                or str(item[3].get("role") or "").casefold()
                in effective_requested_roles
            )
        ]
        menu_query = (
            "axmenuitem" in requested_roles
            or "菜单" in intent_text
            or "menu" in intent_text
        )
        actionable_match_count = len(
            {
                _element_identity(element)
                for _score, _matched, _index, element in direct_matches
                if isinstance(element.get("ax_ref"), dict)
                and bool(_reviewable_supports(element))
                and _action_support_allowed(element, clean_query)
                and not (
                    str(element.get("role") or "") == "AXMenuItem"
                    and not _has_screen_bounds(element)
                    and not menu_query
                )
            }
        )
        guarded_actionable_match_count = len(
            {
                _element_identity(element)
                for _score, _matched, _index, element in direct_matches
                if isinstance(element.get("ax_ref"), dict)
                and bool(_reviewable_supports(element))
                and not _action_support_allowed(element, clean_query)
            }
        )
        visible_match_count = len(
            {
                _element_identity(element)
                for _score, _matched, _index, element in direct_matches
                if _has_screen_bounds(element)
            }
        )
        non_hidden_match_count = len(
            {
                _element_identity(element)
                for _score, _matched, _index, element in direct_matches
                if not (
                    str(element.get("role") or "") == "AXMenuItem"
                    and not _has_screen_bounds(element)
                )
            }
        )
        non_media_action_terms = tuple(
            term
            for term in _ACTION_QUERY_TERMS
            if term not in {"播放", "暂停", "play", "pause"}
        )
        action_query = (
            any(term in intent_text for term in non_media_action_terms)
            or _media_playback_requested(intent_text)
            or _menu_action_requested(clean_query)
        )
        all_window_paths = _window_paths(contextual_elements)
        focused_window_paths = _window_paths(
            contextual_elements,
            focused_only=True,
        )
        preferred_window_path: tuple[int, ...] = ()
        cross_window_ambiguous = False
        cross_window_actionable_count = 0
        if all_window_paths:
            actionable_direct_matches = [
                item
                for item in direct_matches
                if isinstance(item[3].get("ax_ref"), dict)
                and bool(_reviewable_supports(item[3]))
                and _action_support_allowed(item[3], clean_query)
            ]
            if len(focused_window_paths) == 1:
                focused_path = focused_window_paths[0]
                focused_matches = [
                    item
                    for item in actionable_direct_matches
                    if _element_window_path(item[3], all_window_paths)
                    == focused_path
                ]
                if focused_matches:
                    preferred_window_path = focused_path
                    best_score = max(item[0] for item in focused_matches)
                    cross_window_actionable_count = sum(
                        item[0] == best_score
                        for item in focused_matches
                    )
            if not preferred_window_path and actionable_direct_matches:
                best_score = max(item[0] for item in actionable_direct_matches)
                best_matches = [
                    item
                    for item in actionable_direct_matches
                    if item[0] == best_score
                ]
                best_window_paths = {
                    window_path
                    for item in best_matches
                    if (
                        window_path := _element_window_path(
                            item[3],
                            all_window_paths,
                        )
                    )
                }
                cross_window_actionable_count = len(best_matches)
                if len(best_window_paths) == 1:
                    preferred_window_path = next(iter(best_window_paths))
                elif len(best_window_paths) > 1:
                    cross_window_ambiguous = True
            if preferred_window_path:
                scoped_direct_matches = [
                    item
                    for item in direct_matches
                    if _element_window_path(item[3], all_window_paths)
                    == preferred_window_path
                ]
            else:
                scoped_direct_matches = direct_matches
        else:
            scoped_direct_matches = direct_matches
        best_scoped_action_matches: list[
            tuple[int, bool, int, dict[str, Any]]
        ] = []
        if action_query:
            scoped_reviewable_matches = [
                item
                for item in scoped_direct_matches
                if isinstance(item[3].get("ax_ref"), dict)
                and bool(_reviewable_supports(item[3]))
            ]
            if scoped_reviewable_matches:
                best_action_score = max(
                    item[0]
                    for item in scoped_reviewable_matches
                )
                best_scoped_action_matches = [
                    item
                    for item in scoped_reviewable_matches
                    if item[0] == best_action_score
                ]
                actionable_match_count = len(
                    {
                        _element_identity(item[3])
                        for item in best_scoped_action_matches
                        if _action_support_allowed(item[3], clean_query)
                    }
                )
                guarded_actionable_match_count = len(
                    {
                        _element_identity(item[3])
                        for item in best_scoped_action_matches
                        if not _action_support_allowed(item[3], clean_query)
                    }
                )
        requested_collection_role = bool(
            set(requested_roles)
            & {
                "axcollection",
                "axgrid",
                "axlist",
                "axoutline",
                "axtable",
                "axscrollarea",
                "axrow",
                "axoutlinerow",
                "axcell",
                "axlistitem",
            }
        )
        collection_query = bool(
            virtual_collection_role_adaptation
            or (
                (not requested_roles or requested_collection_role)
                and not role_listing_query
                and (
                any(
                    term in intent_text
                    for term in _COLLECTION_QUERY_TERMS
                )
                or (
                    action_query
                    and any(
                        term in intent_text
                        for term in _IMPLICIT_ACTION_COLLECTION_TERMS
                    )
                )
                )
            )
        )
        exhaustive_collection_query = bool(
            collection_query
            and not action_query
            and any(
                term in intent_text
                for term in _EXHAUSTIVE_COLLECTION_QUERY_TERMS
            )
        )
        if collection_query and not action_query:
            # A read-only collection query is already a bounded request for
            # collection context. Return the anchor plus every materialized
            # item (up to the global 64-item cap), even when Brain names AX
            # roles but omits words such as "all". Otherwise the default
            # 16-result limit drops item 16 because the anchor consumes slot 1.
            result_limit = max(
                result_limit,
                AX_COLLECTION_READ_MAX_ITEMS + 1,
            )
        active_chat_verification_query = bool(
            key in {"qq", "wechat"}
            and any(
                term in intent_text
                for term in _ACTIVE_CHAT_VERIFICATION_TERMS
            )
        )
        active_chat_evidence = (
            _active_chat_identity_evidence(
                contextual_elements,
                app_id=key,
                query=clean_query,
            )
            if active_chat_verification_query
            else {}
        )
        role_match_count = len(
            {
                _element_identity(element)
                for _score, _matched, _index, element in direct_matches
                if str(element.get("role") or "").casefold()
                in effective_requested_roles
            }
        )

        candidates: list[tuple[int, bool, int, dict[str, Any]]] = []
        candidate_identities: set[str] = set()
        collection_index: int | None = None
        collection_label = ""
        collection_item_total_count = 0
        collection_materialized_count = 0
        collection_declared_item_count = 0
        collection_branch_capture_complete = False
        collection_semantic_mismatch = False
        collection_near_match_labels: list[str] = []
        actionable_collection_match_count = 0
        guarded_collection_match_count = 0

        def add_candidate(
            item: tuple[int, bool, int, dict[str, Any]],
            *,
            relation: str = "",
            anchor: str = "",
        ) -> None:
            score, matched, index, element = item
            identity = _element_identity(element)
            if identity in candidate_identities:
                return
            candidate_identities.add(identity)
            candidate = (
                dict(element)
                if relation
                in {
                    "collection_anchor",
                    "collection_context",
                    "current_state",
                    "low_confidence",
                    "verification_evidence",
                }
                else _menu_action_element(element, clean_query)
            )
            if relation:
                candidate["context_relation"] = relation
            if anchor:
                candidate["context_anchor"] = anchor
            if relation in {
                "collection_anchor",
                "collection_context",
                "low_confidence",
                "verification_evidence",
            }:
                for key in ("ax_ref", "ax_ref_source", "activation"):
                    candidate.pop(key, None)
            candidates.append((score, matched, index, candidate))

        if active_chat_evidence:
            for evidence_key in (
                "header_index",
                "input_index",
                "message_index",
            ):
                evidence_index = active_chat_evidence.get(evidence_key)
                if not isinstance(evidence_index, int):
                    continue
                evidence_element = contextual_elements[evidence_index]
                evidence_score, evidence_matched = _semantic_score(
                    evidence_element,
                    clean_query,
                    terms,
                )
                add_candidate(
                    (
                        evidence_score + 500,
                        evidence_matched,
                        evidence_index,
                        evidence_element,
                    ),
                    relation="verification_evidence",
                    anchor=str(active_chat_evidence.get("chat_label") or ""),
                )

        if clean_query and collection_query:
            collection_index = _best_collection_index(
                contextual_elements,
                path_index,
                scoped_direct_matches,
                query=clean_query,
                terms=terms,
                window_path=preferred_window_path,
            )
            if collection_index is not None:
                collection = contextual_elements[collection_index]
                raw_collection_label = _element_label(collection)
                finder_native_view = bool(
                    key == "finder"
                    and str(collection.get("role") or "")
                    in _NATIVE_COLLECTION_ROLES
                    and (
                        not raw_collection_label
                        or any(
                            marker
                            in _normalized_text(raw_collection_label)
                            for marker in (
                                "图标视图",
                                "列表视图",
                                "分栏视图",
                                "画廊视图",
                                "iconview",
                                "listview",
                                "columnview",
                                "galleryview",
                            )
                        )
                    )
                )
                collection_label = (
                    "当前文件列表"
                    if finder_native_view
                    else raw_collection_label or "当前集合"
                )
                collection_score, collection_matched = _semantic_score(
                    collection,
                    clean_query,
                    terms,
                )
                collection_items = _collection_items(
                    contextual_elements,
                    collection_index,
                    app_id=key,
                    anchor_label=collection_label,
                    query=clean_query,
                )
                collection_materialized_count = len(collection_items)
                collection_declared_item_count = (
                    _declared_collection_item_count(collection)
                )
                collection_path = _element_tree_path(collection)
                collection_direct_child_count = sum(
                    bool(
                        (path := _element_tree_path(item))
                        and len(path) == len(collection_path) + 1
                        and path[: len(collection_path)] == collection_path
                    )
                    for item in contextual_elements
                )
                collection_branch_capture_complete = bool(
                    collection.get("children_complete") is True
                    and int(collection.get("enqueued_child_count") or 0) > 0
                    and int(collection.get("enqueued_child_count") or 0)
                    == collection_direct_child_count
                    and collection_items
                    and all(
                        bool(
                            (item_path := _element_tree_path(item))
                            and len(item_path) == len(collection_path) + 1
                            and item_path[: len(collection_path)]
                            == collection_path
                        )
                        for _item_index, item in collection_items
                    )
                )
                collection_item_total_count = max(
                    collection_materialized_count,
                    collection_declared_item_count,
                )
                collection_semantic_mismatch = bool(
                    exhaustive_collection_query
                    and _collection_subject_mismatch(
                        collection_label,
                        clean_query,
                    )
                    and not _collection_branch_has_subject(
                        contextual_elements,
                        collection_index,
                        clean_query,
                    )
                )
                if action_query:
                    collection_near_match_labels = _near_collection_labels(
                        clean_query,
                        collection_items,
                    )
                scored_collection_items: list[
                    tuple[int, bool, int, dict[str, Any]]
                ] = []
                for item_index, item in collection_items:
                    item_score, item_matched = _semantic_score(
                        item,
                        clean_query,
                        terms,
                    )
                    scored_collection_items.append(
                        (item_score, item_matched, item_index, item)
                    )
                if action_query:
                    scored_collection_items.sort(
                        key=lambda item: (
                            -int(item[1]),
                            -item[0],
                            item[2],
                        )
                    )
                anchor_candidate = (
                    collection_score,
                    collection_matched,
                    collection_index,
                    collection,
                )
                if not action_query:
                    add_candidate(
                        anchor_candidate,
                        relation="collection_anchor",
                        anchor=collection_label,
                    )
                matched_collection_items = [
                    scored_item
                    for scored_item in scored_collection_items
                    if scored_item[1]
                ]
                best_collection_score = (
                    matched_collection_items[0][0]
                    if matched_collection_items
                    else 0
                )
                collection_result_items = (
                    [
                        scored_item
                        for scored_item in matched_collection_items
                        if scored_item[0] == best_collection_score
                    ][: min(4, result_limit)]
                    if action_query
                    else scored_collection_items
                )
                actionable_collection_match_count = sum(
                    bool(_reviewable_supports(scored_item[3]))
                    and isinstance(scored_item[3].get("ax_ref"), dict)
                    and _action_support_allowed(scored_item[3], clean_query)
                    for scored_item in collection_result_items
                )
                guarded_collection_match_count = sum(
                    bool(_reviewable_supports(scored_item[3]))
                    and isinstance(scored_item[3].get("ax_ref"), dict)
                    and not _action_support_allowed(scored_item[3], clean_query)
                    for scored_item in collection_result_items
                )
                for scored_item in collection_result_items:
                    add_candidate(scored_item)
                if action_query:
                    add_candidate(
                        anchor_candidate,
                        relation="collection_anchor",
                        anchor=collection_label,
                    )
        if (
            virtual_collection_role_adaptation
            and collection_index is not None
        ):
            matched_identities.add(
                _element_identity(contextual_elements[collection_index])
            )
            role_match_count = max(1, role_match_count)
        current_view_title = (
            collection_label
            if view_title_query and collection_index is not None
            else ""
        )
        if view_title_query and not current_view_title:
            window_titles = [
                (
                    int(bool(element.get("focused"))),
                    int(_has_screen_bounds(element)),
                    _element_label(element),
                )
                for element in contextual_elements
                if (
                    str(element.get("role") or "") == "AXWindow"
                    and _element_label(element)
                )
            ]
            focused_titles = [
                title
                for focused, _visible, title in window_titles
                if focused
            ]
            visible_titles = [
                title
                for _focused, visible, title in window_titles
                if visible
            ]
            unique_titles = list(
                dict.fromkeys(
                    focused_titles
                    or visible_titles
                    or [title for _focused, _visible, title in window_titles]
                )
            )
            if len(unique_titles) == 1:
                current_view_title = unique_titles[0]

        if clean_query and matched_identities:
            direct_seed_items = (
                []
                if collection_index is not None
                and (
                    not action_query
                    or actionable_collection_match_count
                    or guarded_collection_match_count
                    or collection_near_match_labels
                )
                else (
                    best_scoped_action_matches
                    if action_query and best_scoped_action_matches
                    else scoped_direct_matches[: min(4, result_limit)]
                )
            )
            for item in direct_seed_items:
                add_candidate(item)
                _score, _matched, seed_index, seed = item
                seed_path = _element_tree_path(seed)
                anchor = _element_label(seed)
                for depth in range(1, min(4, len(seed_path)) + 1):
                    ancestor_index = path_index.get(seed_path[:-depth])
                    if ancestor_index is None:
                        continue
                    ancestor = contextual_elements[ancestor_index]
                    if (
                        isinstance(ancestor.get("ax_ref"), dict)
                        and bool(_reviewable_supports(ancestor))
                    ):
                        ancestor_score, ancestor_matched = _semantic_score(
                            ancestor,
                            clean_query,
                            terms,
                        )
                        add_candidate(
                            (ancestor_score, ancestor_matched, ancestor_index, ancestor),
                            relation="actionable_ancestor",
                            anchor=anchor,
                        )
                        break
                if collection_query and collection_index is None:
                    for context_index in _collection_context_indices(
                        contextual_elements,
                        path_index,
                        seed_index,
                        limit=4,
                    ):
                        context = contextual_elements[context_index]
                        context_score, context_matched = _semantic_score(
                            context,
                            clean_query,
                            terms,
                        )
                        add_candidate(
                            (context_score, context_matched, context_index, context),
                            relation="collection_context",
                            anchor=anchor,
                        )
            if not action_query and collection_index is None:
                for item in scoped_direct_matches:
                    add_candidate(item)
            pinned = [
                item
                for item in ranked
                if (
                    not item[1]
                    and (item[3].get("focused") or item[3].get("selected"))
                    and (
                        collection_index is None
                        or _nearest_collection_index(
                            contextual_elements,
                            path_index,
                            item[2],
                        )
                        == collection_index
                        or (
                            key in {"qq", "wechat"}
                            and str(item[3].get("role") or "") in _EDITABLE_ROLES
                        )
                    )
                )
            ][:2]
            for item in pinned:
                add_candidate(item, relation="current_state")
        elif clean_query:
            for item in ranked[: min(result_limit, 4)]:
                add_candidate(item, relation="low_confidence")
        else:
            for item in ranked:
                add_candidate(item)
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        selected_chars = 0
        collection_labels_truncated_count = 0
        for _score, _matched, _index, element in candidates:
            identity = _element_identity(element)
            if identity in seen:
                continue
            seen.add(identity)
            compact = _compact_element(element)
            if (
                requested_roles
                and str(element.get("role") or "").casefold()
                not in effective_requested_roles
            ):
                for key in (
                    "ax_ref",
                    "ax_ref_source",
                    "activation",
                ):
                    compact.pop(key, None)
            if (
                not action_query
                and compact.get("activation_effect") == "media_playback"
            ):
                for key in (
                    "ax_ref",
                    "ax_ref_source",
                    "activation",
                    "supports",
                ):
                    compact.pop(key, None)
            if (
                exhaustive_collection_query
                and not action_query
                and compact.get("context_relation") == "collection_item"
            ):
                # The collection name already lives in the top-level search
                # metadata.  Repeating it in semantic_path/context_anchor for
                # every item made the nominal 64-item read hit the character
                # budget after only a few dozen long labels.
                original_label = _clean_text(compact.get("label"))
                label = original_label
                compact = {
                    "label": label,
                    "context_relation": "collection_item",
                }
                returned_items = sum(
                    item.get("context_relation") == "collection_item"
                    for item in selected
                )
                remaining_items = max(
                    1,
                    collection_item_total_count - returned_items,
                )
                fair_item_budget = max(
                    64,
                    (AX_SEARCH_MAX_ELEMENT_CHARS - selected_chars)
                    // remaining_items,
                )
                while label:
                    encoded_chars = len(
                        json.dumps(
                            compact,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                    if encoded_chars <= fair_item_budget:
                        break
                    overflow = encoded_chars - fair_item_budget
                    label = label[: max(1, len(label) - max(1, overflow))]
                    compact["label"] = label
                if label != original_label:
                    collection_labels_truncated_count += 1
            compact_chars = len(
                json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
            if selected and selected_chars + compact_chars > AX_SEARCH_MAX_ELEMENT_CHARS:
                break
            selected.append(compact)
            selected_chars += compact_chars
            if len(selected) >= result_limit:
                break
        total_unique = len({_element_identity(item) for item in snapshot.elements})
        contextual_count = sum(bool(item.get("context_relation")) for item in selected)
        selected_matched_identities: set[str] = set()
        selected_actionable_identities: set[str] = set()
        selected_actionable_collection_identities: set[str] = set()
        selected_visible_identities: set[str] = set()
        for element in selected:
            _score, matched = _semantic_score(element, clean_query, terms)
            if not matched:
                continue
            identity = _element_identity(element)
            selected_matched_identities.add(identity)
            if _has_screen_bounds(element):
                selected_visible_identities.add(identity)
            if (
                isinstance(element.get("ax_ref"), dict)
                and bool(_reviewable_supports(element))
                and _action_support_allowed(element, clean_query)
                and not (
                    str(element.get("role") or "") == "AXMenuItem"
                    and not _has_screen_bounds(element)
                    and not menu_query
                )
            ):
                selected_actionable_identities.add(identity)
                if element.get("context_relation") == "collection_item":
                    selected_actionable_collection_identities.add(identity)
            elif (
                isinstance(element.get("ax_ref"), dict)
                and bool(_reviewable_supports(element))
                and not _action_support_allowed(element, clean_query)
            ):
                guarded_actionable_match_count += 1
        if action_query and not requested_roles and (
            actionable_collection_match_count
            or guarded_collection_match_count
            or collection_near_match_labels
        ):
            actionable_match_count = len(
                selected_actionable_collection_identities
            )
        else:
            actionable_match_count = max(
                actionable_match_count,
                len(selected_actionable_identities),
            )
        visible_match_count = max(
            visible_match_count,
            len(selected_visible_identities),
        )
        matched_count = len(matched_identities | selected_matched_identities)
        collection_item_returned_count = sum(
            item.get("context_relation") == "collection_item"
            for item in selected
        )
        collection_complete = bool(
            collection_materialized_count
            and collection_item_total_count == collection_materialized_count
            and collection_item_returned_count == collection_materialized_count
        )
        collection_labels_complete = bool(
            collection_complete
            and (
                not exhaustive_collection_query
                or collection_labels_truncated_count == 0
            )
        )
        captured_child_counts: Counter[tuple[int, ...]] = Counter()
        for element in snapshot.elements:
            element_path = _element_tree_path(element)
            if element_path:
                captured_child_counts[element_path[:-1]] += 1
        window_branch_elements = [
            element
            for element in snapshot.elements
            if (
                preferred_window_path
                and (
                    element_path := _element_tree_path(element)
                )[: len(preferred_window_path)]
                == preferred_window_path
            )
        ]
        window_branch_capture_complete = bool(
            window_branch_elements
            and all(
                element.get("children_complete") is True
                and captured_child_counts[
                    _element_tree_path(element)
                ]
                == int(element.get("enqueued_child_count") or 0)
                for element in window_branch_elements
            )
        )
        scoped_role_targets = [
            item[3]
            for item in scoped_direct_matches
            if (
                requested_roles
                and str(item[3].get("role") or "").casefold()
                in effective_requested_roles
                and isinstance(item[3].get("ax_ref"), dict)
                and bool(_reviewable_supports(item[3]))
                and _has_screen_bounds(item[3])
            )
        ]
        scoped_role_target_identities = {
            _element_identity(element)
            for element in scoped_role_targets
        }
        snapshot_path_index = {
            _element_tree_path(element): element
            for element in snapshot.elements
        }
        reviewed_target_path_complete = False
        if (
            key not in {"qq", "wechat"}
            and preferred_window_path
            and len(scoped_role_target_identities) == 1
        ):
            target = scoped_role_targets[0]
            target_path = _element_tree_path(target)
            lineage = [
                snapshot_path_index.get(target_path[:depth])
                for depth in range(
                    len(preferred_window_path),
                    len(target_path) + 1,
                )
            ]
            reviewed_target_path_complete = bool(
                lineage
                and all(
                    isinstance(element, dict)
                    and element.get("children_complete") is True
                    for element in lineage
                )
            )
        near_match_labels = list(
            dict.fromkeys(
                [
                    *collection_near_match_labels,
                    *(
                        _element_label(item)
                        for item in selected
                        if (
                            item.get("context_relation") == "collection_item"
                            and _element_label(item)
                            and _near_query_label(clean_query, _element_label(item))
                        )
                    ),
                ]
            )
        )[:5]
        if not clean_query:
            sufficient = bool(selected)
            insufficiency_reason = "" if sufficient else "empty_result"
        elif action_query and actionable_match_count <= 0 and near_match_labels:
            sufficient = False
            insufficiency_reason = "near_match_requires_confirmation"
        elif not matched_identities and collection_index is None:
            sufficient = False
            insufficiency_reason = "no_semantic_match"
        elif requested_roles and role_match_count <= 0:
            sufficient = False
            insufficiency_reason = "requested_role_not_found"
        elif (
            not menu_query
            and non_hidden_match_count <= 0
            and collection_index is None
        ):
            sufficient = False
            insufficiency_reason = "only_hidden_menu_matches"
        elif active_chat_verification_query and not active_chat_evidence:
            sufficient = False
            insufficiency_reason = "active_chat_identity_unverified"
        elif active_chat_verification_query:
            sufficient = True
            insufficiency_reason = ""
        elif action_query and guarded_actionable_match_count:
            sufficient = False
            insufficiency_reason = "action_intent_mismatch"
        elif collection_query and collection_index is None:
            sufficient = False
            insufficiency_reason = "collection_container_not_found"
        elif collection_query and collection_item_total_count <= 0:
            sufficient = False
            insufficiency_reason = "empty_collection"
        elif exhaustive_collection_query and collection_semantic_mismatch:
            sufficient = False
            insufficiency_reason = "collection_semantic_mismatch"
        elif exhaustive_collection_query and not collection_complete:
            sufficient = False
            insufficiency_reason = "collection_items_truncated"
        elif exhaustive_collection_query and not collection_labels_complete:
            sufficient = False
            insufficiency_reason = "collection_labels_truncated"
        elif action_query and cross_window_ambiguous:
            sufficient = False
            insufficiency_reason = "ambiguous_actionable_matches"
            actionable_match_count = max(
                actionable_match_count,
                cross_window_actionable_count,
            )
        elif action_query and actionable_match_count > 1:
            sufficient = False
            insufficiency_reason = "ambiguous_actionable_matches"
        elif action_query and actionable_match_count <= 0:
            sufficient = False
            insufficiency_reason = "no_reviewable_actionable_match"
        else:
            sufficient = True
            insufficiency_reason = ""
        complete_truncated_collection_read = bool(
            stale_reason == "capture_truncated"
            and collection_query
            and not action_query
            and collection_complete
            and collection_labels_complete
            and (
                (
                    collection_declared_item_count > 0
                    and collection_declared_item_count
                    == collection_materialized_count
                )
                or collection_branch_capture_complete
            )
        )
        complete_truncated_window_query = bool(
            stale_reason == "capture_truncated"
            and sufficient
            and preferred_window_path
            and (
                window_branch_capture_complete
                or reviewed_target_path_complete
            )
            and (
                action_query
                or (requested_roles and actionable_match_count == 1)
            )
        )
        scoped_truncated_result_complete = bool(
            complete_truncated_collection_read
            or complete_truncated_window_query
        )
        effective_stale_reason = (
            ""
            if scoped_truncated_result_complete
            else stale_reason
        )
        if effective_stale_reason:
            sufficient = False
            insufficiency_reason = "stale_snapshot_requires_refresh"
        if (
            not sufficient
            or effective_stale_reason
            or (complete_truncated_collection_read and not action_query)
        ):
            selected = [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"ax_ref", "ax_ref_source", "activation"}
                }
                for item in selected
            ]
            selected_chars = sum(
                len(
                    json.dumps(
                        item,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                for item in selected
            )
        window_candidates = []
        for element in contextual_elements:
            if str(element.get("role") or "") != "AXWindow":
                continue
            bounds = (
                element.get("bounds")
                if isinstance(element.get("bounds"), dict)
                else {}
            )
            try:
                clean_bounds = {
                    key_name: int(round(float(bounds.get(key_name) or 0)))
                    for key_name in ("x", "y", "width", "height")
                }
            except (TypeError, ValueError):
                continue
            if (
                clean_bounds["width"] < 80
                or clean_bounds["height"] < 40
            ):
                continue
            window_candidates.append(
                {
                    "bounds": clean_bounds,
                    "focused": bool(element.get("focused")),
                    "enabled": element.get("enabled") is not False,
                }
            )
        window_candidates.sort(
            key=lambda item: (
                -int(bool(item.get("focused"))),
                -int(item["bounds"]["width"] * item["bounds"]["height"]),
            )
        )
        return {
            "status": "hit",
            "app_id": key,
            "pid": snapshot.pid,
            "snapshot_id": snapshot.snapshot_id,
            "age_ms": int(age_sec * 1000),
            "ttl_sec": self._ttl_sec,
            "persistent": self._storage_dir is not None,
            "source": snapshot.source,
            "stale": bool(effective_stale_reason),
            "stale_reason": effective_stale_reason,
            "snapshot_stale_reason": stale_reason,
            "process_changed": process_changed,
            "query": clean_query,
            "total_element_count": len(snapshot.elements),
            "total_unique_count": total_unique,
            "returned_count": len(selected),
            "returned_element_chars": selected_chars,
            "matched_count": matched_count,
            "actionable_match_count": actionable_match_count,
            "visible_match_count": visible_match_count,
            "requested_roles": list(requested_roles),
            "effective_requested_roles": list(effective_requested_roles),
            "role_adaptation": (
                "finder_desktop_ax_shape"
                if finder_desktop_role_adaptation
                else (
                    "virtual_axcollection"
                    if virtual_collection_role_adaptation
                    else (
                        "descriptive_context"
                        if descriptive_context_roles
                        else ""
                    )
                )
            ),
            "role_match_count": role_match_count,
            "contextual_count": contextual_count,
            "current_view_title": current_view_title,
            "collection_label": collection_label,
            "collection_item_total_count": collection_item_total_count,
            "collection_item_returned_count": collection_item_returned_count,
            "collection_declared_item_count": collection_declared_item_count,
            "collection_branch_capture_complete": (
                collection_branch_capture_complete
            ),
            "collection_complete": collection_complete,
            "collection_labels_complete": collection_labels_complete,
            "complete_truncated_collection_read": (
                complete_truncated_collection_read
            ),
            "window_branch_capture_complete": (
                window_branch_capture_complete
            ),
            "reviewed_target_path_complete": (
                reviewed_target_path_complete
            ),
            "complete_truncated_window_query": (
                complete_truncated_window_query
            ),
            "collection_labels_truncated_count": (
                collection_labels_truncated_count
            ),
            "exhaustive_collection_query": exhaustive_collection_query,
            "near_match_labels": near_match_labels,
            "active_chat_identity_verified": bool(active_chat_evidence),
            "active_chat_label": _clean_text(
                active_chat_evidence.get("chat_label"),
                max_length=160,
            ),
            "active_chat_input_focused": bool(
                active_chat_evidence.get("input_focused")
            ),
            "visual_fallback_required": (
                not sufficient
                and insufficiency_reason
                not in {
                    "near_match_requires_confirmation",
                    "action_intent_mismatch",
                    "ambiguous_actionable_matches",
                }
            ),
            "sufficient": sufficient,
            "insufficiency_reason": insufficiency_reason,
            "search_truncated": total_unique > len(selected),
            "capture_truncated": snapshot.capture_truncated,
            "visited_count": snapshot.visited_count,
            "index": deepcopy(snapshot.index),
            "window_candidates": window_candidates[:4],
            "elements": selected,
        }


MACOS_AX_SNAPSHOT_CACHE = AXSnapshotCache(
    storage_dir=AX_PERSISTENT_ROOT,
    ttl_sec=AX_CACHE_DEFAULT_TTL_SEC,
)


__all__ = [
    "AX_COLLECTION_READ_MAX_ITEMS",
    "AX_CACHE_DEFAULT_TTL_SEC",
    "AX_CACHE_MAX_APPS",
    "AX_PERSISTENT_ROOT",
    "AX_SEARCH_DEFAULT_LIMIT",
    "AX_SEARCH_MAX_ELEMENT_CHARS",
    "AX_SEARCH_MAX_LIMIT",
    "AX_STORE_CHUNK_SIZE",
    "AX_STORE_MAX_ELEMENTS",
    "AX_STORE_SCHEMA_VERSION",
    "AXSnapshot",
    "AXSnapshotCache",
    "MACOS_AX_SNAPSHOT_CACHE",
    "snapshot_window_signatures",
]
