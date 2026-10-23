"""Offline cancellation through workflow/session, pilot and CLI boundaries."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
import torch

from examples.adaptive_multi_agent_collab.operations.experiment import wire_durable_journal
from examples.adaptive_multi_agent_collab.infrastructure.failure_policy import (
    cancellation_cause,
    cancel_with_cause,
)
from examples.adaptive_multi_agent_collab.workflows.runtime import ScriptedRoutingPolicy
from examples.adaptive_multi_agent_collab.schemas import (
    AgentCallKind,
    QueryExample,
    Scheme,
)
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import (
    _build_offline_experiment,
)
from examples.adaptive_multi_agent_collab.tests.test_runtime import _matrix, _runtime


def test_external_cancel_preserves_completed_session_and_partial_trace_without_next_dispatch():
    async def run():
        matrix = _matrix(2, {(0, 1): Scheme.DEBATE})
        runtime, agents = _runtime(2, {}, {1: matrix, 2: matrix, 3: matrix})
        started = asyncio.Event()
        calls = []
        original = agents[1].invoke

        async def invoke(request):
            calls.append(request)
            if request.kind is AgentCallKind.ROLE and request.message.session == 2:
                started.set()
                await asyncio.Future()
            return await original(request)

        agents[1].invoke = invoke
        events = []

        async def observe(kind, trajectory):
            events.append(
                (kind, len(trajectory.sessions), len(trajectory.call_records))
            )

        runtime.progress_observer = observe
        task = asyncio.create_task(
            runtime.run(QueryExample("q", "Synthetic", ("A", "B")), fixed_horizon=3)
        )
        await asyncio.wait_for(started.wait(), 5)
        cancel_with_cause(task, "operator-cancellation")
        with pytest.raises(asyncio.CancelledError):
            await task
        result = runtime.last_trajectory
        assert result.status == "interrupted" and not result.completed
        assert len(result.sessions) == 1 and result.failed_session_traces
        assert result.operational_halt["unfinished_workflow"]
        assert [kind for kind, _, _ in events] == [
            "workflow-started",
            "initialization-complete",
            "completed-session",
            "workflow-interrupted",
        ]
        assert not any(
            request.message and request.message.session == 3 for request in calls
        )

    asyncio.run(run())


def test_reported_incomplete_cleanup_uses_shutdown_cause_and_blocks_next_session(
    monkeypatch,
):
    """Inject only the cleanup status; actual resistant transport is tested below."""
    from examples.adaptive_multi_agent_collab.workflows import runtime as runtime_module
    from examples.adaptive_multi_agent_collab.infrastructure import shutdown

    async def pending_cleanup(tasks):
        await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks), 1

    monkeypatch.setattr(runtime_module, "bounded_cleanup", pending_cleanup)

    async def run():
        matrix = _matrix(2, {(0, 1): Scheme.DEBATE})
        runtime, agents = _runtime(2, {}, {1: matrix, 2: matrix})
        with pytest.raises(asyncio.CancelledError) as failure:
            await runtime.run(
                QueryExample("q", "Synthetic", ("A", "B")), fixed_horizon=2
            )
        assert cancellation_cause(failure.value) == "shutdown-cleanup"
        assert failure.value.timing_phase == "worker-cleanup"
        assert runtime.last_trajectory.status == "interrupted"
        assert runtime.last_trajectory.operational_halt["reason"] == "shutdown-cleanup"
        state = shutdown.ShutdownState()
        state.request("session cleanup deadline", cause="shutdown-cleanup")
        token = shutdown._state.set(state)
        try:
            with pytest.raises(asyncio.CancelledError) as stopped:
                shutdown.raise_if_stopping()
            assert cancellation_cause(stopped.value) == "shutdown-cleanup"
            assert stopped.value.timing_phase == "pre-dispatch-shutdown"
        finally:
            shutdown._state.reset(token)
        assert not any(
            "s2" in str(call.context.get("tag", ""))
            for agent in agents.values()
            for call in agent.calls
        )

    asyncio.run(run())


def test_pilot_interrupt_saves_partial_observation_and_unsafe_checkpoint(tmp_path):
    async def run():
        trainer, hooks, _, _, _ = _build_offline_experiment(pilot_count=2)
        trainer.hooks, trainer.checkpoint_path = hooks, tmp_path / "checkpoint.pt"
        hooks.bind(trainer)
        wire_durable_journal(trainer, hooks, tmp_path)
        trainer._checkpoint()
        blocked = asyncio.Event()
        factory = hooks.runtime_factory
        created = []

        def runtime_factory(*args):
            runtime = factory(*args)
            created.append(runtime)
            matrix = _matrix(2, {(0, 1): Scheme.DEBATE})
            scripted = ScriptedRoutingPolicy(2, {1: matrix, 2: matrix, 3: matrix})
            runtime._route = lambda state, mode, *_: scripted._action(
                state.session, mode
            )
            agent = runtime.agents[0]
            original = agent.invoke

            async def invoke(request):
                if request.kind is AgentCallKind.ROLE and request.message.session == 2:
                    blocked.set()
                    await asyncio.Future()
                return await original(request)

            agent.invoke = invoke
            return runtime

        hooks.runtime_factory = runtime_factory
        task = asyncio.create_task(
            trainer.run_pilot(hooks, checkpoint_path=trainer.checkpoint_path)
        )
        await asyncio.wait_for(blocked.wait(), 5)
        cancel_with_cause(task, "operator-cancellation")
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(created) == 1
        assert trainer.state.phase == "interrupted" and not trainer.state.pilot_complete
        assert len(hooks.pilot_observations) == 1
        assert hooks.pilot_observations[0].sessions_completed == 1
        assert hooks.pilot_observations[0].status == "interrupted"
        assert not hooks.safety_record.execution_complete
        await hooks.expenditure_ledger.flush_durable()
        from examples.adaptive_multi_agent_collab.infrastructure.persistence import read_journal

        progress = read_journal(tmp_path)["progress"]
        assert any(item["kind"] == "completed-session" for item in progress)
        assert any(item["kind"] == "conversation-progress" for item in progress)
        saved = torch.load(
            trainer.checkpoint_path, map_location="cpu", weights_only=False
        )
        assert saved["request_boundary_pending"] is True
        assert saved["hook_state"]["pilot_observations"][0]["sessions_completed"] == 1
        assert saved["trainer_state"]["phase"] == "interrupted"
        await hooks.expenditure_ledger.durable_journal.aclose()

    asyncio.run(run())


def test_nonpilot_runtime_hook_persists_session_and_call_progress_without_checkpoint_per_call(
    tmp_path, monkeypatch
):
    from examples.adaptive_multi_agent_collab.infrastructure.persistence import read_journal
    from examples.adaptive_multi_agent_collab.models.policies import RoutingRolloutIdentity

    async def run():
        trainer, hooks, _, _, _ = _build_offline_experiment()
        trainer.hooks, trainer.checkpoint_path = hooks, tmp_path / "checkpoint.pt"
        hooks.bind(trainer)
        wire_durable_journal(trainer, hooks, tmp_path)
        trainer._checkpoint()
        saved = []
        original = trainer.save_checkpoint

        def save(path):
            saved.append(path)
            return original(path)

        monkeypatch.setattr(trainer, "save_checkpoint", save)
        before = {
            name: value.clone() for name, value in trainer.alpha.state_dict().items()
        }
        with torch.no_grad():
            trajectory = await hooks.trajectory(
                "routing",
                routing_identity=RoutingRolloutIdentity(
                    trainer.config.seed, "offline-progress", 0
                ),
            )
        assert trajectory.completed and len(trajectory.sessions) == 1
        await hooks.expenditure_ledger.flush_durable()
        journal = read_journal(tmp_path)
        kinds = [item["kind"] for item in journal["progress"]]
        assert (
            "workflow-started" in kinds
            and "completed-session" in kinds
            and "logical-call-result" in kinds
        )
        # Only the existing consumed-query checkpoint precedes dispatch. The
        # session/call deltas do not save numerical state or optimize parameters.
        assert len(saved) == 1
        assert journal["boundary"] is None
        assert all(
            torch.equal(value, trainer.alpha.state_dict()[name])
            for name, value in before.items()
        )
        assert not trainer.alpha_optimizer.state and not hooks.pilot_observations
        await hooks.expenditure_ledger.durable_journal.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("publication_failure", [None, "flush", "checkpoint"])
def test_actual_stage_wrapper_publishes_interrupted_result_and_retains_unsafe_resume(
    tmp_path, monkeypatch, publication_failure
):
    from examples.adaptive_multi_agent_collab.operations import experiment, preflight
    from examples.adaptive_multi_agent_collab.providers import identity

    async def run():
        trainer, hooks, models, _, _ = _build_offline_experiment(pilot_count=2)
        prepared = SimpleNamespace(
            config=trainer.config, dataset=hooks.dataset, specification_sha256="fixture"
        )
        report = SimpleNamespace(require_ready=lambda: None)
        monkeypatch.setattr(experiment, "_campaign_directory", lambda *_: tmp_path)
        monkeypatch.setattr(experiment, "prepare_experiment", lambda *_: prepared)
        monkeypatch.setattr(experiment, "build_experiment", lambda *_: (trainer, hooks))
        monkeypatch.setattr(experiment, "_write_run_inputs", lambda *_: None)
        monkeypatch.setattr(preflight, "inspect_preflight", lambda *_, **__: report)
        monkeypatch.setattr(
            identity,
            "with_registered_response_identity",
            lambda prepared, *_: prepared,
        )
        monkeypatch.setattr(hooks, "authorize_stage", lambda _: None)
        authorization = tmp_path / "authorization.json"
        authorization.write_text("{}")
        started = asyncio.Event()

        async def invoke(**_):
            started.set()
            await asyncio.Future()

        models[0].invoke = invoke
        task = asyncio.create_task(
            experiment._execute_stage(
                tmp_path / "manifest.json",
                tmp_path,
                stage="pilot",
                authorization_path=authorization,
            )
        )
        await asyncio.wait_for(started.wait(), 5)
        if publication_failure == "flush":

            async def failed_flush():
                raise OSError("injected accounting flush failure")

            monkeypatch.setattr(hooks.expenditure_ledger, "flush_durable", failed_flush)
        elif publication_failure == "checkpoint":

            async def failed_checkpoint(**_):
                raise OSError("injected checkpoint publication failure")

            monkeypatch.setattr(trainer, "checkpoint_async", failed_checkpoint)
        cancel_with_cause(task, "operator-cancellation")
        with pytest.raises(asyncio.CancelledError):
            await task
        result = json.loads((tmp_path / "result.json").read_text())
        assert result["phase"] == "interrupted" and not result["stage_complete"]
        assert result["unfinished_workflow"] and not result["resume_safe"]
        assert bool(result["persistence_errors"]) is (publication_failure is not None)
        assert not (tmp_path / "development_authorization_template.json").exists()
        pilot = json.loads((tmp_path / "pilot_record.json").read_text())
        assert pilot["status"] == "incomplete" and pilot["attempted_workflows"] == 1
        saved = torch.load(tmp_path / "checkpoint.pt", weights_only=False)
        if publication_failure != "checkpoint":
            assert saved["request_boundary_pending"] is True
        from examples.adaptive_multi_agent_collab.infrastructure.persistence import read_journal

        assert read_journal(tmp_path)["boundary"] is None
        await hooks.expenditure_ledger.durable_journal.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("resistant", [False, True])
def test_cli_signals_stop_dispatch_and_bound_resistant_cleanup(
    tmp_path, signum, resistant
):
    """Real CLI signal wrapper; a captured-state fake physical task cannot finish."""
    marker = tmp_path / "ready.json"
    result_path = tmp_path / "result.json"
    script = r"""
