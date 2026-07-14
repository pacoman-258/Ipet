from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .previews import ClickPreview


class ApprovalRequirement(str, Enum):
    REQUIRED = "required"
    NOT_REQUIRED = "not_required"


_STATE_CHANGING_ACTIONS = {
    "click",
    "type_text",
    "key_press",
    "hotkey",
    "scroll",
    "drag",
    "launch_app",
    "focus_window",
    "clipboard_write",
    "wait",
    "playwright",
    "file_list",
    "file_read",
    "file_write",
    "file_mkdir",
    "file_copy",
    "file_move",
    "file_delete",
}


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
        if action not in _STATE_CHANGING_ACTIONS:
            raise ValueError(f"action requires no act proposal or is unsupported: {action}")
        return cls(
            proposal_type="act",
            summary=str(summary or "").strip(),
            payload={"action_type": action, "arguments": dict(payload or {})},
            preview=preview,
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
