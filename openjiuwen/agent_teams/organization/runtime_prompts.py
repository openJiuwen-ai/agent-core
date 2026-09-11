# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leader-turn prompt text for organization runtime wakeups."""

from __future__ import annotations

__all__ = [
    "claim_turn",
    "claimed_task_execution_turn",
    "delegated_turn",
    "leader_message_turn",
    "parent_child_failed_turn",
    "parent_ready_turn",
    "parent_repair_turn",
    "parent_review_turn",
    "repair_create_instructions",
    "summary_aggregate_turn",
    "summary_root_attention_turn",
]


def claim_turn(*, task_id: str, organization_id: str, trigger_task_id: str | None = None) -> str:
    trigger_context = (
        f" Task {trigger_task_id} just completed, so re-evaluate this open task now." if trigger_task_id else ""
    )
    return (
        f"Organization task {task_id} is available in {organization_id}.{trigger_context} "
        "Inspect it with org_view_tasks(action='get'). If every required capability is present "
        "in your team, you MUST call org_claim_task for this task in this turn. Do not leave a "
        "capability-matched task OPEN merely because another team's artifact is not ready: claim "
        "it first, prepare any independent work, and use org_view_tasks to wait for dependencies "
        "before starting dependent validation. When the defined scope has been executed, produce "
        "one final result or report and call org_update_task(action='complete') in the same "
        "workflow, including failures and blockers in its output. Do not wait for another team to "
        "fix a reported issue, and do not create an open-ended sequence of extra verification tasks "
        "unless the parent task explicitly requests it. Only skip the claim when a required capability "
        "is actually absent or the claim fails because another team already claimed it."
    )


def delegated_turn(*, task_id: str, organization_id: str) -> str:
    return (
        f"Organization task {task_id} in {organization_id} was delegated to your team. "
        "Inspect it with org_view_tasks(action='get'), then use org_update_task(action='start') "
        "when you are ready. If an independent part requires another organization team's "
        "capabilities, keep this parent task assigned to your team and create a focused OPEN child "
        f"with org_create_task(parent_task_id='{task_id}'). Give each child a clear scope, "
        "acceptance criteria, and only the capabilities it needs; do not set delegated_to_team_id. "
        "Track children with org_view_child_tasks and do not complete the parent until its direct "
        "children are completed and accepted. Otherwise execute the task through your team workflow "
        "and complete it with the resulting output context and output abstract."
    )


def leader_message_turn(*, message_id: str, from_team_id: str, organization_id: str) -> str:
    return (
        f"Leader message {message_id} arrived in organization {organization_id} "
        f"from team {from_team_id}. Read it with org_get_leader_message, perform any required "
        "cross-team coordination or task-pool updates, then call org_ack_leader_message only "
        "after the message has been handled."
    )


def claimed_task_execution_turn(*, task_id: str, organization_id: str) -> str:
    return (
        f"Your team claimed organization task {task_id} in {organization_id}. "
        "Inspect it with org_view_tasks(action='get'). If it is still assigned to your team and "
        "its status is CLAIMED, immediately call org_update_task(action='start'). Then execute the "
        "defined scope through your Team workflow. If an independent part requires another organization "
        "team's capabilities, keep this parent task assigned to your team and create a focused OPEN child "
        f"with org_create_task(parent_task_id='{task_id}'). Give each child a clear scope, acceptance "
        "criteria, and only the capabilities it needs; do not set delegated_to_team_id. Track children "
        "with org_view_child_tasks and do not complete the parent until its direct children are completed "
        "and accepted. When the task is actually complete, submit one concrete result with "
        "org_update_task(action='complete'). If the task is already IN_PROGRESS or COMPLETED, do not "
        "duplicate work."
    )


def parent_review_turn(
    *,
    child_task_id: str,
    parent_task_id: str,
    organization_id: str,
) -> str:
    return (
        f"Child organization task {child_task_id} completed in {organization_id}. "
        f"Inspect its result with org_review_task, then accept or reject it. "
        f"If accepted, use the child output to continue parent task {parent_task_id}. "
        "If rejected, create a repair with org_create_task "
        f"(set repairs_task_id={child_task_id} on the original sibling; never repair-of-repair; "
        "do not org_delegate_task the rejected child). "
        "When all direct children are accepted or superseded by an accepted repair, "
        "complete the parent. For a root task, put the user-facing delivery in "
        "org_update_task output_context.description and provide output_abstract."
    )


