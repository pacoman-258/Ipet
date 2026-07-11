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

## Startup

1. `main.py` composes and starts the desktop shell and Qt lifecycle.
2. `backend/app.py` composes the local FastAPI surface and registers routers.
3. Stable root UI entries are loaded:
   - `index.html`
   - `settings.html`
   - `settings.css`
   - `settings.js`
4. Body initializes pet assets, window state, voice services, and optional observation.
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

The Brain layer classifies LLM output into a `BrainDecision`. The prompt asks providers to return one JSON object such as `{"kind":"say","text":"..."}`. Plain text is still accepted and treated as `say`, so normal chat remains usable even when a provider ignores the schema. The current implementation executes terminal `say` directly, allows `think` and `observe` without approval, and turns `propose_act` decisions into Human Ops approval proposals. Click proposals include a red-dot preview; text input and key-press proposals include a human-readable tool summary. For Human Ops desktop actions, Brain is responsible for advancing the user's target to an approvable one-step proposal: if the user asks to click, open, operate a visible target, type into a focused input, send with Enter, or reply in a chat surface such as WeChat, `say` is not a completion path while `goal.status` is still `in_progress`; Brain must identify the current task stage, interaction surface, and usable affordance, then return the next minimal `propose_act`, which Body executes only after user approval.

Brain uses a computer-use mental model instead of a GUI-only model. It should name the current `stage`, `surface`, and `affordance` in its goal state. Surfaces can include desktop GUI, browser pages, browser chrome, terminal shells, terminal TUIs, editors, file dialogs, WeChat UI, or unknown surfaces. Affordances are human-operable entries such as Dock app icons, buttons, links, browser address bars, chat input boxes, terminal prompts, and TUI menu items. The current executable action set is deliberately small: `click`, `type_text`, and `key_press enter`. Brain must not return direct shortcuts such as `launch_app`, `focus_window`, `hotkey`, `scroll`, `drag`, or `wait`; if it does, the backend treats that as an unsupported action and asks Brain to choose an observable/simple human action instead. A `click` proposal is only reviewable when both `x` and `y` are present and parseable as macOS screen coordinates; if either coordinate is missing or nonnumeric, the backend asks Brain to observe or return a complete coordinate-bearing click instead of showing a red-dot approval at a fallback coordinate. For example, if the user asks to open an app and a Dock app icon is visible with a trustworthy center point, Brain should propose a click on that icon; it should not keep observing merely to prove that the app will open. For chat reply tasks, Brain should progress through stages such as `launch_app`, `locate_contact`, `read_context`, `draft_reply`, `focus_input`, `type_reply`, `send_reply`, and `verify_result`. In `locate_contact`, if the target contact row is not visible but a WeChat search field is visible, Brain should use the normal human path of clicking the search field, typing the contact name, and pressing Enter or clicking the search result instead of repeatedly observing the same list.

Structured computer-use context must distinguish positive visible evidence from negative evidence. A statement such as "Dock is not visible" or "WeChat icon is not visible" must not create a Dock surface or app-icon affordance just because the user goal mentions WeChat. Body may still expose non-visual `target_candidates` such as running apps for diagnostics and future routing, but Brain cannot convert those into `click` proposals unless there is a visible coordinate-bearing affordance.

If an app-opening task observes negative Dock visibility but the screen bounds are known, Body may expose a `screen_edge` affordance with `purpose=reveal_hidden_dock` at the bottom screen edge. Brain may use it once in `launch_app` / `reveal_dock` as a normal `click` proposal with `continue_after_approval=true`, then must observe the changed surface and look for the real Dock app icon before continuing. This keeps the action vocabulary human-simple while still matching the common macOS hidden-Dock path.

Desktop operation requests use a bounded pre-approval ReAct loop in the chat stream. Brain still returns only one decision at a time, but the backend may auto-continue no-side-effect decisions: `think`, `observe`, and one correction when Brain returns `say` or `stop` for an unfinished operation goal. The loop starts from the original user text, feeds observation results and coordinate context back to Brain, and stops immediately on `propose_act` so Human Ops can review. The initial budget is three no-side-effect follow-up Brain calls after the first Brain response. If the budget is consumed before a reviewable proposal or terminal `done` / `blocked` / `need_user` status, the backend returns a blocked `say` that names the missing evidence. The runner never clicks, types, writes memory, mutates settings, or executes a reviewed action.

