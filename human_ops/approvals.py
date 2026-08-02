from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from brain.contracts import action_names

from .previews import ClickPreview


class ApprovalRequirement(str, Enum):
    REQUIRED = "required"
    NOT_REQUIRED = "not_required"


_REVIEWABLE_ACTIONS = frozenset(action_names())


@dataclass(frozen=True)
class ReviewableProposal:
    proposal_type: str
    summary: str
    payload: dict[str, Any]
    requirement: ApprovalRequirement = ApprovalRequirement.REQUIRED
    preview: ClickPreview | None = None
    approved: bool = False

    @property
    def requires_review(self) -> bool:
        return self.requirement == ApprovalRequirement.REQUIRED

    @classmethod
    def act(
        cls,
        *,
        action_type: str,
        summary: str,
        payload: dict[str, Any],
        preview: ClickPreview | None = None,
    ) -> "ReviewableProposal":
        action = str(action_type or "").strip()
        if action not in _REVIEWABLE_ACTIONS:
            raise ValueError(f"action requires no act proposal or is unsupported: {action}")
        return cls(
            proposal_type="act",
            summary=str(summary or "").strip(),
            payload={"action_type": action, "arguments": dict(payload or {})},
            preview=preview,
        )

    @classmethod
    def remember(
        cls,
        *,
        summary: str,
        payload: dict[str, Any],
    ) -> "ReviewableProposal":
        operation = str(payload.get("operation") or "save").strip()
        if operation not in {"save", "forget", "resolve", "snooze"}:
            raise ValueError(f"unsupported memory operation: {operation}")
        return cls(
            proposal_type="remember",
            summary=str(summary or "").strip(),
            payload={**dict(payload or {}), "operation": operation},
        )

    def approve(self) -> "ReviewableProposal":
        return ReviewableProposal(
            proposal_type=self.proposal_type,
            summary=self.summary,
            payload=dict(self.payload),
            requirement=self.requirement,
            preview=self.preview,
            approved=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_type": self.proposal_type,
            "summary": self.summary,
            "payload": dict(self.payload),
            "requirement": self.requirement.value,
            "approved": self.approved,
        }
