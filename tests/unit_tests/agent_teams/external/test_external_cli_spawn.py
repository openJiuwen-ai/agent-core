# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for launching a CLI subprocess and driving it via ExternalCliRuntime."""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.external.cli_agent.spawn import (
    build_cli_runtime,
    descriptor_from_context,
)
from openjiuwen.agent_teams.external.cli_agent.codex.runtime import CodexSdkRuntime
from openjiuwen.agent_teams.external.runtime import ExternalCliRuntime, ReinvokeCliRuntime
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType
from openjiuwen.core.common.exception.errors import BaseError

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

_EVENT_WS_URL = "ws://gateway:19000/ws"


def _ctx(
    member: str = "dev-1",
    cli_agent: str = "generic",
    messager_config: MessagerTransportConfig | None = None,
    use_external_transport: bool = True,
    teammate_mode: str = "build_mode",
) -> TeamRuntimeContext:
    external_messager_config = None
    if use_external_transport:
        external_messager_config = MessagerTransportConfig(
            backend="hybrid",
            team_name="ext_team",
            external_publish_url=_EVENT_WS_URL,
        )
    return TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name=member,
        cli_agent=cli_agent,
        team_spec=TeamSpec(
            team_name="ext_team",
            display_name="Ext",
            language="en",
            teammate_mode=teammate_mode,
            external_messager_config=external_messager_config,
        ),
        db_config=DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"),
        messager_config=messager_config
        or MessagerTransportConfig(
            backend="inprocess",
            team_name="ext_team",
        ),
    )


@pytest.mark.level0
def test_descriptor_from_context_carries_identity():
    ctx = _ctx(member="dev-1")
    token = set_session_id("sess-1")
    try:
        descriptor = descriptor_from_context(ctx)
    finally:
        reset_session_id(token)
    assert descriptor.session_id == "sess-1"
    assert descriptor.team_name == "ext_team"
    assert descriptor.member_name == "dev-1"
    assert descriptor.role == "teammate"
    assert descriptor.language == "en"
    assert descriptor.teammate_mode == "build_mode"
    transport = descriptor.transport_config
    assert transport.backend == "hybrid"
    assert transport.external_publish_url == _EVENT_WS_URL
    assert transport.node_id == "dev-1"
    assert transport.direct_addr is None
    assert transport.pubsub_publish_addr is None
    assert transport.pubsub_subscribe_addr is None
    assert transport.listen_addrs == []
    assert transport.metadata == {}


@pytest.mark.level0
def test_descriptor_from_context_carries_teammate_mode():
    ctx = _ctx(member="dev-1", teammate_mode="plan_mode")
    token = set_session_id("sess-1")
    try:
        descriptor = descriptor_from_context(ctx)
    finally:
        reset_session_id(token)

    assert descriptor.teammate_mode == "plan_mode"


