# AI Assistant Desktop Pet

AI Assistant Desktop Pet is a local-first desktop companion built with Python, Qt WebEngine, FastAPI, and Live2D-style character assets. It combines a floating desktop pet UI with chat, TTS, expression control, and an MCP-based tool system for controllable agent workflows.

## Highlights

- Desktop pet shell with Qt / WebEngine rendering and a browser-based settings page
- Chat backend powered by FastAPI with support for `ollama` and OpenAI-compatible providers
- LangGraph-based agent runtime with tool approval, interrupt/resume, and persisted checkpoints
- TTS support for `edge_tts` and configurable custom HTTP providers
- MCP tool integration for local and third-party servers
- ReAct-style trace visibility and segmented streaming output for the frontend

## Project Structure

- `main.py`: desktop host application
- `backend/`: FastAPI backend, agent orchestration, MCP bridge, TTS, and model APIs
- `index.html`, `settings.html`, `settings.css`, `settings.js`: frontend UI
- `third_party_mcp/`: third-party MCP manifest area and examples
- `test_*.py`: regression and integration-oriented unit tests

## Requirements

- Python 3.13+
- Windows is the primary target environment in the current codebase
- Optional:
  - Ollama for local LLM inference
  - A compatible OpenAI-style API endpoint
  - Edge TTS or a custom HTTP TTS service
  - Local Live2D / model assets placed under `model/`

## Quick Start

1. Install dependencies.

```powershell
uv sync
```

2. Create a local config from the example.

```powershell
Copy-Item pet_config.example.json pet_config.json
```

3. Edit `pet_config.json` to point at your own model files, API endpoint, API key, and optional MCP servers.

4. Start the desktop app.

```powershell
python main.py
```

5. Open the settings page from the tray / right-click menu, or visit `http://127.0.0.1:8008/settings` after the backend starts.

## Configuration Notes

- `pet_config.json` is intentionally ignored and should stay local.
- Local model assets under `model/` are not included in this repository.
- Installed third-party MCP runtimes, `node_modules`, caches, downloaded files, and audio cache outputs are not tracked.
- The safe example config is in `pet_config.example.json`.

## Testing

Run the test suite with:

```powershell
python -m unittest discover -p "test*.py" -v
```

## GitHub Hygiene

The public repository excludes:

- private API keys and personal local paths
- local runtime state and cached audio output
- downloaded or vendored third-party MCP runtime directories
- local model assets and large generated binaries

## Documentation

- Development notes: `DEVELOPMENT_REPORT.md`
- Chinese README: `README.zh-CN.md`

