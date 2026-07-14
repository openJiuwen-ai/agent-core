# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Transport abstraction for launching and driving a CLI agent process.

A :class:`ProcessTransport` turns a launch argv into a :class:`ProcessLike`
object — the remote-process analogue of :func:`asyncio.create_subprocess_exec`.
The team's external-CLI runtime (:class:`ExternalCliRuntime`) drives whatever
the transport returns through the small :class:`ProcessLike` /
:class:`StdinLike` contract below; it never knows whether the CLI runs locally
or over ssh.

Two concrete transports live alongside this module:

* :class:`LocalTransport` (``local.py``) — wraps
  :func:`asyncio.create_subprocess_exec`, returns a native
  ``asyncio.subprocess.Process`` which already satisfies
  :class:`ProcessLike` with zero adaptation.
* :class:`SshTransport` (``ssh.py``) — drives a long-lived remote CLI over an
  ssh connection; an internal adapter shapes asyncssh's ``SSHClientProcess``
  into :class:`ProcessLike`.

This module deliberately stays transport-agnostic: it defines only the three
Protocols and imports no concrete transport. Selecting
which transport to use is the caller's job (``build_cli_runtime``), not a
factory here — there are only two transports today and the dispatch key is a
single optional ``ssh_transport`` field; a registry/factory would be
speculative until a third transport appears.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StreamReaderLike(Protocol):
    """Reader contract for transport stdout/stderr streams."""

    async def read(self, size: int) -> bytes:
        """Read up to ``size`` bytes from the stream."""
        ...

    async def readline(self) -> bytes:
        """Read one line from the stream."""
        ...


@runtime_checkable
class StdinLike(Protocol):
    """Writer contract :class:`StdinPipeInjector` depends on.

    Five methods — the local :class:`asyncio.StreamWriter` and the ssh
    transport's adapted stdin writer both satisfy it. ``can_write_eof`` is
    required: :meth:`StdinPipeInjector.aclose` calls it to decide whether to
    write EOF before closing the underlying stream.
    """

    def write(self, data: bytes) -> None:
        """Write ``data`` (bytes) to the underlying stream."""
        ...

    async def drain(self) -> None:
        """Flush the write buffer."""
        ...

    def write_eof(self) -> None:
        """Close the write half after signalling EOF."""
        ...

    def can_write_eof(self) -> bool:
        """Return whether :meth:`write_eof` is supported on this stream."""
        ...

    def close(self) -> None:
        """Close the underlying stream."""
        ...


@runtime_checkable
class ProcessLike(Protocol):
    """Process contract :class:`ExternalCliRuntime` drives, transport-agnostic.

    Mirrors the minimal subset of :class:`asyncio.subprocess.Process` the
    runtime actually touches: the stdin writer (fed to
    :class:`StdinPipeInjector`), the stdout reader (wrapped into a line
    iterator by the spawn path), ``returncode`` (premature-EOF detection),
    synchronous ``terminate`` and async ``wait`` (release on ``aclose``).

    A native ``asyncio.subprocess.Process`` satisfies this with no adaptation;
    the ssh transport wraps asyncssh's ``SSHClientProcess`` into an adapter
    that does.
    """

    @property
    def stdin(self) -> StdinLike:
        """The CLI's stdin stream writer."""
        ...

    @property
    def stdout(self) -> StreamReaderLike:
        """The CLI's stdout stream reader (spawn wraps it into line chunks)."""
        ...

    @property
    def stderr(self) -> StreamReaderLike | None:
        """The CLI's stderr stream reader."""
        ...

    @property
    def returncode(self) -> int | None:
        """Exit status, or ``None`` while the process is still running."""
        ...

    def terminate(self) -> None:
        """Signal the CLI to stop (synchronous — mirrors asyncio.Process)."""
        ...

    async def wait(self) -> int:
        """Await process exit and return its exit status."""
        ...


@runtime_checkable
class ProcessTransport(Protocol):
    """Launch a CLI argv into a :class:`ProcessLike` process object.

    Implementations that need a connection (ssh) build it lazily on the first
    ``run`` call and reuse it on subsequent calls within the same transport
    instance — so one member maps to one long-lived connection. ``aclose``
    releases that connection (no-op for local).
    """

    async def run(
        self,
        argv: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ProcessLike:
        """Launch ``argv`` with ``env`` / ``cwd`` applied; return a ProcessLike."""
        ...

    async def aclose(self) -> None:
        """Release the transport (close connection / no-op for local)."""
        ...


__all__ = ["StdinLike", "StreamReaderLike", "ProcessLike", "ProcessTransport"]
