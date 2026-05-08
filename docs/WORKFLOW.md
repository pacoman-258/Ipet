# Workflow Reference

## Purpose

This file is the canonical workflow map for the desktop pet runtime after the first pluggable-runtime pass.

Update this file whenever chat streaming, approval, runtime proxying, settings contracts, skill/MCP exposure, or frontend chat behavior changes.

## Scope

The project is now a desktop GUI shell for pluggable agent runtimes. Hermes remains the default runtime; AstrBot can be selected for chat/session use and later QQ integration through AstrBot-managed NapCatQQ.

Owned locally:

- desktop shell startup and shutdown
- Live2D-style rendering, expressions, motions, and window behavior
- chat rendering and TTS/ASR integration
- user approval UI
- settings page, file/resource allowlists, and local visual assets
- SSE event adaptation between the active runtime and the frontend

Owned by the active runtime:

- agent decisions and planning
- durable memory and summarization
- sessions and conversation state
- skills
- MCP servers and tool execution

AstrBot v1 exception: plugins, MCP, providers, knowledge bases, and QQ platform setup stay in AstrBot WebUI. Ipet only proxies chat/session APIs and displays NapCatQQ connection guidance.

## Top-Level Flow

```text
main.py starts desktop shell
        |
        +--> launch FastAPI backend
        |
        +--> if active runtime enabled && auto_start:
        |       launch configured runtime sidecar command
        |
        v
index.html sends POST /api/chat/stream
        |
        v
backend/app.py resolves active runtime
        |
        v
HermesClient or AstrBotClient connects to runtime API
        |
        v
runtime streams native events
        |
        v
backend adapter preserves frontend event shape
        |
        v
index.html renders meta / phase / token / segment / approval_required / done / error
```

If the active runtime is disabled, missing, or unhealthy, `/api/chat/stream` returns a clear `error` SSE event. The backend does not fall back to the legacy local Agent runtime.

## Configuration

Runtime selection lives under `runtime` in `pet_config.json` and `/api/settings/config`:

- `runtime.active`
- `runtime.adapters.hermes`
- `runtime.adapters.astrbot`

The legacy top-level `hermes` object is still read and mirrored for compatibility. `main.py` normalizes runtime values on config load. Command strings are split into argv lists and relative `cwd` values are resolved under the project root.
For a local Hermes checkout, the sidecar command can be `uv run hermes dashboard --host 127.0.0.1 --port 9119 --no-open --tui`, with `base_url` set to `http://127.0.0.1:9119` and `health_path` set to `/api/status`.
For AstrBot, set `runtime.active` to `astrbot`, configure `runtime.adapters.astrbot.base_url` such as `http://127.0.0.1:6185`, and provide an API Key through `api_key` or `api_key_env`.

The backend exposes `/api/runtime/status` for the active runtime and keeps `/api/hermes/status` for Hermes-specific diagnostics.

## Chat Stream

`POST /api/chat/stream` is the Ipet-facing active runtime proxy path.

Hermes: the adapter discovers the Dashboard session token, connects to Hermes Dashboard `/api/ws`, creates or reuses the selected Hermes session, applies the configured Hermes model through `config.set`, submits the prompt with `prompt.submit`, and adapts TUI gateway events back into the existing frontend SSE names.

AstrBot: the adapter calls `POST /api/v1/chat` with `username`, `session_id`, `message`, and `enable_streaming: true`, then adapts streamed chunks into the existing frontend SSE names.

Local responsibilities:

- forward the existing frontend request payload to the active runtime
- adapt runtime-native events into the existing frontend event names
- return clear SSE errors when the active runtime is unavailable
- keep TTS/expression-compatible event shapes intact

Runtime responsibilities:

- route the request
- decide whether tools or approvals are needed
- stream final content
- own session state

Supported frontend event names remain:

- `meta`
- `phase`
- `approval_required`
- `segment`
- `display_segment`
- `token`
- `done`
- `error`

The adapters also map simple delta/final event aliases into `token` and `done` for compatibility.

## Approval Flow

```text
Hermes emits approval_required
        |
        v
frontend renders approval bubble
        |
        +--> approve / reject / reject with guidance
                |
                v
POST /api/chat/approval
                |
                v
backend sends approval.respond to the matching Hermes TUI session
                |
                v
backend preserves returned SSE shape
```

Pending turn ownership belongs to Hermes. AstrBot v1 does not support Ipet approval forwarding and returns a clear unsupported SSE error if `/api/chat/approval` is called while AstrBot is active.

## Skills And MCP

Settings and API routes treat the active runtime as the source of truth.

- In Hermes mode, skills and MCP routes proxy Hermes and return empty clear-status payloads when unavailable.
- In AstrBot mode, skills and MCP routes return compatible empty payloads with details that these surfaces are managed in AstrBot WebUI.

Legacy `third_party_skills/` and `third_party_mcp/` directories are no longer read by the default settings/API path.

## NapCatQQ

NapCatQQ is not an Ipet runtime adapter. It is an AstrBot-managed OneBot v11 platform:

- AstrBot listens for the OneBot v11/aiocqhttp connection.
- NapCatQQ connects as a reverse WebSocket client, typically to `ws://127.0.0.1:6199/ws`.
- Ipet stores and displays the connection URL and guide metadata only.
- Ipet does not write NapCat configuration, read QQ messages, send QQ messages, manage QR login, or store OneBot tokens.

## Settings Page

The settings page shows active runtime controls and status alongside desktop-owned settings:

- Hermes enabled/auto-start/base URL/health path/command/cwd/startup timeout
- Live2D/model resources, expressions, motions, and background
- TTS and ASR
- chat rendering options and approval-facing prompt metadata
- resource allowlists
- window and pet behavior

The settings page does not expose local provider/router controls or app-level memory/summary controls.

## Shutdown

On app exit, `main.py` stops only processes started by this project:

- ASR service started by the desktop shell
- FastAPI backend started by the desktop shell
- Hermes sidecar started by the desktop shell

Externally managed Hermes processes are left alone.
