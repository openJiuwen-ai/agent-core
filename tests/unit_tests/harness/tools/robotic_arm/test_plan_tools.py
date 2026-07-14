#!/usr/bin/env python
# coding: utf-8
"""Tests for the report_plan tool: full sub-task list validation and summarization."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.tools.robotic_arm.plan_tools import report_plan_action


@pytest.mark.asyncio
async def test_report_plan_rejects_empty_list() -> None:
    result = await report_plan_action([], ctx=None)

    assert "Error: InvalidPlan" in result


@pytest.mark.asyncio
async def test_report_plan_rejects_non_list() -> None:
    result = await report_plan_action("not-a-list", ctx=None)

    assert "Error: InvalidPlan" in result


@pytest.mark.asyncio
async def test_report_plan_rejects_invalid_status() -> None:
    sub_tasks = [{"id": "s1", "description": "pick up cup", "status": "bogus"}]

    result = await report_plan_action(sub_tasks, ctx=None)

    assert "Error: InvalidPlan" in result
    assert "status" in result


@pytest.mark.asyncio
async def test_report_plan_rejects_missing_id_or_description() -> None:
    sub_tasks = [{"id": "", "description": "pick up cup", "status": "pending"}]

    result = await report_plan_action(sub_tasks, ctx=None)

    assert "Error: InvalidPlan" in result


@pytest.mark.asyncio
async def test_report_plan_success_summarizes_sub_tasks() -> None:
    sub_tasks = [
        {"id": "s1", "description": "move cup to sink", "status": "in_progress"},
        {"id": "s2", "description": "pour water", "status": "pending"},
    ]

    result = await report_plan_action(sub_tasks, ctx=None)

    assert result.startswith("Success: plan updated.")
    assert "[s1] in_progress: move cup to sink" in result
    assert "[s2] pending: pour water" in result


@pytest.mark.asyncio
async def test_report_plan_publishes_to_ctx_extra_for_next_turn() -> None:
    sub_tasks = [{"id": "s1", "description": "pick up cup", "status": "in_progress"}]
    ctx = AgentCallbackContext(agent=MagicMock(), extra={})

    await report_plan_action(sub_tasks, ctx)

    assert "pick up cup" in ctx.extra["last_plan_summary"]
    assert ctx.extra["last_plan_sub_tasks"] == sub_tasks


@pytest.mark.asyncio
async def test_report_plan_with_none_ctx_does_not_raise() -> None:
    sub_tasks = [{"id": "s1", "description": "pick up cup", "status": "in_progress"}]

    result = await report_plan_action(sub_tasks, ctx=None)

    assert result.startswith("Success: plan updated.")
