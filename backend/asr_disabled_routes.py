from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, WebSocket


async def asr_warmup() -> dict[str, Any]:
    return {
        "ok": False,
        "enabled": False,
        "available": False,
        "detail": "ASR is disabled in the Neo Aspect minimal backend.",
    }


async def asr_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json(
        {
            "type": "error",
            "detail": "ASR streaming is disabled in the Neo Aspect minimal backend.",
        }
    )
    await websocket.close(code=1000)


def create_asr_disabled_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/asr/warmup")
    async def asr_warmup_route() -> dict[str, Any]:
        return await asr_warmup()

    @router.websocket("/api/asr/stream")
    async def asr_stream_route(websocket: WebSocket) -> None:
        await asr_stream(websocket)

    return router


def register_asr_disabled_routes(app: FastAPI) -> None:
    app.include_router(create_asr_disabled_router())
