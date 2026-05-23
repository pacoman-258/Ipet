from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote


_SAFE_TURN_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")


def normalize_runtime_turn_id(turn_id: Any) -> str:
    normalized = str(turn_id or "").strip().replace("/", "-").replace("\\", "-")
    normalized = _SAFE_TURN_ID_RE.sub("-", normalized).strip("-")
    return normalized or "turn"


def temporary_runtime_session_id(turn_id: Any) -> str:
    return f"ipet-temp-{normalize_runtime_turn_id(turn_id)}"


async def cleanup_runtime_session(client: Any, runtime_session_id: str) -> dict[str, Any]:
    session_id = str(runtime_session_id or "").strip()
    if not session_id:
        return {"ok": True, "payload": {}, "skipped": True}
    try:
        delete_runtime_session = getattr(client, "delete_runtime_session", None)
        if callable(delete_runtime_session):
            payload = await delete_runtime_session(session_id)
        else:
            payload = await client.request_json("DELETE", f"/api/chat/topics/{quote(session_id, safe='')}")
        payload_dict = payload if isinstance(payload, dict) else {}
        if payload_dict.get("ok") is False:
            error = str(payload_dict.get("error") or payload_dict.get("detail") or "runtime cleanup failed")
            return {"ok": False, "payload": payload_dict, "error": error}
        return {"ok": True, "payload": payload_dict, "error": ""}
    except Exception as exc:
        return {"ok": False, "payload": {}, "error": str(exc)}
