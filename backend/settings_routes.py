from __future__ import annotations

from collections.abc import Awaitable, Callable
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
    list_local_models: Callable[[], list[dict[str, Any]]] = lambda: []
    preview_action: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None
    settings_live2d_js_response: Callable[[], Response] = lambda: Response()
    accessibility_index_status: Callable[[], Awaitable[dict[str, Any]]] | None = None
    refresh_accessibility_index: Callable[[], Awaitable[dict[str, Any]]] | None = None


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

    @router.get("/settings_live2d.js")
    async def settings_live2d_js() -> Response:
        return deps.settings_live2d_js_response()

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

    @router.get("/api/settings/models-local")
    async def get_local_models() -> dict[str, Any]:
        return {"models": deps.list_local_models()}

    @router.post("/api/settings/preview-action")
    async def post_settings_preview_action(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="preview payload must be an object")
        if deps.preview_action is None:
            raise HTTPException(status_code=503, detail="desktop preview is unavailable")
        try:
            result = await deps.preview_action(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc) or "desktop preview failed") from exc
        return {"ok": True, "result": result}

    @router.get("/api/settings/accessibility-index")
    async def get_accessibility_index() -> dict[str, Any]:
        if deps.accessibility_index_status is None:
            raise HTTPException(status_code=503, detail="Accessibility index is unavailable")
        try:
            return {"ok": True, **await deps.accessibility_index_status()}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc) or "Accessibility index status failed") from exc

    @router.post("/api/settings/accessibility-index/refresh")
    async def post_accessibility_index_refresh() -> dict[str, Any]:
        if deps.refresh_accessibility_index is None:
            raise HTTPException(status_code=503, detail="Accessibility index refresh is unavailable")
        try:
            return {"ok": True, **await deps.refresh_accessibility_index()}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc) or "Accessibility index refresh failed") from exc

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
