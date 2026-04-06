from __future__ import annotations

import json
import re
import sys
from typing import Any


WHITESPACE_RE = re.compile(r"\s+")


def _error(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))


def _load_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    return payload


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return WHITESPACE_RE.sub(" ", text)


def _split_text_items(raw_text: str, max_items: int) -> list[str]:
    chunks = [segment.strip() for segment in re.split(r"[\n\r]+|(?<=[.!?])\s+", raw_text) if segment.strip()]
    items: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        normalized = _clean_text(chunk)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        items.append(normalized[:160])
        if len(items) >= max_items:
            break
    return items


def _structured_items(value: Any, max_items: int) -> list[str]:
    items: list[str] = []

    def append_item(text: str) -> None:
        normalized = _clean_text(text)
        if normalized and normalized not in items:
            items.append(normalized[:160])

    if isinstance(value, dict):
        for key, raw in value.items():
            if len(items) >= max_items:
                break
            if isinstance(raw, (dict, list)):
                rendered = json.dumps(raw, ensure_ascii=False)
            else:
                rendered = str(raw)
            append_item(f"{key}: {rendered}")
    elif isinstance(value, list):
        for raw in value:
            if len(items) >= max_items:
                break
            if isinstance(raw, (dict, list)):
                append_item(json.dumps(raw, ensure_ascii=False))
            else:
                append_item(str(raw))
    return items[:max_items]


def _build_summary(raw_text: str, structured_data: Any, items: list[str]) -> str:
    sources: list[str] = []
    if raw_text:
        sources.append("raw text")
    if structured_data is not None:
        sources.append("structured data")
    source_text = " and ".join(sources) if sources else "browser output"
    return f"Compressed {source_text} into {len(items)} short item(s)."


def main() -> None:
    try:
        payload = _load_payload()
        raw_text = _clean_text(payload.get("raw_text"))
        structured_data = payload.get("structured_data")
        max_items = int(payload.get("max_items", 5) or 5)
        max_items = max(1, min(12, max_items))
        if not raw_text and structured_data is None:
            raise ValueError("raw_text or structured_data is required")

        items: list[str] = []
        if raw_text:
            items.extend(_split_text_items(raw_text, max_items))
        if len(items) < max_items and structured_data is not None:
            for item in _structured_items(structured_data, max_items):
                if item not in items:
                    items.append(item)
                if len(items) >= max_items:
                    break

        raw_preview_source = raw_text or json.dumps(structured_data, ensure_ascii=False)
        raw_preview = raw_preview_source[:280]
        result = {
            "summary": _build_summary(raw_text, structured_data, items),
            "items": items[:max_items],
            "raw_preview": raw_preview,
        }
    except Exception as exc:
        _error(str(exc))
        return

    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
