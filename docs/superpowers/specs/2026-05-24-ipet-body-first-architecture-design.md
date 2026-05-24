# Ipet Body-First Architecture Design

## Purpose

Ipet is a local desktop companion with a visible body, an LLM brain, human-style computer operation, and reviewed long-term growth.

This redesign intentionally removes Ipet's agent-runtime platform role. Ipet will not manage AstrBot, Hermes, Codex, MCP servers, plugins, external providers, or runtime compatibility layers. Ipet may still use apps such as Codex, browsers, IDEs, chat tools, and system settings, but it uses them the way a human would: observe, point, click, type, wait, read, and adapt.

The final project should become small and legible enough that its top-level structure reflects the product model:

```text
Ipet
|-- Body
|   |-- Live2D
|   |-- expressions / motions / bubbles / voice
|   `-- desktop presence
|
|-- Brain
|   |-- LLM replies
|   |-- intent understanding
|   |-- next-step decision making
|   `-- self state and persona expression
|
|-- Human Ops
|   |-- screen observation
|   |-- click
|   |-- typing
|   |-- hotkeys
|   |-- app launching
|   |-- waiting
|   `-- result reading
|
`-- Memory & Skills
    |-- long-term memory
    |-- user preferences
    |-- relationship memory
    |-- task experience
    `-- reusable operation recipes
```

## Product Principles

1. Ipet has its own LLM brain, but it is not an agent runtime.
2. Ipet decides one next step at a time. It does not execute opaque multi-step plans.
3. Ipet freely says and observes.
4. Ipet must request human review before acting, remembering, or learning a skill.
5. Human Ops is the universal compatibility layer. External apps are operated through their user interfaces unless a higher-density read-only channel is explicitly useful.
6. Skills are reviewed behavior recipes, not plugins or MCP tools.
7. The web settings page remains, with the current visual language and interaction style preserved, but its content is rebuilt around the new product model.

## Non-Goals

- Do not preserve runtime compatibility as a product requirement.
- Do not keep AstrBot or Hermes as first-class architectural concepts.
- Do not manage MCP servers, agent plugins, external runtime skills, provider catalogs, or runtime sessions.
- Do not build a multi-agent system inside Ipet.
- Do not turn skills into executable plugin bundles.
- Do not let Ipet silently change the computer or its own long-term memory.

## Target Project Shape

The exact file names may change during implementation, but the final structure should converge toward these ownership boundaries:

```text
Ipet/
|-- body/
|   |-- live2d/
|   |-- desktop_shell/
|   |-- speech/
|   |-- expressions.py
|   `-- motions.py
|
|-- brain/
|   |-- llm_client.py
|   |-- controller.py
|   |-- decisions.py
|   |-- persona.py
|   `-- prompts.py
|
|-- human_ops/
|   |-- observe.py
|   |-- screen.py
|   |-- accessibility.py
|   |-- actions.py
|   |-- previews.py
|   |-- approvals.py
|   `-- verification.py
|
|-- memory/
|   |-- conversation_store.py
|   |-- long_term_memory.py
|   |-- preferences.py
|   `-- relationship.py
|
|-- skills/
|   |-- recipes.py
|   |-- proposals.py
|   |-- review.py
|   `-- store.py
|
|-- app/
|   |-- api.py
|   |-- config.py
|   `-- routes/
|
|-- frontend/
|   |-- index.html
|   |-- live2d_runtime.js
|   |-- chat_ui.js
|   |-- action_preview.js
|   |-- settings.html
|   |-- settings.css
|   `-- settings.js
|
|-- model/
|-- docs/
`-- tests/
```

The current root `index.html`, `settings.html`, `settings.css`, and `settings.js` can remain at the root during transition if the desktop host and backend still load them from root paths. The architectural destination is still a frontend-owned boundary.

## Body

Body owns Ipet's visible and audible presence:

- Live2D rendering and model loading.
- Expressions, motions, lip sync, bubbles, and voice playback.
- Desktop window behavior, tray behavior, position, transparency, drag, resize, lock state, and always-on-top behavior.
- Presentation of action previews and approval prompts.

Body does not own task reasoning. It renders state from Brain and Human Ops.

## Brain

Brain owns Ipet's LLM-backed personality and next-step decisions.

Inputs:

- User message.
- Recent conversation.
- Relevant long-term memory.
- Current self state.
- Current observation from Human Ops.
- Available approved skill recipes.
- Pending approval or review state.

Outputs:

- `say`: speak or display a response to the user.
- `observe`: ask Human Ops for another observation.
- `propose_act`: request approval for one concrete external action.
- `propose_remember`: request approval for one memory write.
- `propose_learn_skill`: request approval for one skill recipe write.

Brain must choose only one next step per cycle. It may keep a conversational goal in state, but it must not hide a long autonomous action chain from the user.

The loop is:

```text
observe -> think -> say / observe / propose_act / propose_remember / propose_learn_skill
          -> review if required
          -> execute if approved
          -> observe again
