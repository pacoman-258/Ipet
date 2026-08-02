from __future__ import annotations

import json
from typing import Any

from .approvals import ReviewableProposal
from .proposals import proposal_arguments, proposal_tool_label


def _prompt_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…<省略 {len(text) - limit} 字符>"


def _compact_prompt_value(value: object, *, depth: int = 0) -> object:
    if depth >= 3:
        return "<nested>"
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                result["<more_fields>"] = len(value) - index
                break
            if str(key) == "content" and isinstance(item, str):
                result[str(key)] = f"<{len(item)} chars>"
            else:
                result[str(key)] = _compact_prompt_value(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        items = [_compact_prompt_value(item, depth=depth + 1) for item in value[:8]]
        if len(value) > 8:
            items.append(f"<{len(value) - 8} more>")
        return items
    if isinstance(value, str):
        return _prompt_text(value, 500)
    return value


def build_human_ops_continuation_prompt(
    *,
    user_text: str,
    proposal: ReviewableProposal,
    execution: dict[str, Any],
    observation: dict[str, Any],
    computer_use_context_text: str = "",
) -> str:
    observation_text = _prompt_text(observation.get("text"), 3_000)
    computer_use_context = _prompt_text(computer_use_context_text, 3_000)
    computer_use_block = f"{computer_use_context}\n\n" if computer_use_context else ""
    action_type = str(proposal.payload.get("action_type") or "").strip()
    arguments = (
        proposal.payload.get("arguments")
        if isinstance(proposal.payload.get("arguments"), dict)
        else {}
    )
    proposal_delta = {
        "action_type": action_type,
        "arguments": _compact_prompt_value(arguments),
    }
    execution_delta = _compact_prompt_value(execution)
    return (
        f"用户原始复杂任务：{_prompt_text(user_text, 1_500)}\n\n"
        f"上一项已批准并执行：{json.dumps(proposal_delta, ensure_ascii=False, separators=(',', ':'))}\n"
        f"执行结果：{json.dumps(execution_delta, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "执行后验证：\n"
        f"{observation_text}\n\n"
        f"{computer_use_block}"
        "请独立判断原始目标是否已经完成、当前证据还缺什么，以及最小安全下一步。"
        "把执行返回值、平台原生状态、chat_context 和可见观察都当作证据；不要重复获取已经足够且一致的证据。"
        "按系统合同返回一个最小下一步；observe 只补齐真正缺少的证据，不要把任何动作类型套进预设顺序。"
    )


def build_post_approval_observe_prompt(
    user_text: str,
    proposal: ReviewableProposal,
) -> str:
    task = _prompt_text(user_text, 1_000)
    label = proposal_tool_label(proposal)
    args = proposal_arguments(proposal)
    action_type = str(proposal.payload.get("action_type") or "").strip()
    chat_transaction = bool(
        str(args.get("intended_chat") or "").strip()
        or str(args.get("expected_text") or "").strip()
        or isinstance(args.get("input_ax_ref"), dict)
    )
    if chat_transaction:
        if action_type == "type_text":
            draft = _prompt_text(args.get("text"), 1_000)
            draft_clause = f"刚才输入的草稿是“{draft}”。" if draft else ""
            intended_chat = _prompt_text(args.get("intended_chat"), 300)
            chat_clause = (
                f"获批的目标会话是“{intended_chat}”。"
                if intended_chat
                else ""
            )
            return (
                f"用户目标是“{task}”。刚才已执行获批动作：{label}。{draft_clause}{chat_clause}"
                "请观察执行后的屏幕状态：当前会话标题是否仍与获批目标完全一致，"
                "聊天输入框中是否已经出现这段草稿，草稿文字是否完整，"
                "以及当前可见的发送入口或其他相关 affordance。"
                "请只用自然语言给出可见依据，不替 Brain 决定下一步动作。"
            )
        if action_type == "key_press" and str(args.get("key") or "enter").strip().lower() in {"enter", "return"}:
            expected_text = _prompt_text(
                args.get("expected_text") or args.get("text") or args.get("draft"),
                1_000,
            )
            expected_clause = f"预期刚发送的回复是“{expected_text}”。" if expected_text else ""
            return (
                f"用户目标是“{task}”。刚才已执行获批动作：{label}。{expected_clause}"
                "请观察执行后的屏幕状态：当前是否仍在目标应用或网页的目标联系人/会话里，"
                "聊天记录底部是否已经发送并出现这条回复，输入框是否已清空或回到可继续输入状态。"
                "请用自然语言给出可见依据；重点判断这次回复是否已经发送成功。"
            )
        return (
            f"用户目标是“{task}”。刚才已执行获批动作：{label}。"
            "请观察执行后的屏幕状态：当前是否在目标应用或网页的目标联系人/会话里，"
            "最近聊天内容是什么，是否有聊天输入框可输入回复，输入框是否已聚焦或有光标，"
            "以及有哪些可见的发送入口或相关 affordance。"
            "请用自然语言给出可见依据；如果有可点击联系人、输入框或发送按钮，也说明位置和可操作入口。"
        )
    return (
        f"请观察刚才获批动作执行后的屏幕状态，找出当前任务阶段和可操作入口。"
        f"{' 原始用户目标：' + task if task else ''}"
    )
