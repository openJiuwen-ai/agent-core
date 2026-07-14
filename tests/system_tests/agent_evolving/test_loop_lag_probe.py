# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Loop-lag probe: assert that async paths do not block the event loop.

Fixes verified:
- coordinate_action_tools.type_text_action  (was time.sleep → asyncio.sleep)
- agent_rl.proxy._wait_for_server_ready    (was requests.get → asyncio.to_thread)

A background task samples ``loop.time()`` every 50 ms while the
fixed code paths run.  Max observed drift must stay < 100 ms.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.harness.tools.mobile_gui.coordinate_action_tools import type_text_action


import pytest_asyncio


PROBE_INTERVAL = 0.05  # 50 ms
MAX_ACCEPTABLE_DRIFT = 0.10  # 100 ms


@pytest_asyncio.fixture
async def probe():
    """Yield a running probe task; cancel it on teardown."""
    max_drift = 0.0
    stop_event = asyncio.Event()

    async def _run():
        nonlocal max_drift
        loop = asyncio.get_running_loop()
        expected = loop.time()
        while not stop_event.is_set():
            actual = loop.time()
            drift = actual - expected
            if drift > max_drift:
                max_drift = drift
            # Reschedule at next expected boundary
            expected += PROBE_INTERVAL
            await asyncio.sleep(max(0, expected - actual))

    task = asyncio.create_task(_run())
    yield lambda: max_drift
    stop_event.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_type_text_action_non_blocking(probe):
    """type_text_action must not monopolise the event loop."""
    get_max_drift = probe

    mock_device = MagicMock()
    mock_focused = MagicMock()
    mock_focused.exists = True
    mock_device.return_value = mock_focused

    mock_ctx = MagicMock()
    mock_ctx.get_extra.return_value = {"device": mock_device}

    with patch(
        "openjiuwen.harness.tools.mobile_gui.coordinate_action_tools.get_device_handle",
        return_value=(mock_device, None),
    ), patch(
        "openjiuwen.harness.tools.mobile_gui.coordinate_action_tools.get_shared_extra",
        return_value={"device": mock_device},
    ):
        # Run the action a few times concurrently to stress the loop
        tasks = [type_text_action("hello", mock_ctx) for _ in range(5)]
        results = await asyncio.gather(*tasks)

    assert all(r.startswith("Success:") for r in results)
    assert get_max_drift() < MAX_ACCEPTABLE_DRIFT, (
        f"Event loop blocked for {get_max_drift():.3f}s "
        f"(max allowed {MAX_ACCEPTABLE_DRIFT}s)"
    )


@pytest.mark.asyncio
async def test_proxy_wait_for_server_non_blocking(probe):
    """BackendProxy._wait_for_server_ready must not block the event loop."""
    pytest.importorskip("flask", reason="flask required for BackendProxy")
    from openjiuwen.agent_evolving.agent_rl.proxy import BackendProxy

    get_max_drift = probe

    proxy = BackendProxy()
    proxy._host = "127.0.0.1"
    proxy._port = 59999  # nothing listening — fast fail path

    # Should exhaust retries quickly (each cycle is 0.5 s in the source)
    with pytest.raises(Exception):  # noqa: B017
        # We expect an error because no server is running.
        # The important thing is the loop stayed responsive during retries.
        await proxy._wait_for_server_ready(max_attempts=3)

    assert get_max_drift() < MAX_ACCEPTABLE_DRIFT, (
        f"Event loop blocked for {get_max_drift():.3f}s "
        f"(max allowed {MAX_ACCEPTABLE_DRIFT}s)"
    )
