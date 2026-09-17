# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Adapter binding the Metis reflectors to the core ``Model`` client.

The algorithm modules (``text_reflect`` / ``code_reflect`` / ``manager_select``)
call ``await llm.async_generate(prompt=...) -> str``; this adapter satisfies
that contract on top of ``Model.invoke`` with evolution-layer retry.
"""

from __future__ import annotations

from openjiuwen.agent_evolving.optimizer.llm_resilience import LLMInvokePolicy, invoke_text_with_retry
from openjiuwen.core.foundation.llm.model import Model

METIS_LLM_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=120.0,
    total_budget_secs=300.0,
    max_attempts=2,
)


class MetisReflectorLLM:
    """Text-in/text-out LLM handle for the Metis reflectors and manager."""

    def __init__(self, llm: Model, model: str, policy: LLMInvokePolicy = METIS_LLM_POLICY) -> None:
        """Bind a model client, model name, and retry policy."""
        self._llm = llm
        self._model = model
        self._policy = policy

    async def async_generate(self, prompt: str) -> str:
        """Generate text for one Metis Manager or reflector prompt."""
        return await invoke_text_with_retry(self._llm, self._model, prompt, policy=self._policy)


__all__ = ["METIS_LLM_POLICY", "MetisReflectorLLM"]
