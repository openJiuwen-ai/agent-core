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
import json
import os
from pathlib import Path
from typing import Any, Callable

from .admission import AgentAdmission
from .backends import MockBackend
from .backends.base import AgentBackend
from .budget import BudgetLedger
from .errors import BudgetExhausted, MetaError
from .journal import Journal
from .loader import load_workflow_source
from .primitives import (
    _budget_snapshot,
    _fresh_holder,
    _invoke_loaded,
    _path,
    _preview,
    _rt,
    _seq,
    _wf_budget_snapshot,
)
from .progress import PhasePlan, ProgressKind, ProgressSink, WorkflowProgressEvent, noop_progress_sink
from .provider import ENGINE_PROVIDER
from .runtime import AbortSignal, Runtime
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


def _budget_sidecar_path(journal_path: str | None) -> str | None:
    """The ``<journal>.budget`` sidecar path, or ``None`` when journaling is off.

    Colocated with the journal and its WAL so resume (which resolves the same
    ``team + session + META.name`` stem) finds it. None when the run has no
    journal (the offline preview path) — in that case budget persistence is
    simply disabled, exactly like WAL appends are.
    """
    return f"{journal_path}.budget" if journal_path else None


def _read_budget_snapshot(path: str | None) -> dict | None:
    """Read the ``<journal>.budget`` sidecar, or ``None`` when absent / corrupt.

    None covers both "first run (no file yet)" and "torn write" — in either case
    the run starts fresh at ``spent=0``, the documented crash-degradation for a
    soft ceiling (losing the in-flight agent's count is tolerated). Synchronous
    because the file is tiny (~100 bytes) and read once at run start.
    """
    if not path or not Path(path).exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.loads(f.read())
    except (OSError, json.JSONDecodeError):
        return None


def _write_budget_snapshot(path: str | None, ledger: BudgetLedger) -> None:
    """Atomically overwrite the ``<journal>.budget`` sidecar with the ledger state.

    Temp file + ``os.replace`` (the same atomic-commit trick :meth:`Journal.save`
    uses) so a crash mid-write leaves either the previous snapshot or the new
    one, never a torn file. Synchronous: the payload is ~100 bytes (a single
    object, not the journal's record stream), so the blocking write is
    microseconds — and synchronous-ness is the point, giving the emit hook a
    durable point-in-time snapshot before the run moves on. A failure is
    swallowed (soft ceiling): the run continues, only resume-fidelity degrades.
    """
    if not path:
        return
    payload = {
        "total": ledger.total,
        "spent": ledger.spent,
        "phase_tokens": dict(ledger.phase_tokens),
    }
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
        os.replace(tmp, path)
    except OSError:
        # Best-effort: a soft ceiling tolerates a missed snapshot. The next
        # successful write re-syncs; only resume fidelity degrades, not the run.
        pass


def _persist_budget_final(rt) -> None:
    """Seal the budget sidecar at a terminal point (normal completion or the
    ``BudgetExhausted`` branch of ``_exec_loaded``).

    The emit-hook writes are best-effort (a soft ceiling tolerates a missed one
    mid-run), so this durable write is what makes a *completed* run's later
    resume read the true final tally — otherwise the sidecar would lag by the
    last agent whose emit write happened to land. No-op when there is no ceiling
    (unbounded) or no persistence path (offline preview).
    """
    wb = rt.workflow_budget
    fn = rt.persist_workflow_budget
    if wb.total is not None and fn is not None:
        fn(wb)


def _resolve_workflow_budget(
    loaded,
    session_budget: BudgetLedger | None = None,
    *,
    resume_spent: int = 0,
    resume_phase_tokens: dict[str, int] | None = None,
) -> BudgetLedger:
    """Build the per-run ledger from the script's ``META.workflow_token_limit``.

    The per-run ceiling is declared by the workflow script itself — the session
    budget is the team-wide total, while each workflow knows its own cost. A
    missing/invalid limit yields an unbounded ledger (``spent`` still counts for
    display, but no per-run ceiling is enforced) — this keeps hand-written
    scripts that do not declare a limit backwards-compatible (equivalent to the
    single-layer behaviour), while the generator enforces the field on new
    scripts.

    A declared per-run ceiling is validated against the session budget: the run
    is a sub-view of the team's shared total, so ``workflow_token_limit`` must
    not exceed the session ceiling — otherwise the run could never complete
    within its own declared budget while the shared pool holds the real limit.

    ``resume_spent`` / ``resume_phase_tokens`` restore the ledger's prior tally
    on a resume (read by :func:`_read_budget_snapshot` from the
    ``<journal>.budget`` sidecar). They are **only** meaningful with a declared
    ceiling (an unbounded ledger has no ceiling to count toward, so the resumed
    tally would never trigger — keeping it 0 is correct). A first run (no
    sidecar) passes the 0 / ``None`` defaults.
    """
    meta = loaded.meta if isinstance(loaded.meta, dict) else {}
    limit = meta.get("workflow_token_limit")
    if isinstance(limit, int) and limit > 0:
        if session_budget is not None and session_budget.total is not None:
            if limit > session_budget.total:
                raise MetaError(
                    f"META.workflow_token_limit ({limit}) exceeds the session "
                    f"swarmflow_budget ({session_budget.total}); a per-run ceiling "
                    f"cannot exceed the team ceiling"
                )
        return BudgetLedger(total=limit, spent=resume_spent,
                            phase_tokens=dict(resume_phase_tokens or {}))
    return BudgetLedger()


