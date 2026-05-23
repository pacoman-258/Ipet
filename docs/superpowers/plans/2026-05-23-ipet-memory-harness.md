# Ipet Memory Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement an Ipet-owned conversation and long-term memory harness that keeps durable state in local files while using AstrBot only as a per-turn temporary task executor.

**Architecture:** Add focused backend modules for local conversations, local long-term memory, runtime task cleanup, and the harness orchestration. Keep frontend-facing chat and topic APIs stable, but make them read/write Ipet local state instead of AstrBot history; route each runtime turn through an `ipet-temp-*` AstrBot session and delete it in cleanup.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, unittest, existing runtime adapter protocol, Qt WebEngine root `index.html`, local Markdown/JSONL storage.

---

## File Structure

Create these backend modules:

- `backend/conversation_store.py`: owns `data/ipet_conversations/`, `INDEX.md`, conversation state, JSONL turns, frontend-compatible list/detail payloads, delete behavior, and runtime cleanup records.
- `backend/ipet_memory_store.py`: owns `data/ipet_memory/`, `MEMORY.md`, Markdown memory records, simple keyword search, duplicate suppression, and deterministic v1 extraction.
- `backend/runtime_tasks.py`: owns temporary runtime session id generation and best-effort runtime cleanup helpers.
- `backend/memory_harness.py`: orchestrates request policy, context assembly, runtime streaming, persistence, memory extraction, and cleanup.

Modify these existing files:

- `backend/models.py`: add `memory_mode` to `ChatStreamRequest`.
- `backend/app.py`: wire `MemoryHarness` into `/api/chat/stream`; route `/api/chat/topics*` to `ConversationStore`; keep vision evidence wrapping before harness context assembly.
- `backend/runtime_contracts.py`: document optional runtime cleanup capability through helper functions, without forcing all adapters to implement new methods immediately.
- `backend/runtime_adapters.py`: add an AstrBot cleanup helper for temporary sessions, reusing Dashboard deletion where available.
- `index.html`: add persistent/temporary memory mode UI, send `memory_mode`, skip topic creation/history refresh in temporary mode.
- `tests/test_chat_modes_ui.py`: assert temporary mode UI and payload hooks.
- `docs/WORKFLOW.md`: describe Ipet-owned conversation/memory and AstrBot temporary task execution.

Create these tests:

- `tests/test_conversation_store.py`
- `tests/test_ipet_memory_store.py`
- `tests/test_runtime_tasks.py`
- `tests/test_memory_harness.py`
- `tests/test_memory_harness_api.py`

Use the approved test runner:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest <test-module> -v
```

### Task 1: Conversation Store

**Files:**

- Create: `backend/conversation_store.py`
- Create: `tests/test_conversation_store.py`

- [ ] **Step 1: Write the failing conversation store tests**

Create `tests/test_conversation_store.py` with these tests:

```python
from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from backend.conversation_store import ConversationStore, MEMORY_MODE_PERSISTENT, MEMORY_MODE_TEMPORARY


class ConversationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / ".tmp_conversation_store" / uuid4().hex
        self.store = ConversationStore(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_append_list_detail_and_delete_conversation(self) -> None:
        meta = self.store.create_conversation(title="新对话")
        conversation_id = meta["conversation_id"]

        self.store.append_turn(
            conversation_id,
            turn_id="turn-1",
            user_text="我喜欢红茶",
            assistant_text="记住啦",
            runtime="astrbot",
            runtime_session_id="ipet-temp-turn-1",
            memory_mode=MEMORY_MODE_PERSISTENT,
            save_policy="save",
            metadata={"chat_mode": "react"},
        )

        topics = self.store.list_topics()
        self.assertEqual([item["topic_id"] for item in topics], [conversation_id])
        self.assertEqual(topics[0]["title"], "我喜欢红茶")
        self.assertEqual(topics[0]["assistant_turn_count"], 1)
        self.assertTrue(topics[0]["supports_history_detail"])
        self.assertTrue(topics[0]["supports_delete"])

        detail = self.store.get_topic_detail(conversation_id)
        self.assertIsNotNone(detail)
        self.assertEqual([item["role"] for item in detail["messages"]], ["user", "assistant"])
        self.assertEqual(detail["messages"][0]["content"], "我喜欢红茶")
        self.assertIn("我喜欢红茶", (self.root / "conversations" / conversation_id / "conversation.md").read_text(encoding="utf-8"))
        self.assertIn(conversation_id, (self.root / "INDEX.md").read_text(encoding="utf-8"))

        payload = self.store.delete_conversation(conversation_id)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.store.list_topics(), [])

    def test_temporary_mode_does_not_create_conversation_content(self) -> None:
        meta = self.store.create_conversation(title="临时", memory_mode=MEMORY_MODE_TEMPORARY, persisted=False)
        self.assertFalse(meta["persisted"])
        self.assertFalse((self.root / "conversations").exists())
        self.assertEqual(self.store.list_topics(), [])

    def test_save_disabled_state_skips_future_appends(self) -> None:
        meta = self.store.create_conversation(title="隐私对话")
        conversation_id = meta["conversation_id"]
        self.store.disable_saving(conversation_id, reason="user_requested_no_save")
        saved = self.store.append_turn(
            conversation_id,
            turn_id="turn-1",
            user_text="这段不要保存",
            assistant_text="好的",
            runtime="astrbot",
            runtime_session_id="ipet-temp-turn-1",
            memory_mode=MEMORY_MODE_PERSISTENT,
            save_policy="conversation_disabled",
        )
        self.assertFalse(saved["saved"])
        detail = self.store.get_topic_detail(conversation_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["messages"], [])
        self.assertTrue(detail["meta"]["save_disabled"])

    def test_cleanup_record_contains_no_transcript(self) -> None:
        self.store.write_cleanup_record(
            runtime_turn_id="turn-cleanup",
            runtime="astrbot",
            runtime_session_id="ipet-temp-turn-cleanup",
            error="delete failed",
        )
        path = self.root / "temp" / "turn-cleanup.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["runtime_session_id"], "ipet-temp-turn-cleanup")
        self.assertNotIn("user", payload)
        self.assertNotIn("assistant", payload)
        self.assertNotIn("content", json.dumps(payload, ensure_ascii=False))
```

- [ ] **Step 2: Run the conversation store tests and verify red**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_conversation_store -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.conversation_store'`.

- [ ] **Step 3: Implement `backend/conversation_store.py`**

Create `backend/conversation_store.py` with this public API and behavior:

