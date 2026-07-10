# Project Structure

## Goal

Keep the Neo Aspect repository easy to scan by grouping product responsibilities around four modules:

- Body
- Brain
- Human Ops
- Memory & Skills

Root entry paths remain stable for desktop compatibility, while implementation responsibilities live in their owning modules.

## Current Layout

```text
Ipet/
|-- body/                    # desktop sensing, speaking, voice, observation, presentation commands
|-- brain/                   # LLM turn decision, prompt contracts, decision tests
|-- human_ops/               # approvals, action review, execution records, safety prompts
|-- memory/                  # durable memory, summaries, preferences, retention rules
|-- skills/                  # built-in skills, learned skills, skill manifests and scripts
|-- app/                     # desktop composition and host support modules
|-- frontend/                # pet UI controllers and behavior modules
|-- backend/                 # FastAPI composition, routes, helpers, and adapters
|-- docs/                    # architecture, workflow, ownership, reports, role playbooks
|-- tests/                   # unit and regression tests
|-- scripts/                 # diagnostics, smoke checks, and developer tools
|-- model/                   # tracked demo/reference pet assets plus ignored local assets
|-- prompts/                 # reusable prompt/persona assets during migration
|-- index.html               # root pet UI document and script loader
|-- settings.html            # root settings UI entry
|-- settings.css             # root settings styles
|-- settings.js              # root settings behavior
|-- main.py                  # desktop composition/bootstrap entry
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
- **Desktop Shell** owns `main.py` composition/bootstrap, Qt lifecycle, host process lifecycle, and compatibility wrappers; desktop feature implementations belong in matching `app/` or `body/` modules.
- **Frontend UI** owns controllers and behavior under `frontend/`; `index.html` only owns the root document structure and script loading order.
- **Backend API** keeps `backend/app.py` limited to FastAPI setup, middleware, dependencies, router registration, and compatibility exports; routes and domain behavior belong in backend route/helper/adapter modules or their product module.
- **QA Reports** owns regression planning, contract checks, and HTML reports.

## Root UI Paths

`index.html`, `settings.html`, `settings.css`, and `settings.js` intentionally remain at the repository root because the desktop host and Python API load those paths. Move them only when the loading paths are changed in the same task and tests or smoke checks cover the new paths.

While these paths remain stable:

- keep `index.html` as a document and script loader; put interactive pet UI behavior in `frontend/`
- do not create a second settings entrypoint
- document UI path changes in `docs/WORKFLOW.md`
- include path migration details in the task HTML report

## Root Conventions

- Keep root files purposeful: README files, config examples, lockfiles, metadata, and runnable entrypoints.
- Do not add one-off generated text, hotspot reports, local scratch files, logs, caches, or personal app state at the root.
- Use `scripts/debug/` for manual diagnostics and smoke scripts.
- Use `docs/reports/` for long-form reports and post-task HTML summaries.
- Use the owning module directory for new domain code. Keep root entrypoints composition-only.

## Hygiene Rules

- `pet_config.json`, `.pet_desktop_command*.json`, `.pet_desktop_host.heartbeat.json`, `.venv/`, `.uv-cache/`, local logs, generated audio, local conversation state, and local caches stay ignored.
- Generated file-playground output belongs under ignored scratch locations, not in Git.
- `model/` contains tracked demo/reference pet assets. Private, large, generated, or downloaded model resources should remain local and ignored before commit.
- Do not commit API keys, local absolute paths, screenshot payloads, generated audio, downloaded toolchains, or temporary analyzer outputs.
- If a workflow change alters Body observation, Brain decisions, Human Ops approval, Memory & Skills persistence, settings contracts, or frontend chat behavior, update `docs/WORKFLOW.md` in the same task.

## Test Command

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```
