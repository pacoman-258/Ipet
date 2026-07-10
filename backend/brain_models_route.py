from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

from fastapi import APIRouter, Body, FastAPI, HTTPException

from brain.llm import PROVIDER_GOOGLE_AISTUDIO, BrainLLMError


@dataclass(frozen=True)
class BrainModelsRouteDependencies:
    normalize_private_config: Callable[[], dict[str, Any]]
    normalize_provider: Callable[[Any], str]
    list_provider_models: Callable[[dict[str, Any]], Awaitable[Iterable[Any]]]
    sanitize_brain_error: Callable[[Exception, dict[str, Any]], str]


def brain_model_to_dict(model: Any) -> dict[str, str]:
    if hasattr(model, "to_dict"):
        data = model.to_dict()
        if isinstance(data, dict):
            model_id = str(data.get("id") or "").strip()
            label = str(data.get("label") or model_id).strip()
            return {"id": model_id, "label": label or model_id}
    model_id = str(getattr(model, "id", "") or "").strip()
    label = str(getattr(model, "label", "") or model_id).strip()
    return {"id": model_id, "label": label or model_id}


async def get_brain_models(
    payload: dict[str, Any] | None,
    *,
    deps: BrainModelsRouteDependencies,
) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    current = deps.normalize_private_config()
    saved_brain = current.get("brain", {}) if isinstance(current.get("brain"), dict) else {}
    saved_human_ops = current.get("human_ops", {}) if isinstance(current.get("human_ops"), dict) else {}
    saved_observe_model = (
        saved_human_ops.get("observe_model") if isinstance(saved_human_ops.get("observe_model"), dict) else {}
    )
    scope = str(body.get("scope") or "brain").strip()
    saved_model_config = saved_observe_model if scope in {"observe", "human_ops.observe_model"} else saved_brain
    provider = deps.normalize_provider(body.get("provider", saved_model_config.get("provider")))
    submitted_key = str(body.get("api_key") or "").strip()
    saved_provider = deps.normalize_provider(saved_model_config.get("provider"))
    if "model_endpoint" in body or "endpoint" in body:
        endpoint = str(body.get("model_endpoint") or body.get("endpoint") or "").strip()
    elif provider == saved_provider:
        endpoint = str(saved_model_config.get("model_endpoint") or saved_model_config.get("endpoint") or "").strip()
    else:
        endpoint = ""
    saved_key = str(saved_model_config.get("api_key") or "").strip()
    api_key = submitted_key or (saved_key if provider == saved_provider else "")
    if not endpoint and provider != PROVIDER_GOOGLE_AISTUDIO:
        raise HTTPException(status_code=400, detail="Brain model endpoint is required.")

    request_config = {
        **saved_model_config,
        "provider": provider,
        "model_endpoint": endpoint,
        "api_key": api_key,
    }
    try:
        models = await deps.list_provider_models(request_config)
    except BrainLLMError as exc:
        detail = deps.sanitize_brain_error(exc, request_config)
        raise HTTPException(status_code=502, detail=f"Brain model list failed: {detail}") from exc
    except Exception as exc:
        detail = deps.sanitize_brain_error(exc, request_config)
        raise HTTPException(status_code=502, detail=f"Brain model list failed: {detail}") from exc

    return {
        "ok": True,
        "provider": provider,
        "models": [item for item in (brain_model_to_dict(model) for model in models) if item["id"]],
    }


def create_brain_models_router(deps: BrainModelsRouteDependencies) -> APIRouter:
    router = APIRouter()

    @router.post("/api/brain/models")
    async def get_brain_models_route(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        return await get_brain_models(payload, deps=deps)

    return router


def register_brain_models_routes(app: FastAPI, deps: BrainModelsRouteDependencies) -> None:
    app.include_router(create_brain_models_router(deps))
