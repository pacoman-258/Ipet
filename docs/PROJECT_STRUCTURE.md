# Project Structure

## Goal

Keep the repository easy to scan:

- root: runnable entrypoints, stable UI entry files, config examples, and README files
- `backend/`: application logic, local APIs, runtime config/contracts/adapters/service, and local evidence services
- `docs/`: human-facing documentation, workflow maps, reports, and role playbooks
- `tests/`: automated verification
- `scripts/`: one-off tooling and diagnostics
- `skills/`: built-in Ipet skills
- `integrations/`: optional bridge/reference integrations that are not part of the default runtime path

## Current Layout

```text
Ipet/
|-- backend/                 # FastAPI APIs, runtime config/contracts/adapters/service, ASR, TTS, skills, MCP, vision
|   |-- mcp/                 # local MCP server, stdio client, third-party manager
|   |-- skills/              # skill discovery, normalization, runtime, models
|   |-- tooling/             # local file/security tools
|   |-- active_vision.py     # active observation request/response helpers
|   |-- vision.py            # in-memory vision service and evidence state
|   |-- vision_analyzer.py   # optional OCR/VLM analyzer boundary
|   `-- vision_state.py      # passive routing, screen state, semantic change timeline
|-- docs/                    # structure docs, workflow reference, reports, subagent playbooks
|-- integrations/            # optional/reference integrations, e.g. AstrBot vision context helper
|-- js/                      # frontend vendor/runtime assets used by index.html
|-- model/                   # tracked demo/reference Live2D assets plus ignored local generated files
|-- prompts/                 # reusable prompt/persona JSON assets
|-- scripts/                 # quick validation and manual debug helpers
|-- skills/                  # built-in Ipet skill definitions and scripts
|-- tests/                   # unit and regression tests
|-- third_party_mcp/         # third-party MCP manifests and ignored local installs
|-- third_party_skills/      # ignored legacy/imported skill storage
|-- index.html               # desktop pet runtime UI entry
|-- settings.html            # browser settings UI entry
|-- settings.css             # settings styles
|-- settings.js              # settings behavior
|-- main.py                  # desktop host entry
|-- pet_config.example.json  # safe example config
|-- pyproject.toml           # project metadata and dependencies
|-- uv.lock                  # locked dependency graph
|-- AGENTS.md                # shortest safe coding-agent entrypoint
|-- README.md                # English project README
|-- README.zh-CN.md          # Chinese Markdown README
`-- README.html              # Chinese human-readable HTML README
```

## Root Conventions

- Keep `main.py`, `index.html`, `settings.html`, `settings.css`, and `settings.js` at the repository root unless the desktop host and backend loading paths are updated in the same change.
- Keep root files purposeful: README files, config examples, lockfiles, metadata, and runnable entrypoints.
- Do not add one-off generated text, hotspot reports, local scratch files, logs, caches, or personal runtime state at the root.
- Use `scripts/debug/` for manual diagnostics and smoke scripts.
- Use `docs/reports/` for long-form reports and postmortems.
- Use `integrations/` for optional bridge code that is not loaded by default.

## Runtime Ownership

- AstrBot is the default runtime. AstrBot owns chat sessions, plugins, MCP, providers, knowledge bases, and downstream platform adapters when AstrBot is active; NapCatQQ remains managed by AstrBot, not Ipet.
- Hermes is a manually selected secondary / advanced runtime. Hermes owns durable conversation state, memory, summaries, skills, MCP, approval turns, and tool execution only when Hermes is active.
- Ipet owns desktop shell startup/shutdown, local settings, chat event adaptation, approval UI where supported by the active runtime, TTS/ASR bridges, and screen-vision evidence governance.
- Ipet controls Live2D, ASR, TTS, lip sync, settings UI, local visual evidence, and presentation mapping. Runtime returns semantic, text, task, phase, approval, done, or error events.
- Local topic files under `data/chat_topics/` are retained only for compatibility with existing UI/session surfaces during the transition.
- Legacy `third_party_skills/` and `third_party_mcp/` runtime installs are not scanned by the default settings/API path.

## Hygiene Rules

- `pet_config.json`, `.pet_runtime_*`, `.venv/`, `.uv-cache/`, runtime logs, audio cache, topic history, `node_modules`, and local third-party installs stay ignored.
- Generated hotspot or file-playground output belongs under ignored `fileplay/`, not in Git.
- `model/` currently includes tracked demo/reference Live2D assets. Private, large, generated, or downloaded model resources should remain local and ignored before commit.
- Do not commit API keys, local absolute paths, screenshot payloads, generated audio, downloaded MCP runtimes, or temporary analyzer outputs.
- If a workflow change alters chat streaming, approvals, runtime proxying, vision evidence, settings contracts, skill/MCP exposure, or frontend chat behavior, update `docs/WORKFLOW.md` in the same task.

## Test Command

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```
