from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from human_ops import ReviewableProposal
from human_ops.filesystem_actions import FILESYSTEM_ACTIONS, execute_filesystem_action, filesystem_action_label
from human_ops.playwright_actions import execute_playwright_action
from human_ops.shell_actions import MAX_OUTPUT_BYTES, SHELL_ACTION, assess_shell_risk, execute_shell_action

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
    shell_default_cwd: Path = Path(".")
    shell_max_output_bytes: int = MAX_OUTPUT_BYTES
    shell_enabled: bool = True
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
    ax_ref = args.get("ax_ref") if isinstance(args.get("ax_ref"), dict) else {}
    payload = {
        "target_app": target_app,
        "label": str(args.get("label") or args.get("target") or "目标位置").strip() or "目标位置",
    }
    if ax_ref:
        payload["ax_ref"] = dict(ax_ref)
    else:
        payload["x"] = _desktop_command_client_helpers._coerce_int(args.get("x"))
        payload["y"] = _desktop_command_client_helpers._coerce_int(args.get("y"))
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
    if action_type == SHELL_ACTION:
        if not deps.shell_enabled:
            raise RuntimeError("Human Ops shell execution is disabled.")
        return await execute_shell_action(
            proposal,
            default_cwd=deps.shell_default_cwd,
            max_output_bytes=deps.shell_max_output_bytes,
        )
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
    elif action_type == SHELL_ACTION:
        risk = assess_shell_risk(args)
        reasons = "；".join(risk.reasons) or str(args.get("risk_reason") or "未提供")
        rules = ", ".join(risk.matched_rules) or "无"
        target_line = (
            f"\n完整命令：{str(args.get('command') or '')}"
            f"\n工作目录：{str(args.get('cwd') or '.')}"
            f"\n模型判断：{str(args.get('model_risk') or 'uncertain')} — {str(args.get('risk_reason') or '')}"
            f"\n本地风险规则：{rules}"
        )
        impact = (
            f"风险原因：{reasons}。批准后，Ipet 会把上面的完整字符串交给当前操作系统 Shell，"
            "命令及其子进程拥有与 Ipet 相同的用户权限，可能访问网络、修改或删除数据；"
            "执行受超时和输出长度限制，但当前版本不提供操作系统级沙箱。拒绝后不会启动进程。"
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


async def notify_human_ops_action(
    proposal: ReviewableProposal,
    *,
    task_id: str,
    deps: AppActionAdapterDependencies,
) -> dict[str, Any]:
    if proposal.proposal_type != "act":
        raise RuntimeError("Human Ops action notice requires an act proposal.")
    action_type = str(proposal.payload.get("action_type") or "").strip()
    args = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    action_label = {
        "click": "点击",
        "type_text": "输入文字",
        "key_press": "按键",
        "launch_app": "打开应用",
        "playwright": "浏览器操作",
        "file_list": "列出文件",
        "file_read": "读取文件",
        "file_write": "写入文件",
        "file_mkdir": "创建文件夹",
        "file_copy": "复制文件",
        "shell": "运行 Shell 命令",
        "file_move": "移动文件",
        "file_delete": "删除文件",
    }.get(action_type, "操作")
    target_app = str(
        args.get("target_app")
        or (args.get("app") if action_type == "launch_app" else "")
        or ""
    ).strip()[:80]
    target_text = f"，目标应用：{target_app}" if target_app else ""
    result = await send_desktop_command(
        "human_ops_native_approval",
        {
            "notice_only": True,
            "title": "Ipet 完全授权操作",
            "message": (
                f"即将执行：{action_label}{target_text}。"
                "你可以在 Ipet 聊天窗口随时停止；已经发出的原子动作可能完成。"
            ),
            "action_type": action_type,
            "target_app": target_app,
            "task_id": str(task_id or "").strip(),
        },
        deps=deps,
        timeout_sec=8,
    )
    if result.get("notified") is not True:
        raise RuntimeError("macOS action notification was not confirmed.")
    return result
