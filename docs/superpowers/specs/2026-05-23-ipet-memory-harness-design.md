# Ipet Memory Harness Design

## Goal

Build an Ipet-owned conversation and memory harness so Ipet, not AstrBot or any future
agent kernel, owns durable conversation history, long-term memory, retrieval, and
save policy. AstrBot v1 integration becomes a temporary task executor: each chat turn
uses a fresh AstrBot session, streams the answer, and then deletes the runtime session.

## Background

The current Ipet runtime path proxies chat streams and topic endpoints through the active
runtime. In AstrBot mode this means Ipet's history drawer can reflect AstrBot webchat
sessions, and durable history is still entangled with the runtime. That conflicts with
the target architecture: Ipet should be the harness and persistent state owner, while
AstrBot or a replaceable agent kernel should handle only short-lived task execution.

NanmiCoder/cc-haha is the reference direction: a harness outside the agent runtime owns
file-based state, memory indexes, retrieval, and background extraction. Ipet should adapt
that idea to its desktop pet architecture rather than copying implementation details.

Reference material:

- https://github.com/NanmiCoder/cc-haha
- https://raw.githubusercontent.com/NanmiCoder/cc-haha/main/docs/memory/01-usage-guide.md
- https://raw.githubusercontent.com/NanmiCoder/cc-haha/main/docs/memory/02-implementation.md

## Decisions Already Made

- Use option 1: Ipet Memory Harness wraps the active runtime.
- AstrBot v1 uses one temporary AstrBot session per Ipet chat turn.
- Ipet deletes the AstrBot temporary session after the turn finishes.
- Ipet uses local file storage for conversation history and long-term memory.
- Ordinary chat saves by default.
- Long-term memory is automatically extracted and saved.
- If the user asks not to save the conversation or turn, Ipet does not save that content.
- Add a new temporary chat mode with the same runtime features but no durable content writes.
- The new conversation history layer does not need to follow the existing `TopicStore`
  layout. It should be redesigned in the cc-haha direction.

## Non-Goals

- Do not modify AstrBot core.
- Do not depend on AstrBot knowledge bases, chat history, or conversation storage.
- Do not add a vector database in v1.
- Do not remove existing `data/chat_topics/` compatibility data in the first migration.
- Do not redesign TTS, ASR, vision, tools, skills, or frontend rendering beyond the
  memory mode controls needed for this feature.

## Architecture

Add an Ipet-owned `MemoryHarness` around the active runtime.

The harness is responsible for:

- loading local conversation state;
- loading and ranking relevant long-term memory;
- building the runtime prompt/context for the current turn;
- creating a fresh temporary runtime session;
- streaming runtime events back to the existing frontend SSE contract;
- cleaning up runtime session state after the turn;
- deciding whether the turn is durable;
- saving local conversation history;
- extracting and saving long-term memory;
- exposing local topic/history endpoints to the existing UI.

AstrBot becomes an implementation of a narrower runtime-task interface:

- start one streamed task;
- use the supplied runtime session id;
- emit existing SSE-compatible events;
- delete the runtime session by id.

Future kernels should implement the same task-oriented adapter surface so Ipet memory
does not depend on AstrBot-specific storage.

## Data Layout

### Conversation History

Create a new Ipet conversation store:

```text
data/ipet_conversations/
|-- INDEX.md
|-- conversations/
|   `-- <conversation_id>/
|       |-- conversation.md
|       |-- turns.jsonl
|       |-- state.json
|       `-- summaries/
|           |-- rolling.md
|           `-- major.md
`-- temp/
    `-- <runtime_turn_id>.json
