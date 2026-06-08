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
from openjiuwen.agent_teams.workflow.engine import (
    MockBackend,
    ProgressKind,
    WorkflowProgressEvent,
    run_workflow,
)
from openjiuwen.agent_teams.workflow.observer import WorkflowObserver
from openjiuwen.agent_teams.workflow.schema import WorkflowRun
from openjiuwen.core.common.logging import team_logger


def _team_log_sink(message: str) -> None:
    team_logger.warning("{}", message)


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
    model_resolver: Callable[[str], Any] | None = None,
    worker_base_spec: Any = None,
    human_base_spec: Any = None,
    build_context: Any = None,
    messager: Any = None,
    session_id: str | None = None,
) -> Any:
    """Execute a swarmflow script with real LLM workers.

    Args:
        script_path: Path to the ``.py`` swarmflow script (``META`` + ``run``).
        model: Default LLM ``Model`` each worker DeepAgent runs on when a call
            carries no ``model`` hint (or the hint can't be resolved).
        observer: Receives the progress-event stream; the caller reads
            ``observer.run`` afterwards for the 4-layer structure.
        args: Value passed to the script's ``run(args)``.
        team_backend: Optional ``TeamBackend`` for worker roster rows.
        team_name: Namespacing for worker member ids.
        language: Prompt language hint.
        log_sink: Optional plain-text diagnostics sink.
        model_resolver: Optional callback resolving an ``agent(model=...)`` name
            hint to a worker ``TeamModelConfig``; ``None`` means the worker
            inherits its base spec's model.
        worker_base_spec: Base ``DeepAgentSpec`` each worker derives from (the
            team's teammate spec, or the leader spec) — gives workers
            teammate-equivalent capabilities without the team tools.
        human_base_spec: Base ``DeepAgentSpec`` for human-session avatars (the
            team's human_agent spec, or a fallback). ``None`` disables
            ``human_session`` / ``human`` (they fail clearly when used).
        build_context: Optional ``BuildContext`` from the leader harness,
            forwarded to each worker's ``NativeHarness`` build. Runtime-only
            handles such as the owner-scoped worktree manager ride in
            ``build_context.extras``.
        messager: The team messager, used by human sessions to receive a real
            person's reply on the dedicated reply topic.
        session_id: The current session id, used to build the human-reply topic.

    Returns:
        Whatever the script's ``run(args)`` returned.
    """
    def _on_human_prompt(member_name: str, correlation_id: str, prompt: str) -> None:
        """Surface a pending human turn as a progress event (leader narrates it)."""
        observer.emit(
            WorkflowProgressEvent(
                kind=ProgressKind.HUMAN_PROMPT,
                label=member_name,
                prompt=prompt,
                correlation_id=correlation_id,
            )
        )

    def _on_human_replied(member_name: str, correlation_id: str) -> None:
        """Signal that a pending human turn was answered."""
        observer.emit(
            WorkflowProgressEvent(
                kind=ProgressKind.HUMAN_REPLIED,
                label=member_name,
                correlation_id=correlation_id,
            )
        )

    backend = TeamWorkerBackend(
        model=model,
        team_backend=team_backend,
        team_name=team_name,
        language=language,
        model_resolver=model_resolver,
        worker_base_spec=worker_base_spec,
        human_base_spec=human_base_spec,
        build_context=build_context,
        messager=messager,
        session_id=session_id,
        on_human_prompt=_on_human_prompt,
        on_human_replied=_on_human_replied,
    )
    return await run_workflow(
        script_path,
        args=args,
        backend=backend,
        progress_sink=observer.emit,
        log_sink=log_sink or _team_log_sink,
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
    )
    return obs.run


__all__ = ["run_swarmflow", "preprocess_swarmflow"]
