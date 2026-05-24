from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryProposal:
    category: str
    text: str
    approved: bool = False

    @property
    def requires_review(self) -> bool:
        return not self.approved

    def approve(self) -> "MemoryProposal":
        return MemoryProposal(category=self.category, text=self.text, approved=True)

    def to_dict(self) -> dict[str, object]:
        return {
            "category": str(self.category or "general"),
            "text": str(self.text or ""),
            "approved": bool(self.approved),
            "requires_review": self.requires_review,
        }
