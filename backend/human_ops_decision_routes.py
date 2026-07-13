from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from human_ops.approval_flow import (
    HumanOpsApprovalFlowDependencies,
    HumanOpsProposalNotFound,
    stream_human_ops_proposal_decision,
)
from human_ops.approvals import ReviewableProposal


HumanOpsDecisionRouteDependencySource = HumanOpsApprovalFlowDependencies | Callable[[], HumanOpsApprovalFlowDependencies]


def _resolve_human_ops_decision_route_deps(
    deps: HumanOpsDecisionRouteDependencySource,
) -> HumanOpsApprovalFlowDependencies:
    return deps() if callable(deps) else deps


def create_human_ops_decision_router(deps: HumanOpsDecisionRouteDependencySource) -> APIRouter:
    router = APIRouter()

    @router.post("/api/human-ops/proposals/{proposal_id}/decision")
    async def decide_human_ops_proposal_route(
        proposal_id: str,
        payload: dict[str, Any] | None = Body(default=None),
    ) -> StreamingResponse:
        try:
            event_stream = stream_human_ops_proposal_decision(
                proposal_id,
                payload,
                _resolve_human_ops_decision_route_deps(deps),
            )
        except HumanOpsProposalNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return StreamingResponse(event_stream, media_type="text/event-stream")

    @router.post("/api/human-ops/proposals/{proposal_id}/native-decision")
    async def native_decide_human_ops_proposal_route(proposal_id: str) -> StreamingResponse:
        resolved = _resolve_human_ops_decision_route_deps(deps)
        record = resolved.pending_proposals.get(str(proposal_id or "").strip())
        proposal = record.get("proposal") if isinstance(record, dict) else None
        if not isinstance(proposal, ReviewableProposal):
            raise HTTPException(status_code=404, detail="Human Ops proposal not found.")
        if record.get("status") != "pending":
            raise HTTPException(status_code=409, detail="Human Ops proposal is no longer pending.")
        if resolved.request_native_approval is None:
            raise HTTPException(status_code=503, detail="macOS native approval is unavailable.")
        try:
            native_result = await resolved.request_native_approval(proposal)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        event_stream = stream_human_ops_proposal_decision(
            proposal_id,
            {"approved": bool(native_result.get("approved"))},
            resolved,
        )
        return StreamingResponse(event_stream, media_type="text/event-stream")

    return router


def register_human_ops_decision_routes(app: FastAPI, deps: HumanOpsDecisionRouteDependencySource) -> None:
    app.include_router(create_human_ops_decision_router(deps))
