from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, FastAPI


@dataclass(frozen=True)
class HealthRouteDependencies:
    app_version: str | Callable[[], str]
    normalize_private_config: Callable[[], dict[str, Any]]
    tts_available: Callable[[str | None, str | None], bool]

    def current_app_version(self) -> str:
        version = self.app_version() if callable(self.app_version) else self.app_version
        return str(version)


HealthRouteDependencySource = HealthRouteDependencies | Callable[[], HealthRouteDependencies]


def _resolve_health_route_deps(deps: HealthRouteDependencySource) -> HealthRouteDependencies:
    return deps() if callable(deps) else deps


def health_payload(*, deps: HealthRouteDependencies) -> dict[str, Any]:
    config = deps.normalize_private_config()
    return {
        "status": "ok",
        "service": "ipet-neo-aspect-backend",
        "version": deps.current_app_version(),
        "neo_aspect": {
            "body": True,
            "brain": True,
            "human_ops": True,
            "memory": bool(config.get("memory", {}).get("conversation_saving", True)),
            "skills": bool(config.get("skills", {}).get("recipes_enabled", True)),
        },
        "asr": False,
        "tts": deps.tts_available(
            config.get("chat", {}).get("tts_provider"),
            config.get("chat", {}).get("tts_provider_url"),
        ),
        "message": "Neo Aspect backend is running with the new Body, Brain, Human Ops, Memory & Skills contract.",
    }


def create_health_router(deps: HealthRouteDependencySource) -> APIRouter:
    router = APIRouter()

    @router.get("/api/health")
    async def health_route() -> dict[str, Any]:
        return health_payload(deps=_resolve_health_route_deps(deps))

    return router


def register_health_routes(app: FastAPI, deps: HealthRouteDependencySource) -> None:
    app.include_router(create_health_router(deps))
