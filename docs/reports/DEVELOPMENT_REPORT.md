# AI Assistant Development Report

## 1. Purpose

This document records stable implementation notes, recent milestones, and maintenance expectations for the desktop pet project.

Update rules:

- Any feature addition, removal, API change, or config change must update this file.
- If the change affects onboarding, startup, or local environment expectations, update `README.md` and `AGENTS.md` in the same task.
- Any workflow change in routing, agent-loop, approval, skill/tool discovery, or chat execution must also update `docs/WORKFLOW.md` in the same task.

---

## 2. Current Snapshot

The project is a local desktop pet application built on Qt WebEngine, FastAPI, LangGraph, MCP, imported skills, and Live2D-style assets.

Stable entry points:

- Desktop host: `main.py`
- Main backend: `backend.app:app`
- ASR backend: `backend.asr_server:app`
- Runtime UI: `index.html`
- Settings UI: `settings.html`, `settings.css`, `settings.js`
- Topic persistence: `backend/chat_topics.py` + `data/chat_topics/`
- Recommended runtime: project `.venv` on Python 3.12

Default local endpoints:

- Main backend: `http://127.0.0.1:8008`
- ASR backend: `http://127.0.0.1:8012`

---

## 3. Topic History And Layered Summaries

### 3.1 Data Model

Chat persistence is topic-based. Internally, incoming `session_id` is treated as `topic_id` when topic history is enabled.

Topic data is stored under:

- `data/chat_topics/<topic_id>/meta.json`
- `data/chat_topics/<topic_id>/full.jsonl`
- `data/chat_topics/<topic_id>/summary.json`

File roles:

- `full.jsonl`: full visible conversation used for UI replay.
- `summary.json`: compressed context blocks used to rebuild model context.
- `meta.json`: title, timestamps, persistence flags, assistant turn counts, and summary counters.

### 3.2 Runtime Behavior

- The UI always shows full topic history.
- The model consumes compressed context rebuilt from `summary.json`.
- Mini summaries are generated every configured assistant-turn interval.
- Major summaries are generated after every three mini summaries.
- New chats start as draft topics.
- Blank draft topics are not persisted.
- History lists filter out blank topics that never became real conversations.

### 3.3 Topic APIs

- `POST /api/chat/topics`
- `GET /api/chat/topics`
- `GET /api/chat/topics/{topic_id}`
- `DELETE /api/chat/topics/{topic_id}`
- `POST /api/chat/topics/{topic_id}/delete` as a compatibility fallback

### 3.4 UI Contract

The runtime sidebar now uses:

- `Settings`
- `Chat History`
- `New Chat`

History items support reopen and delete. Delete confirmation is handled inside the page UI instead of relying on native confirm dialogs.

---

## 4. FunASR Push-To-Talk Input

### 4.1 Product Behavior

The chat UI supports push-to-talk speech drafting:

- Hold the configured hotkey while the chat window is open.
- Recording starts and partial transcripts update the current draft region.
- Releasing the key stops recording and waits for the final transcript.
- The recognized text stays in the input box and is never auto-sent.

Supported hotkeys:

- `Alt`
- `Ctrl`
- `Space`

### 4.2 Architecture

Main modules:

- `backend/asr.py`
- `backend/asr_server.py`
- `index.html`
- `main.py`
- ASR settings in `settings.js`

Protocol:

- Frontend opens `WS /api/asr/stream`
- Client sends `start`
- Client streams PCM16 audio chunks
- Client sends `stop`
- Server returns `ready`, `partial`, `final`, or `error`

### 4.3 Host Integration

The desktop host grants Qt microphone permission only to the local pet page.

- allow `MediaAudioCapture` for the local runtime page
- reject unrelated capture permissions such as camera access

---

## 5. Python 3.12 Environment Migration

### 5.1 Why The Migration Happened

The previous Python 3.13 path repeatedly caused dependency friction for FunASR and related native packages on Windows.

### 5.2 Current Stable Expectation

The project now treats the local `.venv` Python 3.12 environment as the primary runtime for the desktop host, backend, and ASR.

Recommended start command:

```powershell
uv run --no-sync python main.py
```

Stable facts:

- `.python-version` should point at Python `3.12.12`.
- `.venv` is the main runtime.
- Avoid casual `uv sync` unless you intentionally want to rebuild the environment.
- When the environment is mostly healthy, prefer targeted repairs with `uv pip install --python .venv\Scripts\python.exe ...`.

---

## 6. Skills And Tooling Notes

### 6.1 Imported Skills

Imported skills are discovered separately from MCP tools.

Relevant paths:

- built-in skills: `skills/`
- imported skills: `third_party_skills/`

Current examples include:

- `third_party_skills/daily-hotspots/`

The daily-hotspots skill saves dated reports into a whitelisted `热搜` directory, preferring `fileplay/热搜` when `fileplay` exists in the allowlist.

### 6.2 MCP Tools

Third-party MCP servers remain tool-oriented and continue to live under `third_party_mcp/`.

---

## 7. Useful Regression Slices

Topic history and ASR:

```powershell
python -m unittest tests.test_chat_topics tests.test_chat_topics_api tests.test_asr_api tests.test_asr_service tests.test_asr_server_api tests.test_health_endpoint -v
```

Agent runtime:

```powershell
python -m unittest tests.test_chat_segmented_flow tests.test_agent_graph_runtime tests.test_chat_dual_output tests.test_react_trace_visibility -v
```

Settings and UI contracts:

```powershell
python -m unittest tests.test_settings_mcp_draft tests.test_settings_file_allowlist tests.test_chat_modes_ui -v
```

---

## 8. Maintenance Expectations

This file must be updated whenever these areas change:

- topic history file layout or summary strategy
- ASR interaction model, endpoint, or permission flow
- Python runtime expectations or dependency recovery guidance
- imported skills or third-party MCP directory semantics
- startup instructions in `README.md`
- developer routing or repo entrypoint guidance in `AGENTS.md`
- canonical workflow behavior in `docs/WORKFLOW.md`
