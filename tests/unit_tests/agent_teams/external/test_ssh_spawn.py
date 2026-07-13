# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ssh transport integration in external CLI spawn."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.external.cli_agent import spawn as spawn_mod
from openjiuwen.agent_teams.external.cli_agent.adapters import build_adapter
from openjiuwen.agent_teams.external.cli_agent.transport.base import ProcessLike
from openjiuwen.agent_teams.external.runtime import ExternalCliRuntime
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.schema.ssh_transport import SshTransportConfig
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.tools.memory_database import MemoryDatabaseConfig
from openjiuwen.core.common.exception.errors import BaseError


class _FakeStdin:
    def write(self, data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None

    def write_eof(self) -> None:
        return None

    def can_write_eof(self) -> bool:
        return True

    def close(self) -> None:
        return None


class _FakeReader:
    async def readline(self) -> bytes:
        return b""

    async def read(self, size: int) -> bytes:
        return b""


class _FakeProcess(ProcessLike):
    def __init__(self) -> None:
        self._stdin = _FakeStdin()
        self._stdout = _FakeReader()
        self._stderr = _FakeReader()
        self.terminated = False

    @property
    def stdin(self) -> _FakeStdin:
        return self._stdin

    @property
    def stdout(self) -> _FakeReader:
        return self._stdout

    @property
    def stderr(self) -> _FakeReader:
        return self._stderr

    @property
    def returncode(self) -> int | None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    async def wait(self) -> int:
        return 0


class _RecordingTransport:
    def __init__(self, *_args, **_kwargs) -> None:
        self.runs: list[dict] = []
        self.closed = False

    async def run(
        self,
        argv: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ProcessLike:
        self.runs.append({"argv": argv, "env": env, "cwd": cwd})
        return _FakeProcess()

    async def aclose(self) -> None:
        self.closed = True


def _ctx(member: str = "dev-1", cli_agent: str = "claude") -> TeamRuntimeContext:
    return TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name=member,
        cli_agent=cli_agent,
        team_spec=TeamSpec(team_name="ext_team", display_name="Ext", language="en"),
        db_config=MemoryDatabaseConfig(),
        messager_config=MessagerTransportConfig(backend="inprocess", team_name="ext_team"),
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_cli_runtime_ssh_uses_transport_and_keeps_mcp_args(monkeypatch):
    created: list[_RecordingTransport] = []

    def _factory(_config: SshTransportConfig) -> _RecordingTransport:
        transport = _RecordingTransport()
        created.append(transport)
        return transport

    monkeypatch.setattr(spawn_mod, "SshTransport", _factory)
    config = SshTransportConfig(host="host", username="u", password="pw")
    token = set_session_id("sess-1")
    try:
        runtime = await spawn_mod.build_cli_runtime(
            _ctx(),
            ssh_transport=config,
            mcp_server_command=("remote-openjiuwen-team-mcp",),
            extra_env={"REMOTE_ONLY": "1"},
        )
    finally:
        reset_session_id(token)

    assert isinstance(runtime, ExternalCliRuntime)
    assert len(created) == 1
    run = created[0].runs[0]
    assert "--mcp-config" in run["argv"]
    assert "OPENJIUWEN_TEAM_JOIN" in run["env"]
    assert run["env"]["REMOTE_ONLY"] == "1"
    assert "PATH" not in run["env"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_cli_runtime_ssh_rejects_oneshot_cli(monkeypatch):
    monkeypatch.setattr(spawn_mod, "SshTransport", lambda _config: _RecordingTransport())
    config = SshTransportConfig(host="host", username="u", password="pw")

    with pytest.raises(BaseError):
        await spawn_mod.build_cli_runtime(_ctx(cli_agent="hermes"), ssh_transport=config)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_external_runtime_closes_transport():
    transport = _RecordingTransport()
    runtime = ExternalCliRuntime(
        member_name="dev-1",
        adapter=build_adapter("generic"),
        injector=spawn_mod.StdinPipeInjector(_FakeStdin()),
        output_lines=spawn_mod._aiter_stdout(_FakeReader()),
        process=_FakeProcess(),
        transport=transport,
    )

    await runtime.aclose()

    assert transport.closed
