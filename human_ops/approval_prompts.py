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
        "执行后验证：\n"
        f"{observation_text}\n\n"
        f"{computer_use_block}"
        "请独立判断原始目标是否已经完成、当前证据还缺什么，以及最小安全下一步。"
        "surface、affordance 和 stage 是可用的状态描述，不是固定流程。"
        "把执行返回值、平台原生状态、chat_context 和可见观察都当作证据；不要重复获取已经足够且一致的证据。"
        "当前可审批动作能力包括：通过 Playwright 操作浏览器页面、打开已确认的本地应用、点击已定位目标、"
        "向已确认焦点的输入位置输入文字，以及按 Enter/Return。浏览器任务应遵循用户已经选择的 Playwright 或人类操作方式；"
        "也可以通过 Human Ops 执行受允许根目录约束的文件列表、读取、写入、新建目录、复制、移动和非递归删除；每一项文件动作都单独审批，读取文件同样需要审批，不能访问受保护的本地状态。"
        "如果上下文中还没有明确选择，默认使用 Playwright，不为执行方式询问；Chrome 个人资料优先使用用户在当前任务中指定的值，否则使用设置页中的已选值，两者都缺少时才询问。"
        "由你根据目标和证据在 think、observe、propose_act、need_user、blocked 或 done 中选择下一步；"
        "observe 只用于补齐下一步确实依赖的可见状态，不要把任何动作类型套进预设顺序。"
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
            intended_chat = str(args.get("intended_chat") or "").strip()
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
            expected_text = str(args.get("expected_text") or args.get("text") or args.get("draft") or "").strip()
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
