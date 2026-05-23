from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any


TOKEN_EVENTS = {"token", "segment", "display_segment"}
TERMINAL_EVENTS = {"done", "error"}

DEFAULT_FIRST_EVENT_GRACE_SEC = 0.01
DEFAULT_HEARTBEAT_AFTER_SEC = 2.0
DEFAULT_HEARTBEAT_INTERVAL_SEC = 5.0


def _phase_payload(
    status_id: str,
    text: str,
    *,
    phase: str = "thought",
    source: str = "ipet_inferred",
    transient: bool = True,
    confidence: float = 0.6,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "text": text,
        "speaker": "pet",
        "source": source,
        "transient": transient,
        "status_id": status_id,
        "confidence": confidence,
        "render": "worklog",
    }


def _looks_external(text: str) -> bool:
    lowered = str(text or "").lower()
    markers = (
        "http://",
        "https://",
        "search",
        "查",
        "搜",
        "文件",
        "工具",
        "技能",
        "屏幕",
        "截图",
        "网页",
        "打开",
        "读取",
        "运行",
    )
    return any(marker in lowered for marker in markers)


def prelude_phases(request_text: str) -> list[tuple[str, dict[str, Any]]]:
    external = _looks_external(request_text)
    capability_text = "判断这轮可能需要外部能力或运行时工具。" if external else "判断这轮可以先交给模型处理。"
    return [
        (
            "phase",
            _phase_payload(
                "received_question",
                "收到问题，正在整理这轮请求。",
                phase="thought",
                confidence=0.78,
            ),
        ),
        (
            "phase",
            _phase_payload(
                "capability_check",
                capability_text,
                phase="thought",
                confidence=0.62 if external else 0.56,
            ),
        ),
        (
            "phase",
            _phase_payload(
                "handoff_runtime",
                "交给当前运行时处理真实模型、工具和技能调用。",
                phase="action",
                confidence=0.82,
            ),
        ),
    ]


def heartbeat_phase(index: int) -> tuple[str, dict[str, Any]]:
    statuses = [
        ("waiting_model", "等待运行时返回模型输出。", 0.44),
        ("waiting_tool_or_skill", "运行时可能还在等待工具或技能结果。", 0.32),
        ("organizing_output", "继续等待运行时整理最终输出。", 0.36),
    ]
    status_id, text, confidence = statuses[index % len(statuses)]
    return "phase", _phase_payload(status_id, text, phase="thought", confidence=confidence)


def mark_runtime_phase(data: dict[str, Any]) -> dict[str, Any]:
    payload = dict(data or {})
    payload.setdefault("speaker", "pet")
    payload.setdefault("source", "runtime")
    payload.setdefault("transient", False)
    payload.setdefault("render", "worklog")
    payload.setdefault("status_id", f"runtime:{str(payload.get('phase') or 'phase').strip() or 'phase'}")
    payload.setdefault("confidence", 1.0)
    return payload


async def _next_event(iterator: AsyncIterator[tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]]:
    event, data = await iterator.__anext__()
    return str(event or ""), dict(data or {})


async def realtime_stream_events(
    runtime_events: AsyncIterator[tuple[str, dict[str, Any]]],
    *,
    request_text: str,
    heartbeat_after_sec: float = DEFAULT_HEARTBEAT_AFTER_SEC,
    heartbeat_interval_sec: float = DEFAULT_HEARTBEAT_INTERVAL_SEC,
    first_event_grace_sec: float = DEFAULT_FIRST_EVENT_GRACE_SEC,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    iterator = runtime_events.__aiter__()
    pending = asyncio.create_task(_next_event(iterator))
    first_event: tuple[str, dict[str, Any]] | None = None
    heartbeat_enabled = True
    heartbeat_count = 0
    next_wait = max(0.0, float(heartbeat_after_sec))

    try:
        try:
            first_event = await asyncio.wait_for(asyncio.shield(pending), timeout=max(0.0, float(first_event_grace_sec)))
            pending = None
        except asyncio.TimeoutError:
            first_event = None

        if first_event and first_event[0] == "meta":
            yield first_event
            first_event = None

        for event in prelude_phases(request_text):
            yield event

        if first_event:
            event, data = _normalize_runtime_event(first_event)
            if event in TOKEN_EVENTS or event in TERMINAL_EVENTS:
                heartbeat_enabled = False
            yield event, data
            if event in TERMINAL_EVENTS:
                return

        while True:
            if pending is None:
                pending = asyncio.create_task(_next_event(iterator))

            if heartbeat_enabled:
                try:
                    item = await asyncio.wait_for(asyncio.shield(pending), timeout=next_wait)
                except asyncio.TimeoutError:
                    yield heartbeat_phase(heartbeat_count)
                    heartbeat_count += 1
                    next_wait = max(0.0, float(heartbeat_interval_sec))
                    continue
            else:
                item = await pending

            pending = None
            event, data = _normalize_runtime_event(item)
            if event in TOKEN_EVENTS or event in TERMINAL_EVENTS:
                heartbeat_enabled = False
            yield event, data
            if event in TERMINAL_EVENTS:
                return
    except StopAsyncIteration:
        return
    finally:
        if pending is not None and not pending.done():
            pending.cancel()


def _normalize_runtime_event(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    event, data = item
    if event == "phase":
        return event, mark_runtime_phase(data)
    return event, data
