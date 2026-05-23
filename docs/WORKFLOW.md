# Workflow Reference

## Purpose

This file is the canonical workflow map for the desktop pet runtime after the AstrBot-default runtime pass.

Update this file whenever chat streaming, approval, runtime proxying, settings contracts, skill/MCP exposure, or frontend chat behavior changes.

## Scope

The project is a desktop GUI shell for pluggable agent runtimes. AstrBot is the default runtime. Hermes remains available as a manually selected secondary / advanced runtime for users who want the Hermes Dashboard, skills, MCP, and approval path.

Owned locally:

- desktop shell startup and shutdown
- Live2D-style rendering, expressions, motions, and window behavior
- chat rendering and TTS/ASR integration
- conversation history and long-term memory
- automatic vision capture state and temporary vision summaries
- user approval UI where the active runtime supports it
- settings page, file/resource allowlists, and local visual assets
- SSE event adaptation between the active runtime and the frontend

Owned by the active runtime:

- agent decisions and planning
- temporary task execution for the current turn
- skills, plugins, MCP servers, tool execution, and knowledge retrieval within the selected runtime

Default AstrBot boundary: plugins, MCP, providers, knowledge bases, QQ, and NapCatQQ setup stay in AstrBot WebUI. Ipet owns desktop conversation history and memory, sends temporary task sessions to AstrBot, adapts stream events, and displays NapCatQQ connection guidance.

Hermes secondary path: Hermes skills, MCP, and approval are used only when the user manually selects `runtime.active=hermes`. Ipet does not automatically fall back from AstrBot to Hermes or from Hermes to AstrBot.

## Repository Boundaries

The runtime workflow depends on a deliberately small root:

- `main.py` owns desktop startup, Qt bridge behavior, backend process lifecycle, and runtime sidecar lifecycle.
- `index.html` owns the desktop pet runtime UI and must stay at the root until host loading paths change.
- `settings.html`, `settings.css`, and `settings.js` own the browser settings UI and must stay at the root until backend loading paths change.
- `backend/` owns local API behavior, runtime adapters, ASR/TTS services, skills/MCP bridges, and vision evidence services.
- `integrations/` contains optional reference bridge code such as AstrBot helpers; it is not loaded by the default Ipet runtime path.
- `skills/` contains built-in Ipet skills. Imported or third-party skill storage remains separate and ignored unless intentionally promoted.
- `scripts/debug/` is the home for manual diagnostics and smoke tests. Generated reports, local runtime state, caches, screenshots, and scratch files should not become root-level project files.

When workflow changes introduce new runtime-owned or Ipet-owned responsibilities, update this map and `docs/PROJECT_STRUCTURE.md` together so humans and coding agents see the same boundaries.

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
AstrBotClient by default, or HermesClient when manually selected, connects to runtime API
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

Default configuration sets `runtime.active` to `astrbot`. Configure `runtime.adapters.astrbot.base_url` such as `http://127.0.0.1:6185`, and provide an API Key through `api_key` or `api_key_env`.

Hermes is a manual secondary / advanced selection. To use a local Hermes checkout, set `runtime.active` to `hermes`, then configure a sidecar command such as `uv run hermes dashboard --host 127.0.0.1 --port 9119 --no-open --tui`, with `base_url` set to `http://127.0.0.1:9119` and `health_path` set to `/api/status`.

The legacy top-level `hermes` object is still read and mirrored for compatibility. `main.py` normalizes runtime values on config load. Command strings are split into argv lists and relative `cwd` values are resolved under the project root.

The backend exposes `/api/runtime/status` for the active runtime and keeps `/api/hermes/status` for Hermes-specific diagnostics.

## Chat Stream

`POST /api/chat/stream` is the Ipet-facing active runtime proxy path. The runtime contract is streaming-first. Adapters expose `status`, `request_json`, and `stream_sse`; the current frontend consumes compatible SSE names, and the contract can later evolve into formal `AgentInputEvent` / `AgentRuntimeEvent` types.

