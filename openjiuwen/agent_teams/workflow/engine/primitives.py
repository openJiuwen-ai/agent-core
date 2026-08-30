# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The DSL primitives: ``agent / parallel / pipeline / map_parallel /
phase / log / workflow / budget`` plus ``compact`` / ``flatten_filter``.

A workflow imports these (``from swarmflow import agent, parallel, ...``) and
calls them from inside ``async def run(args)``. They read the active
:class:`~workflow.engine.runtime.Runtime` and the current structural path from
``contextvars`` — so each call resolves the run it belongs to, and concurrent
workflows in one event loop stay isolated (each runs in its own Task → its own
context copy). There is no shared ``ctx`` object to misplace across runs.

Concurrency model:

* The cap is a single ``asyncio.Semaphore`` acquired **only inside** ``agent()``
  around the backend call — orchestration coroutines hold no permit, so nested
  ``parallel(parallel(...))`` never deadlocks against the cap.
* ``parallel`` is a fork-join **barrier** (``gather`` over safe-wrapped thunks):
  a failing thunk resolves to ``None`` and the call never raises. We do **not**
  use ``asyncio.TaskGroup`` (it cancels siblings on first error — wrong here).
* ``pipeline`` streams with **no barrier**: each item is an independent chain
  coroutine; ``gather`` runs them concurrently so item A can be in stage 3 while
  B is still in stage 1.

``contextvars`` isolation: ``asyncio.gather`` wraps each coroutine in a Task,
which copies the current context — so a branch/chain's ``_path``/``_seq`` writes
are private to that branch. ``CancelledError`` is a ``BaseException`` (not
``Exception``), so the broad ``except Exception`` guards never swallow it and
runs stay cancellable without an explicit re-raise clause.

Observability hooks (the one divergence from a pure port): ``phase`` / ``log``
and ``agent`` start/end emit structured :class:`WorkflowProgressEvent`s to
``rt.progress_sink``; internal diagnostics go to ``rt.log_sink``.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, Sequence, TypeVar, overload

from .errors import BudgetExhausted, EngineError, WorkflowAborted
from .journal import call_signature, key_str
from .progress import ProgressKind, WorkflowProgressEvent
from .schema import coerce, resolve_schema
from .verify import Reviewer, SCORE_SCHEMA, VERDICT_SCHEMA, VerifyResult, VerifyVote, settle_verify_tally

if TYPE_CHECKING:
    from pydantic import BaseModel

# For typing overloads: `schema=MyModel` narrows the return to `MyModel | None`.
M = TypeVar("M", bound="BaseModel")

# Active runtime + current structural location.
_rt: ContextVar = ContextVar("wf_runtime")
_path: ContextVar[tuple] = ContextVar("wf_path", default=())
_seq: ContextVar[dict | None] = ContextVar("wf_seq", default=None)
# Nesting depth of the *current execution path* (how many ``workflow()`` frames
# this call chain is inside). A ContextVar — not a shared Runtime counter — so it
# is copied per asyncio Task: parallel()/pipeline() branches each inherit the
# parent depth and advance their OWN copy, which lets sibling ``workflow()`` calls
# run concurrently while still capping genuine recursion (a sub-workflow calling
# ``workflow()`` again runs in the same Task, so it sees the incremented depth).
_wf_depth: ContextVar[int] = ContextVar("wf_depth", default=0)
# Max nesting of ``workflow()`` calls along one path. 1 = a script may call a
# sub-workflow, but that sub-workflow may not call another (recursion guard).
_MAX_WORKFLOW_DEPTH = 1
# Frozen parent-phase, set by ``phase()``; survives concurrent sibling branch races.
_parent_phase: ContextVar[str | None] = ContextVar("wf_parent_phase", default=None)
# Per-task display name (``▸ {name} #{N}``), immune to parallel branch races.
_wf_display_name: ContextVar[str | None] = ContextVar("wf_display_name", default=None)
# Per-task current phase, replaces ``rt.current_phase`` — immune to parallel branch races.
_current_phase: ContextVar[str | None] = ContextVar("wf_current_phase", default=None)
# Max items in a single ``parallel()`` / ``pipeline()`` call. Exceeding it raises
# rather than silently truncating — a bounded fan-out keeps one call from
# spawning an unbounded agent fleet by accident.
_MAX_FANOUT = 4096


@dataclass
class _BackendCallResult:
    """Result of a backend call attempt cycle — avoids unwieldy multi-tuple returns.

    Attributes:
        result:        The final, coerced result (str, pydantic model, dict, or None).
        succeeded:     True if the backend call + coercion succeeded.
        error_detail:  Short error description when ``succeeded`` is False.
        raw_text:      The LLM's original text reply before coercion — used as
                       ``outcome`` in ``AGENT_COMPLETED`` progress events.
        tokens:        Tokens billed by this call (``AgentResult.tokens``); ``None`` on skip / failure.
    """

    result: Any = None
    succeeded: bool = False
    error_detail: str | None = None
    raw_text: str | None = None
    tokens: int | None = None


def _task_id():
    try:
        t = asyncio.current_task()
    except RuntimeError:
        return None
    return id(t) if t is not None else None


def _fresh_holder() -> dict:
    """A new per-scope child counter, tagged with the Task that owns it."""
    return {"n": 0, "owner": _task_id()}


def _warn_concurrent_scope() -> None:
    """Emitted once if two flows race the same scope counter (the raw-gather anti-pattern)."""
    rt = _rt.get(None)
    if rt is None or getattr(rt, "warned_concurrent_scope", False):
        return
    rt.warned_concurrent_scope = True
    rt.log_sink(
        "[wf] WARNING: multiple orchestration blocks/agents were started concurrently in "
        "the same scope (e.g. a raw asyncio.gather/create_task that bypasses the DSL). This "
        "races structural resume keys and can corrupt --resume. Fan out with "
        "parallel()/pipeline() (each branch gets its own scope) or await blocks sequentially."
    )


def _next_ordinal() -> int:
    """Next child-ordinal in the current scope.

    A *single* per-scope counter over **all** structural children (agent calls
    *and* parallel/pipeline/workflow blocks) is what guarantees sibling blocks —
    two parallels, or a parallel then a pipeline at the same level — receive
    distinct keys. Within any one scope execution is sequential (one coroutine),
    so the counter advances deterministically; concurrency only ever happens
    across child scopes, each of which resets its own counter.

    The ``owner`` tag detects the one unsafe case: if a *different* Task advances
    this counter (only reachable by raw-gathering sibling blocks in one scope,
    bypassing parallel/pipeline), we warn once — that pattern races resume keys.
    """
    holder = _seq.get()
    if holder is None:
        holder = _fresh_holder()
        _seq.set(holder)
    cur = _task_id()
    owner = holder.get("owner")
    if owner is None:
        holder["owner"] = cur
    elif owner != cur:
        _warn_concurrent_scope()
    k = holder["n"]
    holder["n"] = k + 1
    return k


