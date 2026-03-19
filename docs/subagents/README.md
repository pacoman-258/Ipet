# Fixed Subagent Team

This directory defines the long-lived subagent roles for this repository.

## Why this model

The project already has stable subsystem boundaries:

- desktop shell
- pet runtime UI
- settings console
- chat runtime backend
- MCP platform backend
- QA and integration

Using fixed roles is more efficient than re-inventing task-specific agents on every request.

## Working model

- The coordinator agent assigns exactly one lead owner for each task.
- Support agents may advise or review, but should not edit the lead's high-conflict file.
- High-conflict files:
  - `main.py`
  - `index.html`
  - `backend/app.py`
- `js/` is vendor territory and should be treated as read-only in normal feature work.

## Role list

- `desktop-shell`
- `pet-runtime-ui`
- `settings-console`
- `backend-agent-runtime`
- `backend-mcp-platform`
- `qa-integration`

## Lead assignment matrix

- Shell, window, tray, bridge, backend process lifecycle:
  - lead: `desktop-shell`
- Runtime UI, chat rendering, approval controls, TTS playback UX:
  - lead: `pet-runtime-ui`
- Settings page and configuration UX:
  - lead: `settings-console`
- LangGraph, chat flow, SSE events, provider behavior:
  - lead: `backend-agent-runtime`
- MCP runtime, stdio transport, manifests, tool registration:
  - lead: `backend-mcp-platform`
- Cross-module validation and regression planning:
  - lead: `qa-integration`

## Cross-boundary review rules

- `main.py` <-> `index.html`:
  - lead: `desktop-shell`
  - reviewer: `pet-runtime-ui`
- `settings.js` <-> backend settings or MCP APIs:
  - reviewer required on the opposite side
- Chat SSE contract changes:
  - lead: `backend-agent-runtime`
  - reviewers: `pet-runtime-ui`, `qa-integration`

## Standard handoff format

Each subagent should return:

1. Scope it owned
2. Files changed
3. Behavioral changes
4. Risks or assumptions
5. Verification completed

## Test ownership

- `backend-agent-runtime`
  - `tests/test_chat_segmented_flow.py`
  - `tests/test_agent_graph_runtime.py`
  - `tests/test_chat_dual_output.py`
  - `tests/test_react_trace_visibility.py`
- `backend-mcp-platform`
  - `tests/test_mcp_bridge_tools.py`
  - `tests/test_mcp_command_resolution.py`
  - `tests/test_mcp_server_config.py`
  - `tests/test_mcp_servers_listing.py`
  - `tests/test_stdio_client_protocol.py`
  - `tests/test_tool_runtime.py`
- `settings-console`
  - `tests/test_settings_mcp_draft.py`
- Global baseline
  - `python -m unittest discover -s tests -p "test*.py" -v`