For multi-stage computer-use goals, a proposal may request continuation after approval with `continue_after_approval`. If Brain omits this field on a proposal whose goal still has a non-terminal stage or next step for a desktop/chat operation, the backend defaults it to true so the chain does not stop after the first approved action. Chat send actions are a special verification case: a `key_press` Enter/Return proposal with `expected_text` or a send label defaults to continuation even if Brain already wrote `stage=verify_result`, because the Enter action still must be executed and observed before the task is done. After Body executes that approved action, the backend observes the new surface and asks Brain for the next one-step decision using the original user goal, execution result, and post-action observation. The post-approval continuation is also bounded ReAct: it may auto-continue no-side-effect `think` and `observe` decisions, then stops immediately when Brain returns another `propose_act` so Human Ops can emit the next approval request. This lets a task such as opening WeChat, finding a contact, reading context, typing a reply, and pressing Enter advance one confirmed action at a time, including the common human pattern of click, observe the changed surface, then click or type the next entry. For chat reply tasks, the post-approval observation prompt must carry the original task context and ask for the target contact or conversation, recent visible chat content, chat input box, whether that input is focused or has a cursor, and send/Enter affordance so Brain has enough evidence to draft and send the reply. A visible chat input box is not automatically the keyboard target: if Brain has no focus, cursor, direct-input, or just-approved focus-click evidence, it should click the input box in `focus_input` before proposing `type_text`. After an approved `type_text`, the observation prompt also names the entered draft and asks whether the draft appears completely in the input box and whether pressing Enter is the appropriate next step. In `send_reply`, Brain should include `expected_text` on the Enter proposal; if it omits that field immediately after an approved `type_text`, the backend inherits the just-typed text into the Enter proposal before Human Ops asks for approval. After approved Enter, the observation prompt asks whether that text now appears in the chat history and whether the input returned to an editable state, allowing Brain to finish with `goal.status=done` only after visible verification.

For visual work, Brain asks Observe in natural language. An `observe` decision carries a plain `question` / `observe_prompt`, such as "tell me what is visible on the screen" or "if the target is visible, tell me the clickable center x and y". Body still owns the screenshot and appends the actual screen resolution before calling the observe model. The OpenAI-compatible observe model path receives the Brain question plus the screenshot and answers in natural language, not JSON. That answer is then sent back to Brain for the next decision. If the user wanted a click and the natural-language observation contains trustworthy coordinates, Brain returns `propose_act`, and Human Ops shows the red-dot approval request. If the observation confirms the target is visible but does not provide numeric coordinates, Brain must ask Observe again for the clickable center instead of merely describing the target to the user.

The backend also derives structured computer-use context from each observation. The returned observation may include a `surface` object, an `affordances` list, and for chat surfaces a `chat_context` object alongside the natural-language text. Current lightweight extraction recognizes entries such as Dock app icons, WeChat search fields, chat threads, chat input boxes, send buttons, browser address bars, and terminal prompts. When one observation names multiple coordinates, the extractor binds coordinates to the nearest matching phrase, so a contact row coordinate does not get reused for the chat input box. For WeChat reply tasks, `chat_context` can carry the target contact, recent visible messages, whether the input is ready, whether the input is focused, and whether a send/Enter affordance is visible. When `chat_context.recent_messages` already contains enough visible conversation, Brain should draft or send from that evidence instead of repeatedly observing the same messages. This structured context is included in Brain follow-up prompts and approval continuations under `Structured computer-use context`, giving Brain a stable map of the current surface, available human actions, and visible conversation instead of forcing it to re-parse prose every time.

On macOS, Body may enrich active observations with Dock item candidates from the Dock accessibility tree. These candidates are still only observations: they provide the app name, bounds, and focus point for visible Dock items, letting the backend attach a numeric location to a Dock `app_icon` affordance when the VLM can identify the icon but omits x/y. Brain still proposes a normal `click` and Human Ops still requires approval before execution.

