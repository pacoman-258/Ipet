# Neo Aspect Workflow

## Purpose

This file is the canonical workflow map for Ipet Neo Aspect. Update it whenever Body behavior, Brain decisions, Human Ops approvals, Memory & Skills persistence, settings contracts, or frontend chat behavior changes.

## Product Modules

- **Body** owns what the pet can sense, say, display, hear, and do on the local machine.
- **Brain** owns one-step LLM decision making for the current turn.
- **Human Ops** owns approval, rejection, user guidance, and action execution safety.
- **Memory & Skills** owns durable memory, summaries, preferences, skill definitions, and learned procedures.

The root entry paths remain for desktop compatibility. `main.py`, `backend/app.py`, and `index.html` are composition and loading surfaces; behavior belongs in the owning `app/`, `body/`, backend route/helper/adapter, or `frontend/` module.

## Top-Level Turn Flow

```text
start app
  |
  v
Body observes local context and receives user input
  |
  v
Body says or renders immediate UI state
  |
  v
Brain makes one decision for this turn
  |
  +--> say
  |     |
  |     v
  |   Body renders text, expression, motion, and optional TTS
  |
  +--> act
  |     |
  |     v
  |   Human Ops reviews risk and asks for approval when needed
  |     |
  |     v
  |   Body executes only the approved action
  |
  +--> remember
  |     |
  |     v
  |   Human Ops applies save/no-save intent and privacy rules
  |     |
  |     v
  |   Memory & Skills writes durable memory when allowed
  |
  +--> learn_skill
        |
        v
      Human Ops reviews the learned procedure
        |
        v
      Memory & Skills stores the skill when approved
  |
  v
Body observes the result
  |
  v
Brain summarizes terminal state
  |
  v
qa-reports records the task round in HTML when files changed
```

## User Stop Authority

An active task has a local lifecycle of `running -> stopping -> stopped`. The chat send button becomes a prominent stop button while Brain output, ReAct, Observe, approval waiting, or post-approval continuation is active. `Esc` and `Command + .` invoke the same stop path.

Stop is owned by the local task controller and never waits for Brain approval or a network model response. The UI first enters `stopping` and aborts its active stream, then asks the local backend to invalidate every pending proposal in the same task chain. Brain, Observe, approval, and action boundaries check the same task state before starting more work. Rejection still affects only one proposal; stopping invalidates the whole task chain.

After stop, no new search, observation, approval, click, typing, key press, or state-changing action may begin. Queued proposals are marked invalid, and stale coordinate, focus, and surface assumptions are discarded with the stopped chain. If a bounded system action was already in flight, the executor waits for that atomic action to return, starts no continuation, and reports that the action may already have happened. Completed side effects are never rolled back automatically.

The stopped summary distinguishes completed actions, actions not executed, and uncertain atomic results. The existing work record remains available but collapses, and the terminal copy is always `已由用户停止`, never a normal completion claim.

## Startup

1. `main.py` composes and starts the desktop shell and Qt lifecycle.
   Before the first desktop window is shown, Qt registers the application name and display name as `Ipet` and loads the project-owned avatar icon for the Dock, menu, and windows.
2. `backend/app.py` composes the local FastAPI surface and registers routers.
3. Stable root UI entries are loaded:
   - `index.html`
   - `settings.html`
   - `settings.css`
   - `settings.js`
4. Body initializes pet assets, window state, voice services, and optional observation. Desktop startup performs at most a 150 ms local Qwen TTS health probe, then starts `/Users/lyj/lyj/tool/TTS` with a separate process using its own virtual environment. It never waits for model readiness before showing Ipet. The `qwen_tts_local` provider sends clone requests using `ex.mp3` with the recorded text `就按照小昭审美就没有比较帅的`, and explicitly ignores inherited proxy settings so local synthesis never depends on SOCKS support. If that local service or model is unavailable, Body reports `TTS异常`, clears the speech queue, and does not fall back to browser speech.

