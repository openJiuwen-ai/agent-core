# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for launching a CLI subprocess and driving it via ExternalCliRuntime."""

import asyncio
import sys

import pytest

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.external.cli_agent.spawn import (
    build_cli_runtime,
    descriptor_from_context,
)
from openjiuwen.agent_teams.external.runtime import ExternalCliRuntime, ReinvokeCliRuntime
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.tools.memory_database import MemoryDatabaseConfig

# A streaming stand-in CLI: read a line from stdin, echo it, then emit the
# generic adapter's turn-completion marker. Exercises the real subprocess +
# stdin + stdout-until-completion path without a third-party binary.
_FAKE_CLI = (
    sys.executable,
    "-u",
    "-c",
    "import sys\nfor line in sys.stdin:\n    print('echo:', line.strip())\n    print('<<END_OF_TURN>>')\n",
)

# A one-shot stand-in CLI: print the trailing argv prompt, then exit.
_FAKE_ONESHOT = (sys.executable, "-u", "-c", "import sys\nprint('oneshot:', sys.argv[-1])\n")

# A one-shot CLI that emits one line, sleeps, then emits another before exiting.
# Used to prove the re-invoke runtime surfaces chunks live during the turn (the
# first line must reach the consumer well before the process exits).
_FAKE_ONESHOT_DRIBBLE = (
    sys.executable,
    "-u",
    "-c",
    "import time\nprint('early', flush=True)\ntime.sleep(0.4)\nprint('late', flush=True)\n",
)


def _ctx(member: str = "dev-1", cli_agent: str = "generic") -> TeamRuntimeContext:
    return TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name=member,
        cli_agent=cli_agent,
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
async def test_build_cli_runtime_streaming_drives_a_turn():
    token = set_session_id("sess-1")
    try:
        runtime = await build_cli_runtime(_ctx(), command_override=_FAKE_CLI)
    finally:
        reset_session_id(token)
    assert isinstance(runtime, ExternalCliRuntime)

    async def _drain() -> list:
        return [chunk async for chunk in runtime.run_streaming({"query": "hello"}, session_id="sess-1")]

    try:
        # run_streaming writes the input and consumes stdout until the
        # generic adapter sees the end-of-turn marker; it must not hang. The
        # echoed line is surfaced as an output chunk.
        chunks = await asyncio.wait_for(_drain(), timeout=5.0)
        assert "echo: hello" in [c.payload["content"] for c in chunks]
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_build_cli_runtime_oneshot_reinvokes_per_turn():
    token = set_session_id("sess-1")
    try:
        runtime = await build_cli_runtime(_ctx(cli_agent="hermes"), command_override=_FAKE_ONESHOT)
    finally:
        reset_session_id(token)
    assert isinstance(runtime, ReinvokeCliRuntime)

    async def _drain(query: str) -> list:
        return [chunk async for chunk in runtime.run_streaming({"query": query}, session_id="sess-1")]

    try:
        # Each turn launches a fresh subprocess that prints the argv prompt
        # and exits; the turn completes at stdout EOF without hanging. The
        # per-turn output is surfaced as a chunk after the re-invocation.
        first = await asyncio.wait_for(_drain("first"), timeout=5.0)
        assert [c.payload["content"] for c in first] == ["oneshot: first"]
        second = await asyncio.wait_for(_drain("second"), timeout=5.0)
        assert [c.payload["content"] for c in second] == ["oneshot: second"]
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_reinvoke_surfaces_chunks_live_during_turn():
    token = set_session_id("sess-1")
    try:
        runtime = await build_cli_runtime(
            _ctx(cli_agent="hermes"),
            command_override=_FAKE_ONESHOT_DRIBBLE,
            inject_mcp=False,
        )
    finally:
        reset_session_id(token)
    assert isinstance(runtime, ReinvokeCliRuntime)

    loop = asyncio.get_event_loop()
    start = loop.time()
    arrivals: list = []
    try:
        async for chunk in runtime.run_streaming({"query": "go"}, session_id="sess-1"):
            arrivals.append((chunk.payload["content"], loop.time() - start))
    finally:
        await runtime.aclose()

    assert [content for content, _ in arrivals] == ["early", "late"]
    # The first chunk must arrive while the subprocess is still running — well
    # before the ~0.4s it sleeps before its second line / exit — proving chunks
    # are bridged live through the queue, not batched at turn end.
    early_at = arrivals[0][1]
    assert early_at < 0.3, f"first chunk arrived at {early_at:.2f}s: not surfaced live"
