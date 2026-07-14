from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from human_ops import ReviewableProposal
from human_ops.filesystem_actions import FILESYSTEM_ACTIONS, execute_filesystem_action, filesystem_action_label
from human_ops.playwright_actions import execute_playwright_action

from . import desktop_command_client as _desktop_command_client_helpers


@dataclass(frozen=True)
class AppActionAdapterDependencies:
    command_path: Path
    send_desktop_command: Callable[..., Awaitable[dict[str, Any]]]
    perform_human_ops_click: Callable[[ReviewableProposal], Awaitable[dict[str, Any]]] | None = None
    perform_human_ops_action: Callable[..., Awaitable[dict[str, Any]]] | None = None
    perform_playwright_action: Callable[[ReviewableProposal], Awaitable[dict[str, Any]]] | None = None
    filesystem_roots: tuple[Path, ...] = ()
    filesystem_max_read_bytes: int = 1_000_000
    filesystem_max_write_bytes: int = 1_000_000
    filesystem_max_list_entries: int = 200
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
    if action_type == "playwright":
        playwright_handler = deps.perform_playwright_action or execute_playwright_action
        return await playwright_handler(proposal)
    if action_type in FILESYSTEM_ACTIONS:
        roots = deps.filesystem_roots
        if not roots:
            raise RuntimeError("Human Ops filesystem has no configured allowed root.")
        return await execute_filesystem_action(
            proposal,
            allowed_roots=roots,
            max_read_bytes=deps.filesystem_max_read_bytes,
            max_write_bytes=deps.filesystem_max_write_bytes,
            max_list_entries=deps.filesystem_max_list_entries,
        )
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
    if action_type == "playwright":
        operation = str(args.get("operation") or "browser").strip() or "browser"
        target = str(args.get("url") or args.get("ref") or args.get("label") or "当前浏览器会话").strip()
        profile = str(args.get("profile") or "未选择").strip() or "未选择"
        target_line = f"\nPlaywright 操作：{operation}\n个人资料：{profile}\n目标：{target}"
        impact = (
            "批准后，Ipet 会在该个人资料对应的隔离 Playwright 会话中只执行这一项操作；"
            "open 会精确匹配该 Chrome 资料：若它是唯一活跃且允许远程调试的资料就复用原窗口，"
            "否则只读复制 cookies 与网页存储到 Ipet 私有持久快照，绝不写回原 Chrome 资料；"
            "attach 只连接唯一活跃且允许远程调试的所选资料。"
            "首次运行可能通过 npx 获取 @playwright/cli，并可能访问目标网络地址；不会执行任意网页脚本。"
        )
    elif action_type in FILESYSTEM_ACTIONS:
        target_line = f"\n文件动作：{filesystem_action_label(action_type, args)}"
        impact = (
            "批准后，Ipet 只会在项目根目录或用户配置的允许根目录内执行这一项文件动作；"
            "读取会把限定大小的 UTF-8 文本返回给当前会话，写入/复制/移动/删除会改变本地文件状态。"
            "不会跟随最终符号链接，不会递归删除，不会覆盖复制或移动目标；拒绝后不执行任何文件操作。"
        )
    else:
        target_line = f"\n目标应用：{target_app}" if target_app else "\n目标界面：macOS 桌面"
        impact = (
            "批准后，Ipet 会先切换并验证目标界面，再执行这一项动作。"
            "该操作会改变应用焦点或界面状态；不会在本审批之外执行其他动作。"
        )
    message = (
        f"{proposal.summary}{target_line}\n\n"
        f"{impact}"
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
