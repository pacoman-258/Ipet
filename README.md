# AI Assistant Desktop Pet

AI Assistant Desktop Pet is a local-first desktop companion built with Python, Qt WebEngine, FastAPI, LangGraph, and Live2D-style character assets. It combines a floating desktop pet UI with chat, topic-based history, push-to-talk ASR, TTS, expression control, and MCP or skill-based workflows.

For coding agents, see `AGENTS.md`.

## Highlights

- Desktop pet shell with Qt / WebEngine rendering and a browser-based settings page
- FastAPI chat backend with `ollama` and OpenAI-compatible providers
- LangGraph-based agent runtime with approval, interrupt or resume, and persisted checkpoints
- Topic history system with full transcripts and layered mini or major summaries for model context
- FunASR-based push-to-talk input that writes speech into the chat input box without auto-send
- MCP tool integration plus imported skills under `third_party_skills/`
- ReAct trace visibility and segmented streaming output for the frontend

## Project Structure

- `main.py`: desktop host application and local service launcher
- `backend/`: FastAPI backend, agent orchestration, topic history, ASR, MCP bridge, TTS, and model APIs
- `index.html`, `settings.html`, `settings.css`, `settings.js`: runtime UI and settings UI entry files kept at repository root
- `data/chat_topics/`: persisted topic history, including `meta.json`, `full.jsonl`, and `summary.json`
- `third_party_skills/`: imported skills such as `daily-hotspots`
- `third_party_mcp/`: third-party MCP manifest area and local MCP installs
- `tests/`: regression and integration-oriented unit tests
- `scripts/debug/`: one-off debug and smoke-test scripts
- `docs/`: operational docs, reports, and subagent playbooks
- `prompts/`: reusable prompt or character JSON assets

## Requirements

- Python 3.12+
- Windows is the primary target environment in the current codebase
- The currently verified development runtime is the project `.venv` on Python 3.12
- Optional:
  - Ollama for local LLM inference
  - A compatible OpenAI-style API endpoint
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

3. Edit `pet_config.json` to point at your model files, API endpoint, API key, optional MCP servers, and optional imported skills.

4. Start the desktop app without forcing `uv` to rewrite the environment.

```powershell
uv run --no-sync python main.py
```

5. Open the settings page from the tray or right-click menu, or visit the current backend URL plus `/settings` after the backend starts.

## Runtime Notes

- The desktop host launches the main backend from `backend.app:app` and keeps `chat.backend_url` in sync with the active local port.
- When `chat.asr.enabled` is on, the desktop host also tries to launch `backend.asr_server:app` at `chat.asr.api_base_url`, defaulting to `http://127.0.0.1:8012`.
- Topic history is keyed by topic id. The UI shows full transcripts while the model consumes the compressed context rebuilt from `summary.json`.
- New chats start as drafts. Blank topics are not persisted until a real conversation is successfully completed.

## Feature Notes

### Topic History And Layered Summaries

- Topic persistence is controlled by `chat.topic_history.enabled`.
- Each topic stores `meta.json`, `full.jsonl`, and `summary.json` under `data/chat_topics/<topic_id>/`.
- Full records are used for UI replay. Summaries are used for model context.
- The default mini-summary interval is `10` assistant turns, and every `3` mini summaries are folded into one major summary.
- The runtime UI includes `Settings`, `Chat History`, and `New Chat`, plus delete controls for saved topics.

### Push-To-Talk ASR

- ASR is controlled by `chat.asr.enabled`, `chat.asr.push_to_talk_key`, and `chat.asr.interim_results`.
- The current implementation uses FunASR with a streaming WebSocket endpoint at `/api/asr/stream`.
- Supported hotkeys are `Alt`, `Ctrl`, and `Space`.
- While recording, partial transcripts replace a temporary draft region inside the chat input. Releasing the key commits the final transcript without sending it.

### Skills And MCP

- Imported skills now live under `third_party_skills/` and are discovered separately from MCP tools.
- MCP servers remain tool-oriented and are still managed through the existing backend MCP bridge.
- The repository currently includes skills such as `third_party_skills/daily-hotspots/` and `third_party_skills/atr-pptx/`.

## Configuration Notes

- `pet_config.json` is intentionally ignored and should stay local.
- Important chat settings now include:
  - `chat.topic_history.enabled`
  - `chat.topic_history.summary_interval_assistant_turns`
  - `chat.asr.enabled`
  - `chat.asr.api_base_url`
  - `chat.asr.push_to_talk_key`
  - `chat.skills.default_active_ids`
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