def parent_repair_turn(
    *,
    child_task_id: str,
    parent_task_id: str,
    organization_id: str,
    review_status: str,
    repair_instructions: str,
) -> str:
    return (
        f"Child organization task {child_task_id} was reviewed as {review_status} "
        f"in {organization_id}. Parent task {parent_task_id} cannot advance on that child. "
        "Read the child result and review verdict/required_changes. "
        + repair_instructions
    )


def parent_ready_turn(*, parent_task_id: str, organization_id: str) -> str:
    return (
        f"All direct child tasks for parent organization task {parent_task_id} "
        f"in {organization_id} are accepted or superseded by an accepted repair. "
        "Integrate the child outputs and call org_update_task(action='complete') on the "
        "parent with the final output_context and output_abstract. For a root task, put the "
        "user-facing delivery in output_context.description."
    )


def parent_child_failed_turn(
    *,
    child_task_id: str,
    parent_task_id: str,
    organization_id: str,
    failure_code: str,
    failure_reason: str,
    repair_instructions: str,
) -> str:
    return (
        f"Child organization task {child_task_id} failed in {organization_id} "
        f"(failure_code={failure_code}, failure_reason={failure_reason}). "
        f"Parent task {parent_task_id} cannot advance on that child. "
        "This is not a pending review — do not call org_review_task on the failed child. "
        + repair_instructions
    )


def summary_aggregate_turn(
    *,
    organization_id: str,
    summary_task_id: str,
    root_task_id: str,
) -> str:
    return (
        f"You are the Summary Team aggregating root task {root_task_id} in {organization_id}. "
        f"Organization summary task {summary_task_id} is delegated to your team. "
        "Read its bound source outputs with org_view_tasks(action='get') and "
        "org_view_child_tasks, integrate them into one final result, then call "
        f"org_update_task(action='start') and org_update_task(action='complete') on "
        f"{summary_task_id} in the same workflow. If material content is missing, create "
        f"a focused supplementary task with org_create_task(parent_task_id='{root_task_id}') "
        "rather than fabricating output."
    )


def summary_root_attention_turn(
    *,
    organization_id: str,
    summary_task_id: str,
    root_task_id: str,
) -> str:
    return (
        f"Organization summary task {summary_task_id} for root task {root_task_id} in "
        f"{organization_id} needs your attention. Inspect it with "
        "org_view_tasks(action='get'). If summary sources failed or provisioning failed, "
        "supplement them with new source tasks, create a replacement summary task, or "
        "fail/terminate toward the root as appropriate. When the summary completes, "
        "inject its final result into the root's output context."
    )


def repair_create_instructions(
    *,
    target_id: str,
    report_phrase: str,
    terminal_label: str,
) -> str:
    """Shared wake guidance for creating a repair sibling of a terminal child."""
    return (
        "Create a focused repair task with org_create_task "
        f"(set repairs_task_id={target_id} pointing at the original sibling, never another "
        f"repair; include {report_phrase} and acceptance criteria; prefer capabilities that "
        "match the defect; if the original has retry_limit, do not exceed it). "
        "If org_create_task fails because retry_limit is reached, do not retry create in a "
        "loop: call org_update_task(action='failed') on the parent with failure_reason "
        "explaining that the repair budget is exhausted, so the owning/parent team can "
        "decide the next step or fail/terminate toward the root. Same team may "
        "execute the repair; switching teams is optional—only if switching teams, set "
        "delegated_to_team_id on that new repair (or org_delegate_task the new OPEN repair "
        "only). Do not call org_delegate_task on the "
        f"{terminal_label} child, which is terminal. "
        "Do not leave the parent waiting without creating that repair, and do not silently "
        f"reopen the {terminal_label} child task."
    )
