# Brain ReAct Pre-Approval Design

## Purpose

Brain should not end a desktop operation turn before it has either advanced the user's goal to a Human Ops approval proposal or clearly explained why it cannot proceed. Recent click tasks showed the current one-step Brain contract can still stop early: it may observe a target, describe it, and return `say` even though the user's actual goal was to click or open something.

This design adds an explicit, inspectable ReAct contract for pre-approval desktop operation tasks. ReAct here means a bounded cycle of reasoning state, observation, and next-step selection before any reviewed action. It does not give Brain permission to execute side effects.

## Scope

First implementation scope:

- Applies to user requests that look like desktop operation goals, especially click, open, focus, and UI operation requests.
- Covers the phase before Human Ops approval.
- Allows Brain to make progress through explicit `think` and `observe` decisions.
- Stops immediately when Brain returns `propose_act`; Human Ops then shows the review UI and red-dot preview.
- Prevents `say` or `stop` from ending an operation goal while Brain itself reports the goal is still in progress.

Out of scope for this phase:

- Post-approval verification after Body executes a click.
- Multi-action tasks such as "open Chrome and search X".
- Automatic execution of any reviewed action.
- Memory and skill ReAct loops.
- A frontend redesign of the chat timeline.

Post-approval verification should be a separate phase. It can reuse the same `goal` field and ReAct events, but it needs its own execution-result observation boundary.

## Existing Boundary

The current product boundary remains valid:

- Body observes and executes approved actions.
- Brain decides one next step.
- Human Ops reviews risky effects.
- Memory & Skills handles durable learning.

The ReAct loop is a backend orchestration layer around repeated one-step Brain calls. Brain still returns only one JSON object at a time. The backend decides whether another no-side-effect ReAct step is allowed.

## Decision Model

Add a new executable Brain decision kind:

```text
think
```

`think` is not user-visible final text and has no side effect. It lets Brain state the current goal status and the next safe step in a structured way.

The active decision kinds become:

- `say`: final user-facing answer or clarification request.
- `think`: internal ReAct progress state.
- `observe`: request Human Ops observation.
- `propose_act`: request Human Ops approval for one action.
- `propose_remember`: reserved reviewed memory proposal.
- `propose_learn_skill`: reserved reviewed skill proposal.
- `stop`: terminate a goal with an explicit status.

`think` and `observe` do not require review. `propose_act`, `propose_remember`, and `propose_learn_skill` still require review.

## Goal Field

Every Brain decision may include a lightweight `goal` object. For desktop operation requests, the system prompt requires it.

```json
{
  "goal": {
    "objective": "打开 Dock 里的 Chrome",
    "status": "in_progress",
    "evidence": ["已看到 Dock", "Chrome 图标尚未定位到可信中心点"],
    "missing": ["Chrome 图标中心点的 macOS 屏幕坐标"],
    "next": "observe"
  }
}
```

Allowed `goal.status` values:

- `in_progress`: the user goal is not yet ready to end.
- `ready_for_review`: Brain has enough information to propose a reviewed action.
- `done`: the user goal is complete without further action.
- `blocked`: Brain cannot proceed with available context or tools.
- `need_user`: Brain needs user clarification.
- `handoff_review`: Brain has returned `propose_act` and Human Ops owns the next step.

The backend treats missing `goal.status` on operation requests as `in_progress` until a safe terminal decision is proven.

## Structured Replies

Example `think`:

```json
{
  "kind": "think",
  "goal": {
    "objective": "打开 Dock 里的 Chrome",
    "status": "in_progress",
    "evidence": ["用户要求打开 Chrome", "当前还没有屏幕观察"],
    "missing": ["Dock 中 Chrome 图标位置"],
    "next": "observe"
  },
  "thought": "需要先观察屏幕，找到 Chrome 图标的可点击中心点。",
  "next_kind": "observe"
}
```

Example `observe`:

```json
{
  "kind": "observe",
  "goal": {
    "objective": "打开 Dock 里的 Chrome",
    "status": "in_progress",
    "evidence": ["需要找到 Dock 图标"],
    "missing": ["Chrome 图标中心点 macOS 坐标"],
    "next": "observe"
  },
  "target": "screen",
  "question": "请找到 Dock 中 Chrome 图标的视觉中心，说明相邻图标依据，并返回可点击中心点的 macOS 屏幕坐标 x/y。"
}
```

Example `propose_act`:

