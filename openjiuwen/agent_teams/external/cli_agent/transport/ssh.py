# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SSH transport for running a streaming external CLI on a remote endpoint."""

from __future__ import annotations

import shlex
from typing import Any

from openjiuwen.agent_teams.external.descriptor import TEAM_JOIN_ENV
from openjiuwen.agent_teams.external.cli_agent.transport.base import ProcessLike, StdinLike, StreamReaderLike
from openjiuwen.agent_teams.schema.ssh_transport import SshTransportConfig
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger

_REMOTE_CONNECTION_LOST = 255


def _load_asyncssh() -> Any:
    """Import asyncssh only when an ssh transport is actually used."""
    try:
        import asyncssh
    except ImportError as exc:
        raise_error(
            StatusCode.AGENT_TEAM_SSH_CONNECT_ERROR,
            host="",
            reason="asyncssh is not installed; install openjiuwen with the ssh extra",
            cause=exc,
        )
        raise AssertionError from exc  # pragma: no cover - raise_error always raises
    return asyncssh


def _quote_argv(argv: tuple[str, ...]) -> str:
    """Return a shell-safe command string for the remote POSIX shell."""
    return " ".join(shlex.quote(part) for part in argv)


def _build_remote_command(argv: tuple[str, ...], *, env: dict[str, str] | None, cwd: str | None) -> str:
    """Build the remote shell command with cwd and critical env fallback."""
    command = _quote_argv(argv)
    prefixes: list[str] = []
    join_descriptor = (env or {}).get(TEAM_JOIN_ENV)
    if join_descriptor:
        prefixes.append(f"export {TEAM_JOIN_ENV}={shlex.quote(join_descriptor)}")
    if cwd:
        prefixes.append(f"cd {shlex.quote(cwd)}")
    if prefixes:
        return "; ".join(prefixes) + f"; exec {command}"
    return f"exec {command}"


class _SshStdinAdapter:
    """Adapt asyncssh's stdin writer to the StdinLike contract."""

    def __init__(self, writer: Any) -> None:
        """Bind the asyncssh writer."""
        self._writer = writer

    def write(self, data: bytes) -> None:
        """Write bytes to the remote process stdin."""
        self._writer.write(data)

    async def drain(self) -> None:
        """Flush buffered stdin data."""
        await self._writer.drain()

    def write_eof(self) -> None:
        """Send EOF to the remote stdin stream."""
        self._writer.write_eof()

    def can_write_eof(self) -> bool:
        """Return whether the remote stdin stream supports EOF."""
        can_write_eof = getattr(self._writer, "can_write_eof", None)
        if can_write_eof is None:
            return True
        return bool(can_write_eof())

    def close(self) -> None:
        """Close the remote stdin stream."""
        self._writer.close()


class _RemoteProcessAdapter(ProcessLike):
    """Adapt asyncssh's SSHClientProcess to the ProcessLike contract."""

    def __init__(self, process: Any) -> None:
        """Bind the asyncssh process."""
        self._process = process
        self._stdin = _SshStdinAdapter(process.stdin)
        self._forced_returncode: int | None = None

    @property
    def stdin(self) -> StdinLike:
        """Return the stdin writer adapter."""
        return self._stdin

    @property
    def stdout(self) -> StreamReaderLike:
        """Return the remote stdout reader."""
        return self._process.stdout

    @property
    def stderr(self) -> StreamReaderLike | None:
        """Return the remote stderr reader."""
        return self._process.stderr

    @property
    def returncode(self) -> int | None:
        """Return the mapped remote exit status."""
        if self._forced_returncode is not None:
            return self._forced_returncode
        exit_status = getattr(self._process, "exit_status", None)
        if exit_status is None:
            return None
        return int(exit_status)

    def terminate(self) -> None:
        """Signal the remote process to terminate."""
        try:
            self._process.terminate()
        except Exception as terminate_exc:
            try:
                self._process.kill()
            except Exception as kill_exc:
                team_logger.warning(
                    "[ssh-transport] failed to terminate or kill remote process: terminate={}, kill={}",
                    terminate_exc,
                    kill_exc,
                )

    async def wait(self) -> int:
        """Await remote process exit and return a local-style return code."""
        try:
            await self._process.wait()
        except Exception:
            self._forced_returncode = _REMOTE_CONNECTION_LOST
            return _REMOTE_CONNECTION_LOST
        returncode = self.returncode
        if returncode is None:
            self._forced_returncode = _REMOTE_CONNECTION_LOST
            return _REMOTE_CONNECTION_LOST
        return returncode


class SshTransport:
    """Launch a CLI process through one lazily-created ssh connection."""

    def __init__(self, config: SshTransportConfig) -> None:
        """Store ssh config; connection is built lazily on first run."""
        self._config = config
        self._asyncssh = _load_asyncssh()
        self._conn: Any | None = None

    async def run(
        self,
        argv: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ProcessLike:
        """Launch ``argv`` on the remote endpoint and return a ProcessLike."""
        conn = await self._ensure_connection()
        command = _build_remote_command(argv, env=env, cwd=cwd)
        try:
            process = await conn.create_process(
                command,
                env=env,
                encoding=None,
            )
        except Exception as exc:
            raise_error(
                StatusCode.AGENT_TEAM_SSH_EXECUTION_ERROR,
                reason=str(exc),
                cause=exc,
            )
            raise AssertionError from exc  # pragma: no cover - raise_error always raises
        if process.stdin is None or process.stdout is None:
            raise_error(
                StatusCode.AGENT_TEAM_SSH_EXECUTION_ERROR,
                reason="remote CLI did not expose stdin/stdout streams",
            )
        return _RemoteProcessAdapter(process)

    async def aclose(self) -> None:
        """Close the ssh connection idempotently."""
        if self._conn is None:
            return
        conn = self._conn
        self._conn = None
        conn.close()
        await conn.wait_closed()

    async def _ensure_connection(self) -> Any:
        """Return a live ssh connection, creating it when needed."""
        if self._conn is not None:
            return self._conn
        kwargs: dict[str, Any] = {
            "host": self._config.host,
            "port": self._config.port,
            "username": self._config.username,
            "password": self._config.password,
            "connect_timeout": self._config.connect_timeout_s,
        }
        if not self._config.agent:
            kwargs["agent_path"] = None
        if self._config.key_file:
            kwargs["client_keys"] = [self._config.key_file]
        # Omit known_hosts by default so asyncssh preserves strict default
        # checks. Passing known_hosts=None disables host-key verification.
        if self._config.disable_host_key_check:
            kwargs["known_hosts"] = None
        elif self._config.known_hosts:
            kwargs["known_hosts"] = self._config.known_hosts
        try:
            self._conn = await self._asyncssh.connect(**kwargs)
        except Exception as exc:
            raise_error(
                StatusCode.AGENT_TEAM_SSH_CONNECT_ERROR,
                host=self._config.host,
                reason=str(exc),
                cause=exc,
            )
            raise AssertionError from exc  # pragma: no cover - raise_error always raises
        return self._conn


__all__ = ["SshTransport"]
