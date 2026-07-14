# Optional Subagent Roles

Single-agent work is the default. Use these role prompts only when delegation is explicitly requested; load only the assigned role.

## Roles

- `body`: local observation, voice I/O, pet presentation, and device-facing commands.
- `brain`: LLM provider calls, one-step decision schema, prompts, and chat SSE contracts.
- `human-ops`: approval review, rejection flow, action execution records, and safety copy.
- `memory-skills`: local memory, summaries, user preferences, reviewed skill recipes, and learned procedures.
- `settings-console`: web settings page and settings API contracts.
- `desktop-shell`: `main.py`, Qt/WebEngine bridge, tray/menu behavior, and host process lifecycle.
- `qa-reports`: regression planning, contract checks, documentation audits, and requested durable reports.

## Routing

- Pet shell, expressions, bubbles, ASR/TTS, or observation: lead `body`.
- Brain model selection, structured decisions, or chat streaming: lead `brain`.
- Click/type/hotkey/open-app proposals and approvals: lead `human-ops`.
- Durable memory or skill recipe persistence: lead `memory-skills`.
- Settings UI or settings payloads: lead `settings-console`.
- Window behavior, native menus, app startup, or Qt bridge: lead `desktop-shell`.
- Repo cleanup, test matrix, docs, and reports: lead `qa-reports`.

## Cross-Review

- `main.py` and `index.html` bridge changes: lead `desktop-shell`, review `body`.
- Settings UI and backend settings routes: one side leads, the other reviews, plus `qa-reports`.
- Brain decision event changes: lead `brain`, review `body`, `human-ops`, and `qa-reports`.
- Approval or execution changes: lead `human-ops`, review `brain` and `qa-reports`.
- Memory or skill persistence changes: lead `memory-skills`, review `brain` and `qa-reports`.

## Test Slices

- Body: `tests.test_active_vision tests.test_vision_analyzer tests.test_vision_service tests.test_vision_state tests.test_asr_api tests.test_asr_service tests.test_asr_server_api`
- Brain: `tests.test_brain_llm_providers tests.test_brain_structured_replies tests.test_neo_backend_contract`
- Human Ops: `tests.test_neo_backend_contract tests.test_chat_modes_ui tests.test_neo_aspect_core`
- Memory & Skills: `tests.test_chat_topics tests.test_chat_topics_api tests.test_ipet_memory_store tests.test_neo_aspect_core`
- Settings: `tests.test_neo_aspect_settings_page tests.test_chat_modes_ui tests.test_neo_backend_contract`
- Desktop Shell: `tests.test_main_runtime_env`
- QA Reports: the smallest affected test or contract slice; check a durable report only when the task requested one.
