# Workflow Reference

## Purpose

This file is the canonical workflow map for the runtime chat and agent pipeline.

Update rule:

- Any change to routing, agent-loop behavior, approval flow, tool exposure, skill discovery, topic persistence in the chat path, or frontend chat interaction flow must update this file in the same task.

## Scope

This document covers the stable runtime workflow across:

- `index.html`
- `backend/app.py`
- `backend/agent_orchestrator.py`
- `backend/agent_graph.py`
- `backend/chat_topics.py`

It focuses on the `chat`, `skill`, and `react` execution paths, especially the routed agent workflow.

## Top-Level Flow

```text
User types message in index.html
        |
        v
POST /api/chat/stream
        |
        v
Build request-scoped runtime context
        |
        v
Build working_messages from topic/session history + current user text
        |
        v
Optional router classify_route(...)
        |
        +--> chat mode: search/no-search routing
        |
        +--> skill mode: visible skill selection
        |
        +--> react mode:
                skill_task
                complex_task
                simple_tool_task
                direct_answer
        |
        v
Start AgentGraphRuntime turn
        |
        v
decide_or_respond
        |
        +--> final_reply
        |
        +--> approval_required
                |
                v
        frontend approval bubble
                |
                +--> approve
                |      |
                |      v
                |   execute_tools
                |      |
                |      v
                |   continuation_check
                |      |
                |      +--> more approval_required
                |      |
                |      +--> final_reply
                |
                +--> reject without extra input
                |      |
                |      v
                |   final_reply without tools
                |
                +--> reject with extra input
                       |
                       v
                   pause current turn
                       |
                       v
                   wait for user follow-up text
                       |
                       v
                   resume same turn_id
                       |
                       v
                   decide_or_respond again
```

## Request Entry

### 1. Frontend send path

- The runtime UI sends chat requests from `index.html`.
- The backend entry is `POST /api/chat/stream`.
- The request includes mode, router settings, model settings, skill IDs, and tool toggles.

### 2. History and topic context

- If topic history is enabled, `session_id` is normalized into `topic_id`.
- The backend now builds a single request-scoped runtime context before routing or graph start.
- That context reuses one `raw_config` snapshot to derive settings, tooling, router config, and default active skills.
- Existing topic summaries are used to rebuild model context through one topic runtime snapshot lookup.
- The visible UI still uses full transcript history, but `SESSION_STORE` is refreshed from the same snapshot payload.
- `working_messages` is the active conversation state plus the current user message.

### 3. Request-scoped runtime context

- `POST /api/chat/stream` reads `_load_full_config()` once per request.
- `settings_config` and `tooling_cfg` are derived from that same in-memory config snapshot.
- Default `resolved_skills` are resolved once at request entry.
- A second skill resolve is only allowed if routing changes the selected skill IDs.
- The graph start state now carries `settings_config` and `tooling_config`, so approval resume and later tool phases do not need to re-read chat settings for the same turn.
- In `react` mode, active skill prompt text is visibility-sanitized before entering the graph:
  - workflow instructions stay visible
  - hidden MCP or script tool names from raw `SKILL.md` are replaced with generic placeholders
  - `Dependencies:` sections are not exposed before the runtime actually reveals those tools through phase routing or planner-selected handoff

### 4. Topic runtime snapshot cache

- `TopicStore` exposes a runtime snapshot that bundles:
  - `meta`
  - `model_messages`
  - `full_messages`
- Snapshot cache key is `topic_id`.
- Snapshot cache version is `meta.updated_at`.
- Cache invalidates after any topic write that changes message content or summary structure, including:
  - `append_exchange`
  - `apply_mini_summary`
  - `apply_major_summary`
  - `delete_topic`
- This means `/api/chat/stream` no longer separately does `has_topic()`, `build_model_messages()`, and `load_full_messages()` on the hot path.

### 5. Performance metrics

- `/api/chat/stream` writes a structured backend log event named `chat_stream_perf`.
- `/api/chat/approval` writes a structured backend log event named `chat_approval_perf`.
- These metrics are backend-only and do not change SSE events or frontend contracts.
- Typical timing keys include:
  - `load_config`
  - `derive_settings_tooling`
  - `resolve_skills`
  - `load_topic_snapshot`
  - `router_classify`
  - `build_messages`
  - `graph_start`
  - `approval_resume`
  - `finalize_exchange`

## Router Stage

### 1. Router enablement

- The router only runs when `router_enabled` is true.
- If disabled, the runtime skips straight to the execution model.

### 2. Router outputs

The router returns a mode-dependent route decision.

Runtime prompt management:

- Runtime LLM prompts and protocol/system prompt snippets are centralized in `backend/runtime_prompts.py`.
- When adjusting router, decider, planner, writer, leader, recheck, or expression-protocol prompt text, update that file first.

For `react` mode it may choose:

- `skill_task`
- `complex_task`
- `simple_tool_task`
- `direct_answer`

Important behavior:

