# AI Assistant Desktop Pet

AI Assistant Desktop Pet is a desktop GUI shell for pluggable agent runtimes, built with Python, Qt WebEngine, FastAPI, and Live2D-style character assets. It provides the floating pet UI, chat rendering, push-to-talk ASR, TTS, expressions, approvals, settings, and resource controls while Hermes or AstrBot owns agent decisions and tool-side runtime.

For coding agents, see `AGENTS.md`.

## Highlights

- Desktop pet shell with Qt / WebEngine rendering and a browser-based settings page
- FastAPI desktop backend that proxies chat SSE to the active runtime
- Configurable Hermes or AstrBot sidecar launch from the desktop host
- Runtime status and connection settings in the browser settings page
- FunASR-based push-to-talk input that writes speech into the chat input box without auto-send
- Hermes-proxied skills and MCP management, with AstrBot WebUI delegation in AstrBot mode
- Segmented streaming output for the frontend

## Project Structure

- `main.py`: desktop host application and local service launcher
- `backend/`: FastAPI backend, runtime adapters, ASR, TTS, settings, and resource APIs
- `index.html`, `settings.html`, `settings.css`, `settings.js`: runtime UI and settings UI entry files kept at repository root
- `data/chat_topics/`: transition-era local topic files for existing UI/session surfaces
- `Hermes/`: optional local Hermes sidecar workspace, if configured
- `third_party_skills/`, `third_party_mcp/`: legacy storage not read by the default Hermes shell path
- `tests/`: regression and integration-oriented unit tests
- `scripts/debug/`: one-off debug and smoke-test scripts
- `docs/`: operational docs, reports, and subagent playbooks
- `prompts/`: reusable prompt or character JSON assets

## Requirements

- Python 3.12+
- Windows is the primary target environment in the current codebase
- macOS v1 target: macOS 13+ on Apple Silicon, focused on desktop startup, settings, and chat availability
- The currently verified development runtime is the project `.venv` on Python 3.12
- Optional:
  - Hermes Agent, either externally managed or launched as a sidecar
  - AstrBot v4.18+ with HTTP API enabled, either externally managed or launched as a sidecar
  - NapCatQQ connected to AstrBot through OneBot v11 reverse WebSocket when QQ integration is needed
  - Edge TTS or a custom HTTP TTS service
  - Local Live2D or model assets placed under `model/`

## Quick Start

1. Make sure the project `.venv` is using Python 3.12.

```powershell
.\.venv\Scripts\python.exe -V
```

2. Create a local config from the example if you do not already have one.

```powershell
Copy-Item pet_config.example.json pet_config.json
```

3. Edit `pet_config.json` to point at your model files and the active runtime connection or sidecar command.

4. Start the desktop app without forcing `uv` to rewrite the environment.

```powershell
uv run --no-sync python main.py
```

5. Open the settings page from the tray or right-click menu, or visit the current backend URL plus `/settings` after the backend starts.

## Runtime Notes

- The desktop host launches the main backend from `backend.app:app` and keeps `chat.backend_url` in sync with the active local port.
- When `runtime.active` points at an enabled adapter with `auto_start` true, the desktop host launches the configured sidecar command and stops only that managed process on exit.
- When `chat.asr.enabled` is on, the desktop host also tries to launch `backend.asr_server:app` at `chat.asr.api_base_url`, defaulting to `http://127.0.0.1:8012`.
- `/api/chat/stream` proxies the active runtime and keeps the existing frontend event shape.
- `/api/chat/approval` is supported by Hermes; AstrBot v1 returns a clear unsupported error for approval forwarding.
- If the active runtime is disabled or unavailable, chat returns a clear error instead of falling back to the legacy local Agent runtime.

### macOS v1 Scope

- The current macOS target is `macOS 13+` on Apple Silicon.
- v1 aims to keep the desktop shell, settings page, backend, and normal chat usable.
- ASR is disabled by default on macOS in v1.
- `.app` packaging, code signing, notarization, the full microphone permission chain, and full ASR support are intentionally out of scope for this first pass.
- Third-party MCP servers are not guaranteed to work on macOS in this phase.