def _preview(value: Any) -> str | None:
    """A text preview of an agent result for progress events.

    For strings: returns the full text. For structured results (dicts,
    pydantic models): renders a fixed preamble + complete JSON. No
    truncation — the full data is provided to downstream consumers.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        if hasattr(value, "model_dump") and callable(value.model_dump):
            body = json.dumps(value.model_dump(mode="json"), ensure_ascii=False, default=str)
        else:
            body = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        body = str(value)
    return body


def _outcome_from_result(raw_text: str | None, result: Any) -> str | None:
    """Build AGENT_COMPLETED outcome text from backend raw text or coerced result.

    Prefer non-empty ``raw_text``. Empty string is treated as missing — structured
    schema agents often capture a dict via StructuredOutputTool with ``raw_text=""``,
    and UI must fall back to a JSON preview of ``result``.
    """
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text
    return _preview(result)


def _check_abort(rt) -> None:
    """Raise ``WorkflowAborted`` when an external pause/stop signal is set.

    Called twice per ``agent()`` / session ``send()``: an entry gate (before the
    concurrency permit / backend) stops a queued call; a pre-journal guard (after
    the backend succeeds, before ``journal.use``) ensures a call that finished
    inside the pause window does NOT persist to the WAL — so a resume reruns it.
    The raised ``WorkflowAborted`` carries the signal's ``reason`` (``"pause"``
    vs ``"stop"``) so the caller can tell a resumable pause from a terminal stop.
    A ``None`` signal disables both checks (the back-compat default).
    """
    ev = rt.abort_event
    if ev is not None and ev.is_set():
        raise WorkflowAborted(reason=ev.reason)


def _top_phases(rt) -> list[tuple[str, int]] | None:
    """Top-3 author phases by per-phase token consumption, or ``None`` when empty.

    Reads the per-run ledger's ``phase_tokens`` tally (accumulated by the emit
    hooks below), so an exhausted run can tell the leader which phases burned
    the most tokens — the data the leader needs to redesign a workflow that
    keeps hitting its ceiling.
    """
    acc = rt.workflow_budget.phase_tokens
    if not acc:
        return None
    return sorted(acc.items(), key=lambda kv: kv[1], reverse=True)[:3]


def _check_budget(rt) -> None:
    """Raise ``BudgetExhausted`` when either ledger has hit its ceiling.

    Two ceilings, checked once per ``agent()`` / session ``send()``, at the
    entry gate only: a call already paid for must reach its journal record, or
    a resume would rerun (and re-pay for) it.

    Order-sensitive — **session first**: when both ledgers are dry, the
    terminal (not retryable) session reason wins. Relaunching after a session
    exhaustion hits the same gate immediately, so reporting ``"workflow"``
    would mislead the leader into "redesign the workflow" when raising the
    ceiling is the only way forward. A session still holding headroom never
    masks a per-run (workflow) exhaustion — that check still runs right after.

    This gate alone cannot hold the line — one agent's own loop can burn the
    whole budget long before it returns here. It is the backend's rails that
    stop an agent mid-loop; this stops the *next* one from starting.
    """
    if rt.budget.exhausted:
        raise BudgetExhausted(
            f"session token budget exhausted: {rt.budget.spent}/{rt.budget.total}",
            scope="session", spent=rt.budget.spent, total=rt.budget.total,
            workflow_spent=rt.workflow_budget.spent, workflow_total=rt.workflow_budget.total,
            top_phases=_top_phases(rt),
        )
    if rt.workflow_budget.exhausted:
        raise BudgetExhausted(
            f"workflow token budget exhausted: {rt.workflow_budget.spent}/{rt.workflow_budget.total}",
            scope="workflow", spent=rt.workflow_budget.spent, total=rt.workflow_budget.total,
            workflow_spent=rt.workflow_budget.spent, workflow_total=rt.workflow_budget.total,
            top_phases=_top_phases(rt),
        )


def _branch_disambig(path: tuple) -> str:
    """Serial-disambig over ALL branch segments for ``correlation_id`` uniqueness.

    ``("par", k, i)`` → ``p{i}``, ``("pipe", k, i, s)`` → ``i{i}s{s}``, joined with ``.``.
    Non-branch segments skipped. Empty when no branch (serial / top-level).
    """
    parts = []
    for seg in path:
        if not isinstance(seg, tuple) or len(seg) < 3:
            continue
        tag = seg[0]
        if tag == "par" and len(seg) == 3:
            parts.append(f"p{seg[2]}")
        elif tag == "pipe" and len(seg) == 4:
            parts.append(f"i{seg[2]}s{seg[3]}")
    return ".".join(parts)


def _deepest_branch_i(path: tuple) -> int | None:
    """Deepest branch ``i`` (drop ``s``) for sub-workflow display name ``#N``. ``None`` if no branch.

    Separate from :func:`_branch_disambig`: single-layer ``#N`` for display, vs multi-segment serial for correlation_id.
    """
    i = None
    for seg in path:
        if not isinstance(seg, tuple):
            continue
        if seg and seg[0] == "par" and len(seg) == 3:
            i = seg[2]
        elif seg and seg[0] == "pipe" and len(seg) == 4:
            i = seg[2]  # drop s
    return i


def _budget_snapshot(ledger, scope: str = "session") -> dict:
    """Freeze a ``BudgetLedger`` into the wire shape ``{total, spent, remaining, scope, exhausted}``.

    ``scope`` is ``"session"`` for the team-wide ledger (``rt.budget``) or
    ``"workflow"`` for the per-run ledger (``rt.workflow_budget``). Kept as a
    parameter so emit sites can tag which ledger a snapshot came from; the
    snapshot is a point-in-time observation under concurrency, not a strong
    consistency guarantee.
    """
    return {
        "total": ledger.total,
        "spent": ledger.spent,
        "remaining": ledger.remaining(),
        "scope": scope,
        "exhausted": ledger.exhausted,
    }


def _wf_budget_snapshot(rt) -> dict | None:
    """Snapshot the per-run ledger, always — even when it has no ceiling set.

    A script that does not declare ``workflow_token_limit`` gets an unbounded
    per-run ledger (``total=None``); its snapshot still reports ``spent`` so a
    frontend can render an "unbounded" run-budget badge with live consumption
    (the TUI's own formatter ignores snapshots without a numeric ``total``,
    so this changes nothing for it).
    """
    wb = rt.workflow_budget
    if wb is None:
        return None
    return _budget_snapshot(wb, scope="workflow")


def _resolved_nested_phase(explicit: str | None = None) -> str | None:
    """Return ``explicit`` nested phase, or the per-task display name when inside a sub-workflow."""
    return explicit if explicit is not None else _wf_display_name.get()


def _emit_log(rt, message: str, *, nested_phase: str | None = None) -> None:
    """Emit a LOG progress event with author phase and optional nested display name."""
    rt.progress_sink(
        WorkflowProgressEvent(
            kind=ProgressKind.LOG,
            phase=_current_phase.get(),
            nested_phase=_resolved_nested_phase(nested_phase),
            message=message,
        )
    )


def _emit_agent_started(
    rt,
    opts: dict,
    prompt: str,
    *,
    node_type: str,
    agent_id: str | None = None,
    correlation_id: str | None = None,
    nested_phase: str | None = None,
) -> None:
    rt.current_agent = {
        "agent_id": agent_id,
        "label": opts.get("label"),
        "started_spent": rt.workflow_budget.spent,
    }
    rt.progress_sink(
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_STARTED,
            phase=opts.get("phase") or _current_phase.get(),
            label=opts.get("label"),
            prompt=prompt,
            model=opts.get("model"),
            agent_id=agent_id,
            node_type=node_type,
            correlation_id=correlation_id,
            nested_phase=_resolved_nested_phase(nested_phase),
        )
    )


def _emit_agent_completed(
    rt, opts: dict, outcome_text: str | None, *, agent_id: str | None = None,
    tokens: int | None = None, budget_snapshot: dict | None = None,
    nested_phase: str | None = None,
) -> None:
    """Emit an AGENT_COMPLETED progress event.

    ``outcome_text``: LLM raw text (preferred) or JSON fallback.
    ``tokens``: per-call tokens from ``AgentResult.tokens`` (``None`` on cache-hit).
    ``budget_snapshot``: frozen ``_budget_snapshot(rt.budget)`` at emit time.
    ``nested_phase``: defaults to ``_wf_display_name`` when inside a sub-workflow.
    """
    rt.workflow_budget.add_phase(opts.get("phase") or _current_phase.get() or "?", tokens)
    rt.current_agent = None
    rt.progress_sink(
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_COMPLETED,
            phase=opts.get("phase") or _current_phase.get(),
            label=opts.get("label"),
            outcome=_preview(outcome_text),
            agent_id=agent_id,
            tokens=tokens,
            budget=budget_snapshot,
            workflow_budget=_wf_budget_snapshot(rt),
            nested_phase=_resolved_nested_phase(nested_phase),
        )
    )


def _emit_agent_failed(
    rt, opts: dict, message: str, *, agent_id: str | None = None,
    tokens: int | None = None, budget_snapshot: dict | None = None,
    nested_phase: str | None = None,
) -> None:
    rt.workflow_budget.add_phase(opts.get("phase") or _current_phase.get() or "?", tokens)
    rt.current_agent = None
    rt.progress_sink(
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_FAILED,
            phase=opts.get("phase") or _current_phase.get(),
            label=opts.get("label"),
            message=message,
            agent_id=agent_id,
            tokens=tokens,
            budget=budget_snapshot,
            workflow_budget=_wf_budget_snapshot(rt),
            nested_phase=_resolved_nested_phase(nested_phase),
        )
    )


# ─────────────────────────── options bag ───────────────────────────
#: Engine-owned ``options`` keys. A backend may widen this via its
#: ``KNOWN_OPTIONS``; anything outside the union is a typo and fails fast.
_ENGINE_OPTIONS = frozenset(
    {"label", "phase", "schema", "model", "timeout", "isolation", "agent_type"}
)


def _build_opts(rt, explicit: dict, options: dict | None = None) -> dict:
    """Merge explicit kwargs over an ``options`` bag, drop ``None``, validate keys.

    The session primitives accept tuning knobs through a single ``options`` dict
    (so new knobs need no signature change), with explicit keyword arguments
    taking precedence. Every resulting key must be in
    ``_ENGINE_OPTIONS | rt.backend.KNOWN_OPTIONS`` — an unknown key raises rather
    than silently no-opping, which is the whole point of the bag.

    Args:
        rt: The active runtime (its ``backend`` declares extra allowed keys).
        explicit: Keyword arguments passed directly to the primitive.
        options: The optional ``options`` bag.

    Returns:
        The merged, validated, ``None``-stripped options dict.

    Raises:
        EngineError: If any key is outside the allowed set.
    """
    merged: dict = {}
    for key, value in (options or {}).items():
        if value is not None:
            merged[key] = value
    for key, value in explicit.items():
        if value is not None:
            merged[key] = value
    allowed = _ENGINE_OPTIONS | getattr(rt.backend, "KNOWN_OPTIONS", frozenset())
    unknown = sorted(k for k in merged if k not in allowed)
    if unknown:
        raise EngineError(
            f"unknown option(s) {unknown}; allowed: {sorted(allowed)}"
        )
    return merged


def _resolve_agent_gate(rt):
    """Return the active admission gate, lazily constructing the default sem."""
    from .admission import SemaphoreAdmission

    if rt.agent_gate is None:
        rt.agent_gate = SemaphoreAdmission(rt.make_cap())
    return rt.agent_gate


# ─────────────────────────── agent ───────────────────────────
# Typed return per schema kind:
#   schema=MyModel (pydantic) -> MyModel | None   (attribute access, static types)
#   schema=<JSON Schema dict>  -> dict | None      (interop default)
#   schema=None                -> str | None
@overload
async def agent(
    prompt: str, *, schema: type[M],
    label: str | None = ..., phase: str | None = ...,
    options: dict | None = ...,
) -> "M | None":
    """Overload: ``schema=<pydantic model>`` narrows the result to that model."""
    ...


@overload
async def agent(
    prompt: str, *, schema: dict,
    label: str | None = ..., phase: str | None = ...,
    options: dict | None = ...,
) -> "dict | None":
    """Overload: ``schema=<JSON Schema dict>`` returns a plain ``dict``."""
    ...


@overload
async def agent(
    prompt: str, *, schema: None = ...,
    label: str | None = ..., phase: str | None = ...,
    options: dict | None = ...,
) -> "str | None":
    """Overload: no ``schema`` returns the agent's raw text."""
    ...


