# Vision Todo: Neuro-sama Style Automatic Vision

> This document is the working roadmap for evolving Ipet vision from a grounded screenshot guardrail into a Neuro-sama style automatic perception and proactive conversation loop.

## Current Baseline

Ipet now has a privacy-first ambient presence loop in addition to turn-scoped visual grounding. The desktop can sample foreground application and idle time, keep expiring screen state, route meaningful changes, create shadow or active proactive opportunities, ask the configured persona to return only `say` or `stop`, deliver through the pet UI/TTS, and feed delivery outcome back into cooldown policy.

The remaining gap is richer activity-specific perception. Coding, desktop, and chat modes still need distinct evidence rules; generic window or OCR changes must not be treated as semantic understanding.

## Design Principles

- Privacy filtering precedes frame storage, analysis, candidate generation, and dispatch.
- Keep every visual claim evidence-backed with `claim`, `evidence`, `region`, `confidence`, and `source`.
- Do not send raw screenshots by default. Ipet owns capture, analysis, state, and evidence governance; optional analyzer providers must be configured explicitly by the user.
- Prefer local analyzers first: macOS UI metadata, OCR, accessibility tree, and optional local model adapters.
- Proactive speech must start as candidates, not direct output. Dispatch comes only after candidate quality and cooldown behavior are testable.

## Phase 1: Vision Analyzer v2

Goal: turn captured frames into structured observations automatically.

Implementation note:

- `backend/vision_analyzer.py` now defines the v2 analyzer boundary, merge helpers, bounded analyzer metadata, and a mockable local command runner.
- Implemented analyzer providers now include `macos_vision_ocr`, `openai_compatible_vlm`, and `local_vlm`. OCR decodes `data:image/jpeg/png;base64` frames to a temporary local file and invokes a local `/usr/bin/swift` helper using Apple's Vision OCR framework. VLM providers send the image only to a user-configured multimodal endpoint using the locally saved VLM API Key.
- Passive analyzer configuration remains an advanced local-config contract rather than a public settings control. The settings Human Ops page separately configures the turn-scoped observe model.
- `/api/vision/frame` invokes the analyzer as a serialized background task only after local token validation and only when `vision.analyzer.enabled=true`; stale analysis is discarded and analyzer failure adds `unknowns`/`last_error` without fabricating observations.
- Caller-supplied observations remain first-class and are merged with analyzer observations using bounded claim/source de-duplication.

Scope:

- Add a real analyzer layer that can consume a captured image frame and return observations.
- Keep macOS menu-bar foreground app metadata as one analyzer source.
- Add an OCR-capable local analyzer path where available, preferably macOS Vision through a small local helper or another no-network local command.
- Add an optional OpenAI-compatible or local VLM adapter contract, disabled by default.
- Merge analyzer output with any observations already supplied by capture.
- Bound all analyzer output before prompt injection.

Expected files:

- `backend/vision_analyzer.py`: analyzer models, local analyzer runner, output sanitization, merge helpers.
- `backend/vision.py`: accept analyzer-enriched frame payloads and expose analyzer metadata in status/context.
- `backend/app.py`: invoke analyzer on `/api/vision/frame` when `vision.analyzer.enabled` is true.
- `main.py`: keep collecting local UI metadata during capture and pass it as observations.
- `settings.html`, `settings.js`, `settings.css`: expose analyzer status/config only if needed.
- `tests/test_vision_analyzer.py`: analyzer unit tests with fake local command output.
- Existing vision tests: update for analyzer merge and no-image-leak guarantees.

Definition of done:

- A frame with no caller-supplied observations can gain observations through the analyzer path.
- A frame with caller-supplied observations merges them with analyzer observations without duplication or unbounded prompt growth.
- OCR/text observations carry source, confidence, and region.
- Analyzer failures are surfaced as `unknowns` or `last_error`, not as fabricated observations.
- No external network call is made by default; VLM analysis requires `vision.analyzer.enabled=true` and a user-configured endpoint.

## Phase 2: Screen State And Diff

Goal: understand change over time instead of treating every frame as unrelated.

Current status:

- `backend/vision_state.py` adds a lightweight routing state with event detection for `display_layout_changed`, `foreground_app_changed`, `window_title_changed`, `desktop_context_changed`, `visual_hash_changed`, and `stable_after_change`.
- `/api/vision/frame` evaluates routing before analyzer enrichment. Repeated unchanged frames reuse existing evidence, cooldown blocks repeated VLM calls for the same desktop context, and a global analyzer lock prevents parallel VLM calls.
- `main.py` now sends capture metadata and prefers macOS current-visible-Space all-display capture through `/usr/sbin/screencapture`, with Qt all-screen composition as fallback.
- `/api/vision/status` and `/api/vision/context` expose route/capture metadata without exposing `data_url` by default.
- Routing thresholds remain normalized local configuration; the public settings page exposes the safer Environment mode, intensity, privacy, and initiative controls.

Scope:

- Add a screen state store for foreground app, window title, OCR text, summary, frame hash, and recent observations.
- Add event detection:
  - `display_layout_changed`
  - `foreground_app_changed`
  - `window_title_changed`
  - `desktop_context_changed`
  - `visual_hash_changed`
  - `stable_after_change`
