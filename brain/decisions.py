from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DecisionKind(str, Enum):
    SAY = "say"
    OBSERVE = "observe"
    PROPOSE_ACT = "propose_act"
    PROPOSE_REMEMBER = "propose_remember"
    PROPOSE_LEARN_SKILL = "propose_learn_skill"
    STOP = "stop"


_REVIEW_REQUIRED = {
    DecisionKind.PROPOSE_ACT,
    DecisionKind.PROPOSE_REMEMBER,
    DecisionKind.PROPOSE_LEARN_SKILL,
}


@dataclass(frozen=True)
class BrainDecision:
    kind: DecisionKind | str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            normalized = self.kind if isinstance(self.kind, DecisionKind) else DecisionKind(str(self.kind))
        except ValueError as exc:
            raise ValueError(f"unsupported brain decision kind: {self.kind}") from exc
        object.__setattr__(self, "kind", normalized)
        object.__setattr__(self, "summary", str(self.summary or "").strip())
        object.__setattr__(self, "payload", dict(self.payload or {}))

    @property
    def requires_review(self) -> bool:
        return self.kind in _REVIEW_REQUIRED

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "summary": self.summary,
            "payload": dict(self.payload),
            "requires_review": self.requires_review,
        }

    @classmethod
    def say(cls, text: str) -> "BrainDecision":
        return cls(DecisionKind.SAY, text, {"text": str(text or "")})

    @classmethod
    def observe(
        cls,
        target: str = "screen",
        observe_prompt: str = "",
    ) -> "BrainDecision":
        target_text = str(target or "screen").strip() or "screen"
        payload: dict[str, Any] = {"target": target_text}
        prompt_text = str(observe_prompt or "").strip()
        if prompt_text:
            payload["observe_prompt"] = prompt_text
        return cls(DecisionKind.OBSERVE, f"Observe {target_text}", payload)

    @classmethod
    def propose_act(cls, action_type: str, arguments: dict[str, Any]) -> "BrainDecision":
        action = str(action_type or "").strip()
        return cls(
            DecisionKind.PROPOSE_ACT,
            f"Propose action: {action}",
            {"action_type": action, "arguments": dict(arguments or {})},
        )

    @classmethod
    def propose_remember(cls, category: str, text: str) -> "BrainDecision":
        category_text = str(category or "general").strip() or "general"
        memory_text = str(text or "").strip()
        return cls(
            DecisionKind.PROPOSE_REMEMBER,
            f"Propose memory: {category_text}",
            {"category": category_text, "text": memory_text},
        )

    @classmethod
    def propose_learn_skill(cls, name: str, steps: list[str]) -> "BrainDecision":
        skill_name = str(name or "").strip()
        clean_steps = [str(step or "").strip() for step in steps if str(step or "").strip()]
        return cls(
            DecisionKind.PROPOSE_LEARN_SKILL,
            f"Propose skill: {skill_name}",
            {"name": skill_name, "steps": clean_steps},
        )

    @classmethod
    def stop(cls, summary: str = "") -> "BrainDecision":
        summary_text = str(summary or "Stop turn").strip() or "Stop turn"
        return cls(DecisionKind.STOP, summary_text, {"summary": summary_text})