async def agent(
    prompt: str,
    *,
    label: str | None = None,
    phase: str | None = None,
    schema: Any = None,
    options: dict | None = None,
) -> Any:
    rt = _rt.get()
    # Orchestration/identity params (label/phase/schema) are explicit; tuning and
    # forward-compat params (model/timeout/isolation/agent_type) ride in the
    # validated ``options`` bag, mirroring ``AgentSession.send``. ``_build_opts``
    # whitelists every key against ``_ENGINE_OPTIONS | backend.KNOWN_OPTIONS``.
    opts = _build_opts(rt, {"label": label, "phase": phase, "schema": schema}, options)
    if opts.get("isolation") not in (None, "worktree"):
        raise EngineError("agent(options={'isolation': ...}) only supports 'worktree'")
    json_schema, model_cls = resolve_schema(opts.get("schema"))

    ks = key_str(_path.get() + (("call", _next_ordinal()),))
    sig = call_signature(prompt, opts, json_schema)

    _emit_agent_started(rt, opts, prompt, node_type="agent", agent_id=ks)

    cached = rt.journal.get_cached(ks, sig, rt.run_id)
    if cached is not None:  # resume hit — no semaphore, no backend
        rt.log_sink(f"[wf] agent {opts.get('label') or 'agent'!r} key={ks} CACHE_HIT sig={sig[:12]}")
        await rt.journal.use(ks, cached)
        result = _rehydrate(cached, model_cls)
        # Prefer stored raw_text; if absent (old journal), fall back to
        # preamble + structured data via _preview()
        outcome_text = _outcome_from_result(cached.get("raw_text"), result)
        # Re-bill the per-run ledger from the record's stored tokens so a
        # resume rebuilds spent by replaying cache hits — the run's tally is
        # recomputed agent-by-agent, not restored from a stale snapshot.
        cached_tokens = cached.get("tokens")
        if isinstance(cached_tokens, int) and cached_tokens > 0:
            rt.workflow_budget.add(cached_tokens)
        _emit_agent_completed(
            rt, opts, outcome_text, agent_id=ks,
            tokens=cached_tokens if isinstance(cached_tokens, int) else None,
            budget_snapshot=_budget_snapshot(rt.budget),
        )
        return result

    rt.log_sink(f"[wf] agent {opts.get('label') or 'agent'!r} key={ks} CACHE_MISS sig={sig[:12]} (live run)")
    _check_abort(rt)  # entry gate: a paused run starts no new agent()
    _check_budget(rt)  # entry gate: a run out of tokens starts no new agent()

    if rt.spawn_count >= rt.spawn_limit:
        rt.log_sink(f"[wf] spawn limit {rt.spawn_limit} reached; skipping {opts.get('label')!r}")
        _emit_agent_failed(
            rt, opts, f"spawn limit {rt.spawn_limit} reached; skipping {opts.get('label')!r}",
            agent_id=ks, tokens=None, budget_snapshot=_budget_snapshot(rt.budget),
        )
        return None

    gate = _resolve_agent_gate(rt)

    async with gate.acquire():
        rt.spawn_count += 1
        call_result = await _call_backend(
            rt, prompt, opts, json_schema, model_cls
        )

    if not call_result.succeeded:
        attempts = rt.retries + 1
        label = opts.get("label") or "agent"
        msg = f"agent {label!r} failed after {attempts} attempts"
        if call_result.error_detail:
            msg = f"{msg}: {call_result.error_detail}"
        _emit_agent_failed(
            rt, opts, msg, agent_id=ks,
            tokens=call_result.tokens, budget_snapshot=_budget_snapshot(rt.budget),
        )
        return None

    _check_abort(rt)  # pre-journal guard: a call finished mid-pause does not persist

    await rt.journal.use(
        ks,
        _make_record(
            _JournalRecordInput(
                key=ks,
                sig=sig,
                opts=opts,
                result=call_result.result,
                model=model_cls,
                raw_text=call_result.raw_text,
                run_id=rt.run_id,
                tokens=call_result.tokens,
            )
        ),
    )
    outcome_text = _outcome_from_result(call_result.raw_text, call_result.result)
    _emit_agent_completed(
        rt, opts, outcome_text, agent_id=ks,
        tokens=call_result.tokens, budget_snapshot=_budget_snapshot(rt.budget),
    )
    return call_result.result


