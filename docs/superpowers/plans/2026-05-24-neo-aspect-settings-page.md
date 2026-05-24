# Neo Aspect Settings Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the retained web settings page so it keeps the current desktop-window visual style but configures Body, Brain, Human Ops, Memory, Skills, and Diagnostics instead of agent runtimes.

**Architecture:** Keep `settings.html`, `settings.css`, and `settings.js` as the active entry files for now. Replace settings content and JavaScript behavior with Neo Aspect sections while preserving existing style classes and the existing `/api/settings/config` load/save path.

**Tech Stack:** HTML, CSS class reuse, vanilla JavaScript, Python `unittest` static contract tests.

---

## File Structure

- Create `tests/test_neo_aspect_settings_page.py`: static tests for settings page structure and forbidden legacy runtime content.
- Modify `settings.html`: replace content with Body, Brain, Human Ops, Memory, Skills, and Diagnostics sections.
- Modify `settings.js`: replace runtime/MCP/agent logic with Neo Aspect load/save/render logic.
- Modify `settings.css`: add only small styles needed for click-preview red dot and recipe/memory review UI.
- Modify `main.py`: make the Qt settings entry reuse a single settings window instead of opening duplicate pages.
- Modify legacy settings/UI tests that still asserted removed runtime controls, turning them into Neo Aspect absence/presence contracts.
- Modify `docs/superpowers/specs/2026-05-24-ipet-body-first-architecture-design.md`: note that the settings page has entered the Neo Aspect content model and is opened as a singleton Qt window.
- Add an HTML completion report for this task under `docs/reports/`.

## Task 1: Static Contract Tests

- [x] Write tests that require the new section ids and preserved visual shell classes.
- [x] Run tests and confirm they fail against the old settings page.

## Task 2: Replace Settings HTML

- [x] Rewrite `settings.html` with sections: `overview`, `body`, `brain`, `human-ops`, `memory`, `skills`, `diagnostics`.
- [x] Keep `desktop-menubar`, `desktop-stage`, `content-area`, `panel-card`, `desktop-window`, `desktop-icons`, and `desktop-dock`.
- [x] Remove visible Hermes, AstrBot, MCP, and runtime management copy.

## Task 3: Replace Settings JavaScript

- [x] Rewrite `settings.js` around Neo Aspect form fields.
- [x] Continue using `/api/settings/config` for load/save.
- [x] Preserve current config values where they map to the new model.
- [x] Add diagnostics rendering and click-preview red dot controls.
- [x] Remove `/api/runtime/status`, `/api/hermes/status`, `/api/mcp/*`, and `/api/skills` calls.

## Task 4: Small CSS Additions

- [x] Add red-dot preview styles.
- [x] Add compact review/proposal list styles if existing classes are not enough.

## Task 4.5: Qt Settings Entry Reuse

- [x] Add tests for opening settings once and refocusing the existing window.
- [x] Replace system-browser opening with a singleton Qt settings window.

## Task 4.6: Remove Old UI Contracts

- [x] Update old settings UI assertions so they no longer require removed Hermes, AstrBot, MCP, runtime, or file-picker management controls.
- [x] Keep backend API tests intact only where they still prove lower-level configuration behavior during migration.

## Task 4.7: Completion Report

- [x] Add an HTML report summarizing changes, achieved effects, remaining work, and recommended next steps.

## Task 5: Verification And Commit

- [x] Run `python -m unittest tests.test_neo_aspect_settings_page tests.test_neo_aspect_core tests.test_main_runtime_env tests.test_settings_mcp_draft tests.test_settings_file_allowlist tests.test_chat_modes_ui -v`.
- [x] Re-run `python -m unittest tests.test_neo_aspect_settings_page tests.test_chat_modes_ui tests.test_settings_file_allowlist -v` after the final red-dot default cleanup.
- [x] Run `python -m py_compile app/settings_schema.py main.py`.
- [x] Run `git diff --check`.
- [ ] Commit only this settings page slice and its tests/docs.
