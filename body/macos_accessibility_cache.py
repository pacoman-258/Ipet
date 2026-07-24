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


AX_CACHE_DEFAULT_TTL_SEC = 20.0
AX_CACHE_MAX_APPS = 8
AX_SEARCH_DEFAULT_LIMIT = 10
AX_SEARCH_MAX_LIMIT = 16
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
_REVIEWABLE_SUPPORTS = {"press", "focus", "select", "type_text"}
_TEXT_KEYS = ("label", "title", "description", "identifier", "value")
_COMPACT_KEYS = (
    "role",
    "subrole",
    "tree_path",
    "label",
    "label_source",
    "title",
    "description",
    "identifier",
    "value",
    "enabled",
    "focused",
    "selected",
    "protected",
    "supports",
    "bounds",
    "ax_ref",
    "ax_ref_source",
    "semantic_path",
    "context_relation",
    "context_anchor",
)
_COLLECTION_ROLES = {"AXList", "AXOutline", "AXTable", "AXScrollArea", "AXGroup"}
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
_ACTION_QUERY_TERMS = (
    "点击",
    "打开",
    "选择",
    "进入",
    "按下",
    "click",
    "open",
    "select",
    "press",
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
    for key in ("subrole", "identifier", "title", "description"):
        value = _clean_text(element.get(key), max_length=160)
        if value:
            payload[key] = value
    if not any(payload.get(key) for key in ("identifier", "title", "description")):
        value = _clean_text(element.get("value"), max_length=160)
        if value and role not in _EDITABLE_ROLES and not element.get("protected"):
            payload["value"] = value
    bounds = element.get("bounds") if isinstance(element.get("bounds"), dict) else {}
    payload["bounds"] = dict(bounds)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return payload


def _hierarchy_enriched_elements(
    elements: tuple[dict[str, Any], ...],
    *,
    app_id: str,
) -> tuple[list[dict[str, Any]], dict[tuple[int, ...], int]]:
    enriched = [dict(element) for element in elements]
    for element in enriched:
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
            if not _reviewable_supports(ancestor) or _element_label(ancestor):
                continue
            descendant_labels.setdefault(ancestor_index, []).append(
                (role_priority, depth, child_path, child_label)
            )
    for ancestor_index, candidates in descendant_labels.items():
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        label = candidates[0][3]
        if label:
            enriched[ancestor_index]["label"] = label
            enriched[ancestor_index]["label_source"] = "descendant"
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
        if len(term) >= 2 and term in searchable:
            score += min(120, 35 + len(term) * 8)
            matched = True

    intent_text = str(query or "").casefold()
    if any(term in intent_text for term in ("搜索", "查找", "search", "find")):
        if role == "AXSearchField":
            score += 190
        elif role in _EDITABLE_ROLES:
            score += 55
    if any(term in intent_text for term in ("输入", "回复", "编辑", "消息框", "type", "reply")):
        if role in _EDITABLE_ROLES or "type_text" in supports:
            score += 170
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
    container_path = seed_path[:-1]
    for depth in range(1, min(5, len(seed_path)) + 1):
        candidate_path = seed_path[:-depth]
        candidate_index = path_index.get(candidate_path)
        if candidate_index is None:
            continue
        if str(elements[candidate_index].get("role") or "") in _COLLECTION_ROLES:
            container_path = candidate_path
            break
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
        actionable += bool(clean_supports)
        focused += bool(element.get("focused"))
        selected += bool(element.get("selected"))
    return {
        "roles": dict(sorted(role_counts.items())),
        "supports": dict(sorted(support_counts.items())),
        "actionable": actionable,
        "focused": focused,
        "selected": selected,
    }


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
                        "created_at": (
                            snapshot.created_at
                            if snapshot is not None
                            else float(meta.get("created_at") or 0.0)
                        ),
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
                        "stale": bool(
                            snapshot.stale_reason
                            if snapshot is not None
                            else _clean_text(meta.get("stale_reason"), max_length=120)
                        ),
                        "stale_reason": (
                            snapshot.stale_reason
                            if snapshot is not None
                            else _clean_text(meta.get("stale_reason"), max_length=120)
                        ),
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
            if self._ttl_sec is not None and age_sec > self._ttl_sec:
                self._entries.pop(key, None)
                return {"status": "miss", "reason": "snapshot_expired", "app_id": key}
            self._entries.move_to_end(key)
        requested_pid = max(0, int(pid or 0))
        process_changed = bool(requested_pid and snapshot.pid and snapshot.pid != requested_pid)
        stale_reason = snapshot.stale_reason or ("process_changed" if process_changed else "")

        try:
            requested_limit = int(limit)
        except (TypeError, ValueError):
            requested_limit = AX_SEARCH_DEFAULT_LIMIT
        result_limit = max(1, min(AX_SEARCH_MAX_LIMIT, requested_limit))
        clean_query = _clean_text(query, max_length=240)
        terms = _query_terms(clean_query)
        contextual_elements, path_index = _hierarchy_enriched_elements(
            snapshot.elements,
            app_id=key,
        )
        ranked: list[tuple[int, bool, int, dict[str, Any]]] = []
        for index, element in enumerate(contextual_elements):
            score, matched = _semantic_score(element, clean_query, terms)
            ranked.append((score, matched, index, element))
        ranked.sort(key=lambda item: (-int(item[1]), -item[0], item[2]))

        matched_identities = {
            _element_identity(element)
            for _score, matched, _index, element in ranked
            if matched
        }
        direct_matches = [item for item in ranked if item[1]]
        actionable_match_count = len(
            {
                _element_identity(element)
                for _score, _matched, _index, element in direct_matches
                if isinstance(element.get("ax_ref"), dict)
                and bool(_reviewable_supports(element))
                and not (
                    str(element.get("role") or "") == "AXMenuItem"
                    and not _has_screen_bounds(element)
                )
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
        intent_text = clean_query.casefold()
        collection_query = any(term in intent_text for term in _COLLECTION_QUERY_TERMS)
        action_query = any(term in intent_text for term in _ACTION_QUERY_TERMS)
        requested_roles = tuple(dict.fromkeys(re.findall(r"ax[a-z0-9]+", intent_text)))
        menu_query = (
            "axmenuitem" in requested_roles
            or "菜单" in intent_text
            or "menu" in intent_text
        )
        role_match_count = len(
            {
                _element_identity(element)
                for _score, _matched, _index, element in direct_matches
                if str(element.get("role") or "").casefold() in requested_roles
            }
        )

        candidates: list[tuple[int, bool, int, dict[str, Any]]] = []
        candidate_identities: set[str] = set()

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
            candidate = dict(element)
            if relation:
                candidate["context_relation"] = relation
            if anchor:
                candidate["context_anchor"] = anchor
            candidates.append((score, matched, index, candidate))

        if clean_query and matched_identities:
            for item in direct_matches[: min(4, result_limit)]:
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
                if collection_query:
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
            for item in direct_matches:
                add_candidate(item)
            pinned = [
                item
                for item in ranked
                if not item[1] and (item[3].get("focused") or item[3].get("selected"))
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
        for _score, _matched, _index, element in candidates:
            identity = _element_identity(element)
            if identity in seen:
                continue
            seen.add(identity)
            compact = _compact_element(element)
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
        if not clean_query:
            sufficient = bool(selected)
            insufficiency_reason = "" if sufficient else "empty_result"
        elif not matched_identities:
            sufficient = False
            insufficiency_reason = "no_semantic_match"
        elif requested_roles and role_match_count <= 0:
            sufficient = False
            insufficiency_reason = "requested_role_not_found"
        elif not menu_query and non_hidden_match_count <= 0:
            sufficient = False
            insufficiency_reason = "only_hidden_menu_matches"
        elif action_query and actionable_match_count <= 0:
            sufficient = False
            insufficiency_reason = "no_reviewable_actionable_match"
        elif collection_query and actionable_match_count <= 0 and contextual_count <= 0:
            sufficient = False
            insufficiency_reason = "no_collection_context"
        else:
            sufficient = True
            insufficiency_reason = ""
        return {
            "status": "hit",
            "app_id": key,
            "pid": snapshot.pid,
            "snapshot_id": snapshot.snapshot_id,
            "age_ms": int(age_sec * 1000),
            "ttl_sec": self._ttl_sec,
            "persistent": self._storage_dir is not None,
            "source": snapshot.source,
            "stale": bool(stale_reason),
            "stale_reason": stale_reason,
            "process_changed": process_changed,
            "query": clean_query,
            "total_element_count": len(snapshot.elements),
            "total_unique_count": total_unique,
            "returned_count": len(selected),
            "returned_element_chars": selected_chars,
            "matched_count": len(matched_identities),
            "actionable_match_count": actionable_match_count,
            "visible_match_count": visible_match_count,
            "requested_roles": list(requested_roles),
            "role_match_count": role_match_count,
            "contextual_count": contextual_count,
            "sufficient": sufficient,
            "insufficiency_reason": insufficiency_reason,
            "search_truncated": total_unique > len(selected),
            "capture_truncated": snapshot.capture_truncated,
            "visited_count": snapshot.visited_count,
            "index": deepcopy(snapshot.index),
            "elements": selected,
        }


MACOS_AX_SNAPSHOT_CACHE = AXSnapshotCache(storage_dir=AX_PERSISTENT_ROOT)


__all__ = [
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
]
