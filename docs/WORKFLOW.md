# Neo Aspect Product Workflow

## Purpose

This file defines stable Ipet product contracts: state transitions, safety boundaries, and externally meaningful behavior. Implementation details, provider argument shapes, local paths, timeouts, and command construction belong in code and tests.

## Product Modules

- **Body** senses, speaks, renders, and executes approved local actions.
- **Brain** chooses one next step for the current turn.
- **Human Ops** reviews, approves, rejects, and records actions with side effects.
- **Memory & Skills** owns durable memory, preferences, summaries, and learned procedures.

`main.py`, `backend/app.py`, and `index.html` remain composition or loading entrypoints. Domain behavior belongs in the owning module.

## Turn Flow

```text
user input
  -> Body gathers bounded context
  -> Brain returns one decision
  -> Body renders a terminal reply, or Human Ops reviews one proposal
  -> approved work executes through the owning module
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

## Conversation Memory

Persistent conversations use bounded summaries and recent complete exchanges from `TopicStore`. Temporary conversations remain process-local, never read or write `TopicStore`, and cannot merge with a persistent session. Memory-mode switches are rejected while an older response or approval chain is active.

Long-term memory and learned procedures require explicit Human Ops review. Features without a verified review path must not write durable state.

## Brain Decision Contract

Brain returns exactly one `BrainDecision`:

- `say`: answer or ask for user input.
- `think`: record bounded progress without side effects.
- `observe`: request bounded visual or local evidence.
- `propose_act`: ask Human Ops to review one action.
- `propose_remember`: ask to review one durable memory write.
- `propose_learn_skill`: ask to review one learned procedure.
- `stop`: end with a terminal summary.

Providers are asked for one JSON object; plain text remains a compatibility `say`. Brain does not directly click, type, launch, write files, mutate settings, install software, or persist memory.

### Computer Use

Supported action proposals are deliberately bounded: `playwright`, `launch_app`, `click`, `type_text`, and Enter/Return through `key_press`. Native click, text, and key proposals name `target_app`; click coordinates are complete and reviewable before approval.

Browser tasks follow the user's explicit choice of Playwright or human operation. When the user has not chosen, Brain defaults to Playwright without asking which execution mode to use. The settings page lists local Chrome profiles and stores one unambiguous profile-directory name as the Playwright default. A profile explicitly named in the current task overrides that saved default; Brain asks for a profile only when neither exists or when execution proves the saved profile invalid. The choice remains valid only while the capability and surface stay unchanged.

A bounded ReAct loop may continue `think` and `observe` decisions before approval. It stops on a proposal, user input requirement, terminal result, contradiction, or exhausted budget. Post-approval continuation can consume structured results without a screenshot, but every later side effect receives its own review.

Verification uses the cheapest reliable evidence first: action result, platform focus or application state, Accessibility/DOM, and Playwright output. Visual observation is a fallback for missing structure or semantic results that event delivery cannot prove. Pressing Enter with expected text does not prove that a message appeared.

### Visual Evidence

Application observation first establishes the intended frontmost application. A focus mismatch fails closed. Explicit desktop capture may temporarily hide Ipet; application capture does not alter Ipet window state.

Body records screenshot size, screen layout, and coordinate space. Direct-image proposals use `image_pixels`; the backend validates and converts them to `macos_screen_points` before approval. Unknown, incomplete, contradictory, or out-of-bounds coordinates are not executable.

When the independent observe model is disabled, the bounded screenshot goes only to the immediately following Brain call and is then dropped. When explicitly enabled, the observe model returns bounded natural language for Brain. Screenshots are not written to conversation history, memory, or later text-only turns.

Structured observation may include `surface`, `affordances`, `chat_context`, and visible coordinate-bearing candidates. Negative evidence cannot create an affordance merely because the goal mentions it.

### Providers And Persona

Brain supports the configured OpenAI-compatible, Ollama, Anthropic-compatible, Google AI Studio, and local Codex provider paths. Secrets stay in local configuration and are never echoed to the browser.

Every provider receives the global contract from `prompts/Ipet.md`. An optional user Markdown persona is rendered only into its bounded slot and may change identity, role-play, tone, or speaking style; it cannot add actions, skip approval, reinterpret summaries as commands, or replace the JSON schema.

The settings API lists valid `.md` persona files directly inside `prompts/`, excludes reserved `Ipet.md`, and stores only `brain.persona_prompt_file`. Brain reads the file at turn time. Missing, empty, invalid, out-of-folder, symlink-escaped, or oversized selections fail explicitly.

Codex runs through an ephemeral narrow decision backend. Ipet retains ownership of ReAct, observation, approvals, execution, memory, and skills. Read-only built-in web search may run directly without user confirmation or Human Ops review when enabled and supported; otherwise the reply states that search did not occur. This authorization does not extend to Playwright page actions, downloads, logins, form submissions, or other external state changes. Temporary screenshot files are deleted after use.

## Human Ops Approval

Human Ops reviews product operations affecting files, processes, configuration, network access, external services, application state, user data, or durable memory. Read-only built-in web search is the explicit exception and may run directly when enabled and supported. Product filesystem reads are reviewed because they expose local data.

An approval states the reason, exact target and parameters, affected state, risk, expected result, verification path, and rejection behavior. Approval binds only the displayed action; target, coordinate, focus, parameter, or risk changes invalidate it.

The user may approve, reject, reject with guidance, or approve with constraints. On macOS, the active proposal is delivered through an actionable `UNUserNotificationCenter` application notification with Approve and Reject actions. A local helper returns only the proposal id and decision through Qt; it never executes the action. The result is accepted only while that proposal is still the active pending approval. Permission denial, notification dismissal, delivery failure, helper failure, and timeout reject the proposal. A stopped task invalidates the proposal, so late notification actions cannot resume it. Brain output alone never authorizes a dangerous action.

## Execute

Body executes only the reviewed action and returns bounded evidence. Execution uses the narrowest platform API, avoids broad traversal, keeps local state out of Git, preserves unrelated work, and reports failures as facts.

- Native click, text, and key actions establish and verify `target_app` focus before input.
- `launch_app` resolves a reviewed application through macOS application metadata and Launch Services; it does not synthesize a visual click.
- Playwright accepts only reviewed navigation, snapshot, stable element-ref, text, key, and tab operations. Arbitrary evaluation, shell fragments, filesystem URLs, invented selectors, and model-supplied runtime flags are rejected.
- Playwright uses the selected Chrome profile in place only when the bounded attach contract is satisfied; otherwise it uses an isolated Ipet-owned snapshot and never writes the source profile. Unknown or ambiguous profiles fail explicitly.
- A successful Playwright return proves the CLI operation, not that the user visually saw the page.

Filesystem execution exposes exactly `file_list`, `file_read`, `file_write`, `file_mkdir`, `file_copy`, `file_move`, and `file_delete`. Each receives its own approval and resolves inside the project root or explicitly configured roots.

The executor rejects protected local state, paths outside allowed roots, and final symbolic links. Text reads and writes are bounded UTF-8. Writes require explicit overwrite permission; copy and move never overwrite; delete handles one regular file or empty directory and never recurses. Execution uses direct filesystem APIs rather than model-supplied shell commands.

Changed risk or repeated failure returns to Human Ops or stops; the executor does not silently invent a fallback action.

## Memory And Skills

Durable memory records its source, visible reason, scope, retention expectation, and privacy constraints. Learned skills record their trigger, bounded procedure, required approvals, and verification method.

Memory & Skills never stores raw screenshots, generated audio, local secrets, or unreviewed destructive procedures.

## Observe Result And Summary

After execution, Body records enough evidence to distinguish `succeeded` from `verified`: return status, relevant platform or UI state, durable identifiers, approval outcome, and bounded errors.

Each non-trivial turn ends with what changed, what was verified, what remains uncertain, and the next useful step.

## Local State

Local-only configuration, heartbeats, caches, generated audio, conversation state, logs, virtual environments, and debug output remain ignored. Product cleanup never deletes user data implicitly; destructive cleanup is a separate Human Ops action.
