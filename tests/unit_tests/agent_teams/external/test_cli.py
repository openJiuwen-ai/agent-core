# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the non-interactive external-member CLI."""

import pytest

from openjiuwen.agent_teams.schema.status import TaskStatus
from openjiuwen.agent_teams.skill import cli


def _args(make_descriptor, *argv: str, member: str = "dev-1", role: str = "teammate"):
    descriptor_json = make_descriptor(member=member, role=role).to_json()
    return cli.build_parser().parse_args(["--descriptor-json", descriptor_json, *argv])


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cli_members(team_db, make_descriptor, capsys):
    rc = await cli._run(_args(make_descriptor, "members"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "leader" in out
    assert "dev-1" in out


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cli_send_then_inbox(team_db, make_descriptor, capsys):
    assert await cli._run(_args(make_descriptor, "send", "leader", "hi from cli", member="dev-1")) == 0
    capsys.readouterr()

    rc = await cli._run(_args(make_descriptor, "inbox", member="leader", role="leader"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "hi from cli" in out


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cli_task_list(team_db, make_descriptor, capsys):
    await team_db.task.create_task(
        task_id="t1",
        team_name="ext_team",
        title="Build it",
        content="details",
        status=TaskStatus.PENDING.value,
    )
    rc = await cli._run(_args(make_descriptor, "task", "list"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "t1" in out
    assert "Build it" in out


@pytest.mark.asyncio
@pytest.mark.level0
async def test_cli_claim(team_db, make_descriptor, capsys):
    await team_db.task.create_task(
        task_id="t1",
        team_name="ext_team",
        title="Build it",
        content="details",
        status=TaskStatus.PENDING.value,
    )
    rc = await cli._run(_args(make_descriptor, "claim", "t1"))
    assert rc == 0
    assert "claimed t1" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.level1
async def test_cli_inbox_empty(team_db, make_descriptor, capsys):
    rc = await cli._run(_args(make_descriptor, "inbox"))
    assert rc == 0
    assert "(inbox empty)" in capsys.readouterr().out