async def _call_backend(rt, prompt, opts, json_schema, model) -> _BackendCallResult:
    """Run the single-shot ``agent()`` call (``backend.run``) with retries."""
    return await _attempt_calls(
        rt, opts, json_schema, model,
        lambda: rt.backend.run(prompt, opts, json_schema),
    )


async def _attempt_calls(rt, opts, json_schema, model, make_call) -> _BackendCallResult:
    """Run ``make_call()`` with retries + schema validation.

    Shared by the single-shot ``agent()`` path (``backend.run``) and the stateful
    session path (``backend.send_turn``); the only difference between them is the
    bound ``make_call`` closure. Returns a ``_BackendCallResult``: a
    backend/timeout error or schema-validation failure retries up to
    ``rt.retries`` extra times; a ``skipped`` result short-circuits to a
    non-success with no retry.
    """
    timeout = opts.get("timeout")
    attempts = rt.retries + 1
    last_err: Exception | None = None
    label = opts.get("label") or "agent"
    # Accumulate tokens burned across every retry attempt: each failed call
    # attaches its budget_rail tally to the raised BackendError (backend) or
    # turn delta (session), so a budget-exhausted/failed agent's real
    # consumption reaches the AGENT_FAILED event instead of being dropped.
    burned_tokens = 0
    for attempt in range(1, attempts + 1):
        try:
            if timeout is not None:
                async with asyncio.timeout(timeout):  # py3.11+
                    res = await make_call()
            else:
                res = await make_call()
        except Exception as e:  # backend / timeout error -> retry, then skip
            last_err = e
            # This attempt burned real tokens before failing (budget-exhausted,
            # backend error, etc.). Accumulate so the final failed result can
            # attribute the agent's full cost, not just the last attempt's.
            burned_tokens += getattr(e, "tokens", 0) or 0
            rt.log_sink(
                f"[wf] agent {label!r} attempt {attempt}/{attempts} failed: {str(e)}"
            )
            continue
        # Token accounting here: the backend already billed this call to the
        # shared ledger as its model calls returned (``AgentBackend.bind_budget``);
        # ``res.tokens`` is the per-call total, threaded through so the emitter
        # can attach it to the AGENT_COMPLETED progress event. The skipped branch
        # leaves ``tokens`` at its default ``None`` — there was no call, so
        # nothing was billed.
        if res.skipped:
            detail = "backend declined (skipped)"
            rt.log_sink(f"[wf] agent {label!r} skipped")
            return _BackendCallResult(result=None, succeeded=False, error_detail=detail)
        if json_schema is not None:
            try:
                coerced = coerce(res.structured, json_schema, model)
                return _BackendCallResult(
                    result=coerced, succeeded=True, raw_text=res.text, tokens=res.tokens
                )
            except Exception as e:  # validation failure -> retry
                last_err = e
                rt.log_sink(
                    f"[wf] agent {label!r} attempt {attempt}/{attempts} "
                    f"validation failed: {str(e)}"
                )
                continue
        return _BackendCallResult(
            result=res.text, succeeded=True, raw_text=res.text, tokens=res.tokens
        )
    detail = str(last_err) if last_err else "unknown error"
    rt.log_sink(f"[wf] agent {label!r} failed after {attempts} attempts: {detail}")
    return _BackendCallResult(
        result=None, succeeded=False, error_detail=detail,
        tokens=burned_tokens if burned_tokens > 0 else None,
    )


def _rehydrate(rec: dict, model) -> Any:
    kind = rec.get("kind")
    if kind == "null":
        return None
    val = rec.get("result")
    if kind == "model" and model is not None:
        return model.model_validate(val)
    return val


@dataclass
class _JournalRecordInput:
    key: str
    sig: str
    opts: dict
    result: Any
    model: Any
    raw_text: str | None = None
    run_id: str | None = None
    tokens: int | None = None


def _make_record(spec: _JournalRecordInput) -> dict:
    if spec.result is None:
        kind, payload = "null", None
    elif spec.model is not None and isinstance(spec.result, spec.model):
        kind, payload = "model", spec.result.model_dump(mode="json")
    elif isinstance(spec.result, str):
        kind, payload = "str", spec.result
    else:
        kind, payload = "dict", spec.result  # dict / list
    return {
        "key": spec.key,
        "sig": spec.sig,
        "run_id": spec.run_id,
        "tokens": spec.tokens,
        "label": spec.opts.get("label"),
        "phase": spec.opts.get("phase"),
        "kind": kind,
        "result": payload,
        "raw_text": spec.raw_text,
    }


# ─────────────────────────── verify ───────────────────────────
def _reviewer_call(
    i: int,
    reviewer: Reviewer,
    *,
    base: str,
    phase: str | None,  # pylint: disable=huawei-redefined-outer-name
    options: dict | None,
):
    """Build the zero-arg thunk that runs one reviewer as a structured ``agent()``.

    A verdict reviewer votes against ``VERDICT_SCHEMA`` (pass/fail + feedback),
    a score reviewer against ``SCORE_SCHEMA`` (0-1 + feedback). Reviewer-level
    options override the ``verify()``-level ones. The thunk is meant for
    :func:`parallel`, which gives each reviewer its own structural journal key.
    """
    schema = VERDICT_SCHEMA if reviewer.kind == "verdict" else SCORE_SCHEMA
    rlabel = reviewer.label or f"{base}-{i}"
    merged = {**(options or {}), **(reviewer.options or {})} or None

    async def _call():
        return await agent(
            reviewer.prompt,
            label=rlabel,
            phase=phase,
            schema=schema,
            options=merged,
        )

    return _call


