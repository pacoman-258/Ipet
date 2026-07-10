from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from human_ops import ReviewableProposal

from . import desktop_command_client as _desktop_command_client_helpers


@dataclass(frozen=True)
class AppActionAdapterDependencies:
    command_path: Path
    send_desktop_command: Callable[..., Awaitable[dict[str, Any]]]
    perform_human_ops_click: Callable[[ReviewableProposal], Awaitable[dict[str, Any]]] | None = None
    perform_human_ops_action: Callable[..., Awaitable[dict[str, Any]]] | None = None
    command_timeout_sec: float = 8.0


async def send_desktop_command(
    command_type: str,
    payload: dict[str, Any] | None = None,
    *,
    deps: AppActionAdapterDependencies,
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    return await deps.send_desktop_command(
        command_type,
        payload,
        command_path=deps.command_path,
        timeout_sec=deps.command_timeout_sec if timeout_sec is None else timeout_sec,
    )


async def perform_human_ops_click(
    proposal: ReviewableProposal,
    *,
    deps: AppActionAdapterDependencies,
) -> dict[str, Any]:
    if proposal.proposal_type != "act" or str(proposal.payload.get("action_type") or "") != "click":
        raise RuntimeError("unsupported human ops proposal")
    args = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    payload = {
        "x": _desktop_command_client_helpers._coerce_int(args.get("x")),
        "y": _desktop_command_client_helpers._coerce_int(args.get("y")),
        "label": str(args.get("label") or args.get("target") or "目标位置").strip() or "目标位置",
    }
    result = await send_desktop_command("human_ops_click", payload, deps=deps, timeout_sec=5)
    return {"clicked": True, **payload, **result}


async def perform_human_ops_action(
    proposal: ReviewableProposal,
    *,
    deps: AppActionAdapterDependencies,
) -> dict[str, Any]:
    if proposal.proposal_type != "act":
        raise RuntimeError("unsupported human ops proposal")
    action_type = str(proposal.payload.get("action_type") or "").strip()
    if action_type == "click":
        click_handler = deps.perform_human_ops_click or perform_human_ops_click
        return await click_handler(proposal, deps=deps) if click_handler is perform_human_ops_click else await click_handler(proposal)
    action_handler = deps.perform_human_ops_action or _desktop_command_client_helpers.perform_human_ops_action
    return await action_handler(
        proposal,
        send_command=lambda command_type, payload, *, timeout_sec: deps.send_desktop_command(
            command_type,
            payload,
            command_path=deps.command_path,
            timeout_sec=timeout_sec,
        ),
    )