AstrBot default path: the adapter calls `POST /api/v1/chat` with `username`, `session_id`, `message`, and `enable_streaming: true`, then adapts streamed chunks into the existing frontend SSE names.

Hermes secondary / advanced path: when manually selected, the adapter discovers the Dashboard session token, connects to Hermes Dashboard `/api/ws`, creates or reuses the selected Hermes session, applies the configured Hermes model through `config.set`, submits the prompt with `prompt.submit`, and adapts TUI gateway events back into the existing frontend SSE names.

## Ipet Conversation And Memory Harness

Ipet owns conversation history and long-term memory. The active runtime is a task executor, not the durable chat data layer. In AstrBot mode, AstrBot owns only temporary task execution for Ipet-initiated desktop chat turns.

Local state:

- `data/ipet_conversations/` stores Ipet-owned conversation history and temporary runtime cleanup records.
- `data/ipet_memory/` stores local long-term memory.

`/api/chat/stream` builds the local harness context from Ipet conversation history, local memory, current user input, and bounded Ipet-owned evidence such as vision context. The active runtime receives one temporary session per turn. In AstrBot mode that session id uses an `ipet-temp-*` prefix, and Ipet deletes the runtime session after the turn completes or fails.

`/api/chat/topics*` reads and writes Ipet local conversations. These endpoints do not browse AstrBot chat databases, Dashboard history, or runtime-native conversation stores.

Temporary chat mode does not write business conversation content or long-term memory. A user no-save intent also blocks durable writes to Ipet conversation history and local memory for that turn.

Local responsibilities:

- forward the existing frontend request payload to the active runtime
- run the Ipet-owned Vision Broker evidence gate before entering the active runtime when automatic vision is enabled
- prefix only bounded local vision evidence text when `vision.inject_policy` allows it
- force grounded visual constraints for screen/window/image questions, or when `vision.force_grounding` / `grounding_mode=always` is enabled, by observing in Ipet before the runtime call
- adapt runtime-native events into the existing frontend event names
- emit a proxy-layer realtime worklog prelude before runtime tokens using inferred `phase` events labeled `source="ipet_inferred"`
- label runtime-originated `phase` events with `source="runtime"` so the frontend can distinguish proxy inference from actual runtime progress
- return clear SSE errors when the active runtime is unavailable
- keep TTS/expression-compatible event shapes intact

Runtime responsibilities:

- route the request
- decide whether tools or approvals are needed
- stream final content
- keep only temporary task state needed to execute the current turn
- return semantic, text, task, phase, approval, done, or error events without directly controlling Live2D, ASR, TTS, lip sync, settings, local vision state, or presentation mapping

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

## Automatic Vision

Automatic vision is an Ipet-owned local capture and evidence-broker feature, not an AstrBot plugin, Hermes core change, or runtime-owned tool loop. The Vision Broker runs before `/api/chat/stream` enters AstrBot, Hermes, or any future runtime. It decides whether passive background context is enough, whether active live evidence is required, and when to retry or stop. For active/forced vision, the current screenshot and optional detail crops are handed to a multimodal runtime through `vision_frames` when the adapter supports it; `vision_frame` remains as the first-frame compatibility field. Passive background remains text-only.

It has two independent evidence lanes:

- Passive lane: a background screen timeline. It stores only the latest 5 semantic screen-change events in memory: `timestamp`, `frame_id`, `change_summary`, `important_objects`, `visible_text`, and `confidence`. Repeated frames without meaningful semantic change do not add events; they only update the latest event's observation time.
- Active lane: Ipet broker live evidence. It captures only when the broker or the local `/api/vision/observe` API requests active observe. The broker owns the Survey -> Select -> Focus -> Verify -> Detail loop, target selection, retry limits, and stop decision before the runtime receives the request. The answer model should inspect the active screenshot and detail frames themselves when a multimodal runtime path is available.

