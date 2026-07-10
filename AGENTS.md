# Agent Development Guide

## Security First

- Any potentially risky command must be explained before execution.
- The explanation must include:
  - why the command is needed
  - what it may change or affect
  - whether it touches files, processes, configuration, network access, or destructive operations
- Commands with meaningful risk must not be executed silently.
- Risky actions must be handed to the user for approval first.
- Examples that require explicit approval include:
  - deleting or overwriting files
  - installing, updating, or removing dependencies
  - networked downloads, git push, or external service calls
  - process termination, system-level changes, or commands that may alter local environment state
- If a safer read-only or lower-impact path exists, prefer that path first.

This file is the shortest safe entrypoint for coding agents working in this repository.

## Quick Start

- Product model: Ipet Neo Aspect has four current modules: Body, Brain, Human Ops, and Memory & Skills.
- Current implementation shape: `main.py`, `backend/app.py`, and `index.html` are compatibility entrypoints for desktop composition, API composition, and root UI loading. Domain behavior lives in `app/`, `body/`, backend route/helper/adapter modules, and `frontend/` controllers.
- Module homes: `body/`, `brain/`, `human_ops/`, `memory/`, `skills/`, `app/`, `frontend/`, `backend/`, `docs/`, and `tests/`.
- Primary stack: Python, Qt WebEngine, FastAPI, local HTML/CSS/JS, LLM calls, speech I/O, local screen observation, and Live2D-style assets.
- The currently verified dev runtime is the project `.venv` on Python 3.12.
- Preferred local start command:

```powershell
uv run --no-sync python main.py
```

