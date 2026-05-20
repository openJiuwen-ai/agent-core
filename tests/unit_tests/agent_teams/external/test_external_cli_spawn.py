# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for launching a CLI subprocess and driving it via ExternalCliRuntime."""

import asyncio
import sys

import pytest

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.external.cli_agent.spawn import (
    descriptor_from_context,
    launch_external_cli,
)
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.tools.memory_database import MemoryDatabaseConfig

# A stand-in CLI: read a line from stdin, echo it, then emit the generic
# adapter's turn-completion marker. Exercises the real subprocess + stdin +
# stdout-until-completion path without depending on a third-party binary.
_FAKE_CLI = (
    sys.executable,
    "-u",
    "-c",
    "import sys\nfor line in sys.stdin:\n    print('echo:', line.strip())\n    print('<<END_OF_TURN>>')\n",
)


def _ctx(member: str = "dev-1") -> TeamRuntimeContext:
    return TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name=member,
        cli_agent="generic",
        team_spec=TeamSpec(team_name="ext_team", display_name="Ext", language="en"),
        db_config=MemoryDatabaseConfig(),
        messager_config=MessagerTransportConfig(backend="inprocess", team_name="ext_team"),
    )


@pytest.mark.level0
def test_descriptor_from_context_carries_identity():
    token = set_session_id("sess-1")
    try:
        descriptor = descriptor_from_context(_ctx(member="dev-1"))
    finally:
        reset_session_id(token)
    assert descriptor.session_id == "sess-1"
    assert descriptor.team_name == "ext_team"
    assert descriptor.member_name == "dev-1"
    assert descriptor.role == "teammate"
    assert descriptor.language == "en"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_launch_external_cli_drives_a_turn():
    token = set_session_id("sess-1")
    try:
        runtime, process = await launch_external_cli(_ctx(), command_override=_FAKE_CLI)
    finally:
        reset_session_id(token)

    async def _drain() -> list:
        return [chunk async for chunk in runtime.run_streaming({"query": "hello"}, session_id="sess-1")]

    try:
        # run_streaming writes the input and consumes stdout until the
        # generic adapter sees the end-of-turn marker; it must not hang.
        chunks = await asyncio.wait_for(_drain(), timeout=5.0)
        assert chunks == []
    finally:
        await runtime.aclose()
        if process.returncode is None:
            process.terminate()
        await process.wait()
