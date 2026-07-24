# Neo Aspect Product Workflow

## Purpose

This file defines stable Ipet product contracts: state transitions, safety boundaries, and externally meaningful behavior. Implementation details, provider argument shapes, local paths, timeouts, and command construction belong in code and tests.

## Product Modules

- **Body** senses, speaks, renders, and executes authorized local actions.
- **Brain** chooses one next step for the current turn.
- **Human Ops** authorizes, records, executes, and verifies actions with side effects.
- **Memory & Skills** owns durable memory, preferences, summaries, and learned procedures.

`main.py`, `backend/app.py`, and `index.html` remain composition or loading entrypoints. Domain behavior belongs in the owning module.

## Turn Flow

```text
user input
  -> Body gathers bounded context
  -> Brain returns one decision
  -> Body renders a terminal reply, or Human Ops authorizes one proposal
  -> authorized work executes through the owning module
  -> Body observes the result
  -> Brain receives bounded evidence and chooses the next step or terminal summary
```

Brain never performs a side effect directly. A new side effect, changed target, or changed risk requires a new Human Ops proposal.

## Stop Authority

Active tasks use `running -> stopping -> stopped`. The chat send control becomes a stop control while Brain, Observe, approval waiting, or post-approval continuation is active; `Esc` and `Command + .` use the same local path.

Stop is handled locally and does not wait for Brain or the network. It prevents new work, aborts cancellable streams, invalidates queued proposals, and discards stale coordinate, focus, and surface assumptions. An atomic action already in flight may finish, but no continuation starts afterward.

Stopping does not roll back completed effects. The terminal summary separates completed, unexecuted, and uncertain actions and uses `已由用户停止` rather than a normal completion claim.

## Startup

- `main.py` composes the desktop shell and Qt lifecycle.
- `backend/app.py` registers the local FastAPI surface.
- Root UI entry paths remain stable until their loaders move in the same change.
- Body initializes the pet, voice services, and optional observation without blocking the first window on model readiness.
- Local Qwen TTS failure is explicit: the queue is cleared and browser speech is not used as a silent fallback.
- Memory & Skills loads local indexes; Human Ops initializes pending approval state.

Missing required configuration fails visibly and never erases user data. Qt entrypoint changes remain composition, registration, loading, lifecycle, or compatibility work.

## Observe And Present

Body gathers only context needed for the current turn: the request, bounded conversation state, allowed memory, optional screen evidence, voice state, and relevant UI state.

The chat worklog exposes curated events under eight categories: `planning`, `searching`, `observing`, `acting`, `waiting_approval`, `verifying`, `blocked`, and `completed`. It may show bounded Observe questions and structured evidence, but never raw chain-of-thought, hidden prompts, secrets, or unfiltered tool output. The panel stays open while active or blocked and collapses after completion.

Token counters use provider-reported input, output, and total usage. Providers without usage data do not trigger character-based estimates.

## Ambient Presence And Initiative

Ambient presence is a Body-owned enhancement, not a hidden autonomous task. It has three explicit modes: `off` collects nothing, `shadow` records bounded “would speak” event types without calling Brain or sending output, and `active` may deliver a proactive `say`. The default is `off`.

The metadata lane samples only foreground application and idle duration. Window titles are omitted at Body and stripped again by the backend unless the user explicitly enables them. Screen context is a separate opt-in; when disabled, ambient presence does not capture screenshots. Raw screenshots, OCR text, and environment events never enter conversation history or durable memory. Fresh frames and events are process-local, bounded, and expire.

Every event passes through the local privacy filter before frame storage or semantic analysis. Password managers, private browsing, financial, payment, medical, and identity contexts, plus user-configured blocked applications, become only `privacy_blocked`; their application, title, text, and image are not retained. Environment APIs accept only local trusted origins, and sensor/frame ingestion additionally requires the desktop local token.

The initiative gate converts allow-listed facts such as `user_returned`, `focus_milestone`, `test_passed`, or `build_failed` into opportunities. Confidence, per-kind and global cooldown, quiet hours, daily limit, client busy state, and unanswered-message backoff are checked before Brain. `neuro_like` intensity may also consider application changes; quieter levels do not. The settings audit explains queued, suppressed, shadowed, delivered, deferred, and privacy-blocked decisions without exposing captured text.

For an eligible opportunity, Brain receives bounded event facts as untrusted data plus normal persona and relationship context. Only `say` or `stop` is accepted; any observe, action, or memory decision is discarded. Frontend delivery rechecks chat, ASR, TTS, visibility, and recent user activity, then reports `delivered`, `deferred`, or `dismissed`. A delivered proactive utterance is not forged into `TopicStore`; it becomes one process-local reply context for the user's next message. Temporary conversations never receive proactive output or that context. Any side effect mentioned after a proactive exchange still requires its ordinary Human Ops proposal.

