# Neo Aspect Workflow

## Purpose

This file is the canonical workflow map for Ipet Neo Aspect. Update it whenever Body behavior, Brain decisions, Human Ops approvals, Memory & Skills persistence, settings contracts, or frontend chat behavior changes.

## Product Modules

- **Body** owns what the pet can sense, say, display, hear, and do on the local machine.
- **Brain** owns one-step LLM decision making for the current turn.
- **Human Ops** owns approval, rejection, user guidance, and action execution safety.
- **Memory & Skills** owns durable memory, summaries, preferences, skill definitions, and learned procedures.

The current codebase is in transition. Some module code still lives in `backend/`, `main.py`, and root UI files, but new documentation and ownership should use the Neo module names.

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

1. `main.py` starts the desktop shell.
2. The Python API surface starts for local routes and settings.
3. Root UI entries are loaded during the transition:
   - `index.html`
   - `settings.html`
   - `settings.css`
   - `settings.js`
4. Body initializes pet assets, window state, voice services, and optional observation.
5. Memory & Skills loads local memory indexes and skill manifests.
6. Human Ops initializes pending approval state.

Startup should fail loudly for missing required local configuration and should never silently erase user data.

## Observe And Say

Body gathers only the context needed for the turn:

- current user message
- recent local conversation context
- allowed memory snippets
- optional screen observation when enabled
- voice input state when ASR is active
- current UI state needed for presentation

Body may immediately say or render small status updates such as listening, thinking, or waiting for approval. These updates are presentation state, not hidden autonomous work.

## Brain Decision

Brain receives a compact turn packet from Body plus allowed Memory & Skills context. Brain must return one next step:

- `say`: answer or ask a clarifying question.
- `observe`: request a screen or local context observation.
- `act`: request a local action.
- `remember`: request a durable memory write.
- `learn_skill`: request a new or updated skill.
- `stop`: end the turn with a summary.

Brain does not directly write files, click UI, mutate settings, persist memory, or install anything. It describes intent; Body, Human Ops, and Memory & Skills enforce the actual boundary.

The active runtime classifies LLM output into a `BrainDecision`. The prompt asks providers to return one JSON object such as `{"kind":"say","text":"..."}`. Plain text is still accepted and treated as `say`, so normal chat remains usable even when a provider ignores the schema. In the current implementation only `say` is executed; `observe`, `act`, `remember`, `learn_skill`, and `stop` are parsed as reserved decision kinds for later review and execution flows.

The active Brain provider is configured in the web settings page. The current API formats are:

- OpenAI-compatible chat completions
- Ollama chat
- Anthropic-compatible messages

The provider endpoint, model name, temperature, and optional API key live under the local Brain settings. The API key is stored locally and redacted from settings responses.

The settings page can ask the backend to discover available models from the currently entered provider and endpoint. This uses `/api/brain/models`, supports draft values that have not been saved yet, and never echoes API keys back to the browser.

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
- keep generated runtime state out of Git
- preserve unrelated user or coworker changes
- surface failures as facts for the next Brain step

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
- `.pet_runtime_*`
- `backend/audio_cache/`
- `data/`
- `.runtime-logs/`
- `.uv-cache/`
- `.venv/`
- generated debug output under ignored locations

Deletion is destructive. Coding agents must explain the need, affected paths, and risk before asking for approval.
