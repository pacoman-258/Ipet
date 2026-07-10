from __future__ import annotations

import json
from typing import Any, Callable

from .approvals import ReviewableProposal
from .proposals import proposal_arguments, proposal_tool_label

IntentPredicate = Callable[[str], bool]


def build_human_ops_continuation_prompt(
    *,
    user_text: str,
    proposal: ReviewableProposal,
    execution: dict[str, Any],
    observation: dict[str, Any],
    computer_use_context_text: str = "",
) -> str:
    observation_text = str(observation.get("text") or "").strip()
    computer_use_context = str(computer_use_context_text or "").strip()
    computer_use_block = f"{computer_use_context}\n\n" if computer_use_context else ""
    return (
        f"用户原始复杂任务：{user_text}\n\n"
        f"上一项已批准并执行：{proposal.to_dict()}\n"
        f"执行结果：{json.dumps(execution, ensure_ascii=False)}\n\n"
        "执行后观察：\n"
        f"{observation_text}\n\n"
        f"{computer_use_block}"
        "请像会用电脑的人类一样继续当前任务阶段。"
        "识别当前 surface、可用 affordance、stage 和下一步最小动作。"
        "如果 chat_context.recent_messages 已经提供足够聊天依据，请据此起草或发送回复，不要反复观察同一段消息。"
        "如果已经有足够入口，请返回 propose_act；"
        "需要输入回复时使用 type_text，需要发送/确认时使用 key_press enter；"
        "只有缺少可操作入口时才返回 observe。"
    )


def build_post_approval_observe_prompt(
    user_text: str,
    proposal: ReviewableProposal,
    *,
    looks_like_chat_reply_request: IntentPredicate | None = None,
) -> str:
    task = str(user_text or "").strip()
    label = proposal_tool_label(proposal)
    args = proposal_arguments(proposal)
    action_type = str(proposal.payload.get("action_type") or "").strip()
    looks_like_chat = looks_like_chat_reply_request or (lambda _text: False)
    if looks_like_chat(task):
        if action_type == "type_text":
            draft = str(args.get("text") or "").strip()
            draft_clause = f"刚才输入的草稿是“{draft}”。" if draft else ""
            return (
                f"用户目标是“{task}”。刚才已执行获批动作：{label}。{draft_clause}"
                "请观察执行后的屏幕状态：当前是否仍在微信目标联系人/会话里，"
                "聊天输入框中是否已经出现这段草稿，草稿文字是否完整，"
                "是否有发送入口，或是否可以用回车发送。"
                "请用自然语言给出可见依据；如果可以发送，请说明 key_press enter 是否是合适的下一步。"
            )
        if action_type == "key_press" and str(args.get("key") or "enter").strip().lower() in {"enter", "return"}:
            expected_text = str(args.get("expected_text") or args.get("text") or args.get("draft") or "").strip()
            expected_clause = f"预期刚发送的回复是“{expected_text}”。" if expected_text else ""
            return (
                f"用户目标是“{task}”。刚才已执行获批动作：{label}。{expected_clause}"
                "请观察执行后的屏幕状态：当前是否仍在微信目标联系人/会话里，"
                "聊天记录底部是否已经发送并出现这条回复，输入框是否已清空或回到可继续输入状态。"
                "请用自然语言给出可见依据；重点判断这次回复是否已经发送成功。"
            )
        return (
            f"用户目标是“{task}”。刚才已执行获批动作：{label}。"
            "请观察执行后的屏幕状态：当前是否在微信或目标联系人/会话里，"
            "最近聊天内容是什么，是否有聊天输入框可输入回复，输入框是否已聚焦或有光标，"
            "是否有发送入口或可以用回车发送。"
            "请用自然语言给出可见依据；如果有可点击联系人、输入框或发送按钮，也说明位置和可操作入口。"
        )
    return (
        f"请观察刚才获批动作执行后的屏幕状态，找出当前任务阶段和可操作入口。"
        f"{' 原始用户目标：' + task if task else ''}"
    )
