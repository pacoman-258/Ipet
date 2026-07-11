from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException

from .chat_topics import TopicStore


@dataclass(frozen=True)
class ChatTopicsRouteDependencies:
    topic_store: TopicStore | Callable[[], TopicStore]
    default_topic_title: str
    conversation_saving_enabled: Callable[[], bool] = lambda: True

    def resolved(self) -> ChatTopicsRouteDependencies:
        topic_store = self.topic_store() if callable(self.topic_store) else self.topic_store
        if topic_store is self.topic_store:
            return self
        return ChatTopicsRouteDependencies(
            topic_store=topic_store,
            default_topic_title=self.default_topic_title,
            conversation_saving_enabled=self.conversation_saving_enabled,
        )

    def current_topic_store(self) -> TopicStore:
        topic_store = self.topic_store() if callable(self.topic_store) else self.topic_store
        return topic_store


def list_skills() -> dict[str, Any]:
    return {"skills": [], "recipes": [], "items": []}


async def create_chat_topic(
    payload: dict[str, Any] | None,
    *,
    deps: ChatTopicsRouteDependencies,
) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    topic_id = body.get("topic_id") or body.get("session_id")
    title = str(body.get("title") or deps.default_topic_title)
    persisted = bool(body.get("persisted", True)) and bool(deps.conversation_saving_enabled())
    topic = deps.current_topic_store().create_topic(topic_id=topic_id, title=title, persisted=persisted)
    return {"ok": True, "topic": topic, **topic}


async def list_chat_topics(*, deps: ChatTopicsRouteDependencies) -> dict[str, Any]:
    return {"ok": True, "topics": deps.current_topic_store().list_topics()}


async def get_chat_topic(topic_id: str, *, deps: ChatTopicsRouteDependencies) -> dict[str, Any]:
    detail = deps.current_topic_store().get_topic_detail(topic_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Topic not found.")
    return {"ok": True, **detail}


async def delete_chat_topic(topic_id: str, *, deps: ChatTopicsRouteDependencies) -> dict[str, Any]:
    topic_store = deps.current_topic_store()
    deleted = topic_store.delete_topic(topic_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Topic not found.")
    return {"ok": True, "deleted": True, "topics": topic_store.list_topics()}


async def delete_chat_topic_post(topic_id: str, *, deps: ChatTopicsRouteDependencies) -> dict[str, Any]:
    return await delete_chat_topic(topic_id, deps=deps)


def create_chat_topics_router(deps: ChatTopicsRouteDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/api/skills")
    async def list_skills_route() -> dict[str, Any]:
        return list_skills()

    @router.post("/api/chat/topics")
    async def create_chat_topic_route(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        return await create_chat_topic(payload, deps=deps.resolved())

    @router.get("/api/chat/topics")
    async def list_chat_topics_route() -> dict[str, Any]:
        return await list_chat_topics(deps=deps.resolved())

    @router.get("/api/chat/topics/{topic_id}")
    async def get_chat_topic_route(topic_id: str) -> dict[str, Any]:
        return await get_chat_topic(topic_id, deps=deps.resolved())

    @router.delete("/api/chat/topics/{topic_id}")
    async def delete_chat_topic_route(topic_id: str) -> dict[str, Any]:
        return await delete_chat_topic(topic_id, deps=deps.resolved())

    @router.post("/api/chat/topics/{topic_id}/delete")
    async def delete_chat_topic_post_route(topic_id: str) -> dict[str, Any]:
        return await delete_chat_topic_post(topic_id, deps=deps.resolved())

    return router


def register_chat_topics_routes(app: FastAPI, deps: ChatTopicsRouteDependencies) -> None:
    app.include_router(create_chat_topics_router(deps))
