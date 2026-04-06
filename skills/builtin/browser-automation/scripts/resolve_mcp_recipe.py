from __future__ import annotations

import json
import sys
from typing import Any


SUPPORTED_RECIPES = {
    "open_page",
    "open_and_type",
    "click_followup",
    "extract_or_verify",
}

FAMILY_PREFIXES = ("playwright_mcp", "playwright")
READ_TOOL_SUFFIXES = (
    "browser_snapshot",
    "browser_read_text",
    "browser_read",
    "browser_extract",
    "browser_extract_text",
    "browser_evaluate",
)


def _error(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))


def _load_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    return payload


def _normalize_tool_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("visible_tool_names must be a list")
    names: list[str] = []
    seen: set[str] = set()
    for item in value:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _require_string(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required for this recipe")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


def _family_tools(tool_names: list[str], family: str) -> set[str]:
    prefix = f"{family}."
    return {name for name in tool_names if name.startswith(prefix)}


def _pick_family(tool_names: list[str], recipe: str) -> str:
    family_tools = {family: _family_tools(tool_names, family) for family in FAMILY_PREFIXES}
    supported: list[str] = []
    for family, tools in family_tools.items():
        if not tools:
            continue
        if recipe == "open_page" and f"{family}.browser_navigate" in tools:
            supported.append(family)
        elif recipe == "open_and_type" and {
            f"{family}.browser_navigate",
            f"{family}.browser_type",
        }.issubset(tools):
            supported.append(family)
        elif recipe == "click_followup" and f"{family}.browser_click" in tools:
            supported.append(family)
        elif recipe == "extract_or_verify":
            supported.append(family)
    if supported:
        return supported[0]
    if family_tools["playwright_mcp"]:
        return "playwright_mcp"
    if family_tools["playwright"]:
        return "playwright"
    raise ValueError("no visible playwright or playwright_mcp browser tools were provided")


def _pick_read_tool(tool_names: list[str], family: str) -> str:
    available = _family_tools(tool_names, family)
    for suffix in READ_TOOL_SUFFIXES:
        candidate = f"{family}.{suffix}"
        if candidate in available:
            return candidate
    return ""


def _build_recipe(payload: dict[str, Any], tool_names: list[str]) -> dict[str, Any]:
    recipe = str(payload.get("recipe") or "").strip()
    if recipe not in SUPPORTED_RECIPES:
        raise ValueError("recipe must be one of open_page, open_and_type, click_followup, extract_or_verify")

    family = _pick_family(tool_names, recipe)
    tool_calls: list[dict[str, Any]] = []
    notes = [f"Use the {family} tool family for this step."]

    if recipe == "open_page":
        url = _require_string(payload, "url")
        tool_calls.append({"name": f"{family}.browser_navigate", "arguments": {"url": url}})
        notes.append("Navigate first, then verify a stable page anchor before continuing.")
    elif recipe == "open_and_type":
        url = _require_string(payload, "url")
        text = _require_string(payload, "text")
        selector = _optional_string(payload, "selector")
        type_args: dict[str, Any] = {"text": text}
        if selector:
            type_args["selector"] = selector
        tool_calls.extend(
            [
                {"name": f"{family}.browser_navigate", "arguments": {"url": url}},
                {"name": f"{family}.browser_type", "arguments": type_args},
            ]
        )
        notes.append("Keep navigation and typing in one family-scoped batch when possible.")
    elif recipe == "click_followup":
        selector = _require_string(payload, "selector")
        tool_calls.append({"name": f"{family}.browser_click", "arguments": {"selector": selector}})
        notes.append("Verify the page state after the click instead of chaining blind follow-up clicks.")
    else:
        selector = _require_string(payload, "selector")
        read_tool = _pick_read_tool(tool_names, family)
        if read_tool:
            tool_calls.append({"name": read_tool, "arguments": {"selector": selector}})
            notes.append("Use the visible read or snapshot tool for targeted verification.")
        else:
            notes.append(
                "No dedicated read tool is visible, so verify with the latest browser state and summarize only the needed anchor."
            )

    return {
        "family": family,
        "tool_calls": tool_calls,
        "notes": notes,
    }


def main() -> None:
    try:
        payload = _load_payload()
        tool_names = _normalize_tool_names(payload.get("visible_tool_names"))
        result = _build_recipe(payload, tool_names)
    except Exception as exc:
        _error(str(exc))
        return
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
