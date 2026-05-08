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
?? backend/               # FastAPI desktop shell APIs, runtime adapters, ASR, TTS, settings, resources
?? data/                  # persisted runtime state such as chat topics
?? docs/                  # reports, structure notes, subagent playbooks
?? js/                    # frontend vendor/runtime assets used by index.html
?? prompts/               # reusable prompt JSON assets
?? scripts/               # manual utilities and debug helpers
?? tests/                 # unit and regression tests
?? Hermes/                # optional local Hermes sidecar workspace, if configured
?? third_party_skills/    # legacy storage, not read by the default Hermes shell path
?? third_party_mcp/       # legacy storage, not read by the default Hermes shell path
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
- Hermes Agent owns durable conversation state, memory, summaries, skills, MCP, and tool execution when Hermes is active.
- AstrBot owns chat sessions and downstream platform adapters when AstrBot is active; NapCatQQ remains managed by AstrBot, not Ipet.
- Local topic files under `data/chat_topics/` are retained only for compatibility with existing UI/session surfaces during the transition.
- The default settings/API path proxies skills and MCP to Hermes in Hermes mode. AstrBot mode returns compatible empty status for those surfaces and points to AstrBot WebUI. Legacy `third_party_skills/` and `third_party_mcp/` are not scanned.
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
