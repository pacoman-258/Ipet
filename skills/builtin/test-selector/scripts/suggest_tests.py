import json
import sys


def _unique(items):
    out = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


payload = json.load(sys.stdin)
changed_files = [str(item or "") for item in payload.get("changed_files") or []]
areas = [str(item or "").lower() for item in payload.get("areas") or []]
commands = []
reasons = []

text_blob = " ".join(changed_files + areas).lower()

if "skill" in text_blob or "memory" in text_blob:
    commands.append("python -m unittest tests.test_chat_topics tests.test_chat_topics_api tests.test_ipet_memory_store tests.test_neo_aspect_core -v")
    reasons.append("Memory & Skills behavior changed.")
if "human_ops" in text_blob or "approval" in text_blob or "click" in text_blob:
    commands.append("python -m unittest tests.test_neo_backend_contract tests.test_chat_modes_ui tests.test_neo_aspect_core -v")
    reasons.append("Human Ops review or execution flow changed.")
if "brain" in text_blob or "chat" in text_blob or "backend/app.py" in text_blob:
    commands.append("python -m unittest tests.test_brain_llm_providers tests.test_brain_structured_replies tests.test_neo_backend_contract -v")
    reasons.append("Brain or chat stream contract changed.")
if "settings" in text_blob:
    commands.append("python -m unittest tests.test_neo_aspect_settings_page tests.test_chat_modes_ui tests.test_neo_backend_contract -v")
    reasons.append("Settings page or settings API changed.")
if "index.html" in text_blob or "frontend" in text_blob or "ui" in text_blob:
    commands.append("python -m unittest tests.test_chat_modes_ui -v")
    reasons.append("Root UI or display contract changed.")

commands = _unique(commands)
if not commands:
    commands = ["python -m unittest discover -s tests -p \"test*.py\" -v"]
    reasons = ["No narrow match found, default to the full suite."]
else:
    commands.append("python -m unittest discover -s tests -p \"test*.py\" -v")
    reasons.append("Run the full suite before finalizing if the focused subset passes.")

print(
    json.dumps(
        {
            "ok": True,
            "result": {
                "commands": commands,
                "reasons": reasons,
            },
        },
        ensure_ascii=False,
    )
)