import asyncio,json,os,sys
from pathlib import Path
from examples.adaptive_multi_agent_collab.cli import configure_logging
configure_logging()
from examples.adaptive_multi_agent_collab import cli
from examples.adaptive_multi_agent_collab.operations import experiment
from examples.adaptive_multi_agent_collab.infrastructure import shutdown

directory=Path(sys.argv[1]); resistant=sys.argv[2]=='True'
original=shutdown.run_stage
shutdown.run_stage=lambda value:original(value,cleanup_seconds=0.1)
async def physical():
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        if not resistant:
            raise
        while True:
            try: await asyncio.sleep(0.1)
            except asyncio.CancelledError: pass
async def pilot(*args,**kwargs):
    task=asyncio.create_task(physical())
    (directory/'ready.json').write_text(json.dumps({'physical_dispatches':1}))
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        task.cancel()
        await shutdown.bounded_cleanup([task],timeout=0.1)
        (directory/'result.json').write_text(json.dumps({
            'stage_complete':False,'phase':'interrupted','resume_safe':False,
            'physical_dispatches':1,'unknown_hold_retained':True,
            'cleanup_pending':not task.done()}))
        raise
experiment.pilot_experiment=pilot
raise SystemExit(cli.main(['pilot','fixture','--output',str(directory),'--authorization','fixture']))
"""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTEST_ADDOPTS="")
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", script, str(tmp_path), str(resistant)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while (
            not marker.exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert marker.exists(), process.communicate(timeout=2)
        process.send_signal(signum)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 128 + signum, (stdout, stderr)
        result = json.loads(result_path.read_text())
        assert not result["stage_complete"] and not result["resume_safe"]
        assert result["physical_dispatches"] == 1 and result["unknown_hold_retained"]
        assert result["cleanup_pending"] is resistant
        assert json.loads(stdout)["phase"] == "interrupted"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_cli_signal_preserves_real_gateway_dispatch_hold_and_journal(tmp_path, signum):
    """No provider traffic: the actual Model consumes a resistant mocked HTTP stream."""
    script = r"""
