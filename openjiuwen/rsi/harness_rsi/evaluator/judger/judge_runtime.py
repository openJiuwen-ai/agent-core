# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded read-only DeepAgent runtime for independent evaluation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from openjiuwen.core.foundation.llm import Model, SystemMessage, UserMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness import create_deep_agent
from openjiuwen.harness.rails._multimodal import build_read_image_multimodal_resolver
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.tools.filesystem import GlobTool, GrepTool, ListDirTool, ReadFileTool
from openjiuwen.rsi.harness_rsi.artifact_io import _io_path
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.judger.direct_evidence import MAX_CLOSEOUT_BYTES, inline_evidence
from openjiuwen.rsi.harness_rsi.member_optimizer.model_config import load_model_config_ref, without_inner_sdk_retries


class JudgeIterationLimitError(EvaluationInfrastructureError):
    """Reading exhausted its budget without returning a verdict."""


class JudgeReadOnlyRail(SysOperationRail):
    """Do not expose shell, write, skill execution or subagent tools to the judge."""

    def init(self, agent: Any) -> None:
        language = agent.system_prompt_builder.language
        agent_id = agent.card.id
        self.tools = [
            ReadFileTool(
                self.sys_operation,
                language,
                agent_id,
                enable_image_multimodal=build_read_image_multimodal_resolver(agent),
            ),
            ListDirTool(self.sys_operation, language, agent_id),
            GlobTool(self.sys_operation, language, agent_id),
            GrepTool(self.sys_operation, language, agent_id),
        ]
        for tool in self.tools:
            agent.ability_manager.add_ability(tool.card, tool)


class JudgeBudgetRail(AgentRail):
    """Bound evidence reading and record tool use without disabling read tools."""

    def __init__(self, iterations: int, log_path: Path) -> None:
        self.iterations = iterations
        self.log_path = log_path
        self.continuation: Callable[[], Awaitable[str]] | None = None
        self._closed = False

    async def closeout(self, _raw: str) -> str:
        """Finalize once from complete frozen evidence, never partial read history."""
        if self._closed or self.continuation is None:
            raise EvaluationInfrastructureError("Judge closeout context is unavailable or already consumed")
        self._closed = True
        return await self.continuation()

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        turn = int(ctx.extra.get("judge_turn", 0)) + 1
        ctx.extra["judge_turn"] = turn
        if turn >= self.iterations:
            await ctx.context.add_messages(
                UserMessage(
                    content=(
                        "Final evaluation turn. Finish with the grading JSON when evidence is sufficient. "
                        "If evidence is still needed, use native read tools, not tool-call markup. "
                        "Missing/deleted deliverables or a summary-only answer are task failures: "
                        "return status=completed, scoring unmet requirements 0. "
                        "Assess criteria independently: missing code does not erase supported "
                        "proof/analysis credit. Explain each item's supported and missing parts. "
                        "Unread or truncated evidence is not proof of absence. "
                        "Use status=unavailable only for genuine evaluator limitations such as "
                        "unreadable supplied evidence or unavailable verification tools, not missing work."
                    )
                )
            )

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        result = ctx.inputs.tool_result
        success = result.get("success") if isinstance(result, dict) else getattr(result, "success", None)
        data = result.get("data") if isinstance(result, dict) else getattr(result, "data", None)
        error = result.get("error") if isinstance(result, dict) else getattr(result, "error", None)
        content = data.get("content", "") if isinstance(data, dict) else ""
        with _io_path(self.log_path).open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {"tool": ctx.inputs.tool_name, "arguments": ctx.inputs.tool_args, "success": success,
                     "error": error, "returned_chars": len(content) if isinstance(content, str) else None},
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )


def _judge_model(config: EvaluatorConfig) -> Model:
    from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig

    ref = config.judge_model_config_ref or config.model_config_ref
    loaded = load_model_config_ref(ref)
    data = without_inner_sdk_retries(loaded.get("model", loaded))
    request = dict(data.get("model_request_config") or {})
    request.update(temperature=0.0)
    data["model_request_config"] = request
    return TeamModelConfig.model_validate(data).build()


def build_judge_agent(
    config: EvaluatorConfig, workspace: Path, log_path: Path, *, budget: JudgeBudgetRail | None = None,
) -> Any:
    budget = budget or JudgeBudgetRail(config.judge_agent_max_iterations, log_path)
    model = _judge_model(config)

    async def complete_evidence_verdict() -> str:
        payload = await asyncio.to_thread(
            inline_evidence, workspace, max_bytes=MAX_CLOSEOUT_BYTES, required=True,
        )
        if payload is None:
            raise EvaluationInfrastructureError("Judge closeout evidence is unavailable; no score produced")
        return await _invoke_complete_evidence(model, payload)

    budget.continuation = complete_evidence_verdict
    return create_deep_agent(
        model=model,
        card=AgentCard(name="evaluator_agent", description="Independent reference-based evaluator"),
        system_prompt=Path(__file__).with_name("judge_prompt.md").read_text(encoding="utf-8"),
        workspace=str(workspace),
        restrict_to_work_dir=True,
        auto_create_workspace=False,
        enable_task_loop=False,
        enable_task_planning=False,
        enable_skill_discovery=False,
        max_iterations=budget.iterations,
        language="en",
        rails=[JudgeReadOnlyRail(), budget],
    )


async def run_judge_agent(
    config: EvaluatorConfig, workspace: Path, prompt: str, log_path: Path, *, budget: JudgeBudgetRail | None = None,
) -> str:
    from openjiuwen.core.runner import Runner

    payload = await asyncio.to_thread(inline_evidence, workspace) if budget is not None else None
    if payload is not None:
        model = _judge_model(config)

        async def invoke_direct() -> str:
            return await _invoke_complete_evidence(model, payload)

        budget.continuation = invoke_direct
        return await invoke_direct()
    agent = build_judge_agent(config, workspace, log_path, budget=budget)
    try:
        result = await Runner.run_agent(agent=agent, inputs={"query": prompt}, session=f"judge_{agent.card.id}")
        if isinstance(result, dict):
            if result.get("result_type") == "error":
                if budget is not None and result.get("output") == "Max iterations reached without completion":
                    raise JudgeIterationLimitError("Judge reading iteration limit reached")
                raise RuntimeError(str(result.get("output") or "evaluator agent failed"))
            result = result.get("output", result.get("answer", result))
        return json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
    finally:
        for rail in agent.configured_rails():
            if isinstance(rail, JudgeReadOnlyRail):
                rail.uninit(agent)
        await agent.cleanup_task_resources()
        Runner.resource_mgr.remove_sys_operation(f"{agent.card.name}_{agent.card.id}")


async def _invoke_complete_evidence(model: Model, payload: str) -> str:
    policy = await asyncio.to_thread(Path(__file__).with_name("judge_prompt.md").read_text, encoding="utf-8")
    policy = "All grading evidence is supplied inline. Evaluate it directly.\nEvaluation policy:" + policy.split(
        "Evaluation policy:", 1,
    )[1]
    response = await model.invoke(messages=[SystemMessage(content=policy), UserMessage(content=payload)], tools=None)
    return response.content or ""
