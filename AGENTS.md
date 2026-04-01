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

- Project shape: desktop pet host in `main.py`, FastAPI backend in `backend/`, topic persistence in `backend/chat_topics.py`, ASR runtime in `backend/asr.py` and `backend/asr_server.py`, runtime UI in `index.html`, settings UI in `settings.html`, `settings.css`, and `settings.js`.
- Primary stack: Python, Qt WebEngine, FastAPI, LangGraph, MCP, imported skills, Live2D-style assets.
- The currently verified dev runtime is the project `.venv` on Python 3.12.
- Preferred local start command:

```powershell
uv run --no-sync python main.py
```

- Default test command:

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```

- Do not start by scanning the whole repo. Read the minimum docs below, then jump to task-specific files.
- Qt binding order is fixed: prefer `PySide6`, fall back to `PyQt6`.

## Read Order

Read in this order unless the task is extremely narrow:

1. `README.md`
2. `docs/PROJECT_STRUCTURE.md`
3. `docs/SUBAGENTS.md`
4. Only the 1-2 files directly related to the task

Useful stable facts:

- Root UI files stay at repo root because the desktop host and backend currently load them from there.
- Topic history persists under `data/chat_topics/<topic_id>/`.
- Imported skills live under `skills/` and `third_party_skills/`.
- Third-party MCP runtimes remain separate from skills and still live under `third_party_mcp/`.
- The canonical runtime workflow map lives in `docs/WORKFLOW.md`; any workflow change must update that file in the same task.
- Tests live under `tests/`.
- Debug helpers live under `scripts/debug/`.
- Fixed role prompts live under `docs/subagents/`.

## Hot Files And Ownership

These are high-conflict files. Only one implementation owner should lead changes to each in a task:

- `main.py`
- `index.html`
- `backend/app.py`

Current ownership map:

- `desktop-shell`: `main.py`
- `pet-runtime-ui`: `index.html`
- `settings-console`: `settings.html`, `settings.css`, `settings.js`
- `backend-agent-runtime`: `backend/agent_graph.py`, `backend/agent_orchestrator.py`, chat slices in `backend/app.py`
- `backend-mcp-platform`: `backend/mcp_bridge.py`, `backend/mcp/*`, `backend/tool_runtime.py`, `backend/tooling/*`, MCP-management slices in `backend/app.py`
- `qa-integration`: regression planning, contract checks, targeted test additions

Cross-boundary rules:

- `main.py` <-> `index.html`: lead `desktop-shell`, review by `pet-runtime-ui`
- `settings.js` <-> backend settings or MCP APIs: one side leads, the other reviews
- Chat SSE changes: lead `backend-agent-runtime`, review by `pet-runtime-ui` and `qa-integration`

## Task Routing Cheatsheet

Use this map before opening large files:

| Task type | Read first | Minimum follow-up |
| --- | --- | --- |
| Desktop host, tray, Qt bridge, runtime command handling | `main.py` | `docs/SUBAGENTS.md` |
| Chat SSE, LangGraph, approval flow, provider routing | `backend/agent_graph.py` | `backend/agent_orchestrator.py`, chat routes in `backend/app.py` |
| Topic history, persisted chat, layered summaries | `backend/chat_topics.py` | topic routes in `backend/app.py`, `index.html`, topic settings in `settings.js` |
| ASR, push-to-talk, mic permissions, streaming recognition | `backend/asr.py` | `backend/asr_server.py`, `main.py`, `index.html`, ASR settings in `settings.js` |
| MCP, stdio transport, file tools, third-party server lifecycle | `backend/mcp_bridge.py` | `backend/tool_runtime.py`, `backend/tooling/`, MCP routes in `backend/app.py` |
| Runtime UI, chat window, skills drawer, display state | `index.html` | related backend route only if contract changed |
| Settings page | `settings.js` | `settings.html`, `settings.css`, relevant settings route in `backend/app.py` |
| Skills import/runtime | `backend/skills/manager.py` | `backend/skills/runtime.py`, `backend/skills/models.py`, related `third_party_skills/*` manifests |

## Token-Saving Rules

- Prefer `rg` or `rg --files` to locate symbols, routes, tests, and feature names.
- Do not read all of `index.html` or `main.py` unless the task truly lives there. Search first, then open the relevant block.
- Do not open every test file. Pick the smallest matching regression slice from the matrix below.
- Do not reread `README.md` or development reports after the first pass unless the task is documentation-only.
- Read model or request types before implementations when tracing behavior.
- When a task touches one subsystem, stay inside that subsystem until an interface boundary forces expansion.
- If you need repository shape or ownership rules, use this file and `docs/SUBAGENTS.md`; do not traverse the whole `docs/` tree by default.

## Regression Matrix

Run the smallest useful set first.

### Agent Runtime

```powershell
python -m unittest tests.test_chat_segmented_flow tests.test_agent_graph_runtime tests.test_chat_dual_output tests.test_react_trace_visibility -v
```

### Topic History And ASR

```powershell
python -m unittest tests.test_chat_topics tests.test_chat_topics_api tests.test_asr_api tests.test_asr_service tests.test_asr_server_api tests.test_health_endpoint -v
```

### MCP And Tooling

```powershell
python -m unittest tests.test_mcp_bridge_tools tests.test_mcp_servers_listing tests.test_tool_runtime tests.test_stdio_client_protocol -v
```

### Skills

```powershell
python -m unittest tests.test_skills_api tests.test_skills_manager tests.test_skills_runtime tests.test_skills_compat_contracts -v
```

### Settings And UI Contracts

```powershell
python -m unittest tests.test_settings_mcp_draft tests.test_settings_file_allowlist tests.test_chat_modes_ui -v
```

### Full Regression

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```