For streamed Brain replies, Body splits the incoming text at sentence punctuation and submits each completed sentence to TTS immediately. TTS prepares later sentences while the browser plays the current one; playback remains ordered, and stopping the turn aborts any pending local request and discards buffered audio.
5. Memory & Skills loads local memory indexes and skill manifests.
6. Human Ops initializes pending approval state.

Startup should fail loudly for missing required local configuration and should never silently erase user data.
Entrypoint changes should only adjust composition, registration, loading, lifecycle, or compatibility delegates; place new behavior in the owning module.

## Observe And Say

Body gathers only the context needed for the turn:

- current user message
- recent local conversation context
- allowed memory snippets
- optional screen observation when enabled
- voice input state when ASR is active
- current UI state needed for presentation

Body may immediately say or render small status updates such as listening, thinking, or waiting for approval. These updates are presentation state, not hidden autonomous work.

The chat UI renders the curated execution record as one Codex-style `<details>` panel. Runtime events use exactly eight presentation categories: `planning`, `searching`, `observing`, `acting`, `waiting_approval`, `verifying`, `blocked`, and `completed`. Each row shows its source, live elapsed time, and settled status; a new stage completes the previous stage. Observe rows may show the bounded natural-language task that Brain sent to Observe, but must not expose raw chain-of-thought, hidden prompts, secrets, or unfiltered tool output. The panel stays open while work or approval is active, remains open when blocked, and automatically collapses after `completed` so the final assistant reply is visually primary.

The chat input bar shows the provider-reported token usage for the completed Brain turn: input, output, and total tokens. It does not estimate from characters. Codex supplies this data through `thread/tokenUsage/updated` in streaming mode and the CLI JSONL completion usage in non-streaming mode; providers that do not return usage leave the previous counter unchanged.

## Conversation Memory

Persistent conversations inject bounded history from `TopicStore`: conversation summaries use at most 4,000 characters, the recent tail keeps at most 10 complete user/assistant exchanges, and the combined history uses at most 12,000 characters. Temporary conversations keep only process-local history, bounded to 64 sessions and 10 exchanges per session, and never read or write `TopicStore`. The backend also forces temporary behavior when `memory.conversation_saving` is false, including the topic-creation endpoint, and returns the effective `memory_mode` in both `meta` and `done` events. Switching memory mode or starting a new conversation creates a new session ID so temporary and persistent histories cannot merge accidentally; the UI rejects those switches while a response or approval flow is active so an older stream cannot write into a newer conversation.

Long-term memory still requires a dedicated Human Ops approval chain, and automatic mini/major summary generation is not wired into the turn flow. Neither capability may write durable memory or otherwise bypass Human Ops until its review path is implemented and verified.

## Brain Decision

Brain receives a compact turn packet from Body plus allowed Memory & Skills context. Brain must return one next step:

- `say`: answer or ask a clarifying question.
- `think`: record bounded ReAct progress for an unfinished desktop operation.
- `observe`: request a screen or local context observation.
- `propose_act`: request Human Ops approval for one local action.
- `propose_remember`: request a durable memory write review.
- `propose_learn_skill`: request a new or updated skill review.
- `stop`: end the turn with a summary.

Brain does not directly write files, click UI, mutate settings, persist memory, or install anything. It describes intent; Body, Human Ops, and Memory & Skills enforce the actual boundary.

The Brain layer classifies LLM output into a `BrainDecision`. The prompt asks providers to return one JSON object such as `{"kind":"say","text":"..."}`. Plain text is still accepted and treated as `say`, so normal chat remains usable even when a provider ignores the schema. The current implementation executes terminal `say` directly, allows `think` and `observe` without approval, and turns `propose_act` decisions into Human Ops approval proposals. Playwright, app-launch, click, text-input, and key-press proposals all require approval; only native click proposals include a red-dot preview. Every native click, text-input, and key-press proposal must also name `target_app`; a proposal without a target application is not executable because Body cannot safely establish the intended macOS focus. For unfinished computer-use operations, Brain owns the choice of surface, evidence-gathering path, stage names, and next supported action. Backend intent heuristics may recognize that a request needs bounded ReAct handling, but they must not replace Brain's decision or map user keywords to a product action. Human Ops validates and approves the proposal Brain actually returned.

