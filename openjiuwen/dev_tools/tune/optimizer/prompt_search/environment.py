# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Environment interface + a shared case runner.

A :class:`PromptEnvironment` executes a candidate prompt and returns a backend-
agnostic :class:`Execution`. The concrete backend only needs to answer "given
this system prompt and this input, what is the output?" — :func:`run_cases`
handles the per-case timing and error isolation around it.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Optional

from openjiuwen.core.foundation.llm import Model, SystemMessage, UserMessage
from openjiuwen.dev_tools.tune.optimizer.prompt_search.models import (
    CaseResult,
    Execution,
    PromptCandidate,
    PromptTaskCase,
    PromptTaskSpec,
)

# A runner turns (system_prompt, case_input) into (output text, tokens used).
CaseRunner = Callable[[str, str], Awaitable[tuple]]


class PromptEnvironment(ABC):
    """Executes a candidate prompt against a task and reports an :class:`Execution`."""

    @abstractmethod
    async def execute(self, candidate: PromptCandidate, task: PromptTaskSpec) -> Execution:
        ...


async def run_cases(
    runner: CaseRunner,
    candidate: PromptCandidate,
    task: PromptTaskSpec,
    *,
    parallel: bool = True,
    max_concurrency: int = 5,
) -> Execution:
    """Run every case of ``task`` through ``runner`` under ``candidate``'s prompt."""

    cases = task.cases or [PromptTaskCase.from_text(task.objective)]
    sem = asyncio.Semaphore(max(1, max_concurrency))
    tokens_used = 0

    async def one(case: PromptTaskCase) -> CaseResult:
        nonlocal tokens_used
        started = time.monotonic()
        try:
            if parallel:
                async with sem:
                    output, tokens = await runner(candidate.prompt, case.input_text)
            else:
                output, tokens = await runner(candidate.prompt, case.input_text)
            tokens_used += max(0, int(tokens or 0))
            return CaseResult(
                case_input=case.input_text,
                output=output or "",
                latency_s=time.monotonic() - started,
                hidden=case.hidden,
            )
        except Exception as exc:  # noqa: BLE001
            return CaseResult(
                case_input=case.input_text,
                output="",
                latency_s=time.monotonic() - started,
                hidden=case.hidden,
                error=str(exc),
            )

    started = time.monotonic()
    if parallel:
        results = list(await asyncio.gather(*(one(c) for c in cases)))
    else:
        results = [await one(c) for c in cases]
    total_latency = time.monotonic() - started

    errors = [r.error for r in results if r.error]
    return Execution(
        candidate=candidate,
        case_results=results,
        latency_s=total_latency,
        token_usage={"total": {"total_tokens": tokens_used}},
        error=errors[0] if errors and len(errors) == len(results) else None,
    )


class LLMEnvironment(PromptEnvironment):
    """Execute a candidate prompt directly against an LLM.

    The candidate becomes the system prompt; each case input becomes the user
    message. This is the default backend and requires no external workflow.
    """

    def __init__(
        self,
        model: Model,
        *,
        parallel: bool = True,
        max_concurrency: int = 5,
    ) -> None:
        self._model = model
        self._parallel = parallel
        self._max_concurrency = max_concurrency

    async def execute(self, candidate: PromptCandidate, task: PromptTaskSpec) -> Execution:
        async def runner(system_prompt: str, case_input: str) -> tuple:
            response = await self._model.invoke(
                messages=[SystemMessage(content=system_prompt), UserMessage(content=case_input)],
            )
            tokens = response.usage_metadata.total_tokens if response.usage_metadata else 0
            return response.content or "", tokens

        return await run_cases(
            runner,
            candidate,
            task,
            parallel=self._parallel,
            max_concurrency=self._max_concurrency,
        )


# (system_prompt, case_input) -> output text (sync or async)
RunFn = Callable[[str, str], object]


class CallableEnvironment(PromptEnvironment):
    """Wrap any ``(system_prompt, case_input) -> str`` callable (sync or async).

    Adapts *any* execution backend — a deterministic evaluator, a plugin, an
    agent coroutine, or a full workflow — behind the same
    :class:`PromptEnvironment` contract, so the optimizer stays decoupled from
    how a candidate prompt is actually run.
    """

    def __init__(
        self,
        fn: RunFn,
        *,
        parallel: bool = True,
        max_concurrency: int = 5,
    ) -> None:
        self._fn = fn
        self._parallel = parallel
        self._max_concurrency = max_concurrency

    async def execute(self, candidate: PromptCandidate, task: PromptTaskSpec) -> Execution:
        async def runner(system_prompt: str, case_input: str) -> tuple:
            result = self._fn(system_prompt, case_input)
            if isinstance(result, Awaitable):
                result = await result
            return ("" if result is None else str(result)), 0

        return await run_cases(
            runner,
            candidate,
            task,
            parallel=self._parallel,
            max_concurrency=self._max_concurrency,
        )


# An agent runner is just a callable that happens to drive an agent; alias for clarity.
AgentEnvironment = CallableEnvironment
# A workflow runner is the same shape too — e.g. a wrapper around a team run or plan execution.
WorkflowEnvironment = CallableEnvironment


__all__ = [
    "PromptEnvironment",
    "CaseRunner",
    "run_cases",
    "LLMEnvironment",
    "CallableEnvironment",
    "AgentEnvironment",
    "WorkflowEnvironment",
]
