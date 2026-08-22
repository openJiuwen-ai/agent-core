# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The SwarmFlow facade — the stable surface workflow scripts bind to.

This module lives **inside the engine package** and is the canonical home of
the public primitives. There is no physical ``swarmflow`` package: the name
``swarmflow`` is mapped onto this module in ``sys.modules`` once, at import
time (see :func:`_register_aliases`). The mapping is fixed for the process and
always points here, so there is no per-run install/teardown — a lazy
``from swarmflow import ...`` inside ``run`` resolves just like a top-level
import.

A workflow file imports the primitives by whatever name is mapped::

    from swarmflow import agent, parallel, pipeline, map_parallel, phase, log

These are a *contract*, not an implementation: each primitive forwards to the
**current provider** (see :mod:`workflow.engine.seam`). The reference engine
installs its :class:`~workflow.engine.provider.EngineProvider` for the duration
of a run, so the entire primitive implementation can be swapped (real vs.
simulated, local vs. distributed) without touching script code.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, Sequence, TypeVar, overload

from .seam import (
    BudgetView,
    Provider,
    current_provider,
    reset_provider,
    use_provider,
)

# Harness re-exports (engine internals exposed under the public name). These are
# eager because importing this module already imports the engine package.
from .backends import AgentBackend, AgentResult, MockBackend
from .errors import EngineError, LintError, MetaError, SchemaError
from .journal import Journal
from .loader import LoadedWorkflow, load_workflow_source
from .primitives import AgentSession, HumanSession
from .runner import run_workflow
from .runtime import Runtime

if TYPE_CHECKING:
    from pydantic import BaseModel

# For typing overloads: `schema=MyModel` narrows the return to `MyModel | None`.
M = TypeVar("M", bound="BaseModel")


# ─────────────────────────── agent ───────────────────────────
# Return type follows the schema kind:
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
    """Spawn a sub-agent. Delegates to the current provider's ``agent``.

    Orchestration/identity params (``label`` / ``phase`` / ``schema``) are
    explicit; tuning and forward-compat params ride in the validated ``options``
    bag — e.g. ``options={"model": "...", "timeout": 30, "isolation": "worktree",
    "agent_type": "..."}`` — so a new knob needs no signature change. Keys are
    whitelisted against the engine + backend option sets; an unknown key raises.
    """
    return await current_provider().agent(
        prompt,
        label=label,
        phase=phase,
        schema=schema,
        options=options,
    )


async def parallel(thunks: Sequence[Callable[[], Awaitable]]) -> list:
    """Fork-join barrier over lazy thunks. Delegates to the current provider."""
    return await current_provider().parallel(thunks)


async def pipeline(items: Sequence, *stages: Callable) -> list:
    """No-barrier streaming pipeline. Delegates to the current provider."""
    return await current_provider().pipeline(items, *stages)


async def map_parallel(items: Sequence, fn: Callable) -> list:
    """Footgun-free fan-out. Delegates to the current provider."""
    return await current_provider().map_parallel(items, fn)


pmap = map_parallel  # alias


def phase(title: str) -> None:
    """Mark the current phase (observability). Delegates to the current provider."""
    current_provider().phase(title)


def log(message: Any) -> None:
    """Emit a progress line. Delegates to the current provider."""
    current_provider().log(message)


async def workflow(name_or_path: str, args: Any = None) -> Any:
    """Run another workflow inline (one level). Delegates to the current provider."""
    return await current_provider().workflow(name_or_path, args)


def agent_session(
    *,
    label: str | None = None,
    phase: str | None = None,
    instructions: str | None = None,
    options: dict | None = None,
) -> AgentSession:
    """Open a stateful, multi-turn agent. Delegates to the current provider."""
    return current_provider().agent_session(
        label=label, phase=phase, instructions=instructions, options=options
    )


def human_session(
    *,
    label: str | None = None,
    phase: str | None = None,
    instructions: str | None = None,
    options: dict | None = None,
) -> AgentSession:
    """Open a stateful, multi-turn human participant. Delegates to the current provider."""
    return current_provider().human_session(
        label=label, phase=phase, instructions=instructions, options=options
    )


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


async def human(
    prompt: str,
    *,
    schema: Any = None,
    label: str | None = None,
    phase: str | None = None,
    options: dict | None = None,
) -> Any:
    """One-shot human turn. Delegates to the current provider."""
    return await current_provider().human(
        prompt, schema=schema, label=label, phase=phase, options=options
    )


class _BudgetProxy:
    """Public ``budget``: proxies to ``current_provider().budget`` per access."""

    @property
    def total(self) -> int | None:
        return current_provider().budget.total

    @staticmethod
    def spent() -> int:
        return current_provider().budget.spent()

    @staticmethod
    def remaining() -> int | None:
        return current_provider().budget.remaining()


#: Importable singleton: a script does `from swarmflow import budget` (name mapped
#: at runtime), then `budget.spent()`.
budget = _BudgetProxy()


# ─────────────────── pure list helpers (no provider seam) ───────────────────
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


__all__ = [
    # script-facing primitives (the contract)
    "agent",
    "agent_session",
    "human_session",
    "human",
    "AgentSession",
    "HumanSession",
    "parallel",
    "pipeline",
    "map_parallel",
    "pmap",
    "phase",
    "log",
    "workflow",
    "budget",
    "compact",
    "flatten_filter",
    # provider seam (custom engines / tests)
    "Provider",
    "BudgetView",
    "use_provider",
    "reset_provider",
    "current_provider",
    # harness
    "run_workflow",
    "Runtime",
    "Journal",
    "load_workflow_source",
    "LoadedWorkflow",
    "AgentBackend",
    "AgentResult",
    "MockBackend",
    "EngineError",
    "MetaError",
    "LintError",
    "SchemaError",
]


def _register_aliases() -> None:
    """Map the ``swarmflow`` import name onto this module, once per process.

    A workflow file's ``from swarmflow import agent`` resolves because this
    module is registered in ``sys.modules`` under that name — there is no
    on-disk ``swarmflow`` package. The mapping is fixed and always points to
    this one module, so registration happens at import time rather than per run;
    that is what lets a lazy ``from swarmflow import ...`` inside ``run``'s body
    resolve too.
    """
    import sys

    sys.modules.setdefault("swarmflow", sys.modules[__name__])


_register_aliases()
