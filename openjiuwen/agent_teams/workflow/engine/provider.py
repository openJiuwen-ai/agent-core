# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The engine's :class:`~workflow.engine.seam.Provider` implementation.

This adapts the reference engine (``primitives``) to the public seam. The
public facade (``facade.agent`` etc.) forwards to the *current provider*;
:func:`workflow.engine.runner.run_workflow` installs :data:`ENGINE_PROVIDER`
for the duration of a run, so those facade calls land here.

The provider is **stateless** — all per-run state lives in the ``_rt``/``_path``/
``_seq`` contextvars inside :mod:`workflow.engine.primitives` — so a single
module-level instance safely serves every concurrent run (each run has its own
contextvars).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Sequence

from . import primitives as _p


class EngineProvider:
    """Forwards the public primitives to the reference engine implementation."""

    async def agent(
        self,
        prompt: str,
        *,
        label: str | None = None,
        phase: str | None = None,
        schema: Any = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        return await _p.agent(
            prompt, label=label, phase=phase, schema=schema, model=model, timeout=timeout
        )

    async def parallel(self, thunks: Sequence[Callable[[], Awaitable]]) -> list:
        return await _p.parallel(thunks)

    async def pipeline(self, items: Sequence, *stages: Callable) -> list:
        return await _p.pipeline(items, *stages)

    async def map_parallel(self, items: Sequence, fn: Callable) -> list:
        return await _p.map_parallel(items, fn)

    def phase(self, title: str) -> None:
        _p.phase(title)

    def log(self, message: Any) -> None:
        _p.log(message)

    async def workflow(self, name_or_path: str, args: Any = None) -> Any:
        return await _p.workflow(name_or_path, args)

    @property
    def budget(self) -> Any:
        return _p.budget


#: Stateless singleton installed by `run_workflow` for the lifetime of a run.
ENGINE_PROVIDER = EngineProvider()