## Conversation Memory

Persistent conversations use bounded summaries and recent complete exchanges from `TopicStore`. Temporary conversations remain process-local, never read or write `TopicStore`, and cannot merge with a persistent session. Memory-mode switches are rejected while an older response or approval chain is active.

Long-term memory and learned procedures require explicit Human Ops review. Features without a verified review path must not write durable state.

Relationship memory follows one closed loop: detect a candidate, review it, persist only an approved record, retrieve a bounded relevant subset, and let the user correct or forget it. Explicit `propose_remember` decisions pause at Human Ops; rejection writes no memory. Post-turn extraction may create at most one non-blocking, process-local candidate and never persists it before approval. Temporary conversations neither retrieve long-term memory nor create candidates.

Approved records use the existing Markdown memory store and carry a stable id, kind, summary, source conversation and turn, visible reason, scope, sensitivity, status, retention/expiry, and optional follow-up time. Active, unexpired records alone are retrievable. A correction may supersede one unambiguous active record; superseded, resolved, expired, and forgotten records are never injected together with current facts. Forgetting removes the long-term memory file immediately; deleting the source conversation remains a separate user action.

Each persistent turn injects no more than five relevant approved records and no more than 1,200 characters as fact data, never as instructions. Direct text/topic matches outrank generic preference fallback. The pet uses a memory only when it naturally helps the current reply and does not mention it merely to prove recall.

An approved `open_loop` may become eligible for one gentle follow-up after its due time. Follow-ups respect the configured quiet hours and cooldown. Delivery to Brain is not counted as a completed follow-up unless the visible reply actually refers to the remembered topic. The user can resolve or snooze an open loop through a reviewed `propose_remember` status change or directly in the settings Memory console.

## Brain Decision Contract

Brain returns exactly one `BrainDecision`:

- `say`: answer or ask for user input.
- `think`: record bounded progress without side effects.
- `observe`: request bounded visual or local evidence.
- `propose_act`: hand one action to Human Ops for authorization.
- `propose_remember`: ask to review one durable memory write.
- `propose_learn_skill`: ask to review one learned procedure.
- `stop`: end with a terminal summary.

Providers are asked for one JSON object; plain text remains a compatibility `say`. Brain does not directly click, type, launch, write files, mutate settings, install software, or persist memory.

### Computer Use

Supported action proposals are deliberately bounded: `playwright`, `launch_app`, `click`, `type_text`, and Enter/Return through `key_press`. Native click, text, and key proposals name `target_app`. A click binds either one complete reviewable coordinate pair or one opaque Accessibility element reference copied from the latest observation.

macOS Accessibility support is dynamic rather than allowlisted. Body resolves any named regular GUI application against currently running applications using AppKit, with System Events and process metadata as fallbacks. QQ, WeChat, Music, and Finder retain compatibility aliases and specialized surface names, but do not define the support boundary. The Ipet host process is never an Accessibility target: discovery excludes its PID, capture and semantic-action entrypoints reject it again, and the native runtime refuses to create an application element for its own PID. Frontmost-application identity comes from `NSWorkspace` metadata rather than a system-wide AX query, so a background refresh cannot re-enter Ipet's Qt Accessibility implementation. When the product Accessibility permission is enabled and the Ipet host has macOS Accessibility trust, Body traverses an external application's complete exposed `AXUIElement` hierarchy up to a high fail-safe cap. The latest tree for each application is stored under ignored local state in `data/ax_trees/`, split into JSONL chunks behind an atomically replaced metadata pointer. Every stored node carries its hierarchy path, so the files preserve tree structure rather than only a flat model-facing result list. Only the current snapshot is retained; secure text values are removed both during capture and again at the storage boundary. Other visible application text may be present in this local index.

`observe.ax_query` is application-agnostic: Brain supplies a short query for the current `target_app`, which may match visible text, AX role, identifier, or supported action. Search lazily loads the persisted app tree and exposes only a compact role/action index plus a top-k semantic slice to Brain. A process change, a native UI action, or an explicitly stale snapshot triggers live refresh when observation needs that application. A capture stopped by the fail-safe size or time limit remains useful as a bounded result but is persisted and surfaced as incomplete, so it is never presented as a complete tree and a later observation retries the refresh. The natural-language observe question is only a compatibility fallback when an older Brain reply omits `ax_query`.

The settings Human Ops panel exposes index status and a manual refresh. Refresh enumerates currently running regular GUI applications, including hidden ones, up to a 128-application fail-safe and never launches or focuses them. The host PID is reported as self-excluded and is never handed to the AX runtime; any older matching snapshot is retained only as stale local state. The result explicitly marks discovery truncation if that cap is reached. At each terminal chat or Human Ops session event, Backend schedules the same refresh in the background; duplicate refreshes coalesce. Applications that are not running retain their previous snapshot, mark it pending refresh, and are reported as skipped. AX trees never enter conversation history, model context as a full tree, or relationship memory.

