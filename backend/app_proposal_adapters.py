from __future__ import annotations

from collections.abc import Callable, MutableMapping
from typing import Any

from brain.decisions import BrainDecision
from human_ops.approvals import ReviewableProposal

from . import human_ops_proposal_flow as _human_ops_proposal_flow_helpers

ProposalFlowDependencies = _human_ops_proposal_flow_helpers.HumanOpsProposalFlowDependencies


def proposal_flow_dependencies(
    *,
    pending_proposals: MutableMapping[str, dict[str, Any]],
    uuid_factory: Callable[[], str],
    time_func: Callable[[], float],
    looks_like_desktop_action_request: Callable[[str], bool],
    looks_like_chat_reply_request: Callable[[str], bool],
    goal_is_terminal: Callable[[str], bool],
    computer_use_context_text: Callable[[dict[str, Any]], str],
) -> ProposalFlowDependencies:
    return ProposalFlowDependencies(
        pending_proposals=pending_proposals,
        uuid_factory=uuid_factory,
        time_func=time_func,
        looks_like_desktop_action_request=looks_like_desktop_action_request,
        looks_like_chat_reply_request=looks_like_chat_reply_request,
        goal_is_terminal=goal_is_terminal,
        computer_use_context_text=computer_use_context_text,
    )


def coerce_int(value: Any, fallback: int = 0) -> int:
    return _human_ops_proposal_flow_helpers.coerce_int(value, fallback)


def proposal_tool_label(proposal: ReviewableProposal) -> str:
    return _human_ops_proposal_flow_helpers.proposal_tool_label(proposal)


def proposal_event_payload(proposal_id: str, proposal: ReviewableProposal) -> dict[str, Any]:
    return _human_ops_proposal_flow_helpers.proposal_event_payload(proposal_id, proposal)


def should_default_continue_after_approval(
    user_text: str,
    goal: dict[str, Any],
    *,
    action_type: str,
    arguments: dict[str, Any],
    deps: ProposalFlowDependencies,
) -> bool:
    return _human_ops_proposal_flow_helpers.should_default_continue_after_approval(
        user_text,
        goal,
        action_type=action_type,
        arguments=arguments,
        deps=deps,
    )


def create_human_ops_act_proposal(
    decision: BrainDecision,
    *,
    session_id: str,
    user_text: str,
    deps: ProposalFlowDependencies,
) -> tuple[str, ReviewableProposal]:
    return _human_ops_proposal_flow_helpers.create_human_ops_act_proposal(
        decision,
        session_id=session_id,
        user_text=user_text,
        deps=deps,
    )


def proposal_arguments(proposal: ReviewableProposal) -> dict[str, Any]:
    return _human_ops_proposal_flow_helpers.proposal_arguments(proposal)


def proposal_continue_after_approval(proposal: ReviewableProposal) -> bool:
    return _human_ops_proposal_flow_helpers.proposal_continue_after_approval(proposal)


def with_inherited_enter_expected_text(
    decision: BrainDecision,
    *,
    previous_proposal: ReviewableProposal,
    execution: dict[str, Any],
) -> BrainDecision:
    return _human_ops_proposal_flow_helpers.with_inherited_enter_expected_text(
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
    deps: ProposalFlowDependencies,
) -> str:
    return _human_ops_proposal_flow_helpers.human_ops_continuation_prompt(
        user_text=user_text,
        proposal=proposal,
        execution=execution,
        observation=observation,
        deps=deps,
    )


def post_approval_observe_prompt(
    user_text: str,
    proposal: ReviewableProposal,
    *,
    deps: ProposalFlowDependencies,
) -> str:
    return _human_ops_proposal_flow_helpers.post_approval_observe_prompt(
        user_text,
        proposal,
        deps=deps,
    )
