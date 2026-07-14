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

## Repository Layout

```text
Ipet/
|-- main.py                    # desktop composition, Qt lifecycle, compatibility wrappers
|-- backend/                   # API composition, routes, helpers, and adapters
|-- body/                      # Body shell, observation, voice, and presentation behavior
|-- brain/                     # Brain LLM turn decisions
|-- human_ops/                 # approvals, action review, and execution records
|-- memory/                    # durable memory and summaries
|-- skills/                    # built-in and learned local skills
|-- app/                       # desktop composition and host support modules
|-- frontend/                  # pet UI controllers and behavior modules
|-- index.html                 # root pet UI document and script loader
|-- settings.html              # root settings UI entry
|-- settings.css
|-- settings.js
|-- docs/                      # architecture, workflow, ownership, and optional reports
|-- tests/                     # unit and regression tests
|-- scripts/                   # diagnostics and developer tools
|-- model/                     # tracked demo/reference pet assets plus ignored local assets
|-- pet_config.example.json    # safe example config
|-- README.zh-CN.md            # Chinese Markdown README
|-- README.html                # Chinese HTML README
```

The root entry paths remain stable for desktop compatibility. `main.py`, `backend/app.py`, and `index.html` stay focused on composition and loading; new domain behavior belongs in the corresponding module directory.

## Local Run

Use the project `.venv` on Python 3.12 when available.

```powershell
uv run --no-sync python main.py
```

Create local configuration from the example when needed:

```powershell
Copy-Item pet_config.example.json pet_config.json
```

`pet_config.json` is local-only. Do not commit API keys, local absolute paths, screenshots, generated audio, or local app state.

## Tests

Run the full regression suite with:

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```

Coding agents should select the smallest matching test slice. Documentation-only work normally needs `git diff --check` and focused contract searches rather than the Python suite.

## Core Docs

- `docs/architecture.md`
- `docs/architecture_ZH.md`
- `docs/WORKFLOW.md`
- `docs/PROJECT_STRUCTURE.md`
- `docs/SUBAGENTS.md`
- `AGENTS.md`
