# -*- coding: utf-8 -*-
"""Unit tests for LSPClient crash reporting and pending-request cleanup.

Covers:
  - _read_loop reporting a crash on stdout EOF (server exited) so pending
    futures fail fast with ConnectionError instead of waiting for the
    request timeout.
  - _rpc_request removing the pending entry when a response arrives after
    the request timed out (no unbounded _pending growth).
  - stop() not triggering the crash callback during graceful shutdown.
  - path_to_file_uri percent-encoding special characters on Windows.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from openjiuwen.harness.lsp.core.client import LSPClient


def _make_config(workspace_folder: str = "/ws") -> Any:
    from openjiuwen.harness.lsp.core.types import ScopedLspServerConfig

    return ScopedLspServerConfig(
        server_id="test-server",
        command=sys.executable,
        args=["-c", "pass"],
        workspace_folder=workspace_folder,
    )


def _make_process(
    stdout_eof: bool = True,
) -> Any:
    """Build a fake asyncio subprocess whose stdout immediately hits EOF."""
    proc: Any = {}
    proc["returncode"] = None

    async def _readline() -> bytes:
        await asyncio.sleep(0)
        return b""

    async def _wait() -> int:
        proc["returncode"] = 1
        return 1

    async def _drain() -> None:
        return None

    class _Stream:
        async def readline(self) -> bytes:
            return await _readline()

        def write(self, _data: bytes) -> None:
            return None

        async def drain(self) -> None:
            await _drain()

    class _Process:
        returncode = None
        stdout = _Stream()
        stdin = _Stream()
        stderr = _Stream()

        async def wait(self) -> int:
            return await _wait()

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    process = _Process()
    return process


@pytest.mark.asyncio
async def test_read_loop_reports_crash_on_eof():
    """stdout EOF (server exited) must fire the crash callback."""
    process = _make_process()
    exits: list[int | None] = []
    client = LSPClient(
        config=_make_config(),
        process=process,
        on_exit=lambda code: exits.append(code),
    )
    reader = asyncio.create_task(client._read_loop())
    await asyncio.wait_for(reader, timeout=2.0)
    await asyncio.sleep(0)
    assert exits, "EOF on stdout must trigger the on_exit callback"
    assert client._crash_reported is True


@pytest.mark.asyncio
async def test_crash_fails_pending_futures_immediately():
    """Pending requests must fail with ConnectionError on EOF crash."""
    process = _make_process()
    client = LSPClient(
        config=_make_config(),
        process=process,
        on_exit=lambda code: None,
    )
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()
    client._pending["1"] = (future, "textDocument/hover")

    reader = asyncio.create_task(client._read_loop())
    await asyncio.wait_for(reader, timeout=2.0)

    with pytest.raises(ConnectionError):
        await asyncio.wait_for(future, timeout=1.0)


@pytest.mark.asyncio
async def test_dispatch_pops_timed_out_pending_entry():
    """A late response for a timed-out request must not leak the entry."""
    process = _make_process()
    client = LSPClient(
        config=_make_config(),
        process=process,
        on_exit=lambda code: None,
    )
    from openjiuwen.harness.lsp.core.client import LSPError

    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()
    future.set_exception(LSPError(-1, "Request timeout"))
    client._pending["5"] = (future, "textDocument/definition")

    client._dispatch({"jsonrpc": "2.0", "id": "5", "result": {}})

    assert "5" not in client._pending, "late response must remove the pending entry"


@pytest.mark.asyncio
async def test_rpc_request_timeout_removes_pending_entry():
    """on_timeout must drop the pending entry so it cannot accumulate."""
    from unittest.mock import patch

    from openjiuwen.harness.lsp.core.client import LSPError

    process = _make_process()
    client = LSPClient(
        config=_make_config(),
        process=process,
        on_exit=lambda code: None,
    )

    # Patch the default timeout down to a test-friendly value and make stdin
    # writes silent; the future then times out through the on_timeout hook.
    with patch("openjiuwen.harness.lsp.core.client.DEFAULT_REQUEST_TIMEOUT_MS", 100):
        with patch.object(process.stdin, "write", lambda data: None):
            with pytest.raises(LSPError):
                await asyncio.wait_for(
                    client._rpc_request("textDocument/hover", {}), timeout=10.0
                )
    # After the timeout the pending map must be empty again.
    assert not client._pending


@pytest.mark.asyncio
async def test_graceful_stop_does_not_report_crash():
    """stop() must not fire the crash callback (reader is cancelled first)."""
    process = _make_process()
    exits: list[int | None] = []
    client = LSPClient(
        config=_make_config(),
        process=process,
        on_exit=lambda code: exits.append(code),
    )
    # Reader task is never started; stop() tolerates that.
    await client.stop()
    assert exits == [], "graceful stop must not fire the crash callback"


class TestPathToFileUri:
    def test_windows_encodes_special_characters(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        from openjiuwen.harness.lsp.core.utils.file_uri import path_to_file_uri

        uri = path_to_file_uri(r"C:\Users\John Doe\proj#1\a b.py")
        assert uri.startswith("file:///")
        # Spaces and '#' must be percent-encoded, not passed through.
        assert " " not in uri
        assert "#" not in uri
        assert "%20" in uri

    def test_windows_plain_path_unchanged(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        from openjiuwen.harness.lsp.core.utils.file_uri import path_to_file_uri

        uri = path_to_file_uri(r"C:\work\proj\main.py")
        assert uri == "file:///C:/work/proj/main.py"

    def test_roundtrip_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        from openjiuwen.harness.lsp.core.utils.file_uri import (
            file_uri_to_path,
            path_to_file_uri,
        )

        original = r"C:\Users\John Doe\proj#1\a b.py"
        uri = path_to_file_uri(original)
        assert file_uri_to_path(uri).lower() == original.lower()