```text
/api/chat/stream receives user input
        |
        +--> Ipet Vision Broker evaluates passive/active evidence policy
        |       - runs before AstrBot/Hermes/future runtime adapters
        |       - owns Survey -> Select -> Focus -> Verify -> Detail target choice, retry, and stop decisions
        |       - emits bounded text evidence and, for active/forced multimodal paths, active screenshot/detail-frame evidence
        |
        +--> passive timeline may be injected as compact background context when available
        |       - format starts with `最近屏幕变化：`
        |       - max 5 events
        |       - background context only; it is not proof of a fresh live observation
        |       - passive timeline can suggest broker candidate targets but cannot replace active evidence
        |
        +--> if current live visual evidence is required, broker starts active observe
        |       - every active observe follows Survey -> Select -> Focus -> Verify -> Detail
        |       - first attempt is always `mode=desktop_survey`: hide Ipet, screenshot the desktop, enumerate visible window candidates, collect fallback candidates, and do not click
        |       - target selection scores unified `target_candidates`; it does not branch on browser/media/site names
        |       - `target_candidates` may come from window enumeration, host-native running-app discovery, screenshot regions, and passive timeline hints
        |       - System Events/window enumeration failures are recorded in `discovery_errors`; `desktop_targets=[]` is not final failure if fallback candidates exist
        |       - AstrBot mode uses the same pre-runtime active observe path for visual questions and `grounding_mode=always`
        |       - active screenshots and detail crops may be attached as `vision_frames`; `vision_frame` is the first frame for old adapters
        |       - macOS menu/window/target metadata stays routing/debug context and is not answer evidence
        |
        +--> internal active-observation call, or POST /api/vision/observe?lane=active for local clients
        |       - `/api/vision/observe` is an Ipet local API guarded by `X-Ipet-Local-Token`
        |       - it is not an AstrBot plugin dependency or recommended runtime path
        |
        +--> .pet_runtime_command active_vision_capture
        |       - desktop host temporarily hides/restores Ipet
        |       - payload supports `mode=desktop_survey|focus_target`, `target_id`, `target_candidates`, and `click_policy=window_focus_only`
        |       - macOS host enumerates `desktop_targets` with target_id, app, title, bounds, frontmost/minimized state, and a safe title-bar focus point
        |       - if System Events/window enumeration fails, host records `discovery_errors` and contributes fallback `target_candidates` from native running-app discovery and the active screenshot region
        |       - `focus_target` uses only safe focus/activation: Accessibility activate/raise plus a surveyed title-bar/window-top click, or host-native activation of an already running app candidate; it may not touch content controls
        |       - screenshot is captured only after a short settle delay, then verify metadata records the post-focus frame
        |       - active capture uses higher quality than passive capture, and may add a local detail crop for the selected target
        |
        v
active frame payload
        |
        +--> Vision Analyzer enrichment and VisionService storage
        +--> active_observation metadata:
             attempt_index, mode, target_id, target_hint, attempt_reason,
             discovery_errors, desktop_targets, target_candidates, selected_candidate,
             focused_target, action_trace, click_point, click_policy,
             focus_result, verify_result, detail_frames_count,
             foreground_app, window_title, frame_hash, self_occluded,
             relevance_hint, available_next_targets, attempted_targets, stop
        +--> broker may retry with another target or stop with bounded unknowns
        |
        v
/api/chat/stream injects bounded passive/active evidence text
        |
        v
active runtime consumes temporary evidence for this request only

main.py ScreenVisionController (passive/non-forced context)
        |
        +--> macOS: `/usr/sbin/screencapture` captures the current visible Space across all displays
        |
        +--> fallback: Qt enumerates `QGuiApplication.screens()`, captures each display, and composes one canvas
        |
        +--> scale and JPEG-encode in memory
        |
        +--> attach capture metadata:
             capture_backend, capture_scope=visible_spaces_all_displays, display_count, display_layout
        |
        +--> dispatch metadata observation and POST on a background daemon thread
             (overlapping captures are skipped while the previous POST is in flight)
        |
        +--> macOS foreground menu-bar/window metadata observation when available
        |
        v
POST /api/vision/frame
        |
        +--> lightweight routing in `backend.vision_state`
        |       - detects display layout, foreground app, window title, desktop context, and visual hash changes
        |       - reuses previous evidence for unchanged frames
        |       - waits for stable_after_change before expensive analysis
        |       - enforces vlm_cooldown_sec per desktop context
        |       - allows only one analyzer/VLM call in flight globally
        |
        +--> optional passive analyzer enrichment only when routing says new evidence is needed and `vision.analyzer.enabled=true`
        |       - `macos_vision_ocr` decodes the in-memory data URL to a temporary local file
        |       - invokes a mockable `/usr/bin/swift` + Vision OCR helper
        |       - keeps Swift module cache under the OS temp directory, not the user home cache
        |       - converts recognized text into bounded observations
        |       - `openai_compatible_vlm` / `local_vlm` send the image to the user-configured multimodal endpoint with the locally saved API key
        |       - passive direct VLM failure, timeout, provider-empty, or no observations does not invoke the active runtime or AstrBot chat queue as a vision substitute
        |       - while analysis is in flight, passive routing uses latest-wins: only the latest pending frame is retained, not a queue
        |
        +--> merge caller observations / analyzer observations / unknowns
        |
        v
backend.vision.VisionService stores latest frame plus passive timeline in memory only
        |
        +--> frame_id, frame_hash, captured_at, TTL, observations, unknowns, analyzer metadata
        +--> route status, recent events, capture backend/scope/display count, sanitized desktop context
        +--> passive timeline, max 5 semantic changes
        |
        v
/api/vision/context?lane=passive|active|merged returns passive timeline, active observation, or combined context
        |
        v
local client may inspect bounded temporary context
```