@pytest.mark.level0
def test_descriptor_from_context_preserves_standard_messager() -> None:
    ctx = _ctx(
        messager_config=MessagerTransportConfig(
            backend="pyzmq",
            team_name="ext_team",
            direct_addr="tcp://127.0.0.1:15555",
            pubsub_publish_addr="tcp://127.0.0.1:15556",
            pubsub_subscribe_addr="tcp://127.0.0.1:15557",
        ),
        use_external_transport=False,
    )
    token = set_session_id("sess-1")
    try:
        descriptor = descriptor_from_context(ctx)
    finally:
        reset_session_id(token)

    transport = descriptor.transport_config
    assert transport.backend == "pyzmq"
    assert transport.external_publish_url is None
    assert transport.node_id == "dev-1"
    assert transport.direct_addr == "tcp://127.0.0.1:*"
    assert transport.pubsub_publish_addr == "tcp://127.0.0.1:15556"
    assert transport.pubsub_subscribe_addr == "tcp://127.0.0.1:15557"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_build_cli_runtime_streaming_drives_a_turn():
    token = set_session_id("sess-1")
    try:
        runtime = await build_cli_runtime(
            _ctx(),
            command_override=_FAKE_CLI,
        )
    finally:
        reset_session_id(token)
    assert isinstance(runtime, ExternalCliRuntime)

    async def _drain() -> list:
        return [chunk async for chunk in runtime._drive({"query": "hello"})]

    try:
        # _drive writes the input and consumes stdout until the generic adapter
        # sees the end-of-turn marker; it must not hang. The echoed line is
        # surfaced as an output chunk.
        chunks = await asyncio.wait_for(_drain(), timeout=5.0)
        assert "echo: hello" in [c.payload["content"] for c in chunks]
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_build_cli_runtime_oneshot_reinvokes_per_turn():
    token = set_session_id("sess-1")
    try:
        runtime = await build_cli_runtime(
            _ctx(cli_agent="hermes"),
            command_override=_FAKE_ONESHOT,
        )
    finally:
        reset_session_id(token)
    assert isinstance(runtime, ReinvokeCliRuntime)

    async def _drain(query: str) -> list:
        return [chunk async for chunk in runtime._drive({"query": query})]

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
        async for chunk in runtime._drive({"query": "go"}):
            arrivals.append((chunk.payload["content"], loop.time() - start))
    finally:
        await runtime.aclose()

    assert [content for content, _ in arrivals] == ["early", "late"]
    # The first chunk must arrive while the subprocess is still running — well
    # before the ~0.4s it sleeps before its second line / exit — proving chunks
    # are bridged live through the queue, not batched at turn end.
    early_at = arrivals[0][1]
    assert early_at < 0.3, f"first chunk arrived at {early_at:.2f}s: not surfaced live"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_cli_runtime_dispatches_codex_to_sdk_backend(monkeypatch):
    class FakeCodexConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    sdk = SimpleNamespace(
        CodexConfig=FakeCodexConfig,
        AsyncCodex=object,
        ApprovalMode=SimpleNamespace(deny_all="deny-all"),
        Sandbox=SimpleNamespace(full_access="full-access"),
    )
    monkeypatch.setattr(
        "openjiuwen.agent_teams.external.cli_agent.codex.runtime.load_codex_sdk",
        lambda: sdk,
    )
    token = set_session_id("sess-1")
    try:
        runtime = await build_cli_runtime(
            _ctx(member="dev-1", cli_agent="codex"),
            cwd="/workspace",
            codex_bin="/opt/codex",
            inject_mcp=True,
            mcp_default_tools_approval_mode="approve",
            codex_bypass_approvals_and_sandbox=True,
            codex_turn_idle_timeout_s=45.0,
            codex_turn_idle_retries=2,
            system_prompt="ROLE: isolated developer",
            member_agent_id="ext_team_dev-1",
        )
    finally:
        reset_session_id(token)

    assert isinstance(runtime, CodexSdkRuntime)
    assert runtime._thread_options == {
        "ephemeral": False,
        "cwd": "/workspace",
        "developer_instructions": "ROLE: isolated developer",
        "approval_mode": "deny-all",
        "sandbox": "full-access",
    }
    assert runtime._config.kwargs["cwd"] == "/workspace"
    assert runtime._config.kwargs["codex_bin"] == "/opt/codex"
    assert (
        'mcp_servers.openjiuwen_team.default_tools_approval_mode="approve"'
        in runtime._config.kwargs["config_overrides"]
    )
    assert runtime._member_agent_id == "ext_team_dev-1"
    assert runtime._turn_idle_timeout_s == 45.0
    assert runtime._turn_idle_retries == 2


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_cli_runtime_codex_requires_stable_member_agent_id():
    token = set_session_id("sess-1")
    try:
        with pytest.raises(BaseError, match="stable member_agent_id"):
            await build_cli_runtime(
                _ctx(member="dev-1", cli_agent="codex"),
                inject_mcp=False,
                resume_external_backend=True,
            )
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_cli_runtime_codex_rejects_full_command_override():
    token = set_session_id("sess-1")
    try:
        with pytest.raises(BaseError, match="configure codex_bin instead"):
            await build_cli_runtime(
                _ctx(member="dev-1", cli_agent="codex"),
                command_override=("codex", "app-server", "--listen", "stdio://"),
                inject_mcp=False,
                member_agent_id="ext_team_dev-1",
            )
    finally:
        reset_session_id(token)
