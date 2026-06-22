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

from .errors import WorkflowError
from .journal import call_signature, key_str
from .progress import ProgressKind, WorkflowProgressEvent
from .schema import coerce, resolve_schema

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
    """

    result: Any = None
    succeeded: bool = False
    error_detail: str | None = None
    raw_text: str | None = None


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


def _emit_agent_started(rt, opts: dict, prompt: str) -> None:
    rt.progress_sink(
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_STARTED,
            phase=opts.get("phase") or rt.current_phase,
            label=opts.get("label"),
            prompt=prompt,
            model=opts.get("model"),
        )
    )


def _emit_agent_completed(rt, opts: dict, outcome_text: str | None) -> None:
    """Emit an AGENT_COMPLETED progress event.

    ``outcome_text`` is a human-readable summary of the agent's result —
    the LLM's raw text reply (preferred), or a preamble + complete JSON
    fallback when raw text is unavailable and the result is structured.
    """
    rt.progress_sink(
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_COMPLETED,
            phase=opts.get("phase") or rt.current_phase,
            label=opts.get("label"),
            outcome=_preview(outcome_text),
        )
    )


def _emit_agent_failed(rt, opts: dict, message: str) -> None:
    rt.progress_sink(
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_FAILED,
            phase=opts.get("phase") or rt.current_phase,
            label=opts.get("label"),
            message=message,
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
        WorkflowError: If any key is outside the allowed set.
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
        raise WorkflowError(
            f"unknown option(s) {unknown}; allowed: {sorted(allowed)}"
        )
    return merged


# ─────────────────────────── agent ───────────────────────────
# Typed return per schema kind:
#   schema=MyModel (pydantic) -> MyModel | None   (attribute access, static types)
#   schema=<JSON Schema dict>  -> dict | None      (interop default)
#   schema=None                -> str | None
@overload
async def agent(
    prompt: str, *, schema: type[M],
    label: str | None = ..., phase: str | None = ...,
    model: str | None = ..., timeout: float | None = ...,
    isolation: Literal["worktree"] | None = ..., agent_type: str | None = ...,
) -> "M | None":
    """Overload: ``schema=<pydantic model>`` narrows the result to that model."""
    ...


@overload
async def agent(
    prompt: str, *, schema: dict,
    label: str | None = ..., phase: str | None = ...,
    model: str | None = ..., timeout: float | None = ...,
    isolation: Literal["worktree"] | None = ..., agent_type: str | None = ...,
) -> "dict | None":
    """Overload: ``schema=<JSON Schema dict>`` returns a plain ``dict``."""
    ...


@overload
async def agent(
    prompt: str, *, schema: None = ...,
    label: str | None = ..., phase: str | None = ...,
    model: str | None = ..., timeout: float | None = ...,
    isolation: Literal["worktree"] | None = ..., agent_type: str | None = ...,
) -> "str | None":
    """Overload: no ``schema`` returns the agent's raw text."""
    ...


