# Neo Backend Destructive Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Ipet's old platform backend and documentation with a Neo Aspect backend centered on Body, Brain, Human Ops, Memory, and Skills.

**Architecture:** The first destructive pass keeps the desktop host and settings page launchable while cutting old external-management surfaces from the active backend contract. `backend/app.py` becomes a small Neo backend with settings, health, simple Brain SSE, TTS/ASR shell support, local conversation topics, and explicit `410 Gone` responses for removed management endpoints.

**Tech Stack:** Python 3.12, FastAPI, vanilla HTML/CSS/JS, `unittest`, Qt WebEngine host.

---

## File Structure

- Replace `backend/app.py` with a slim Neo Aspect FastAPI app.
- Modify `main.py` to stop starting removed sidecars and stop requiring old topic-delete OpenAPI routes for backend health.
- Modify `tests/test_main_runtime_env.py` for the new host health and no-sidecar behavior.
- Create `tests/test_neo_backend_contract.py` for settings, health, chat stream, and legacy `410 Gone` routes.
- Rewrite `README.md`, `README.zh-CN.md`, `README.html`, `AGENTS.md`, `docs/WORKFLOW.md`, `docs/PROJECT_STRUCTURE.md`, `docs/SUBAGENTS.md`, `docs/architecture.md`, and `docs/architecture_ZH.md`.
- Create `docs/reports/2026-05-25-neo-backend-destructive-refactor.html`.

## Task 1: Backend Contract Cutover

- [x] Replace `backend/app.py` with a compact Neo Aspect app.
- [x] Keep these active routes: `/api/health`, `/settings`, `/settings.css`, `/settings.js`, `/api/settings/config`, `/api/chat/stream`, `/api/chat/topics*`, `/api/asr/warmup`, `/api/asr/stream`, `/api/tts`, `/api/audio/{file_name}`.
- [x] Return `410 Gone` for removed old management and import endpoints.
- [x] Keep `GET /api/skills` as a temporary empty recipe list only if `index.html` still calls it.
- [x] Add `tests/test_neo_backend_contract.py`.
- [x] Run `python -m unittest tests.test_neo_backend_contract tests.test_neo_aspect_settings_page tests.test_neo_aspect_core -v`.

## Task 2: Desktop Host Management Removal

- [x] Remove removed sidecar startup from `DesktopPet.__init__()` and config reload.
- [x] Make backend health require only `/api/health`.
- [x] Remove old external-service defaults from `DEFAULT_CONFIG` and normalization.
- [x] Leave backend startup through `uvicorn backend.app:app`.
- [x] Update `tests/test_main_runtime_env.py`.
- [x] Run `python -m unittest tests.test_main_runtime_env -v`.

## Task 3: Documentation Rewrite

- [x] Rewrite README files around `Ipet Neo Aspect`.
- [x] Rewrite `AGENTS.md` as a concise coding-agent guide for Body, Brain, Human Ops, Memory & Skills.
- [x] Rewrite `docs/WORKFLOW.md` as the canonical Neo workflow.
- [x] Rewrite `docs/PROJECT_STRUCTURE.md` and `docs/SUBAGENTS.md` for the new ownership model.
- [x] Rewrite `docs/architecture.md` and `docs/architecture_ZH.md` as canonical architecture docs.
- [x] Ensure docs do not present removed external-management terms as active product architecture.

## Task 4: Verification And Report

- [x] Run `python -m py_compile main.py backend/app.py`.
- [x] Run targeted Neo tests from Tasks 1 and 2.
- [x] Run a documentation audit with `rg` for old architecture terms in active docs.
- [x] Generate `docs/reports/2026-05-25-neo-backend-destructive-refactor.html`.
- [x] Commit the destructive backend refactor and docs rewrite.
