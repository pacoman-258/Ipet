# Agent Development Guide

## Security First

- Any potentially risky command must be explained before execution.
- The explanation must include:
  - why the command is needed
  - what it may change or affect
  - whether it touches files, processes, configuration, network access, or destructive operations
- Commands with meaningful risk must not be executed silently.
- Risky actions must be handed to the user for approval first.
- Examples that require explicit approval include:
  - deleting or overwriting files
  - installing, updating, or removing dependencies
  - networked downloads, git push, or external service calls
  - process termination, system-level changes, or commands that may alter local environment state
- If a safer read-only or lower-impact path exists, prefer that path first.

This file is the shortest safe entrypoint for coding agents working in this repository.

## Core Development Principles / 开发核心原则

The complete rationale lives in `开发核心原则.txt`. Every implementation and plan must follow these enforceable rules:

1. **工作过程透明化**：提供类似 Codex 的流式、可折叠工作日志，展示目标、阶段、Brain -> Observe 请求、工具活动、结构化证据、审批、验证和失败；展示经过整理的工作记录，不展示原始思维链或敏感数据，且不得把计划和推测写成事实。
2. **人类思路、机器增强**：用人类容易理解的阶段、surface 和 affordance 规划行动，用操作系统状态、Accessibility、DOM、窗口、焦点和语义接口增强定位与验证；单一局部信号不得冒充完整结果。
3. **探索与验证分离**：探索未知 GUI 时，在屏幕观察权限已开启的前提下鼓励主动截图；验证优先使用动作/语义返回值、平台原生状态和结构化界面信息，仍不足时先询问用户“是否已经达成 XX 效果”，截图验证只作为最后手段。
4. **结果验证闭环**：动作发出不等于目标达成。每个状态更改动作必须声明预期结果、验证方法和失败条件；未验证前不得推进依赖步骤。事实证据优先于截图推断，截图推断优先于目标提示。
5. **批准流程严谨**：凡是会更改文件、进程、配置、外部服务、应用状态、焦点、输入、用户数据或持久记忆的操作，执行前都必须审批；目标、坐标、焦点、参数或风险变化后旧批准失效。动作前批准与动作后结果确认不得混淆。
6. **用户中止权最高优先**：任务运行、搜索、观察、等待审批和连续操作期间必须提供停止按键。停止由本地执行层处理，不等待 Brain 或网络；停止后禁止新副作用、取消可取消任务、清空未执行动作、使待审批提案失效，并说明已完成、未执行和不确定结果。停止不自动回滚既有副作用。
7. **工具选择最短可靠**：即时公开信息优先使用模型内置联网搜索，网页交互使用浏览器，桌面操作使用 Human Ops，系统验证使用平台原生只读接口。降级路径必须显式，优先步骤少、可验证、隐私影响小的方案。
8. **失败显式且重试有界**：预期与事实矛盾时立即失败关闭，清除失效坐标、焦点和界面假设；重试必须改变证据或路径，达到预算后停止并展示事实、未知项和建议。
9. **平台实现原生化**：统一意图、安全合同和结果语义，底层操作与验证使用各平台正式能力；平台适配独立测试，不把未实现能力伪装成通用支持。
10. **结构规范但不过度拆分**：按产品职责组织代码，入口文件保持 composition-only，共享规则只实现一次；既不堆积巨型文件，也不新增没有独立职责的实体。
11. **开发过程谦虚**：先检查现有代码、测试和官方能力；安全、隐私、架构或未知 API 问题再研究 Codex、Claude Code、Hermes 等同行，学习其约束和失败处理，不只模仿外观。优先复用、标准库、平台原生能力和最小实现。
12. **测试贴近真实环境**：关键能力覆盖单元、矛盾证据、平台集成和小型端到端烟雾测试；模拟事件只能证明协议，不能单独证明真实搜索、模型或桌面操作成功。

## Quick Start

