# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Top-level engine entrypoint.

``run_workflow`` loads a script, builds a :class:`~workflow.engine.runtime.Runtime`,
creates the concurrency semaphore **inside the running loop**, binds the run via
``contextvars``, and awaits the script's ``run(args)``. It brackets the run with
``WORKFLOW_STARTED`` / ``WORKFLOW_COMPLETED`` progress events.

Unlike the dw reference, there is no CLI ``main()`` here — the team integration
drives runs through ``workflow/runner.py:run_swarmflow`` (real worker backend)
or directly with ``MockBackend`` from tests; library code must not ``print``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from .backends import MockBackend
from .backends.base import AgentBackend
from .journal import Journal
from .loader import load_workflow_source
from .primitives import _fresh_holder, _invoke_loaded, _path, _rt, _seq
from .progress import ProgressKind, ProgressSink, WorkflowProgressEvent, noop_progress_sink
from .provider import ENGINE_PROVIDER
from .runtime import Runtime
from .seam import reset_provider, use_provider


def _silent(_message: str) -> None:
    """No-op text sink (default ``log_sink``); never ``print`` in library code."""
    return None


async def _exec_loaded(loaded, rt: Runtime) -> Any:
    # Install the engine as the active provider so the public facade primitives
    # forward here for the lifetime of this run.
    tok_prov = use_provider(ENGINE_PROVIDER)
    tok_rt = _rt.set(rt)
    tok_p = _path.set(())
    tok_s = _seq.set(_fresh_holder())
    name = loaded.meta.get("name") if isinstance(loaded.meta, dict) else None
    try:
        rt.progress_sink(WorkflowProgressEvent(kind=ProgressKind.WORKFLOW_STARTED, message=name))
        result = await _invoke_loaded(loaded, rt.args)
        rt.progress_sink(WorkflowProgressEvent(kind=ProgressKind.WORKFLOW_COMPLETED, message=name))
        return result
    finally:
        _seq.reset(tok_s)
        _path.reset(tok_p)
        _rt.reset(tok_rt)
        reset_provider(tok_prov)


async def run_workflow(
    path: str,
    *,
    args: Any = None,
    backend: AgentBackend | None = None,
    resume: str | None = None,
    journal_path: str | None = None,
    strict: bool = False,
    log_sink: Callable[[str], None] | None = None,
    progress_sink: ProgressSink | None = None,
    cap: int | None = None,
    budget_total: int | None = None,
) -> Any:
    # The ``swarmflow`` name a script imports the primitives under is registered
    # in ``sys.modules`` once at facade import time; the mapping is fixed for the
    # process and there is nothing to install or tear down per run. See
    # ``workflow.engine.facade._register_aliases``.
    log = log_sink or _silent
    loaded = load_workflow_source(path)
    for w in loaded.warnings:
        log(f"[lint] {w}")
    if strict and loaded.warnings:
        from .errors import LintError

        raise LintError(f"{len(loaded.warnings)} lint warning(s) in strict mode")

    rt = Runtime(
        backend=backend or MockBackend(),
        journal=Journal.load(resume),
        args=args,
        log_sink=log,
        progress_sink=progress_sink or noop_progress_sink,
        strict=strict,
        cap_override=cap,
        budget_total=budget_total,
    )
    rt.sem = asyncio.Semaphore(rt.make_cap())  # created inside the running loop
    result = await _exec_loaded(loaded, rt)
    if journal_path:
        rt.journal.save(journal_path)
    return result