```

## Human Ops

Human Ops owns Ipet's computer-use ability. It provides a small set of concrete operations:

- Observe screen.
- Observe accessibility tree or window metadata where available.
- Focus a window.
- Launch an app.
- Click a point or UI target.
- Type text.
- Press hotkeys.
- Scroll.
- Drag.
- Read or set clipboard when approved.
- Wait for a condition or time interval.
- Verify result after an action.

Human Ops is intentionally lower-level than an agent runtime. It should not know about Codex, AstrBot, Hermes, or browser internals as product concepts. Those are just apps or windows Ipet can observe and operate.

## Reviewable Action Model

Ipet may freely execute:

```text
say
observe
```

Ipet must request approval before:

```text
act
remember
learn_skill
```

Review payloads must make the proposed change human-readable before execution.

Examples:

- Click: show a small red dot hovering over the intended click location, plus a short explanation such as "I want to click this send button."
- Typing: show the exact text that will be typed and where Ipet believes it will go.
- Hotkey: show the key combination, target app/window, and intended effect.
- Drag: show start point, end point, and approximate path.
- Scroll: show direction, amount, and target window.
- Launch app: show app name, source, and expected window.
- Clipboard write: show the content or a safe summary if long.
- Memory write: show the proposed memory text and category.
- Skill write: show the proposed skill name, trigger conditions, steps, cautions, and verification method.

After approval, Human Ops executes one action and immediately observes again so Brain can decide the next step from fresh state.

## Memory & Skills

Memory is Ipet's durable personal context. It stores:

- Conversation history.
- Long-term facts.
- User preferences.
- Relationship memory.
- Task experience.

Skill recipes are reviewed operation memories. They are not plugins, MCP tools, or arbitrary code. A skill recipe contains:

- Name.
- When to use it.
- Trigger cues.
- Human Ops steps.
- Things to check before acting.
- Verification method.
- When to ask the user instead of continuing.

Ipet may propose memory or skill updates after a task completes, but writes happen only after human review and approval. Users must be able to edit, reject, disable, merge, or delete proposed and saved skill recipes.

## Web Settings Page

The web settings page is retained. Its visual style, layout language, and existing interaction feel should remain recognizable. The content changes substantially to match the new architecture.

Remove or archive settings sections for:

- Runtime selection.
- AstrBot and Hermes runtime status.
- Runtime sidecar commands.
- MCP server management.
- External runtime skills.
- Agent mode, planner mode, runtime proxy details, and compatibility surfaces.

New settings sections:

- Body: Live2D model, expressions, motions, bubble behavior, voice/TTS, ASR, window behavior, and desktop presence.
- Brain: model endpoint, model name, API key storage, persona profile, response style, self-state display, and single-step decision tuning.
- Human Ops: observation permissions, accessibility permissions, click preview behavior, red-dot styling, action review defaults, allowed action types, and per-action approval policy.
- Memory: conversation saving, long-term memory categories, review queue, retention policy, export/import, and privacy controls.
- Skills: saved recipe list, proposal queue, enable/disable, edit, merge, delete, and review history.
- Diagnostics: local health, last observation status, last approved action, last failed action, and logs relevant to Body, Brain, Human Ops, and Memory.

The settings page should not become a control panel for external agent ecosystems. It should configure Ipet itself.

## Removal And Migration Direction

The refactor can be aggressive. The following areas should be removed, archived, or replaced by the new modules:

- Agent graph and local planner/orchestrator code.
- Runtime adapter and runtime contract layers for AstrBot/Hermes compatibility.
- Runtime proxy chat routes.
- MCP management APIs and UI.
- External runtime skill import/delete APIs and UI.
- Hermes-specific control panel copy and settings.
- Third-party MCP storage from the core project model.
- Legacy topic history once conversation memory has a clear replacement path.

The following concepts should be retained and moved into the new boundaries:

- Live2D rendering and presentation mapping.
- ASR/TTS if used for companion interaction.
- Vision and active observation concepts that serve Human Ops.
- Conversation and memory persistence ideas.
- Local settings infrastructure, rewritten for Body, Brain, Human Ops, Memory, and Skills.

## Minimum Product Loop

Example: using Codex through Human Ops.

```text
User: Help me modify this project with Codex.
Ipet observe: sees current desktop.
Ipet say: "I'll open Codex and check the current state first."
Ipet propose_act: red dot previews the Codex icon or target window.
User approves.
Ipet act: clicks.
Ipet observe: reads Codex window.
Ipet propose_act: shows the exact task text it wants to type.
User approves.
Ipet act: types and sends.
Ipet observe: waits for and reads the result.
Ipet say: summarizes what Codex did.
Ipet propose_learn_skill: offers a reviewed recipe for "Use Codex to modify this project."
User reviews and approves or edits.
Ipet saves the recipe.
```

## Testing Strategy

Tests should prove the new boundaries rather than preserve old runtime compatibility.

- Brain decision tests: given observation and memory, output exactly one next-step decision.
- Permission tests: `act`, `remember`, and `learn_skill` cannot execute without approval.
- Preview tests: click proposals include coordinates and red-dot preview metadata.
- Memory tests: proposed memories are not saved until approved.
- Skill tests: proposed recipes are editable and not saved until approved.
- Settings contract tests: settings payload contains Body, Brain, Human Ops, Memory, and Skills sections, with no runtime/MCP management fields.
- Human Ops tests: action execution is followed by a fresh observation request.

## Open Implementation Notes

- During migration, keep root UI file loading stable until the desktop shell is split.
- Prefer deleting compatibility code over wrapping it if no new-product requirement depends on it.
- Keep destructive migrations separate from behavior changes so each removal is reviewable.
- Preserve user data where it maps to the new model, especially conversation history, memories, persona configuration, and model assets.
