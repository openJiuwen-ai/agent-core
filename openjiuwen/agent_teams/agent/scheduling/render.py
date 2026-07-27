# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Scheduler message assembly (F_62, templated by F_63).

Two audiences, two mechanisms:

* **members** get leader-identity mailbox messages. Their wording is *not*
  assembled here — this module only builds the delivery payload (``meta``:
  template key + row refs + transient params) and the mailbox row stores an
  empty ``content``. The text lives in ``prompts/<lang>/scheduler_*.md`` and
  is rendered against the *current* task row at delivery time, so a handoff
  that sat in an offline member's mailbox never carries a stale task brief.
  See ``message_template.py``.
* **the leader** gets direct input injections (digests / escalations). Those
  never touch the mailbox, so there is no meta channel and no delivery-time
  expansion — they stay one-line ``i18n.t`` strings rendered right here.
"""

from __future__ import annotations

from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.message_template import build_meta
from openjiuwen.agent_teams.schema.status import TaskStatus

# Template keys, i.e. ``prompts/<lang>/<key>.md`` basenames.
_TASK_START = "scheduler_task_start"
_TASK_START_PLAN = "scheduler_task_start_plan"
_REVIEW_REQUEST = "scheduler_review_request"
_REVIEW_RENUDGE = "scheduler_review_renudge"
_REWORK = "scheduler_rework"
_VERIFIED_REPORT = "scheduler_verified_report"


def meta_task_start(task) -> dict:
    """Delivery payload for the assignee's start instruction (plan gate aware)."""
    template = _TASK_START_PLAN if task.status == TaskStatus.PLANNING.value else _TASK_START
    return build_meta(template, refs={"task": task.task_id})


def meta_review_request(task) -> dict:
    """Delivery payload for one reviewer of a freshly opened round."""
    return build_meta(_REVIEW_REQUEST, refs={"task": task.task_id})


def meta_review_renudge(task) -> dict:
    """Delivery payload for a reviewer that has not voted in the open round."""
    return build_meta(_REVIEW_RENUDGE, refs={"task": task.task_id})


def meta_rework(task, max_rounds: int, feedback: str) -> dict:
    """Delivery payload for the author after a failed round settled.

    ``max_rounds`` and ``feedback`` are params rather than refs: the ceiling is
    the *resolved* value (the task column may be NULL and fall back to the spec
    default), and the feedback aggregates the vote rows of the round that just
    closed — the task row can answer neither at delivery time.
    """
    return build_meta(
        _REWORK,
        refs={"task": task.task_id},
        params={"max_rounds": str(max_rounds), "feedback": feedback or t("scheduler.none")},
    )


def meta_verified_report(task) -> dict:
    """Delivery payload asking the author to report results to the leader."""
    return build_meta(_VERIFIED_REPORT, refs={"task": task.task_id})


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