Brain uses a computer-use mental model instead of a GUI-only model. `stage`, `surface`, and `affordance` are optional state descriptions, not a fixed reasoning template or mandatory sequence. Surfaces can include desktop GUI, browser pages, browser chrome, terminal shells, terminal TUIs, editors, file dialogs, chat UI, or unknown surfaces. The executable action set is deliberately bounded: `playwright`, `launch_app`, `click`, `type_text`, and `key_press enter`. Brain must distinguish a confirmed local application from a website, online service, existing window, or ambiguous target; text such as "open Bilibili" is not automatically a macOS application launch. When Brain determines that the next work is a browser task and the user has not already selected an execution mode, it returns a terminal-for-the-turn `say` with `goal.status=need_user` asking the user to choose Playwright or human operation (screen observation, virtual click, text input, Enter). The backend does not infer or rewrite that choice. Once chosen, the mode remains in force for the current task and Brain does not ask again unless execution evidence shows that the selected capability is unavailable or the surface crosses into a different boundary such as browser chrome, an extension, or a system file picker. This mode choice does not prescribe the later action sequence. When Brain independently chooses `launch_app`, Human Ops shows its requested application name and waits for approval; after approval, the desktop host reads the macOS application-bundle inventory, resolves that app, and starts it through Launch Services. A native `click` proposal remains reviewable only when both `x` and `y` are present and parseable as macOS screen coordinates. Chat messages, search fields, input focus, and send controls are evidence and affordances Brain may use in any order justified by the current state; they do not impose a hard-coded chat workflow.

After the user selects Playwright, Brain also asks which Chrome personal profile to use unless the current task context already names one. It must not invent a default. The selected display name is copied unchanged into every Playwright proposal as `arguments.profile` and remains valid for the current task. Human Ops resolves that value against Chrome's `Local State` by exact display name or profile-directory name; missing or ambiguous values fail with the available display names instead of creating an unrelated empty profile. For `open`, a selected profile that is the sole active profile and exposes Chrome's remote-debugging endpoint is reused in place. Otherwise Human Ops copies only the selected profile's cookies and web storage into an Ipet-owned persistent snapshot and launches that snapshot visibly; the original Chrome profile is read-only and later snapshot changes remain isolated. If the user explicitly requires the already-open original window, Brain uses `attach`, which fails unless the selected profile is the sole active profile and Chrome has remote debugging enabled. Chrome's default user-data directory is never launched directly by Playwright.

Structured computer-use context must distinguish positive visible evidence from negative evidence. A statement such as "Dock is not visible" or "WeChat icon is not visible" must not create a Dock surface or app-icon affordance just because the user goal mentions WeChat. Body may still expose non-visual `target_candidates` such as running apps for diagnostics and future routing, but Brain cannot convert those into `click` proposals unless there is a visible coordinate-bearing affordance.

`screen_edge` remains available for explicit visual click tasks. A `launch_app` proposal itself does not use screenshots or click coordinates. App launch failures report the requested name and bounded Launch Services error instead of silently replacing Brain's chosen path with screenshot-based discovery.

Desktop operation requests use a bounded pre-approval ReAct loop in the chat stream. Brain still returns only one decision at a time, but the backend may auto-continue no-side-effect decisions: `think`, `observe`, and one correction when Brain returns `say` or `stop` for an unfinished operation goal. The loop starts from the original user text, feeds observation results and coordinate context back to Brain, and stops immediately on `propose_act` so Human Ops can review. The initial budget is three no-side-effect follow-up Brain calls after the first Brain response. If the budget is consumed before a reviewable proposal or terminal `done` / `blocked` / `need_user` status, the backend returns a blocked `say` that names the missing evidence. The runner never clicks, types, writes memory, mutates settings, or executes a reviewed action.

