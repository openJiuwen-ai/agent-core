# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The provider seam: the indirection that lets the engine be swapped.

A *provider* implements the DSL primitives. The public facade (the
``agent``/``parallel``/... a workflow script imports) does **not** call the
engine directly — it resolves the **current provider** from a ``contextvar``
and forwards to it. The engine installs its
:class:`~workflow.engine.provider.EngineProvider` for the duration of a run; a
different implementation (a distributed engine, an in-process simulator, a
record/replay shim) can install its own provider instead, and **workflow
scripts never change** — they import the same names from the facade.

Because the provider lives in a ``contextvar`` and ``asyncio`` Tasks copy the
context, concurrent runs in one event loop can even use *different* providers
without interfering.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:
    from pydantic import BaseModel


@runtime_checkable
class Provider(Protocol):
    """What an engine must implement to back the public primitives.

    Mirrors the public surface one-to-one. ``compact``/``flatten_filter`` are
    pure list helpers with no implementation freedom, so they are *not* part of
    the seam — they ship as plain functions in the facade.
    """

    async def agent(
        self,
        prompt: str,
        *,
        label: str | None = ...,
        phase: str | None = ...,
        schema: Any = ...,
        model: str | None = ...,
        timeout: float | None = ...,
    ) -> Any:
        """Spawn a sub-agent and return its result (typed per ``schema``)."""
        ...

    async def parallel(self, thunks: Sequence[Callable[[], Awaitable]]) -> list:
        """Fork-join barrier over lazy thunks."""
        ...

    async def pipeline(self, items: Sequence, *stages: Callable) -> list:
        """No-barrier streaming pipeline running ``items`` through ``stages``."""
        ...

    async def map_parallel(self, items: Sequence, fn: Callable) -> list:
        """Footgun-free fan-out of ``fn`` over ``items``."""
        ...

    def phase(self, title: str) -> None:
        """Mark the current phase for observability."""
        ...

    def log(self, message: Any) -> None:
        """Emit a progress line."""
        ...

    async def workflow(self, name_or_path: str, args: Any = ...) -> Any:
        """Run another workflow inline (one level deep)."""
        ...

    @property
    def budget(self) -> "BudgetView":
        """The active run's budget view."""
        ...


@runtime_checkable
class BudgetView(Protocol):
    """The object returned by ``Provider.budget`` (what the facade ``budget`` proxies)."""

    @property
    def total(self) -> int | None:
        """The run's total token budget, or ``None`` when unbounded."""
        ...

    def spent(self) -> int:
        """Output tokens spent so far across the run."""
        ...

    def remaining(self) -> int | None:
        """Tokens left before the budget ceiling, or ``None`` when unbounded."""
        ...


# The active provider for the current task/context. None until an engine installs one.
_provider: ContextVar["Provider | None"] = ContextVar("swarmflow_provider", default=None)


def current_provider() -> "Provider":
    """The provider backing this context, or raise a clear error if none is installed."""
    p = _provider.get()
    if p is None:
        raise RuntimeError(
            "No SwarmFlow provider is installed. Run workflows via "
            "`run_workflow(...)` (which installs the engine provider), "
            "or install a custom one with `use_provider(provider)`."
        )
    return p


def use_provider(provider: "Provider") -> Token:
    """Install *provider* as the current one; returns a token for :func:`reset_provider`.

    The engine calls this for the lifetime of a run. Tests / alternative engines
    can call it to swap the whole primitive implementation behind the facade.
    """
    return _provider.set(provider)


def reset_provider(token: Token) -> None:
    """Restore the provider that was active before the matching :func:`use_provider`."""
    _provider.reset(token)