Defaults and boundaries:

- `vision.enabled` is `false` by default.
- `vision.active_observation.enabled` is `true` by default under the vision feature; when vision is enabled, forced visual requests actively observe before answering.
- `vision.active_observation.allowed_interaction` is normalized to `light` or `none`; `light` means window-focus-only active vision. It never types, drags, navigates, sends, deletes, submits, purchases, clicks content-area buttons/links/inputs, or performs generic UI control.
- `vision.passive_capture.use_for_forced` is retained for compatibility, but the passive lane should be treated as background timeline rather than proof that the model just looked at the screen.
- `vision.analyzer.enabled` is `false` by default and `provider=none`; no networked VLM provider is called unless the user explicitly enables the analyzer and configures a dedicated VLM endpoint/API key. Analyzer failures do not invoke AstrBot/Hermes chat as a vision substitute.
- `vision.routing` is enabled by default with `vlm_cooldown_sec=10` and `stable_after_change_ms=700`, so repeated timer frames do not repeatedly call VLM.
- Frames are not written to disk; `persist_frames` is normalized to `false`.
- `/api/vision/status`, `/api/settings/config`, and `/api/health` never expose image `data_url`; `/api/vision/frame`, `/api/vision/context`, `/api/vision/observe`, and `/api/vision/clear` require the local `X-Ipet-Local-Token` header.
- `/api/vision/status` and `/api/vision/context` expose evidence metadata such as `frame_id`, `frame_hash`, `freshness`, `evidence_count`, `grounded`, analyzer status, route decision, recent events, passive timeline, capture backend, capture scope, display count, sanitized desktop context, and sanitized active-observation routing metadata. `/api/vision/observe?lane=active&include_image=true` also returns active `discovery_errors`, `desktop_targets`, `target_candidates`, `selected_candidate`, `focused_target`, `focus_result`, `verify_result`, `detail_frames_count`, `action_trace`, `click_point`, `frame_hash`, and `available_next_targets`. Images are omitted unless `include_image=true` is requested with the local token or the active chat path attaches temporary runtime image frames; raw screenshots are never persisted.
- Vision Analyzer v2 can add VLM or OCR observations with the same evidence contract: `observations[{claim,evidence,region,confidence,source}]`, `unknowns[]`, and `summary`, all bounded and sanitized before prompt injection.
- Analyzer failures are recorded as bounded `unknowns` plus analyzer `last_error`; they do not create fake observations.
- Without active observations, Ipet injects only passive timeline background. The runtime should say it is based on recent background screen changes, not claim it just inspected a fresh screenshot. If the passive timeline is insufficient, it should say the current background vision cannot confirm.
- With active broker evidence, the runtime should inspect the attached active screenshot/detail frames when present, first reading the main content area, large headings, and prominent text. If the text is not readable, it should say so. Bounded active observations are supporting evidence, and passive timeline is only background. Menu-bar/frontmost-app/window-candidate metadata can help select/focus a target or debug the broker, but it must not be treated as proof of visible screen content.
- Active observe is an Ipet broker operation. The broker owns target choice, retry, and stop decisions. It stops active visual investigation after at most 3 active observe attempts: 1 desktop survey plus at most 2 focus/verify attempts. It also stops after repeated `frame_hash` or repeated `target_id`, when no focusable or useful candidates remain, on tool/permission/host failure, when total elapsed time exceeds the configured limit, or when the question no longer needs visual evidence. If `desktop_targets=[]` but fallback `target_candidates` exist, the broker can still select/focus/verify or pass the active screenshot/detail frames to a multimodal runtime; it must not stop solely because window enumeration failed.
- If no evidence remains, Ipet sends bounded `unknowns` and the runtime must answer “我无法从当前截图确认” without inventing observations.
- Active-observation failures are surfaced as bounded `unknowns` and never become fake observations.
- `/api/vision/observe` remains available as an Ipet local API for desktop host calls, debugging, and local integrations. It is not an AstrBot plugin contract. Old AstrBot bridge skeletons are deprecated reference helpers, not the recommended runtime path.

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

