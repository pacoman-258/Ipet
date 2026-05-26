# Neo Brain Structured Say Implementation Plan

**Goal:** Classify Brain LLM replies into structured `BrainDecision` values while keeping the chat-only path usable. This iteration executes only `say`; `observe`, `act`, `remember`, `learn_skill`, and `stop` are reserved for later flows.

**Approach:** Keep provider transport in `brain/llm.py`, parse the provider text after completion, and return a `BrainCompletion` that includes both user-visible text and the classified decision. The backend streams only the visible `say` text while adding the decision payload to `done`.

## Task 1: Tests

- [x] Add failing tests for plain text replies becoming `say`.
- [x] Add failing tests for structured `say` JSON being unwrapped.
- [x] Add failing tests for reserved `observe` and `act` parsing.
- [x] Add failing tests for chat SSE exposing `done.decision.kind`.

## Task 2: Brain Classification

- [x] Add structured reply instructions to the Brain prompt.
- [x] Implement `parse_brain_reply`.
- [x] Preserve provider raw text while returning clean user-visible `say` text.
- [x] Add reserved `stop` decision kind.

## Task 3: Backend Integration

- [x] Convert completion output to `BrainDecision`.
- [x] Stream only visible chat text for `say`.
- [x] Include decision metadata in the `done` SSE event.

## Task 4: Docs, Report, Verification

- [x] Update workflow and architecture docs.
- [x] Generate this task's HTML report.
- [x] Run targeted verification and commit.