def _bind_workflow_budget(backend: AgentBackend, workflow_budget: BudgetLedger) -> None:
    """Hand the per-run ledger to the backend if it accepts one.

    ``AgentBackend.bind_budget`` binds the session-wide ledger; the per-run
    ledger is plumbed through a separate hook so a backend that does not
    override ``bind_workflow_budget`` (older implementations) simply ignores it.
    The engine's own ``_check_budget`` gate still reads ``rt.workflow_budget``
    directly, so enforcement does not depend on backend support.
    """
    fn = getattr(backend, "bind_workflow_budget", None)
    if callable(fn):
        fn(workflow_budget)


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
    except BudgetExhausted as exc:
        # Seal the budget sidecar at the exhaustion point so a later resume
        # reads the true spent (otherwise the sidecar lags by the in-flight
        # agent's emit write that never landed). The run is terminal — there is
        # no finalize (the WAL stays for recovery), so this is the durable write.
        _persist_budget_final(rt)
        rt.progress_sink(WorkflowProgressEvent(
            kind=ProgressKind.WORKFLOW_FAILED,
            name=name,
            description=description,
            message=f"Workflow failed, exception: {exc}",
            budget=_budget_snapshot(rt.budget),
            workflow_budget=_wf_budget_snapshot(rt),
            budget_exhausted_scope=exc.scope,
        ))
        raise
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
    agent_gate: AgentAdmission | None = None,
    budget: BudgetLedger | None = None,
    workflow_budget: BudgetLedger | None = None,
    abort_event: AbortSignal | None = None,
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

    # The WAL is a sidecar of the canonical journal write-path (``<journal>.wal``):
    # fresh records are appended to it as they complete, so a mid-run crash (no
    # save) stays recoverable, and a residual WAL is replayed on the next load.
    # Derived in-engine from the given path (no agent_teams import — engine stays
    # business-agnostic); the journal path itself comes from the caller (paths.py).
    wal_path = f"{journal_path}.wal" if journal_path else None
    journal = await Journal.load(resume, wal_path=wal_path)
    log(f"[wf] journal loaded: prior_records={len(journal.prior)} path={resume} wal={wal_path}")
    # Resume budget recovery: read the per-run ledger's prior tally from the
    # ``<journal>.budget`` sidecar (colocated with the journal stem, so a resume
    # — which resolves the same team/session/name path — finds it). None on a
    # first run or a torn write; the ledger then starts at spent=0 (the
    # documented soft-ceiling degradation). Only a declared ceiling carries the
    # tally forward; an unbounded ledger has no ceiling to count toward.
    budget_sidecar = _budget_sidecar_path(journal_path)
    resume_snap = _read_budget_snapshot(budget_sidecar)
    rt = Runtime(
        backend=backend or MockBackend(),
        journal=journal,
        args=args,
        log_sink=log,
        progress_sink=progress_sink or noop_progress_sink,
        strict=strict,
        cap_override=cap,
        budget=budget if budget is not None else BudgetLedger(),
        workflow_budget=workflow_budget if workflow_budget is not None else _resolve_workflow_budget(
            loaded,
            session_budget=budget,
            resume_spent=(resume_snap or {}).get("spent", 0),
            resume_phase_tokens=(resume_snap or {}).get("phase_tokens"),
        ),
        abort_event=abort_event,
        agent_gate=agent_gate,
        # The emit hooks call this synchronously at agent boundaries to keep the
        # sidecar a durable point-in-time snapshot (soft ceiling: a missed write
        # only degrades resume fidelity, not the run). None disables persistence
        # (offline preview path with no journal).
        persist_workflow_budget=(
            (lambda wb, _p=budget_sidecar: _write_budget_snapshot(_p, wb))
            if budget_sidecar else None
        ),
    )
    # Hand the ledger to the backend: it is the only layer that sees what a call
    # really costs, so it does the accounting and the engine only reads. The
    # per-run ledger is bound too so the rail bills both (session-wide + per-run).
    rt.backend.bind_budget(rt.budget)
    _bind_workflow_budget(rt.backend, rt.workflow_budget)
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
        # Reached only when the workflow ran to normal completion (any
        # interrupt / crash / cancellation re-raises and skips this line, leaving
        # the WAL for recovery). finalize = atomic journal write + terminal WAL
        # removal; a future mid-run checkpoint must call save() (keeps the WAL).
        # Durable budget snapshot too: the emit-hook writes are best-effort
        # (synchronous but soft-ceiling-tolerant), so seal a final one here
        # before the WAL goes away — a completed run's resume would otherwise
        # read a stale sidecar that's missing the last agent.
        _persist_budget_final(rt)
        await rt.journal.finalize(journal_path)
    return result