- `skill_task` means one or more currently visible skills should be preferred.
- `complex_task` means fall back to the general agent loop.
- `simple_tool_task` is only for one explicit direct tool step.
- `direct_answer` means no tool or skill work is needed.
- If the router still returns `direct_answer` or `complex_task`, but the user explicitly asks to use a visible skill workflow and the current active skill set can be matched confidently, the backend upgrades that turn to `skill_task` before graph start.
- In React mode, if the user explicitly asks what skills, MCP tools, tools, or capabilities are currently available, the backend upgrades a `direct_answer` route into the general agent loop so the internal planner can enumerate the current inventory instead of guessing.

### 3. Router does not execute searches

- The router classifies only.
- It does not itself run capability discovery or concrete tools.
- Real capability planning happens later inside the agent graph, depending on phase.

## Agent Graph Stages

Logical-role mapping:

- `decide_or_respond` = current round `decider + searcher + planner + writer`
- `continuation_check` = `writer loop controller + leader`

The runtime graph is:

```text
prepare_context
  -> decide_or_respond
     -> require_approval
        -> execute_tools
           -> continuation_check
              -> require_approval or final_reply
     -> final_reply
```

### 1. `prepare_context`

- Normalizes state for the turn.
- Carries forward tool results, reasoning step, selected skills, selected MCP tools, inventory state, and trace items.
- Also carries forward loop-specific state:
  - `loop_round`
  - `last_expected_effect`
  - `last_assessment`
  - `last_inventory_result`
  - `rejected_call_signatures`
  - `no_progress_streak`
  - `phase_events`

### 2. `decide_or_respond`

- Handles route shortcuts first.
- For `simple_tool_task`, converts the routed tool call into an approval request directly.
- For `react` mode, it now runs one internal round in this order:
  - `decider`: decide whether tools are needed at all
  - `searcher`: first check settings-page default-enabled skills, then classify MCP task domains against currently registered non-skill MCP tools
  - `planner`: turn the searcher result into a structured multi-step execution plan
  - `writer`: only see the current plan step's candidate tool set and turn it into the next concrete approval batch
- In React mode this stage also performs an action-request re-check so actionable asks are less likely to be treated as direct-answer turns.
- Explicit capability-availability requests can still finish here with a deterministic inventory result and no approval bubble.
- The searcher only sees:
  - settings-page `default_active_ids` skills
  - runtime-registered non-skill MCP tools
- The planner no longer picks only one skill-or-tool bundle. It now emits an execution plan:
  - `plan_id`
  - `plan_summary`
  - `reason`
  - `steps[]`
- Each step contains:
  - `step_id`
  - `title`
  - `goal`
  - `success_criteria`
  - `call_mode = single | batch`
  - `candidate_tool_sets`
  - `notes`
- Produces either:
  - a final reply path
  - or an approval request with the current step's planned tool batch

### 3. `require_approval`

- Suspends the graph with LangGraph `interrupt(...)`.
- The frontend receives `approval_required`.
- Resume data currently includes:
  - `turn_id`
  - `approved`
  - `user_text`

### 4. `execute_tools`

- Executes the approved writer batch.
- Persists cumulative tool results.
- For a multi-tool batch, runtime execution is still serial in the batch order.
- Records the latest batch result so the same plan step can continue or complete without re-running searcher/planner.

### 5. `continuation_check`

- First evaluates the current writer step before involving `leader`.
- Step-loop rules:
  - one writer batch can contain one tool call (`single`) or multiple tool calls (`batch`)
  - if the current batch completes the step, runtime advances to the next step without re-running searcher/planner
  - if the step still needs work, runtime stays on the same step and asks writer for another batch
  - after repeated no-progress batches, runtime can switch to the next candidate tool set for that step
  - if the step is exhausted or blocked, the plan is marked blocked
- `leader` is only consulted at plan boundaries:
  - plan completed
  - plan blocked
  - or explicit need for a fresh replan
- Additional fail-safes still apply:
  - `loop_round` increments only when the runtime starts a fresh searcher-first plan round
  - rejected call signatures are blocked from immediate reuse
  - no-progress protection still stops unproductive loops early
  - the outer max loop-round cap still prevents infinite continuation churn

### 6. `final_reply`

- Streams the final assistant answer.
- If tools were rejected without extra input, it falls back to a no-tool answer.
- If tools were rejected with extra input, it uses the updated prompt chain for the continued turn.
- An `已完成` bubble ends the current frontend thought session; the next user message starts a new agent-loop display chain.

## Execution Phases

The runtime exposes different tool sets depending on phase.

### 1. `skill_selection`

Tool exposure:

- visible skill tools
- `system.agent_loop`

Use case:

- start from visible default skills
- or switch into the general agent loop

### 2. `skill_execution`

Tool exposure:

- tools from selected active skills
- allowlisted MCP tools for those skills

Use case:

- run inside already selected skills
- or continue a planner-selected skill handoff

### 3. `agent_loop`

Tool exposure:

- ordinary agent loop: runtime-available non-skill MCP tools
- planner-selected MCP handoff: only the selected concrete MCP tools