def _collect(reviewers: Sequence[Reviewer], raws: Sequence) -> tuple[list[VerifyVote], dict]:
    """Fold each reviewer's raw vote (or ``None`` when it did not vote) into votes + a tally.

    A ``None`` raw (``agent()`` failed / skipped) marks the reviewer as
    not-voted: it contributes its pool's *total* but not its *voted* count, so
    ``settle_verify_tally`` yields undecided rather than a silent pass.
    """
    votes: list[VerifyVote] = []
    verdict_total = verdict_voted = verdict_fail = 0
    score_total = score_voted = 0
    score_sum = 0.0

    for reviewer, raw in zip(reviewers, raws):
        if reviewer.kind == "verdict":
            verdict_total += 1
            if raw is None:
                votes.append(VerifyVote(kind="verdict"))
                continue
            decision = raw.get("decision")
            fail = decision == "fail"
            if decision in ("pass", "fail"):
                verdict_voted += 1
                if fail:
                    verdict_fail += 1
                decision_pass = not fail
            else:
                decision_pass = None  # malformed decision: not counted, vote reads undecided
            votes.append(VerifyVote(kind="verdict", decision=decision_pass, feedback=raw.get("feedback", "")))
        else:
            score_total += 1
            if raw is None:
                votes.append(VerifyVote(kind="score"))
                continue
            score = raw.get("score")
            if isinstance(score, (int, float)):
                score_voted += 1
                score_sum += float(score)
            else:
                score = None  # malformed score: not counted, vote reads undecided
            votes.append(VerifyVote(kind="score", score=score, feedback=raw.get("feedback", "")))

    tally = {
        "verdict_total": verdict_total,
        "verdict_voted": verdict_voted,
        "verdict_fail_count": verdict_fail,
        "score_count": score_total,
        "score_voted": score_voted,
        "score_avg": (score_sum / score_voted) if score_voted else None,
    }
    return votes, tally


def _aggregate_feedback(reviewers: Sequence[Reviewer], votes: Sequence[VerifyVote]) -> str:
    """Join every reviewer's non-empty feedback into an attributed block for rework.

    Mirrors the scheduled ``format_fail_feedback`` shape (``- reviewer: text``),
    attributing each line by the reviewer's label (falling back to its kind).
    """
    lines = []
    for reviewer, vote in zip(reviewers, votes):
        fb = (vote.feedback or "").strip()
        if not fb:
            continue
        name = reviewer.label or reviewer.kind
        lines.append(f"- {name}: {fb}")
    return "\n".join(lines)


async def verify(
    reviewers: Sequence[Reviewer],
    *,
    threshold: float = 0.85,
    label: str | None = None,
    phase: str | None = None,  # pylint: disable=huawei-redefined-outer-name
    options: dict | None = None,
) -> VerifyResult:
    """Run one review round over ``reviewers`` and return a structured verdict.

    Each reviewer is a single structured ``agent()`` call fanned out via
    :func:`parallel` (each vote gets its own journal key, so resume replays the
    reviewer calls). Votes are folded by :func:`_collect` and judged by
    ``settle_verify_tally``.

    This is a **single-shot** round: it does not wait on or drive a rework loop.
    A reviewer whose ``agent()`` returned ``None`` counts as not-voted, so the
    round returns ``verdict=None`` (undecided) unless every reviewer of every
    pool has voted. An empty ``reviewers`` list is rejected.

    Args:
        reviewers: The reviewers to run. Must be non-empty.
        threshold: Minimum average score for the score pool to pass.
        label: Base label for reviewer progress events (fallback name prefix).
        phase: Phase attributed to every reviewer ``agent()`` call.
        options: Default ``options`` bag applied to every reviewer (reviewer-level
            options take precedence).

    Returns:
        A ``VerifyResult`` with the round verdict, per-reviewer votes and the
        aggregated review feedback.

    Raises:
        EngineError: If ``reviewers`` is empty.
    """
    rt = _rt.get()
    reviewers = list(reviewers)
    if not reviewers:
        raise EngineError("verify() requires at least one reviewer")
    base = label or "verify"

    _emit_log(rt, f"verify: dispatching {len(reviewers)} reviewer(s)")
    raws = await parallel(
        [_reviewer_call(i, r, base=base, phase=phase, options=options) for i, r in enumerate(reviewers)]
    )

    votes, tally = _collect(reviewers, raws)
    verdict = settle_verify_tally(tally, threshold)
    result = VerifyResult(
        verdict=verdict,
        votes=votes,
        feedback=_aggregate_feedback(reviewers, votes),
        passed=verdict == "pass",
    )
    _emit_log(rt, f"verify: verdict={verdict} (threshold={threshold})")
    return result


# ─────────────────────── stateful sessions ───────────────────────
def _jsonable(result: Any, model) -> Any:
    """A JSON-able form of a turn result for the history mirror (cf. ``_make_record``)."""
    if result is None:
        return None
    if model is not None and isinstance(result, model):
        return result.model_dump(mode="json")
    return result


def _warn_concurrent_session(rt) -> None:
    """Emitted once if a single session object receives overlapping ``send()``s."""
    if getattr(rt, "warned_concurrent_session", False):
        return
    rt.warned_concurrent_session = True
    rt.log_sink(
        "[wf] WARNING: a single session received concurrent send()s (e.g. the same "
        "session used from two parallel() branches). A session is a serial "
        "conversation — await its turns in order, or use one session per entity."
    )


@dataclass
class _TurnRequest:
    """One session turn's request payload, threaded through ``_drive``/``_turn``.

    Bundles the per-turn parameters so the drive/turn helpers take a single
    request object instead of a long positional list.
    """

    prompt: str
    opts: dict
    json_schema: dict | None
    model_cls: Any
    correlation_id: str | None


