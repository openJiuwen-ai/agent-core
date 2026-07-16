# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Claude Agent SDK transport that starts Claude through SSH."""

from __future__ import annotations

import json
import shlex
from collections.abc import AsyncIterator
from typing import Any

from openjiuwen.agent_teams.external.cli_agent.claude.options import load_claude_sdk
from openjiuwen.agent_teams.external.descriptor import TEAM_JOIN_ENV
from openjiuwen.agent_teams.schema.ssh_transport import SshTransportConfig
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger

_REMOTE_CONNECTION_LOST = 255


def _load_asyncssh() -> Any:
    """Import asyncssh only when the SSH transport is used."""
    try:
        import asyncssh
    except ImportError as exc:
        raise_error(
            StatusCode.AGENT_TEAM_SSH_CONNECT_ERROR,
            host="",
            reason="asyncssh is not installed; install openjiuwen with the ssh extra",
            cause=exc,
        )
        raise AssertionError("raise_error should have raised") from exc
    return asyncssh


def _quote_argv(argv: list[str]) -> str:
    """Return a shell-safe command string for the remote POSIX shell."""
    return " ".join(shlex.quote(part) for part in argv)


def _build_remote_command(argv: list[str], *, env: dict[str, str], cwd: str | None) -> str:
    """Build a remote shell command with cwd and team descriptor fallback."""
    command = _quote_argv(argv)
    prefixes: list[str] = []
    join_descriptor = env.get(TEAM_JOIN_ENV)
    if join_descriptor:
        prefixes.append(f"export {TEAM_JOIN_ENV}={shlex.quote(join_descriptor)}")
    if cwd:
        prefixes.append(f"cd {shlex.quote(cwd)}")
    if prefixes:
        return "; ".join(prefixes) + f"; exec {command}"
    return f"exec {command}"


def build_claude_sdk_ssh_transport(
    *,
    prompt: Any,
    options: Any,
    config: SshTransportConfig,
) -> Any:
    """Build a Claude SDK SSH transport after the optional SDK is available."""
    load_claude_sdk()
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    class ClaudeSdkSshTransport(SubprocessCLITransport):  # type: ignore[misc]
        """Run the SDK-built Claude command on an SSH endpoint."""

        def __init__(
            self,
            *,
            transport_prompt: Any,
            transport_options: Any,
            transport_config: SshTransportConfig,
        ):
            """Store SSH config and force command resolution to happen remotely."""
            super().__init__(prompt=transport_prompt, options=transport_options)
            self._config = transport_config
            self._asyncssh = _load_asyncssh()
            self._conn: Any | None = None

        async def connect(self) -> None:
            """Connect to SSH and start the SDK-built Claude command remotely."""
            if self._process:
                return
            self._cli_path = str(self._options.cli_path) if self._options.cli_path is not None else "claude"
            cmd = self._build_command()
            # Mirror SDK subprocess env semantics while replacing only the process launcher.
            process_env = {
                "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
                **self._options.env,
                "CLAUDE_AGENT_SDK_VERSION": _sdk_version(),
            }
            command = _build_remote_command(cmd, env=process_env, cwd=self._cwd)
            conn = await self._ensure_connection()
            try:
                self._process = await conn.create_process(command, env=process_env, encoding=None)
                self._ready = True
            except Exception as exc:
                raise_error(
                    StatusCode.AGENT_TEAM_SSH_EXECUTION_ERROR,
                    reason=str(exc),
                    cause=exc,
                )
                raise AssertionError("raise_error should have raised") from exc

        async def write(self, data: str) -> None:
            """Write raw SDK protocol data to remote Claude stdin."""
            if not self._ready or self._process is None:
                sdk = load_claude_sdk()
                raise sdk.CLIConnectionError("Claude SDK SSH transport is not ready for writing")
            self._process.stdin.write(data.encode("utf-8"))
            await self._process.stdin.drain()

        def read_messages(self) -> AsyncIterator[dict[str, Any]]:
            """Read JSON SDK protocol messages from remote Claude stdout."""
            return self._read_messages_impl()

        async def _read_messages_impl(self) -> AsyncIterator[dict[str, Any]]:
            """Yield parsed JSON messages until remote stdout closes."""
            process = self._process
            if process is None:
                sdk = load_claude_sdk()
                raise sdk.CLIConnectionError("Claude SDK SSH transport is not connected")
            json_buffer = ""
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if not json_buffer and not line.startswith("{"):
                    continue
                json_buffer += line
                try:
                    message = json.loads(json_buffer)
                except json.JSONDecodeError:
                    continue
                json_buffer = ""
                if isinstance(message, dict):
                    yield message
            returncode = await self._wait_process(process)
            if returncode not in (0, None):
                sdk = load_claude_sdk()
                raise sdk.ProcessError(
                    f"Remote Claude command failed with exit code {returncode}",
                    exit_code=returncode,
                    stderr="Check remote stderr output for details",
                )

        async def close(self) -> None:
            """Close remote Claude and the SSH connection."""
            self._ready = False
            process = self._process
            self._process = None
            if process is not None:
                try:
                    process.stdin.write_eof()
                except Exception as exc:
                    team_logger.warning("Failed to close remote Claude stdin: {}", exc)
                if process.exit_status is None:
                    try:
                        process.terminate()
                    except Exception:
                        process.kill()
                await self._wait_process(process)
            if self._conn is not None:
                conn = self._conn
                self._conn = None
                conn.close()
                await conn.wait_closed()

        def is_ready(self) -> bool:
            """Return whether the remote process is ready for protocol writes."""
            return bool(self._ready and self._process is not None)

        async def end_input(self) -> None:
            """Close the remote process stdin."""
            if self._process is not None:
                self._process.stdin.write_eof()

        async def _ensure_connection(self) -> Any:
            """Return a live SSH connection, creating it when needed."""
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
                raise AssertionError("raise_error should have raised") from exc
            return self._conn

        async def _wait_process(self, process: Any | None = None) -> int:
            """Wait for the remote process and map missing status to a failure code."""
            target = process if process is not None else self._process
            if target is None:
                return 0
            try:
                await target.wait()
            except Exception as exc:
                team_logger.warning("Failed to wait for remote Claude process: {}", exc)
                return _REMOTE_CONNECTION_LOST
            exit_status = getattr(target, "exit_status", None)
            if exit_status is None:
                return _REMOTE_CONNECTION_LOST
            return int(exit_status)

    return ClaudeSdkSshTransport(
        transport_prompt=prompt,
        transport_options=options,
        transport_config=config,
    )


def _sdk_version() -> str:
    """Return the installed Claude Agent SDK version."""
    sdk = load_claude_sdk()
    return str(sdk.__version__)


__all__ = ["build_claude_sdk_ssh_transport"]