```

File responsibilities:

- `INDEX.md`: human-readable index inspired by cc-haha memory indexes. It lists
  conversation title, updated time, short summary, tags, mode, and path.
- `conversation.md`: readable transcript and summary for debugging, review, and manual
  inspection.
- `turns.jsonl`: machine-readable append-only turn records.
- `state.json`: conversation state and save policy.
- `summaries/rolling.md`: compact recent-context summary.
- `summaries/major.md`: durable cross-turn summary.
- `temp/*.json`: runtime cleanup state only. It must not store full business transcript
  content for temporary chat.

`turns.jsonl` records should include:

```json
{
  "turn_id": "ipet-turn-id",
  "created_at": "ISO-8601 timestamp",
  "status": "completed",
  "memory_mode": "persistent",
  "save_policy": "save",
  "runtime": "astrbot",
  "runtime_session_id": "ipet-temp-ipet-turn-id",
  "user": {"role": "user", "content": "..."},
  "assistant": {"role": "assistant", "content": "..."},
  "metadata": {
    "chat_mode": "react",
    "vision_used": false,
    "interrupted": false
  }
}
```

`state.json` should include:

```json
{
  "conversation_id": "stable-id",
  "title": "Conversation title",
  "created_at": "ISO-8601 timestamp",
  "updated_at": "ISO-8601 timestamp",
  "memory_mode": "persistent",
  "save_disabled": false,
  "save_disabled_reason": "",
  "assistant_turn_count": 0,
  "last_summary_turn": 0,
  "last_memory_extracted_turn": 0,
  "tags": []
}
```

### Long-Term Memory

Create a separate local memory store:

```text
data/ipet_memory/
|-- MEMORY.md
`-- memories/
    `-- <memory_id>.md
```

`MEMORY.md` is the durable memory index. Each memory file stores one stable fact,
preference group, important decision, long-running plan, or durable constraint.

Memory files should include front matter-like metadata in plain Markdown:

```markdown
# Memory Title

- id: memory-id
- created_at: ISO-8601 timestamp
- updated_at: ISO-8601 timestamp
- source_conversation_id: conversation-id
- source_turn_id: turn-id
- tags: preference, project

## Summary

Durable memory text.

## Evidence

Short quoted or paraphrased source evidence from the conversation.
```

## Request Lifecycle

For ordinary persistent chat:

1. Frontend calls `POST /api/chat/stream`.
2. `MemoryHarness` normalizes `conversation_id` and `memory_mode`.
3. Harness loads conversation state, summaries, recent turns, and relevant long-term
   memory.
4. Harness checks the current user message for save-disable intent.
5. Harness builds a temporary runtime prompt containing:
   - system/persona prompt;
   - bounded local vision evidence when applicable;
   - relevant long-term memory;
   - conversation summaries;
   - recent turns;
   - current user request.
6. Harness creates a `runtime_turn_id`.
7. Harness calls AstrBot with `session_id = ipet-temp-<runtime_turn_id>`.
8. Harness streams adapted runtime events unchanged to the frontend.
9. On `done`, harness persists the turn only if saving is allowed.
10. Harness runs automatic memory extraction only if saving is allowed and the turn
    completed.
11. Harness updates `INDEX.md`, conversation Markdown, summaries, and memory index as
    needed.
12. In `finally`, harness deletes the AstrBot temporary session.

For temporary chat:

1. Follow the same runtime prompt, streaming, tool, vision, and TTS behavior.
2. Do not write the user request, assistant answer, summaries, or long-term memory.
3. Store only minimal cleanup state if AstrBot deletion fails.

## Save Policy

### Modes

- `persistent`: default ordinary chat. Ipet saves conversation turns and extracts
  long-term memory.
- `temporary`: same capabilities, no durable business content writes.

### User Save-Disable Intent

The harness should detect phrases such as:

- "不保存此次对话"
- "这次不要保存"
- "不要记住"
- "别记录"
- "临时聊聊"
- "don't save this"
- "do not remember this"
- "off the record"

The first implementation can use a conservative lexical detector. Later versions can
route ambiguous cases through a small classifier.

Save-disable semantics:

- If the request clearly applies to the current turn, do not persist the current turn and
  do not extract memory from it.
- If the request clearly applies to the whole conversation, set
  `state.json.save_disabled = true` and skip all future durable writes in that
  conversation.
- If the user says not to use memory or history, the harness should also skip retrieval
  for that turn.

## Memory Extraction

Automatic extraction runs after a completed persistent turn when save policy allows it.

The extractor should save only durable information:

- stable user preferences;
- personal facts that are useful for future assistance;
- long-running plans or recurring tasks;
- project decisions and constraints;
- unresolved commitments that should survive the current conversation.

The extractor must not save:

- credentials, secrets, tokens, or private keys;
- one-off commands or transient troubleshooting details;
- content from temporary chat;
- content from a no-save turn;
- full sensitive user text when a short paraphrase is enough.

V1 storage can use deterministic text files and simple search:

- index scan over `MEMORY.md`;
- keyword overlap and recency scoring;
- duplicate suppression by normalized title plus content containment;
- append/update Markdown files through `IpetMemoryStore`.

Failures in extraction or memory writes must not fail the chat response. They should be
logged and retried from `last_memory_extracted_turn` when safe.

## AstrBot Temporary Sessions

For each streamed turn:

- Generate `runtime_turn_id`.
- Use AstrBot session id `ipet-temp-<runtime_turn_id>`.
- Send a single task prompt to AstrBot.
- Stream AstrBot events to the frontend.
- Delete the AstrBot session in `finally`.

Cleanup rules:

- If chat succeeds and delete succeeds, remove any temp cleanup file.
- If delete fails, keep a minimal `data/ipet_conversations/temp/<runtime_turn_id>.json`
  cleanup record containing runtime id, runtime session id, timestamps, and error text.
- Startup or periodic cleanup can retry deletion for stale `ipet-temp-*` records.
- Delete failure must not hide a successful answer from the user.
- Temporary chat cleanup records must not contain transcript content.

## API Changes

Keep the frontend-facing API stable where possible:

- `POST /api/chat/stream`: handled by `MemoryHarness`.
- `POST /api/chat/topics`: creates a local Ipet conversation.
- `GET /api/chat/topics`: lists local Ipet conversations from the new store.
- `GET /api/chat/topics/{id}`: returns local Ipet conversation detail.
- `DELETE /api/chat/topics/{id}`: deletes or soft-deletes local Ipet conversation data.

Extend chat request payload with:

```json
{
  "memory_mode": "persistent"
}
```

Accepted values:

- `persistent`
- `temporary`

Existing clients that omit `memory_mode` default to `persistent`.

The existing AstrBot session-listing behavior should move out of the primary history
drawer path. If needed, expose it later as diagnostics, not as Ipet chat history.

## UI Changes

The runtime UI should add a temporary chat mode control:

- ordinary chat: appears in history and can update long-term memory;
- temporary chat: same visible capabilities, but marked temporary and absent from
  history.

History drawer should list Ipet conversations, not AstrBot webchat sessions.

If a user asks not to save a turn, the UI does not need to interrupt the flow. The answer
streams normally, and the turn simply does not appear in durable history.

## Migration And Compatibility

Do not delete existing `data/chat_topics/` in v1.

Recommended migration stance:

- new conversations use `data/ipet_conversations/`;
- old `TopicStore` remains available for compatibility tests or future import;
- topic endpoints read the new store first;
- optional later importer can convert `data/chat_topics/<topic_id>/full.jsonl` and
  `summary.json` into the new layout.

## Testing Strategy

Add focused tests before implementation.

Harness tests:

- persistent chat injects summaries, recent turns, and relevant memories into the runtime
  request;
- every turn uses a unique `ipet-temp-*` runtime session;
- `done` triggers local turn persistence;
- `done` triggers AstrBot session deletion;
- delete failure still streams the answer and writes a cleanup record;
- runtime error does not save a completed turn;
- interrupted partial answer can be recorded as interrupted only when policy allows it and
  must not trigger memory extraction.

Conversation store tests:

- creates conversation directory and `INDEX.md`;
- appends `turns.jsonl`;
- updates `conversation.md`;
- reads list/detail in frontend-compatible shape;
- temporary mode writes no conversation content.

Memory store tests:

- writes memory Markdown files;
- updates `MEMORY.md`;
- retrieves relevant memories by keyword;
- suppresses duplicates;
- skips extraction for temporary and no-save turns.

AstrBot adapter tests:

- accepts supplied temporary session id;
- exposes delete-session capability for harness cleanup;
- does not provide primary history list/detail for Ipet history endpoints.

API tests:

- `/api/chat/topics` uses Ipet local conversations, not AstrBot session APIs;
- `/api/chat/stream` with `memory_mode=temporary` does not write conversation or memory
  files;
- no-save user text prevents persistence and extraction;
- frontend topic shape remains compatible with existing history drawer normalization.

Docs tests/checks:

- update `docs/WORKFLOW.md` in the implementation task to state that Ipet owns
  conversation history and long-term memory, while AstrBot owns only temporary task
  execution.

## Implementation Ownership

Lead owner: `backend-agent-runtime`.

Required reviewers:

- `pet-runtime-ui` for temporary chat mode and history drawer behavior;
- `qa-integration` for migration, endpoint contracts, and regression coverage.

`backend/app.py` is a high-conflict file. The implementation plan should keep changes
small and route most new logic into focused modules such as:

- `backend/memory_harness.py`
- `backend/conversation_store.py`
- `backend/ipet_memory_store.py`
- `backend/runtime_tasks.py`

## Open Risks

- AstrBot Dashboard deletion may be unavailable or fail. Mitigation: cleanup records and
  retry cleanup; do not block user-visible answers.
- Prompt size can grow if too many memories are injected. Mitigation: bounded retrieval
  and summary-first context assembly.
- Lexical no-save detection can miss ambiguous requests. Mitigation: conservative phrase
  set in v1, classifier later.
- Old topic-history tests currently expect runtime proxy behavior. Mitigation: update
  tests to assert Ipet-local ownership and keep legacy tests explicitly marked as
  migration coverage where useful.

## Success Criteria

- Ipet can load durable local conversation history without reading AstrBot history.
- Ipet can save and reload conversations from the new file layout.
- Ipet can automatically save local long-term memory.
- Temporary chat produces normal streamed answers without durable conversation or memory
  writes.
- User no-save intent prevents durable writes for the affected turn or conversation.
- AstrBot receives only temporary task context and its temporary session is deleted after
  the turn.
- The frontend history drawer displays Ipet conversations.
- The implementation has targeted unit and API coverage.