class AgentSession:
    """A stateful, multi-turn handle over one agent — or one human.

    Created by :func:`agent_session` / :func:`human_session`; scripts never
    construct it directly. Each :meth:`send` advances the conversation and keeps
    context across turns. The backend owns the real state (a long-lived avatar
    harness); this object keeps a light ``(user, assistant)`` history mirror used
    for the resume signature and lazy-open bookkeeping.

    A human session (``_human=True``) sources each turn's input from a real
    person (the backend formats it into the requested shape) and does not hold
    the LLM concurrency permit while waiting; it is otherwise identical to an
    agent session.
    """

    __slots__ = (
        "_label", "_phase", "_instructions", "_options", "_human", "_node_type",
        "_history", "_sid", "_member_name", "_in_flight", "_fork_data",
    )

    def __init__(
        self,
        *,
        label: str | None = None,
        phase: str | None = None,
        instructions: str | None = None,
        options: dict | None = None,
        _human: bool = False,
        _node_type: str = "agent_session",
        _fork_data: dict | None = None,
        _history: list[dict] | None = None,
    ) -> None:
        self._label = label
        self._phase = phase
        self._instructions = instructions
        self._options = dict(options or {})
        self._human = _human
        self._node_type = _node_type
        self._history: list[dict] = list(_history) if _history else []
        self._sid: str | None = None
        self._member_name: str | None = None
        self._in_flight = False
        self._fork_data = _fork_data

    @overload
    async def send(self, prompt: str, *, notify: Literal[True], options: dict | None = ...) -> None:
        """Overload: ``notify=True`` is a one-way push and returns ``None``."""
        ...

    @overload
    async def send(self, prompt: str, *, schema: type[M], options: dict | None = ...) -> "M | None":
        """Overload: ``schema=<pydantic model>`` narrows the reply to that model."""
        ...

    @overload
    async def send(self, prompt: str, *, schema: dict, options: dict | None = ...) -> "dict | None":
        """Overload: ``schema=<JSON Schema dict>`` returns a plain ``dict``."""
        ...

    @overload
    async def send(self, prompt: str, *, schema: None = ..., options: dict | None = ...) -> "str | None":
        """Overload: no ``schema`` returns the reply's raw text."""
        ...

    async def send(self, prompt, *, schema=None, notify=False, options=None):
        """Advance the conversation one turn (or push a one-way ``notify``).

        With ``schema`` the reply is validated/coerced to it (``MyModel | None``
        for a pydantic model, ``dict | None`` for a JSON-Schema dict); without,
        the raw text (``str | None``). ``notify=True`` pushes a one-way message
        (still recorded so context continues, still journaled for resume) and
        returns ``None``; it is text-only and rejects a ``schema``.
        """
        rt = _rt.get()
        if notify and schema is not None:
            raise EngineError("send(notify=True) is text-only; don't also pass a schema")
        # Per-task current phase; fall back to the session's own default.
        phase_val = _current_phase.get() if _current_phase.get() is not None else self._phase
        opts = _build_opts(
            rt,
            {"label": self._label, "phase": phase_val, "schema": schema},
            {**self._options, **(options or {})},
        )
        json_schema, model_cls = resolve_schema(opts.get("schema"))

        ks = key_str(_path.get() + (("call", _next_ordinal()),))
        sig = call_signature(prompt, opts, json_schema, history=self._history)

        correlation_id = self._correlation_id(opts)

        if self._in_flight:
            _warn_concurrent_session(rt)
        self._in_flight = True
        try:
            _emit_agent_started(
                rt, opts, prompt,
                node_type=self._node_type,
                agent_id=ks,
                correlation_id=correlation_id,
            )

            # Reserve the member identity on the FIRST turn regardless of cache
            # hit, so a fully-hit resume still knows this session's member name
            # (fork() needs it to locate the parent's persisted context). Only
            # runs once — no avatar, no LLM, no spawn/budget slot.
            if self._member_name is None and not self._human:
                await self._ensure_member_name(rt, opts)

            cached = rt.journal.get_cached(ks, sig, rt.run_id)
            if cached is not None:  # resume hit — no backend, no harness, no person
                rt.log_sink(f"[wf] session {opts.get('label') or 'session'!r} key={ks} CACHE_HIT sig={sig[:12]}")
                await rt.journal.use(ks, cached)
                result = _rehydrate(cached, model_cls)
                self._append_history(prompt, result, model_cls)
                outcome_text = _outcome_from_result(cached.get("raw_text"), result)
                cached_tokens = cached.get("tokens")
                if isinstance(cached_tokens, int) and cached_tokens > 0:
                    rt.workflow_budget.add(cached_tokens)
                _emit_agent_completed(
                    rt, opts, outcome_text, agent_id=ks,
                    tokens=cached_tokens if isinstance(cached_tokens, int) else None,
                    budget_snapshot=_budget_snapshot(rt.budget),
                )
                return None if notify else result

            rt.log_sink(
                f"[wf] session {opts.get('label') or 'session'!r} key={ks} CACHE_MISS "
                f"sig={sig[:12]} (live run, history_len={len(self._history)})"
            )
            _check_abort(rt)  # entry gate: a paused run starts no new turn
            _check_budget(rt)  # entry gate: a run out of tokens starts no new turn

            req = _TurnRequest(
                prompt=prompt,
                opts=opts,
                json_schema=json_schema,
                model_cls=model_cls,
                correlation_id=correlation_id,
            )
            call_result = await self._drive(rt, req)
            if not call_result.succeeded:
                attempts = rt.retries + 1
                who = "human" if self._human else "agent"
                label = opts.get("label") or who
                msg = f"{who} session {label!r} failed after {attempts} attempts"
                if call_result.error_detail:
                    msg = f"{msg}: {call_result.error_detail}"
                _emit_agent_failed(
                    rt, opts, msg, agent_id=ks,
                    tokens=call_result.tokens, budget_snapshot=_budget_snapshot(rt.budget),
                )
                return None

            result = call_result.result
            _check_abort(rt)  # pre-journal guard: an interrupted turn does not persist
            await rt.journal.use(
                ks,
                _make_record(
                    _JournalRecordInput(
                        key=ks,
                        sig=sig,
                        opts=opts,
                        result=result,
                        model=model_cls,
                        raw_text=call_result.raw_text,
                        run_id=rt.run_id,
                        tokens=call_result.tokens,
                    )
                ),
            )
            self._append_history(prompt, result, model_cls)
            outcome_text = _outcome_from_result(call_result.raw_text, result)
            _emit_agent_completed(
                rt, opts, outcome_text, agent_id=ks,
                tokens=call_result.tokens, budget_snapshot=_budget_snapshot(rt.budget),
            )
            return None if notify else result
        finally:
            self._in_flight = False

    async def aclose(self) -> None:
        """Close the backing session if it was ever opened (idempotent)."""
        if self._sid is None:
            return
        rt = _rt.get()
        sid, self._sid = self._sid, None
        await rt.backend.close_session(sid)

    async def fork(
        self,
        *,
        fork_mode: str = "full",
        keep_rounds: int | None = None,
        label: str | None = None,
        phase: str | None = None,  # pylint: disable=huawei-redefined-outer-name
        instructions: str | None = None,
        options: dict | None = None,
    ) -> "AgentSession":
        """Fork this session into a fresh, independent session (context inherited).

        The five ``fork_mode`` values mirror the team fork modes (``full`` /
        ``before`` / ``after`` / ``keep_before_compact_after`` /
        ``keep_after_compact_before``); ``keep_rounds`` is the split point in
        **rounds** (each prior ``send()`` is one round) for the truncating /
        compacting modes. Every mode but ``full`` requires ``keep_rounds`` —
        omitting it raises (there is no truncation without a split point).
        ``keep_rounds`` beyond the parent's actual round count is not an error;
        the backend warns and silently falls back to a full-context fork (the
        team fork's "wrong name → full" guard). The parent context is captured
        **eagerly** here (the fork point), so later ``send()``s on this session
        do not affect the child.

        The child inherits this session's ``_history`` mirror (resume-signature
        consistency) and, when the backend returns a ``fork_data`` snapshot, a
        full context including ToolMessage. A fork of a ``human_session`` is
        rejected. The child is ``_node_type="agent_session_fork"`` when the
        parent has history (so a fork turn is observable as a branch), else a
        plain ``agent_session`` (a parent that never sent has nothing to
        inherit).
        """
        if self._human:
            raise EngineError("fork() is only supported on agent_session")
        if fork_mode != "full" and keep_rounds is None:
            raise EngineError(
                "fork() requires keep_rounds unless fork_mode='full'"
                f" (fork_mode={fork_mode!r} has no split point without it)"
            )
        fork_data = None
        if self._member_name is not None:
            rt = _rt.get()
            fork_data = await rt.backend.capture_fork(
                self._member_name,
                keep_rounds=keep_rounds,
                fork_mode=fork_mode,
            )
        return AgentSession(
            label=label if label is not None else self._label,
            phase=phase if phase is not None else self._phase,
            instructions=instructions if instructions is not None else self._instructions,
            options={**self._options, **(options or {})},
            _human=False,
            _node_type="agent_session_fork" if self._history else "agent_session",
            _history=[dict(m) for m in self._history],
            _fork_data=fork_data,
        )

    async def _drive(self, rt, req: _TurnRequest):
        """Open the session lazily, then run one turn through the retry helper.

        Agent turns mirror ``agent()`` (spawn-budget gate + LLM permit); human
        turns skip both — waiting on a person must not hold a concurrency permit.
        """
        if self._human:
            await self._ensure_open(rt, req.opts)
            return await self._turn(rt, req)
        if rt.spawn_count >= rt.spawn_limit:
            detail = f"spawn limit {rt.spawn_limit} reached"
            rt.log_sink(f"[wf] {detail}; skipping {req.opts.get('label')!r}")
            return _BackendCallResult(result=None, succeeded=False, error_detail=detail)
        gate = _resolve_agent_gate(rt)
        async with gate.acquire():
            await self._ensure_open(rt, req.opts)
            return await self._turn(rt, req)

    async def _turn(self, rt, req: _TurnRequest):
        """Run one ``send_turn`` (the prior history is what the backend may replay)."""
        hist = list(self._history)
        sid = self._sid
        return await _attempt_calls(
            rt, req.opts, req.json_schema, req.model_cls,
            lambda: rt.backend.send_turn(
                sid,
                req.prompt,
                req.opts,
                req.json_schema,
                history=hist,
                correlation_id=req.correlation_id,
            ),
        )

    def _correlation_id(self, opts: dict) -> str:
        """Deterministic turn id with optional branch disambig.

        Serial: ``{phase}:{label}:{turn}``. With branch: ``{phase}#{disambig}:{label}:{turn}``.
        Disambig serial-concats all branch segments (``p{i}``/``i{i}s{s}``, ``.``-joined).
        Stable across resume; turn = ``len(history) // 2``.
        """
        phase = opts.get("phase") or "_"
        label = opts.get("label") or "human"
        turn = len(self._history) // 2
        disambig = _branch_disambig(_path.get())
        phase_seg = f"{phase}#{disambig}" if disambig else phase
        return f"{phase_seg}:{label}:{turn}"

    async def _ensure_member_name(self, rt, opts) -> None:
        """Reserve this session's member identity (once), no avatar built.

        Runs on the first turn regardless of cache hit so a fully-hit resume
        still knows the member name ``fork()`` needs to locate the parent's
        persisted context. Pure in-process backend bookkeeping (a counter
        increment) — no harness, no LLM, no spawn/budget slot.
        """
        if self._member_name is not None:
            return
        self._member_name = await rt.backend.ensure_member_name(
            kind="agent",
            opts=opts,
        )

    async def _ensure_open(self, rt, opts) -> None:
        """Open the backend session on the first real turn (one avatar per session)."""
        if self._sid is not None:
            return
        if not self._human:
            rt.spawn_count += 1  # the avatar is this session's one spawned agent
        # Reuse the identity already reserved on the first turn (cache-hit or
        # miss) so we never re-mint a name and drift the counter across a resume.
        if not self._human:
            await self._ensure_member_name(rt, opts)
        rt.log_sink(f"[wf] session {opts.get('label') or 'session'!r} opening backend session (avatar (re)created)")
        self._sid = await rt.backend.open_session(
            kind="human" if self._human else "agent",
            instructions=self._instructions,
            opts=opts,
            fork_data=self._fork_data,
            member_name=self._member_name,
        )

    def _append_history(self, prompt: str, result: Any, model_cls) -> None:
        """Append the ``(user, assistant)`` pair so the next turn carries context."""
        self._history.append({"role": "user", "content": prompt})
        self._history.append({"role": "assistant", "content": _jsonable(result, model_cls)})


