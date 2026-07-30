# -*- coding: UTF-8 -*-
"""Tests for StdioClient disconnect bug fix.

Verifies the owner-task actor pattern prevents:
1. Half-enter state leak when connect is cancelled (Bug 1)
2. Double exit of anyio context in disconnect (Bug 2)
3. Exit stack not reset → reconnect fails (Bug 3)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.stdio_client import StdioClient


def _make_config() -> McpServerConfig:
    return McpServerConfig(
        server_id="test-srv",
        server_name="test-server",
        server_path="",
        client_type="stdio",
        params={"command": "python", "args": ["-m", "test"]},
    )


def _make_fake_async_cm(name: str, hang_on_exit: bool = False):
    """Create an async context manager instance with tracking.

    Returns (instance, class) — the class must be used for patching because
    Python looks up special methods (__aenter__/__aexit__) on the class, not
    the instance.
    """
    class FakeAsyncCM:
        enter_count = 0
        exit_count = 0
        entered = False
        exited = False

        async def __aenter__(self_inner):
            FakeAsyncCM.enter_count += 1
            FakeAsyncCM.entered = True
            return f"{name}_read", f"{name}_write"

        async def __aexit__(self_inner, exc_type, exc_val, exc_tb):
            FakeAsyncCM.exit_count += 1
            if hang_on_exit:
                await asyncio.sleep(100)
            FakeAsyncCM.exited = True
            FakeAsyncCM.entered = False
            return False

    return FakeAsyncCM(), FakeAsyncCM


def _make_fake_session_cm(session_obj):
    """Create a session async context manager that returns session_obj from __aenter__."""
    class FakeSessionCM:
        async def __aenter__(self_inner):
            return session_obj

        async def __aexit__(self_inner, exc_type, exc_val, exc_tb):
            return False

    return FakeSessionCM()


@pytest.mark.asyncio
async def test_normal_connect_disconnect_lifecycle() -> None:
    """Normal connect → disconnect should cleanly enter and exit contexts."""
    config = _make_config()
    client = StdioClient(config)

    fake_stdio, FakeStdio = _make_fake_async_cm("stdio")
    fake_session_obj = MagicMock()
    fake_session_obj.initialize = AsyncMock()
    fake_session_cm = _make_fake_session_cm(fake_session_obj)

    with patch("mcp.client.stdio.stdio_client", return_value=fake_stdio), \
         patch("mcp.ClientSession", return_value=fake_session_cm):

        result = await client.connect(timeout=5.0)
        assert result is True
        assert client._session is not None
        assert FakeStdio.enter_count == 1

        result = await client.disconnect(timeout=5.0)
        assert result is True
        assert FakeStdio.exited is True
        assert FakeStdio.exit_count == 1  # No double exit


@pytest.mark.asyncio
async def test_connect_cancelled_no_half_enter_leak() -> None:
    """When connect is cancelled, owner task must clean up half-entered contexts.

    This tests Bug 1: before the fix, connect() used `except Exception` which
    doesn't catch CancelledError (it inherits from BaseException in Python 3.8+).
    So when connect was cancelled after stdio_client entered but before
    ClientSession entered, the stdio scope was left half-entered → subprocess leak.
    """
    config = _make_config()
    client = StdioClient(config)

    fake_stdio, FakeStdio = _make_fake_async_cm("stdio")

    session_enter_started = asyncio.Event()

    class SlowSessionCM:
        """Simulates ClientSession that is slow during __aenter__."""
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            session_enter_started.set()
            # Sleep long enough to be cancelled
            await asyncio.sleep(10)
            return MagicMock()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

    with patch("mcp.client.stdio.stdio_client", return_value=fake_stdio), \
         patch("mcp.ClientSession", SlowSessionCM):

        connect_task = asyncio.create_task(client.connect(timeout=10.0))

        # Wait until session enter starts (stdio already entered)
        await session_enter_started.wait()
        assert FakeStdio.entered is True

        # Cancel the connect — this should trigger cleanup
        connect_task.cancel()
        try:
            await connect_task
        except asyncio.CancelledError:
            pass

        # Give a moment for cleanup to complete
        await asyncio.sleep(0.1)

        # Verify: stdio context was exited by owner task's finally block
        # Before the fix, this would be False because except Exception
        # didn't catch CancelledError, so the cleanup path was never reached
        assert FakeStdio.exited is True, \
            "stdio context leaked: owner task did not clean up after cancel"
        assert FakeStdio.exit_count == 1


@pytest.mark.asyncio
async def test_disconnect_no_double_exit() -> None:
    """Disconnect must not call __aexit__ twice on the same context.

    This tests Bug 2: before the fix, disconnect() had:
        try:
            await self._exit_stack.aclose()  # exits stdio_client
        except (CancelledError, RuntimeError):
            await self._client.__aexit__(...)  # exits stdio_client AGAIN!
    This double exit corrupted anyio cancel scope → CPU 100% spin.
    """
    config = _make_config()
    client = StdioClient(config)

    fake_stdio, FakeStdio = _make_fake_async_cm("stdio")
    fake_session_obj = MagicMock()
    fake_session_obj.initialize = AsyncMock()
    fake_session_cm = _make_fake_session_cm(fake_session_obj)

    with patch("mcp.client.stdio.stdio_client", return_value=fake_stdio), \
         patch("mcp.ClientSession", return_value=fake_session_cm):

        await client.connect(timeout=5.0)
        await client.disconnect(timeout=5.0)

        # Before the fix, if aclose() threw RuntimeError (from dirty scope),
        # the except branch would call __aexit__ again → exit_count == 2
        assert FakeStdio.exit_count == 1, \
            f"stdio exited {FakeStdio.exit_count} times, expected 1 (double exit bug)"


@pytest.mark.asyncio
async def test_exit_stack_reset_allows_reconnect() -> None:
    """After disconnect, exit stack must be reset so reconnect works.

    This tests Bug 3: before the fix, disconnect() only cleared references
    but didn't reset _exit_stack. AsyncExitStack after aclose() enters a
    'closed' state, so the next connect() would fail when trying to
    enter_async_context on the closed stack.
    """
    config = _make_config()
    client = StdioClient(config)

    call_count = {"stdio": 0}

    def make_stdio_cm(*args, **kwargs):
        call_count["stdio"] += 1
        instance, cls = _make_fake_async_cm(f"stdio_{call_count['stdio']}")
        return instance

    session_count = {"n": 0}

    def make_session_cm(*args, **kwargs):
        session_count["n"] += 1
        fake_session_obj = MagicMock()
        fake_session_obj.initialize = AsyncMock()
        return _make_fake_session_cm(fake_session_obj)

    with patch("mcp.client.stdio.stdio_client", side_effect=make_stdio_cm), \
         patch("mcp.ClientSession", side_effect=make_session_cm):

        # First connect
        result = await client.connect(timeout=5.0)
        assert result is True

        # Disconnect
        result = await client.disconnect(timeout=5.0)
        assert result is True

        # Second connect — before the fix, this would fail because
        # _exit_stack was not reset after aclose()
        result = await client.connect(timeout=5.0)
        assert result is True, "reconnect failed: exit stack was not reset"

        # Second disconnect
        result = await client.disconnect(timeout=5.0)
        assert result is True


@pytest.mark.asyncio
async def test_force_close_on_hang() -> None:
    """_force_close should handle owner task that hangs during cleanup."""
    config = _make_config()
    client = StdioClient(config)

    # Create a stdio CM that hangs on exit
    fake_stdio, FakeStdio = _make_fake_async_cm("stdio", hang_on_exit=True)
    fake_session_obj = MagicMock()
    fake_session_obj.initialize = AsyncMock()
    fake_session_cm = _make_fake_session_cm(fake_session_obj)

    with patch("mcp.client.stdio.stdio_client", return_value=fake_stdio), \
         patch("mcp.ClientSession", return_value=fake_session_cm):

        # Connect must succeed first
        result = await client.connect(timeout=5.0)
        assert result is True

        # Disconnect with short timeout — should trigger _force_close
        result = await client.disconnect(timeout=0.5)
        assert result is False  # Timeout → force close

        # Verify: owner task handle cleared (may or may not be leaked depending on timing)
        assert client._owner_task is None
        assert client._is_disconnected is True


@pytest.mark.asyncio
async def test_connect_failure_cleans_up() -> None:
    """When connect fails (e.g., session.initialize raises), owner task cleans up."""
    config = _make_config()
    client = StdioClient(config)

    fake_stdio, FakeStdio = _make_fake_async_cm("stdio")
    fake_session_obj = MagicMock()
    fake_session_obj.initialize = AsyncMock(side_effect=RuntimeError("init failed"))
    fake_session_cm = _make_fake_session_cm(fake_session_obj)

    with patch("mcp.client.stdio.stdio_client", return_value=fake_stdio), \
         patch("mcp.ClientSession", return_value=fake_session_cm):

        result = await client.connect(timeout=5.0)
        assert result is False

        await asyncio.sleep(0.2)

        # Verify: stdio context was exited despite failure
        assert FakeStdio.exited is True
        assert FakeStdio.exit_count == 1


@pytest.mark.asyncio
async def test_disconnect_idempotent() -> None:
    """Calling disconnect multiple times should be safe."""
    config = _make_config()
    client = StdioClient(config)

    fake_stdio, FakeStdio = _make_fake_async_cm("stdio")
    fake_session_obj = MagicMock()
    fake_session_obj.initialize = AsyncMock()
    fake_session_cm = _make_fake_session_cm(fake_session_obj)

    with patch("mcp.client.stdio.stdio_client", return_value=fake_stdio), \
         patch("mcp.ClientSession", return_value=fake_session_cm):

        await client.connect(timeout=5.0)

        result = await client.disconnect(timeout=5.0)
        assert result is True
        assert FakeStdio.exit_count == 1

        # Second disconnect — should be no-op
        result = await client.disconnect(timeout=5.0)
        assert result is True
        assert FakeStdio.exit_count == 1  # Still 1


def test_resolve_timeout() -> None:
    """_resolve_timeout should handle NO_TIMEOUT and explicit values."""
    config = _make_config()
    client = StdioClient(config)

    # NO_TIMEOUT → use default
    assert client._resolve_timeout(timeout=-1, default_s=60.0) == 60.0

    # Explicit timeout
    assert client._resolve_timeout(timeout=30.0, default_s=60.0) == 30.0

    # Invalid timeout → use default
    assert client._resolve_timeout(timeout=-5.0, default_s=60.0) == 60.0
    assert client._resolve_timeout(timeout=0.0, default_s=60.0) == 60.0

    # Config override
    config.params["timeout_s"] = 45.0
    client2 = StdioClient(config)
    assert client2._resolve_timeout(timeout=-1, default_s=60.0) == 45.0
