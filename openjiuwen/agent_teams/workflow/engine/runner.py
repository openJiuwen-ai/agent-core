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
from .primitives import _fresh_holder, _invoke_loaded, _path, _preview, _rt, _seq
from .progress import PhasePlan, ProgressKind, ProgressSink, WorkflowProgressEvent, noop_progress_sink
from .provider import ENGINE_PROVIDER
from .runtime import Runtime
from .seam import reset_provider, use_provider


def _normalize_meta_phases(raw: list[Any] | None) -> list[PhasePlan] | None:
    """Normalize raw META ``phases`` (strings / dicts) to ``list[PhasePlan]``.

    Accepts the shapes that ``ast.literal_eval`` produces from a META dict:
    plain strings (``"Search"``) or dicts with ``title`` / ``name`` and
    optional ``description``.
    """
    if raw is None:
        return None
    result: list[PhasePlan] = []
    for item in raw:
        if isinstance(item, str):
            result.append(PhasePlan(title=item))
        elif isinstance(item, dict):
            title = str(item.get("title") or "?")
            desc = item.get("description")
            result.append(PhasePlan(title=title, description=str(desc) if desc is not None else None))
        else:
            result.append(PhasePlan(title=str(item)))
    return result


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
    description = loaded.meta.get("description") if isinstance(loaded.meta, dict) else None
    raw_phases = loaded.meta.get("phases") if isinstance(loaded.meta, dict) else None
    phases = _normalize_meta_phases(raw_phases)
    try:
        args_text = _preview(rt.args) or ""
        rt.progress_sink(WorkflowProgressEvent(
            kind=ProgressKind.WORKFLOW_STARTED,
            name=name,
            description=description,
            message=f"Workflow started, args: {args_text}",
            phases=phases,
        ))
        result = await _invoke_loaded(loaded, rt.args)
        result_text = _preview(result) or ""
        rt.progress_sink(WorkflowProgressEvent(
            kind=ProgressKind.WORKFLOW_COMPLETED,
            name=name,
            description=description,
            message=f"Workflow completed, result: {result_text}",
        ))
        return result
    except Exception as exc:
        rt.progress_sink(WorkflowProgressEvent(
            kind=ProgressKind.WORKFLOW_FAILED,
            name=name,
            description=description,
            message=f"Workflow failed, exception: {exc}"))
        raise
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
    try:
        result = await _exec_loaded(loaded, rt)
    finally:
        # Close any stateful sessions the backend opened during the run. Best
        # effort — a teardown error must never mask the run's own outcome.
        try:
            await rt.backend.aclose()
        except Exception as exc:  # noqa: BLE001 - teardown is best-effort
            log(f"[wf] backend.aclose() failed: {exc}")
    if journal_path:
        rt.journal.save(journal_path)
    return result
