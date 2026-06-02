# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``run_swarmflow`` — drive a swarmflow script on the team worker backend.

This is the integration entrypoint the ``swarmflow()`` tool calls: it wires a
:class:`TeamWorkerBackend` (real LLM workers) and the caller's observer into the
engine's ``run_workflow``. ``preprocess_swarmflow`` runs the same script offline
with the deterministic ``MockBackend`` to produce the 4-layer ``WorkflowRun``
the console can preview before real execution.

The script's primitives are exposed under ``jiuwenswarm.swarmflow`` (and the
bare ``swarmflow`` the loader always maps), so a script may import either.
"""
from __future__ import annotations

from typing import Any, Callable

from openjiuwen.agent_teams.workflow.backends.team_worker_backend import TeamWorkerBackend
from openjiuwen.agent_teams.workflow.engine import MockBackend, run_workflow
from openjiuwen.agent_teams.workflow.observer import WorkflowObserver
from openjiuwen.agent_teams.workflow.schema import WorkflowRun

_IMPORT_AS = "jiuwenswarm.swarmflow"


async def run_swarmflow(
    script_path: str,
    *,
    model: Any,
    observer: WorkflowObserver,
    args: Any = None,
    team_backend: Any = None,
    team_name: str = "swarmflow",
    language: str = "cn",
    log_sink: Callable[[str], None] | None = None,
) -> Any:
    """Execute a swarmflow script with real LLM workers.

    Args:
        script_path: Path to the ``.py`` swarmflow script (``META`` + ``run``).
        model: LLM ``Model`` each worker DeepAgent runs on.
        observer: Receives the progress-event stream; the caller reads
            ``observer.run`` afterwards for the 4-layer structure.
        args: Value passed to the script's ``run(args)``.
        team_backend: Optional ``TeamBackend`` for worker roster rows.
        team_name: Namespacing for worker member ids.
        language: Prompt language hint.
        log_sink: Optional plain-text diagnostics sink.

    Returns:
        Whatever the script's ``run(args)`` returned.
    """
    backend = TeamWorkerBackend(
        model=model,
        team_backend=team_backend,
        team_name=team_name,
        language=language,
    )
    return await run_workflow(
        script_path,
        args=args,
        backend=backend,
        progress_sink=observer.emit,
        log_sink=log_sink,
        import_as=_IMPORT_AS,
    )


async def preprocess_swarmflow(
    script_path: str,
    *,
    args: Any = None,
    observer: WorkflowObserver | None = None,
) -> WorkflowRun:
    """Dry-run a script offline (MockBackend) to build the 4-layer preview.

    Zero network, deterministic. Used before real execution to hand the TUI
    console the planned Phase ▸ agents ▸ {prompt, activity, outcome} shape.
    """
    obs = observer or WorkflowObserver()
    await run_workflow(
        script_path,
        args=args,
        backend=MockBackend(),
        progress_sink=obs.emit,
        import_as=_IMPORT_AS,
    )
    return obs.run


__all__ = ["run_swarmflow", "preprocess_swarmflow"]
