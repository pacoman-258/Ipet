from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from brain.decisions import BrainDecision
from human_ops.approval_prompts import (
    build_human_ops_continuation_prompt,
    build_post_approval_observe_prompt,
)
from human_ops.approvals import ReviewableProposal
from human_ops.proposals import (
    build_human_ops_act_proposal,
    proposal_arguments as human_ops_proposal_arguments,
    proposal_continue_after_approval as human_ops_proposal_continue_after_approval,
    proposal_event_payload as human_ops_proposal_event_payload,
    proposal_tool_label as human_ops_proposal_tool_label,
    should_default_continue_after_approval as human_ops_should_default_continue_after_approval,
    with_inherited_enter_expected_text as human_ops_with_inherited_enter_expected_text,
)


@dataclass(frozen=True)
class HumanOpsProposalFlowDependencies:
    pending_proposals: MutableMapping[str, dict[str, Any]]
    uuid_factory: Callable[[], str]
    time_func: Callable[[], float]
    looks_like_desktop_action_request: Callable[[str], bool]
    looks_like_chat_reply_request: Callable[[str], bool]
    goal_is_terminal: Callable[[str], bool]
    computer_use_context_text: Callable[[dict[str, Any]], str]


def coerce_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


def proposal_tool_label(proposal: ReviewableProposal) -> str:
    return human_ops_proposal_tool_label(proposal)


def proposal_event_payload(proposal_id: str, proposal: ReviewableProposal) -> dict[str, Any]:
    return human_ops_proposal_event_payload(proposal_id, proposal)


def should_default_continue_after_approval(
    user_text: str,
    goal: dict[str, Any],
    *,
    action_type: str,
    arguments: dict[str, Any],
    deps: HumanOpsProposalFlowDependencies,
) -> bool:
    return human_ops_should_default_continue_after_approval(
        user_text,
        goal,
        action_type=action_type,
        arguments=arguments,
        looks_like_desktop_action_request=deps.looks_like_desktop_action_request,
        looks_like_chat_reply_request=deps.looks_like_chat_reply_request,
        goal_is_terminal=deps.goal_is_terminal,
    )


def create_human_ops_act_proposal(
    decision: BrainDecision,
    *,
    session_id: str,
    user_text: str,
    deps: HumanOpsProposalFlowDependencies,
) -> tuple[str, ReviewableProposal]:
    proposal = build_human_ops_act_proposal(
        decision,
        user_text=user_text,
        looks_like_desktop_action_request=deps.looks_like_desktop_action_request,
        looks_like_chat_reply_request=deps.looks_like_chat_reply_request,
        goal_is_terminal=deps.goal_is_terminal,
    )
    proposal_id = str(deps.uuid_factory())
    deps.pending_proposals[proposal_id] = {
        "proposal": proposal,
        "session_id": session_id,
        "user_text": user_text,
        "created_at": deps.time_func(),
        "status": "pending",
    }
    return proposal_id, proposal


def proposal_arguments(proposal: ReviewableProposal) -> dict[str, Any]:
    return human_ops_proposal_arguments(proposal)


def proposal_continue_after_approval(proposal: ReviewableProposal) -> bool:
    return human_ops_proposal_continue_after_approval(proposal)


def with_inherited_enter_expected_text(
    decision: BrainDecision,
    *,
    previous_proposal: ReviewableProposal,
    execution: dict[str, Any],
) -> BrainDecision:
    return human_ops_with_inherited_enter_expected_text(
        decision,
        previous_proposal=previous_proposal,
        execution=execution,
    )


def human_ops_continuation_prompt(
    *,
    user_text: str,
    proposal: ReviewableProposal,
    execution: dict[str, Any],
    observation: dict[str, Any],
    deps: HumanOpsProposalFlowDependencies,
) -> str:
    return build_human_ops_continuation_prompt(
        user_text=user_text,
        proposal=proposal,
        execution=execution,
        observation=observation,
        computer_use_context_text=deps.computer_use_context_text(observation),
    )


def post_approval_observe_prompt(
    user_text: str,
    proposal: ReviewableProposal,
    *,
    deps: HumanOpsProposalFlowDependencies,
) -> str:
    return build_post_approval_observe_prompt(
        user_text,
        proposal,
        looks_like_chat_reply_request=deps.looks_like_chat_reply_request,
    )
