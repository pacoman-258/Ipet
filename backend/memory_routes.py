from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException

from human_ops.approvals import ReviewableProposal

from .ipet_memory_store import IpetMemoryStore


@dataclass(frozen=True)
class MemoryRouteDependencies:
    memory_store: IpetMemoryStore
    pending_proposals: MutableMapping[str, dict[str, Any]]
    normalize_private_config: Callable[[], dict[str, Any]] = lambda: {}


MemoryRouteDependencySource = MemoryRouteDependencies | Callable[[], MemoryRouteDependencies]


def _resolve_memory_route_deps(deps: MemoryRouteDependencySource) -> MemoryRouteDependencies:
    return deps() if callable(deps) else deps


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "path"}


def _pending_memory_candidates(
    pending: MutableMapping[str, dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for proposal_id, record in pending.items():
        proposal = record.get("proposal") if isinstance(record, dict) else None
        if not isinstance(proposal, ReviewableProposal) or proposal.proposal_type != "remember":
            continue
        if record.get("status") != "pending":
            continue
        memory = proposal.payload.get("memory") if isinstance(proposal.payload.get("memory"), dict) else None
        target = proposal.payload.get("target") if isinstance(proposal.payload.get("target"), dict) else None
        candidates.append(
            {
                "proposal_id": str(proposal_id),
                "operation": str(proposal.payload.get("operation") or "save"),
                "summary": proposal.summary,
                "memory": dict(memory or {}),
                "target": _public_record(dict(target or {})),
                "origin": str(record.get("origin") or "explicit"),
                "source_conversation_id": str(record.get("session_id") or ""),
                "created_at": record.get("created_at"),
            }
        )
    candidates.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    return candidates[: max(0, int(limit or 0))]


def create_memory_router(deps: MemoryRouteDependencySource) -> APIRouter:
    router = APIRouter()

    @router.get("/api/memory")
    async def get_memory_catalog() -> dict[str, Any]:
        resolved = _resolve_memory_route_deps(deps)
        private_config = resolved.normalize_private_config()
        memory_config = private_config.get("memory", {}) if isinstance(private_config.get("memory"), dict) else {}
        try:
            review_limit = max(1, int(memory_config.get("review_limit") or 20))
        except (TypeError, ValueError):
            review_limit = 20
        records = [_public_record(record) for record in resolved.memory_store.list_memories(include_inactive=True)]
        return {
            "records": records,
            "pending": _pending_memory_candidates(resolved.pending_proposals, limit=review_limit),
        }

    @router.patch("/api/memory/{memory_id}")
    async def patch_memory(
        memory_id: str,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else payload
        allowed = {
            "title",
            "summary",
            "kind",
            "topic",
            "reason",
            "status",
            "retention_days",
            "expires_at",
            "follow_up_at",
            "tags",
        }
        update = {key: value for key, value in changes.items() if key in allowed}
        if not update:
            raise HTTPException(status_code=400, detail="No editable memory fields were provided.")
        status = str(update.get("status") or "").strip()
        if status and status not in {"active", "resolved", "superseded"}:
            raise HTTPException(status_code=400, detail="Unsupported memory status.")
        try:
            record = _resolve_memory_route_deps(deps).memory_store.update_memory(
                str(memory_id or "").strip(),
                **update,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="Memory not found.")
        return {"memory": _public_record(record)}

    @router.delete("/api/memory/{memory_id}")
    async def delete_memory(memory_id: str) -> dict[str, Any]:
        record = _resolve_memory_route_deps(deps).memory_store.forget_memory(str(memory_id or "").strip())
        if record is None:
            raise HTTPException(status_code=404, detail="Memory not found.")
        return {
            "forgotten": True,
            "memory": _public_record(record),
            "source_conversation_retained": True,
        }

    return router


def register_memory_routes(app: FastAPI, deps: MemoryRouteDependencySource) -> None:
    app.include_router(create_memory_router(deps))
