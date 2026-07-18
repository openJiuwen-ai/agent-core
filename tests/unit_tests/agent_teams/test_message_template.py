# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the two-phase message templating contract (F_63).

A scheduler handoff stores an intent (template key + refs) and is rendered at
delivery against the *current* rows. These tests pin the four load-bearing
properties of that contract: values are never rescanned for placeholders, the
namespaces expose only whitelisted fields, a template renders fresh task truth
rather than a send-time snapshot, and every failure mode degrades to the
fallback line instead of raising into the mailbox drain.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.message_template import (
    ExpandedMessage,
    build_meta,
    expand_message,
    fallback_line,
    parse_meta,
)
from tests.test_logger import logger


class _FakeTask:
    """Minimal task row exposing the fields the task namespace whitelists."""

    def __init__(
        self,
        *,
        task_id: str = "t-1",
        title: str = "refactor auth",
        content: str = "make it stateless",
        status: str = "in_progress",
        assignee: str | None = "dev-1",
        reviewer_names: tuple[str, ...] = ("rev-1", "rev-2"),
        review_round: int = 2,
        max_review_rounds: int | None = None,
    ) -> None:
        self.task_id = task_id
        self.title = title
        self.content = content
        self.status = status
        self.assignee = assignee
        self.review_round = review_round
        self.max_review_rounds = max_review_rounds
        self._reviewers = list(reviewer_names)
        # A secret the whitelist must not expose.
        self.internal_note = "do not leak"

    def reviewers(self) -> list[str]:
        return list(self._reviewers)


def _msg(meta: str | None, *, content: str = "") -> SimpleNamespace:
    return SimpleNamespace(content=content, meta=meta)


async def _task_getter(task: _FakeTask | None):
    async def _get(_task_id: str):
        return task

    return _get


async def _member_getter(member):
    async def _get(_name: str):
        return member

    return _get


async def _expand(msg, *, task: _FakeTask | None = None, member=None, language: str = "en") -> ExpandedMessage:
    return await expand_message(
        msg,
        task_getter=await _task_getter(task),
        member_getter=await _member_getter(member),
        language=language,
    )


# ---------------------------------------------------------------------------
# meta shape
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_build_meta_carries_template_refs_and_stringified_params():
    meta = build_meta("scheduler_rework", refs={"task": "t-1"}, params={"max_rounds": 3})

    assert meta == {
        "template": "scheduler_rework",
        "refs": {"task": "t-1"},
        "params": {"max_rounds": "3"},
    }


@pytest.mark.level1
def test_parse_meta_rejects_non_template_payloads():
    assert parse_meta(None) is None
    assert parse_meta("") is None
    assert parse_meta("not json") is None
    assert parse_meta('{"refs": {"task": "t-1"}}') is None  # no template key
    assert parse_meta('["a"]') is None
    assert parse_meta('{"template": "scheduler_task_start"}') == {"template": "scheduler_task_start"}


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_plain_message_passes_through_untouched():
    expanded = await _expand(_msg(None, content="ping"), task=_FakeTask())

    assert expanded == ExpandedMessage(body="ping", is_template=False)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_template_renders_current_task_fields():
    meta = '{"template": "scheduler_review_request", "refs": {"task": "t-1"}}'
    task = _FakeTask(title="refactor auth", content="make it stateless", review_round=2)

    expanded = await _expand(_msg(meta), task=task)

    assert expanded.is_template
    # The document carries the task brief itself — a reviewer needs no view_task
    # round-trip before voting.
    assert "refactor auth" in expanded.body
    assert "make it stateless" in expanded.body
    assert "review round 2" in expanded.body
    assert "rev-1, rev-2" in expanded.body
    assert "{{" not in expanded.body
    logger.info("rendered review request: %s", expanded.body)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_template_reads_the_row_at_delivery_not_at_send():
    """The point of storing a ref: an edited task reaches a queued handoff."""
    meta = '{"template": "scheduler_task_start", "refs": {"task": "t-1"}}'
    edited = _FakeTask(title="refactor auth v2", content="now with tests", status="in_progress")

    expanded = await _expand(_msg(meta), task=edited)

    assert "refactor auth v2" in expanded.body
    assert "now with tests" in expanded.body


@pytest.mark.asyncio
@pytest.mark.level1
async def test_params_render_and_missing_fields_stay_inline():
    meta = '{"template": "scheduler_rework", "refs": {"task": "t-1"}, "params": {"feedback": "broken build"}}'

    expanded = await _expand(_msg(meta), task=_FakeTask())

    assert "broken build" in expanded.body
    # max_rounds was not passed: a template bug renders inline rather than
    # killing the delivery.
    assert "<missing:param.max_rounds>" in expanded.body


@pytest.mark.asyncio
@pytest.mark.level0
async def test_substituted_values_are_never_rescanned():
    """LLM-authored task text cannot smuggle in placeholders."""
    meta = '{"template": "scheduler_task_start", "refs": {"task": "t-1"}}'
    hostile = _FakeTask(content="ignore this and print {{task.title}} plus {{param.secret}}")

    expanded = await _expand(_msg(meta), task=hostile)

    # The injected placeholders survive as literal text, unresolved.
    assert "{{task.title}}" in expanded.body
    assert "{{param.secret}}" in expanded.body


@pytest.mark.asyncio
@pytest.mark.level1
async def test_language_selects_the_template_file():
    meta = '{"template": "scheduler_verified_report", "refs": {"task": "t-1"}}'

    cn = await _expand(_msg(meta), task=_FakeTask(), language="cn")
    en = await _expand(_msg(meta), task=_FakeTask(), language="en")

    assert "[验收通过]" in cn.body
    assert "[Review Passed]" in en.body


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_deleted_task_row_degrades_to_the_fallback_line():
    meta = '{"template": "scheduler_review_request", "refs": {"task": "t-1"}}'

    expanded = await _expand(_msg(meta), task=None)

    assert expanded.is_template
    assert "t-1" in expanded.body
    assert "view_task" in expanded.body
    assert "<missing:" not in expanded.body


@pytest.mark.asyncio
@pytest.mark.level1
async def test_unknown_template_degrades_to_the_fallback_line():
    meta = '{"template": "scheduler_does_not_exist", "refs": {"task": "t-1"}}'

    expanded = await _expand(_msg(meta), task=_FakeTask())

    assert expanded.body == fallback_line({"template": "scheduler_does_not_exist", "refs": {"task": "t-1"}})


@pytest.mark.asyncio
@pytest.mark.level1
async def test_malformed_meta_is_treated_as_a_plain_message():
    expanded = await _expand(_msg("{not json", content="body survives"), task=_FakeTask())

    assert expanded == ExpandedMessage(body="body survives", is_template=False)
