# Ipet Desktop Pet Agent Shell

Ipet is a local-first desktop pet shell for pluggable agent runtimes. It combines a Python desktop host, Qt WebEngine UI, FastAPI backend, Live2D-style character assets, chat streaming, TTS/ASR, settings, approval UI, and optional screen-vision grounding. AstrBot is the default runtime for agent decisions, sessions, plugins, MCP, knowledge bases, providers, and QQ/NapCatQQ integration; Hermes is a manually selected secondary / advanced runtime. Ipet owns the desktop experience and the adapter layer around those runtimes.

For coding agents, start with `AGENTS.md`.

## Highlights

- Floating desktop pet host with Qt / WebEngine rendering.
- FastAPI backend that adapts active runtime chat streams into the frontend SSE shape.
- Runtime selection with `runtime.active=astrbot` by default; Hermes remains available as a manual secondary / advanced runtime.
- Browser settings page for runtime, model, TTS, ASR, vision, resource, and window settings.
- Push-to-talk ASR through the local ASR server when enabled.
- TTS playback, expression hints, segmented output, and approval bubbles.
- Ipet-owned automatic vision pipeline: passive screen timeline, active observation, analyzer metadata, and bounded evidence injection.
- Built-in local skills plus compatibility paths for Hermes-managed skills and MCP when Hermes is selected.
- AstrBot default mode for chat/session use while plugins, MCP, providers, knowledge bases, and QQ/NapCatQQ setup remain in AstrBot WebUI.

## Repository Layout

```text
Ipet/
|-- main.py                    # desktop host, Qt bridge, backend/runtime sidecar launcher
|-- backend/                   # FastAPI APIs, runtime adapters, ASR/TTS, skills, MCP, vision
|-- index.html                 # desktop pet runtime UI, kept at root for host loading
|-- settings.html              # browser settings UI entry, kept at root for backend loading
|-- settings.css
|-- settings.js
|-- docs/                      # structure docs, workflow map, reports, subagent playbooks
|-- tests/                     # unit and regression tests
|-- scripts/                   # manual tooling and debug helpers
|-- skills/                    # built-in Ipet skills
|-- integrations/              # optional bridge/reference integrations, e.g. AstrBot helpers
|-- prompts/                   # reusable prompt/persona JSON assets
|-- model/                     # tracked demo/reference Live2D assets plus ignored local overlays
|-- third_party_mcp/           # third-party MCP manifests and ignored local installs
|-- third_party_skills/        # ignored legacy/imported skill storage
|-- pet_config.example.json    # safe example config
|-- README.zh-CN.md            # Chinese Markdown README
|-- README.html                # Chinese human-readable HTML README
```

Root UI files intentionally stay at the repository root because the desktop host and backend load them from those paths. One-off generated content should not be added at the root; use ignored runtime/cache locations or `scripts/debug/` for manual diagnostics.

## Requirements

- Python 3.12+
- The currently verified development runtime is the project `.venv` on Python 3.12.
- Qt binding order is fixed: prefer `PySide6`, fall back to `PyQt6`.
- Windows remains the original target environment.
- macOS v1 target is macOS 13+ on Apple Silicon, focused on startup, settings, chat, and screen-vision availability.

Optional components:

- AstrBot v4.18+ with HTTP API enabled, externally managed or launched as a sidecar.
- Hermes Agent, externally managed or launched as a sidecar, when manually selected as the secondary / advanced runtime.
- NapCatQQ connected to AstrBot through OneBot v11 reverse WebSocket when QQ integration is needed.
- Edge TTS or a custom HTTP TTS endpoint.
- Live2D-compatible model assets under `model/`.

## Quick Start

1. Make sure the project `.venv` uses Python 3.12.

```powershell
.\.venv\Scripts\python.exe -V
```

2. Create a local config if you do not already have one.

```powershell
Copy-Item pet_config.example.json pet_config.json
```

3. Edit `pet_config.json` for your model path, sidecar command, runtime URL/API key, and optional vision/TTS/ASR settings. The default runtime is `runtime.active=astrbot`. To use Hermes, manually set `runtime.active=hermes` and configure `runtime.adapters.hermes`.

4. Start the desktop app without asking `uv` to rewrite the environment.

```powershell
uv run --no-sync python main.py
```

5. Open settings from the tray/right-click menu, or visit the backend URL plus `/settings` after the backend starts.

## Runtime Workflow

`main.py` starts the desktop shell, launches the FastAPI backend from `backend.app:app`, and optionally starts the configured runtime sidecar. The frontend sends chat requests to `/api/chat/stream`; the backend resolves the active runtime, gathers any required Ipet-owned vision evidence, calls the AstrBot adapter by default or the Hermes adapter when manually selected, and emits the existing frontend event names.

Supported frontend events:

- `meta`
- `phase`
- `approval_required`
- `segment`
- `display_segment`
- `token`
- `done`
- `error`

If the active runtime is disabled, missing, or unhealthy, the chat stream returns a clear SSE error. The backend does not silently fall back to the legacy local Agent runtime.

## Automatic Vision

Vision is disabled by default and configured under `vision.*`. Ipet owns capture, state, analyzer routing, and evidence governance. Raw screenshots are not persisted, settings/status responses do not expose image data, and local frame/context APIs require the desktop host token.

The vision pipeline has two lanes:

- Passive lane: keeps a short in-memory timeline of semantic screen changes.
- Active lane: performs a bounded live observation for visual questions or forced grounding, temporarily hiding/restoring the pet window and allowing only light interaction such as focus, one window/tab cycle, or small scroll.

Vision Analyzer v2 can enrich frames with local OCR or a configured OpenAI-compatible/local VLM provider when explicitly enabled. Analyzer failures become bounded unknowns; they never become fabricated observations. Without sufficient observations, runtimes must answer that the current screenshot cannot confirm the claim.

## Configuration Notes

- `pet_config.json` is ignored and must stay local.
- Runtime selection lives under `runtime.active` and `runtime.adapters.*`; the default is `runtime.active=astrbot`.
- Hermes is a manual secondary / advanced runtime, not an automatic fallback.
- The legacy top-level `hermes` object is still read for compatibility.
- `chat.tooling.file_allowlist` controls local file-tool access.
- `model/` contains tracked demo/reference assets in this repository. Private, large, generated, or downloaded model assets should remain local and ignored before commit.
- Third-party MCP installs, `node_modules`, runtime state, topic history, generated hotspot reports, downloaded files, and audio cache outputs are ignored.

## Testing

Run the full regression suite with:

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```

Useful targeted slices are documented in `AGENTS.md`.

## Documentation

- Project structure: `docs/PROJECT_STRUCTURE.md`
- Runtime workflow: `docs/WORKFLOW.md`
- Chinese README: `README.zh-CN.md`
- Chinese HTML README: `README.html`
- Subagent quick guide: `docs/SUBAGENTS.md`
- Stable role prompts: `docs/subagents/`