- In default AstrBot mode, plugins, MCP, providers, knowledge bases, and QQ/NapCatQQ are managed in AstrBot WebUI. Ipet skills/MCP routes return compatible empty payloads with details that these surfaces are managed there.
- In manually selected Hermes mode, skills and MCP routes proxy Hermes and return empty clear-status payloads when unavailable.

Legacy `third_party_skills/` and `third_party_mcp/` directories are no longer read by the default settings/API path.

## NapCatQQ

NapCatQQ is not an Ipet runtime adapter. It is an AstrBot-managed OneBot v11 platform:

- AstrBot listens for the OneBot v11/aiocqhttp connection.
- NapCatQQ connects as a reverse WebSocket client, typically to `ws://127.0.0.1:6199/ws`.
- Ipet stores and displays the connection URL and guide metadata only.
- Ipet does not write NapCat configuration, read QQ messages, send QQ messages, manage QR login, or store OneBot tokens.

## Settings Page

The settings page shows active runtime controls and status alongside desktop-owned settings:

- active runtime selection, defaulting to AstrBot
- AstrBot enabled/auto-start/base URL/API key/session username/NapCatQQ guide metadata
- Hermes enabled/auto-start/base URL/health path/command/cwd/startup timeout for the manual secondary / advanced path
- Live2D/model resources, expressions, motions, and background
- TTS and ASR
- automatic vision controls and status, without screenshot preview
- chat rendering options and approval-facing prompt metadata
- resource allowlists
- window and pet behavior

The settings page does not expose local provider/router controls or app-level memory/summary controls.

## Shutdown

On app exit, `main.py` stops only processes started by this project:

- ASR service started by the desktop shell
- FastAPI backend started by the desktop shell
- AstrBot or Hermes sidecar started by the desktop shell

Externally managed AstrBot or Hermes processes are left alone.

## Local State And Cleanup

The app and active runtime may create local-only state while the app runs:

- `.pet_runtime_command*.json` and `.pet_runtime_host.heartbeat.json` for desktop/backend command exchange
- `backend/audio_cache/` for generated TTS output
- `data/ipet_conversations/` for Ipet-owned conversation history and temporary runtime cleanup records
- `data/ipet_memory/` for local long-term memory
- `data/chat_topics/` for ignored legacy compatibility topic history during migration
- `.runtime-logs/`, `.uv-cache/`, `.venv/`, and third-party `node_modules/`
- `fileplay/` when file-output skills create dated reports such as hotspot summaries

These paths are ignored and are not part of the committed workflow. Clean them only when the app is not relying on the state, and treat deletion as a destructive action that needs explicit approval in agent-driven work.