async def agent(
    prompt: str,
    *,
    label: str | None = None,
    phase: str | None = None,
    schema: Any = None,
    model: str | None = None,
    timeout: float | None = None,
    isolation: Literal["worktree"] | None = None,
    agent_type: str | None = None,
) -> Any:
    rt = _rt.get()
    if isolation is not None and isolation != "worktree":
        raise WorkflowError("agent(isolation=...) only supports 'worktree'")
    opts: dict = {}
    # ``isolation`` / ``agent_type`` are carried in opts for forward-compat with
    # the reference tool's surface, but the reference engine's backend does not
    # act on them yet (a worktree-isolated or typed sub-agent is not spawned);
    # they are accepted, not silently rejected. TODO: wire them into the backend
    # and into ``call_signature`` once isolated / typed execution lands.
    for _k, _v in (
        ("label", label), ("phase", phase), ("schema", schema),
        ("model", model), ("timeout", timeout),
        ("isolation", isolation), ("agent_type", agent_type),
    ):
        if _v is not None:
            opts[_k] = _v
    json_schema, model_cls = resolve_schema(opts.get("schema"))

    ks = key_str(_path.get() + (("call", _next_ordinal()),))
    sig = call_signature(prompt, opts, json_schema)

    _emit_agent_started(rt, opts, prompt)

    cached = rt.journal.get_cached(ks, sig)
    if cached is not None:  # resume hit — no semaphore, no backend
        await rt.journal.use(ks, cached)
        result = _rehydrate(cached, model_cls)
        # Prefer stored raw_text; if absent (old journal), fall back to
        # preamble + structured data via _preview()
        outcome_text = cached.get("raw_text") or _preview(result)
        _emit_agent_completed(rt, opts, outcome_text)
        return result

    if rt.spawn_count >= rt.spawn_limit:
        rt.log_sink(f"[wf] spawn limit {rt.spawn_limit} reached; skipping {opts.get('label')!r}")
        _emit_agent_failed(rt, opts, f"spawn limit {rt.spawn_limit} reached; skipping {opts.get('label')!r}")
        return None

    if rt.sem is None:  # safety net; normally created in run_workflow
        rt.sem = asyncio.Semaphore(rt.make_cap())

    async with rt.sem:
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
        _emit_agent_failed(rt, opts, msg)
        return None

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
            )
        ),
    )
    outcome_text = call_result.raw_text or _preview(call_result.result)
    _emit_agent_completed(rt, opts, outcome_text)
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
    for attempt in range(1, attempts + 1):
        try:
            if timeout is not None:
                async with asyncio.timeout(timeout):  # py3.11+
                    res = await make_call()
            else:
                res = await make_call()
        except Exception as e:  # backend / timeout error -> retry, then skip
            last_err = e
            rt.log_sink(
                f"[wf] agent {label!r} attempt {attempt}/{attempts} failed: {str(e)}"
            )
            continue
        rt.tokens_spent += res.tokens
        if res.skipped:
            detail = "backend declined (skipped)"
            rt.log_sink(f"[wf] agent {label!r} skipped")
            return _BackendCallResult(result=None, succeeded=False, error_detail=detail)
        if json_schema is not None:
            try:
                coerced = coerce(res.structured, json_schema, model)
                return _BackendCallResult(result=coerced, succeeded=True, raw_text=res.text)
            except Exception as e:  # validation failure -> retry
                last_err = e
                rt.log_sink(
                    f"[wf] agent {label!r} attempt {attempt}/{attempts} "
                    f"validation failed: {str(e)}"
                )
                continue
        return _BackendCallResult(result=res.text, succeeded=True, raw_text=res.text)
    detail = str(last_err) if last_err else "unknown error"
    rt.log_sink(f"[wf] agent {label!r} failed after {attempts} attempts: {detail}")
    return _BackendCallResult(result=None, succeeded=False, error_detail=detail)


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
        "label": spec.opts.get("label"),
        "phase": spec.opts.get("phase"),
        "kind": kind,
        "result": payload,
        "raw_text": spec.raw_text,
    }


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
        "_label", "_phase", "_instructions", "_options", "_human",
        "_history", "_sid", "_in_flight",
    )

    def __init__(
        self,
        *,
        label: str | None = None,
        phase: str | None = None,
        instructions: str | None = None,
        options: dict | None = None,
        _human: bool = False,
    ) -> None:
        self._label = label
        self._phase = phase
        self._instructions = instructions
        self._options = dict(options or {})
        self._human = _human
        self._history: list[dict] = []
        self._sid: str | None = None
        self._in_flight = False

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
            raise WorkflowError("send(notify=True) is text-only; don't also pass a schema")
        # Phase is run-global orchestration state: the active phase() wins, so a
        # session naturally spans phases; fall back to the session's own default.
        phase_val = rt.current_phase if rt.current_phase is not None else self._phase
        opts = _build_opts(
            rt,
            {"label": self._label, "phase": phase_val, "schema": schema},
            {**self._options, **(options or {})},
        )
        json_schema, model_cls = resolve_schema(opts.get("schema"))

        ks = key_str(_path.get() + (("call", _next_ordinal()),))
        sig = call_signature(prompt, opts, json_schema, history=self._history)

        if self._in_flight:
            _warn_concurrent_session(rt)
        self._in_flight = True
        try:
            _emit_agent_started(rt, opts, prompt)

            cached = rt.journal.get_cached(ks, sig)
            if cached is not None:  # resume hit — no backend, no harness, no person
                await rt.journal.use(ks, cached)
                result = _rehydrate(cached, model_cls)
                self._append_history(prompt, result, model_cls)
                outcome_text = cached.get("raw_text") or _preview(result)
                _emit_agent_completed(rt, opts, outcome_text)
                return None if notify else result

            # A human turn carries a deterministic correlation id (phase:label:turn)
            # so a person's reply matches even across a resume — never a uuid.
            correlation_id = self._correlation_id(opts) if self._human else None
            call_result = await self._drive(
                rt, prompt, opts, json_schema, model_cls, correlation_id
            )
            if not call_result.succeeded:
                attempts = rt.retries + 1
                who = "human" if self._human else "agent"
                label = opts.get("label") or who
                msg = f"{who} session {label!r} failed after {attempts} attempts"
                if call_result.error_detail:
                    msg = f"{msg}: {call_result.error_detail}"
                _emit_agent_failed(rt, opts, msg)
                return None

            result = call_result.result
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
                    )
                ),
            )
            self._append_history(prompt, result, model_cls)
            outcome_text = call_result.raw_text or _preview(result)
            _emit_agent_completed(rt, opts, outcome_text)
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

    async def _drive(self, rt, prompt, opts, json_schema, model_cls, correlation_id):
        """Open the session lazily, then run one turn through the retry helper.

        Agent turns mirror ``agent()`` (spawn-budget gate + LLM permit); human
        turns skip both — waiting on a person must not hold a concurrency permit.
        """
        if self._human:
            await self._ensure_open(rt, opts)
            return await self._turn(rt, prompt, opts, json_schema, model_cls, correlation_id)
        if rt.spawn_count >= rt.spawn_limit:
            detail = f"spawn limit {rt.spawn_limit} reached"
            rt.log_sink(f"[wf] {detail}; skipping {opts.get('label')!r}")
            return _BackendCallResult(result=None, succeeded=False, error_detail=detail)
        if rt.sem is None:  # safety net; normally created in run_workflow
            rt.sem = asyncio.Semaphore(rt.make_cap())
        async with rt.sem:
            await self._ensure_open(rt, opts)
            return await self._turn(rt, prompt, opts, json_schema, model_cls, correlation_id)

    async def _turn(self, rt, prompt, opts, json_schema, model_cls, correlation_id):
        """Run one ``send_turn`` (the prior history is what the backend may replay)."""
        hist = list(self._history)
        sid = self._sid
        return await _attempt_calls(
            rt, opts, json_schema, model_cls,
            lambda: rt.backend.send_turn(
                sid, prompt, opts, json_schema, history=hist, correlation_id=correlation_id
            ),
        )

    def _correlation_id(self, opts: dict) -> str:
        """Deterministic id for a human turn: ``{phase}:{label}:{turn}``.

        The script flow is deterministic, so this is stable across a resume — the
        same interaction point yields the same id, which keeps a person's reply
        valid even if the run was interrupted while waiting. ``turn`` is this
        session's send index (``len(history) // 2``); it advances on every send
        (hit or miss) because history is appended each turn, so replay matches.
        """
        phase = opts.get("phase") or "_"
        label = opts.get("label") or "human"
        turn = len(self._history) // 2
        return f"{phase}:{label}:{turn}"

    async def _ensure_open(self, rt, opts) -> None:
        """Open the backend session on the first real turn (one avatar per session)."""
        if self._sid is not None:
            return
        if not self._human:
            rt.spawn_count += 1  # the avatar is this session's one spawned agent
        self._sid = await rt.backend.open_session(
            kind="human" if self._human else "agent",
            instructions=self._instructions,
            opts=opts,
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
    return AgentSession(label=label, phase=phase, instructions=instructions, options=options)


def human_session(
    *,
    label: str | None = None,
    phase: str | None = None,
    instructions: str | None = None,
    options: dict | None = None,
) -> AgentSession:
    """Open a stateful, multi-turn human participant (each turn's input from a person)."""
    return AgentSession(
        label=label, phase=phase, instructions=instructions, options=options, _human=True
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
    s = AgentSession(_human=True, label=label, phase=phase)
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
        raise WorkflowError(
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
        raise WorkflowError(
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
    rt.current_phase = title
    rt.progress_sink(WorkflowProgressEvent(kind=ProgressKind.PHASE, phase=title))


def log(message: Any) -> None:
    rt = _rt.get()
    rt.progress_sink(
        WorkflowProgressEvent(
            kind=ProgressKind.LOG,
            phase=rt.current_phase,
            message=str(message),
        )
    )


class _Budget:
    """Reads the active run's budget via the contextvar — importable & run-agnostic."""

    @property
    def total(self) -> int | None:
        return _rt.get().budget_total

    @staticmethod
    def spent() -> int:
        return _rt.get().tokens_spent

    @staticmethod
    def remaining() -> int | None:
        rt = _rt.get()
        return None if rt.budget_total is None else rt.budget_total - rt.tokens_spent


#: Importable singleton: `from swarmflow import budget` then `budget.spent()`.
budget = _Budget()


# ─────────────────────── inline sub-workflow ───────────────────────
async def workflow(name_or_path: str, args: Any = None) -> Any:
    """Run a sub-workflow inline and return its ``run()`` result.

    Safe to fan out concurrently via ``parallel`` / ``pipeline``: depth is tracked
    per execution path (a ContextVar copied per Task), so concurrent sibling
    ``workflow()`` calls each get their own depth budget and all run. Only genuine
    recursion — a sub-workflow's ``run()`` calling ``workflow()`` again on the same
    path — is capped at ``_MAX_WORKFLOW_DEPTH`` (returns ``None``, logged).
    """
    rt = _rt.get()
    depth = _wf_depth.get()
    if depth >= _MAX_WORKFLOW_DEPTH:
        rt.log_sink(
            f"[wf] nested workflow depth > {_MAX_WORKFLOW_DEPTH} not allowed; skipping"
        )
        return None
    from .loader import load_workflow_source  # lazy: avoid import cycle

    loaded = load_workflow_source(name_or_path)
    k = _next_ordinal()
    tok_d = _wf_depth.set(depth + 1)
    tok_p = _path.set(_path.get() + (("wf", k, loaded.meta.get("name", str(name_or_path))),))
    tok_s = _seq.set(_fresh_holder())
    try:
        return await _invoke_loaded(loaded, args)
    finally:
        _seq.reset(tok_s)
        _path.reset(tok_p)
        _wf_depth.reset(tok_d)


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
        raise WorkflowError(f"{loaded.path}: must define `async def run(args)`")
    return await (run_fn(args) if _arity(run_fn) >= 1 else run_fn())
