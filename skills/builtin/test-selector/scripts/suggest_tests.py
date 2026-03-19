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

if "skill" in text_blob:
    commands.append("python -m unittest tests.test_skills_manager tests.test_skills_runtime -v")
    reasons.append("Skills package or runtime logic changed.")
if "mcp" in text_blob or "tool" in text_blob:
    commands.append("python -m unittest tests.test_mcp_bridge_tools tests.test_tool_runtime -v")
    reasons.append("MCP or tool integration changed.")
if "chat" in text_blob or "agent" in text_blob or "backend/app.py" in text_blob:
    commands.append("python -m unittest tests.test_chat_segmented_flow tests.test_agent_graph_runtime -v")
    reasons.append("Chat runtime or agent orchestration changed.")
if "settings" in text_blob:
    commands.append("python -m unittest tests.test_settings_mcp_draft -v")
    reasons.append("Settings page or settings API changed.")
if "index.html" in text_blob or "frontend" in text_blob or "ui" in text_blob:
    commands.append("python -m unittest tests.test_chat_dual_output tests.test_react_trace_visibility -v")
    reasons.append("Chat UI or display protocol changed.")

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
