from __future__ import annotations

import json
import re
from typing import Any

from brain.decisions import BrainDecision


def _without_inline_media(value: Any) -> tuple[Any, int]:
    omitted = 0

    def scrub(item: Any) -> Any:
        nonlocal omitted
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for key, child in item.items():
                key_text = str(key)
                if key_text in {
                    "data_url",
                    "image_data_url",
                    "screenshot_data_url",
                }:
                    omitted += 1
                    continue
                result[key_text] = scrub(child)
            return result
        if isinstance(item, list):
            return [scrub(child) for child in item]
        if isinstance(item, tuple):
            return [scrub(child) for child in item]
        if (
            isinstance(item, str)
            and item.casefold().startswith("data:")
            and ";base64," in item[:160].casefold()
        ):
            omitted += 1
            return "[inline media omitted]"
        return item

    return scrub(value), omitted


def sse(event: str, data: dict[str, Any]) -> str:
    payload: dict[str, Any] = data
    if str(event or "").strip().casefold() == "done":
        safe_payload, omitted = _without_inline_media(data)
        payload = safe_payload if isinstance(safe_payload, dict) else {}
        if omitted:
            payload["inline_media_omitted_count"] = omitted
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def sanitize_brain_error(error: Exception | None, brain_config: dict[str, Any]) -> str:
    text = str(error or "").strip()
    if not text:
        text = error.__class__.__name__ if error is not None else "unknown provider error"
    secret = str(brain_config.get("api_key") or "")
    endpoint = str(brain_config.get("model_endpoint") or "")
    for sensitive in (secret, endpoint):
        if sensitive:
            text = text.replace(sensitive, "[redacted]")
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|token|key)=)[^\s&]+", r"\1[redacted]", text)
    return text[:240] + "..." if len(text) > 240 else text


def decision_from_completion(completion: Any) -> BrainDecision:
    decision = getattr(completion, "decision", None)
    if isinstance(decision, BrainDecision):
        return decision
    return BrainDecision.say(str(getattr(completion, "text", "") or ""))
