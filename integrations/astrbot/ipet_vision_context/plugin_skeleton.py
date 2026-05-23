"""Deprecated reference helper for old AstrBot-side Ipet vision experiments.

The supported runtime path is Ipet-owned: the Vision Broker runs before
`/api/chat/stream`, owns passive timeline, active observe, and
Observe-Reason-Retry retry/stop policy, then sends bounded text evidence to
Hermes, AstrBot, or future runtimes. Raw screenshots are not passed to AstrBot
by default.

This module stays only for compatibility tests and manual reference. Do not
install it as the recommended production AstrBot plugin path.
"""

from __future__ import annotations

import urllib.request
import urllib.parse
import json
import os


IPET_VISION_CONTEXT_URL = "http://127.0.0.1:8008/api/vision/context"
IPET_VISION_OBSERVE_URL = "http://127.0.0.1:8008/api/vision/observe"
IPET_LOCAL_TOKEN_ENV = "IPET_LOCAL_API_TOKEN"
IPET_LOCAL_TOKEN_HEADER = "X-Ipet-Local-Token"

VISION_TOOL_PROTOCOL_PROMPT = """[Deprecated Ipet Vision Reference Helper]
正式路径：Ipet Vision Broker 在 /api/chat/stream 进入 Hermes/AstrBot/future runtime 前完成 passive/active evidence gate。Ipet owns passive timeline、active observe、Observe-Reason-Retry 的 target/retry/stop；runtime 默认只接收 bounded text evidence，不接收 raw screenshots。
本提示仅保留给旧 AstrBot-side helper 和手动调试；不要把它作为推荐插件安装路径。
passive timeline 只是后台近期变化背景，不能代替 active broker live evidence，也不能让 runtime 声称刚刚亲眼看过当前屏幕。
如果在旧调试流程中手动调用 ipet_active_observe，它只是请求 Ipet local API 的单次 active observation；不要让 AstrBot/model 接管 retry loop。
当前生产路径应由 Ipet broker 在进入 runtime 前完成主动证据门、重试和停止。停止后若仍无证据，runtime 应回答“我无法从当前截图确认”，不能编造观察。"""


def build_vision_tool_protocol_context() -> str:
    return VISION_TOOL_PROTOCOL_PROMPT


