# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the sub-session id TaskTool assigns to a delegation."""

from __future__ import annotations

import re

import pytest

from openjiuwen.harness.tools.subagent.task_tool import TaskTool


def test_sub_session_id_is_stable_for_the_same_delegation() -> None:
    """A replayed call must land on the session the first call created.

    A resumed delegation arrives with the same arguments; a fresh random
    suffix would point it at a new subagent and strand the parked state.
    """
    first = TaskTool._build_sub_session_id(
        "parent_session", "code", task_description="run task"
    )
    second = TaskTool._build_sub_session_id(
        "parent_session", "code", task_description="run task"
    )

    assert first == second


def test_sub_session_id_separates_distinct_tasks() -> None:
    """Unrelated delegations of one type still get isolated sessions."""
    first = TaskTool._build_sub_session_id(
        "parent_session", "code", task_description="task one"
    )
    second = TaskTool._build_sub_session_id(
        "parent_session", "code", task_description="task two"
    )

    assert first != second


def test_sub_session_id_separates_distinct_parents() -> None:
    """Two parent sessions never share a subagent session."""
    first = TaskTool._build_sub_session_id(
        "parent_a", "code", task_description="run task"
    )
    second = TaskTool._build_sub_session_id(
        "parent_b", "code", task_description="run task"
    )

    assert first != second


def test_sub_session_id_keeps_its_established_shape() -> None:
    """The id keeps the ``<parent>_sub_<type>_<8 hex>`` form consumers match on."""
    sub_session_id = TaskTool._build_sub_session_id(
        "parent_session", "code", task_description="run task"
    )

    assert re.fullmatch(r"parent_session_sub_code_[0-9a-f]{8}", sub_session_id)


def test_sticky_sub_session_id_is_unchanged() -> None:
    """Sticky types keep the bare deterministic id they already had."""
    sub_session_id = TaskTool._build_sub_session_id(
        "parent_session", "verification_agent", task_description="run task"
    )

    assert sub_session_id == "parent_session_sub_verification_agent"


def test_missing_task_description_still_yields_a_valid_id() -> None:
    """An absent description degrades to a constant suffix, not a crash."""
    sub_session_id = TaskTool._build_sub_session_id("parent_session", "code")

    assert re.fullmatch(r"parent_session_sub_code_[0-9a-f]{8}", sub_session_id)


def test_explicit_resume_id_outranks_the_derived_suffix() -> None:
    """A caller naming its session is obeyed, whatever the description holds.

    The derived suffix and an explicitly resumed session are two answers to
    the same question, so the precedence between them has to be pinned: the
    id the caller names is returned verbatim and the description is ignored.
    """
    resume_id = "parent_session_sub_browser_agent_1234abcd"

    sub_session_id = TaskTool._build_sub_session_id(
        "parent_session",
        "browser_agent",
        resume_id,
        task_description="a description that would hash to something else",
    )

    assert sub_session_id == resume_id


def test_derived_suffix_applies_when_no_resume_id_is_given() -> None:
    """Omitting the resume id leaves the description deriving the suffix."""
    first = TaskTool._build_sub_session_id(
        "parent_session", "browser_agent", "", task_description="browse a page"
    )
    second = TaskTool._build_sub_session_id(
        "parent_session", "browser_agent", "", task_description="browse a page"
    )

    assert first == second
    assert re.fullmatch(r"parent_session_sub_browser_agent_[0-9a-f]{8}", first)


def test_resume_id_from_another_parent_is_rejected() -> None:
    """The caller cannot name a session belonging to a different parent."""
    with pytest.raises(ValueError):
        TaskTool._build_sub_session_id(
            "another_parent",
            "browser_agent",
            "parent_session_sub_browser_agent_1234abcd",
            task_description="run task",
        )
