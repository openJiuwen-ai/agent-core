# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded DeepAgent runtime used by evaluation diagnosis.

This module owns model construction, read-only tool policy, and transient-call
retries. Evidence preparation and issue compilation deliberately remain outside
the runtime so the diagnosis agent cannot become the workflow controller.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
from openjiuwen.rsi.harness_rsi.evaluator.runtime_adapters import RSISysOperationRail
from openjiuwen.rsi.harness_rsi.member_optimizer.model_config import (
    load_model_config_ref,
    without_inner_sdk_retries,
)
from openjiuwen.rsi.harness_rsi.model_call import run_model_call_with_retries

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.base import BaseAgent


_DIAGNOSIS_BASH_DENY_PATTERNS = [
    r"(?:^|[;&|]\s*)(?:find|du|ls|tree)\s+/(?:\s|$)",
    r"(?:^|\s)\.\.(?:[/\\]|\s|$)",
]


class DiagnosisAgentExecutionError(RuntimeError):
    """The agent returned an execution failure rather than a diagnosis answer."""


class _DiagnosisBudgetRail(AgentRail):
    """Reserve the existing final turn for an answer, not another investigation."""

    def __init__(self, max_iterations: int) -> None:
        self._max_iterations = max_iterations

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        turn = int(ctx.extra.get("_diagnosis_model_turn", 0)) + 1
        ctx.extra["_diagnosis_model_turn"] = turn
        note = (
            f"Investigation turn {turn}/{self._max_iterations}. "
            "Return the diagnosis as soon as the local cause is supported."
        )
        if turn >= self._max_iterations:
            ctx.inputs.tools = None
            note = (
                "This is the final turn of the existing investigation budget. "
                "Return the per-case diagnosis JSON now using only the task and "
                "observations already present. No more tools are available. "
                "Preserve uncertainty explicitly instead of inventing evidence."
            )
        # The model window is rebuilt after rails; modifying its preview loses the note.
        await ctx.context.add_messages(UserMessage(content=note))


class DiagnosisAgentRuntime:
    """Create a read-only DeepAgent without owning diagnosis policy."""

    def __init__(self, config: EvaluationResultAnalyzerConfig) -> None:
        self._config = config

    async def build_agent(self, workspace: str, *, system_prompt: str) -> "BaseAgent":
        """Build a bounded DeepAgent for one isolated evidence workspace."""
        ref_path = self._config.diagnosis_agent_model_config_ref or self._config.model_config_ref
        if not ref_path:
            raise ValueError("model_config_ref must be set")

        ref_data = load_model_config_ref(ref_path)
        model_data = ref_data.get("model", ref_data)
        model_config = TeamModelConfig.model_validate(without_inner_sdk_retries(model_data))
        return create_deep_agent(
            model=model_config.build(),
            card=AgentCard(
                name="diagnosis_agent",
                description="Evaluation result diagnosis agent",
            ),
            system_prompt=system_prompt,
            workspace=workspace,
            restrict_to_work_dir=True,
            max_iterations=self._config.diagnosis_agent_max_iterations,
            auto_create_workspace=False,
            rails=[
                RSISysOperationRail(
                    read_only=True,
                    bash_pipefail=True,
                    bash_deny_patterns=_DIAGNOSIS_BASH_DENY_PATTERNS,
                ),
                _DiagnosisBudgetRail(self._config.diagnosis_agent_max_iterations),
            ],
        )


async def run_deep_agent_text(
    agent: "BaseAgent",
    prompt: str,
    *,
    max_retries: int,
    operation_name: str,
) -> str:
    """Run one DeepAgent request with an independent session per retry.

    Reusing the failed session can replay corrupted or incomplete agent state.
    The model retry policy therefore retries the same evidence and prompt in a
    fresh session while keeping the DeepAgent configuration fixed.
    """
    from openjiuwen.core.runner.runner import Runner

    async def call_once() -> str:
        result = await Runner.run_agent(
            agent=agent,
            inputs={"query": prompt},
            session=f"diagnosis_{uuid.uuid4().hex}",
        )
        if isinstance(result, dict):
            fallback = json.dumps(result, ensure_ascii=False)
            output = str(result.get("output", result.get("answer", fallback)))
            if result.get("result_type") == "error":
                raise DiagnosisAgentExecutionError(output)
            return output
        return str(result)

    return await run_model_call_with_retries(
        call_once,
        operation_name=operation_name,
        max_retries=max(0, int(max_retries or 0)),
    )


__all__ = ["DiagnosisAgentExecutionError", "DiagnosisAgentRuntime", "run_deep_agent_text"]
