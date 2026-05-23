from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


SENSITIVE_KEYWORD_PATTERN = r"(?:password|private[_-]?key|api[_-]?key|token|secret|passwd)"

SECRET_PATTERNS = [
    re.compile(rf"\b{SENSITIVE_KEYWORD_PATTERN}\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(rf"\b{SENSITIVE_KEYWORD_PATTERN}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
]

DURABLE_KEYWORDS = [
    "记住",
    "以后",
    "长期",
    "偏好",
    "喜欢",
    "不喜欢",
    "习惯",
    "常用",
    "决定",
    "计划",
    "目标",
    "约束",
    "remember",
    "prefer",
    "preference",
    "like",
    "dislike",
    "habit",
    "plan",
    "decide",
    "goal",
    "always",
    "never",
]

MAX_TITLE_CHARS = 48
MAX_SUMMARY_CHARS = 220
MAX_EVIDENCE_CHARS = 500


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def compact_text(value: Any, limit: int = MAX_SUMMARY_CHARS) -> str:
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


def slugify(value: Any, fallback: str = "memory") -> str:
    text = str(value or "").strip().lower()
    text = text.replace("\\", "-").replace("/", "-")
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    return text or fallback


def contains_secret(value: Any) -> bool:
    text = str(value or "")
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def _strip_memory_prefix(value: Any) -> str:
    text = str(value or "")
    positions = [pos for pos in (text.find("："), text.find(":")) if pos >= 0]
    if not positions:
        return text
    return text[min(positions) + 1 :]


def _normalize_for_duplicate(value: Any) -> str:
    return re.sub(r"\W+", "", str(value or "").casefold())


def _json_meta(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _parse_json_meta(value: str, default: Any = "") -> Any:
    try:
        return json.loads(value)
    except Exception:
        return default


def _candidate_tags(text: str) -> list[str]:
    lowered = text.casefold()
    tags: list[str] = []
    if any(word in lowered for word in ["偏好", "喜欢", "不喜欢", "prefer", "preference", "like", "dislike"]):
        tags.append("preference")
    if any(word in lowered for word in ["计划", "目标", "以后", "约束", "plan", "goal", "always", "never"]):
        tags.append("plan")
    if any(word in lowered for word in ["决定", "decide"]):
        tags.append("decision")
    return tags or ["memory"]


def _candidate_title(tags: list[str], summary: str) -> str:
    snippet = compact_text(summary, 34)
    if "preference" in tags:
        prefix = "用户偏好"
    elif "plan" in tags:
        prefix = "用户约束与计划"
    elif "decision" in tags:
        prefix = "用户决定"
    else:
        prefix = "长期记忆"
    return compact_text(f"{prefix}：{snippet}", MAX_TITLE_CHARS)


def build_memory_candidates_from_turn(
    *,
    conversation_id: str,
    turn_id: str,
    user_text: str,
    assistant_text: str = "",
) -> list[dict[str, Any]]:
    source_text = compact_text(_strip_memory_prefix(user_text), MAX_EVIDENCE_CHARS)
    combined_text = f"{user_text}\n{assistant_text}"
    if not source_text or contains_secret(combined_text):
        return []

    lowered = source_text.casefold()
    if not any(keyword.casefold() in lowered for keyword in DURABLE_KEYWORDS):
        return []

    tags = _candidate_tags(source_text)
    title = _candidate_title(tags, source_text)

    return [
        {
            "title": title,
            "summary": source_text,
            "evidence": source_text,
            "source_conversation_id": str(conversation_id or ""),
            "source_turn_id": str(turn_id or ""),
            "tags": tags,
        }
    ]


class IpetMemoryStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.memories_dir = self.root_dir / "memories"
        self.index_path = self.root_dir / "MEMORY.md"
        self._lock = threading.RLock()

    def write_memory(
        self,
        *,
        title: str,
        summary: str,
        evidence: str = "",
        source_conversation_id: str = "",
        source_turn_id: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        clean_title = compact_text(title, MAX_TITLE_CHARS) or "长期记忆"
        clean_summary = compact_text(summary, MAX_SUMMARY_CHARS)
        clean_evidence = compact_text(evidence, MAX_EVIDENCE_CHARS)
        clean_tags = [compact_text(tag, 32) for tag in (tags or []) if compact_text(tag, 32)]
        if not clean_summary or contains_secret(f"{clean_title}\n{clean_summary}\n{clean_evidence}"):
            return {
                "ok": True,
                "saved": False,
                "duplicate": False,
                "reason": "empty_or_secret",
                "memory": None,
            }

        with self._lock:
            self.memories_dir.mkdir(parents=True, exist_ok=True)
            existing = self._find_duplicate(clean_title, clean_summary)
            if existing is not None:
                return {"ok": True, "saved": False, "duplicate": True, "memory": existing}

            memory_id = self._memory_id(clean_title, clean_summary)
            path = self.memories_dir / f"{memory_id}.md"
            updated_at = now_text()
            record = {
                "id": memory_id,
                "title": clean_title,
                "summary": clean_summary,
                "path": str(path),
                "tags": clean_tags,
                "source_conversation_id": str(source_conversation_id or ""),
                "source_turn_id": str(source_turn_id or ""),
                "updated_at": updated_at,
            }
            path.write_text(self._memory_markdown(record, clean_evidence), encoding="utf-8")
            self._write_index()
            return {"ok": True, "saved": True, "duplicate": False, "memory": record}

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        needle = compact_text(query, 120).casefold()
        safe_limit = max(0, int(limit or 0))
        if safe_limit <= 0:
            return []

        with self._lock:
            records = self._records()

        if not needle:
            return records[:safe_limit]

        terms = [needle]
        terms.extend(term for term in re.split(r"\s+", needle) if term and term not in terms)
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for record in records:
            title = str(record.get("title") or "").casefold()
            summary = str(record.get("summary") or "").casefold()
            tags = " ".join(str(tag) for tag in record.get("tags") or []).casefold()
            haystack = f"{title} {summary} {tags}"
            score = 0
            for term in terms:
                if term in title:
                    score += 5
                if term in summary:
                    score += 3
                if term in tags:
                    score += 2
                if term in haystack:
                    score += 1
            if score > 0:
                scored.append((score, str(record.get("updated_at") or ""), record))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [record for _, _, record in scored[:safe_limit]]

    def _record_from_path(self, path: Path) -> dict[str, Any] | None:
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except Exception:
            return None
        if not lines or lines[0].strip() != "---":
            return None

        meta: dict[str, Any] = {}
        for line in lines[1:]:
            if line.strip() == "---":
                break
            key, sep, value = line.partition(":")
            if not sep:
                continue
            meta[key.strip()] = _parse_json_meta(value.strip(), "")

        memory_id = str(meta.get("id") or path.stem)
        tags = meta.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        return {
            "id": memory_id,
            "title": str(meta.get("title") or ""),
            "summary": str(meta.get("summary") or ""),
            "path": str(path),
            "tags": [str(tag) for tag in tags],
            "source_conversation_id": str(meta.get("source_conversation_id") or ""),
            "source_turn_id": str(meta.get("source_turn_id") or ""),
            "updated_at": str(meta.get("updated_at") or ""),
        }

    def _write_index(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        records = self._records()
        lines = ["# Ipet Long-Term Memory", ""]
        if not records:
            lines.append("_No memories yet._")
        for record in records:
            rel_path = Path(str(record["path"])).relative_to(self.root_dir)
            tags = ", ".join(record.get("tags") or [])
            suffix = f" [{tags}]" if tags else ""
            lines.append(f"- [{record['title']}]({rel_path.as_posix()}){suffix}")
            lines.append(f"  - {record['summary']}")
        self.index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def _records(self) -> list[dict[str, Any]]:
        if not self.memories_dir.exists():
            return []
        records = []
        for path in sorted(self.memories_dir.glob("*.md")):
            record = self._record_from_path(path)
            if record is not None:
                records.append(record)
        records.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return records

    def _find_duplicate(self, title: str, summary: str) -> dict[str, Any] | None:
        normalized_title = _normalize_for_duplicate(title)
        normalized_summary = _normalize_for_duplicate(summary)
        for record in self._records():
            existing_title = _normalize_for_duplicate(record.get("title"))
            existing_summary = _normalize_for_duplicate(record.get("summary"))
            if normalized_summary and normalized_summary == existing_summary:
                return record
            if (
                normalized_title
                and normalized_title == existing_title
                and normalized_summary
                and normalized_summary == existing_summary
            ):
                return record
        return None

    def _memory_id(self, title: str, summary: str) -> str:
        digest = hashlib.sha1(f"{title}\n{summary}".encode("utf-8")).hexdigest()[:10]
        return f"{slugify(title)}-{digest}"

    def _memory_markdown(self, record: dict[str, Any], evidence: str) -> str:
        meta_lines = [
            "---",
            f"id: {_json_meta(record['id'])}",
            f"title: {_json_meta(record['title'])}",
            f"summary: {_json_meta(record['summary'])}",
            f"tags: {_json_meta(record.get('tags') or [])}",
            f"source_conversation_id: {_json_meta(record.get('source_conversation_id') or '')}",
            f"source_turn_id: {_json_meta(record.get('source_turn_id') or '')}",
            f"updated_at: {_json_meta(record['updated_at'])}",
            "---",
            "",
            f"# {record['title']}",
            "",
            "## Summary",
            "",
            record["summary"],
            "",
            "## Evidence",
            "",
            evidence or record["summary"],
            "",
        ]
        return "\n".join(meta_lines)