```python
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


MEMORY_MODE_PERSISTENT = "persistent"
MEMORY_MODE_TEMPORARY = "temporary"
MEMORY_MODES = {MEMORY_MODE_PERSISTENT, MEMORY_MODE_TEMPORARY}
DEFAULT_CONVERSATION_TITLE = "新对话"


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_memory_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in MEMORY_MODES else MEMORY_MODE_PERSISTENT


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
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def build_conversation_title(first_user_text: str) -> str:
    return compact_text(first_user_text, 32) or DEFAULT_CONVERSATION_TITLE


@dataclass(frozen=True)
class ConversationContext:
    conversation_id: str
    meta: dict[str, Any]
    recent_messages: tuple[dict[str, str], ...]
    rolling_summary: str
    major_summary: str


class ConversationStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.conversations_dir = self.root_dir / "conversations"
        self.temp_dir = self.root_dir / "temp"
        self.index_path = self.root_dir / "INDEX.md"
        self._lock = threading.RLock()

    def conversation_dir(self, conversation_id: str) -> Path:
        return self.conversations_dir / normalize_conversation_id(conversation_id)

    def state_path(self, conversation_id: str) -> Path:
        return self.conversation_dir(conversation_id) / "state.json"

    def turns_path(self, conversation_id: str) -> Path:
        return self.conversation_dir(conversation_id) / "turns.jsonl"

    def transcript_path(self, conversation_id: str) -> Path:
        return self.conversation_dir(conversation_id) / "conversation.md"

    def rolling_summary_path(self, conversation_id: str) -> Path:
        return self.conversation_dir(conversation_id) / "summaries" / "rolling.md"

    def major_summary_path(self, conversation_id: str) -> Path:
        return self.conversation_dir(conversation_id) / "summaries" / "major.md"

    def create_conversation(self, *, conversation_id: str = "", title: str = DEFAULT_CONVERSATION_TITLE, memory_mode: str = MEMORY_MODE_PERSISTENT, persisted: bool = True) -> dict[str, Any]:
        mode = normalize_memory_mode(memory_mode)
        normalized_id = normalize_conversation_id(conversation_id)
        created_at = now_text()
        meta = {
            "conversation_id": normalized_id,
            "topic_id": normalized_id,
            "session_id": normalized_id,
            "title": str(title or DEFAULT_CONVERSATION_TITLE).strip() or DEFAULT_CONVERSATION_TITLE,
            "preview": "",
            "created_at": created_at,
            "updated_at": created_at,
            "memory_mode": mode,
            "persisted": bool(persisted and mode == MEMORY_MODE_PERSISTENT),
            "save_disabled": False,
            "save_disabled_reason": "",
            "assistant_turn_count": 0,
            "last_summary_turn": 0,
            "last_memory_extracted_turn": 0,
            "tags": [],
            "runtime": "ipet",
            "source": "ipet",
            "supports_history_detail": True,
            "supports_delete": True,
        }
        if not meta["persisted"]:
            return meta
        with self._lock:
            directory = self.conversation_dir(normalized_id)
            (directory / "summaries").mkdir(parents=True, exist_ok=True)
            self._write_json(self.state_path(normalized_id), meta)
            self.turns_path(normalized_id).write_text("", encoding="utf-8")
            self.transcript_path(normalized_id).write_text(f"# {meta['title']}\n\n", encoding="utf-8")
            self.rolling_summary_path(normalized_id).write_text("", encoding="utf-8")
            self.major_summary_path(normalized_id).write_text("", encoding="utf-8")
            self._write_index()
        return deepcopy(meta)

    def ensure_conversation(self, conversation_id: str, *, title: str = DEFAULT_CONVERSATION_TITLE) -> dict[str, Any]:
        existing = self.load_state(conversation_id)
        if existing is not None:
            return existing
        return self.create_conversation(conversation_id=conversation_id, title=title)

    def load_state(self, conversation_id: str) -> dict[str, Any] | None:
        path = self.state_path(conversation_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        payload.setdefault("topic_id", payload.get("conversation_id"))
        payload.setdefault("session_id", payload.get("conversation_id"))
        payload.setdefault("runtime", "ipet")
        payload.setdefault("source", "ipet")
        payload.setdefault("supports_history_detail", True)
        payload.setdefault("supports_delete", True)
        payload.setdefault("save_disabled", False)
        payload.setdefault("save_disabled_reason", "")
        return payload

    def list_topics(self) -> list[dict[str, Any]]:
        if not self.conversations_dir.exists():
            return []
        topics: list[dict[str, Any]] = []
        for directory in self.conversations_dir.iterdir():
            if not directory.is_dir():
                continue
            state = self.load_state(directory.name)
            if state is not None and bool(state.get("persisted", True)):
                topics.append(self._topic_payload(state))
        topics.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return topics

    def get_topic_detail(self, conversation_id: str) -> dict[str, Any] | None:
        state = self.load_state(conversation_id)
        if state is None:
            return None
        return {
            "ok": True,
            "runtime": "ipet",
            "meta": self._topic_payload(state),
            "topic": self._topic_payload(state),
            "messages": self.load_messages(conversation_id),
            "history_unavailable": False,
            "supports_history_detail": True,
            "supports_delete": True,
        }

    def load_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        path = self.turns_path(conversation_id)
        if not path.exists():
            return []
        messages: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            for key in ("user", "assistant"):
                part = item.get(key) if isinstance(item, dict) else None
                if isinstance(part, dict) and str(part.get("content") or ""):
                    messages.append({"role": key, "content": str(part.get("content") or ""), "created_at": item.get("created_at") or ""})
        return messages

    def load_context(self, conversation_id: str, *, recent_turns: int = 8) -> ConversationContext | None:
        state = self.load_state(conversation_id)
        if state is None:
            return None
        messages = self.load_messages(conversation_id)[-max(1, recent_turns) * 2 :]
        rolling = self.rolling_summary_path(conversation_id).read_text(encoding="utf-8") if self.rolling_summary_path(conversation_id).exists() else ""
        major = self.major_summary_path(conversation_id).read_text(encoding="utf-8") if self.major_summary_path(conversation_id).exists() else ""
        return ConversationContext(
            conversation_id=normalize_conversation_id(conversation_id),
            meta=deepcopy(state),
            recent_messages=tuple({"role": item["role"], "content": item["content"]} for item in messages),
            rolling_summary=rolling.strip(),
            major_summary=major.strip(),
        )

    def append_turn(self, conversation_id: str, *, turn_id: str, user_text: str, assistant_text: str, runtime: str, runtime_session_id: str, memory_mode: str, save_policy: str, status: str = "completed", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        mode = normalize_memory_mode(memory_mode)
        if mode == MEMORY_MODE_TEMPORARY:
            return {"ok": True, "saved": False, "reason": "temporary"}
        with self._lock:
            state = self.ensure_conversation(conversation_id, title=build_conversation_title(user_text))
            if bool(state.get("save_disabled")):
                return {"ok": True, "saved": False, "reason": "conversation_disabled", "meta": state}
            now = now_text()
            next_turn = int(state.get("assistant_turn_count") or 0) + 1
            record = {
                "turn_id": str(turn_id or normalize_conversation_id()),
                "created_at": now,
                "status": str(status or "completed"),
                "memory_mode": mode,
                "save_policy": str(save_policy or "save"),
                "runtime": str(runtime or ""),
                "runtime_session_id": str(runtime_session_id or ""),
                "user": {"role": "user", "content": str(user_text or "")},
                "assistant": {"role": "assistant", "content": str(assistant_text or "")},
                "metadata": dict(metadata or {}),
            }
            self._append_jsonl(self.turns_path(conversation_id), record)
            state["assistant_turn_count"] = next_turn
            state["updated_at"] = now
            if int(state.get("assistant_turn_count") or 0) == 1 or state.get("title") == DEFAULT_CONVERSATION_TITLE:
                state["title"] = build_conversation_title(user_text)
            state["preview"] = compact_text(assistant_text or user_text, 120)
            self._write_json(self.state_path(conversation_id), state)
            self._append_transcript(conversation_id, record)
            self._write_index()
            return {"ok": True, "saved": True, "turn": record, "meta": deepcopy(state)}

    def disable_saving(self, conversation_id: str, *, reason: str) -> dict[str, Any]:
        with self._lock:
            state = self.ensure_conversation(conversation_id)
            state["save_disabled"] = True
            state["save_disabled_reason"] = str(reason or "user_requested_no_save")
            state["updated_at"] = now_text()
            self._write_json(self.state_path(conversation_id), state)
            self._write_index()
            return deepcopy(state)

    def update_memory_extracted_turn(self, conversation_id: str, turn_count: int) -> dict[str, Any] | None:
        with self._lock:
            state = self.load_state(conversation_id)
            if state is None:
                return None
            state["last_memory_extracted_turn"] = max(0, int(turn_count or 0))
            state["updated_at"] = now_text()
            self._write_json(self.state_path(conversation_id), state)
            self._write_index()
            return deepcopy(state)

    def delete_conversation(self, conversation_id: str) -> dict[str, Any]:
        normalized = normalize_conversation_id(conversation_id)
        with self._lock:
            directory = self.conversation_dir(normalized)
            if directory.exists():
                shutil.rmtree(directory)
            self._write_index()
            return {"ok": True, "deleted_topic_id": normalized, "topics": self.list_topics()}

    def write_cleanup_record(self, *, runtime_turn_id: str, runtime: str, runtime_session_id: str, error: str) -> Path:
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        path = self.temp_dir / f"{normalize_conversation_id(runtime_turn_id)}.json"
        self._write_json(path, {
            "runtime_turn_id": str(runtime_turn_id or ""),
            "runtime": str(runtime or ""),
            "runtime_session_id": str(runtime_session_id or ""),
            "error": str(error or ""),
            "created_at": now_text(),
        })
        return path

    def clear_cleanup_record(self, runtime_turn_id: str) -> None:
        try:
            (self.temp_dir / f"{normalize_conversation_id(runtime_turn_id)}.json").unlink(missing_ok=True)
        except Exception:
            return

    def _topic_payload(self, state: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(state.get("conversation_id") or state.get("topic_id") or "").strip()
        return {
            **deepcopy(state),
            "topic_id": conversation_id,
            "session_id": conversation_id,
            "runtime": "ipet",
            "source": "ipet",
            "supports_history_detail": True,
            "supports_delete": True,
            "history_unavailable": False,
        }

    def _append_transcript(self, conversation_id: str, record: dict[str, Any]) -> None:
        path = self.transcript_path(conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        user_text = str((record.get("user") or {}).get("content") or "")
        assistant_text = str((record.get("assistant") or {}).get("content") or "")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"## {record.get('created_at')}\n\n")
            fh.write(f"**User:** {user_text}\n\n")
            fh.write(f"**Assistant:** {assistant_text}\n\n")

    def _write_index(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        lines = ["# Ipet Conversations", ""]
        for topic in self.list_topics():
            lines.append(f"- [{topic['title']}](conversations/{topic['topic_id']}/conversation.md)")
            lines.append(f"  - id: {topic['topic_id']}")
            lines.append(f"  - updated_at: {topic.get('updated_at') or ''}")
            lines.append(f"  - preview: {topic.get('preview') or ''}")
        self.index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
```