Observe coordinate contracts are explicit because screenshots can be scaled before they reach the VLM. Body records the encoded screenshot pixel size as `image_width` / `image_height` and the display layout in macOS screen points. Before a VLM call, the backend derives `image_resolution`, `screen_bounds`, `screen_resolution`, and `coordinate_scale` under `active_observation`. The VLM prompt must state the attached image pixel size, the macOS screen point coordinate space, and the image-to-screen scale, and must ask for click targets as macOS screen coordinates rather than screenshot pixel coordinates. For click targets, the VLM prompt also includes the screen-to-image inverse scale and requires the model to project its proposed macOS coordinate back onto the screenshot, checking that the point lands on the target icon or control rather than an adjacent Dock icon, label, window edge, or background. The follow-up Brain decision also receives this coordinate context so it can convert any observed screenshot/image-pixel coordinates before returning `propose_act`.

Human Ops gives the active vision capture command enough time to collect macOS metadata, Dock item positions, and the screenshot before the separate VLM call begins. A capture timeout should report a desktop capture problem, not be confused with Brain indecision.

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

Codex app-server threads replace the default Codex agent instructions with a narrow text-backend instruction and disable Codex shell, multi-agent, apps, plugins, browser, computer-use, image, goals, hooks, and workspace-dependency tools. Built-in web search is enabled alone when requested and when `modelProvider/capabilities/read` reports support; otherwise the turn continues without search and the final `say` explicitly states that no web search occurred. Ipet remains responsible for ReAct, observation, Human Ops review, action execution, memory, and skills. A selected Codex reasoning effort is normalized and passed unchanged; an empty value follows that model's advertised default, and an explicit model ID is also passed unchanged.

Codex subprocesses run from the system temporary directory with read-only sandboxing and no approval escalation. App-server threads are ephemeral. The compatibility `codex exec` fallback ignores user config and project rules and uses JSONL output. Ipet does not read or copy Codex credentials. Brain uses the narrow text-decision bridge; Human Ops may independently select Codex for screenshot analysis.

The settings page can ask the backend to discover available models from the currently entered provider and endpoint. This uses `/api/brain/models`, supports draft values that have not been saved yet, and never echoes API keys back to the browser.

Human Ops observe supports OpenAI-compatible, Ollama, Google AI Studio, and Codex visual providers. Google AI Studio uses Gemini `generateContent` with inline image data and the saved local API key. Codex needs no endpoint or key: model discovery keeps only models whose catalog advertises image input, and analysis writes the bounded screenshot to a temporary PNG/JPEG, attaches it with `codex exec --image`, runs ephemerally with read-only sandboxing and no approval escalation, then deletes the temporary image. The desktop host keeps the Brain and observe provider/key fields in `pet_config.json` when it reloads or saves local state, so reopening the app preserves the previous API configuration.

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

## Execute

After approval, Body executes the bounded action and records the result. Execution should be narrow:

- use the minimum command or API call needed
- avoid broad filesystem traversal
- keep generated local app state out of Git
- preserve unrelated user or coworker changes
- surface failures as facts for the next Brain step

For approved Human Ops clicks on macOS, Body treats the reviewed x/y as absolute screen coordinates, briefly hides the Ipet host window so the desktop target is not blocked by Qt, then posts a bounded CoreGraphics mouse event at that coordinate. If CoreGraphics event posting is unavailable, Body falls back to System Events. The red-dot preview payload includes the click coordinate and marker size so the user can inspect the intended target before approval; the in-page preview is shown only when the target is actually inside the WebView, while the Qt preview overlay owns full-screen coordinates.

The full-screen click preview overlay uses a high-level Qt tooltip-style transparent window and draws the marker directly, rather than relying on a styled label. This keeps the red dot visible above macOS UI surfaces such as the Dock.

For approved Human Ops text and key actions on macOS, Body sends narrow System Events commands through the same desktop command bridge. `type_text` uses `keystroke` with the reviewed text and label. `key_press` currently supports Enter/Return via key code 36. These actions are deliberately small so Brain can build complex workflows from observe, click, type, and Enter primitives while Human Ops still confirms each side-effecting step.

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
