# Neo Aspect Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start the Neo Aspect refactor by introducing the new Body/Brain/Human Ops/Memory & Skills boundaries and replacing runtime-platform assumptions with reviewable single-step decisions.

**Architecture:** Phase 1 adds new core modules without deleting old runtime code yet, so the project remains runnable while the new architecture gains tests. Brain emits one next-step decision; Human Ops represents reviewable proposals and previews; settings content is modeled around Body, Brain, Human Ops, Memory, and Skills.

**Tech Stack:** Python 3.12, dataclasses, unittest, existing repository layout.

---

## File Structure

- Create `brain/__init__.py`: public Brain exports.
- Create `brain/decisions.py`: single-step decision dataclasses and validation.
- Create `human_ops/__init__.py`: public Human Ops exports.
- Create `human_ops/previews.py`: visual preview metadata, including red-dot click previews.
- Create `human_ops/approvals.py`: review policy for actions, memory writes, and skill writes.
- Create `memory/__init__.py`: public Memory exports.
- Create `memory/review.py`: reviewed memory proposal model.
- Create `skills/__init__.py`: public skill recipe exports for the new recipe layer.
- Create `skills/recipes.py`: reviewed reusable operation recipe model.
- Create `app/__init__.py`: public app schema exports.
- Create `app/settings_schema.py`: Neo Aspect settings sections for the retained web settings page.
- Create `tests/test_neo_aspect_core.py`: unit tests for decision permissions, previews, memory proposals, skill recipes, and settings sections.

## Task 1: Brain Single-Step Decision Models

**Files:**
- Create: `brain/__init__.py`
- Create: `brain/decisions.py`
- Create: `tests/test_neo_aspect_core.py`

- [ ] **Step 1: Write failing tests for decision review requirements**

Add this initial content to `tests/test_neo_aspect_core.py`:

```python
from __future__ import annotations

import unittest

from brain.decisions import BrainDecision, DecisionKind


class NeoAspectDecisionTests(unittest.TestCase):
    def test_say_and_observe_do_not_require_review(self) -> None:
        self.assertFalse(BrainDecision.say("我先看一下。").requires_review)
        self.assertFalse(BrainDecision.observe("screen").requires_review)

    def test_state_changing_decisions_require_review(self) -> None:
        self.assertTrue(BrainDecision.propose_act("click", {"x": 10, "y": 20}).requires_review)
        self.assertTrue(BrainDecision.propose_remember("preference", "用户喜欢简洁回答").requires_review)
        self.assertTrue(BrainDecision.propose_learn_skill("Use Codex", ["打开 Codex"]).requires_review)

    def test_decision_rejects_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            BrainDecision(kind="plan_many_steps", summary="bad")

    def test_decision_kind_values_are_stable(self) -> None:
        self.assertEqual(DecisionKind.SAY.value, "say")
        self.assertEqual(DecisionKind.OBSERVE.value, "observe")
        self.assertEqual(DecisionKind.PROPOSE_ACT.value, "propose_act")
        self.assertEqual(DecisionKind.PROPOSE_REMEMBER.value, "propose_remember")
        self.assertEqual(DecisionKind.PROPOSE_LEARN_SKILL.value, "propose_learn_skill")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_neo_aspect_core.NeoAspectDecisionTests -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'brain'`.

- [ ] **Step 3: Implement Brain decision models**

Create `brain/decisions.py`:

```python
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
    def observe(cls, target: str = "screen") -> "BrainDecision":
        target_text = str(target or "screen").strip() or "screen"
        return cls(DecisionKind.OBSERVE, f"Observe {target_text}", {"target": target_text})

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
```

Create `brain/__init__.py`:

```python
from .decisions import BrainDecision, DecisionKind

__all__ = ["BrainDecision", "DecisionKind"]
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_neo_aspect_core.NeoAspectDecisionTests -v
```

Expected: PASS with 4 tests.

## Task 2: Human Ops Review And Preview Models

**Files:**
- Create: `human_ops/__init__.py`
- Create: `human_ops/previews.py`
- Create: `human_ops/approvals.py`
- Modify: `tests/test_neo_aspect_core.py`

- [ ] **Step 1: Add failing tests for reviewable actions and red-dot previews**

Append to `tests/test_neo_aspect_core.py`:

