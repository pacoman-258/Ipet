# Ipet Project Architecture

## 1. Positioning

Ipet is not "AstrBot with a Live2D skin". It is an independent desktop pet product. The current implementation is a Python desktop host, a FastAPI backend, and root-level HTML/JS UI files. It is not a TypeScript `src/` application.

The project has two boundaries:

- **Ipet application layer**: the user-facing desktop pet product.
- **Agent Runtime layer**: the agent capability provider. **AstrBot is the default Runtime**. **Hermes is a manually selected secondary / advanced Runtime**.

Core rule:

```text
Ipet owns the product experience.
Agent Runtime owns semantic, text, and task events.
Runtime Adapters connect them.
```

Ipet does not treat AstrBot or Hermes as the application itself. It also does not rely on automatic failover between runtimes. If the active runtime is unavailable, Ipet returns a clear error and the user fixes configuration or manually switches runtime.

---

## 2. Current Architecture

```mermaid
flowchart TD
    User[User] --> Shell[main.py desktop host]
    Shell --> UI[index.html pet UI]
    Shell --> Backend[backend/app.py FastAPI]
    UI --> Chat[/api/chat/stream]
    Chat --> Service[backend/runtime_service.py]
    Service --> Contract[backend/runtime_contracts.py]
    Service --> Adapters[backend/runtime_adapters.py]
    Adapters --> AstrBot[AstrBot default Runtime]
    Adapters --> Hermes[Hermes secondary/advanced Runtime]
    Backend --> Vision[backend/vision*.py + active_vision.py]
    Backend --> ASR[backend/asr.py + asr_server.py]
    UI --> Presentation[Live2D/bubbles/TTS/lip sync/presentation mapping]
    Vision --> Service
    AstrBot --> Events[semantic/text/task events]
    Hermes --> Events
    Events --> UI
```

Key files:

- `main.py`: desktop host, Qt/WebEngine bridge, backend lifecycle, runtime sidecar lifecycle.
- `backend/app.py`: FastAPI routes for chat streaming, settings, vision, ASR/TTS, and status surfaces.
- `backend/runtime_config.py`: reads and normalizes `runtime.active` and `runtime.adapters.*`.
- `backend/runtime_contracts.py`: shared request, status, and event contracts between Ipet and Runtime Adapters.
- `backend/runtime_adapters.py`: concrete AstrBot and Hermes adapter implementations.
- `backend/runtime_service.py`: active runtime resolution, status lookup, and streaming request dispatch.
- `index.html`: runtime pet UI, kept at the repository root because the desktop host loads it from there.
- `settings.html`, `settings.css`, `settings.js`: settings UI, also kept at the repository root.

---

## 3. Layers

### 3.1 Ipet Application Layer

Ipet owns all user-facing experience and local device capabilities:

- Live2D-style rendering, model assets, expression and motion mapping
- desktop window behavior, tray/context-menu entry points, chat bubbles, frontend state
- ASR, TTS, audio playback, and real-time lip sync
- settings UI, local configuration, and resource allowlists
- local vision evidence: screenshots, passive timeline, active observation, analyzer metadata, injection policy
- approval UI, frontend SSE rendering, segmented output, and presentation mapping

Ipet does not directly implement runtime reasoning and does not manage AstrBot or Hermes internal state.

### 3.2 Agent Runtime Layer

The Agent Runtime owns agent capability and semantic results:

- LLM reasoning, context, and sessions
- tool calling, plugins, MCP, knowledge bases, and task planning
- text, semantic, task, phase, approval, and error events returned to Ipet

Default policy:

- **AstrBot is the default Runtime**. AstrBot WebUI manages plugins, MCP, knowledge bases, providers, QQ, and NapCatQQ.
- **Hermes is the secondary / advanced Runtime**. Hermes skills, MCP, and approval flow are used only after the user manually selects Hermes.

The Runtime does not own Live2D, ASR, TTS, lip sync, settings UI, local vision evidence, or final presentation mapping.

### 3.3 Runtime Adapter Layer

The current Adapter layer is implemented in Python backend modules, not as a TypeScript package. Adapters:

- convert Ipet requests into AstrBot or Hermes API calls
- expose `status`, `request_json`, and `stream_sse`
- prefer streaming and convert native runtime responses into stable frontend SSE events
- isolate runtime-specific authentication, errors, sessions, and event formats

Stable frontend events remain:

- `meta`
- `phase`
- `approval_required`
- `segment`
- `display_segment`
- `token`
- `done`
- `error`

