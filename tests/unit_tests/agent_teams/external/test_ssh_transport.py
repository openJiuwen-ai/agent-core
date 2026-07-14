# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ssh/local process transports used by external CLI members."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import openjiuwen.agent_teams.external.cli_agent.transport.ssh as ssh_mod
from openjiuwen.agent_teams.external.descriptor import TEAM_JOIN_ENV
from openjiuwen.agent_teams.external.cli_agent.transport import LocalTransport
from openjiuwen.agent_teams.external.cli_agent.transport.base import StdinLike
from openjiuwen.agent_teams.external.cli_agent.transport.ssh import SshTransport
from openjiuwen.agent_teams.schema.ssh_transport import SshTransportConfig
from openjiuwen.core.common.exception.errors import BaseError


@pytest.mark.level0
def test_ssh_transport_config_requires_auth():
    with pytest.raises(BaseError):
        SshTransportConfig(host="127.0.0.1")


@pytest.mark.level0
def test_ssh_transport_config_accepts_auth_methods():
    config = SshTransportConfig(host="127.0.0.1", key_file="id_rsa", password="pw", agent=True)

    assert config.key_file == "id_rsa"
    assert config.password == "pw"
    assert config.agent
    assert not config.disable_host_key_check
    assert config.known_hosts is None


class _FakeWriter:
    def __init__(self) -> None:
        self.data: list[bytes] = []
        self.eof = False
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.append(data)

    async def drain(self) -> None:
        return None

    def write_eof(self) -> None:
        self.eof = True

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = _FakeWriter()
        self.stdout = object()
        self.stderr = object()
        self.exit_status: int | None = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> None:
        self.exit_status = 0


class _FakeBrokenProcess(_FakeProcess):
    def __init__(self, *, stdin: object | None = None, stdout: object | None = None) -> None:
        super().__init__()
        self.stdin = stdin
        self.stdout = stdout


class _FakeUnkillableProcess(_FakeProcess):
    def terminate(self) -> None:
        raise RuntimeError("terminate failed")

    def kill(self) -> None:
        raise RuntimeError("kill failed")


class _FakeConnection:
    def __init__(self) -> None:
        self.commands: list[dict] = []
        self.closed = False

    async def create_process(self, command: str, **kwargs) -> _FakeProcess:
        self.commands.append({"command": command, **kwargs})
        return _FakeProcess()

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _FakeAsyncSsh:
    def __init__(self) -> None:
        self.connect = AsyncMock()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_ssh_transport_uses_create_process_and_reuses_connection(monkeypatch):
    fake_conn = _FakeConnection()
    fake_asyncssh = _FakeAsyncSsh()
    fake_asyncssh.connect.return_value = fake_conn
    monkeypatch.setattr(ssh_mod, "_load_asyncssh", lambda: fake_asyncssh)
    config = SshTransportConfig(host="host", username="u", password="pw", disable_host_key_check=True)
    transport = SshTransport(config)

    process = await transport.run(
        ("claude", "--flag value"),
        env={TEAM_JOIN_ENV: '{"session_id":"s"}'},
        cwd="/remote work",
    )
    await transport.run(("claude",), env={}, cwd=None)

    fake_asyncssh.connect.assert_awaited_once()
    assert len(fake_conn.commands) == 2
    first = fake_conn.commands[0]
    assert first["encoding"] is None
    assert first["env"][TEAM_JOIN_ENV] == '{"session_id":"s"}'
    assert "export OPENJIUWEN_TEAM_JOIN=" in first["command"]
    assert "cd '/remote work'" in first["command"]
    assert "'--flag value'" in first["command"]
    assert isinstance(process.stdin, StdinLike)

    await transport.aclose()
    assert fake_conn.closed


@pytest.mark.asyncio
@pytest.mark.level0
async def test_ssh_transport_rejects_process_without_required_streams(monkeypatch):
    fake_conn = _FakeConnection()
    fake_conn.create_process = AsyncMock(return_value=_FakeBrokenProcess(stdin=None, stdout=object()))
    fake_asyncssh = _FakeAsyncSsh()
    fake_asyncssh.connect.return_value = fake_conn
    monkeypatch.setattr(ssh_mod, "_load_asyncssh", lambda: fake_asyncssh)
    config = SshTransportConfig(host="host", username="u", password="pw")
    transport = SshTransport(config)

    with pytest.raises(BaseError):
        await transport.run(("claude",), env={}, cwd=None)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_ssh_process_terminate_suppresses_kill_failure(monkeypatch):
    fake_conn = _FakeConnection()
    fake_conn.create_process = AsyncMock(return_value=_FakeUnkillableProcess())
    fake_asyncssh = _FakeAsyncSsh()
    fake_asyncssh.connect.return_value = fake_conn
    monkeypatch.setattr(ssh_mod, "_load_asyncssh", lambda: fake_asyncssh)
    config = SshTransportConfig(host="host", username="u", password="pw")
    transport = SshTransport(config)
    process = await transport.run(("claude",), env={}, cwd=None)

    process.terminate()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_ssh_transport_known_hosts_default_is_not_passed(monkeypatch):
    fake_asyncssh = _FakeAsyncSsh()
    fake_asyncssh.connect.return_value = _FakeConnection()
    monkeypatch.setattr(ssh_mod, "_load_asyncssh", lambda: fake_asyncssh)
    config = SshTransportConfig(host="host", username="u", password="pw")
    transport = SshTransport(config)

    await transport.run(("claude",), env={}, cwd=None)

    kwargs = fake_asyncssh.connect.await_args.kwargs
    assert "known_hosts" not in kwargs
    assert kwargs["agent_path"] is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_ssh_transport_disable_host_key_check_passes_none(monkeypatch):
    fake_asyncssh = _FakeAsyncSsh()
    fake_asyncssh.connect.return_value = _FakeConnection()
    monkeypatch.setattr(ssh_mod, "_load_asyncssh", lambda: fake_asyncssh)
    config = SshTransportConfig(host="host", username="u", password="pw", disable_host_key_check=True)
    transport = SshTransport(config)

    await transport.run(("claude",), env={}, cwd=None)

    kwargs = fake_asyncssh.connect.await_args.kwargs
    assert kwargs["known_hosts"] is None


@pytest.mark.level0
def test_local_transport_is_exported():
    assert LocalTransport.__name__ == "LocalTransport"
