"""Signals must bound the stage itself, including stalled async finalization."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import pytest


def test_interrupted_finalization_error_is_preserved():
    from examples.adaptive_multi_agent_collab.infrastructure.shutdown import (
        StageInterrupted,
        current_shutdown,
        run_stage,
    )

    async def finalizing():
        current_shutdown().request("external-cancellation")
        raise OSError("mocked final publication failure")

    with pytest.raises(StageInterrupted) as captured:
        run_stage(finalizing(), cleanup_seconds=0.1)
    assert captured.value.cleanup_errors == [
        {
            "operation": "stage finalization",
            "type": "OSError",
            "error": "mocked final publication failure",
        }
    ]


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("mode", ["resistant-main", "stalled-finalization"])
def test_signal_bounds_main_and_finalization(tmp_path, signum, mode):
    """The real CLI wrapper must exit without waiting for the main task's ack."""
    script = r"""
import asyncio,json,sys
from pathlib import Path
from examples.adaptive_multi_agent_collab.cli import configure_logging
configure_logging()
from examples.adaptive_multi_agent_collab import cli
from examples.adaptive_multi_agent_collab.operations import experiment
from examples.adaptive_multi_agent_collab.infrastructure import shutdown

directory=Path(sys.argv[1]); mode=sys.argv[2]
original=shutdown.run_stage
shutdown.run_stage=lambda value:original(value,cleanup_seconds=0.1)
original_cleanup=shutdown.bounded_cleanup
cleanup_allowances=[]
async def cleanup(tasks,*,timeout):
    cleanup_allowances.append(timeout)
    (directory/'cleanup_allowances.json').write_text(json.dumps(cleanup_allowances))
    return await original_cleanup(tasks,timeout=timeout)
shutdown.bounded_cleanup=cleanup
async def pilot(*args,**kwargs):
    # Captured-state fixture: this is not a physical provider operation.
    state={'stage_complete':False,'resume_safe':False,'unfinished_workflow':True,
           'mock_dispatches':1,'unknown_hold':0.01}
    (directory/'ready.json').write_text(json.dumps(state))
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        try:
            shutdown.raise_if_stopping()
            state['mock_dispatches']+=1
        except asyncio.CancelledError:
            state['new_dispatch_blocked']=True
        state['stopping_reason']=shutdown.current_shutdown().reason
        if mode=='resistant-main':
            (directory/'observed.json').write_text(json.dumps(state))
            while True:
                try: await asyncio.sleep(1)
                except asyncio.CancelledError: pass
        raise
    finally:
        if mode=='stalled-finalization':
            state['finalization_started']=True
            (directory/'observed.json').write_text(json.dumps(state))
            await asyncio.Future()
experiment.pilot_experiment=pilot
raise SystemExit(cli.main(['pilot','fixture','--output',str(directory),'--authorization','fixture']))
"""
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", script, str(tmp_path), mode],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTEST_ADDOPTS=""),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while (
            not (tmp_path / "ready.json").exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert (tmp_path / "ready.json").exists(), process.communicate(timeout=2)
        started = time.monotonic()
        process.send_signal(signum)
        stdout, stderr = process.communicate(timeout=2)
        assert time.monotonic() - started < 2
        assert process.returncode == 128 + signum, (stdout, stderr)
        result = json.loads(stdout)
        assert result["phase"] == "interrupted"
        assert not result["stage_complete"] and not result["resume_safe"]
        assert result["persistence_errors"][0]["type"] == "AdministrativeCleanupExpired"
        allowances = json.loads((tmp_path / "cleanup_allowances.json").read_text())
        assert len(allowances) == 2 and sum(allowances) <= 0.1
        state = json.loads((tmp_path / "observed.json").read_text())
        assert state["new_dispatch_blocked"] and state["mock_dispatches"] == 1
        assert state["unknown_hold"] == 0.01 and state["unfinished_workflow"]
        if mode == "stalled-finalization":
            assert state["finalization_started"]
        assert not (tmp_path / "result.json").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
