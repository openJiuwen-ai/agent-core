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

import re
from typing import TYPE_CHECKING, Any

from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.message_template import (
    build_meta,
    completion_summary,
    completion_summary_truncated,
    upstream_brief,
)
from openjiuwen.agent_teams.schema.status import TaskStatus

if TYPE_CHECKING:
    from openjiuwen.agent_teams.tools.models import TeamTaskReviewVoteBase

# Template keys, i.e. ``prompts/<lang>/<key>.md`` basenames.
_TASK_START = "scheduler_task_start"
_TASK_START_PLAN = "scheduler_task_start_plan"
_REVIEW_REQUEST = "scheduler_review_request"
_REVIEW_PREVIOUS_FEEDBACK = "scheduler_review_previous_feedback"
_REVIEW_SUBMISSION_NOTE = "scheduler_review_submission_note"
_REVIEW_UPSTREAM = "scheduler_review_upstream"
_REVIEW_RENUDGE = "scheduler_review_renudge"
_REWORK = "scheduler_rework"
_REWORK_SUMMARY = "scheduler_rework_summary"


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


def meta_rework_summary(task, max_rounds: int) -> dict:
    """Delivery payload asking the assignee to send a rework summary to the leader.

    Sent alongside an escalation when the review round ceiling is exhausted,
    so the leader receives both reviewer feedback and the assignee's
    self-assessment in the same decision window.
    """
    return build_meta(
        _REWORK_SUMMARY,
        refs={"task": task.task_id},
        params={"max_rounds": str(max_rounds)},
    )


def render_leader_task_done(task: Any, *, verified: bool) -> str:
    """Terminal digest injected into the leader, carrying the completion summary.

    The digest replaces the member's own completion report (F_119): it names
    the task, the bounded completion summary, and — when there is more to read
    (a completion note, or a summary cut for length) — where to read it.

    Carries no board count: the digest is queued as a follow-up and may reach
    the leader well after newer tasks were created, so a count snapshotted
    here would read as stale progress. The leader queries the live board.
    """
    how_key = "scheduler.leader_task_done_how_verified" if verified else "scheduler.leader_task_done_how_direct"
    lines = [t("scheduler.leader_task_done", task_id=task.task_id, title=task.title, how=t(how_key))]
    summary = completion_summary(task)
    if summary:
        lines.append(t("scheduler.leader_task_done_summary", summary=summary))
    if (getattr(task, "note", None) or "").strip() or completion_summary_truncated(task):
        lines.append(t("scheduler.leader_task_done_note_hint", task_id=task.task_id))
    lines.append(t("scheduler.leader_task_done_board_hint"))
    return "\n".join(lines)


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


_SUBMISSION_LABELS = {
    "cn": ("摘要", "详情"),
    "en": ("Summary", "Detail"),
}


def _submission_body(task: Any, language: str) -> str:
    """The assignee's summary and full note, verbatim, for a reviewer.

    A reviewer must see the whole submission, so nothing here is truncated.
    Each part is labelled and left out when missing.
    """
    summary_label, note_label = _SUBMISSION_LABELS.get(language, _SUBMISSION_LABELS["en"])
    parts: list[str] = []
    summary = (getattr(task, "summary", None) or "").strip()
    if summary:
        parts.append(f"{summary_label}: {summary}")
    note = (getattr(task, "note", None) or "").strip()
    if note:
        parts.append(f"{note_label}:\n{note}")
    return "\n\n".join(parts)


async def render_review_request_for_harness(
    task: Any,
    *,
    language: str = "cn",
    reviewer: str = "",
    loader: "Any" = None,  # TemplateLoader — bound lazily to dodge import cycles
    previous_vote: "TeamTaskReviewVoteBase | None" = None,
    upstream: list[Any] | None = None,
) -> str:
    """Render the review request as a plain user prompt for a temp reviewer harness.

    ``loader`` binds the team scheduler's per-team workspace cache so
    reviewer_* templates read evolved workspace values; ``None`` (default)
    keeps the framework read-only loader.

    A temp reviewer starts every round with a fresh context, so its own vote
    of the previous round (``previous_vote``) is rendered into the request:
    the reviewer can then check whether the rework it asked for was done.
    Without one (first round, or no vote then) the section is left out.

    The assignee's completion ``summary`` and full ``note`` (artifact paths,
    key decisions) are rendered verbatim as well, so the reviewer can locate
    the deliverable without a ``view_task`` round-trip. A task submitted
    without either leaves the section out. Likewise the ``upstream`` task rows
    are listed with their completion summaries, so a reviewer of a task that
    builds on others (an aggregation, say) knows where its inputs came from;
    no upstream task leaves the section out.
    """
    from openjiuwen.agent_teams.prompts.loader import load_template

    if loader is None:
        loader = load_template

    template = loader(_REVIEW_REQUEST, language)
    body = template.content
    body = body.replace("{{task.task_id}}", str(getattr(task, "task_id", "")))
    body = body.replace("{{task.title}}", getattr(task, "title", "") or "")
    body = body.replace("{{task.content}}", getattr(task, "content", "") or "")
    body = body.replace("{{task.status}}", str(getattr(task, "status", "")))
    body = body.replace("{{task.assignee}}", getattr(task, "assignee", "") or "")
    body = body.replace("{{task.review_round}}", str(getattr(task, "review_round", 0)))
    body = body.replace("{{task.max_review_rounds}}", str(getattr(task, "max_review_rounds") or ""))
    body = body.replace("{{task.reviewer}}", ", ".join(getattr(task, "reviewers", lambda: [])() or []))
    # Inject per-reviewer instruction from the structured reviewer list.
    instruction = ""
    if reviewer:
        for detail in (task.reviewer_details() if hasattr(task, 'reviewer_details') else []):
            if detail.get("reviewer_id") == reviewer:
                instruction = detail.get("instruction", "")
                break
    body = body.replace("{{task.reviewer_instruction}}", instruction)
    previous = ""
    if previous_vote is not None:
        previous = loader(_REVIEW_PREVIOUS_FEEDBACK, language).content.strip()
        previous = previous.replace("{{vote.review_round}}", str(previous_vote.review_round))
        previous = previous.replace("{{vote.decision}}", previous_vote.decision)
        previous = previous.replace("{{vote.feedback}}", previous_vote.feedback or t("scheduler.none"))
    body = body.replace("{{task.previous_feedback}}", previous)
    submission_note = ""
    submission_body = _submission_body(task, language)
    if submission_body:
        submission_note = loader(_REVIEW_SUBMISSION_NOTE, language).content.strip()
        submission_note = submission_note.replace("{{task.submission_body}}", submission_body)
    body = body.replace("{{task.submission_note}}", submission_note)
    upstream_section = ""
    upstream_lines = upstream_brief(upstream or [], language)
    if upstream_lines:
        upstream_section = loader(_REVIEW_UPSTREAM, language).content.strip()
        upstream_section = upstream_section.replace("{{task.upstream_brief}}", upstream_lines)
    body = body.replace("{{task.upstream_section}}", upstream_section)
    # Optional sections left out above leave their blank separators behind.
    return re.sub(r"\n{3,}", "\n\n", body)