For multi-stage computer-use goals, a proposal may request continuation after approval with `continue_after_approval`. If Brain omits this field on a proposal whose goal still has a non-terminal stage or next step for a desktop/chat operation, the backend defaults it to true so the chain does not stop after the first approved action. Post-approval verification is layered rather than screenshot-first. Human Ops first checks the action-specific return flag (`playwright_done`, `launched`, `clicked`, `typed`, or `pressed`) and, for native desktop actions, the platform focus result. A successful Playwright return includes bounded CLI output such as the current page snapshot and stable element references, so Brain can use that evidence without switching macOS focus or capturing the desktop. When structured checks pass, their bounded evidence is sent directly to Brain and no screenshot is captured. Brain returns `observe` only if the next decision genuinely depends on a newly visible non-Playwright surface or affordance. Missing native evidence falls back to visual observation, while contradictory action results fail closed. A chat-send Enter/Return carrying `expected_text` is deliberately considered structurally insufficient because posting a key cannot prove that the expected message appeared; it therefore upgrades to visual verification before the goal may become `done`. This avoids repeated screenshots after focus clicks and text entry while preserving a visible result check for semantically meaningful sends. The bounded post-approval ReAct loop still stops immediately on another `propose_act`, and each new side effect still needs Human Ops approval.

For visual work, Brain asks Observe in natural language. An `observe` decision carries a plain `question` / `observe_prompt`, such as "tell me what is visible on the screen" or "if the target is visible, tell me the clickable center x and y", plus `target_app` when the requested evidence belongs to an application. Before an application screenshot, Body asks macOS to activate that application and verifies that it is frontmost; a focus mismatch fails closed instead of capturing the wrong Space. Application captures, including full-screen applications, leave the Ipet window state unchanged. Only an explicit desktop capture temporarily hides Ipet, then restores it without raising it above the observed surface. Body appends the actual screen resolution and coordinate contract before analysis.

When `human_ops.observe_model.enabled` is false, Body performs capture only and attaches the bounded screenshot directly to the immediately following Brain call. Brain itself reads the image and returns the next `BrainDecision`, so the original goal, previous decision, coordinate context, and visual evidence stay on one provider/model path without a separate VLM answer being converted to prose and parsed again. The screenshot is attached once, then dropped from the continuation state; it is never written to conversation history, durable memory, or later text-only follow-ups. OpenAI-compatible chat completions use `image_url`, Ollama uses `images`, Anthropic-compatible messages use a base64 image source, Google AI Studio uses `inline_data`, and Codex uses a temporary local image deleted after the call. If the selected Brain model does not accept image input, the Brain call fails closed with the provider error; the user can then select a vision-capable Brain model or explicitly enable the independent observe model.

When the independent observe model is explicitly enabled, the existing compatibility path remains: it receives the Brain question plus the screenshot and answers in natural language, not JSON, then Brain reads that bounded answer for the next decision. For clicks, Human Ops accepts a proposal only when the target application and complete coordinates satisfy the safety contract. Brain decides whether the current evidence justifies that proposal, whether another observation would add missing evidence, or whether ambiguity requires `need_user` / `blocked`; entering Observe does not force a predetermined follow-up action.

The backend also derives structured computer-use context from each observation. The returned observation may include a `surface` object, an `affordances` list, and for chat surfaces a `chat_context` object alongside the natural-language text. Current lightweight extraction recognizes entries such as Dock app icons, WeChat search fields, chat threads, chat input boxes, send buttons, browser address bars, and terminal prompts. When one observation names multiple coordinates, the extractor binds coordinates to the nearest matching phrase, so a contact row coordinate does not get reused for the chat input box. For WeChat reply tasks, `chat_context` can carry the target contact, recent visible messages, whether the input is ready, whether the input is focused, and whether a send/Enter affordance is visible. When `chat_context.recent_messages` already contains enough visible conversation, Brain should draft or send from that evidence instead of repeatedly observing the same messages. This structured context is included in Brain follow-up prompts and approval continuations under `Structured computer-use context`, giving Brain a stable map of the current surface, available human actions, and visible conversation instead of forcing it to re-parse prose every time.

On macOS, Body may enrich active observations with Dock item candidates from the Dock accessibility tree. These candidates are still only observations: they provide the app name, bounds, and focus point for visible Dock items, letting the backend attach a numeric location to a Dock `app_icon` affordance when the VLM can identify the icon but omits x/y. Brain still proposes a normal `click` and Human Ops still requires approval before execution.