```python
from human_ops.approvals import ApprovalRequirement, ReviewableProposal
from human_ops.previews import ClickPreview, PreviewKind, red_dot_click_preview


class NeoAspectHumanOpsTests(unittest.TestCase):
    def test_click_preview_uses_red_dot(self) -> None:
        preview = red_dot_click_preview(x=120, y=240, label="发送按钮")

        self.assertEqual(preview.kind, PreviewKind.RED_DOT)
        self.assertEqual(preview.x, 120)
        self.assertEqual(preview.y, 240)
        self.assertEqual(preview.label, "发送按钮")
        self.assertEqual(preview.to_dict()["marker"], "red_dot")

    def test_act_proposal_requires_approval(self) -> None:
        preview = ClickPreview(x=10, y=20, label="Codex 图标")
        proposal = ReviewableProposal.act(
            action_type="click",
            summary="点击 Codex 图标",
            payload={"x": 10, "y": 20},
            preview=preview,
        )

        self.assertEqual(proposal.requirement, ApprovalRequirement.REQUIRED)
        self.assertFalse(proposal.approved)
        self.assertEqual(proposal.preview.to_dict()["label"], "Codex 图标")

    def test_observe_proposal_is_not_reviewable_action(self) -> None:
        with self.assertRaises(ValueError):
            ReviewableProposal.act(action_type="observe", summary="观察屏幕", payload={})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_neo_aspect_core.NeoAspectHumanOpsTests -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'human_ops'`.

- [ ] **Step 3: Implement Human Ops preview and approval models**

Create `human_ops/previews.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class PreviewKind(str, Enum):
    RED_DOT = "red_dot"
    TEXT = "text"
    HOTKEY = "hotkey"
    PATH = "path"


@dataclass(frozen=True)
class ClickPreview:
    x: int
    y: int
    label: str = ""
    kind: PreviewKind = PreviewKind.RED_DOT

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "marker": "red_dot",
            "x": int(self.x),
            "y": int(self.y),
            "label": str(self.label or ""),
        }


def red_dot_click_preview(*, x: int, y: int, label: str = "") -> ClickPreview:
    return ClickPreview(x=int(x), y=int(y), label=str(label or ""))
```

Create `human_ops/approvals.py`:

```python
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
    "hotkey",
    "scroll",
    "drag",
    "launch_app",
    "focus_window",
    "clipboard_write",
    "wait",
}


@dataclass(frozen=True)
class ReviewableProposal:
    proposal_type: str
    summary: str
    payload: dict[str, Any]
    requirement: ApprovalRequirement = ApprovalRequirement.REQUIRED
    preview: ClickPreview | None = None
    approved: bool = False

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
```

Create `human_ops/__init__.py`:

```python
from .approvals import ApprovalRequirement, ReviewableProposal
from .previews import ClickPreview, PreviewKind, red_dot_click_preview

__all__ = [
    "ApprovalRequirement",
    "ClickPreview",
    "PreviewKind",
    "ReviewableProposal",
    "red_dot_click_preview",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_neo_aspect_core.NeoAspectHumanOpsTests -v
```

Expected: PASS with 3 tests.

## Task 3: Reviewed Memory And Skill Recipe Models

**Files:**
- Create: `memory/__init__.py`
- Create: `memory/review.py`
- Create: `skills/__init__.py`
- Create: `skills/recipes.py`
- Modify: `tests/test_neo_aspect_core.py`

- [ ] **Step 1: Add failing tests for reviewed memory and skill proposals**

Append to `tests/test_neo_aspect_core.py`:

```python
from memory.review import MemoryProposal
from skills.recipes import SkillRecipeProposal


class NeoAspectMemorySkillTests(unittest.TestCase):
    def test_memory_proposal_requires_review_before_save(self) -> None:
        proposal = MemoryProposal(category="preference", text="用户喜欢直接的技术结论")

        self.assertFalse(proposal.approved)
        self.assertTrue(proposal.requires_review)
        self.assertEqual(proposal.to_dict()["category"], "preference")
        self.assertTrue(proposal.approve().approved)

    def test_skill_recipe_contains_reviewable_operation_recipe(self) -> None:
        proposal = SkillRecipeProposal(
            name="Use Codex for project edits",
            triggers=["用户要求修改代码", "用户要求修复 bug"],
            steps=["观察 Codex 窗口", "输入任务", "等待结果", "读取 diff"],
            cautions=["不要自动提交", "删除文件前询问用户"],
            verification="确认 Codex 输出和测试结果",
        )

        self.assertFalse(proposal.approved)
        self.assertEqual(proposal.to_dict()["name"], "Use Codex for project edits")
        self.assertIn("读取 diff", proposal.steps)
        self.assertTrue(proposal.approve().approved)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_neo_aspect_core.NeoAspectMemorySkillTests -v
```

Expected: FAIL with `ModuleNotFoundError` for `memory.review` or `skills.recipes`.

- [ ] **Step 3: Implement reviewed memory and skill recipe models**

Create `memory/review.py`:

```python
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
```

Create `memory/__init__.py`:

```python
from .review import MemoryProposal

__all__ = ["MemoryProposal"]
```

Create `skills/recipes.py`:

