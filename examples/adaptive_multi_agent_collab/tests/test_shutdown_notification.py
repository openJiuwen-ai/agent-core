"""Runtime internal stops must notify the same bounded CLI supervisor as signals."""

import json
import os
import signal
import subprocess
import sys
import time

import pytest


@pytest.mark.parametrize(
    "signals,mode",
    [
        ([], "resistant"),
        ([signal.SIGINT], "resistant"),
        ([signal.SIGTERM], "stalled"),
        ([signal.SIGINT, signal.SIGTERM, signal.SIGINT], "resistant"),
        ([], "slow"),
    ],
)
def test_runtime_internal_stop_bounds_main_and_preserves_signals(
    tmp_path, signals, mode
):
    script = r"""
import asyncio,json,socket,sys,time
from pathlib import Path
def forbidden(*args,**kwargs):raise AssertionError('offline fixture forbids network')
socket.create_connection=socket.getaddrinfo=forbidden
from examples.adaptive_multi_agent_collab.cli import configure_logging
configure_logging()
from examples.adaptive_multi_agent_collab import cli
from examples.adaptive_multi_agent_collab.operations import experiment
from examples.adaptive_multi_agent_collab.infrastructure import shutdown, failure_policy
from examples.adaptive_multi_agent_collab.workflows import runtime as runtime_module
from examples.adaptive_multi_agent_collab.infrastructure.persistence import DurableJournal,read_journal
from examples.adaptive_multi_agent_collab.tests.test_failure_policy_resources import controller
from examples.adaptive_multi_agent_collab.tests.test_runtime import _runtime,_matrix
from examples.adaptive_multi_agent_collab.schemas import QueryExample,Scheme

directory=Path(sys.argv[1]);mode=sys.argv[2];allowance=0.3
state={'allowances':[],'main_cancel_calls':0,'repeated_internal':0}
main_task=None
original_cancel=failure_policy.cancel_with_cause
def cancel(task,cause):
    if task is main_task:state['main_cancel_calls']+=1
    return original_cancel(task,cause)
failure_policy.cancel_with_cause=cancel
original_cleanup=shutdown.bounded_cleanup
async def cleanup(tasks,*,timeout):
    state['allowances'].append(timeout)
    return await original_cleanup(tasks,timeout=timeout)
shutdown.bounded_cleanup=cleanup
original_run=shutdown.run_stage
shutdown.run_stage=lambda value:original_run(value,cleanup_seconds=allowance)

# Captured-state uncertain receipt fixture, not a generation or provider call.
resource=controller();journal=DurableJournal([directory],resource.ledger.state_dict())
resource.ledger.durable_journal=journal
entry=resource.ledger.reserve(0.01,kind='role',model='captured-fixture',metadata={'request_hash':'synthetic'})
entry['status']='dispatched';resource.ledger.persist(entry);journal.flush_sync()
matrix=_matrix(2,{(0,1):Scheme.DEBATE})
runtime,agents=_runtime(2,{}, {1:matrix,2:matrix})

async def pending_cleanup(tasks):
    # Inject only the residual cleanup outcome; Runtime executes its real
    # internal-stop branch after real deterministic offline role processing.
    await asyncio.gather(*tasks,return_exceptions=True)
    state['runtime_cleanup_entry']=True
    state['first_stop_at']=time.monotonic()
    return len(tasks),1
runtime_module.bounded_cleanup=pending_cleanup
controller_state=None
async def pilot(*args,**kwargs):
    global main_task,controller_state
    main_task=asyncio.current_task()
    try:
        await runtime.run(QueryExample('synthetic','Synthetic',('A','B')),fixed_horizon=2)
    finally:
        controller_state=shutdown.current_shutdown()
        state['reason']=controller_state.reason;state['cause']=controller_state.cause
        state['calls_before_cleanup']=sum(len(agent.calls) for agent in agents.values())
        try:shutdown.raise_if_stopping()
        except asyncio.CancelledError:state['new_dispatch_blocked']=True
        def again():
            state['repeated_internal']+=1
            controller_state.request('later internal stop must not replace original',cause='later-cause')
        loop=asyncio.get_running_loop()
        loop.call_soon(again);loop.call_later(0.06,again);loop.call_later(0.15,again)
        (directory/'ready.json').write_text(json.dumps(state))
        if mode=='slow':
            # Finishes inside the allowance, yet must still report interrupted.
            await asyncio.sleep(0.2)
        elif mode=='stalled':
            await asyncio.Future()
        else:
            while True:
                try:await asyncio.sleep(1)
                except asyncio.CancelledError:state['finalization_recancellations']=state.get('finalization_recancellations',0)+1
experiment.pilot_experiment=pilot
code=cli.main(['pilot','synthetic','--output',str(directory),'--authorization','synthetic'])
state['elapsed_since_internal_stop']=time.monotonic()-state['first_stop_at']
state['calls_after_cleanup']=sum(len(agent.calls) for agent in agents.values())
state['signals']=getattr(controller_state,'signals',[])
state['original_signum']=controller_state.signum
state['final_reason']=controller_state.reason;state['final_cause']=controller_state.cause
state['code']=code
journal.flush_sync();saved=read_journal(directory)
state['hold']=saved['state']['entries'][0]['reserved'];state['charge']=saved['state']['entries'][0]['charged']
state['unsafe_boundary']=saved['boundary'] is None
(directory/'observed.json').write_text(json.dumps(state))
raise SystemExit(code)
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
            time.sleep(0.01)
        assert (tmp_path / "ready.json").exists(), process.communicate(timeout=2)
        for signum in signals:
            process.send_signal(signum)
            time.sleep(0.03)
        stdout, stderr = process.communicate(timeout=2)
        assert process.returncode != 0, (stdout, stderr)
        result = json.loads(stdout)
        state = json.loads((tmp_path / "observed.json").read_text())
        assert state["runtime_cleanup_entry"] and state["new_dispatch_blocked"]
        assert state["calls_after_cleanup"] == state["calls_before_cleanup"]
        assert (
            state["final_reason"]
            == state["reason"]
            == "session cleanup deadline; physical operations remain unconfirmed"
        )
        assert state["final_cause"] == state["cause"] == "shutdown-cleanup"
        assert state["original_signum"] is None and state["signals"] == signals
        assert (
            state["main_cancel_calls"] == 0
        )  # Runtime is already unwinding its own stop.
        assert state.get("finalization_recancellations", 0) == 0
        assert state["repeated_internal"] == 3
        assert (
            state["hold"] == 0.01
            and state["charge"] is None
            and state["unsafe_boundary"]
        )
        assert state["elapsed_since_internal_stop"] < 0.8
        assert sum(state["allowances"]) <= 0.3
        assert (
            result["phase"] == "interrupted"
            and result["stage_complete"] is False
            and result["resume_safe"] is False
        )
        assert (
            result["operator_signals"] == signals
            and result["stop_cause"] == "shutdown-cleanup"
        )
        if mode != "slow":
            assert any(
                item["type"] == "AdministrativeCleanupExpired"
                for item in result["persistence_errors"]
            )
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize("already_cancelling", [False, True])
def test_internal_notification_does_not_recancel_main_finalization(
    monkeypatch, already_cancelling
):
    import asyncio
    from examples.adaptive_multi_agent_collab.infrastructure import failure_policy, shutdown

    requests = []
    original = failure_policy.cancel_with_cause
    monkeypatch.setattr(
        failure_policy,
        "cancel_with_cause",
        lambda task, cause: (requests.append(cause), original(task, cause))[1],
    )
    seen = {}

    async def work():
        state = shutdown.current_shutdown()
        seen["state"] = state
        if already_cancelling:
            asyncio.current_task().cancel()
        else:

            async def child():
                state.request("internal worker stop")

            asyncio.create_task(child())
        try:
            await asyncio.Future()
        finally:
            state.request("internal worker stop")
            asyncio.get_running_loop().call_soon(
                state.request, "repeated notification", signal.SIGTERM
            )
            await asyncio.sleep(0.01)
            seen["finalization_finished"] = True

    with pytest.raises(shutdown.StageInterrupted) as error:
        shutdown.run_stage(work(), cleanup_seconds=0.1)
    assert seen["finalization_finished"]
    assert requests == ([] if already_cancelling else ["shutdown-cleanup"])
    assert error.value.state.reason == "internal worker stop"
    assert error.value.state.cause == "shutdown-cleanup"
    assert error.value.state.signals == [signal.SIGTERM]