def fetch_ipet_vision_context(timeout_sec: float = 0.8) -> str:
    token = str(os.environ.get(IPET_LOCAL_TOKEN_ENV) or "").strip()
    if not token:
        return ""
    request = urllib.request.Request(
        IPET_VISION_CONTEXT_URL,
        headers={IPET_LOCAL_TOKEN_HEADER: token},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return ""
    if not payload.get("available"):
        return ""
    if not payload.get("grounded"):
        return "有最近截图，但 Ipet 尚无可验证视觉证据；如被问到屏幕内容，应回答“我无法从当前截图确认”。"
    lines = [
        "[Ipet grounded vision evidence]",
        f"frame_id={payload.get('frame_id') or ''} frame_hash={payload.get('frame_hash') or ''}",
    ]
    summary = str(payload.get("verified_summary") or payload.get("summary") or "").strip()
    if summary:
        lines.append(f"summary={summary}")
    for index, item in enumerate(payload.get("observations") or [], 1):
        claim = str(item.get("claim") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        confidence = item.get("confidence")
        if claim:
            lines.append(f"{index}. {claim} confidence={confidence} evidence={evidence}")
    return "\n".join(lines).strip()


def fetch_ipet_vision_summary(timeout_sec: float = 0.8) -> str:
    return fetch_ipet_vision_context(timeout_sec=timeout_sec)


def _local_token() -> str:
    return str(os.environ.get(IPET_LOCAL_TOKEN_ENV) or "").strip()


def _compact_timeline(payload: dict) -> str:
    timeline = payload.get("timeline") if isinstance(payload.get("timeline"), list) else []
    if not timeline:
        return ""
    lines = ["最近屏幕变化："]
    for index, item in enumerate(timeline[:5], 1):
        summary = str(item.get("change_summary") or "").strip()
        if not summary:
            continue
        extras = []
        objects = [str(value or "").strip() for value in (item.get("important_objects") or []) if str(value or "").strip()]
        text = [str(value or "").strip() for value in (item.get("visible_text") or []) if str(value or "").strip()]
        if objects:
            extras.append("对象=" + ", ".join(objects[:3]))
        if text:
            extras.append("文字=" + ", ".join(text[:3]))
        if item.get("confidence") is not None:
            extras.append(f"confidence={item.get('confidence')}")
        suffix = f" ({'；'.join(extras)})" if extras else ""
        lines.append(f"{index}. {summary}{suffix}")
    return "\n".join(lines).strip()


def ipet_passive_vision_timeline(timeout_sec: float = 0.8) -> str:
    """Tool: read Ipet's latest passive visual timeline without requesting a screenshot."""

    token = _local_token()
    if not token:
        return ""
    url = IPET_VISION_CONTEXT_URL + "?lane=passive"
    request = urllib.request.Request(url, headers={IPET_LOCAL_TOKEN_HEADER: token})
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return ""
    return _compact_timeline(payload if isinstance(payload, dict) else {})


def ipet_active_observe(
    text: str = "",
    timeout_sec: float = 15.0,
    target_hint: str = "",
    attempt_reason: str = "",
    exclude_seen: list | None = None,
) -> dict:
    """Deprecated reference helper: request one Ipet local active observation.

    Current production flow should let the Ipet broker decide whether active
    observe is needed and own retry/stop before AstrBot receives bounded text
    evidence. This compatibility helper may request an image URL, so do not wire
    it into the default AstrBot chat path.
    """

    token = _local_token()
    if not token:
        return {"ok": False, "error": "IPET_LOCAL_API_TOKEN is not configured", "image_urls": []}
    url = IPET_VISION_OBSERVE_URL + "?lane=active&include_image=true"
    body = json.dumps(
        {
            "text": text,
            "force": True,
            "target_hint": target_hint,
            "attempt_reason": attempt_reason,
            "exclude_seen": list(exclude_seen or [])[:10],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            IPET_LOCAL_TOKEN_HEADER: token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "image_urls": []}
    return payload if isinstance(payload, dict) else {"ok": False, "error": "invalid Ipet response", "image_urls": []}


def inject_active_observe_result_into_next_request(request, result: dict) -> None:
    """Legacy/debug helper for old experiments that accepted one-shot image URLs."""

    if not isinstance(result, dict):
        return
    parts = list(getattr(request, "extra_user_content_parts", []) or [])
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    observations = context.get("observations") if isinstance(context.get("observations"), list) else []
    if observations:
        lines = ["[Ipet active visual observation]"]
        for index, item in enumerate(observations[:5], 1):
            claim = str(item.get("claim") or "").strip()
            if claim:
                lines.append(f"{index}. {claim}")
        if len(lines) > 1:
            parts.append({"type": "text", "text": "\n".join(lines)})
    image_urls = result.get("image_urls") if isinstance(result.get("image_urls"), list) else []
    for image_url in image_urls:
        url = str(image_url or "").strip()
        if url.startswith("base64://"):
            parts.append({"type": "image_url", "image_url": url})
    request.extra_user_content_parts = parts


async def on_before_llm_request(request) -> None:
    """Deprecated pseudo-hook retained only as a reference compatibility shape.

    The current runtime path injects Ipet broker evidence before the AstrBot
    adapter is called. Do not install this as a production plugin.
    """

    parts = list(getattr(request, "extra_user_content_parts", []) or [])
    parts.insert(0, {"type": "text", "text": build_vision_tool_protocol_context()})
    context = ipet_passive_vision_timeline() or fetch_ipet_vision_context()
    if context:
        context_text = f"[Ipet vision context]\n{context}"
        parts.insert(1, {"type": "text", "text": context_text})
    request.extra_user_content_parts = parts