- [ ] **Step 4: Run the conversation store tests and verify green**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_conversation_store -v
```

Expected: PASS for 4 tests.

- [ ] **Step 5: Commit Task 1**

Run:

```bash
git add backend/conversation_store.py tests/test_conversation_store.py
git commit -m "feat: add ipet conversation store"
```

### Task 2: Local Long-Term Memory Store

**Files:**

- Create: `backend/ipet_memory_store.py`
- Create: `tests/test_ipet_memory_store.py`

- [ ] **Step 1: Write the failing memory store tests**

Create `tests/test_ipet_memory_store.py`:

```python
from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from backend.ipet_memory_store import IpetMemoryStore, build_memory_candidates_from_turn


class IpetMemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / ".tmp_ipet_memory_store" / uuid4().hex
        self.store = IpetMemoryStore(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_write_search_and_duplicate_suppression(self) -> None:
        first = self.store.write_memory(
            title="用户偏好：红茶",
            summary="用户喜欢红茶，不喜欢太甜的饮品。",
            evidence="用户说：我喜欢红茶，不喜欢太甜。",
            source_conversation_id="conv-1",
            source_turn_id="turn-1",
            tags=["preference"],
        )
        second = self.store.write_memory(
            title="用户偏好：红茶",
            summary="用户喜欢红茶，不喜欢太甜的饮品。",
            evidence="重复来源",
            source_conversation_id="conv-1",
            source_turn_id="turn-2",
            tags=["preference"],
        )

        self.assertTrue(first["saved"])
        self.assertFalse(second["saved"])
        self.assertTrue(second["duplicate"])
        index_text = (self.root / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("用户偏好：红茶", index_text)
        results = self.store.search("红茶", limit=3)
        self.assertEqual(results[0]["title"], "用户偏好：红茶")
        self.assertIn("不喜欢太甜", results[0]["summary"])

    def test_build_memory_candidates_from_turn_keeps_only_durable_text(self) -> None:
        candidates = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-1",
            user_text="请记住：我以后写 Python 测试时偏好 unittest。",
            assistant_text="好的，我会记住。",
        )
        self.assertEqual(len(candidates), 1)
        self.assertIn("unittest", candidates[0]["summary"])
        self.assertIn("preference", candidates[0]["tags"])

    def test_build_memory_candidates_ignores_transient_and_secret_text(self) -> None:
        transient = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-2",
            user_text="帮我算一下 1+1",
            assistant_text="2",
        )
        secret = build_memory_candidates_from_turn(
            conversation_id="conv-1",
            turn_id="turn-3",
            user_text="请记住 token=abc123",
            assistant_text="我不能保存密钥。",
        )
        self.assertEqual(transient, [])
        self.assertEqual(secret, [])
```

- [ ] **Step 2: Run the memory store tests and verify red**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_ipet_memory_store -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.ipet_memory_store'`.

- [ ] **Step 3: Implement `backend/ipet_memory_store.py`**

Create `backend/ipet_memory_store.py`:

```python
from __future__ import annotations

import hashlib
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


SECRET_PATTERNS = (
    re.compile(r"\b(api[_-]?key|token|secret|password|passwd|private[_-]?key)\b", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
)
DURABLE_KEYWORDS = (
    "记住",
    "以后",
    "偏好",
    "喜欢",
    "不喜欢",
    "习惯",
    "决定",
    "长期",
    "计划",
    "约束",
    "prefer",
    "preference",
    "remember",
    "always",
    "never",
    "like",
    "dislike",
)


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def compact_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").replace("\r", "\n").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text).strip("-")
    digest = hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:10]
    return f"{text[:48].strip('-') or 'memory'}-{digest}"


def contains_secret(value: str) -> bool:
    return any(pattern.search(value or "") for pattern in SECRET_PATTERNS)


def build_memory_candidates_from_turn(*, conversation_id: str, turn_id: str, user_text: str, assistant_text: str) -> list[dict[str, Any]]:
    source = compact_text(user_text, 500)
    lowered = source.lower()
    if not source or contains_secret(source):
        return []
    if not any(keyword in lowered for keyword in DURABLE_KEYWORDS):
        return []
    title = "长期记忆"
    tags = ["memory"]
    if any(keyword in lowered for keyword in ("偏好", "喜欢", "不喜欢", "prefer", "preference", "like", "dislike")):
        title = "用户偏好"
        tags = ["preference"]
    elif any(keyword in lowered for keyword in ("决定", "约束", "计划", "以后", "always", "never")):
        title = "用户约束与计划"
        tags = ["decision"]
    summary = source
    if "：" in summary:
        summary = summary.split("：", 1)[1].strip() or summary
    elif ":" in summary:
        summary = summary.split(":", 1)[1].strip() or summary
    return [{
        "title": f"{title}：{compact_text(summary, 32)}",
        "summary": compact_text(summary, 240),
        "evidence": compact_text(user_text, 360),
        "source_conversation_id": str(conversation_id or ""),
        "source_turn_id": str(turn_id or ""),
        "tags": tags,
    }]


class IpetMemoryStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.memories_dir = self.root_dir / "memories"
        self.index_path = self.root_dir / "MEMORY.md"
        self._lock = threading.RLock()

    def write_memory(self, *, title: str, summary: str, evidence: str, source_conversation_id: str, source_turn_id: str, tags: list[str] | None = None) -> dict[str, Any]:
        title_text = compact_text(title, 120)
        summary_text = compact_text(summary, 1000)
        if not title_text or not summary_text or contains_secret(summary_text):
            return {"ok": True, "saved": False, "duplicate": False, "reason": "empty_or_secret"}
        existing = self.search(f"{title_text} {summary_text}", limit=20)
        normalized_summary = summary_text.casefold()
        for item in existing:
            if item["title"].casefold() == title_text.casefold() or normalized_summary in item["summary"].casefold() or item["summary"].casefold() in normalized_summary:
                return {"ok": True, "saved": False, "duplicate": True, "memory": item}
        memory_id = slugify(f"{title_text}-{summary_text}")
        now = now_text()
        tag_values = [str(item).strip() for item in (tags or []) if str(item).strip()]
        path = self.memories_dir / f"{memory_id}.md"
        body = "\n".join([
            f"# {title_text}",
            "",
            f"- id: {memory_id}",
            f"- created_at: {now}",
            f"- updated_at: {now}",
            f"- source_conversation_id: {source_conversation_id}",
            f"- source_turn_id: {source_turn_id}",
            f"- tags: {', '.join(tag_values)}",
            "",
            "## Summary",
            "",
            summary_text,
            "",
            "## Evidence",
            "",
            compact_text(evidence, 600),
            "",
        ])
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            self._write_index()
        return {"ok": True, "saved": True, "duplicate": False, "memory": self._record_from_path(path)}

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        terms = [term.casefold() for term in re.findall(r"[\w\u4e00-\u9fff]+", str(query or "")) if term.strip()]
        records = [self._record_from_path(path) for path in sorted(self.memories_dir.glob("*.md"))] if self.memories_dir.exists() else []
        if not terms:
            return records[:limit]
        scored: list[tuple[int, dict[str, Any]]] = []
        for record in records:
            haystack = f"{record['title']} {record['summary']} {' '.join(record.get('tags') or [])}".casefold()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, record))
        scored.sort(key=lambda item: (item[0], item[1].get("updated_at") or ""), reverse=True)
        return [record for _, record in scored[: max(1, int(limit or 1))]]

    def _record_from_path(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8-sig")
        title = path.stem
        summary = ""
        tags: list[str] = []
        source_conversation_id = ""
        source_turn_id = ""
        updated_at = ""
        lines = text.splitlines()
        if lines and lines[0].startswith("# "):
            title = lines[0][2:].strip()
        for line in lines:
            if line.startswith("- tags:"):
                tags = [item.strip() for item in line.split(":", 1)[1].split(",") if item.strip()]
            elif line.startswith("- source_conversation_id:"):
                source_conversation_id = line.split(":", 1)[1].strip()
            elif line.startswith("- source_turn_id:"):
                source_turn_id = line.split(":", 1)[1].strip()
            elif line.startswith("- updated_at:"):
                updated_at = line.split(":", 1)[1].strip()
        if "## Summary" in text:
            summary_part = text.split("## Summary", 1)[1]
            summary = summary_part.split("## Evidence", 1)[0].strip()
        return {
            "id": path.stem,
            "title": title,
            "summary": summary,
            "path": str(path),
            "tags": tags,
            "source_conversation_id": source_conversation_id,
            "source_turn_id": source_turn_id,
            "updated_at": updated_at,
        }

    def _write_index(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        records = [self._record_from_path(path) for path in sorted(self.memories_dir.glob("*.md"))] if self.memories_dir.exists() else []
        lines = ["# Ipet Memory", ""]
        for record in records:
            rel = Path(record["path"]).relative_to(self.root_dir).as_posix()
            lines.append(f"- [{record['title']}]({rel})")
            lines.append(f"  - id: {record['id']}")
            lines.append(f"  - updated_at: {record.get('updated_at') or ''}")
            lines.append(f"  - tags: {', '.join(record.get('tags') or [])}")
            lines.append(f"  - summary: {compact_text(record.get('summary') or '', 160)}")
        self.index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run the memory store tests and verify green**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_ipet_memory_store -v
```