import asyncio,json,sys,time
from pathlib import Path
from dataclasses import replace
from examples.adaptive_multi_agent_collab.cli import configure_logging
configure_logging()
import httpx
from examples.adaptive_multi_agent_collab import cli
from examples.adaptive_multi_agent_collab.operations import experiment
from examples.adaptive_multi_agent_collab.infrastructure import shutdown
from examples.adaptive_multi_agent_collab.providers.gateway import build_openrouter_model
from examples.adaptive_multi_agent_collab.infrastructure.persistence import read_journal
from examples.adaptive_multi_agent_collab.tests.test_gateway import binding
from examples.adaptive_multi_agent_collab.tests.test_failure_policy_resources import controller
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment

directory=Path(sys.argv[1]); requests=[]
resource=controller()
trainer,hooks,_,_,_=_build_offline_experiment(pilot_count=1)
hooks.expenditure_ledger=resource.ledger
trainer.hooks=hooks; trainer.checkpoint_path=directory/'checkpoint.pt'; hooks.bind(trainer)
experiment.wire_durable_journal(trainer,hooks,directory)
trainer._checkpoint()
class Stream(httpx.AsyncByteStream):
    async def __aiter__(self):
        (directory/'ready.json').write_text(json.dumps({'post_count':len(requests)}))
        while True:
            try: await asyncio.sleep(0.1)
            except asyncio.CancelledError: pass
        yield b''
    async def aclose(self): pass
