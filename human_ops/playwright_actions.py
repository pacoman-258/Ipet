from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence
from urllib.parse import urlparse

from .approvals import ReviewableProposal
from .chrome_profiles import (
    active_chrome_profiles,
    chrome_profile_snapshot_directory,
    chrome_remote_debugging_ready,
    prepare_chrome_profile_snapshot,
    resolve_chrome_profile,
)


_ELEMENT_REF_RE = re.compile(r"^e\d+$")
_SESSION_RE = re.compile(r"[^A-Za-z0-9_-]+")
_OPERATIONS = {
    "attach",
    "open",
    "snapshot",
    "click",
    "fill",
    "type",
    "press",
    "go_back",
    "go_forward",
    "reload",
    "tab_list",
    "tab_new",
    "tab_select",
    "tab_close",
}
_KEYS = {
    "Enter",
    "Tab",
    "Escape",
    "Backspace",
    "Delete",
    "Space",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Home",
    "End",
    "PageUp",
    "PageDown",
}
_COMMANDS = {
    "go_back": "go-back",
    "go_forward": "go-forward",
    "tab_list": "tab-list",
    "tab_new": "tab-new",
    "tab_select": "tab-select",
    "tab_close": "tab-close",
}

ProcessRunner = Callable[[Sequence[str], float], Awaitable[tuple[int, str, str]]]


def _http_url(value: Any, *, required: bool) -> str:
    url = str(value or "").strip()
    if not url and not required:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Playwright URL must be an absolute http/https URL.")
    return url


def _element_ref(value: Any) -> str:
    ref = str(value or "").strip()
    if not _ELEMENT_REF_RE.fullmatch(ref):
        raise ValueError("Playwright element ref must come from the latest snapshot, for example e12.")
    return ref


def _text(value: Any, *, name: str) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"Playwright {name} is required.")
    if len(text) > 20_000:
        raise ValueError(f"Playwright {name} is too long.")
    return text


def _tab_index(value: Any, *, required: bool) -> str:
    if (value is None or value == "") and not required:
        return ""
    try:
        index = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Playwright tab index must be a non-negative integer.") from exc
    if index < 0:
        raise ValueError("Playwright tab index must be a non-negative integer.")
    return str(index)


def playwright_command(arguments: dict[str, Any]) -> list[str]:
    profile = _profile_name(arguments)
    operation = str(arguments.get("operation") or "").strip().lower()
    if operation not in _OPERATIONS:
        raise ValueError(f"Unsupported Playwright operation: {operation or 'missing operation'}.")
    command = [_COMMANDS.get(operation, operation)]
    if operation == "attach":
        resolve_chrome_profile(profile)
        command.append("--cdp=chrome")
    elif operation == "open":
        command.extend(
            (
                _http_url(arguments.get("url"), required=True),
                "--headed",
                f"--profile={_profile_directory(profile)}",
            )
        )
    elif operation == "click":
        command.append(_element_ref(arguments.get("ref")))
    elif operation == "fill":
        command.extend((_element_ref(arguments.get("ref")), _text(arguments.get("text"), name="text")))
    elif operation == "type":
        command.append(_text(arguments.get("text"), name="text"))
    elif operation == "press":
        key = str(arguments.get("key") or "").strip()
        if key not in _KEYS and len(key) != 1:
            raise ValueError("Unsupported Playwright key.")
        command.append(key)
    elif operation == "tab_new":
        url = _http_url(arguments.get("url"), required=False)
        if url:
            command.append(url)
    elif operation == "tab_select":
        command.append(_tab_index(arguments.get("index"), required=True))
    elif operation == "tab_close":
        index = _tab_index(arguments.get("index"), required=False)
        if index:
            command.append(index)
    return command


def playwright_action_label(arguments: dict[str, Any]) -> str:
    operation = str(arguments.get("operation") or "").strip().lower()
    label = str(arguments.get("label") or "").strip()
    if label:
        return label
    if operation in {"open", "tab_new"}:
        return str(arguments.get("url") or "浏览器页面").strip() or "浏览器页面"
    if operation in {"click", "fill"}:
        return str(arguments.get("ref") or "页面元素").strip() or "页面元素"
    return operation or "浏览器操作"


def _profile_name(arguments: dict[str, Any]) -> str:
    profile = str(arguments.get("profile") or "").strip()
    if not profile:
        raise ValueError("Playwright profile must be explicitly selected by the user.")
    if len(profile) > 120 or any(ord(character) < 32 for character in profile):
        raise ValueError("Playwright profile name is invalid.")
    return profile