def agent_session(
    *,
    label: str | None = None,
    phase: str | None = None,
    instructions: str | None = None,
    options: dict | None = None,
) -> AgentSession:
    """Open a stateful, multi-turn agent — ``send()`` it repeatedly; context persists."""
    return AgentSession(
        label=label,
        phase=phase,
        instructions=instructions,
        options=options,
        _node_type="agent_session",
    )


def human_session(
    *,
    label: str | None = None,
    phase: str | None = None,
    instructions: str | None = None,
    options: dict | None = None,
) -> AgentSession:
    """Open a stateful, multi-turn human participant (each turn's input from a person)."""
    return AgentSession(
        label=label,
        phase=phase,
        instructions=instructions,
        options=options,
        _human=True,
        _node_type="human_session",
    )


#: Annotation alias: ``human_session()`` returns the same class with ``_human`` set.
HumanSession = AgentSession


@overload
async def human(
    prompt: str, *, schema: type[M],
    label: str | None = ..., phase: str | None = ..., options: dict | None = ...,
) -> "M | None":
    """Overload: ``schema=<pydantic model>`` narrows the answer to that model."""
    ...


@overload
async def human(
    prompt: str, *, schema: dict,
    label: str | None = ..., phase: str | None = ..., options: dict | None = ...,
) -> "dict | None":
    """Overload: ``schema=<JSON Schema dict>`` returns a plain ``dict``."""
    ...


@overload
async def human(
    prompt: str, *, schema: None = ...,
    label: str | None = ..., phase: str | None = ..., options: dict | None = ...,
) -> "str | None":
    """Overload: no ``schema`` returns the person's answer as raw text."""
    ...


async def human(prompt, *, schema=None, label=None, phase=None, options=None):
    """One-shot human turn: ask a person once, return their (typed) answer.

    Sugar over an ephemeral :func:`human_session` opened, asked once, and closed —
    use :func:`human_session` when you need multiple turns with memory. ``label`` /
    ``phase`` mirror :func:`agent` / :func:`human_session`: they name the turn in
    progress events and the deterministic human correlation id.
    """
    s = AgentSession(
        _human=True,
        _node_type="human",
        label=label,
        phase=phase,
    )
    try:
        return await s.send(prompt, schema=schema, options=options)
    finally:
        await s.aclose()


# ─────────────────────── parallel (barrier) ───────────────────────
async def parallel(thunks: Sequence[Callable[[], Awaitable]]) -> list:
    k = _next_ordinal()  # this block's slot in the parent scope
    base = _path.get()
    thunks = list(thunks)
    if len(thunks) > _MAX_FANOUT:
        raise EngineError(
            f"parallel() got {len(thunks)} thunks; the per-call limit is "
            f"{_MAX_FANOUT}. Split into batches instead of one giant fan-out."
        )

    async def branch(i: int, th: Callable[[], Awaitable]):
        # Runs in a Task-copied context; sets are private to this branch.
        _path.set(base + (("par", k, i),))
        _seq.set(_fresh_holder())
        try:
            r = th()
            return await r if inspect.isawaitable(r) else r
        except Exception:
            # CancelledError is BaseException, not caught here — cancellation
            # still propagates out of the branch; only real errors map to None.
            return None

    return await asyncio.gather(*[branch(i, th) for i, th in enumerate(thunks)])


