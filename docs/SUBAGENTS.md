# Subagent Operating Model

This repository uses a fixed-role subagent model for day-to-day development.

## Core rule

- The coordinator agent routes work, assigns one lead, integrates results, and resolves conflicts.
- High-conflict files may only have one implementation owner in a task:
  - `main.py`
  - `index.html`
  - `backend/app.py`

## Fixed roles

- `desktop-shell`: owns `main.py`
- `pet-runtime-ui`: owns `index.html`
- `settings-console`: owns `settings.html`, `settings.css`, `settings.js`
- `backend-agent-runtime`: owns `backend/agent_graph.py`, `backend/agent_orchestrator.py`, and chat slices in `backend/app.py`
- `backend-mcp-platform`: owns `backend/mcp_bridge.py`, `backend/mcp/*`, `backend/tool_runtime.py`, `backend/tooling/*`, and MCP-management slices in `backend/app.py`
- `qa-integration`: review-first; owns regression planning, contract checks, and targeted test additions when explicitly delegated

## Dispatch defaults

- Desktop shell or bridge behavior: `desktop-shell`
- Pet runtime UI, chat rendering, TTS playback UX: `pet-runtime-ui`
- Settings page work: `settings-console`
- LangGraph, chat SSE, provider behavior: `backend-agent-runtime`
- MCP manifests, stdio transport, third-party server lifecycle: `backend-mcp-platform`
- Cross-module validation or regression coverage: `qa-integration`

## Cross-boundary rules

- `main.py` <-> `index.html` contract changes:
  - lead: `desktop-shell`
  - required reviewer: `pet-runtime-ui`
- `settings.js` <-> backend settings or MCP APIs:
  - lead may be frontend or backend
  - the other side must review
- Chat SSE event changes:
  - lead: `backend-agent-runtime`
  - required reviewers: `pet-runtime-ui`, `qa-integration`

## Prompt library

Stable role prompts live in `docs/subagents/`.

- Coordinator playbook: `docs/subagents/coordinator.md`
- Team overview: `docs/subagents/README.md`
- Role prompts:
  - `docs/subagents/desktop-shell.md`
  - `docs/subagents/pet-runtime-ui.md`
  - `docs/subagents/settings-console.md`
  - `docs/subagents/backend-agent-runtime.md`
  - `docs/subagents/backend-mcp-platform.md`
  - `docs/subagents/qa-integration.md`
