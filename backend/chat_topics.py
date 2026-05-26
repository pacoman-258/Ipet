from __future__ import annotations

import json
import re
import shutil
import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_TOPIC_TITLE = "\u65b0\u8bdd\u9898"
MAX_TOPIC_TITLE_CHARS = 24
MAX_TOPIC_PREVIEW_CHARS = 96

BLOCK_RAW_MESSAGES = "raw_messages"
BLOCK_MINI_SUMMARY = "mini_summary"
BLOCK_MAJOR_SUMMARY = "major_summary"

TOPIC_META_DEFAULTS = {
    "last_long_term_memory_saved_marker": None,
    "last_long_term_memory_dismissed_marker": None,
}


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_utf8_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _compact_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\r", "\n").split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def build_topic_title(first_user_text: str, fallback: str = DEFAULT_TOPIC_TITLE) -> str:
    title = _compact_text(first_user_text, MAX_TOPIC_TITLE_CHARS)
    return title or str(fallback or DEFAULT_TOPIC_TITLE)


def normalize_topic_id(raw_value: str | None = None) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return uuid4().hex
    text = text.replace("\\", "-").replace("/", "-")
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-_")
    return text or uuid4().hex


def _block_turn_span(block: dict[str, Any]) -> tuple[int, int]:
    start = int(block.get("start_assistant_turn") or 0)
    end = int(block.get("end_assistant_turn") or 0)
    return start, end


@dataclass(frozen=True)
class TopicRuntimeSnapshot:
    topic_id: str
    meta: dict[str, Any]
    model_messages: tuple[dict[str, str], ...]
    full_messages: tuple[dict[str, Any], ...]


class TopicStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self._lock = threading.RLock()
        self._snapshot_cache: dict[str, tuple[str, TopicRuntimeSnapshot]] = {}

    def topic_dir(self, topic_id: str) -> Path:
        return self.root_dir / normalize_topic_id(topic_id)

    def meta_path(self, topic_id: str) -> Path:
        return self.topic_dir(topic_id) / "meta.json"

    def full_path(self, topic_id: str) -> Path:
        return self.topic_dir(topic_id) / "full.jsonl"

    def summary_path(self, topic_id: str) -> Path:
        return self.topic_dir(topic_id) / "summary.json"

    def create_topic(
        self,
        *,
        topic_id: str | None = None,
        persisted: bool = True,
        title: str = DEFAULT_TOPIC_TITLE,
    ) -> dict[str, Any]:
        normalized_id = normalize_topic_id(topic_id)
        now_text = _now_text()
        meta = {
            "topic_id": normalized_id,
            "title": str(title or DEFAULT_TOPIC_TITLE),
            "preview": "",
            "created_at": now_text,
            "updated_at": now_text,
            "persisted": bool(persisted),
            "assistant_turn_count": 0,
            "mini_summary_count": 0,
            "major_summary_count": 0,
            "pending_summary_start_assistant_turn": None,
            "pending_summary_end_assistant_turn": None,
            **TOPIC_META_DEFAULTS,
        }
        if not persisted:
            return meta
        with self._lock:
            topic_dir = self.topic_dir(normalized_id)
            topic_dir.mkdir(parents=True, exist_ok=True)
            self._write_json(self.meta_path(normalized_id), meta)
            self.full_path(normalized_id).write_text("", encoding="utf-8")
            self._write_json(
                self.summary_path(normalized_id),
                {
                    "topic_id": normalized_id,
                    "updated_at": now_text,
                    "blocks": [],
                },
            )
            self._invalidate_snapshot_cache(normalized_id)
        return meta

    def ensure_topic(
        self,
        topic_id: str,
        *,
        persisted: bool = True,
        title: str = DEFAULT_TOPIC_TITLE,
    ) -> dict[str, Any]:
        normalized_id = normalize_topic_id(topic_id)
        if not persisted:
            return self.create_topic(topic_id=normalized_id, persisted=False, title=title)
        meta = self.load_meta(normalized_id)
        if meta is not None:
            return meta
        return self.create_topic(topic_id=normalized_id, persisted=True, title=title)

    def has_topic(self, topic_id: str) -> bool:
        return self.load_meta(topic_id) is not None

    def load_meta(self, topic_id: str) -> dict[str, Any] | None:
        path = self.meta_path(topic_id)
        if not path.exists():
            return None
        try:
            data = json.loads(_read_utf8_text(path))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        for key, value in TOPIC_META_DEFAULTS.items():
            data.setdefault(key, value)
        return data

    def load_full_messages(self, topic_id: str) -> list[dict[str, Any]]:
        path = self.full_path(topic_id)
        if not path.exists():
            return []
        messages: list[dict[str, Any]] = []
        for raw_line in _read_utf8_text(path).splitlines():
            if not raw_line.strip():
                continue
            try:
                item = json.loads(raw_line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "")
            if role not in {"user", "assistant"}:
                continue
            messages.append(
                {
                    "role": role,
                    "content": content,
                    "created_at": str(item.get("created_at") or ""),
                    "assistant_turn": int(item.get("assistant_turn") or 0),
                }
            )
        return messages

    def load_summary_document(self, topic_id: str) -> dict[str, Any]:
        path = self.summary_path(topic_id)
        if not path.exists():
            return {
                "topic_id": normalize_topic_id(topic_id),
                "updated_at": _now_text(),
                "blocks": [],
            }
        try:
            data = json.loads(_read_utf8_text(path))
        except Exception:
            return {
                "topic_id": normalize_topic_id(topic_id),
                "updated_at": _now_text(),
                "blocks": [],
            }
        if not isinstance(data, dict):
            return {
                "topic_id": normalize_topic_id(topic_id),
                "updated_at": _now_text(),
                "blocks": [],
            }
        blocks = data.get("blocks")
        data["blocks"] = list(blocks) if isinstance(blocks, list) else []
        return data

    def get_topic_detail(self, topic_id: str) -> dict[str, Any] | None:
        meta = self.load_meta(topic_id)
        if meta is None:
            return None
        return {
            "meta": meta,
            "messages": self.load_full_messages(topic_id),
            "summary": self.load_summary_document(topic_id),
        }

    def list_topics(self) -> list[dict[str, Any]]:
        if not self.root_dir.exists():
            return []
        topics: list[dict[str, Any]] = []
        for topic_dir in self.root_dir.iterdir():
            if not topic_dir.is_dir():
                continue
            meta = self.load_meta(topic_dir.name)
            if meta is None:
                continue
            if int(meta.get("assistant_turn_count") or 0) <= 0:
                continue
            topics.append(meta)
        topics.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return topics

    def delete_topic(self, topic_id: str) -> bool:
        normalized_id = normalize_topic_id(topic_id)
        topic_dir = self.topic_dir(normalized_id)
        with self._lock:
            if not topic_dir.exists():
                return False
            shutil.rmtree(topic_dir, ignore_errors=False)
            self._invalidate_snapshot_cache(normalized_id)
        return True

    def update_long_term_memory_marker(
        self,
        topic_id: str,
        *,
        saved_marker: str | None = None,
        dismissed_marker: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_id = normalize_topic_id(topic_id)
        now_text = _now_text()
        with self._lock:
            meta = self.load_meta(normalized_id)
            if meta is None:
                return None
            updated = deepcopy(meta)
            if saved_marker is not None:
                updated["last_long_term_memory_saved_marker"] = str(saved_marker or "").strip() or None
            if dismissed_marker is not None:
                updated["last_long_term_memory_dismissed_marker"] = str(dismissed_marker or "").strip() or None
            updated["updated_at"] = now_text
            self._write_json(self.meta_path(normalized_id), updated)
            self._invalidate_snapshot_cache(normalized_id)
            return updated

    def truncate_from_assistant_turn(self, topic_id: str, assistant_turn: int) -> dict[str, Any] | None:
        normalized_id = normalize_topic_id(topic_id)
        start_turn = max(1, int(assistant_turn or 0))
        now_text = _now_text()
        with self._lock:
            meta = self.load_meta(normalized_id)
            if meta is None:
                return None
            remaining_messages = [
                deepcopy(item)
                for item in self.load_full_messages(normalized_id)
                if int(item.get("assistant_turn") or 0) < start_turn
            ]
            max_turn = max((int(item.get("assistant_turn") or 0) for item in remaining_messages), default=0)
            blocks = [self._raw_block_from_messages(remaining_messages, now_text)] if remaining_messages else []
            summary_document = {
                "topic_id": normalized_id,
                "updated_at": now_text,
                "blocks": blocks,
            }
            updated_meta = deepcopy(meta)
            updated_meta["assistant_turn_count"] = max_turn
            updated_meta["mini_summary_count"] = 0
            updated_meta["major_summary_count"] = 0
            updated_meta["updated_at"] = now_text
            updated_meta["preview"] = self._build_preview(blocks)
            pending = self._pending_raw_turn_range(blocks)
            updated_meta["pending_summary_start_assistant_turn"] = pending[0] if pending else None
            updated_meta["pending_summary_end_assistant_turn"] = pending[1] if pending else None

            self._write_jsonl(self.full_path(normalized_id), remaining_messages)
            self._write_json(self.meta_path(normalized_id), updated_meta)
            self._write_json(self.summary_path(normalized_id), summary_document)
            self._invalidate_snapshot_cache(normalized_id)
            return {
                "meta": updated_meta,
                "summary": summary_document,
            }

    def build_model_messages(self, topic_id: str) -> list[dict[str, str]]:
        summary_document = self.load_summary_document(topic_id)
        return self._build_model_messages_from_summary_document(summary_document)

    def get_runtime_snapshot(self, topic_id: str) -> TopicRuntimeSnapshot | None:
        normalized_id = normalize_topic_id(topic_id)
        meta = self.load_meta(normalized_id)
        if meta is None:
            return None
        version = str(meta.get("updated_at") or "")
        cached = self._snapshot_cache.get(normalized_id)
        if cached is not None and cached[0] == version:
            snapshot = cached[1]
            return TopicRuntimeSnapshot(
                topic_id=snapshot.topic_id,
                meta=deepcopy(meta),
                model_messages=tuple(deepcopy(list(snapshot.model_messages))),
                full_messages=tuple(deepcopy(list(snapshot.full_messages))),
            )
        summary_document = self.load_summary_document(normalized_id)
        model_messages = tuple(self._build_model_messages_from_summary_document(summary_document))
        full_messages = tuple(self.load_full_messages(normalized_id))
        snapshot = TopicRuntimeSnapshot(
            topic_id=normalized_id,
            meta=deepcopy(meta),
            model_messages=model_messages,
            full_messages=full_messages,
        )
        self._snapshot_cache[normalized_id] = (version, snapshot)
        return TopicRuntimeSnapshot(
            topic_id=normalized_id,
            meta=deepcopy(meta),
            model_messages=tuple(deepcopy(list(model_messages))),
            full_messages=tuple(deepcopy(list(full_messages))),
        )

    def _build_model_messages_from_summary_document(self, summary_document: dict[str, Any]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for block in summary_document.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").strip()
            if block_type == BLOCK_RAW_MESSAGES:
                for item in block.get("messages") or []:
                    if not isinstance(item, dict):
                        continue
                    role = str(item.get("role") or "").strip()
                    content = str(item.get("content") or "")
                    if role in {"user", "assistant"} and content:
                        messages.append({"role": role, "content": content})
                continue
            content = str(block.get("content") or "").strip()
            if not content:
                continue
            start_turn, end_turn = _block_turn_span(block)
            label = "Mini Summary" if block_type == BLOCK_MINI_SUMMARY else "Major Summary"
            messages.append(
                {
                    "role": "system",
                    "content": f"[{label} | assistant turns {start_turn}-{end_turn}]\n{content}",
                }
            )
        return messages

    def append_exchange(
        self,
        topic_id: str,
        *,
        user_text: str,
        assistant_text: str,
    ) -> dict[str, Any]:
        normalized_id = normalize_topic_id(topic_id)
        now_text = _now_text()
        with self._lock:
            meta = deepcopy(self.ensure_topic(normalized_id, persisted=True))
            summary_document = self.load_summary_document(normalized_id)
            blocks = [deepcopy(item) for item in (summary_document.get("blocks") or []) if isinstance(item, dict)]
            next_turn = int(meta.get("assistant_turn_count") or 0) + 1
            user_entry = {
                "role": "user",
                "content": str(user_text or ""),
                "created_at": now_text,
                "assistant_turn": next_turn,
            }
            assistant_entry = {
                "role": "assistant",
                "content": str(assistant_text or ""),
                "created_at": now_text,
                "assistant_turn": next_turn,
            }
            self._append_jsonl(self.full_path(normalized_id), user_entry)
            self._append_jsonl(self.full_path(normalized_id), assistant_entry)

            if blocks and str(blocks[-1].get("type") or "") == BLOCK_RAW_MESSAGES:
                tail = deepcopy(blocks[-1])
                tail_messages = list(tail.get("messages") or [])
                tail_messages.extend([user_entry, assistant_entry])
                tail["messages"] = tail_messages
                tail["end_assistant_turn"] = next_turn
                tail["updated_at"] = now_text
                blocks[-1] = tail
            else:
                blocks.append(
                    {
                        "type": BLOCK_RAW_MESSAGES,
                        "start_assistant_turn": next_turn,
                        "end_assistant_turn": next_turn,
                        "created_at": now_text,
                        "updated_at": now_text,
                        "messages": [user_entry, assistant_entry],
                    }
                )

            previous_turn_count = int(meta.get("assistant_turn_count") or 0)
            meta["assistant_turn_count"] = next_turn
            meta["updated_at"] = now_text
            if previous_turn_count == 0 or str(meta.get("title") or "").strip() in {"", DEFAULT_TOPIC_TITLE}:
                meta["title"] = build_topic_title(user_text)

            summary_document["topic_id"] = normalized_id
            summary_document["updated_at"] = now_text
            summary_document["blocks"] = blocks
            meta["preview"] = self._build_preview(blocks)
            pending = self._pending_raw_turn_range(blocks)
            meta["pending_summary_start_assistant_turn"] = pending[0] if pending else None
            meta["pending_summary_end_assistant_turn"] = pending[1] if pending else None

            self._write_json(self.meta_path(normalized_id), meta)
            self._write_json(self.summary_path(normalized_id), summary_document)
            self._invalidate_snapshot_cache(normalized_id)
            return {
                "meta": meta,
                "summary": summary_document,
            }

    def get_pending_mini_summary(self, topic_id: str, interval_turns: int) -> dict[str, Any] | None:
        required_turns = max(1, int(interval_turns or 1))
        summary_document = self.load_summary_document(topic_id)
        for index, raw_block in enumerate(summary_document.get("blocks") or []):
            if not isinstance(raw_block, dict) or str(raw_block.get("type") or "") != BLOCK_RAW_MESSAGES:
                continue
            grouped_messages = self._group_messages_by_turn(raw_block.get("messages") or [])
            ordered_turns = sorted(grouped_messages)
            if len(ordered_turns) < required_turns:
                continue
            selected_turns = ordered_turns[:required_turns]
            selected_messages: list[dict[str, Any]] = []
            for turn in selected_turns:
                selected_messages.extend(grouped_messages.get(turn) or [])
            return {
                "block_index": index,
                "start_assistant_turn": selected_turns[0],
                "end_assistant_turn": selected_turns[-1],
                "messages": selected_messages,
            }
        return None

    def apply_mini_summary(self, topic_id: str, candidate: dict[str, Any], content: str) -> dict[str, Any]:
        return self._apply_summary_block(
            topic_id,
            candidate=candidate,
            content=content,
            block_type=BLOCK_MINI_SUMMARY,
        )

    def get_pending_major_summary(self, topic_id: str, mini_group_size: int = 3) -> dict[str, Any] | None:
        required = max(1, int(mini_group_size or 1))
        summary_document = self.load_summary_document(topic_id)
        blocks = list(summary_document.get("blocks") or [])
        if len(blocks) < required:
            return None
        for start_index in range(0, len(blocks) - required + 1):
            group = blocks[start_index : start_index + required]
            if not all(isinstance(item, dict) and str(item.get("type") or "") == BLOCK_MINI_SUMMARY for item in group):
                continue
            return {
                "start_index": start_index,
                "end_index": start_index + required - 1,
                "blocks": deepcopy(group),
                "start_assistant_turn": int(group[0].get("start_assistant_turn") or 0),
                "end_assistant_turn": int(group[-1].get("end_assistant_turn") or 0),
            }
        return None

    def apply_major_summary(self, topic_id: str, candidate: dict[str, Any], content: str) -> dict[str, Any]:
        normalized_id = normalize_topic_id(topic_id)
        now_text = _now_text()
        with self._lock:
            meta = deepcopy(self.ensure_topic(normalized_id, persisted=True))
            summary_document = self.load_summary_document(normalized_id)
            blocks = [deepcopy(item) for item in (summary_document.get("blocks") or []) if isinstance(item, dict)]
            start_index = int(candidate.get("start_index") or 0)
            end_index = int(candidate.get("end_index") or start_index)
            replacement = {
                "type": BLOCK_MAJOR_SUMMARY,
                "start_assistant_turn": int(candidate.get("start_assistant_turn") or 0),
                "end_assistant_turn": int(candidate.get("end_assistant_turn") or 0),
                "created_at": now_text,
                "updated_at": now_text,
                "content": str(content or "").strip(),
            }
            blocks[start_index : end_index + 1] = [replacement]
            summary_document["topic_id"] = normalized_id
            summary_document["updated_at"] = now_text
            summary_document["blocks"] = blocks

            meta["updated_at"] = now_text
            meta["major_summary_count"] = int(meta.get("major_summary_count") or 0) + 1
            meta["preview"] = self._build_preview(blocks)
            pending = self._pending_raw_turn_range(blocks)
            meta["pending_summary_start_assistant_turn"] = pending[0] if pending else None
            meta["pending_summary_end_assistant_turn"] = pending[1] if pending else None
            self._write_json(self.meta_path(normalized_id), meta)
            self._write_json(self.summary_path(normalized_id), summary_document)
            self._invalidate_snapshot_cache(normalized_id)
            return {
                "meta": meta,
                "summary": summary_document,
            }

    def _apply_summary_block(
        self,
        topic_id: str,
        *,
        candidate: dict[str, Any],
        content: str,
        block_type: str,
    ) -> dict[str, Any]:
        normalized_id = normalize_topic_id(topic_id)
        now_text = _now_text()
        with self._lock:
            meta = deepcopy(self.ensure_topic(normalized_id, persisted=True))
            summary_document = self.load_summary_document(normalized_id)
            blocks = [deepcopy(item) for item in (summary_document.get("blocks") or []) if isinstance(item, dict)]
            block_index = int(candidate.get("block_index") or 0)
            raw_block = deepcopy(blocks[block_index])
            start_turn = int(candidate.get("start_assistant_turn") or 0)
            end_turn = int(candidate.get("end_assistant_turn") or start_turn)

            before_messages: list[dict[str, Any]] = []
            after_messages: list[dict[str, Any]] = []
            for item in raw_block.get("messages") or []:
                if not isinstance(item, dict):
                    continue
                assistant_turn = int(item.get("assistant_turn") or 0)
                if assistant_turn < start_turn:
                    before_messages.append(deepcopy(item))
                elif assistant_turn > end_turn:
                    after_messages.append(deepcopy(item))

            replacement_blocks: list[dict[str, Any]] = []
            if before_messages:
                replacement_blocks.append(self._raw_block_from_messages(before_messages, now_text))
            replacement_blocks.append(
                {
                    "type": block_type,
                    "start_assistant_turn": start_turn,
                    "end_assistant_turn": end_turn,
                    "created_at": now_text,
                    "updated_at": now_text,
                    "content": str(content or "").strip(),
                }
            )
            if after_messages:
                replacement_blocks.append(self._raw_block_from_messages(after_messages, now_text))

            blocks[block_index : block_index + 1] = replacement_blocks
            summary_document["topic_id"] = normalized_id
            summary_document["updated_at"] = now_text
            summary_document["blocks"] = blocks

            if block_type == BLOCK_MINI_SUMMARY:
                meta["mini_summary_count"] = int(meta.get("mini_summary_count") or 0) + 1
            meta["updated_at"] = now_text
            meta["preview"] = self._build_preview(blocks)
            pending = self._pending_raw_turn_range(blocks)
            meta["pending_summary_start_assistant_turn"] = pending[0] if pending else None
            meta["pending_summary_end_assistant_turn"] = pending[1] if pending else None

            self._write_json(self.meta_path(normalized_id), meta)
            self._write_json(self.summary_path(normalized_id), summary_document)
            self._invalidate_snapshot_cache(normalized_id)
            return {
                "meta": meta,
                "summary": summary_document,
            }

    def _group_messages_by_turn(self, raw_messages: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
        grouped: dict[int, list[dict[str, Any]]] = {}
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            turn = int(item.get("assistant_turn") or 0)
            if turn <= 0:
                continue
            grouped.setdefault(turn, []).append(deepcopy(item))
        return grouped

    def _raw_block_from_messages(self, messages: list[dict[str, Any]], now_text: str) -> dict[str, Any]:
        grouped = self._group_messages_by_turn(messages)
        ordered_turns = sorted(grouped)
        if not ordered_turns:
            return {
                "type": BLOCK_RAW_MESSAGES,
                "start_assistant_turn": 0,
                "end_assistant_turn": 0,
                "created_at": now_text,
                "updated_at": now_text,
                "messages": [],
            }
        flattened: list[dict[str, Any]] = []
        for turn in ordered_turns:
            flattened.extend(grouped[turn])
        return {
            "type": BLOCK_RAW_MESSAGES,
            "start_assistant_turn": ordered_turns[0],
            "end_assistant_turn": ordered_turns[-1],
            "created_at": now_text,
            "updated_at": now_text,
            "messages": flattened,
        }

    def _pending_raw_turn_range(self, blocks: list[dict[str, Any]]) -> tuple[int, int] | None:
        for block in blocks:
            if not isinstance(block, dict) or str(block.get("type") or "") != BLOCK_RAW_MESSAGES:
                continue
            start_turn, end_turn = _block_turn_span(block)
            if start_turn > 0 and end_turn >= start_turn:
                return start_turn, end_turn
        return None

    def _build_preview(self, blocks: list[dict[str, Any]]) -> str:
        if not blocks:
            return ""
        last_block = next((item for item in reversed(blocks) if isinstance(item, dict)), None)
        if not last_block:
            return ""
        block_type = str(last_block.get("type") or "")
        if block_type in {BLOCK_MINI_SUMMARY, BLOCK_MAJOR_SUMMARY}:
            return _compact_text(last_block.get("content") or "", MAX_TOPIC_PREVIEW_CHARS)
        for item in reversed(last_block.get("messages") or []):
            if not isinstance(item, dict):
                continue
            content = _compact_text(item.get("content") or "", MAX_TOPIC_PREVIEW_CHARS)
            if content:
                return content
        return ""

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _write_jsonl(self, path: Path, payloads: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(json.dumps(payload, ensure_ascii=False) + "\n" for payload in payloads)
        path.write_text(content, encoding="utf-8")

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _invalidate_snapshot_cache(self, topic_id: str) -> None:
        self._snapshot_cache.pop(normalize_topic_id(topic_id), None)
