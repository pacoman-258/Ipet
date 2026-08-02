from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DecisionKind(str, Enum):
    SAY = "say"
    THINK = "think"
    OBSERVE = "observe"
    PROPOSE_ACT = "propose_act"
    PROPOSE_REMEMBER = "propose_remember"
    STOP = "stop"


_REVIEW_REQUIRED = {
    DecisionKind.PROPOSE_ACT,
    DecisionKind.PROPOSE_REMEMBER,
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
    def say(cls, text: str, *, goal: dict[str, Any] | None = None) -> "BrainDecision":
        payload: dict[str, Any] = {"text": str(text or "")}
        if isinstance(goal, dict):
            payload["goal"] = dict(goal)
        return cls(DecisionKind.SAY, text, payload)

    @classmethod
    def think(
        cls,
        thought: str,
        *,
        goal: dict[str, Any] | None = None,
        next_kind: str = "",
    ) -> "BrainDecision":
        thought_text = str(thought or "").strip()
        payload: dict[str, Any] = {"thought": thought_text}
        if isinstance(goal, dict):
            payload["goal"] = dict(goal)
        next_text = str(next_kind or "").strip()
        if next_text:
            payload["next_kind"] = next_text
        return cls(DecisionKind.THINK, thought_text or "Think", payload)

    @classmethod
    def observe(
        cls,
        target: str = "screen",
        observe_prompt: str = "",
        *,
        target_app: str = "",
        ax_query: str = "",
        require_coordinates: bool = False,
        goal: dict[str, Any] | None = None,
    ) -> "BrainDecision":
        target_text = str(target or "screen").strip() or "screen"
        payload: dict[str, Any] = {"target": target_text}
        prompt_text = str(observe_prompt or "").strip()
        if prompt_text:
            payload["observe_prompt"] = prompt_text
        target_app_text = str(target_app or "").strip()
        if target_app_text:
            payload["target_app"] = target_app_text
        ax_query_text = str(ax_query or "").strip()
        if ax_query_text:
            payload["ax_query"] = ax_query_text[:240]
        if require_coordinates:
            payload["require_coordinates"] = True
        if isinstance(goal, dict):
            payload["goal"] = dict(goal)
        return cls(DecisionKind.OBSERVE, f"Observe {target_text}", payload)

    @classmethod
    def propose_act(
        cls,
        action_type: str,
        arguments: dict[str, Any],
        *,
        goal: dict[str, Any] | None = None,
    ) -> "BrainDecision":
        action = str(action_type or "").strip()
        payload: dict[str, Any] = {"action_type": action, "arguments": dict(arguments or {})}
        if isinstance(goal, dict):
            payload["goal"] = dict(goal)
        return cls(
            DecisionKind.PROPOSE_ACT,
            f"Propose action: {action}",
            payload,
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
    def stop(cls, summary: str = "", *, goal: dict[str, Any] | None = None) -> "BrainDecision":
        summary_text = str(summary or "Stop turn").strip() or "Stop turn"
        payload: dict[str, Any] = {"summary": summary_text}
        if isinstance(goal, dict):
            payload["goal"] = dict(goal)
        return cls(DecisionKind.STOP, summary_text, payload)
