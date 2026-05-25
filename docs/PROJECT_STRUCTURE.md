# Project Structure

## Goal

Keep the Neo Aspect repository easy to scan by grouping product responsibilities around four modules:

- Body
- Brain
- Human Ops
- Memory & Skills

The codebase is in transition, so this document describes both target homes and current transition-period locations.

## Target Layout

```text
Ipet/
|-- body/                    # desktop sensing, speaking, voice, observation, presentation commands
|-- brain/                   # LLM turn decision, prompt contracts, decision tests
|-- human_ops/               # approvals, action review, execution records, safety prompts
|-- memory/                  # durable memory, summaries, preferences, retention rules
|-- skills/                  # built-in skills, learned skills, skill manifests and scripts
|-- app/                     # composition layer connecting Body, Brain, Human Ops, Memory & Skills
|-- frontend/                # future home for pet UI and settings UI
|-- backend/                 # transition-period Python API surface
|-- docs/                    # architecture, workflow, ownership, reports, role playbooks
|-- tests/                   # unit and regression tests
|-- scripts/                 # diagnostics, smoke checks, and developer tools
|-- model/                   # tracked demo/reference pet assets plus ignored local assets
|-- prompts/                 # reusable prompt/persona assets during migration
|-- index.html               # transition-period pet UI entry
|-- settings.html            # transition-period settings UI entry
|-- settings.css             # transition-period settings styles
|-- settings.js              # transition-period settings behavior
|-- main.py                  # transition-period desktop host entry
|-- pet_config.example.json  # safe example config
|-- pyproject.toml           # project metadata and dependencies
|-- uv.lock                  # locked dependency graph
|-- AGENTS.md                # shortest safe coding-agent entrypoint
|-- README.md                # English project README
|-- README.zh-CN.md          # Chinese Markdown README
`-- README.html              # Chinese human-readable HTML README
```

## Ownership

- **Body** owns the desktop host behavior, local observation, voice I/O, pet presentation, and device-facing commands.
- **Brain** owns the LLM turn packet, one-step decision schema, prompt rules, and decision tests.
- **Human Ops** owns approval prompts, rejection handling, execution ledgers, and safety review for risky actions.
- **Memory & Skills** owns conversation memory, summaries, user preferences, local skills, and learned procedures.
- **Settings Console** owns settings UI files and settings API contracts.
- **Desktop Shell** owns `main.py` while the root host remains the entrypoint.
- **QA Reports** owns regression planning, contract checks, and HTML reports.

## Root UI Transition

`index.html`, `settings.html`, `settings.css`, and `settings.js` intentionally remain at the repository root for now. The desktop host and Python API still load them from those paths. Move them only when the loading paths are changed in the same task and tests or smoke checks cover the new paths.

Until then:

- do not duplicate root UI files into `frontend/`
- do not create a second settings entrypoint
- document UI path changes in `docs/WORKFLOW.md`
- include path migration details in the task HTML report

## Root Conventions

- Keep root files purposeful: README files, config examples, lockfiles, metadata, and runnable entrypoints.
- Do not add one-off generated text, hotspot reports, local scratch files, logs, caches, or personal runtime state at the root.
- Use `scripts/debug/` for manual diagnostics and smoke scripts.
- Use `docs/reports/` for long-form reports and post-task HTML summaries.
- Use target module directories for new code when the implementation owner confirms the migration path.

## Hygiene Rules

- `pet_config.json`, `.pet_runtime_*`, `.venv/`, `.uv-cache/`, runtime logs, generated audio, local conversation state, and local caches stay ignored.
- Generated file-playground output belongs under ignored scratch locations, not in Git.
- `model/` contains tracked demo/reference pet assets. Private, large, generated, or downloaded model resources should remain local and ignored before commit.
- Do not commit API keys, local absolute paths, screenshot payloads, generated audio, downloaded toolchains, or temporary analyzer outputs.
- If a workflow change alters Body observation, Brain decisions, Human Ops approval, Memory & Skills persistence, settings contracts, or frontend chat behavior, update `docs/WORKFLOW.md` in the same task.

## Test Command

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```