Expected: PASS for 3 tests.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add backend/ipet_memory_store.py tests/test_ipet_memory_store.py
git commit -m "feat: add local ipet memory store"
```

### Task 3: Runtime Temporary Session Helpers

**Files:**

- Create: `backend/runtime_tasks.py`
- Modify: `backend/runtime_adapters.py`
- Test: `tests/test_runtime_tasks.py`

- [ ] **Step 1: Write failing runtime task tests**

Create `tests/test_runtime_tasks.py`:

```python
from __future__ import annotations

import asyncio
import unittest

from backend.runtime_tasks import cleanup_runtime_session, temporary_runtime_session_id


class _FakeRuntimeClient:
    runtime_id = "astrbot"

    def __init__(self, *, fail_delete: bool = False) -> None:
        self.fail_delete = fail_delete
        self.requests: list[tuple[str, str, object]] = []

    async def request_json(self, method, path, *, json_payload=None):
        self.requests.append((method, path, json_payload))
        if self.fail_delete:
            raise RuntimeError("delete failed")
        return {"ok": True}


class RuntimeTaskTests(unittest.TestCase):
    def test_temporary_runtime_session_id_is_prefixed_and_stable(self) -> None:
        self.assertEqual(temporary_runtime_session_id("abc"), "ipet-temp-abc")
        self.assertEqual(temporary_runtime_session_id("bad/session id"), "ipet-temp-bad-session-id")

    def test_cleanup_runtime_session_calls_runtime_delete_path(self) -> None:
        client = _FakeRuntimeClient()
        result = asyncio.run(cleanup_runtime_session(client, "ipet-temp-abc"))
        self.assertTrue(result["ok"])
        self.assertEqual(client.requests, [("DELETE", "/api/chat/topics/ipet-temp-abc", None)])

    def test_cleanup_runtime_session_reports_delete_failure(self) -> None:
        client = _FakeRuntimeClient(fail_delete=True)
        result = asyncio.run(cleanup_runtime_session(client, "ipet-temp-abc"))
        self.assertFalse(result["ok"])
        self.assertIn("delete failed", result["error"])
```

- [ ] **Step 2: Run runtime task tests and verify red**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_runtime_tasks -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.runtime_tasks'`.

- [ ] **Step 3: Implement runtime task helpers**

Create `backend/runtime_tasks.py`:

```python
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote


def normalize_runtime_turn_id(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("\\", "-").replace("/", "-")
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-_")
    return text or "turn"


def temporary_runtime_session_id(runtime_turn_id: Any) -> str:
    return f"ipet-temp-{normalize_runtime_turn_id(runtime_turn_id)}"


async def cleanup_runtime_session(client: Any, runtime_session_id: str) -> dict[str, Any]:
    session_id = str(runtime_session_id or "").strip()
    if not session_id:
        return {"ok": True, "skipped": True, "error": ""}
    target = quote(session_id, safe="")
    try:
        if hasattr(client, "delete_runtime_session"):
            payload = await client.delete_runtime_session(session_id)
        else:
            payload = await client.request_json("DELETE", f"/api/chat/topics/{target}")
        return {"ok": True, "payload": payload if isinstance(payload, dict) else {}, "error": ""}
    except Exception as exc:
        return {"ok": False, "payload": {}, "error": str(exc)}
```

Modify `backend/runtime_adapters.py` inside `AstrBotRuntimeAdapter`:

```python
    async def delete_runtime_session(self, session_id: str) -> dict[str, Any]:
        return await self._delete_session(str(session_id or "").strip())
```

Place the method near `request_json()` or `_delete_session()`. This keeps cleanup explicit for the harness while reusing existing Dashboard deletion safeguards.

- [ ] **Step 4: Run runtime task tests and targeted adapter tests**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_runtime_tasks tests.test_runtime_proxy.AstrBotTopicAdapterTests -v
```

Expected: PASS for runtime helper tests and existing AstrBot topic adapter tests.

- [ ] **Step 5: Commit Task 3**

Run:

```bash
git add backend/runtime_tasks.py backend/runtime_adapters.py tests/test_runtime_tasks.py
git commit -m "feat: add temporary runtime session helpers"
```

### Task 4: Memory Harness Core

**Files:**

- Create: `backend/memory_harness.py`
- Create: `tests/test_memory_harness.py`

- [ ] **Step 1: Write failing memory harness tests**

Create `tests/test_memory_harness.py`:

```python
from __future__ import annotations

import asyncio
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from backend.conversation_store import ConversationStore
from backend.ipet_memory_store import IpetMemoryStore
from backend.memory_harness import MemoryHarness, detect_save_policy


