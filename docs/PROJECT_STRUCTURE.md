# Project Structure

## Goal

Keep the repository easy to scan at a glance:

- root: runnable entrypoints and essential project files
- `backend/`: application logic and APIs
- `docs/`: human-facing documentation
- `tests/`: automated verification
- `scripts/`: one-off tooling and diagnostics
- `prompts/`: reusable prompt assets

## Current Layout

```text
AI_assistant/
├─ backend/               # FastAPI, agent runtime, MCP bridge, TTS, tooling
├─ docs/                  # reports, structure notes, subagent playbooks
├─ js/                    # frontend vendor/runtime assets used by index.html
├─ prompts/               # reusable prompt JSON assets
├─ scripts/               # manual utilities and debug helpers
├─ tests/                 # unit and regression tests
├─ third_party_mcp/       # third-party MCP manifests and local installs
├─ index.html             # desktop pet runtime UI entry
├─ settings.html          # browser settings UI entry
├─ settings.css           # settings page styles
├─ settings.js            # settings page behavior
├─ main.py                # desktop host entry
├─ pyproject.toml         # project metadata and dependencies
├─ uv.lock                # locked dependency graph
├─ README.md
└─ README.zh-CN.md
```

## Conventions

- Do not move `main.py`, `index.html`, `settings.html`, `settings.css`, or `settings.js` without updating the desktop host and backend loading paths.
- New automated tests should go under `tests/`.
- One-off verification scripts should go under `scripts/debug/`.
- Long-form reports belong in `docs/reports/`.
- Role/process docs belong in `docs/` or `docs/subagents/`.
- Prompt or persona JSON assets should go under `prompts/`.

## Test Command

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```
