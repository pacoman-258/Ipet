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
    target_app = str(args.get("target_app") or "").strip()
    if not target_app:
        raise RuntimeError("Human Ops click requires target_app.")
    payload = {
        "target_app": target_app,
        "x": _desktop_command_client_helpers._coerce_int(args.get("x")),
        "y": _desktop_command_client_helpers._coerce_int(args.get("y")),
        "label": str(args.get("label") or args.get("target") or "目标位置").strip() or "目标位置",
    }
    result = await send_desktop_command("human_ops_click", payload, deps=deps, timeout_sec=8)
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


async def request_native_human_ops_approval(
    proposal: ReviewableProposal,
    *,
    deps: AppActionAdapterDependencies,
) -> dict[str, Any]:
    args = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    action_type = str(proposal.payload.get("action_type") or "").strip()
    target_app = str(
        args.get("target_app")
        or (args.get("app") if action_type == "launch_app" else "")
        or ""
    ).strip()
    target_line = f"\n目标应用：{target_app}" if target_app else "\n目标界面：macOS 桌面"
    message = (
        f"{proposal.summary}{target_line}\n\n"
        "批准后，Ipet 会先切换并验证目标界面，再执行这一项动作。"
        "该操作会改变应用焦点或界面状态；不会在本审批之外执行其他动作。"
    )
    return await send_desktop_command(
        "human_ops_native_approval",
        {
            "title": "Ipet Human Ops 审批",
            "message": message,
            "action_type": action_type,
            "target_app": target_app,
            "timeout_sec": 300,
        },
        deps=deps,
        timeout_sec=310,
    )
