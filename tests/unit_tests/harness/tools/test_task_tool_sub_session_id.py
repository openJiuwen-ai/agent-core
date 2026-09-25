# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for the sub-session id TaskTool assigns to a delegation."""

from __future__ import annotations

import re

import pytest

from openjiuwen.harness.tools.subagent.task_tool import TaskTool


def test_sub_session_id_is_stable_for_the_same_tool_call() -> None:
    """A replayed call must land on the session the first call created.

    A resumed delegation is replayed under the tool call id the model's
    original call carried; a fresh random suffix would point it at a new
    subagent and strand the parked state.
    """
    first = TaskTool._build_sub_session_id(
        "parent_session", "code", tool_call_id="call_1"
    )
    second = TaskTool._build_sub_session_id(
        "parent_session", "code", tool_call_id="call_1"
    )

    assert first == second


def test_sub_session_id_separates_distinct_tool_calls() -> None:
    """Two delegations are two calls and keep isolated sessions.

    That holds even when both carry the same task description, which is what
    the tool call id buys over deriving the suffix from the description.
    """
    first = TaskTool._build_sub_session_id(
        "parent_session", "code", tool_call_id="call_1"
    )
    second = TaskTool._build_sub_session_id(
        "parent_session", "code", tool_call_id="call_2"
    )

    assert first != second


def test_sub_session_id_separates_distinct_parents() -> None:
    """Two parent sessions never share a subagent session."""
    first = TaskTool._build_sub_session_id("parent_a", "code", tool_call_id="call_1")
    second = TaskTool._build_sub_session_id("parent_b", "code", tool_call_id="call_1")

    assert first != second


def test_sub_session_id_keeps_its_established_shape() -> None:
    """The id keeps the ``<parent>_sub_<type>_<8 hex>`` form consumers match on."""
    sub_session_id = TaskTool._build_sub_session_id(
        "parent_session", "code", tool_call_id="call_1"
    )

    assert re.fullmatch(r"parent_session_sub_code_[0-9a-f]{8}", sub_session_id)


def test_sticky_sub_session_id_is_unchanged() -> None:
    """Sticky types keep the bare deterministic id they already had."""
    sub_session_id = TaskTool._build_sub_session_id(
        "parent_session", "verification_agent", tool_call_id="call_1"
    )

    assert sub_session_id == "parent_session_sub_verification_agent"


def test_missing_tool_call_id_keeps_the_established_random_suffix() -> None:
    """A call the ability manager did not dispatch degrades to a random suffix.

    Such a call has no id to be replayed under, so there is nothing to make
    reproducible; two of them must still not collide.
    """
    first = TaskTool._build_sub_session_id("parent_session", "code")
    second = TaskTool._build_sub_session_id("parent_session", "code")

    assert re.fullmatch(r"parent_session_sub_code_[0-9a-f]{8}", first)
    assert first != second


def test_explicit_resume_id_outranks_the_derived_suffix() -> None:
    """A caller naming its session is obeyed, whatever call id is supplied.

    The derived suffix and an explicitly resumed session are two answers to
    the same question, so the precedence between them has to be pinned: the
    id the caller names is returned verbatim and the call id is ignored.
    """
    resume_id = "parent_session_sub_browser_agent_1234abcd"

    sub_session_id = TaskTool._build_sub_session_id(
        "parent_session",
        "browser_agent",
        resume_id,
        tool_call_id="a call id that would hash to something else",
    )

    assert sub_session_id == resume_id


def test_derived_suffix_applies_when_no_resume_id_is_given() -> None:
    """Omitting the resume id leaves the call id deriving the suffix."""
    first = TaskTool._build_sub_session_id(
        "parent_session", "browser_agent", "", tool_call_id="call_1"
    )
    second = TaskTool._build_sub_session_id(
        "parent_session", "browser_agent", "", tool_call_id="call_1"
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
            tool_call_id="call_1",
        )
