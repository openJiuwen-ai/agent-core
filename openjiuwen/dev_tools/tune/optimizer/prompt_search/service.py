# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Standalone entrypoint for the prompt-search optimizer.

Use this when you just want a prompt optimized for a task — no ``LLMCall``,
Agent, or ``Trainer`` required::

    from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
    from openjiuwen.dev_tools.tune.optimizer.prompt_search import (
        PromptTaskCase, PromptTaskSpec, optimize_prompt,
    )

    task = PromptTaskSpec(
        objective="Summarize a support ticket into at most 3 action items.",
        cases=[PromptTaskCase.from_text("My invoice is wrong.", "- Fix invoice")],
    )
    result = await optimize_prompt(task, model_config, model_client_config)
    print(result.best_prompt, result.best_score)
"""

from __future__ import annotations

from typing import Optional

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.dev_tools.tune.optimizer.prompt_search.models import OptimizationResult, PromptTaskSpec
from openjiuwen.dev_tools.tune.optimizer.prompt_search.optimizer import PromptSearchOptimizer


async def optimize_prompt(
    task: PromptTaskSpec,
    model_config: ModelRequestConfig,
    model_client_config: ModelClientConfig,
    *,
    optimizer: Optional[PromptSearchOptimizer] = None,
    **kwargs,
) -> OptimizationResult:
    """Optimize a system prompt for ``task``.

    Pass ``optimizer`` to reuse an already-configured
    :class:`PromptSearchOptimizer` (e.g. to keep its memory backend warm
    across calls); otherwise one is built from ``model_config`` /
    ``model_client_config`` plus any ``PromptSearchOptimizer`` keyword
    (``candidate_prompts``, ``max_iterations``, ``reward_weights``,
    ``policy``, ``environment``, ``reward_model``, ``drift_judge``,
    ``memory``, ...).
    """

    runner = optimizer or PromptSearchOptimizer(model_config, model_client_config, **kwargs)
    return await runner.optimize(task)


__all__ = ["optimize_prompt"]
