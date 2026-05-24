from __future__ import annotations

from dataclasses import dataclass, field


def _clean_list(values: list[str] | tuple[str, ...]) -> list[str]:
    return [str(item or "").strip() for item in values if str(item or "").strip()]


@dataclass(frozen=True)
class SkillRecipeProposal:
    name: str
    triggers: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    verification: str = ""
    approved: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", str(self.name or "").strip())
        object.__setattr__(self, "triggers", _clean_list(self.triggers))
        object.__setattr__(self, "steps", _clean_list(self.steps))
        object.__setattr__(self, "cautions", _clean_list(self.cautions))
        object.__setattr__(self, "verification", str(self.verification or "").strip())

    def approve(self) -> "SkillRecipeProposal":
        return SkillRecipeProposal(
            name=self.name,
            triggers=list(self.triggers),
            steps=list(self.steps),
            cautions=list(self.cautions),
            verification=self.verification,
            approved=True,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "triggers": list(self.triggers),
            "steps": list(self.steps),
            "cautions": list(self.cautions),
            "verification": self.verification,
            "approved": bool(self.approved),
        }
