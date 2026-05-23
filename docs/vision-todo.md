# Vision Todo: Neuro-sama Style Automatic Vision

> This document is the working roadmap for evolving Ipet vision from a grounded screenshot guardrail into a Neuro-sama style automatic perception and proactive conversation loop.

## Current Baseline

Ipet vision v1 can capture the screen, keep the latest frame in memory, attach bounded observations, and inject grounded evidence into the active runtime. It prevents unsupported visual claims by forcing the runtime to say "我无法从当前截图确认" when no fresh evidence exists.

The current gap is understanding and initiative. v1 can hold evidence, but it does not yet continuously analyze images, maintain a world state, detect meaningful changes, or decide when to speak first.

## Design Principles

- Build perception before moderation. Heavy privacy/safety gating is postponed until the system can reliably see, summarize, and produce evidence.
- Keep every visual claim evidence-backed with `claim`, `evidence`, `region`, `confidence`, and `source`.
- Do not send raw screenshots to AstrBot. Ipet owns capture, analysis, state, and evidence governance; AstrBot receives temporary text evidence.
- Prefer local analyzers first: macOS UI metadata, OCR, accessibility tree, and optional local model adapters.
- Proactive speech must start as candidates, not direct output. Dispatch comes only after candidate quality and cooldown behavior are testable.

## Phase 1: Vision Analyzer v2

Goal: turn captured frames into structured observations automatically.

Implementation note:

- `backend/vision_analyzer.py` now defines the v2 analyzer boundary, merge helpers, bounded analyzer metadata, and a mockable local command runner.
- Implemented analyzer providers now include `macos_vision_ocr`, `openai_compatible_vlm`, `local_vlm`, and active-runtime fallback. OCR decodes `data:image/jpeg/png;base64` frames to a temporary local file and invokes a local `/usr/bin/swift` helper using Apple's Vision OCR framework. VLM providers send the image to a user-configured multimodal endpoint using the locally saved VLM API Key, and provider-empty/unavailable cases can fall back to the current active runtime such as AstrBot.
- The settings page lets the user enter VLM Base URL and VLM API Key directly, fetch the endpoint's model list through `/api/vision/models`, and one-click fill the selected model name into the analyzer config.
- `/api/vision/frame` invokes the analyzer only after local token validation and only when `vision.analyzer.enabled=true`; analyzer failure adds `unknowns`/`last_error` without fabricating observations.
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
- No external network call is made by default; VLM or AstrBot fallback requires `vision.analyzer.enabled=true`.

## Phase 2: Screen State And Diff

Goal: understand change over time instead of treating every frame as unrelated.

Current status:

- `backend/vision_state.py` adds a lightweight routing state with event detection for `display_layout_changed`, `foreground_app_changed`, `window_title_changed`, `desktop_context_changed`, `visual_hash_changed`, and `stable_after_change`.
- `/api/vision/frame` evaluates routing before analyzer enrichment. Repeated unchanged frames reuse existing evidence, cooldown blocks repeated VLM calls for the same desktop context, and a global analyzer lock prevents parallel VLM calls.
- `main.py` now sends capture metadata and prefers macOS current-visible-Space all-display capture through `/usr/sbin/screencapture`, with Qt all-screen composition as fallback.
- `/api/vision/status` and `/api/vision/context` expose route/capture metadata without exposing `data_url` by default.
- Settings expose visual change threshold, VLM cooldown seconds, and stable-after-change milliseconds.

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

Scope:

- Add a candidate engine that receives vision events and returns bounded candidate messages.
- Add cooldowns by event type and global cooldown.
- Add confidence thresholds.
- Keep candidates separate from chat history until explicitly dispatched.

Expected files:

- `backend/proactive.py`: event-to-candidate policy and cooldowns.
- `backend/app.py`: local APIs for pending candidates.
- `tests/test_proactive.py`: candidate generation and cooldown tests.

Definition of done:

- A foreground app change can generate one candidate.
- The same event does not generate repeated candidates inside cooldown.
- Low-confidence events are ignored or produce an internal unknown, not a spoken message.

## Phase 4: Proactive Dispatch

Goal: allow Ipet to speak first under controlled conditions.

Scope:

- Add APIs:
  - `GET /api/proactive/pending`
  - `POST /api/proactive/dispatch`
  - `POST /api/proactive/clear`
- Add frontend/runtime bridge support for displaying or sending proactive messages.
- Mark proactive messages with `source=vision_proactive`, `event_id`, and `frame_id`.
- Do not pretend proactive output came from the user.

Expected files:

- `backend/app.py`: proactive endpoints.
- `index.html`: display and dispatch support.
- `tests/test_proactive_api.py`: API contract tests.

Definition of done:

- A vision event can become a visible pending proactive message.
- Dispatch sends the message through the active runtime with evidence context.
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

Start with Phase 1 only: Vision Analyzer v2.

Do not implement proactive dispatch, activity-specific modes, memory, or the Phase 7 privacy hardening in the same task. The analyzer should make Ipet better at producing grounded observations while preserving the existing v1 behavior: evidence-backed answers only, and refusal when evidence is missing.
