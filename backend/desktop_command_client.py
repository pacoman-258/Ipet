from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from human_ops import ReviewableProposal


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DESKTOP_COMMAND_PATH = ROOT_DIR / ".pet_desktop_command.json"


def _coerce_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


async def send_desktop_command(
    command_type: str,
    payload: dict[str, Any] | None = None,
    *,
    command_path: str | Path = DEFAULT_DESKTOP_COMMAND_PATH,
    timeout_sec: float = 8.0,
) -> dict[str, Any]:
    nonce = uuid4().hex
    response_path = Path(tempfile.gettempdir()) / f"ipet-{command_type}-{nonce}.response.json"
    command_payload = dict(payload or {})
    command_payload["response_path"] = str(response_path)
    command = {
        "nonce": nonce,
        "type": str(command_type or "").strip(),
        "payload": command_payload,
        "timestamp_ns": time.time_ns(),
    }
    try:
        if response_path.exists():
            response_path.unlink()
    except Exception:
        pass
    command_file = Path(command_path)
    tmp_path = command_file.with_name(f"{command_file.name}.{nonce}.tmp")
    tmp_path.write_text(json.dumps(command, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(command_file)

    deadline = time.monotonic() + max(0.2, float(timeout_sec))
    while time.monotonic() < deadline:
        if response_path.exists():
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
            except Exception:
                await asyncio.sleep(0.05)
                continue
            try:
                response_path.unlink()
            except Exception:
                pass
            if str(response.get("nonce") or "") != nonce:
                await asyncio.sleep(0.05)
                continue
            result = response.get("result") if isinstance(response.get("result"), dict) else {}
            if response.get("ok") or response.get("status") == "success":
                return result
            detail = str(result.get("error") or response.get("status") or "desktop command failed")
            raise RuntimeError(detail)
        await asyncio.sleep(0.05)
    raise TimeoutError(f"desktop command timed out: {command_type}")


async def perform_human_ops_click(
    proposal: ReviewableProposal,
    *,
    send_command: Callable[..., Awaitable[dict[str, Any]]] = send_desktop_command,
) -> dict[str, Any]:
    if proposal.proposal_type != "act" or str(proposal.payload.get("action_type") or "") != "click":
        raise RuntimeError("unsupported human ops proposal")
    args = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    payload = {
        "x": _coerce_int(args.get("x")),
        "y": _coerce_int(args.get("y")),
        "label": str(args.get("label") or args.get("target") or "目标位置").strip() or "目标位置",
    }
    result = await send_command("human_ops_click", payload, timeout_sec=5)
    return {"clicked": True, **payload, **result}


async def perform_human_ops_action(
    proposal: ReviewableProposal,
    *,
    send_command: Callable[..., Awaitable[dict[str, Any]]] = send_desktop_command,
) -> dict[str, Any]:
    if proposal.proposal_type != "act":
        raise RuntimeError("unsupported human ops proposal")
    action_type = str(proposal.payload.get("action_type") or "").strip()
    args = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    if action_type == "click":
        return await perform_human_ops_click(proposal, send_command=send_command)
    if action_type == "type_text":
        text = str(args.get("text") or "")
        label = str(args.get("label") or args.get("target") or "输入位置").strip() or "输入位置"
        result = await send_command("human_ops_type_text", {"text": text, "label": label}, timeout_sec=5)
        return {"typed": True, "text": text, "label": label, **result}
    if action_type == "launch_app":
        app_name = str(args.get("app") or args.get("name") or args.get("label") or "").strip()
        if not app_name:
            raise RuntimeError("Human Ops app launch requires an application name.")
        result = await send_command("human_ops_launch_app", {"app": app_name, "label": app_name}, timeout_sec=8)
        return {"launched": True, "app": app_name, **result}
    if action_type == "key_press":
        key = str(args.get("key") or "enter").strip().lower() or "enter"
        label = str(args.get("label") or args.get("target") or "当前焦点").strip() or "当前焦点"
        result = await send_command("human_ops_key_press", {"key": key, "label": label}, timeout_sec=5)
        return {"pressed": True, "key": key, "label": label, **result}
    raise RuntimeError(f"unsupported human ops action: {action_type}")
