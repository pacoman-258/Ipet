# Neo Brain Model Discovery Implementation Plan

**Goal:** Let the user fetch available models from the API address and key currently entered in the web settings page, then click a model to fill the Brain model name field.

**Provider endpoints:**

- OpenAI-compatible: `GET /v1/models`
- Ollama: `GET /api/tags`
- Anthropic-compatible: `GET /v1/models`

## Task 1: Contract Tests

- [x] Add provider tests for model-list parsing across OpenAI-compatible, Ollama, and Anthropic-compatible responses.
- [x] Add backend route tests for draft settings, saved key fallback, no key echo, and missing endpoint validation.
- [x] Add settings page tests for fetch button, model list container, status text, route usage, and one-click fill hook.

## Task 2: Backend And Brain Provider

- [x] Add `BrainModel` and `list_provider_models`.
- [x] Normalize provider-specific model payloads into `{id, label}` records.
- [x] Add `/api/brain/models` without persisting draft settings.
- [x] Reuse saved API key only when the selected provider matches the saved provider.

## Task 3: Settings UI

- [x] Add model discovery panel under Brain endpoint.
- [x] Add model fetch button, inline status, and clickable model option buttons.
- [x] Clicked models fill `brain-model-name` and refresh the summary.

## Task 4: Verification And Report

- [x] Update workflow and architecture docs.
- [x] Generate the HTML task report.
- [x] Run final verification and commit.