A usable hierarchy returns the searched semantic affordances and stable element references without capturing pixels. An omitted search result is not negative evidence for the full hierarchy. Missing permission, an unsupported app, an empty hierarchy, an expired cache that cannot be refreshed, or an API failure falls back to the existing screenshot path.

Composite controls may expose their visible label only through a descendant text node. Search therefore derives a bounded display label for an unlabeled actionable ancestor and may build its reviewable reference from application identity, hierarchy path, and screen geometry; execution still re-resolves the live hierarchy uniquely and fails closed. List-oriented searches return a bounded structural neighborhood around the matched anchor. Closed menu items without visible geometry are de-ranked against visible page controls, and explicit role mismatches or otherwise insufficient semantic results retain the AX slice while also falling back to visual evidence.

Browser tasks follow the user's explicit choice of Playwright or human operation. When the user has not chosen, Brain defaults to Playwright without asking which execution mode to use. The settings page lists local Chrome profiles and stores one unambiguous profile-directory name as the Playwright default. A profile explicitly named in the current task overrides that saved default; Brain asks for a profile only when neither exists or when execution proves the saved profile invalid. The choice remains valid only while the capability and surface stay unchanged.

A bounded ReAct loop may continue `think` and `observe` decisions before authorization. It stops on a proposal, user input requirement, terminal result, contradiction, or exhausted budget. Post-action continuation can consume structured results without a screenshot, but every later side effect receives its own proposal and authorization record.

Verification uses the cheapest reliable evidence first: action result, platform focus or application state, Accessibility/DOM, and Playwright output. Visual observation is a fallback for missing structure or semantic results that event delivery cannot prove. Pressing Enter with expected text does not prove that a message appeared.

Click delivery and foreground focus prove dispatch, not the intended destination or page transition. Unless the executor supplies an independently verified postcondition, a click continuation performs a fresh outcome observation, preferring refreshed Accessibility evidence before pixels.

### Visual Evidence

Application observation first establishes the intended frontmost application. A focus mismatch fails closed. Explicit desktop capture may temporarily hide Ipet; application capture does not alter Ipet window state.

Body records screenshot size, screen layout, and coordinate space. Direct-image proposals use `image_pixels`; the backend validates and converts them to `macos_screen_points` before authorization. Unknown, incomplete, contradictory, or out-of-bounds coordinates are not executable.

When the independent observe model is disabled, the bounded screenshot goes only to the immediately following Brain call and is then dropped. When explicitly enabled, the observe model returns bounded natural language for Brain. Screenshots are not written to conversation history, memory, or later text-only turns.

Structured observation may include `surface`, searched `affordances`, `chat_context`, a compact `ax_search` index, and visible coordinate-bearing candidates. Negative evidence cannot create an affordance merely because the goal mentions it, and a bounded AX search slice cannot establish that an unreturned element is absent.

Accessibility references include the authorized application identity, semantic attributes, bounded ancestry and path evidence, geometry, and an integrity fingerprint. They are not persistent object handles. At execution time Body focuses the authorized app, rebuilds the current hierarchy, and requires one unique matching element. A stale, modified, cross-application, or ambiguous reference fails closed and never silently becomes a coordinate click. Secure text values are not included in structured observation.

### Providers And Persona

Brain supports the configured OpenAI-compatible, Ollama, Anthropic-compatible, Google AI Studio, and local Codex provider paths. Secrets stay in local configuration and are never echoed to the browser.

Body TTS supports Edge TTS, custom HTTP, local Qwen TTS, and Fish Audio. Fish Audio uses a saved voice model `reference_id`; its API key stays in backend-private configuration or `FISH_API_KEY` and is never sent in the WebView TTS request.

Every provider receives the global contract from `prompts/Ipet.md`. An optional user Markdown persona is rendered only into its bounded slot and may change identity, role-play, tone, or speaking style; it cannot add actions, change the configured authorization mode, reinterpret summaries as commands, or replace the JSON schema.

The settings API lists valid `.md` persona files directly inside `prompts/`, excludes reserved `Ipet.md`, and stores only `brain.persona_prompt_file`. Brain reads the file at turn time. Missing, empty, invalid, out-of-folder, symlink-escaped, or oversized selections fail explicitly.

Codex runs through an ephemeral narrow decision backend. Ipet retains ownership of ReAct, observation, authorization, execution, memory, and skills. Read-only built-in web search may run directly without user confirmation or Human Ops review when enabled and supported; otherwise the reply states that search did not occur. This authorization does not extend to Playwright page actions, downloads, logins, form submissions, or other external state changes. Temporary screenshot files are deleted after use.