class _FakeRuntimeClient:
    runtime_id = "astrbot"

    def __init__(self, *, events=None, fail_delete: bool = False) -> None:
        self.events = events or [("token", {"delta": "好的"}), ("done", {"text": "好的"})]
        self.fail_delete = fail_delete
        self.stream_payloads: list[dict] = []
        self.delete_calls: list[str] = []

    async def stream_sse(self, path, payload):
        self.stream_payloads.append({"path": path, "payload": dict(payload)})
        for event, data in self.events:
            yield event, dict(data)

    async def delete_runtime_session(self, session_id):
        self.delete_calls.append(session_id)
        if self.fail_delete:
            raise RuntimeError("delete failed")
        return {"ok": True}


class MemoryHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parent / ".tmp_memory_harness" / uuid4().hex
        self.conversations = ConversationStore(self.root / "conversations")
        self.memories = IpetMemoryStore(self.root / "memory")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_detect_save_policy(self) -> None:
        self.assertEqual(detect_save_policy("这次不要保存", "persistent").scope, "turn")
        self.assertEqual(detect_save_policy("这个话题之后都别记录", "persistent").scope, "conversation")
        self.assertEqual(detect_save_policy("临时聊聊 Python", "persistent").memory_mode, "temporary")

    def test_persistent_turn_injects_memory_saves_and_cleans_runtime(self) -> None:
        conversation = self.conversations.create_conversation(conversation_id="conv-1", title="Demo")
        self.conversations.append_turn(
            conversation["conversation_id"],
            turn_id="old-turn",
            user_text="旧问题",
            assistant_text="旧答案",
            runtime="astrbot",
            runtime_session_id="ipet-temp-old",
            memory_mode="persistent",
            save_policy="save",
        )
        self.memories.write_memory(
            title="用户偏好：红茶",
            summary="用户喜欢红茶。",
            evidence="用户说喜欢红茶。",
            source_conversation_id="conv-1",
            source_turn_id="old-turn",
            tags=["preference"],
        )
        runtime = _FakeRuntimeClient()
        harness = MemoryHarness(conversation_store=self.conversations, memory_store=self.memories, runtime_client=runtime)

        events = asyncio.run(self._collect(harness.stream_chat({
            "session_id": "conv-1",
            "text": "请记住：我以后写测试偏好 unittest。",
            "chat_mode": "react",
            "memory_mode": "persistent",
        })))

        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["topic_id"], "conv-1")
        sent_text = runtime.stream_payloads[0]["payload"]["text"]
        self.assertIn("[Long-term Memory]", sent_text)
        self.assertIn("用户喜欢红茶", sent_text)
        self.assertIn("旧问题", sent_text)
        self.assertTrue(runtime.stream_payloads[0]["payload"]["session_id"].startswith("ipet-temp-"))
        self.assertEqual(runtime.delete_calls, [runtime.stream_payloads[0]["payload"]["session_id"]])
        detail = self.conversations.get_topic_detail("conv-1")
        self.assertEqual(detail["messages"][-2]["content"], "请记住：我以后写测试偏好 unittest。")
        self.assertTrue(self.memories.search("unittest"))

    def test_temporary_turn_does_not_save_business_content(self) -> None:
        runtime = _FakeRuntimeClient()
        harness = MemoryHarness(conversation_store=self.conversations, memory_store=self.memories, runtime_client=runtime)
        asyncio.run(self._collect(harness.stream_chat({
            "session_id": "conv-temp",
            "text": "临时问题",
            "chat_mode": "react",
            "memory_mode": "temporary",
        })))

        self.assertEqual(self.conversations.list_topics(), [])
        self.assertFalse((self.root / "memory" / "MEMORY.md").exists())
        self.assertEqual(len(runtime.delete_calls), 1)

    def test_no_save_turn_does_not_persist_or_extract_memory(self) -> None:
        runtime = _FakeRuntimeClient()
        harness = MemoryHarness(conversation_store=self.conversations, memory_store=self.memories, runtime_client=runtime)
        asyncio.run(self._collect(harness.stream_chat({
            "session_id": "conv-private",
            "text": "这个话题之后都别记录。请记住：我喜欢咖啡。",
            "chat_mode": "react",
            "memory_mode": "persistent",
        })))

        detail = self.conversations.get_topic_detail("conv-private")
        self.assertIsNotNone(detail)
        self.assertTrue(detail["meta"]["save_disabled"])
        self.assertEqual(detail["messages"], [])
        self.assertEqual(self.memories.search("咖啡"), [])

    def test_cleanup_failure_writes_cleanup_record_without_transcript(self) -> None:
        runtime = _FakeRuntimeClient(fail_delete=True)
        harness = MemoryHarness(conversation_store=self.conversations, memory_store=self.memories, runtime_client=runtime)
        events = asyncio.run(self._collect(harness.stream_chat({
            "session_id": "conv-1",
            "text": "你好",
            "chat_mode": "react",
            "memory_mode": "persistent",
        })))
        self.assertEqual(events[-1][0], "done")
        cleanup_files = list((self.root / "conversations" / "temp").glob("*.json"))
        self.assertEqual(len(cleanup_files), 1)
        self.assertNotIn("你好", cleanup_files[0].read_text(encoding="utf-8"))

    async def _collect(self, stream):
        return [(event, payload) async for event, payload in stream]
```

- [ ] **Step 2: Run harness tests and verify red**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_memory_harness -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.memory_harness'`.

- [ ] **Step 3: Implement `backend/memory_harness.py`**

