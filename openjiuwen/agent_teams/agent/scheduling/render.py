# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Render scheduler handoff messages (F_62).

Pure text assembly over ``i18n.t`` — the scheduler speaks to members through
leader-identity mailbox messages and to the leader through direct input
injection; every wording lives in ``agent_teams/i18n.py`` under the
``scheduler.*`` keys. No XML here: mailbox delivery wraps the content in
``<team-inbound>`` on the receiving side with the leader as sender.
"""

from __future__ import annotations

from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.schema.status import TaskStatus


def render_task_start(task) -> str:
    """Start instruction for the assignee (plan gate aware)."""
    key = "scheduler.task_start_plan" if task.status == TaskStatus.PLANNING.value else "scheduler.task_start"
    return t(key, task_id=task.task_id, title=task.title, content=task.content)


def render_review_request(task) -> str:
    """Review instruction for one reviewer of a freshly opened round."""
    return t(
        "scheduler.review_request",
        task_id=task.task_id,
        title=task.title,
        author=task.assignee or "?",
        round=task.review_round,
    )


def render_review_renudge(task) -> str:
    """Reminder for a reviewer that has not voted in the open round."""
    return t("scheduler.review_renudge", task_id=task.task_id, title=task.title, round=task.review_round)


def render_rework(task, max_rounds: int, feedback: str) -> str:
    """Rework instruction for the author after a failed round settled."""
    return t(
        "scheduler.rework",
        task_id=task.task_id,
        title=task.title,
        round=task.review_round,
        max_rounds=max_rounds,
        feedback=feedback or t("scheduler.none"),
    )


def render_verified_report(task) -> str:
    """Post-pass notice asking the author to report results to the leader."""
    return t("scheduler.verified_report", task_id=task.task_id, title=task.title)


def render_leader_task_done(task_id: str, title: str, *, verified: bool, remaining: int) -> str:
    """One-line terminal digest injected into the leader."""
    how_key = "scheduler.leader_task_done_how_verified" if verified else "scheduler.leader_task_done_how_direct"
    return t("scheduler.leader_task_done", task_id=task_id, title=title, how=t(how_key), remaining=remaining)


def render_leader_escalation_rounds(task, feedback: str) -> str:
    """Escalation injected into the leader when the round ceiling is exhausted."""
    return t(
        "scheduler.leader_escalation_rounds",
        task_id=task.task_id,
        title=task.title,
        rounds=task.review_round,
        feedback=feedback or t("scheduler.none"),
    )


def render_leader_escalation_stall(task, *, minutes: int, voted: list[str], pending: list[str]) -> str:
    """Escalation injected into the leader when a review round stalls voteless."""
    return t(
        "scheduler.leader_escalation_stall",
        task_id=task.task_id,
        title=task.title,
        round=task.review_round,
        minutes=minutes,
        voted=", ".join(voted) if voted else t("scheduler.none"),
        pending=", ".join(pending) if pending else t("scheduler.none"),
    )


def render_leader_all_done(count: int) -> str:
    """Final digest injected into the leader when the board drains."""
    return t("scheduler.leader_all_done", count=count)


def format_fail_feedback(fail_feedback: dict[str, str]) -> str:
    """Join per-reviewer fail feedback into an attributed block."""
    lines = [f"- {reviewer}: {feedback}" for reviewer, feedback in fail_feedback.items()]
    return "\n".join(lines)