Observe coordinate contracts are explicit because screenshots can be scaled before they reach the VLM. Body records the encoded screenshot pixel size as `image_width` / `image_height` and the display layout in macOS screen points. Before a VLM call, the backend derives `image_resolution`, `screen_bounds`, `screen_resolution`, and `coordinate_scale` under `active_observation`. On the independent observe-model compatibility path, the VLM still returns macOS screen coordinates. When Brain directly receives the image, its click proposal instead labels the target center as `coordinate_space=image_pixels`; the backend validates those coordinates against the captured image and deterministically converts them to `macos_screen_points` before Human Ops creates the approval proposal. Explicit screen-point coordinates are bounds-checked, while unknown coordinate spaces and out-of-bounds points lose their executable x/y and return to the bounded correction path instead of showing or executing a misleading preview.

Human Ops gives the active vision capture command enough time to collect macOS metadata, Dock item positions, and the screenshot before either direct Brain analysis or the separate VLM compatibility call begins. A capture timeout should report a desktop capture problem, not be confused with Brain indecision.

When Brain asks to observe `screen` but its goal says the current stage is missing a click target's center coordinates, the backend rewrites the observe prompt into a narrow coordinate follow-up for that target, such as "only locate the WeChat icon center and return x/y." If that narrow coordinate follow-up still returns a visible target without complete x/y, Human Ops marks `observe_coordinate_incomplete` so the loop stops with a concrete coordinate-missing reason instead of spending the remaining budget on generic observations.

For follow-up observes, `screen` is treated as an interaction surface placeholder, not as the task target. If Brain's goal carries an objective such as opening WeChat, Body preserves that objective as `active_observation.target_hint` so fallback affordances and app-label extraction still know which app or contact matters. If an observation is marked coordinate-incomplete but already includes a reviewable click affordance with numeric x/y, the ReAct loop must continue to Brain instead of blocking on the missing coordinates for a different target.

The active Brain provider is configured in the web settings page. The current API formats are:

- OpenAI-compatible chat completions
- Ollama chat
- Anthropic-compatible messages
- Google AI Studio Gemini `generateContent`
- local Codex CLI using the current machine's Codex login

The provider endpoint, model name, temperature, and optional API key live under the local Brain settings. Google AI Studio uses the official Gemini API endpoint by default, so the settings page does not require a custom endpoint for that provider. The API key is stored locally and redacted from settings responses. Codex also requires no endpoint or API key: model discovery runs `codex login status` with the user's resolved `CODEX_HOME`, trusts its exit status rather than localized stdout/stderr text, then queries a short-lived Codex app-server `model/list` connection. The response supplies concrete account model IDs, the actual default model, and model-specific supported/default reasoning efforts. If the field still contains `default`, the settings page replaces it with the returned default model ID; clicking another model also refreshes its reasoning-effort choices.

Brain settings include optional streaming-output and model-built-in-web-search switches. Codex streaming starts a short-lived app-server thread and forwards `item/agentMessage/delta` text through the existing chat SSE connection; only the visible `text` of structured `say` decisions is exposed, so JSON decision envelopes are not rendered to the user. When built-in search is enabled and supported, native Codex `webSearch` items are forwarded into the same worklog as live “searching”, “opening page”, and “finding in page” phases. Non-streaming Codex uses the same app-server path without forwarding deltas or live search phases. If app-server fails before any visible delta, Brain retries through the compatibility CLI path. Other providers currently use their existing non-streaming paths.

Codex app-server threads replace the default Codex agent instructions with a narrow multimodal decision-backend instruction and disable Codex shell, multi-agent, apps, plugins, browser, computer-use, image generation, goals, hooks, and workspace-dependency tools. A Body-captured screenshot is request input rather than a Codex tool. Built-in web search is enabled alone when requested and when `modelProvider/capabilities/read` reports support; otherwise the turn continues without search and the final `say` explicitly states that no web search occurred. Ipet remains responsible for ReAct, observation, Human Ops review, action execution, memory, and skills. A selected Codex reasoning effort is normalized and passed unchanged; an empty value follows that model's advertised default, and an explicit model ID is also passed unchanged.

