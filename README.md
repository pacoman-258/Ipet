# Ipet Neo Aspect

Ipet Neo Aspect is a local desktop pet product with a Body-first backend. The app is organized around four current product modules:

- **Body**: desktop shell, pet window, frontend presentation, voice input/output, screen observation, and local device control.
- **Brain**: one-step LLM decision making for each user turn.
- **Human Ops**: approval, rejection, review notes, and execution safety for actions that may affect the local machine or user data.
- **Memory & Skills**: durable conversation memory, user preferences, learned procedures, and curated local skills.

For coding agents, start with `AGENTS.md`.

## What It Does

- Runs a local desktop pet through the Python desktop host and root HTML UI.
- Accepts text or push-to-talk input and can answer through chat bubbles and TTS.
- Lets the Brain choose one next step at a time instead of hiding a long autonomous chain.
- Routes risky actions through Human Ops before execution.
- Keeps Memory & Skills as Ipet-owned product data, not as opaque external state.
- Uses bounded screen observation when enabled, with local evidence governance.
- Produces an HTML report after every task round so destructive refactors remain auditable.

## Repository Layout

```text
Ipet/
|-- main.py                    # desktop host and Qt/WebEngine bridge
|-- backend/                   # current Python API surface during the Neo transition
|-- body/                      # target Body module for shell, observation, voice, presentation
|-- brain/                     # target Brain module for LLM turn decisions
|-- human_ops/                 # target approvals, action review, and execution records
|-- memory/                    # target durable memory and summaries
|-- skills/                    # built-in and learned local skills
|-- app/                       # target application composition layer
|-- frontend/                  # target frontend home after root UI migration
|-- index.html                 # transition-period pet UI entry, still loaded from root
|-- settings.html              # transition-period settings UI entry, still loaded from root
|-- settings.css
|-- settings.js
|-- docs/                      # architecture, workflow, ownership, and reports
|-- tests/                     # unit and regression tests
|-- scripts/                   # diagnostics and developer tools
|-- model/                     # tracked demo/reference pet assets plus ignored local assets
|-- pet_config.example.json    # safe example config
|-- README.zh-CN.md            # Chinese Markdown README
|-- README.html                # Chinese HTML README
```

The Neo model is being introduced while some implementation files still live under the previous Python layout. Root UI files stay where they are until the desktop host and backend loading paths move together.

## Local Run

Use the project `.venv` on Python 3.12 when available.

```powershell
uv run --no-sync python main.py
```

Create local configuration from the example when needed:

```powershell
Copy-Item pet_config.example.json pet_config.json
```

`pet_config.json` is local-only. Do not commit API keys, local absolute paths, screenshots, generated audio, or runtime state.

## Tests

Run the full regression suite with:

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```

Targeted slices are listed in `AGENTS.md`. For documentation-only work, at minimum run the requested documentation audit and inspect `git status --short`.

## HTML Report Rule

Every task round must create or update an HTML report under `docs/reports/`. The report should record:

- task goal
- subagent or worker split
- files changed
- achieved effect
- remaining work
- recommended next step

This keeps the Neo Aspect refactor readable for humans, not just for diffs. Tiny chores deserve tiny reports; destructive backend or workflow work needs a fuller one.

## Core Docs

- `docs/architecture.md`
- `docs/architecture_ZH.md`
- `docs/WORKFLOW.md`
- `docs/PROJECT_STRUCTURE.md`
- `docs/SUBAGENTS.md`
- `AGENTS.md`