Use case:

- general fallback workflow after skill-based selection is not enough

Important implication:

- The system is not a flat "search everything at once" workflow.
- It is phase-driven.
- If the route starts as `complex_task`, the turn enters `agent_loop` directly.
- The decider does not guess concrete tools by itself.
- It now hands off to `searcher`, then `planner`, then a step-scoped `writer`.

## React Discovery Flow

```text
skill_selection
  -> visible skill tool
  -> or system.agent_loop
       -> agent_loop
          -> decider
          -> searcher
          -> planner execution plan
          -> writer step / batch
          -> concrete batch execution
          -> same-step continuation or next-step continuation
          -> leader assessment at plan boundary
          -> next round or final reply
```

## Searcher / Planner Semantics

### 1. `system.agent_loop`

- Available outside `agent_loop`.
- Switches the turn into the general fallback loop.
- After it succeeds, the next phase becomes `agent_loop`.
- It also clears any route-selected or planner-selected concrete handoff bundle from the turn state.

### 2. Searcher

- Runs inside `decide_or_respond`.
- Searcher visibility is fixed:
  - skills: only settings-page default-enabled skills
  - MCP: all currently registered non-skill MCP tools
- Searcher can:
  - return deterministic inventory for explicit "what capabilities are available" questions
  - match one default-enabled skill
  - or classify task types and narrow MCP tools
- Backend task types are fixed:
  - `browser_automation`
  - `web_search`
  - `file_io`
  - `general_mcp`

### 3. Planner

- Runs after searcher and only sees the searcher-selected context.
- Skill path input:
  - user request
  - selected skill prompt
- MCP path input:
  - user request
  - task types
  - filtered MCP tools
- Planner returns an execution plan instead of a single bundle:
  - `plan_id`
  - `plan_summary`
  - `reason`
  - `steps[]`
- Each step contains:
  - `step_id`
  - `title`
  - `goal`
  - `success_criteria`
  - `call_mode`
  - `candidate_tool_sets`
  - `notes`

### 4. Writer-loop state

- New React turn state fields:
  - `selection_origin`
  - `selected_skill_ids`
  - `selected_tool_names`
  - `current_searcher_result`
  - `current_execution_plan`
  - `current_step_index`
  - `current_step_attempt`
  - `current_candidate_tool_set_index`
  - `current_candidate_no_progress`
  - `current_batch_id`
  - `completed_steps`
  - `blocked_steps`
  - `step_history`
  - `writer_loop_active`
  - `loop_round`
  - `last_expected_effect`
  - `last_assessment`
  - `last_inventory_result`
  - `rejected_call_signatures`
- `selection_origin="route"` means the router chose currently visible skills directly.
- `selection_origin="planner"` means the internal planner selected the current bundle for writer.

## Approval Flow

### 1. Normal approval

```text
approval_required
  -> user clicks Approve
  -> POST /api/chat/approval { approved: true }
  -> execute_tools
  -> continuation_check
```

### 2. Reject without follow-up

```text
approval_required
  -> user clicks reject path but gives no extra guidance
  -> POST /api/chat/approval { approved: false, user_text: "" }
  -> final_reply without tool execution
```

### 3. Reject with follow-up

```text
approval_required
  -> user clicks "Reject And Guide"
  -> frontend keeps same pending turn paused
  -> user types extra guidance
  -> POST /api/chat/approval { approved: false, user_text: "..." }
  -> graph resumes same turn_id
  -> inject updated user guidance into working/decision/prompt messages
  -> decide_or_respond again
```

Frontend UX contract:

- The reject button text is `Reject And Guide`.
- After clicking it, the current turn stays paused.
- The input box becomes the follow-up channel for that same turn.
- The `phase` SSE event name stays the same, but payloads may now include:
  - `loop_round`
  - `thought_role`
- This allows the frontend to group React thought output by `loop_round + thought_role` and render one collapsible "思考中：当前第 X 轮" block with child items for `decider / planner / writer`.
- Thought groups are also keyed by a frontend-local thought session. Once a `done` bubble is rendered, that session is closed; the next user message starts a new agent-loop thought chain even if it begins again at `loop_round=1`.

## Finalization

### 1. Answer streaming

- `stream_final_reply(...)` produces the final deltas.
- The frontend renders either:
  - NDJSON segmented expression output
  - or plain token streaming

### 2. Conversation persistence

- After final answer completion, the backend persists the exchange.
- Topic history writes:
  - transcript records
  - summary updates when thresholds are met
- Non-topic mode writes trimmed session memory into `SESSION_STORE`.

## Maintenance Checklist

Update this file whenever any of the following change:

- route kinds or router prompt contract
- execution phases or phase transitions
- tool exposure rules in `_build_runtime_tool_bridge(...)`
- capability planner behavior or planner handoff semantics
- approval request or approval resume payload shape
- reject-and-follow-up behavior
- frontend chat send, pause, or approval interaction model
- finalization or persistence path in the chat workflow