Codex subprocesses run from the system temporary directory with read-only sandboxing and no approval escalation. App-server threads are ephemeral. The compatibility `codex exec` fallback ignores user config and project rules and uses JSONL output. Ipet does not read or copy Codex credentials. Brain uses the narrow multimodal decision bridge; Human Ops may still independently select Codex for screenshot analysis when the separate observe model is explicitly enabled.

The settings page can ask the backend to discover available models from the currently entered provider and endpoint. This uses `/api/brain/models`, supports draft values that have not been saved yet, and never echoes API keys back to the browser.

The optional independent Human Ops observe path supports OpenAI-compatible, Ollama, Google AI Studio, and Codex visual providers. Google AI Studio uses Gemini `generateContent` with inline image data and the saved local API key. Codex needs no endpoint or key: model discovery keeps only models whose catalog advertises image input, and analysis writes the bounded screenshot to a temporary PNG/JPEG, attaches it with `codex exec --image`, runs ephemerally with read-only sandboxing and no approval escalation, then deletes the temporary image. The desktop host keeps the Brain and observe provider/key fields in `pet_config.json` when it reloads or saves local state, so reopening the app preserves the previous API configuration.

Brain and observe HTTP clients do not trust ambient proxy variables automatically. Local providers and local endpoints such as Ollama, loopback, and `.local` hosts connect directly. Remote OpenAI-compatible, Anthropic-compatible, and Google AI Studio calls may use the first HTTP(S) proxy from `HTTPS_PROXY` / `HTTP_PROXY`; SOCKS-only `ALL_PROXY` is ignored so missing SOCKS extras do not break local model paths. Human Ops observe model analysis defaults to a 90 second read timeout and accepts custom values up to 300 seconds because image reasoning can take materially longer than text-only chat.

## Human Ops Approval

Human Ops reviews any operation that may affect files, processes, configuration, network access, external services, user data, or durable memory.

Approval prompts must explain:

- why the action is needed
- what it may change or affect
- whether it touches files, processes, configuration, network access, or destructive operations
- what will happen if the user rejects it

User choices:

- approve
- reject
- reject with guidance
- approve with constraints

No dangerous action should execute from Brain output alone. Human Ops is the gate.

On macOS, the active approval is presented as a native system dialog with Approve and Reject choices; the chat worklog contains status and evidence but no inline approval buttons. Approval leaves the current target application in front so execution can continue without an Ipet focus hop. Rejection invalidates the proposal, brings the Ipet window and chat input back to the foreground, and performs no proposed action. A missing response or dialog timeout is treated as rejection.

## Execute

After approval, Body executes the bounded action and records the result. Execution should be narrow:

- use the minimum command or API call needed
- avoid broad filesystem traversal
- keep generated local app state out of Git
- preserve unrelated user or coworker changes
- surface failures as facts for the next Brain step

For approved Human Ops clicks on macOS, Body first activates the reviewed `target_app` through Launch Services and verifies the frontmost application through System Events. Only after that verification does it treat the reviewed x/y as absolute screen coordinates and post a bounded CoreGraphics mouse event. If CoreGraphics event posting is unavailable, Body falls back to System Events. Ipet is not hidden, restored, or raised as part of an application click. The red-dot preview payload includes the click coordinate and marker size so the user can inspect the intended target before approval. In the desktop app, the Qt overlay is the sole preview owner and uses full-screen coordinates; the in-page DOM marker is only a no-bridge fallback, preventing duplicate markers and WebView-origin offsets.

The full-screen click preview overlay uses a high-level Qt tooltip-style transparent window and draws the marker directly, rather than relying on a styled label. This keeps the red dot visible above macOS UI surfaces such as the Dock.