The contract may later evolve into formal `AgentInputEvent` / `AgentRuntimeEvent` types, but current docs must not claim that `src/runtime/*.ts` already exists.

---

## 4. Boundary Rules

```text
1. Ipet controls Live2D, ASR, TTS, lip sync, settings UI, local vision evidence, and presentation mapping.
2. Runtime returns only semantic, text, phase, approval, task, and error events.
3. Runtime must not directly manipulate windows, models, audio players, screenshots, settings pages, or UI DOM.
4. AstrBot is the default owner of plugins, MCP, knowledge bases, providers, QQ, and NapCatQQ.
5. Hermes skills/MCP/approval are a manually selected secondary / advanced path.
6. Ipet reaches Runtime through the `backend/runtime_*` contract and adapter layer.
7. Unhealthy active runtime produces a clear error; do not describe or depend on automatic failover.
8. Ipet maps Runtime semantics into Live2D expressions, motions, TTS, lip sync, and UI behavior.
```

---

## 5. Runtime Contract

The current contract is streaming-first:

```text
Ipet request
  -> backend/runtime_service.py
  -> active adapter.status()
  -> active adapter.stream_sse() or request_json()
  -> frontend-compatible SSE events
```

Adapter capabilities:

- `status`: checks whether the active runtime is configured, enabled, and reachable.
- `request_json`: supports non-streaming or management requests.
- `stream_sse`: default chat path, preserving streaming output.

Evolution path:

- `AgentInputEvent`: unified input from desktop, voice, vision, system timer, or external platforms.
- `AgentRuntimeEvent`: unified output for token, segment, phase, approval, task, error, and done events.

These are contract direction names, not current TypeScript files.

---

## 6. Typical Flows

### 6.1 Default AstrBot Chat Flow

```text
index.html
  -> POST /api/chat/stream
  -> runtime_service resolves runtime.active=astrbot
  -> Ipet vision gate injects bounded text evidence when needed
  -> AstrBot adapter calls AstrBot HTTP API
  -> AstrBot returns streaming text / semantic events
  -> Ipet adapts them to frontend SSE
  -> index.html renders text, TTS, expressions, and bubbles
```

AstrBot plugins, MCP, knowledge bases, providers, QQ, and NapCatQQ are not reimplemented in Ipet settings. Users manage them in AstrBot WebUI.

### 6.2 Hermes Secondary / Advanced Flow

```text
user manually selects runtime.active=hermes
  -> Ipet calls Hermes adapter
  -> Hermes Dashboard / TUI gateway handles sessions, skills, MCP, approval
  -> Ipet converts token/phase/approval/done events
  -> frontend keeps rendering the same SSE event names
```

Hermes owns pending approval turns. AstrBot v1 does not support Ipet approval forwarding.

### 6.3 Vision Evidence Flow

```text
/api/chat/stream receives user input
  -> Ipet Vision Broker decides passive/active evidence needs
  -> main.py captures screen, hides/restores the pet, and performs bounded light interaction when needed
  -> backend/vision*.py produces bounded observations/unknowns
  -> only text evidence is injected into Runtime
  -> Runtime answers from evidence without owning screenshots or vision state
```

---

## 7. Configuration

Default configuration uses:

```json
{
  "runtime": {
    "active": "astrbot",
    "adapters": {
      "astrbot": {
        "base_url": "http://127.0.0.1:6185",
        "api_key_env": "ASTRBOT_API_KEY",
        "username": "ipet"
      },
      "hermes": {
        "base_url": "http://127.0.0.1:9119",
        "health_path": "/api/status"
      }
    }
  }
}
```

`runtime.active=astrbot` is the default. To use Hermes, the user manually sets `runtime.active=hermes` and configures Hermes Dashboard URL, health path, and optional sidecar command.

---

## 8. Development Constraints

Hard requirements for Codex and other coding agents:

```text
Do not treat AstrBot as the Ipet application.
Do not implement desktop UI, Live2D, ASR, TTS, lip sync, or settings pages inside AstrBot or Hermes.
Do not document the current architecture as a nonexistent src/*.ts tree.
Do not describe automatic failover between AstrBot and Hermes.
Preserve Ipet-owned vision evidence governance; Runtime consumes text evidence by default.
Document AstrBot as the default Runtime and Hermes as the manually selected secondary / advanced Runtime.
```

---

## 9. One-Sentence Summary

```text
Ipet is the desktop pet product; AstrBot is the default Agent Runtime; Hermes is a manually selected advanced Runtime.
They are decoupled through the Python/FastAPI runtime contract and adapter layer.
```
