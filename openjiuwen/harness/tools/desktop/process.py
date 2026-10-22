"""Bounded process-group teardown shared by native worker and SSH transport."""

import asyncio
import os
import signal


async def stop_process(process):
    """Terminate and reap only the process group created by this caller."""
    if process is None:
        return
    # A reaped leader can leave descendants. Our start_new_session PID is its PGID.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        if sig == signal.SIGTERM:
            try:
                await asyncio.wait_for(process.wait(), 0.3)
            except TimeoutError:
                pass
    await process.wait()


async def finish_cleanup(awaitable):
    """A second cancellation must not detach the cleanup before lease release."""
    task = asyncio.create_task(awaitable)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()