For approved Human Ops text and key actions on macOS, Body establishes and verifies the reviewed `target_app` focus before sending narrow System Events commands through the same desktop command bridge. `type_text` uses `keystroke` with the reviewed text and label. `key_press` currently supports Enter/Return via key code 36. These actions are deliberately small so Brain can build complex workflows from observe, click, type, and Enter primitives while Human Ops still confirms each side-effecting step. A failed focus verification stops the action; the executor never types or presses a key into whichever application happened to be frontmost.

For approved `launch_app` actions, the desktop host queries macOS Spotlight metadata for application bundles, matches the reviewed application name to an exact or unique bundle, and calls `/usr/bin/open` on that bundle. If the inventory is unavailable or has no unique match, Launch Services receives the reviewed name through `open -a`. This path does not capture the screen, synthesize a click, or require Accessibility event-posting permission.

For approved `playwright` actions, Human Ops requires the user-selected `profile` and accepts only a fixed CLI command set: attach, open, snapshot, click, fill, type, press, navigation, and tab management. URLs must be absolute HTTP(S) addresses; click and fill refs must match the `e<number>` identifier returned by the latest snapshot in the same profile session. The executor resolves profile names from Chrome `Local State`; it never treats a display label as a new blank directory. A first snapshot copies the selected profile's `Preferences`, cookies, Network state, Local Storage, IndexedDB, Session Storage, WebStorage, Storage, and SharedStorage into an ignored Ipet directory, excluding saved passwords, history, extensions, and caches. Snapshot creation happens only after approval, is atomic, and never writes the source profile. `open` reuses the sole active Chrome profile through CDP when that endpoint is ready; otherwise it appends fixed `--headed` and `--profile=<Ipet snapshot directory>` arguments. `attach` is fixed to `--cdp=chrome`, accepts only the sole active selected profile, and explains how to enable `chrome://inspect/#remote-debugging` when unavailable. The executor hashes the user-confirmed profile label into a stable `ipet-profile-v4-*` CLI session, builds argument vectors, and never invokes a shell. Arbitrary evaluation, `run-code`, filesystem URLs, selectors invented by Brain, raw CLI fragments, model-supplied profile paths/CDP/headed flags, direct launch of Chrome's default user-data directory, missing profile names, and unknown or ambiguous profiles are rejected before execution. Human Ops uses an installed `playwright-cli` when available and otherwise uses `npx --package @playwright/cli`; the native approval notice discloses profile-state copying, reuse behavior, possible CLI fetch, and network access. A non-zero exit, timeout, or invalid ref fails explicitly. A zero exit confirms the CLI action and returns its page output, but does not by itself prove the user visually saw the page. Brain may then choose a different reviewed path, but the executor does not silently synthesize native clicks or screenshots on its own.

If execution fails, Brain may decide one follow-up step, but repeated retries should go back through Human Ops when they change the risk profile.

## Memory & Skills

Memory writes are explicit product actions. They should include:

- source turn
- user-visible reason
- scope and retention expectation
- privacy or no-save constraints

Skill learning is also explicit. A learned skill should include:

- trigger condition
- procedure
- required approvals
- verification method
- owner or review role

Memory & Skills should not store raw screenshots, generated audio, local secrets, or unreviewed destructive procedures.

## Observe Result

After Body executes or renders the result, it observes enough state to confirm what happened:

- command exit status or API response
- visible UI result when relevant
- file path or record id for durable writes
- approval outcome
- bounded error text when failed

This observation becomes the next Brain input and the human-readable summary.

## Summary And Report

Each non-trivial turn ends with a concise summary:

- what changed
- what was verified
- what remains uncertain
- what should happen next

Every file-changing task round must also produce an HTML report under `docs/reports/`. Reports are owned by `qa-reports` and should be readable without opening the diff first.

## Local State And Cleanup

Local-only state may include:

- `pet_config.json`
- `.pet_desktop_command*.json`
- `.pet_desktop_host.heartbeat.json`
- `backend/audio_cache/`
- `data/`
- `.app-logs/`
- `.service-logs/`
- `.uv-cache/`
- `.venv/`
- generated debug output under ignored locations

Deletion is destructive. Coding agents must explain the need, affected paths, and risk before asking for approval.