## Feature Notes

### Hermes Agent

- Hermes connection is controlled by `runtime.adapters.hermes.*`; the legacy top-level `hermes` field is still read for compatibility.
- For a local Hermes checkout, point `hermes.cwd` at that checkout and use a sidecar command such as `uv run hermes dashboard --host 127.0.0.1 --port 9119 --no-open --tui` with `hermes.base_url` set to `http://127.0.0.1:9119` and `hermes.health_path` set to `/api/status`.
- The backend exposes `/api/runtime/status` for the active runtime and keeps `/api/hermes/status` for Hermes-specific diagnostics.
- Hermes owns sessions, memory, summaries, skills, MCP, planning, and tool execution.

### AstrBot And NapCatQQ

- Set `runtime.active` to `astrbot` and configure `runtime.adapters.astrbot.base_url`, `username`, and an API Key or `api_key_env`.
- AstrBot chat uses `POST /api/v1/chat` and session listing uses `GET /api/v1/chat/sessions`.
- AstrBot v1 mode leaves plugins, MCP, knowledge bases, providers, and QQ platform setup in AstrBot WebUI.
- NapCatQQ is treated as an AstrBot-managed OneBot v11 platform. Ipet only records/displays the reverse WebSocket URL such as `ws://127.0.0.1:6199/ws`; it does not read QQ messages, send QQ messages, write NapCat config, or manage QR login/token state.

### Push-To-Talk ASR

- ASR is controlled by `chat.asr.enabled`, `chat.asr.push_to_talk_key`, and `chat.asr.interim_results`.
- The current implementation uses FunASR with a streaming WebSocket endpoint at `/api/asr/stream`.
- Supported hotkeys are `Alt`, `Ctrl`, and `Space`.
- While recording, partial transcripts replace a temporary draft region inside the chat input. Releasing the key commits the final transcript without sending it.

### Skills And MCP

- Skills and MCP settings routes proxy Hermes when Hermes is active.
- In AstrBot mode, skills/MCP routes return compatible empty status payloads and point the user to AstrBot WebUI.
- Legacy local skills and MCP directories are not scanned by the default settings/API path.

## Configuration Notes

- `pet_config.json` is intentionally ignored and should stay local.
- Important chat settings now include:
  - `hermes.enabled`
  - `runtime.active`
  - `runtime.adapters.hermes.*`
  - `runtime.adapters.astrbot.*`
  - `hermes.auto_start`
  - `hermes.command`
  - `hermes.cwd`
  - `hermes.base_url`
  - `hermes.health_path`
  - `hermes.startup_timeout_sec`
  - `chat.asr.enabled`
  - `chat.asr.api_base_url`
  - `chat.asr.push_to_talk_key`
- Local model assets under `model/` are not included in this repository.
- Installed third-party MCP runtimes, `node_modules`, caches, topic history data, downloaded files, and audio cache outputs are not tracked.
- The safe example config is in `pet_config.example.json`.

## Testing

Run the full test suite with:

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```

Useful targeted slices:

```powershell
python -m unittest tests.test_chat_topics tests.test_chat_topics_api tests.test_asr_api tests.test_asr_service tests.test_asr_server_api tests.test_health_endpoint -v
```

## GitHub Hygiene

The public repository excludes:

- private API keys and personal local paths
- local runtime state, topic history data, and cached audio output
- downloaded or vendored third-party MCP runtime directories
- local model assets and large generated binaries

## Documentation

- Project structure guide: `docs/PROJECT_STRUCTURE.md`
- Development notes: `docs/reports/DEVELOPMENT_REPORT.md`
- Chinese README: `README.zh-CN.md`
- Subagent quick guide: `docs/SUBAGENTS.md`
- Subagent prompt library: `docs/subagents/`
