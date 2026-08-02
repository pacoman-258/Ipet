from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, FastAPI
from fastapi.responses import StreamingResponse
from brain.llm import release_codex_task

from .chat_stream_flow import ChatStreamFlowDependencies, stream_chat_response
from .task_control import TASK_CONTROL


ChatStreamRouteDependencySource = ChatStreamFlowDependencies | Callable[[], ChatStreamFlowDependencies]


def _resolve_chat_stream_route_deps(deps: ChatStreamRouteDependencySource) -> ChatStreamFlowDependencies:
    return deps() if callable(deps) else deps


def create_chat_stream_router(deps: ChatStreamRouteDependencySource) -> APIRouter:
    router = APIRouter()

    @router.post("/api/chat/stream")
    async def chat_stream_route(payload: dict[str, Any] | None = Body(default=None)) -> StreamingResponse:
        event_stream = stream_chat_response(payload, _resolve_chat_stream_route_deps(deps))
        return StreamingResponse(event_stream, media_type="text/event-stream")

    @router.post("/api/chat/tasks/{task_id}/stop")
    async def stop_chat_task_route(task_id: str) -> dict[str, Any]:
        resolved = _resolve_chat_stream_route_deps(deps)
        pending = getattr(resolved, "pending_proposals", {})
        normalized_task_id = str(task_id or "").strip()
        result = TASK_CONTROL.stop(normalized_task_id, pending)
        await release_codex_task(normalized_task_id)
        return result

    return router


def register_chat_stream_routes(app: FastAPI, deps: ChatStreamRouteDependencySource) -> None:
    app.include_router(create_chat_stream_router(deps))