# ─────────────────────── pipeline (streaming) ───────────────────────
async def pipeline(items: Sequence, *stages: Callable) -> list:
    k = _next_ordinal()  # this block's slot in the parent scope
    base = _path.get()
    items = list(items)
    if len(items) > _MAX_FANOUT:
        raise EngineError(
            f"pipeline() got {len(items)} items; the per-call limit is "
            f"{_MAX_FANOUT}. Split into batches instead of one giant fan-out."
        )

    async def chain(i: int, item: Any):
        prev = item
        try:
            for s, stage in enumerate(stages):
                _path.set(base + (("pipe", k, i, s),))
                _seq.set(_fresh_holder())
                r = stage(prev, item, i)  # stage may be sync (returning awaitable) or async
                prev = await r if inspect.isawaitable(r) else r
            return prev
        except Exception:
            # CancelledError is BaseException, not caught here — cancellation
            # still propagates; the first throwing stage drops THIS item only.
            return None

    return await asyncio.gather(*[chain(i, it) for i, it in enumerate(items)])


# ─────────────────────── ergonomic fan-out ───────────────────────
def _arity(fn: Callable) -> int:
    try:
        params = [
            p
            for p in inspect.signature(fn).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        return len(params)
    except (TypeError, ValueError):
        return 1


async def map_parallel(items: Sequence, fn: Callable) -> list:
    """Footgun-free fan-out: ``fn`` is ``async def fn(item)`` or ``fn(item, i)``.

    Binds each item correctly (no late-binding closure trap), then defers to
    :func:`parallel`.
    """
    items = list(items)
    if _arity(fn) >= 2:
        thunks = [(lambda it=it, i=i: fn(it, i)) for i, it in enumerate(items)]
    else:
        thunks = [(lambda it=it: fn(it)) for it in items]
    return await parallel(thunks)


pmap = map_parallel  # alias


# ─────────────────────── phase / log / budget ───────────────────────
def phase(title: str) -> None:
    rt = _rt.get()
    _current_phase.set(title)
    _parent_phase.set(title)
    rt.progress_sink(WorkflowProgressEvent(kind=ProgressKind.PHASE, phase=title))


def log(message: Any) -> None:
    _emit_log(_rt.get(), str(message))


class _Budget:
    """Reads the active run's **workflow-level** ledger via the contextvar.

    Importable & run-agnostic: the public ``budget`` name in scripts now reports
    the per-run budget (``rt.workflow_budget``, sourced from
    ``META.workflow_token_limit``) — not the session-wide ledger — so a script
    polling ``budget.remaining()`` sees exactly what "this run" has left before
    its own ceiling, which is the ceiling the engine enforces for the run.

    Live rather than end-of-call: the ledger is written by the backend as each
    model call returns, so a script polling ``remaining()`` sees the burn of
    every agent currently in flight, not just the ones that have returned.
    """

    @property
    def total(self) -> int | None:
        return _rt.get().workflow_budget.total

    @staticmethod
    def spent() -> int:
        return _rt.get().workflow_budget.spent

    @staticmethod
    def remaining() -> int | None:
        return _rt.get().workflow_budget.remaining()


#: Importable singleton: `from swarmflow import budget` then `budget.spent()`.
budget = _Budget()


# ─────────────────────── inline sub-workflow ───────────────────────
async def workflow(name_or_path: str, args: Any = None) -> Any:
    """Run a sub-workflow inline, return its ``run()`` result.

    Safe to fan out via ``parallel``/``pipeline`` — depth tracked per Task (ContextVar).
    Emits child ``PHASE`` (``phase_type="child"``, display name ``▸ {name} #{N}``,
    ``parent_phase`` from enclosing ``phase()``) so Monitor builds a child card.
    Sets ``_wf_display_name`` (per-task ContextVar) so ``agent()``/``log()``
    pick up the display name without touching ``_current_phase``.
    """
    rt = _rt.get()
    parent_phase = _parent_phase.get() or _current_phase.get()
    depth = _wf_depth.get()
    if depth >= _MAX_WORKFLOW_DEPTH:
        _emit_log(
            rt,
            f"[wf] nested workflow depth > {_MAX_WORKFLOW_DEPTH} not allowed; skipping",
        )
        return None
    from .loader import load_workflow_source  # lazy: avoid import cycle

    loaded = load_workflow_source(name_or_path)
    name = loaded.meta.get("name", str(name_or_path)) if isinstance(loaded.meta, dict) else str(name_or_path)
    # Display name: deepest branch segment i (drop s); pure #N, NOT _branch_disambig.
    display_name = f"▸ {name}"
    branch_i = _deepest_branch_i(_path.get())
    if branch_i is not None:
        display_name = f"▸ {name} #{branch_i}"
    k = _next_ordinal()
    # Emit child PHASE declaration BEFORE pushing the wf segment + resetting seq,
    # so a progress consumer (Monitor) can open a separate child card. ``phase``
    # is the script's original phase name; ``nested_phase`` carries the display
    # name; ``parent_phase`` links back to the enclosing author phase.
    rt.log_sink(f"[wf] child phase declared: {display_name}, parent: {parent_phase}, name: {name}")
    rt.progress_sink(WorkflowProgressEvent(
        kind=ProgressKind.PHASE,
        phase=name,
        phase_type="child",
        nested_phase=display_name,
        parent_phase=parent_phase,
    ))
    tok_d = _wf_depth.set(depth + 1)
    tok_p = _path.set(_path.get() + (("wf", k, name),))
    tok_s = _seq.set(_fresh_holder())
    saved_parent = _parent_phase.set(parent_phase)
    phase_name = _wf_display_name.set(display_name)
    try:
        return await _invoke_loaded(loaded, args)
    finally:
        _seq.reset(tok_s)
        _path.reset(tok_p)
        _wf_depth.reset(tok_d)
        _parent_phase.reset(saved_parent)
        _wf_display_name.reset(phase_name)


# ─────────────────────── list helpers (JS idioms) ───────────────────────
def compact(xs: Sequence) -> list:
    """``xs.filter(Boolean)`` — drops every falsy element (None, '', 0, [])."""
    return [x for x in xs if x]


def flatten_filter(xs: Sequence) -> list:
    """``xs.flat().filter(Boolean)`` — one level, None sublists tolerated."""
    out: list = []
    for sub in xs:
        for x in sub or []:
            if x:
                out.append(x)
    return out


# ─────────────────────── entrypoint ───────────────────────
async def _invoke_loaded(loaded, args: Any) -> Any:
    """Run a loaded workflow's entrypoint.

    A SwarmFlow file is a real Python module exposing ``async def run(args)`` (or
    ``run()``), loaded via importlib. It imports the primitives it needs; each
    call resolves the active run via the ``_rt`` contextvar, so concurrent
    workflows in one loop stay isolated (each runs in its own Task → its own
    context copy).
    """
    run_fn = getattr(loaded.module, "run", None)
    if not inspect.iscoroutinefunction(run_fn):
        raise EngineError(f"{loaded.path}: must define `async def run(args)`")
    return await (run_fn(args) if _arity(run_fn) >= 1 else run_fn())
