# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the non-interactive external-member CLI (member + operator scopes)."""

import pytest

from openjiuwen.agent_teams.schema.status import TaskStatus
from openjiuwen.agent_teams.skill import cli


def _member(make_descriptor, *argv: str, member: str = "dev-1", role: str = "teammate"):
    """Build a (descriptor, args) pair for the member-scope CLI."""
    descriptor = make_descriptor(member=member, role=role, scope="member")
    args = cli._build_member_parser().parse_args(list(argv))
    return descriptor, args


def _operator(make_descriptor, *argv: str, member: str = "op", role: str = "leader"):
    """Build a (descriptor, args) pair for the operator-scope CLI."""
    descriptor = make_descriptor(member=member, role=role, scope="operator")
    args = cli._build_operator_parser().parse_args(list(argv))
    return descriptor, args


# ---- member scope ---------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level1
async def test_cli_member_inbox_empty(team_db, make_descriptor, capsys):
    rc = await cli._run(*_member(make_descriptor, "inbox"))
    assert rc == 0
    assert "(inbox empty)" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cli_member_send_then_inbox(team_db, make_descriptor, capsys):
    assert await cli._run(*_member(make_descriptor, "send_message", "leader", "hi from cli")) == 0
    capsys.readouterr()

    rc = await cli._run(*_member(make_descriptor, "inbox", member="leader", role="leader"))
    assert rc == 0
    assert "hi from cli" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cli_member_view_task(team_db, make_descriptor, capsys):
    await team_db.task.create_task(
        task_id="t1",
        team_name="ext_team",
        title="Build it",
        content="details",
        status=TaskStatus.PENDING.value,
    )
    rc = await cli._run(*_member(make_descriptor, "view_task", "--action", "list"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "t1" in out
    assert "Build it" in out


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cli_member_claim_then_complete(team_db, make_descriptor, capsys):
    await team_db.task.create_task(
        task_id="t1",
        team_name="ext_team",
        title="Build it",
        content="details",
        status=TaskStatus.PENDING.value,
    )
    assert await cli._run(*_member(make_descriptor, "claim_task", "t1", "claimed")) == 0
    assert "t1" in capsys.readouterr().out
    assert (await team_db.task.get_task("t1")).status == TaskStatus.IN_PROGRESS.value

    assert await cli._run(*_member(make_descriptor, "claim_task", "t1", "completed")) == 0
    capsys.readouterr()
    assert (await team_db.task.get_task("t1")).status == TaskStatus.COMPLETED.value


# ---- operator scope -------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cli_operator_members(team_db, make_descriptor, capsys):
    rc = await cli._run(*_operator(make_descriptor, "members"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "leader" in out
    assert "dev-1" in out


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cli_operator_create_task(team_db, make_descriptor, capsys):
    rc = await cli._run(*_operator(make_descriptor, "create_task", "Ship it", "do the thing"))
    assert rc == 0
    assert "created" in capsys.readouterr().out
    tasks = await team_db.task.get_team_tasks(team_name="ext_team")
    assert any(t.title == "Ship it" for t in tasks)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_cli_operator_task_list(team_db, make_descriptor, capsys):
    await team_db.task.create_task(
        task_id="t1",
        team_name="ext_team",
        title="Build it",
        content="details",
        status=TaskStatus.PENDING.value,
    )
    rc = await cli._run(*_operator(make_descriptor, "task", "list"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "t1" in out
    assert "Build it" in out