def _cli_prefix() -> list[str]:
    configured = str(os.environ.get("IPET_PLAYWRIGHT_CLI") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError("IPET_PLAYWRIGHT_CLI does not point to an executable file.")
        return [str(path)]
    installed = shutil.which("playwright-cli")
    if installed:
        return [installed]
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", "--package", "@playwright/cli", "playwright-cli"]
    raise RuntimeError("Playwright is unavailable because neither playwright-cli nor npx is installed.")


def _session_name(profile: str) -> str:
    configured = str(os.environ.get("IPET_PLAYWRIGHT_SESSION") or "ipet-profile-v4").strip() or "ipet-profile-v4"
    prefix = _SESSION_RE.sub("-", configured).strip("-") or "ipet-profile-v4"
    digest = hashlib.sha256(profile.casefold().encode("utf-8")).hexdigest()[:12]
    return f"{prefix[:48]}-{digest}"


def _profile_directory(profile: str) -> Path:
    return chrome_profile_snapshot_directory(profile)


async def _run_process(argv: Sequence[str], timeout_sec: float) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_sec)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError(f"Playwright action timed out after {timeout_sec:g} seconds.") from None
    return (
        int(process.returncode or 0),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def execute_playwright_action(
    proposal: ReviewableProposal,
    *,
    runner: ProcessRunner = _run_process,
    timeout_sec: float = 45.0,
) -> dict[str, Any]:
    if not proposal.approved:
        raise RuntimeError("Playwright action requires an approved Human Ops proposal.")
    if proposal.proposal_type != "act" or str(proposal.payload.get("action_type") or "").strip() != "playwright":
        raise RuntimeError("unsupported Playwright proposal")
    arguments = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    profile = _profile_name(arguments)
    operation = str(arguments.get("operation") or "").strip().lower()
    profile_reused = False
    reuse_open_chrome = False
    if operation == "open":
        resolved_profile = resolve_chrome_profile(profile)
        reuse_open_chrome = chrome_remote_debugging_ready(resolved_profile)
        if not reuse_open_chrome:
            _directory, _resolved_profile, profile_reused = prepare_chrome_profile_snapshot(profile)
    elif operation == "attach":
        resolved_profile = resolve_chrome_profile(profile)
        active_profiles = active_chrome_profiles()
        if not any(item.directory == resolved_profile.directory for item in active_profiles):
            raise RuntimeError(
                f'Chrome profile "{resolved_profile.display_name}" is not the active Chrome profile. '
                "Open that profile first, or use Playwright open to work from its private snapshot."
            )
        if len(active_profiles) != 1:
            names = ", ".join(item.display_name for item in active_profiles)
            raise RuntimeError(
                f"Multiple Chrome profiles are active ({names}), so Playwright cannot prove which one CDP will select. "
                "Keep only the chosen profile active, or use Playwright open with its private snapshot."
            )
        if not chrome_remote_debugging_ready(resolved_profile):
            raise RuntimeError(
                "Chrome remote debugging is not ready. In the selected Chrome profile, open "
                "chrome://inspect/#remote-debugging, enable remote debugging, then approve attach again."
            )
    session = _session_name(profile)
    commands = (
        [["attach", "--cdp=chrome"], ["goto", _http_url(arguments.get("url"), required=True)]]
        if operation == "open" and reuse_open_chrome
        else [playwright_command(arguments)]
    )
    outputs: list[str] = []
    warnings: list[str] = []
    for command in commands:
        argv = [*_cli_prefix(), "--session", session, *command]
        returncode, stdout, stderr = await runner(argv, timeout_sec)
        if returncode != 0:
            detail = (stderr or stdout or f"exit code {returncode}").strip()[:2_000]
            raise RuntimeError(f"Playwright {command[0]} failed: {detail}")
        if stdout.strip():
            outputs.append(stdout.strip())
        if stderr.strip():
            warnings.append(stderr.strip())
    return {
        "playwright_done": True,
        "operation": operation,
        "headed": operation in {"attach", "open"},
        "attached": operation == "attach" or reuse_open_chrome,
        "persistent": operation == "open" and not reuse_open_chrome,
        "profile": profile,
        "profile_source": "open_chrome" if operation == "attach" or reuse_open_chrome else "chrome_snapshot",
        "profile_reused": profile_reused or reuse_open_chrome,
        "session": session,
        "output": "\n\n".join(outputs)[:64_000],
        "warnings": "\n\n".join(warnings)[:4_000],
    }