- Skip expensive analysis when frame hash and metadata have not changed enough.
- Keep a short rolling history in memory only.

Expected files:

- `backend/vision_state.py`: screen state, diff engine, event generation.
- `tests/test_vision_state.py`: state transitions and diff tests.
- `docs/WORKFLOW.md`: update data flow.

Definition of done:

- Switching foreground app creates one state event.
- Repeated unchanged frames do not create repeated events.
- State expires cleanly with the existing vision TTL.
- Analyzer calls are serialized globally and unchanged frames do not call VLM again.

## Phase 3: Proactive Candidate Engine

Goal: generate possible主动发言 without sending them yet.

Current status: implemented by the bounded process-local `EnvironmentService`. `shadow` mode records `would_speak` audit entries without calling Brain; confidence, quiet hours, cooldowns, daily limits, and unanswered backoff suppress noisy candidates.

Scope:

- Add a candidate engine that receives vision events and returns bounded candidate messages.
- Add cooldowns by event type and global cooldown.
- Add confidence thresholds.
- Keep candidates separate from chat history until explicitly dispatched.

Implemented files:

- `backend/environment.py`: event-to-opportunity policy, privacy, cooldowns, and process-local audit.
- `backend/environment_routes.py`: authenticated ingestion and safe status/pulse/feedback APIs.
- `tests/test_environment.py`: opportunity, privacy, cooldown, backoff, and reply-context tests.

Definition of done:

- A foreground app change can generate one candidate.
- The same event does not generate repeated candidates inside cooldown.
- Low-confidence events are ignored or produce an internal unknown, not a spoken message.

## Phase 4: Proactive Dispatch

Goal: allow Ipet to speak first under controlled conditions.

Current status: implemented through `/api/environment/pulse` and `/api/environment/feedback` plus `frontend/proactive_presence.js`. Delivery never fabricates a user message or persists a synthetic exchange. The older proposed `/api/proactive/*` names were replaced by the single environment surface.

Scope:

- `GET /api/environment/status`
- `POST /api/environment/pulse`
- `POST /api/environment/feedback`
- `POST /api/environment/clear`
- Frontend presentation and optional TTS without pretending output came from the user.

Implemented files:

- `backend/environment_routes.py`: proactive endpoints and Brain `say/stop` restriction.
- `frontend/proactive_presence.js`: client busy gate, message/TTS delivery, and feedback.
- `tests/test_environment_routes.py` and `tests/test_frontend_proactive_presence_source.py`: API and frontend contracts.

Definition of done:

- A vision event can become a visible pending proactive message.
- Dispatch sends the message through Brain with evidence context.
- Clearing pending messages does not affect chat history.

## Phase 5: Activity-Specific Vision Modes

Goal: move from generic screen descriptions to task-aware perception.

Initial modes:

- `desktop_assistant`: foreground app, windows, menus, settings pages.
- `coding_companion`: terminal failures, IDE diagnostics, test output, GitHub PR pages.
- `chat_observer`: visible chat message changes without copying private content by default.

Definition of done:

- Each mode has its own analyzer prompt/heuristics and event rules.
- The mode selector can be automatic from screen state or manual from settings.
- Mode-specific observations remain compatible with the same evidence schema.

## Phase 6: Memory And Personality Integration

Goal: make proactive vision feel like a character, not a logging system.

Current status: the active opportunity reuses the selected persona, bounded recent conversation, and approved relationship context. The delivered utterance is process-local one-turn context for a natural reply; raw environment evidence is not durable memory. Intensity levels `quiet`, `balanced`, `active`, and `neuro_like` are available.

Scope:

- Store recent visual context in short-term memory only.
- Allow user-approved visual facts to enter longer-term memory.
- Tune proactive speaking style by intensity:
  - quiet
  - balanced
  - active
  - neuro-like

Definition of done:

- Repeated context does not cause repetitive chatter.
- Persona affects phrasing but never overrides evidence.
- User can tell Ipet to stop watching a window or topic.

## Phase 7: Privacy, Moderation, And Hardening

Goal: add the stricter safety layer after the perception loop works.

Current status: baseline hardening is implemented. Sensitive built-in markers and user blocked apps run before frame storage, window titles are double-gated, sensor/frame ingestion uses a local token, browser-facing APIs reject untrusted origins, and settings expose safe audit reasons. Content classifiers and per-window allow lists remain future work.

Scope:

- Sensitive-content classifiers for credentials, private messages, financial/medical/identity data, and personal photos.
- A local redaction path before observations reach proactive dispatch.
- Audit status that explains why the agent stayed silent.
- Stronger settings controls for capture scope and disabled apps.

Definition of done:

- Sensitive visual content is not proactively repeated.
- Users can configure app allow/deny lists.
- The system can explain "I stayed silent because the screen looked private" without exposing the private content.

## Immediate Implementation Target

Build Phase 5 adapters on top of the completed privacy and initiative contracts. Start with `coding_companion`: accept only evidence-backed test/build success or failure labels, keep raw terminal text out of proactive state, and measure false-positive and repetition rates in shadow mode before enabling active delivery.
