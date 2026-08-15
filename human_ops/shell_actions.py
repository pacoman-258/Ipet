from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .approvals import ReviewableProposal


SHELL_ACTION = "shell"
MODEL_RISK_LEVELS = frozenset({"safe", "dangerous", "uncertain"})
DEFAULT_TIMEOUT_SEC = 30.0
MAX_TIMEOUT_SEC = 300.0
MAX_OUTPUT_BYTES = 200_000
_CATALOG_PATH = Path(__file__).with_name("dangerous_commands.json")


@dataclass(frozen=True)
class ShellRiskAssessment:
    dangerous: bool
    model_risk: str
    reasons: tuple[str, ...]
    matched_rules: tuple[str, ...]


def _catalog_rules() -> tuple[dict[str, str], ...]:
    try:
        payload = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Dangerous command catalog is unavailable: {exc}") from exc
    rules = payload.get("rules") if isinstance(payload, dict) else None
    if not isinstance(rules, list) or not rules:
        raise RuntimeError("Dangerous command catalog contains no rules.")
    normalized: list[dict[str, str]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_id = str(rule.get("id") or "").strip()
        pattern = str(rule.get("pattern") or "").strip()
        reason = str(rule.get("reason") or rule_id).strip()
        if rule_id and pattern:
            normalized.append({"id": rule_id, "pattern": pattern, "reason": reason})
    if not normalized:
        raise RuntimeError("Dangerous command catalog contains no valid rules.")
    return tuple(normalized)


def validate_shell_action(arguments: dict[str, Any]) -> tuple[bool, str]:
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return False, "shell missing command"
    if "\x00" in command:
        return False, "shell command contains a NUL byte"
    model_risk = str(arguments.get("model_risk") or "").strip().lower()
    if model_risk not in MODEL_RISK_LEVELS:
        return False, "shell model_risk must be safe, dangerous, or uncertain"
    reason = arguments.get("risk_reason")
    if not isinstance(reason, str) or not reason.strip():
        return False, "shell missing risk_reason"
    cwd = arguments.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
        return False, "shell cwd must be a non-empty string"
    timeout = arguments.get("timeout_sec", DEFAULT_TIMEOUT_SEC)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return False, "shell timeout_sec must be a number"
    if float(timeout) <= 0 or float(timeout) > MAX_TIMEOUT_SEC:
        return False, f"shell timeout_sec must be between 0 and {int(MAX_TIMEOUT_SEC)}"
    return True, ""


_KNOWN_SAFE_BINARIES = frozenset({
    "pwd",
    "ls",
    "dir",
    "echo",
    "printf",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "rg",
    "which",
    "where",
    "type",
    "date",
    "uptime",
    "whoami",
    "id",
    "uname",
    "file",
    "stat",
    "tree",
})

_GIT_SAFE_READONLY_SUBCMDS = frozenset({
    "status",
    "log",
    "diff",
    "show",
    "rev-parse",
    "describe",
    "version",
})

_SENSITIVE_OPERAND_PATTERN = re.compile(
    r"(?:~|\$HOME|%USERPROFILE%)?(?:[/\\]|\b)(?:\.ssh|\.aws|\.gnupg|\.docker|\.kube|\.netrc|\.npmrc|\.pypirc|\.config[/\\]gcloud|\.env(?:\.[a-zA-Z0-9_-]+)?|id_[a-zA-Z0-9_-]+|credentials|secrets|shadow|sudoers|master\.passwd|passwd|keychain|keystore)(?:[/\\]|\b|$)",
    re.IGNORECASE,
)


def is_known_safe_shell_command(command: str) -> tuple[bool, str]:
    import shlex

    text = str(command or "").strip()
    if not text:
        return False, "empty command"
    if "$(" in text or "`" in text or "${" in text:
        return False, "dynamic command substitution"
    # Split on pipe and chaining operators
    segments = re.split(r"(?:&&|\|\||;|\|)", text)
    for raw_segment in segments:
        segment = raw_segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment, posix=(os.name != "nt"))
        except ValueError as exc:
            return False, f"invalid syntax: {exc}"
        if not tokens:
            continue
        # Check all operand tokens for sensitive paths or credential files
        for token in tokens[1:]:
            if _SENSITIVE_OPERAND_PATTERN.search(token):
                return False, f"sensitive path operand requires approval: {token}"
        raw_cmd = tokens[0].replace("/", "\\").split("\\")[-1].lower()
        if raw_cmd.endswith((".exe", ".cmd", ".bat")):
            raw_cmd = raw_cmd.rsplit(".", 1)[0]
        if raw_cmd in _KNOWN_SAFE_BINARIES:
            continue
        if raw_cmd == "git":
            if len(tokens) == 1:
                continue
            subcmd = tokens[1].lower()
            if subcmd in _GIT_SAFE_READONLY_SUBCMDS:
                continue
            if subcmd == "branch":
                forbidden = {"-d", "-D", "-m", "-M", "--delete", "--move"}
                if not any(token in forbidden for token in tokens[2:]):
                    continue
            elif subcmd == "tag":
                forbidden = {"-d", "--delete", "-a", "-s", "-u"}
                if not any(token in forbidden for token in tokens[2:]):
                    continue
            elif subcmd == "remote":
                forbidden = {"add", "rename", "remove", "rm", "set-url", "set-head"}
                if not any(token in forbidden for token in tokens[2:]):
                    continue
            elif subcmd == "config":
                allowed = {"--get", "-l", "--list", "--get-all", "--get-regexp"}
                if any(token in allowed for token in tokens[2:]):
                    continue
            return False, f"git subcommand requires approval: {subcmd}"
        return False, f"unknown command: {raw_cmd}"
    return True, ""