async def handle(request):
    assert request.method=='POST', 'no metadata regeneration or lookup is permitted by fixture'
    requests.append(request)
    return httpx.Response(200,stream=Stream())
model=build_openrouter_model(binding(),'offline-fixture-key',temperature=0.7,transport=httpx.MockTransport(handle))
original=shutdown.run_stage
shutdown.run_stage=lambda value:original(value,cleanup_seconds=0.1)
async def pilot(*args,**kwargs):
    try:
        await resource.invoke(model,messages=[{'role':'user','content':'Synthetic accounting probe'}],
            kind='role',deadline=time.monotonic()+30,settings={'seed':42})
    except asyncio.CancelledError:
        trainer.state.phase='interrupted'
        await resource.ledger.flush_durable()
        trainer._checkpoint(safe_boundary=False)
        state=resource.ledger.state_dict()
        (directory/'result.json').write_text(json.dumps({'stage_complete':False,'phase':'interrupted',
            'post_count':len(requests),'entries':state['entries'],'resume_safe':False}))
        raise
experiment.pilot_experiment=pilot
raise SystemExit(cli.main(['pilot','fixture','--output',str(directory),'--authorization','fixture']))
"""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTEST_ADDOPTS="")
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", script, str(tmp_path)],
        env=env,
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
        process.send_signal(signum)
        stdout, stderr = process.communicate(timeout=8)
        assert process.returncode == 128 + signum, (stdout, stderr)
        result = json.loads((tmp_path / "result.json").read_text())
        assert result["post_count"] == 1 and result["phase"] == "interrupted"
        assert len(result["entries"]) == 1
        entry = result["entries"][0]
        assert entry["charged"] is None and entry["reserved"] > 0
        assert (
            entry["status"] == "failed"
            and entry["usage"]["failure_kind"] == "interrupted-generation"
        )
        from examples.adaptive_multi_agent_collab.infrastructure.persistence import read_journal

        journal = read_journal(tmp_path)
        assert journal["boundary"] is None
        assert journal["state"]["entries"][0]["reserved"] == entry["reserved"]
        saved = torch.load(tmp_path / "checkpoint.pt", weights_only=False)
        assert saved["request_boundary_pending"] is True
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
