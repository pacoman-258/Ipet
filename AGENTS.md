# Agent Development Guide

This is the canonical development guide for every coding agent and model used in this repository. Tool-specific entry files may import it, but must not copy or redefine its project rules. Start from the user request, locate the smallest relevant code slice, and open other docs only when the routing table below requires them.

## Cross-Tool Collaboration Contract

- Repository instructions, checked-in configuration, and nearby code define the style. Do not impose a model vendor's preferred architecture, naming, formatting, or workflow.
- One working tree has one writer by default. Parallel writers are allowed only after the user assigns disjoint file or subsystem ownership; one lead owns every shared entrypoint, schema, generated artifact, and other high-conflict file.
- Before writing, run `git status --short`, inspect the current file and its relevant diff, and state the intended file slice. Treat unfamiliar changes as another contributor's work.
- Change only the owned slice. Do not revert, overwrite, broadly reformat, regenerate, or "clean up" unrelated work. If the required edit overlaps an unowned or unclear change, stop and ask for ownership instead of guessing.
- Re-read a shared file immediately before patching it. If it changed since inspection, reconcile the new content and preserve both intents; never replace the file from a stale copy.
- Keep patches responsibility-complete: when an interface changes, update its directly affected producer, consumer, and focused contract test together. Do not expand into adjacent refactors without authorization.
- Ownership ends with a handoff that names changed files, checks run and their results, plus any unresolved risk. A plan, command dispatch, or partial test signal is not a completed handoff.
- `AGENTS.md` is the single source of project-wide agent rules. Tool adapters such as `CLAUDE.md` must stay thin and point here; tool-only mechanics may be added only when they do not restate or contradict this guide.

## Safety And Authorization

- An explicit implementation request authorizes edits inside the requested scope plus local formatting and verification.
- Ask before destructive deletion or replacement of user data or unrelated work, dependency installation or updates, network downloads or external writes, `git push`, process termination, system configuration, or a material scope expansion.
- Before a risky action, explain why it is needed, the exact target, what it can affect, and how success or failure will be checked.
- Read-only inspection and bounded local diagnostics do not need separate approval.
- Prefer the lower-impact path. Never revert unrelated dirty work.

## Working Rules

- Run `git status --short` before cleanup or broad edits. Treat existing changes as user work.
- Use `rg`, focused line ranges, summaries, and diffs before reading large files or directories.
- Skip `.venv/`, `node_modules/`, build output, caches, generated files, large logs, and historical reports unless the task targets them.
- Stay inside one subsystem until an actual interface boundary requires expansion.
- Reuse existing modules, standard library, platform-native capabilities, and installed dependencies. Add no entity without an independent responsibility.
- State facts from evidence. Do not present a plan, command dispatch, or partial signal as a verified result.

## Architecture Boundaries

- `main.py`, `backend/app.py`, and `index.html` are composition-only compatibility entrypoints. Domain behavior belongs in `app/`, `body/`, `backend/` helpers/routes/adapters, or `frontend/` controllers.
- Body owns desktop presentation, observation, voice, and device-facing commands.
- Brain owns one-step LLM decisions and model boundaries.
- Human Ops owns product approval, rejection, execution safety, and action records.
- Memory & Skills owns durable memory, preferences, summaries, and learned procedures.
- Settings Console owns the root settings UI and its API contract.
- When delegation is explicitly requested, assign one lead per high-conflict file and preserve every other worker's slice.
- Qt binding order is fixed: prefer `PySide6`, then fall back to `PyQt6`.

## Read Only When Needed

| Task | Read first |
| --- | --- |
| Product runtime behavior, approval, observation, memory, or skills | `docs/WORKFLOW.md` |
| Brain or cross-module architecture contracts | `docs/architecture.md` |
| Repository layout or path migration | `docs/PROJECT_STRUCTURE.md` |
| Explicit multi-agent work | `docs/SUBAGENTS.md`, then only the assigned role prompt |
| Settings UI | `settings.js`, then the matching route or markup slice |
| Other implementation work | the matching source and smallest relevant tests |

README files are human-facing overviews, not mandatory agent pre-reading.

## Verification

- Run the smallest relevant test or contract check first. Full regression is not the default.
- For documentation-only changes, use `git diff --check` plus focused `rg` checks for the rule or term being changed.
- Update `docs/WORKFLOW.md` only when a stable product contract, safety boundary, or state transition changes; implementation details belong in code and tests.
- Do not claim completion until the requested effect and the relevant check both succeed.

Useful commands:

```powershell
uv run --no-sync python main.py
python -m unittest discover -s tests -p "test*.py" -v
```

## Reports

Routine tasks do not create HTML reports. Use `docs/reports/` only when the user asks for a durable report or when a security assessment, migration, release, destructive refactor, or major cross-module architecture change genuinely needs one.
