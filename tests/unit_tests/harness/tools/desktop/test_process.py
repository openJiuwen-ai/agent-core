"""Local process integration: no GUI input and no model inference."""

import asyncio
import subprocess
import sys

import pytest

from openjiuwen.harness.tools.desktop.ownership import DesktopLease
from openjiuwen.harness.tools.desktop.process import finish_cleanup, stop_process


@pytest.mark.asyncio
async def test_owned_process_group_stops_descendant_writes(tmp_path):
    output = tmp_path / "ticks"
    child = (
        "import time; from pathlib import Path; "
        f"p=Path({str(output)!r});\nwhile True:\n p.write_text(str(time.monotonic())); time.sleep(.01)"
    )
    parent = f'import subprocess,time,sys; subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(100)'
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", parent, start_new_session=True)
    try:
        for _ in range(100):
            if output.exists():
                break
            await asyncio.sleep(0.01)
        assert output.exists()
        await stop_process(proc)
        before = output.read_text()
        await asyncio.sleep(0.1)
        assert output.read_text() == before
        assert proc.returncode is not None
    finally:
        await stop_process(proc)


def test_lease_excludes_other_process_and_recovers(tmp_path):
    path = tmp_path / "lock"
    lease = DesktopLease(path).acquire()
    script = (
        "from pathlib import Path; "
        "from openjiuwen.harness.tools.desktop.ownership import DesktopLease; "
        f"DesktopLease(Path({str(path)!r})).acquire()"
    )
    try:
        assert subprocess.run([sys.executable, "-c", script], capture_output=True).returncode != 0
    finally:
        lease.release()
    assert subprocess.run([sys.executable, "-c", script], capture_output=True).returncode == 0


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_cleanup():
    done = asyncio.Event()

    async def cleanup():
        await asyncio.sleep(0.04)
        done.set()

    task = asyncio.create_task(finish_cleanup(cleanup()))
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    await task
    assert done.is_set()