```python
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
```

Create `skills/__init__.py`:

```python
from .recipes import SkillRecipeProposal

__all__ = ["SkillRecipeProposal"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_neo_aspect_core.NeoAspectMemorySkillTests -v
```

Expected: PASS with 2 tests.

## Task 4: Neo Aspect Settings Schema

**Files:**
- Create: `app/__init__.py`
- Create: `app/settings_schema.py`
- Modify: `tests/test_neo_aspect_core.py`

- [ ] **Step 1: Add failing tests for retained web settings content**

Append to `tests/test_neo_aspect_core.py`:

```python
from app.settings_schema import neo_aspect_settings_sections


class NeoAspectSettingsTests(unittest.TestCase):
    def test_settings_sections_match_new_architecture(self) -> None:
        sections = neo_aspect_settings_sections()
        section_ids = [item["id"] for item in sections]

        self.assertEqual(section_ids, ["body", "brain", "human_ops", "memory", "skills", "diagnostics"])

    def test_settings_schema_removes_runtime_management_content(self) -> None:
        text = repr(neo_aspect_settings_sections()).lower()

        self.assertNotIn("astrbot", text)
        self.assertNotIn("hermes", text)
        self.assertNotIn("mcp", text)
        self.assertNotIn("runtime sidecar", text)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_neo_aspect_core.NeoAspectSettingsTests -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app'`.

- [ ] **Step 3: Implement settings schema**

Create `app/settings_schema.py`:

```python
from __future__ import annotations


def neo_aspect_settings_sections() -> list[dict[str, object]]:
    return [
        {
            "id": "body",
            "title": "Body",
            "items": [
                "Live2D model",
                "expressions",
                "motions",
                "chat bubble",
                "voice",
                "desktop window",
            ],
        },
        {
            "id": "brain",
            "title": "Brain",
            "items": [
                "model endpoint",
                "model name",
                "API key storage",
                "persona profile",
                "response style",
                "single-step decision tuning",
            ],
        },
        {
            "id": "human_ops",
            "title": "Human Ops",
            "items": [
                "screen observation",
                "accessibility permission",
                "click preview red dot",
                "action review defaults",
                "allowed action types",
            ],
        },
        {
            "id": "memory",
            "title": "Memory",
            "items": [
                "conversation saving",
                "long-term memory",
                "user preferences",
                "relationship memory",
                "review queue",
            ],
        },
        {
            "id": "skills",
            "title": "Skills",
            "items": [
                "recipe list",
                "proposal queue",
                "enable or disable recipes",
                "edit recipes",
                "review history",
            ],
        },
        {
            "id": "diagnostics",
            "title": "Diagnostics",
            "items": [
                "local health",
                "last observation",
                "last approved action",
                "last failed action",
            ],
        },
    ]
```

Create `app/__init__.py`:

```python
from .settings_schema import neo_aspect_settings_sections

__all__ = ["neo_aspect_settings_sections"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_neo_aspect_core.NeoAspectSettingsTests -v
```

Expected: PASS with 2 tests.

## Task 5: Phase 1 Regression And Commit

**Files:**
- All files from Tasks 1-4.

- [ ] **Step 1: Run the full Neo Aspect core test file**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m unittest tests.test_neo_aspect_core -v
```

Expected: PASS with 11 tests.

- [ ] **Step 2: Run syntax compilation for new modules**

Run:

```bash
/Users/lyj/lyj/third_round/Ipet/Ipet/.venv/bin/python -m py_compile brain/decisions.py human_ops/previews.py human_ops/approvals.py memory/review.py skills/recipes.py app/settings_schema.py
```

Expected: exit status 0.

- [ ] **Step 3: Check diff hygiene**

Run:

```bash
git diff --check
```

Expected: no output and exit status 0.

- [ ] **Step 4: Commit Phase 1**

Run:

```bash
git add brain human_ops memory skills app tests/test_neo_aspect_core.py docs/superpowers/specs/2026-05-24-ipet-body-first-architecture-design.md docs/superpowers/plans/2026-05-24-neo-aspect-refactor.md
git commit -m "feat: start neo aspect refactor"
```

Expected: local commit created on `Neo_Aspect`. Existing unrelated modified files remain unstaged.

## Follow-Up Plans

Future plans should remove old architecture in aggressive but testable slices:

1. Split Body out of `main.py` and `index.html`.
2. Move vision capture into Human Ops observation modules.
3. Replace settings page content with the Neo Aspect settings schema while preserving visual style.
4. Replace runtime-proxy chat with Brain single-step controller routes.
5. Migrate conversation and memory storage into the new Memory boundary.
6. Delete runtime adapters, agent graph, MCP management, and old external skill management after replacement routes pass.
