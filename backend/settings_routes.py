from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException
from fastapi.responses import Response


@dataclass(frozen=True)
class SettingsRouteDependencies:
    settings_page_response: Callable[[], Response]
    settings_css_response: Callable[[], Response]
    settings_js_response: Callable[[], Response]
    settings_form_js_response: Callable[[], Response]
    settings_model_picker_js_response: Callable[[], Response]
    settings_payload: Callable[..., dict[str, Any]]
    normalize_private_config: Callable[[], dict[str, Any]]
    apply_settings_update: Callable[..., dict[str, Any]]
    save_config: Callable[[dict[str, Any]], None]
    list_persona_prompts: Callable[[], list[dict[str, Any]]] = lambda: []
    list_chrome_profiles: Callable[[], Any] = lambda: ()


def create_settings_router(deps: SettingsRouteDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/settings")
    async def settings_page() -> Response:
        return deps.settings_page_response()

    @router.get("/settings.css")
    async def settings_css() -> Response:
        return deps.settings_css_response()

    @router.get("/settings.js")
    async def settings_js() -> Response:
        return deps.settings_js_response()

    @router.get("/settings_form.js")
    async def settings_form_js() -> Response:
        return deps.settings_form_js_response()

    @router.get("/settings_model_picker.js")
    async def settings_model_picker_js() -> Response:
        return deps.settings_model_picker_js_response()

    @router.get("/api/settings/config")
    async def get_settings_config() -> dict[str, Any]:
        return deps.settings_payload()

    @router.get("/api/settings/persona-prompts")
    async def get_persona_prompts() -> dict[str, Any]:
        return {"prompts": deps.list_persona_prompts()}

    @router.get("/api/settings/chrome-profiles")
    async def get_chrome_profiles() -> dict[str, Any]:
        try:
            profiles = deps.list_chrome_profiles()
        except ValueError as exc:
            return {"profiles": [], "error": str(exc)}
        payload: list[dict[str, Any]] = []
        for profile in profiles:
            if isinstance(profile, dict):
                directory = str(profile.get("directory") or "").strip()
                display_name = str(profile.get("display_name") or directory).strip() or directory
                active = bool(profile.get("active", False))
            else:
                directory = str(getattr(profile, "directory", "") or "").strip()
                display_name = str(getattr(profile, "display_name", "") or directory).strip() or directory
                active = bool(getattr(profile, "active", False))
            if directory:
                payload.append({"directory": directory, "display_name": display_name, "active": active})
        return {"profiles": payload, "error": ""}

    @router.put("/api/settings/config")
    async def put_settings_config(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        incoming = payload.get("config") if isinstance(payload, dict) else {}
        if not isinstance(incoming, dict):
            raise HTTPException(status_code=400, detail="config must be an object.")

        current = deps.normalize_private_config()
        sanitized = deps.apply_settings_update(incoming, current=current)
        deps.save_config(sanitized)
        return deps.settings_payload(sanitized)

    return router


def register_settings_routes(app: FastAPI, deps: SettingsRouteDependencies) -> None:
    app.include_router(create_settings_router(deps))
