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
- `backend-agent-runtime`: owns `backend/runtime_config.py`, `backend/runtime_adapters.py`, `backend/runtime_service.py`, `backend/runtime_contracts.py`, Hermes integration paths, `backend/agent_graph.py`, `backend/agent_orchestrator.py`, and chat slices in `backend/app.py`
- `backend-mcp-platform`: owns `backend/mcp_bridge.py`, `backend/mcp/*`, `backend/tool_runtime.py`, `backend/tooling/*`, and MCP-management slices in `backend/app.py`; under the default AstrBot runtime, plugins/MCP/knowledge bases/providers are managed in AstrBot WebUI, not reimplemented in Ipet
- `backend-vision-runtime`: owns `backend/vision.py`, `backend/vision_analyzer.py`, `backend/vision_state.py`, `backend/active_vision.py`, vision slices in `backend/app.py`, and vision regression tests
- `qa-integration`: review-first; owns regression planning, contract checks, and targeted test additions when explicitly delegated

## Dispatch defaults

- Desktop shell or bridge behavior: `desktop-shell`
- Pet runtime UI, chat rendering, TTS playback UX: `pet-runtime-ui`
- Settings page work: `settings-console`
- Runtime config/adapters/contracts/service, LangGraph, chat SSE, provider behavior, Hermes approval path: `backend-agent-runtime`
- MCP manifests, stdio transport, third-party server lifecycle, compatibility MCP surfaces: `backend-mcp-platform`
- Automatic vision, screenshot evidence, OCR/VLM analyzer behavior: `backend-vision-runtime`
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
- Vision capture/evidence changes:
  - lead: `backend-vision-runtime`
  - required reviewers: `desktop-shell` when capture behavior in `main.py` changes, `qa-integration` when contracts or regression coverage change

## Prompt library

Stable role prompts live in `docs/subagents/`.

- Coordinator playbook: `docs/subagents/coordinator.md`
- Team overview: `docs/subagents/README.md`
- Role prompts:
  - `docs/subagents/desktop-shell.md`
  - `docs/subagents/pet-runtime-ui.md`
  - `docs/subagents/settings-console.md`
  - `docs/subagents/backend-vision-runtime.md`
  - `docs/subagents/backend-agent-runtime.md`
  - `docs/subagents/backend-mcp-platform.md`
  - `docs/subagents/qa-integration.md`