def assess_shell_risk(arguments: dict[str, Any]) -> ShellRiskAssessment:
    valid, reason = validate_shell_action(arguments)
    if not valid:
        return ShellRiskAssessment(True, "uncertain", (reason,), ("invalid_request",))
    command = str(arguments["command"])
    model_risk = str(arguments["model_risk"]).strip().lower()
    reasons: list[str] = []
    matched_rules: list[str] = []
    if model_risk != "safe":
        reasons.append(str(arguments.get("risk_reason") or "Model marked the command as risky."))
        matched_rules.append(f"model_{model_risk}")
    scan_text = re.sub(r"[$()`{}]", " ", command)
    for rule in _catalog_rules():
        if re.search(rule["pattern"], command, flags=re.IGNORECASE | re.MULTILINE) or re.search(
            rule["pattern"],
            scan_text,
            flags=re.IGNORECASE | re.MULTILINE,
        ):
            matched_rules.append(rule["id"])
            reasons.append(rule["reason"])
    is_safe, safe_reason = is_known_safe_shell_command(command)
    if not is_safe:
        matched_rules.append("unknown_command")
        reasons.append(f"未知或非只读 Shell 命令需要审批：{safe_reason}")
    return ShellRiskAssessment(bool(matched_rules), model_risk, tuple(dict.fromkeys(reasons)), tuple(dict.fromkeys(matched_rules)))


def shell_action_label(arguments: dict[str, Any]) -> str:
    command = str(arguments.get("command") or "").strip()
    cwd = str(arguments.get("cwd") or ".").strip() or "."
    return f"Shell：{command} · cwd={cwd}"


def _resolve_cwd(value: object, *, default_cwd: Path) -> Path:
    text = str(value or "").strip()
    candidate = Path(text).expanduser() if text else default_cwd
    if not candidate.is_absolute():
        candidate = default_cwd / candidate
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"Shell working directory is not a directory: {resolved}")
    return resolved


def _shell_argv(command: str) -> list[str]:
    if os.name == "nt":
        shell = os.environ.get("COMSPEC") or "powershell.exe"
        if Path(shell).name.casefold() in {"cmd", "cmd.exe"}:
            return [shell, "/D", "/S", "/C", command]
        return [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    shell = os.environ.get("SHELL") or "/bin/sh"
    return [shell, "-lc", command]


async def _read_bounded(
    stream: asyncio.StreamReader | None,
    *,
    limit: int,
) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False
    chunks: list[bytes] = []
    retained = 0
    truncated = False
    while True:
        chunk = await stream.read(16_384)
        if not chunk:
            break
        remaining = max(0, limit - retained)
        if remaining:
            kept = chunk[:remaining]
            chunks.append(kept)
            retained += len(kept)
        if len(chunk) > remaining:
            truncated = True
    return b"".join(chunks), truncated


async def execute_shell_action(
    proposal: ReviewableProposal,
    *,
    default_cwd: Path,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    if proposal.proposal_type != "act" or str(proposal.payload.get("action_type") or "").strip() != SHELL_ACTION:
        raise RuntimeError("unsupported shell proposal")
    if not proposal.approved:
        raise PermissionError("Shell action must be authorized before execution.")
    arguments = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    valid, reason = validate_shell_action(arguments)
    if not valid:
        raise ValueError(reason)
    command = str(arguments["command"])
    current_risk = assess_shell_risk(arguments)
    if not proposal.requires_review and current_risk.dangerous:
        raise PermissionError("Shell risk changed after automatic authorization; explicit review is required.")
    cwd = _resolve_cwd(arguments.get("cwd"), default_cwd=default_cwd)
    timeout_sec = float(arguments.get("timeout_sec", DEFAULT_TIMEOUT_SEC))
    process = await asyncio.create_subprocess_exec(
        *_shell_argv(command),
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    limit = max(1, int(max_output_bytes))
    stdout_task = asyncio.create_task(_read_bounded(process.stdout, limit=limit))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr, limit=limit))
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        await asyncio.gather(stdout_task, stderr_task)
        raise TimeoutError(f"Shell command exceeded {timeout_sec:g} seconds.")
    (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.gather(
        stdout_task,
        stderr_task,
    )
    exit_code = int(process.returncode or 0)
    return {
        "shell_done": exit_code == 0,
        "completed": True,
        "ok": exit_code == 0,
        "command": command,
        "cwd": str(cwd),
        "exit_code": exit_code,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }
