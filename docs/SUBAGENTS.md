# Subagent Operating Model

This repository uses fixed ownership roles for day-to-day development. Neo Aspect work should use the product module names below.

## Core Rule

- The coordinator routes work, assigns one lead, integrates results, and resolves conflicts.
- High-conflict files may only have one implementation owner in a task:
  - `main.py`
  - `index.html`
  - `backend/app.py`
- All three high-conflict entrypoints are composition-only. Workers must place new domain behavior in the owning `app/`, `body/`, `backend/`, or `frontend/` module and leave only wiring, loading, registration, lifecycle, or compatibility delegates in the entrypoint.
- Documentation-only workers may edit docs inside their assigned slice, but must not revert code owned by another worker.

## Fixed Roles

- `body`: owns local observation, voice I/O, pet presentation commands, Body API slices, and Body tests.
- `brain`: owns turn packets, one-step LLM decision schema, prompt contracts, decision summaries, and Brain tests.
- `human-ops`: owns approvals, rejection flow, execution review, action ledgers, and safety tests.
- `memory-skills`: owns local memory, summaries, preferences, built-in skills, learned skills, and skill tests.
- `settings-console`: owns `settings.html`, `settings.css`, `settings.js`, and settings API contracts.
- `desktop-shell`: owns `main.py` composition/bootstrap, startup/shutdown, Qt bridge composition, host-side process lifecycle, and compatibility wrappers.
- `qa-reports`: owns regression planning, contract checks, targeted test additions, documentation audits, and HTML reports.

## Dispatch Defaults

- Desktop host, tray, window behavior, host bridge: `desktop-shell`; implementation goes to matching `app/desktop_*` modules unless it is entrypoint wiring or lifecycle.
- Pet expression, motion, chat bubble presentation, local observation, ASR/TTS: `body`; frontend behavior goes to matching `frontend/` controllers, not `index.html`.
- Backend API routes and domain helpers: the owning product role; `backend/app.py` changes are limited to composition, router registration, and compatibility exports.
- Brain prompt, turn decision, model boundary, output schema: `brain`
- Approval prompt, risky execution, user rejection, safety copy: `human-ops`
- Memory write, summary layering, user preference, skill discovery or learning: `memory-skills`
- Settings page or settings contracts: `settings-console`
- Cross-module validation, docs audit, regression plan, report writing: `qa-reports`

## Cross-Boundary Rules

- Any change that would add domain logic to `main.py`, `backend/app.py`, or `index.html` must be split into an owner module in the same task. The assigned entrypoint owner only integrates the thin delegate, registration, or loader change.
- `main.py` <-> `index.html` contract changes:
  - lead: `desktop-shell`
  - reviewer: `body`
- `settings.js` <-> backend settings APIs:
  - lead may be `settings-console` or the backend module owner
  - reviewer: the other side plus `qa-reports`
- Brain decision event changes:
  - lead: `brain`
  - reviewers: `body`, `human-ops`, `qa-reports`
- Approval or execution changes:
  - lead: `human-ops`
  - reviewers: `brain`, `qa-reports`
- Memory or skill persistence changes:
  - lead: `memory-skills`
  - reviewers: `brain`, `qa-reports`
- Repository structure or documentation cleanup:
  - lead: `qa-reports`
  - consult implementation owners only when behavior or ownership boundaries change

## Report Requirement

Every file-changing task round must produce an HTML report in `docs/reports/`. The report should list:

- task goal
- worker split
- files changed
- achieved effect
- remaining work
- recommended next step

`qa-reports` verifies the report exists before calling the task complete.

## Prompt Library

Stable role prompts live in `docs/subagents/`.

- Coordinator playbook: `docs/subagents/coordinator.md`
- Team overview: `docs/subagents/README.md`
- Body: `docs/subagents/body.md`
- Brain: `docs/subagents/brain.md`
- Human Ops: `docs/subagents/human-ops.md`
- Memory & Skills: `docs/subagents/memory-skills.md`
- Settings Console: `docs/subagents/settings-console.md`
- Desktop Shell: `docs/subagents/desktop-shell.md`
- QA Reports: `docs/subagents/qa-reports.md`