Create `backend/memory_harness.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator
from uuid import uuid4

from .conversation_store import MEMORY_MODE_PERSISTENT, MEMORY_MODE_TEMPORARY, ConversationStore, normalize_conversation_id, normalize_memory_mode
from .ipet_memory_store import IpetMemoryStore, build_memory_candidates_from_turn
from .runtime_tasks import cleanup_runtime_session, temporary_runtime_session_id


TURN_NO_SAVE_MARKERS = ("这次不要保存", "本轮不要保存", "不要保存这次", "don't save this", "do not save this")
CONVERSATION_NO_SAVE_MARKERS = ("不保存此次对话", "这个话题之后都别记录", "本对话不要保存", "以后都别记录", "off the record")
TEMPORARY_MODE_MARKERS = ("临时聊聊", "临时对话", "temporary chat")
SKIP_RETRIEVAL_MARKERS = ("不参考记忆", "别用历史", "不要使用记忆", "do not use memory")


@dataclass(frozen=True)
class SavePolicy:
    memory_mode: str
    save_allowed: bool
    scope: str
    reason: str
    use_memory: bool = True


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(marker.casefold() in lowered for marker in markers)


def detect_save_policy(user_text: str, memory_mode: str) -> SavePolicy:
    mode = normalize_memory_mode(memory_mode)
    text = str(user_text or "")
    use_memory = not _contains_any(text, SKIP_RETRIEVAL_MARKERS)
    if mode == MEMORY_MODE_TEMPORARY or _contains_any(text, TEMPORARY_MODE_MARKERS):
        return SavePolicy(memory_mode=MEMORY_MODE_TEMPORARY, save_allowed=False, scope="turn", reason="temporary", use_memory=use_memory)
    if _contains_any(text, CONVERSATION_NO_SAVE_MARKERS):
        return SavePolicy(memory_mode=MEMORY_MODE_PERSISTENT, save_allowed=False, scope="conversation", reason="user_requested_no_save", use_memory=use_memory)
    if _contains_any(text, TURN_NO_SAVE_MARKERS):
        return SavePolicy(memory_mode=MEMORY_MODE_PERSISTENT, save_allowed=False, scope="turn", reason="user_requested_no_save", use_memory=use_memory)
    return SavePolicy(memory_mode=MEMORY_MODE_PERSISTENT, save_allowed=True, scope="none", reason="", use_memory=use_memory)


class MemoryHarness:
    def __init__(self, *, conversation_store: ConversationStore, memory_store: IpetMemoryStore, runtime_client: Any) -> None:
        self.conversation_store = conversation_store
        self.memory_store = memory_store
        self.runtime_client = runtime_client

    async def stream_chat(self, payload: dict[str, Any]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        original_text = str(payload.get("original_text") or payload.get("text") or "").strip()
        runtime_input_text = str(payload.get("text") or original_text).strip()
        conversation_id = normalize_conversation_id(payload.get("session_id") or "default")
        policy = detect_save_policy(original_text, str(payload.get("memory_mode") or MEMORY_MODE_PERSISTENT))
        if policy.scope == "conversation":
            self.conversation_store.disable_saving(conversation_id, reason=policy.reason)

        runtime_turn_id = uuid4().hex
        runtime_session_id = temporary_runtime_session_id(runtime_turn_id)
        runtime_payload = dict(payload)
        runtime_payload["session_id"] = runtime_session_id
        runtime_payload["text"] = self.build_runtime_text(
            conversation_id=conversation_id,
            user_text=runtime_input_text,
            policy=policy,
        )

        full_answer = ""
        completed = False
        try:
            yield "meta", {
                "runtime": getattr(self.runtime_client, "runtime_id", "runtime"),
                "topic_id": conversation_id,
                "session_id": conversation_id,
                "runtime_session_id": runtime_session_id,
                "memory_mode": policy.memory_mode,
                "chat_mode": str(payload.get("chat_mode") or "react"),
            }
            async for event, data in self.runtime_client.stream_sse("/api/chat/stream", runtime_payload):
                next_data = dict(data or {})
                if event == "token":
                    full_answer += str(next_data.get("delta") or "")
                    yield event, next_data
                elif event == "done":
                    completed = True
                    full_answer = str(next_data.get("text") or full_answer)
                    if policy.save_allowed:
                        save_result = self.conversation_store.append_turn(
                            conversation_id,
                            turn_id=runtime_turn_id,
                            user_text=original_text,
                            assistant_text=full_answer,
                            runtime=getattr(self.runtime_client, "runtime_id", "runtime"),
                            runtime_session_id=runtime_session_id,
                            memory_mode=policy.memory_mode,
                            save_policy="save",
                            metadata={"chat_mode": str(payload.get("chat_mode") or "react")},
                        )
                        self._extract_memory_if_needed(conversation_id, runtime_turn_id, original_text, full_answer, save_result)
                    next_data["topic_id"] = conversation_id
                    next_data["session_id"] = conversation_id
                    next_data["memory_mode"] = policy.memory_mode
                    yield "done", next_data
                elif event == "meta":
                    next_data["topic_id"] = conversation_id
                    next_data["session_id"] = conversation_id
                    next_data["memory_mode"] = policy.memory_mode
                    yield event, next_data
                else:
                    yield event, next_data
        finally:
            cleanup = await cleanup_runtime_session(self.runtime_client, runtime_session_id)
            if cleanup.get("ok"):
                self.conversation_store.clear_cleanup_record(runtime_turn_id)
            else:
                self.conversation_store.write_cleanup_record(
                    runtime_turn_id=runtime_turn_id,
                    runtime=getattr(self.runtime_client, "runtime_id", "runtime"),
                    runtime_session_id=runtime_session_id,
                    error=str(cleanup.get("error") or "runtime cleanup failed"),
                )
            if not completed:
                return

    def build_runtime_text(self, *, conversation_id: str, user_text: str, policy: SavePolicy) -> str:
        sections: list[str] = []
        if policy.use_memory and policy.memory_mode != MEMORY_MODE_TEMPORARY:
            memories = self.memory_store.search(user_text, limit=5)
            if memories:
                lines = ["[Long-term Memory]"]
                for item in memories:
                    lines.append(f"- {item['title']}: {item['summary']}")
                sections.append("\n".join(lines))
        context = self.conversation_store.load_context(conversation_id)
        if context is not None and policy.memory_mode != MEMORY_MODE_TEMPORARY:
            if context.major_summary:
                sections.append(f"[Conversation Major Summary]\n{context.major_summary}")
            if context.rolling_summary:
                sections.append(f"[Conversation Rolling Summary]\n{context.rolling_summary}")
            if context.recent_messages:
                lines = ["[Recent Conversation]"]
                for item in context.recent_messages[-12:]:
                    lines.append(f"{item['role']}: {item['content']}")
                sections.append("\n".join(lines))
        sections.append(f"用户消息：{user_text}")
        return "\n\n".join(section for section in sections if str(section or "").strip())

    def _extract_memory_if_needed(self, conversation_id: str, runtime_turn_id: str, user_text: str, assistant_text: str, save_result: dict[str, Any]) -> None:
        if not save_result.get("saved"):
            return
        candidates = build_memory_candidates_from_turn(
            conversation_id=conversation_id,
            turn_id=runtime_turn_id,
            user_text=user_text,
            assistant_text=assistant_text,
        )
        for candidate in candidates:
            self.memory_store.write_memory(**candidate)
        meta = save_result.get("meta") if isinstance(save_result.get("meta"), dict) else {}
        self.conversation_store.update_memory_extracted_turn(conversation_id, int(meta.get("assistant_turn_count") or 0))
```

- [ ] **Step 4: Run harness tests and verify green**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_memory_harness -v
```

Expected: PASS for 5 tests.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
git add backend/memory_harness.py tests/test_memory_harness.py
git commit -m "feat: add ipet memory harness core"
```

### Task 5: Backend API Integration

**Files:**

- Modify: `backend/models.py`
- Modify: `backend/app.py`
- Create: `tests/test_memory_harness_api.py`
- Modify: `tests/test_runtime_proxy.py`

- [ ] **Step 1: Write failing API integration tests**

Create `tests/test_memory_harness_api.py`:

```python
from __future__ import annotations

import asyncio
import shutil
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

from fastapi.testclient import TestClient

import backend.app as backend_app
from backend.conversation_store import ConversationStore
from backend.ipet_memory_store import IpetMemoryStore


class _FakeRuntimeClient:
    runtime_id = "astrbot"

    def __init__(self) -> None:
        self.requests = []
        self.deleted = []

    async def stream_sse(self, path, payload):
        self.requests.append(("POST", path, dict(payload)))
        yield "token", {"delta": "你好"}
        yield "done", {"text": "你好"}

    async def delete_runtime_session(self, session_id):
        self.deleted.append(session_id)
        return {"ok": True}

    async def request_json(self, method, path, *, json_payload=None):
        self.requests.append((method, path, json_payload))
        raise AssertionError("topic endpoints must not call runtime request_json")


class MemoryHarnessApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(__file__).resolve().parent / ".tmp_memory_harness_api" / uuid4().hex
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self.conversations = ConversationStore(self.tmp_root / "ipet_conversations")
        self.memories = IpetMemoryStore(self.tmp_root / "ipet_memory")
        self.client = TestClient(backend_app.app)

    def tearDown(self) -> None:
        self.client.close()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _patch_stores_and_runtime(self, runtime):
        return mock.patch.multiple(
            backend_app,
            _get_conversation_store=mock.Mock(return_value=self.conversations),
            _get_ipet_memory_store=mock.Mock(return_value=self.memories),
            _get_runtime_client=mock.Mock(return_value=runtime),
            _load_settings_config=mock.Mock(return_value={"vision": {"enabled": False}, "chat": {"backend_url": "http://127.0.0.1:8008"}}),
        )

    def test_chat_stream_persistent_saves_locally_and_deletes_runtime_session(self) -> None:
        runtime = _FakeRuntimeClient()
        with self._patch_stores_and_runtime(runtime):
            resp = self.client.post("/api/chat/stream", json={"session_id": "conv-1", "text": "你好", "expression_mode": False})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("event: done", resp.text)
        self.assertEqual(len(runtime.deleted), 1)
        detail = self.conversations.get_topic_detail("conv-1")
        self.assertIsNotNone(detail)
        self.assertEqual([item["content"] for item in detail["messages"]], ["你好", "你好"])

    def test_chat_stream_temporary_does_not_write_local_history(self) -> None:
        runtime = _FakeRuntimeClient()
        with self._patch_stores_and_runtime(runtime):
            resp = self.client.post("/api/chat/stream", json={"session_id": "conv-1", "text": "临时聊聊", "memory_mode": "temporary", "expression_mode": False})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.conversations.list_topics(), [])
        self.assertFalse((self.tmp_root / "ipet_memory" / "MEMORY.md").exists())

    def test_topic_endpoints_use_local_store_not_runtime_sessions(self) -> None:
        runtime = _FakeRuntimeClient()
        self.conversations.create_conversation(conversation_id="conv-1", title="本地会话")
        self.conversations.append_turn(
            "conv-1",
            turn_id="turn-1",
            user_text="问",
            assistant_text="答",
            runtime="astrbot",
            runtime_session_id="ipet-temp-turn-1",
            memory_mode="persistent",
            save_policy="save",
        )
        with self._patch_stores_and_runtime(runtime):
            list_resp = self.client.get("/api/chat/topics")
            detail_resp = self.client.get("/api/chat/topics/conv-1")
            delete_resp = self.client.delete("/api/chat/topics/conv-1")
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(list_resp.json()["topics"][0]["runtime"], "ipet")
        self.assertEqual(detail_resp.json()["messages"][0]["content"], "问")
        self.assertEqual(delete_resp.json()["deleted_topic_id"], "conv-1")
        self.assertFalse(any(request[0] == "GET" for request in runtime.requests))
```

