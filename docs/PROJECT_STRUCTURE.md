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
?? backend/               # FastAPI, topic history, ASR, agent runtime, MCP bridge, TTS, tooling
?? data/                  # persisted runtime state such as chat topics
?? docs/                  # reports, structure notes, subagent playbooks
?? js/                    # frontend vendor/runtime assets used by index.html
?? prompts/               # reusable prompt JSON assets
?? scripts/               # manual utilities and debug helpers
?? tests/                 # unit and regression tests
?? third_party_skills/    # imported skills and skill-local scripts/resources
?? third_party_mcp/       # third-party MCP manifests and local installs
?? index.html             # desktop pet runtime UI entry
?? settings.html          # browser settings UI entry
?? settings.css           # settings page styles
?? settings.js            # settings page behavior
?? main.py                # desktop host entry
?? pyproject.toml         # project metadata and dependencies
?? uv.lock                # locked dependency graph
?? README.md
?? README.zh-CN.md
```

## Conventions

- Do not move `main.py`, `index.html`, `settings.html`, `settings.css`, or `settings.js` without updating the desktop host and backend loading paths.
- Topic history persists under `data/chat_topics/`.
- Imported skills belong under `third_party_skills/` unless they are built-in repository skills.
- Third-party MCP manifests remain under `third_party_mcp/`.
- New automated tests should go under `tests/`.
- One-off verification scripts should go under `scripts/debug/`.
- Long-form reports belong in `docs/reports/`.
- Role or process docs belong in `docs/` or `docs/subagents/`.
- The canonical runtime workflow map lives in `docs/WORKFLOW.md` and must be updated whenever the workflow changes.
- Prompt or persona JSON assets should go under `prompts/`.

## Test Command

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```
