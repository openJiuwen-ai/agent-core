# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Transport abstraction for launching and driving local CLI agent processes.

A :class:`ProcessTransport` turns a launch argv into a :class:`ProcessLike`
object, matching the subset of :func:`asyncio.create_subprocess_exec` used by
the generic external-CLI runtime. The runtime drives the returned process
through the small :class:`ProcessLike` / :class:`StdinLike` contract below.

The generic CLI backend currently supports only :class:`LocalTransport`
(``local.py``). SDK-backed agents can provide their own transport
implementations outside this package.
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

    Five methods are required so subprocess-style stdin writers can satisfy
    the runtime contract. ``can_write_eof`` is
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

    A native ``asyncio.subprocess.Process`` satisfies this with no adaptation.
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

    ``aclose`` releases transport resources. For the local transport this is a
    no-op.
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