Modify `tests/test_runtime_proxy.py` by replacing `test_astrbot_topics_proxy_uses_active_runtime` with a new expectation:

```python
    def test_chat_topics_use_ipet_local_store(self) -> None:
        fake = _FakeRuntimeClient(payload={"ok": True, "runtime": "astrbot", "topics": [{"topic_id": "qq"}]})
        with mock.patch.object(backend_app, "_get_runtime_client", return_value=fake):
            resp = self.client.get("/api/chat/topics")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["runtime"], "ipet")
        self.assertEqual(fake.requests, [])
```

- [ ] **Step 2: Run API tests and verify red**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_memory_harness_api -v
```

Expected: FAIL because `_get_conversation_store` and `_get_ipet_memory_store` do not exist or `/api/chat/stream` still bypasses `MemoryHarness`.

- [ ] **Step 3: Add `memory_mode` to `ChatStreamRequest`**

Modify `backend/models.py`:

```python
class ChatStreamRequest(BaseModel):
    session_id: str = Field(default="default")
    text: str
    model: str = Field(default="gpt-5.4")
    system_prompt: str = Field(default="")
    expression_mode: bool = Field(default=True)
    expression_output_format: str = Field(default="ndjson_v1")
    available_expressions: list[str] = Field(default_factory=list)
    tools_enabled: bool = Field(default=True)
    tool_mode: str = Field(default="mcp_local_phase2")
    react_enabled: bool = Field(default=True)
    react_visibility: str = Field(default="inline")
    max_reasoning_steps: int = Field(default=10, ge=1)
    chat_mode: str = Field(default="react")
    skill_ids: list[str] = Field(default_factory=list)
    memory_mode: str = Field(default="persistent")
```

- [ ] **Step 4: Wire stores and harness in `backend/app.py`**

Add imports near existing backend imports:

```python
from .conversation_store import ConversationStore
from .ipet_memory_store import IpetMemoryStore
from .memory_harness import MemoryHarness
```

Add globals near `_CHAT_TOPIC_STORE`:

```python
_CONVERSATION_STORE: ConversationStore | None = None
_IPET_MEMORY_STORE: IpetMemoryStore | None = None
```

Add store getters near `_get_chat_topic_store`:

```python
def _get_conversation_store(force_reload: bool = False) -> ConversationStore:
    global _CONVERSATION_STORE
    if force_reload or _CONVERSATION_STORE is None:
        _CONVERSATION_STORE = ConversationStore(ROOT_DIR / "data" / "ipet_conversations")
    return _CONVERSATION_STORE


def _get_ipet_memory_store(force_reload: bool = False) -> IpetMemoryStore:
    global _IPET_MEMORY_STORE
    if force_reload or _IPET_MEMORY_STORE is None:
        _IPET_MEMORY_STORE = IpetMemoryStore(ROOT_DIR / "data" / "ipet_memory")
    return _IPET_MEMORY_STORE
```

Update `_reset_agent_graph_runtime()` to clear the new globals:

```python
global _AGENT_GRAPH_RUNTIME, _SKILL_MANAGER, _CHAT_TOPIC_STORE, _CONVERSATION_STORE, _IPET_MEMORY_STORE, _ASR_SERVICE, _ASR_WARMUP_TASK
_CONVERSATION_STORE = None
_IPET_MEMORY_STORE = None
```

Modify `_chat_stream_via_runtime()` after vision payload construction:

```python
    original_text = req.text
    payload = _with_active_runtime_vision_frame(payload, active_result)
    payload = _with_ipet_visual_evidence_payload(payload, req, settings_config, forced_context=active_context)
    payload["original_text"] = original_text
    harness = MemoryHarness(
        conversation_store=_get_conversation_store(),
        memory_store=_get_ipet_memory_store(),
        runtime_client=client,
    )
```

Replace `runtime_events = client.stream_sse("/api/chat/stream", payload)` with:

```python
            runtime_events = harness.stream_chat(payload)
