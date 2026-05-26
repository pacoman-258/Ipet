# Neo Brain LLM API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect Ipet Brain to user-configured LLM APIs in OpenAI-compatible, Ollama, and Anthropic-compatible formats.

**Architecture:** Add a focused `brain/llm.py` provider boundary that normalizes config, builds provider-specific requests, parses text responses, and keeps network details out of `backend/app.py`. The Neo backend keeps `/api/chat/stream` as the UI contract but calls Brain when a configured endpoint is present, otherwise it returns the existing local placeholder. The web settings page gains an explicit provider selector and persists redacted Brain API settings through `/api/settings/config`.

**Tech Stack:** Python 3.12, FastAPI, httpx, unittest, vanilla HTML/CSS/JS settings UI.

---

## File Structure

- Create `brain/llm.py` for provider config, request building, response parsing, and `run_brain_turn`.
- Modify `brain/__init__.py` to export the Brain LLM boundary.
- Modify `backend/app.py` to add Brain provider defaults, sanitize settings, and call `run_brain_turn` from chat SSE.
- Modify `settings.html` and `settings.js` to expose provider selection and endpoint/key/model fields.
- Modify `app/settings_schema.py` and settings tests to reflect provider selection.
- Add `tests/test_brain_llm_providers.py` for red-green provider request tests.
- Extend `tests/test_neo_backend_contract.py` and `tests/test_neo_aspect_settings_page.py`.
- Create `docs/reports/2026-05-26-neo-brain-llm-api.html`.

## Task 1: Provider Boundary

- [x] Write failing tests for OpenAI-compatible, Ollama, and Anthropic-compatible request/parse behavior.
- [x] Implement `brain/llm.py` with `BrainProviderConfig`, `BrainMessage`, `BrainCompletion`, `BrainLLMError`, `run_brain_turn`, and provider-specific helpers.
- [x] Verify tests pass.

## Task 2: Backend Chat Integration

- [x] Write failing tests showing configured Brain replaces the placeholder and missing endpoint keeps the placeholder.
- [x] Add provider defaults and settings sanitization.
- [x] Wire `/api/chat/stream` to `run_brain_turn`.
- [x] Verify backend contract tests pass.

## Task 3: Settings UI

- [x] Write failing tests for provider selector and settings persistence hooks.
- [x] Add provider selector to `settings.html`.
- [x] Update `settings.js` defaults, load, save, and summary rendering.
- [x] Verify settings UI tests pass.

## Task 4: Verification And Report

- [x] Run `python -m py_compile backend/app.py brain/llm.py app/settings_schema.py`.
- [x] Run targeted Brain/backend/settings tests.
- [x] Generate the HTML task report.
- [x] Commit the implementation.
