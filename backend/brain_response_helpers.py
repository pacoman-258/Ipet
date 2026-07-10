from __future__ import annotations

import json
import re
from typing import Any

from brain.decisions import BrainDecision


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