```

Keep the existing `realtime_stream_events(...)` wrapper around `runtime_events`.

Modify topic endpoints:

```python
@app.post("/api/chat/topics")
async def create_chat_topic(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    topic = _get_conversation_store().create_conversation(
        conversation_id=str(data.get("topic_id") or data.get("session_id") or ""),
        title=str(data.get("title") or DEFAULT_TOPIC_TITLE),
        memory_mode=str(data.get("memory_mode") or "persistent"),
        persisted=str(data.get("memory_mode") or "persistent") != "temporary",
    )
    return {"ok": True, "runtime": "ipet", "topic_id": topic["topic_id"], "persisted": topic["persisted"], "topic": topic}


@app.get("/api/chat/topics")
async def list_chat_topics() -> dict[str, Any]:
    return {"ok": True, "runtime": "ipet", "enabled": True, "topics": _get_conversation_store().list_topics()}


@app.get("/api/chat/topics/{topic_id}")
async def get_chat_topic(topic_id: str) -> dict[str, Any]:
    detail = _get_conversation_store().get_topic_detail(topic_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="chat topic not found")
    return detail


@app.delete("/api/chat/topics/{topic_id}")
@app.post("/api/chat/topics/{topic_id}/delete")
async def delete_chat_topic(topic_id: str) -> dict[str, Any]:
    return _get_conversation_store().delete_conversation(topic_id)
```

- [ ] **Step 5: Run API and runtime proxy tests**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_memory_harness_api tests.test_runtime_proxy -v
```

Expected: PASS for new API tests and updated runtime proxy tests.

- [ ] **Step 6: Commit Task 5**

Run:

```bash
git add backend/models.py backend/app.py tests/test_memory_harness_api.py tests/test_runtime_proxy.py
git commit -m "feat: route chat history through ipet memory harness"
```

### Task 6: Frontend Temporary Chat Mode

**Files:**

- Modify: `index.html`
- Modify: `tests/test_chat_modes_ui.py`

- [ ] **Step 1: Write failing UI contract tests**

Modify `tests/test_chat_modes_ui.py`:

```python
    def test_index_contains_memory_mode_controls_and_payload(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('data-memory-mode="persistent"', source)
        self.assertIn('data-memory-mode="temporary"', source)
        self.assertIn("let currentMemoryMode = \"persistent\"", source)
        self.assertIn("function normalizeMemoryMode", source)
        self.assertIn("memory_mode: currentMemoryMode", source)
        self.assertIn("if (currentMemoryMode !== \"temporary\")", source)
```

- [ ] **Step 2: Run UI tests and verify red**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_chat_modes_ui -v
```

Expected: FAIL because the temporary memory mode UI does not exist yet.

- [ ] **Step 3: Add memory mode controls to `index.html`**

In the `#chat-mode-bar` block, add two buttons after the existing mode buttons:

```html
      <button class="chat-mode-btn is-active" data-memory-mode="persistent">保存</button>
      <button class="chat-mode-btn" data-memory-mode="temporary">临时</button>
```

Near `chatModeButtons`, add:

```javascript
      const memoryModeButtons = Array.from(document.querySelectorAll("[data-memory-mode]"));
```

Near `let currentChatMode = "react";`, add:

```javascript
      let currentMemoryMode = "persistent";
```

Add these functions near `normalizeChatMode` and `setChatMode`:

```javascript
      function normalizeMemoryMode(value) {
        const mode = String(value || "").trim().toLowerCase();
        return mode === "temporary" ? "temporary" : "persistent";
      }

      function setMemoryMode(mode) {
        currentMemoryMode = normalizeMemoryMode(mode);
        applyMemoryModeUI();
        renderTopicHistoryList();
        updateShellButtons();
      }

      function applyMemoryModeUI() {
        for (const button of memoryModeButtons) {
          const mode = normalizeMemoryMode(button.dataset.memoryMode);
          const active = mode === currentMemoryMode;
          button.classList.toggle("is-active", active);
          button.setAttribute("aria-pressed", String(active));
        }
        syncTopicIndicator();
      }
```

Modify `currentTopicLabel()`:

```javascript
      function currentTopicLabel() {
        if (currentMemoryMode === "temporary") {
          return "临时聊天";
        }
        return currentTopicMeta?.title || currentTopicId || "未创建";
      }
```

Modify `renderTopicHistoryList()` before the empty-list branch:

```javascript
        if (currentMemoryMode === "temporary") {
          setChatHistoryStatus("临时聊天不会写入历史。");
          syncTopicIndicator();
          return;
        }
```

Modify `streamChat(text)`:

```javascript
        if (currentMemoryMode !== "temporary") {
          await ensureTopicReady();
        }
```

Replace the existing unconditional `await ensureTopicReady();` with the block above.

In the chat payload add:

```javascript
              memory_mode: currentMemoryMode,
```

Modify the `done` event branch:

```javascript
              if (payload.memory_mode) {
                currentMemoryMode = normalizeMemoryMode(payload.memory_mode);
                applyMemoryModeUI();
              }
              if (topicHistoryEnabled() && currentMemoryMode !== "temporary") {
                await loadTopicHistory(true);
              }
```

Replace the existing `if (topicHistoryEnabled()) { await loadTopicHistory(true); }` block.

Near the bottom event bindings, add:

```javascript
        for (const button of memoryModeButtons) {
          button.addEventListener("click", () => setMemoryMode(button.dataset.memoryMode));
        }
```

- [ ] **Step 4: Run UI tests and verify green**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_chat_modes_ui -v
```

Expected: PASS for UI contract tests.

- [ ] **Step 5: Commit Task 6**

Run:

```bash
git add index.html tests/test_chat_modes_ui.py
git commit -m "feat: add temporary chat memory mode"
```

### Task 7: Workflow Documentation

**Files:**

- Modify: `docs/WORKFLOW.md`
- Test: `tests/test_memory_harness_api.py`

- [ ] **Step 1: Add docs assertion to API tests**

Add this test to `tests/test_memory_harness_api.py`:

```python
    def test_workflow_documents_ipet_memory_harness(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / "docs" / "WORKFLOW.md").read_text(encoding="utf-8")
        self.assertIn("Ipet owns conversation history", workflow)
        self.assertIn("AstrBot owns only temporary task execution", workflow)
        self.assertIn("data/ipet_conversations/", workflow)
        self.assertIn("data/ipet_memory/", workflow)
```

- [ ] **Step 2: Run docs assertion and verify red**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_memory_harness_api.MemoryHarnessApiTests.test_workflow_documents_ipet_memory_harness -v
```

Expected: FAIL because `docs/WORKFLOW.md` still describes AstrBot session history as the primary history drawer path.

- [ ] **Step 3: Update `docs/WORKFLOW.md`**

Replace the "AstrBot Session History" section with a new "Ipet Conversation And Memory Harness" section containing these exact statements:

```markdown
## Ipet Conversation And Memory Harness

Ipet owns conversation history and long-term memory. The active runtime is a task executor, not the durable chat data layer. In AstrBot mode, AstrBot owns only temporary task execution for Ipet-initiated desktop chat turns.

Persistent Ipet chat state is stored locally:

- `data/ipet_conversations/` stores the conversation index, readable transcripts, JSONL turns, summaries, and runtime cleanup records.
- `data/ipet_memory/` stores `MEMORY.md` plus Markdown long-term memory files.

`POST /api/chat/stream` builds a local harness context from relevant long-term memories, conversation summaries, recent turns, local vision evidence, and the current user message. The AstrBot adapter receives one temporary `ipet-temp-*` session per turn. Ipet deletes that temporary AstrBot session after the stream completes or fails.

`GET /api/chat/topics`, `GET /api/chat/topics/{topic_id}`, `POST /api/chat/topics`, and `DELETE /api/chat/topics/{topic_id}` read and write Ipet local conversations. They do not browse AstrBot chat databases.

Temporary chat mode keeps the same runtime features but does not write business conversation content or long-term memory. User no-save intent also prevents durable writes for the affected turn or conversation.
```

Also update "Local State And Cleanup" to replace `data/chat_topics/` with both:

```markdown
- `data/ipet_conversations/` for Ipet-owned conversation history and temporary runtime cleanup records
- `data/ipet_memory/` for local long-term memory
- `data/chat_topics/` for ignored legacy compatibility topic history during migration
```

- [ ] **Step 4: Run docs assertion and workflow-adjacent tests**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_memory_harness_api tests.test_chat_modes_ui -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 7**

Run:

```bash
git add docs/WORKFLOW.md tests/test_memory_harness_api.py
git commit -m "docs: document ipet memory harness workflow"
```

### Task 8: Targeted Regression And Cleanup

**Files:**

- Modify only files needed to fix failures revealed by the commands below.

- [ ] **Step 1: Run the focused memory harness suite**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_conversation_store tests.test_ipet_memory_store tests.test_runtime_tasks tests.test_memory_harness tests.test_memory_harness_api tests.test_chat_modes_ui -v
```

Expected: PASS.

- [ ] **Step 2: Run settings/UI/runtime contract slices**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_runtime_proxy tests.test_settings_mcp_draft tests.test_settings_file_allowlist tests.test_chat_modes_ui -v
```

Expected: PASS. If `tests.test_runtime_proxy` still contains obsolete AstrBot-history expectations, update those assertions to expect local `runtime="ipet"` topic ownership and no runtime `request_json` calls for topic list/detail/delete.

- [ ] **Step 3: Run topic history compatibility tests**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_chat_topics tests.test_chat_topics_api -v
```

Expected: PASS. Keep low-level `TopicStore` tests passing because the legacy store remains as migration compatibility. For API tests that explicitly cover old local endpoint behavior, either update them to the new `ConversationStore` API path or keep the existing skip markers if they document retired runtime-proxy behavior.

- [ ] **Step 4: Run full regression**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest discover -s tests -p "test*.py" -v
```

Expected: PASS. If a failure appears outside the memory-harness surface, inspect whether the new local topic endpoints changed a shared contract; fix the narrow contract mismatch rather than reverting the harness.

- [ ] **Step 5: Inspect git status**

Run:

```bash
git status --short
```

Expected: only intentional files from the tasks above are modified.

- [ ] **Step 6: Commit final regression fixes**

If Step 4 required fixes after Task 7, commit them:

```bash
git add backend tests index.html docs/WORKFLOW.md
git commit -m "test: stabilize ipet memory harness regression"
```

If Step 4 passed without additional edits, skip this commit.

## Self-Review Checklist

- Spec coverage: Tasks 1-2 cover local file conversation and memory stores; Tasks 3-5 cover temporary AstrBot execution and API routing; Task 6 covers temporary chat UI; Task 7 covers workflow docs; Task 8 covers regression.
- Completeness scan: this plan uses concrete file paths, commands, expected red/green outcomes, and implementation snippets for each production module.
- Type consistency: `memory_mode`, `conversation_id`, `topic_id`, `runtime_turn_id`, and `runtime_session_id` are named consistently across store, harness, API, and UI tasks.
