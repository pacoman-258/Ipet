# Optional Subagent Operating Model

Single-agent work is the default. This document applies only when the user explicitly requests subagents or a higher-level instruction requires delegation.

## Coordination Rules

- Split only independent work that can proceed without shared edits.
- Assign one lead for each high-conflict file: `main.py`, `index.html`, and `backend/app.py`.
- Entry points remain composition-only; workers place behavior in the owning module.
- Each worker stays inside its assigned slice and preserves unrelated dirty work.
- The coordinator integrates results and runs the smallest relevant verification.

## Optional Roles

- `body`: observation, voice I/O, presentation, and Body tests.
- `brain`: model calls, prompt contracts, decisions, and Brain tests.
- `human-ops`: approvals, rejection, execution records, and safety tests.
- `memory-skills`: memory, summaries, preferences, skills, and persistence tests.
- `settings-console`: settings UI and settings API contracts.
- `desktop-shell`: desktop composition, Qt lifecycle, bridge wiring, and host processes.
- `qa-reports`: regression planning, documentation audits, and durable reports when explicitly needed.

## Cross-Boundary Work

One side leads and the other reviews only when the task actually crosses that boundary:

- `main.py` and `index.html`: `desktop-shell` leads; `body` reviews.
- Settings UI and backend settings API: either side leads; the other reviews.
- Brain decision events: `brain` leads; affected Body or Human Ops owners review.
- Approval and execution: `human-ops` leads; affected Brain owner reviews.
- Memory or skill persistence: `memory-skills` leads; affected Brain owner reviews.

Do not spawn reviewers merely to satisfy this list. A focused local test is enough when no independent review task exists.

## Prompt Library

Role prompts live in `docs/subagents/`. Read only the prompt for the assigned role. Worker handoffs should contain the changed scope, evidence, verification, and unresolved risks; no HTML report is required unless the task explicitly requests one.
