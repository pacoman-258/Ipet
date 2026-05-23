from __future__ import annotations

import json
import os
import re
import shutil
import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


MEMORY_MODE_PERSISTENT = "persistent"
MEMORY_MODE_TEMPORARY = "temporary"
MEMORY_MODES = {MEMORY_MODE_PERSISTENT, MEMORY_MODE_TEMPORARY}
DEFAULT_CONVERSATION_TITLE = "新对话"
MAX_CONVERSATION_TITLE_CHARS = 24
MAX_TOPIC_PREVIEW_CHARS = 96


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_memory_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in MEMORY_MODES else MEMORY_MODE_PERSISTENT


def normalize_conversation_id(value: Any = "") -> str:
    text = str(value or "").strip()
    if not text:
        return uuid4().hex
    text = text.replace("\\", "-").replace("/", "-")
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-_")
    return text or uuid4().hex


def compact_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\r", "\n").split())
    if not text:
        return ""
    safe_limit = max(0, int(limit or 0))
    if safe_limit <= 0:
        return ""
    if len(text) <= safe_limit:
        return text
    if safe_limit <= 3:
        return "." * safe_limit
    return f"{text[: safe_limit - 3].rstrip()}..."


def build_conversation_title(first_user_text: str) -> str:
    return compact_text(first_user_text, MAX_CONVERSATION_TITLE_CHARS) or DEFAULT_CONVERSATION_TITLE


@dataclass(frozen=True)
class ConversationContext:
    conversation_id: str
    meta: dict[str, Any]
    recent_messages: list[dict[str, Any]]
    rolling_summary: str
    major_summary: str


class ConversationStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self._lock = threading.RLock()

    def create_conversation(
        self,
        *,
        conversation_id: str | None = None,
        title: str = DEFAULT_CONVERSATION_TITLE,
        memory_mode: str = MEMORY_MODE_PERSISTENT,
        persisted: bool = True,
        runtime: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_id = normalize_conversation_id(conversation_id)
        normalized_mode = normalize_memory_mode(memory_mode)
        should_persist = bool(persisted) and normalized_mode == MEMORY_MODE_PERSISTENT
        created_at = now_text()
        meta = {
            "conversation_id": normalized_id,
            "topic_id": normalized_id,
            "session_id": normalized_id,
            "title": str(title or DEFAULT_CONVERSATION_TITLE),
            "preview": "",
            "created_at": created_at,
            "updated_at": created_at,
            "persisted": should_persist,
            "memory_mode": normalized_mode,
            "runtime": "ipet",
            "source": "ipet",
            "execution_runtime": str(runtime or ""),
            "tags": [],
            "assistant_turn_count": 0,
            "save_disabled": False,
            "save_disabled_reason": "",
            "last_summary_turn": 0,
            "last_memory_extracted_turn": 0,
            "metadata": dict(metadata or {}),
            "supports_history_detail": True,
            "supports_delete": True,
        }
        if not should_persist:
            meta["persisted"] = False
            return meta

        with self._lock:
            existing = self.load_state(normalized_id)
            if existing is not None:
                return deepcopy(existing)
            conversation_dir = self.conversation_dir(normalized_id)
            summaries_dir = conversation_dir / "summaries"
            summaries_dir.mkdir(parents=True, exist_ok=True)
            self._write_json(self.state_path(normalized_id), meta)
            self.turns_path(normalized_id).write_text("", encoding="utf-8")
            self.transcript_path(normalized_id).write_text(self._transcript_header(meta), encoding="utf-8")
            self.rolling_summary_path(normalized_id).write_text("", encoding="utf-8")
            self.major_summary_path(normalized_id).write_text("", encoding="utf-8")
            self._write_index()
        return deepcopy(meta)

    def ensure_conversation(
        self,
        conversation_id: str,
        *,
        title: str = DEFAULT_CONVERSATION_TITLE,
        memory_mode: str = MEMORY_MODE_PERSISTENT,
        persisted: bool = True,
        runtime: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_id = normalize_conversation_id(conversation_id)
        if bool(persisted) and normalize_memory_mode(memory_mode) == MEMORY_MODE_PERSISTENT:
            state = self.load_state(normalized_id)
            if state is not None:
                return state
        return self.create_conversation(
            conversation_id=normalized_id,
            title=title,
            memory_mode=memory_mode,
            persisted=persisted,
            runtime=runtime,
            metadata=metadata,
        )

    def load_state(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock:
            path = self.state_path(conversation_id)
            if not path.exists():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                return None
            if not isinstance(data, dict):
                return None
            data.setdefault("conversation_id", normalize_conversation_id(conversation_id))
            data.setdefault("topic_id", data["conversation_id"])
            data.setdefault("session_id", data["conversation_id"])
            data.setdefault("title", DEFAULT_CONVERSATION_TITLE)
            data.setdefault("preview", "")
            data.setdefault("assistant_turn_count", 0)
            data.setdefault("persisted", True)
            data.setdefault("memory_mode", MEMORY_MODE_PERSISTENT)
            data["runtime"] = "ipet"
            data.setdefault("source", "ipet")
            data["source"] = "ipet"
            data.setdefault("execution_runtime", "")
            data.setdefault("tags", [])
            if not isinstance(data["tags"], list):
                data["tags"] = []
            data.setdefault("last_summary_turn", 0)
            data.setdefault("last_memory_extracted_turn", 0)
            data.setdefault("save_disabled", False)
            data.setdefault("save_disabled_reason", "")
            data.setdefault("supports_history_detail", True)
            data.setdefault("supports_delete", True)
            return data

    def list_topics(self) -> list[dict[str, Any]]:
        with self._lock:
            conversations_dir = self.conversations_dir
            if not conversations_dir.exists():
                return []
            topics: list[dict[str, Any]] = []
            for path in conversations_dir.iterdir():
                if not path.is_dir():
                    continue
                state = self.load_state(path.name)
                if state is None:
                    continue
                if int(state.get("assistant_turn_count") or 0) <= 0:
                    continue
                topics.append(self._topic_from_state(state))
            topics.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            return topics

    def get_topic_detail(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock:
            normalized_id = normalize_conversation_id(conversation_id)
            state = self.load_state(normalized_id)
            if state is None:
                return None
            return {
                "ok": True,
                "runtime": "ipet",
                "topic": self._topic_from_state(state),
                "meta": deepcopy(state),
                "messages": self.load_messages(normalized_id),
                "history_unavailable": False,
                "supports_history_detail": True,
                "supports_delete": True,
            }

    def load_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            path = self.turns_path(conversation_id)
            if not path.exists():
                return []
            messages: list[dict[str, Any]] = []
            for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
                if not raw_line.strip():
                    continue
                try:
                    item = json.loads(raw_line)
                except Exception:
                    continue
                if not isinstance(item, dict):
                    continue
                created_at = str(item.get("created_at") or "")
                assistant_turn = int(item.get("assistant_turn") or 0)
                turn_id = str(item.get("turn_id") or "")
                user = item.get("user") if isinstance(item.get("user"), dict) else {}
                assistant = item.get("assistant") if isinstance(item.get("assistant"), dict) else {}
                user_text = str(user.get("content") if user else item.get("user_text") or "")
                assistant_text = str(assistant.get("content") if assistant else item.get("assistant_text") or "")
                if user_text:
                    messages.append(
                        {
                            "role": "user",
                            "content": user_text,
                            "created_at": created_at,
                            "assistant_turn": assistant_turn,
                            "turn_id": turn_id,
                        }
                    )
                if assistant_text:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": assistant_text,
                            "created_at": created_at,
                            "assistant_turn": assistant_turn,
                            "turn_id": turn_id,
                        }
                    )
            return messages

    def load_context(
        self,
        conversation_id: str,
        *,
        recent_turns: int = 8,
        recent_limit: int | None = None,
    ) -> ConversationContext | None:
        with self._lock:
            normalized_id = normalize_conversation_id(conversation_id)
            state = self.load_state(normalized_id)
            if state is None:
                return None
            messages = self.load_messages(normalized_id)
            if recent_limit is not None:
                limit = max(0, int(recent_limit or 0))
                recent_messages = messages[-limit:] if limit else []
            else:
                recent_messages = self._recent_messages_by_turn(messages, recent_turns)
            return ConversationContext(
                conversation_id=normalized_id,
                meta=deepcopy(state),
                recent_messages=recent_messages,
                rolling_summary=self._read_optional_text(self.rolling_summary_path(normalized_id)),
                major_summary=self._read_optional_text(self.major_summary_path(normalized_id)),
            )

    def append_turn(
        self,
        conversation_id: str,
        *,
        turn_id: str,
        user_text: str,
        assistant_text: str,
        runtime: str,
        runtime_session_id: str,
        memory_mode: str = MEMORY_MODE_PERSISTENT,
        save_policy: str = "save",
        status: str = "completed",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_mode = normalize_memory_mode(memory_mode)
        normalized_id = normalize_conversation_id(conversation_id)
        if normalized_mode == MEMORY_MODE_TEMPORARY:
            return {"saved": False, "reason": "temporary", "conversation_id": normalized_id}

        with self._lock:
            state = self.ensure_conversation(
                normalized_id,
                title=DEFAULT_CONVERSATION_TITLE,
                memory_mode=normalized_mode,
                persisted=True,
                runtime=runtime,
            )
            if bool(state.get("save_disabled")):
                return {"saved": False, "reason": "save_disabled", "conversation_id": normalized_id}

            created_at = now_text()
            next_turn = int(state.get("assistant_turn_count") or 0) + 1
            record = {
                "turn_id": str(turn_id or ""),
                "created_at": created_at,
                "status": str(status or "completed"),
                "memory_mode": normalized_mode,
                "save_policy": str(save_policy or ""),
                "runtime": str(runtime or ""),
                "runtime_session_id": str(runtime_session_id or ""),
                "user": {"role": "user", "content": str(user_text or "")},
                "assistant": {"role": "assistant", "content": str(assistant_text or "")},
                "metadata": dict(metadata or {}),
                "assistant_turn": next_turn,
            }
            self._append_jsonl(self.turns_path(normalized_id), record)

            previous_turn_count = int(state.get("assistant_turn_count") or 0)
            state["assistant_turn_count"] = next_turn
            state["updated_at"] = created_at
            state["runtime"] = "ipet"
            state["source"] = "ipet"
            state["execution_runtime"] = str(runtime or state.get("execution_runtime") or "")
            state["preview"] = compact_text(user_text or assistant_text, MAX_TOPIC_PREVIEW_CHARS)
            if previous_turn_count == 0 or str(state.get("title") or "").strip() in {"", DEFAULT_CONVERSATION_TITLE}:
                state["title"] = build_conversation_title(user_text)
            self._write_json(self.state_path(normalized_id), state)
            self._append_transcript(normalized_id, record)
            self._write_index()
            return {
                "ok": True,
                "saved": True,
                "conversation_id": normalized_id,
                "assistant_turn": next_turn,
                "turn": deepcopy(record),
                "meta": deepcopy(state),
            }

    def disable_saving(self, conversation_id: str, *, reason: str = "") -> dict[str, Any] | None:
        normalized_id = normalize_conversation_id(conversation_id)
        with self._lock:
            state = self.ensure_conversation(normalized_id, persisted=True, memory_mode=MEMORY_MODE_PERSISTENT)
            state["save_disabled"] = True
            state["save_disabled_reason"] = str(reason or "")
            state["updated_at"] = now_text()
            self._write_json(self.state_path(normalized_id), state)
            self._write_index()
            return deepcopy(state)

    def update_memory_extracted_turn(self, conversation_id: str, turn_id: str | int | None) -> dict[str, Any] | None:
        normalized_id = normalize_conversation_id(conversation_id)
        with self._lock:
            state = self.load_state(normalized_id)
            if state is None:
                return None
            state["last_memory_extracted_turn"] = turn_id
            state["updated_at"] = now_text()
            self._write_json(self.state_path(normalized_id), state)
            self._write_index()
            return deepcopy(state)

    def delete_conversation(self, conversation_id: str) -> dict[str, Any]:
        normalized_id = normalize_conversation_id(conversation_id)
        with self._lock:
            path = self.conversation_dir(normalized_id)
            if path.exists():
                shutil.rmtree(path, ignore_errors=False)
            self._write_index()
            return {"ok": True, "deleted_topic_id": normalized_id, "topics": self.list_topics()}

    def write_cleanup_record(
        self,
        *,
        runtime_turn_id: str,
        runtime: str,
        runtime_session_id: str,
        error: str,
    ) -> Path:
        payload = {
            "runtime_turn_id": str(runtime_turn_id or ""),
            "runtime": str(runtime or ""),
            "runtime_session_id": str(runtime_session_id or ""),
            "error": str(error or ""),
            "created_at": now_text(),
        }
        with self._lock:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            path = self.temp_dir / f"{normalize_conversation_id(runtime_turn_id)}.json"
            self._write_json(path, payload)
        return path

    def clear_cleanup_record(self, runtime_turn_id: str) -> bool:
        path = self.temp_dir / f"{normalize_conversation_id(runtime_turn_id)}.json"
        with self._lock:
            if not path.exists():
                return False
            path.unlink()
            return True

    @property
    def conversations_dir(self) -> Path:
        return self.root_dir / "conversations"

    @property
    def temp_dir(self) -> Path:
        return self.root_dir / "temp"

    def conversation_dir(self, conversation_id: str) -> Path:
        return self.conversations_dir / normalize_conversation_id(conversation_id)

    def state_path(self, conversation_id: str) -> Path:
        return self.conversation_dir(conversation_id) / "state.json"

    def turns_path(self, conversation_id: str) -> Path:
        return self.conversation_dir(conversation_id) / "turns.jsonl"

    def transcript_path(self, conversation_id: str) -> Path:
        return self.conversation_dir(conversation_id) / "conversation.md"

    def conversation_path(self, conversation_id: str) -> Path:
        return self.transcript_path(conversation_id)

    def rolling_summary_path(self, conversation_id: str) -> Path:
        return self.conversation_dir(conversation_id) / "summaries" / "rolling.md"

    def major_summary_path(self, conversation_id: str) -> Path:
        return self.conversation_dir(conversation_id) / "summaries" / "major.md"

    def _topic_from_state(self, state: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(state.get("conversation_id") or state.get("topic_id") or "")
        return {
            "topic_id": conversation_id,
            "conversation_id": conversation_id,
            "title": str(state.get("title") or DEFAULT_CONVERSATION_TITLE),
            "preview": str(state.get("preview") or ""),
            "created_at": str(state.get("created_at") or ""),
            "updated_at": str(state.get("updated_at") or ""),
            "persisted": bool(state.get("persisted", True)),
            "assistant_turn_count": int(state.get("assistant_turn_count") or 0),
            "supports_history_detail": True,
            "supports_delete": True,
            "history_unavailable": False,
            "runtime": "ipet",
            "source": "ipet",
            "memory_mode": normalize_memory_mode(state.get("memory_mode")),
        }

    def _recent_messages_by_turn(self, messages: list[dict[str, Any]], recent_turns: int) -> list[dict[str, Any]]:
        limit = max(0, int(recent_turns or 0))
        if limit <= 0:
            return []
        seen_turns: list[int] = []
        for item in reversed(messages):
            turn = int(item.get("assistant_turn") or 0)
            if turn and turn not in seen_turns:
                seen_turns.append(turn)
            if len(seen_turns) >= limit:
                break
        selected_turns = set(seen_turns)
        return [item for item in messages if int(item.get("assistant_turn") or 0) in selected_turns]

    def _write_index(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        topics = self.list_topics()
        lines = ["# Ipet Conversations", ""]
        if not topics:
            lines.append("_No saved conversations._")
        else:
            lines.append("| Updated | Conversation | Turns |")
            lines.append("| --- | --- | ---: |")
            for topic in topics:
                lines.append(
                    "| {updated} | [{title}](conversations/{conversation_id}/conversation.md) `{conversation_id}` | {turns} |".format(
                        updated=topic.get("updated_at") or "",
                        title=str(topic.get("title") or DEFAULT_CONVERSATION_TITLE).replace("|", "\\|"),
                        conversation_id=topic.get("conversation_id") or "",
                        turns=topic.get("assistant_turn_count") or 0,
                    )
                )
        (self.root_dir / "INDEX.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def _transcript_header(self, meta: dict[str, Any]) -> str:
        title = str(meta.get("title") or DEFAULT_CONVERSATION_TITLE)
        conversation_id = str(meta.get("conversation_id") or "")
        created_at = str(meta.get("created_at") or "")
        return f"# {title}\n\n- Conversation ID: `{conversation_id}`\n- Created: {created_at}\n\n"

    def _append_transcript(self, conversation_id: str, record: dict[str, Any]) -> None:
        path = self.conversation_path(conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            state = self.load_state(conversation_id) or {"conversation_id": conversation_id, "created_at": now_text()}
            path.write_text(self._transcript_header(state), encoding="utf-8")
        assistant_turn = int(record.get("assistant_turn") or 0)
        created_at = str(record.get("created_at") or "")
        user = record.get("user") if isinstance(record.get("user"), dict) else {}
        assistant = record.get("assistant") if isinstance(record.get("assistant"), dict) else {}
        user_text = str(user.get("content") if user else record.get("user_text") or "")
        assistant_text = str(assistant.get("content") if assistant else record.get("assistant_text") or "")
        chunk = (
            f"## Turn {assistant_turn}\n\n"
            f"- Created: {created_at}\n\n"
            f"**User**\n\n{user_text}\n\n"
            f"**Assistant**\n\n{assistant_text}\n\n"
        )
        with path.open("a", encoding="utf-8") as stream:
            stream.write(chunk)

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp_path, path)

    def _read_optional_text(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8-sig")