- Default test command:

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```

- Every completed task round must generate an HTML report in `docs/reports/`.
- Do not start by scanning the whole repo. Read the minimum docs below, then jump to task-specific files.
- Qt binding order is fixed: prefer `PySide6`, fall back to `PyQt6`.

## Read Order

Read in this order unless the task is extremely narrow:

1. `README.md`
2. `docs/PROJECT_STRUCTURE.md`
3. `docs/SUBAGENTS.md`
4. `docs/WORKFLOW.md` when behavior, approvals, memory, skills, or observation changes
5. Only the 1-2 files directly related to the task

Useful stable facts:

- Body owns the desktop shell, pet presentation, voice I/O, local observation, and device-facing commands.
- Brain owns one-step LLM decision making for a user turn.
- Human Ops owns approval prompts, rejection handling, execution records, and safety review.
- Memory & Skills owns local memory, summaries, user preferences, skill definitions, and learned procedures.
- Root UI entry files stay at the repository root because the desktop host and backend load those paths. Keep them as loading and document surfaces; put interactive behavior in `frontend/`.
- Tests live under `tests/`.
- Debug helpers live under `scripts/debug/`.
- Fixed role prompts live under `docs/subagents/`.
- Root scratch files, generated output, local app state, caches, and personal configs should be ignored or removed instead of documented as project structure.

## Hot Files And Ownership

These are high-conflict, composition-only files. Only one implementation owner should lead changes to each in a task:

- `main.py`
- `index.html`
- `backend/app.py`

Current ownership map:

- `desktop-shell`: `main.py` composition/bootstrap, process lifecycle, Qt/WebEngine bridge composition, tray/menu behavior, compatibility wrappers
- `body`: Body module code, observation behavior, voice I/O, presentation commands, Body tests
- `brain`: Brain decision code, prompt contracts, model call boundaries, Brain tests
- `human-ops`: approvals, action review, execution ledgers, user confirmation flows, safety tests
- `memory-skills`: memory stores, summaries, skill discovery, learned procedures, skill tests
- `settings-console`: `settings.html`, `settings.css`, `settings.js`, settings API contracts
- `qa-reports`: regression planning, contract checks, targeted tests, HTML reports

Cross-boundary rules:

- `main.py`, `backend/app.py`, and `index.html` must remain composition-only. New domain behavior belongs in the owning `app/`, `body/`, `backend/`, or `frontend/` module; leave only registration, dependency wiring, loading, or a compatibility delegate in the entrypoint.
- `main.py` <-> `index.html`: lead `desktop-shell`, review by `body`.
- `settings.js` <-> backend settings APIs: one side leads, the other reviews.
- Brain decision event changes: lead `brain`, review by `body`, `human-ops`, and `qa-reports`.
- Approval or action execution changes: lead `human-ops`, review by `brain` and `qa-reports`.
- Memory or skill persistence changes: lead `memory-skills`, review by `brain` and `qa-reports`.
- Documentation-only cleanup: lead `qa-reports`, with implementation owners consulted only when behavior or ownership boundaries change.

## Task Routing Cheatsheet

Use this map before opening large files:

| Task type | Read first | Minimum follow-up |
| --- | --- | --- |
| Desktop host, tray, Qt bridge, window behavior | matching `app/desktop_*` module | `main.py` only for composition, lifecycle, or compatibility wiring |
| Pet UI, chat window, display state, TTS playback UX | matching `frontend/` controller | `index.html` only for document structure or script loading; related API route only if its contract changed |
| Backend API route, dependency, or compatibility export | matching `backend/*_routes.py`, helper, or adapter module | `backend/app.py` only for middleware, dependency wiring, router registration, or compatibility export installation |
| Body observation, screenshots, ASR/TTS, local command bridge | `docs/WORKFLOW.md` | `body/` or current matching `backend/vision*.py`, `backend/asr*.py` slice |
| Brain turn decision, prompt contract, model call boundary | `docs/architecture.md` | `brain/` or current matching backend slice |
| Human Ops approval and execution safety | `docs/WORKFLOW.md` | `human_ops/` or current approval/action route slice |
| Memory, summaries, topic continuity | `memory/` | current matching persistence slice and UI contract |
| Skills import, skill execution, learned procedures | `skills/` | `memory/` and related tests |
| Settings page | `settings.js` | `settings.html`, `settings.css`, relevant settings route |
| Repository organization, docs, cleanup | `docs/PROJECT_STRUCTURE.md` | `README.md`, `README.zh-CN.md`, `docs/WORKFLOW.md`, `.gitignore` |

## Token-Saving Rules

- Prefer `rg` or `rg --files` to locate symbols, routes, tests, and feature names before opening files.
- Inspect only the smallest useful slice: use line ranges, focused snippets, diffs, or summaries instead of full-file reads.
- Do not read all of `index.html` or `main.py` unless the task truly lives there. Search first, then open the relevant block.
- Do not open every test file. Pick the smallest matching regression slice from the matrix below.
- Read model, schema, or request types before implementations when tracing behavior.
- When a task touches one subsystem, stay inside that subsystem until an interface boundary forces expansion.
- For logs, JSON, CSV, HTML reports, and other large artifacts, create or inspect a compact summary first, then read only necessary raw slices.
- Use `python scripts/context_summary.py` before exploring broad project context. Prefer targeted modes: `--repo`, `--reports`, `--data`, or `--logs`.
- When investigating `docs/reports/`, local `data/`, or log directories, run the matching `scripts/context_summary.py` mode first and only open raw files that the summary makes relevant.
- Default noisy paths to skip: `.venv/`, `.uv-cache/`, `__pycache__/`, `.service-logs/`, `.app-logs/`, `.cache/`, generated outputs, local app state under ignored `data/` paths, and large historical `docs/reports/` files unless the task is about them.
- Cap unknown command output. Prefer focused commands such as `git status --short`, `git diff --name-only`, `rg PATTERN PATH`, and `sed -n 'START,ENDp' FILE`.
- Use `git status --short` before cleanup. Treat unrelated modified/untracked files as user work and never revert them.
- Deleting tracked files, ignored local app state, caches, or generated outputs is destructive. Explain why, what it affects, and ask for approval unless the user explicitly named the exact deletion target.

## HTML Report Rule

Every round that changes files must create or update an HTML report under `docs/reports/`. The report must include:

- goal
- worker or subagent split
- files changed
- achieved effect
- remaining work
- recommended next step

Do not mark a task complete until the report exists and the relevant verification command has been run.

## Regression Matrix

Run the smallest useful set first.

### Body

```powershell
python -m unittest tests.test_active_vision tests.test_vision_analyzer tests.test_vision_service tests.test_vision_state tests.test_asr_api tests.test_asr_service tests.test_asr_server_api -v
```

### Brain

```powershell
python -m unittest tests.test_brain_llm_providers tests.test_brain_structured_replies tests.test_neo_backend_contract -v
```

### Human Ops

```powershell
python -m unittest tests.test_neo_backend_contract tests.test_chat_modes_ui tests.test_neo_aspect_core -v
```

### Memory & Skills

```powershell
python -m unittest tests.test_chat_topics tests.test_chat_topics_api tests.test_ipet_memory_store tests.test_neo_aspect_core -v
```

### Settings And UI Contracts

```powershell
python -m unittest tests.test_neo_aspect_settings_page tests.test_chat_modes_ui tests.test_neo_backend_contract -v
```

### Documentation Audit

Run the Neo forbidden-term audit requested by the task owner against README, AGENTS, workflow, structure, ownership, and architecture docs. Expected result for Neo Aspect docs: no output.

### Full Regression

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```