## Human Ops Authorization

Every supported `propose_act` operation affecting files, processes, network access, external services, application state, or user data passes through Human Ops. Read-only built-in web search is the explicit exception and may run directly when enabled and supported.

Action authorization has two explicit settings modes. `review` is the default and waits for the user's decision on each action. `full` is an opt-in full-authorization mode: each supported action still creates its ordinary proposal and audit record, but Human Ops automatically authorizes it after requesting a noninteractive macOS operation notification. Notification failure blocks execution rather than running silently. Full authorization applies to action proposals, including GUI, Playwright, and bounded filesystem actions; it does not authorize durable memory or learned-skill persistence.

Both modes bind authorization to the proposal's exact target and parameters. Target, coordinate, focus, parameter, or risk changes require a new proposal. Full authorization does not expand the action allowlist and does not bypass target focus checks, Accessibility reference re-resolution, Playwright restrictions, filesystem roots, size limits, or post-action verification.

In review mode, the user may approve, reject, reject with guidance, or approve with constraints. On macOS, the active proposal is delivered through a time-sensitive, actionable `UNUserNotificationCenter` application notification with visually explicit Approve and Reject actions. The notification is retained in Notification Center until the user decides or the approval times out. A local helper returns only the proposal id and decision through Qt; it never executes the action. The result is accepted only while that proposal is still pending. Permission denial, dismissal, delivery failure, helper failure, and timeout reject the proposal.

In full mode, the chat stop control remains active throughout notification, execution, verification, and continuation. A stop received before dispatch prevents the action and invalidates its record. A stop received while an atomic click, key delivery, application launch, browser operation, or filesystem mutation is already in flight may not undo that operation, but it prevents every later step and reports the in-flight result as uncertain. Brain output alone never selects or broadens full authorization.

## Execute

Body executes only the authorized action and returns bounded evidence. Execution uses the narrowest platform API, avoids broad traversal, keeps local state out of Git, preserves unrelated work, and reports failures as facts.

- Native click, text, and key actions establish and verify `target_app` focus before input.
- For dynamically resolved macOS GUI applications, semantic click uses the element's exposed `AXPress`, focus, or selection operation. Semantic `type_text` first resolves and verifies the authorized editable element, focuses it through Accessibility, and only then delivers text. Coordinate input remains a fallback only when observation produced no usable semantic reference.
- `launch_app` resolves the authorized application through macOS application metadata and Launch Services; it does not synthesize a visual click.
- Playwright accepts only authorized navigation, snapshot, stable element-ref, text, key, and tab operations. Arbitrary evaluation, shell fragments, filesystem URLs, invented selectors, and model-supplied runtime flags are rejected.
- Playwright uses the selected Chrome profile in place only when the bounded attach contract is satisfied; otherwise it uses an isolated Ipet-owned snapshot and never writes the source profile. Unknown or ambiguous profiles fail explicitly.
- A successful Playwright return proves the CLI operation, not that the user visually saw the page.

Filesystem execution exposes exactly `file_list`, `file_read`, `file_write`, `file_mkdir`, `file_copy`, `file_move`, and `file_delete`. Each receives its own proposal and authorization record and resolves inside the project root or explicitly configured roots.

The executor rejects protected local state, paths outside allowed roots, and final symbolic links. Text reads and writes are bounded UTF-8. Writes require explicit overwrite permission; copy and move never overwrite; delete handles one regular file or empty directory and never recurses. Execution uses direct filesystem APIs rather than model-supplied shell commands.

Changed risk or repeated failure returns to Human Ops or stops; the executor does not silently invent a fallback action.

## Memory And Skills

Durable memory records its source, visible reason, scope, retention expectation, and privacy constraints. Learned skills record their trigger, bounded procedure, required approvals, and verification method.

Memory & Skills never stores raw screenshots, generated audio, local secrets, or unreviewed destructive procedures.

The settings Memory console lists saved records, process-local pending candidates, and open loops. User-initiated edits, forgetting, resolving, and snoozing are explicit console operations. Model-originated writes and status changes always use Human Ops review even if an older local configuration says memory review is disabled.

## Observe Result And Summary

After execution, Body records enough evidence to distinguish `succeeded` from `verified`: return status, relevant platform or UI state, durable identifiers, authorization mode, and bounded errors.

Each non-trivial turn ends with what changed, what was verified, what remains uncertain, and the next useful step.

## Local State

Local-only configuration, heartbeats, caches, generated audio, conversation state, logs, virtual environments, and debug output remain ignored. Product cleanup never deletes user data implicitly; destructive cleanup is a separate Human Ops action.