```json
{
  "kind": "propose_act",
  "goal": {
    "objective": "打开 Dock 里的 Chrome",
    "status": "handoff_review",
    "evidence": ["Chrome 图标中心点坐标可信"],
    "missing": [],
    "next": "human_ops_review"
  },
  "action_type": "click",
  "arguments": {"x": 382, "y": 930, "label": "Dock Chrome 图标"}
}
```

`say` may end an operation goal only when `goal.status` is `done`, `blocked`, or `need_user`.

`stop` must include `goal.status`. It is not allowed to hide an unfinished operation goal.

## Backend ReAct Orchestration

Introduce a small pre-approval ReAct runner inside the chat stream path, or as a helper called by it.

Inputs:

- original user text
- Brain provider config
- Human Ops observe config
- current request system prompt
- session id and turn id

Loop rules:

1. Start with one Brain call using the original user text.
2. Parse the decision and goal.
3. If decision is `think`, call Brain again with a ReAct follow-up prompt that includes the thought, goal state, and remaining budget.
4. If decision is `observe`, perform Human Ops observe, then call Brain again with observation text and coordinate context.
5. If decision is `propose_act`, create the Human Ops approval proposal and stop the loop.
6. If decision is `say` or `stop`, allow it only when the goal status is terminal: `done`, `blocked`, or `need_user`.
7. If decision is `say` or `stop` while the goal is `in_progress`, do not emit it as final. Call Brain once more with a correction prompt: the goal is unfinished, so it must choose `observe`, `propose_act`, `need_user`, or `blocked`.
8. Stop after a small fixed budget, initially three no-side-effect ReAct steps after the first Brain call. If no proposal is ready, emit a user-facing `say` explaining what is missing.

Only these decisions may be auto-continued:

```text
think
observe
invalid say/stop with in_progress goal, once per turn
```

No reviewed action is auto-executed.

## Human Ops Boundary

`propose_act` remains the hard stop:

- The backend creates a reviewable Human Ops proposal.
- The UI displays the red-dot preview for click actions.
- The stream returns `approval_required`.
- Body executes only after explicit user approval.

The ReAct runner must not click, type, launch, write memory, or mutate settings.

## SSE and UI

First version can reuse existing stream events:

- `phase`: emit `neo_brain`, `brain_react`, `human_ops_observe`, and `human_ops_review`.
- `display_segment`: show observation summaries when useful.
- `approval_required`: unchanged for Human Ops proposals.
- `done`: include the final decision and, when applicable, a compact `react_trace`.

The UI does not need a new layout for this phase. Existing worklog rendering can show the phase messages.

## Error Handling

- Brain provider failure before any observation returns a sanitized `say`.
- Observe model failure returns a sanitized `say` with `goal.status=blocked`.
- Invalid JSON from Brain is parsed by existing fallback rules, but operation requests without a terminal goal are treated as unfinished.
- Repeated `think` without new missing information consumes budget and then returns a blocked summary.
- Repeated `observe` with equivalent missing coordinate evidence consumes budget and then returns a blocked summary.

## Prompt Requirements

The Brain system prompt must explain:

- ReAct is explicit and bounded.
- `think` is for internal progress, not user-facing completion.
- Operation goals need a `goal` object.
- `say` is not a completion path for an unfinished operation goal.
- `propose_act` is a Human Ops handoff, not execution.
- When coordinates are needed, ask Observe for macOS screen coordinates and visible evidence.
- If an observation is inconsistent or insufficient, continue observing or request clarification instead of guessing.

## Tests

Minimum tests for implementation:

- `BrainDecision.think` exists, is parsed, and does not require review.
- Brain structured prompt documents `think`, `goal.status`, and the `say`/unfinished-goal rule.
- Chat stream auto-continues from `think` to `observe`.
- Chat stream auto-continues from `observe` back to Brain.
- Chat stream blocks `say` as a final answer when a click/open goal is still `in_progress`.
- Chat stream stops and emits `approval_required` when ReAct reaches `propose_act`.
- ReAct budget prevents infinite `think` or `observe` loops.
- Existing Human Ops click approval and red-dot preview tests still pass.

## Rollout

Implement behind the existing chat stream path without a settings toggle. The behavior should be conservative:

- Pure chat remains unchanged.
- Operation requests get explicit ReAct handling.
- If the Brain model ignores the new schema, existing fallback behavior remains, with stricter unfinished-goal handling for operation requests.

The next separate design can cover post-approval verification after Body executes an approved action.