- Product model: Ipet Neo Aspect has four current modules: Body, Brain, Human Ops, and Memory & Skills.
- Current implementation shape: `main.py`, `backend/app.py`, and `index.html` are compatibility entrypoints for desktop composition, API composition, and root UI loading. Domain behavior lives in `app/`, `body/`, backend route/helper/adapter modules, and `frontend/` controllers.
- Module homes: `body/`, `brain/`, `human_ops/`, `memory/`, `skills/`, `app/`, `frontend/`, `backend/`, `docs/`, and `tests/`.
- Primary stack: Python, Qt WebEngine, FastAPI, local HTML/CSS/JS, LLM calls, speech I/O, local screen observation, and Live2D-style assets.
- The currently verified dev runtime is the project `.venv` on Python 3.12.
- Preferred local start command:

```powershell
uv run --no-sync python main.py
```

- Default test command:

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```

- Every completed task round must generate an HTML report in `docs/reports/`.
- Do not start by scanning the whole repo. Read the minimum docs below, then jump to task-specific files.
- Qt binding order is fixed: prefer `PySide6`, fall back to `PyQt6`.

## Read Order

Read in this order unless the task is extremely narrow:

1. `README.md`
2. `docs/PROJECT_STRUCTURE.md`
3. `docs/SUBAGENTS.md`
4. `docs/WORKFLOW.md` when behavior, approvals, memory, skills, or observation changes
5. Only the 1-2 files directly related to the task

Useful stable facts:

- Body owns the desktop shell, pet presentation, voice I/O, local observation, and device-facing commands.
- Brain owns one-step LLM decision making for a user turn.
- Human Ops owns approval prompts, rejection handling, execution records, and safety review.
- Memory & Skills owns local memory, summaries, user preferences, skill definitions, and learned procedures.
- Root UI entry files stay at the repository root because the desktop host and backend load those paths. Keep them as loading and document surfaces; put interactive behavior in `frontend/`.
- Tests live under `tests/`.
- Debug helpers live under `scripts/debug/`.
- Fixed role prompts live under `docs/subagents/`.
- Root scratch files, generated output, local app state, caches, and personal configs should be ignored or removed instead of documented as project structure.

## Hot Files And Ownership

These are high-conflict, composition-only files. Only one implementation owner should lead changes to each in a task:

- `main.py`
- `index.html`
- `backend/app.py`

Current ownership map:

- `desktop-shell`: `main.py` composition/bootstrap, process lifecycle, Qt/WebEngine bridge composition, tray/menu behavior, compatibility wrappers
- `body`: Body module code, observation behavior, voice I/O, presentation commands, Body tests
- `brain`: Brain decision code, prompt contracts, model call boundaries, Brain tests
- `human-ops`: approvals, action review, execution ledgers, user confirmation flows, safety tests
- `memory-skills`: memory stores, summaries, skill discovery, learned procedures, skill tests
- `settings-console`: `settings.html`, `settings.css`, `settings.js`, settings API contracts
- `qa-reports`: regression planning, contract checks, targeted tests, HTML reports

Cross-boundary rules:

- `main.py`, `backend/app.py`, and `index.html` must remain composition-only. New domain behavior belongs in the owning `app/`, `body/`, `backend/`, or `frontend/` module; leave only registration, dependency wiring, loading, or a compatibility delegate in the entrypoint.
- `main.py` <-> `index.html`: lead `desktop-shell`, review by `body`.
- `settings.js` <-> backend settings APIs: one side leads, the other reviews.
- Brain decision event changes: lead `brain`, review by `body`, `human-ops`, and `qa-reports`.
- Approval or action execution changes: lead `human-ops`, review by `brain` and `qa-reports`.
- Memory or skill persistence changes: lead `memory-skills`, review by `brain` and `qa-reports`.
- Documentation-only cleanup: lead `qa-reports`, with implementation owners consulted only when behavior or ownership boundaries change.

## Task Routing Cheatsheet

Use this map before opening large files:

| Task type | Read first | Minimum follow-up |
| --- | --- | --- |
| Desktop host, tray, Qt bridge, window behavior | matching `app/desktop_*` module | `main.py` only for composition, lifecycle, or compatibility wiring |
| Pet UI, chat window, display state, TTS playback UX | matching `frontend/` controller | `index.html` only for document structure or script loading; related API route only if its contract changed |
| Backend API route, dependency, or compatibility export | matching `backend/*_routes.py`, helper, or adapter module | `backend/app.py` only for middleware, dependency wiring, router registration, or compatibility export installation |
| Body observation, screenshots, ASR/TTS, local command bridge | `docs/WORKFLOW.md` | `body/` or current matching `backend/vision*.py`, `backend/asr*.py` slice |
| Brain turn decision, prompt contract, model call boundary | `docs/architecture.md` | `brain/` or current matching backend slice |
| Human Ops approval and execution safety | `docs/WORKFLOW.md` | `human_ops/` or current approval/action route slice |
| Memory, summaries, topic continuity | `memory/` | current matching persistence slice and UI contract |
| Skills import, skill execution, learned procedures | `skills/` | `memory/` and related tests |
| Settings page | `settings.js` | `settings.html`, `settings.css`, relevant settings route |
| Repository organization, docs, cleanup | `docs/PROJECT_STRUCTURE.md` | `README.md`, `README.zh-CN.md`, `docs/WORKFLOW.md`, `.gitignore` |

## Token-Saving Rules

- Prefer `rg` or `rg --files` to locate symbols, routes, tests, and feature names before opening files.
- Inspect only the smallest useful slice: use line ranges, focused snippets, diffs, or summaries instead of full-file reads.
- Do not read all of `index.html` or `main.py` unless the task truly lives there. Search first, then open the relevant block.
- Do not open every test file. Pick the smallest matching regression slice from the matrix below.
- Read model, schema, or request types before implementations when tracing behavior.
- When a task touches one subsystem, stay inside that subsystem until an interface boundary forces expansion.
- For logs, JSON, CSV, HTML reports, and other large artifacts, create or inspect a compact summary first, then read only necessary raw slices.
- Use `python scripts/context_summary.py` before exploring broad project context. Prefer targeted modes: `--repo`, `--reports`, `--data`, or `--logs`.
- When investigating `docs/reports/`, local `data/`, or log directories, run the matching `scripts/context_summary.py` mode first and only open raw files that the summary makes relevant.
- Default noisy paths to skip: `.venv/`, `.uv-cache/`, `__pycache__/`, `.service-logs/`, `.app-logs/`, `.cache/`, generated outputs, local app state under ignored `data/` paths, and large historical `docs/reports/` files unless the task is about them.
- Cap unknown command output. Prefer focused commands such as `git status --short`, `git diff --name-only`, `rg PATTERN PATH`, and `sed -n 'START,ENDp' FILE`.
- Use `git status --short` before cleanup. Treat unrelated modified/untracked files as user work and never revert them.
- Deleting tracked files, ignored local app state, caches, or generated outputs is destructive. Explain why, what it affects, and ask for approval unless the user explicitly named the exact deletion target.

## HTML Report Rule

Every round that changes files must create or update an HTML report under `docs/reports/`. The report must include:

- goal
- worker or subagent split
- files changed
- achieved effect
- remaining work
- recommended next step

Do not mark a task complete until the report exists and the relevant verification command has been run.

## Regression Matrix

Run the smallest useful set first.

### Body

```powershell
python -m unittest tests.test_active_vision tests.test_vision_analyzer tests.test_vision_service tests.test_vision_state tests.test_asr_api tests.test_asr_service tests.test_asr_server_api -v
```

### Brain

```powershell
python -m unittest tests.test_brain_llm_providers tests.test_brain_structured_replies tests.test_neo_backend_contract -v
```

### Human Ops

```powershell
python -m unittest tests.test_neo_backend_contract tests.test_chat_modes_ui tests.test_neo_aspect_core -v
```

### Memory & Skills

```powershell
python -m unittest tests.test_chat_topics tests.test_chat_topics_api tests.test_ipet_memory_store tests.test_neo_aspect_core -v
```

### Settings And UI Contracts

```powershell
python -m unittest tests.test_neo_aspect_settings_page tests.test_chat_modes_ui tests.test_neo_backend_contract -v
```

### Documentation Audit

Run the Neo forbidden-term audit requested by the task owner against README, AGENTS, workflow, structure, ownership, and architecture docs. Expected result for Neo Aspect docs: no output.

### Full Regression

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```
