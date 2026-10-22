# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reward deep agent: default tools + workspace permission + submit tool.

The agent validates its submission with ``submit_reward``; a valid payload is
recorded into the host-provided sink and force-finishes the run.  The build
surface stays behind :class:`RewardBuilder` so the implementation can later
change without touching the orchestration layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from openjiuwen.agent_evolving.attribution.agent import ContextBudgetRail, LlmStatusRail
from openjiuwen.agent_evolving.attribution.model import StatusSink
from openjiuwen.agent_evolving.attribution.reward_stage.reward_schema import (
    SCHEMA_VERSION,
    validate_reward_payload,
)
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from openjiuwen.core.foundation.llm.model import Model
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard
    from openjiuwen.harness.deep_agent import DeepAgent

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

_AGENT_CARD_ID = "attribution-reward-agent"

_prompt_cache: dict[str, str] = {}


def load_reward_prompt(language: str) -> str:
    lang = "zh" if str(language).strip().lower() in {"zh", "cn"} else "en"
    if lang not in _prompt_cache:
        _prompt_cache[lang] = (_PROMPT_DIR / f"reward_{lang}.md").read_text(encoding="utf-8")
    return _prompt_cache[lang]


class RewardSink(Protocol):
    """Host-provided persistence for a validated reward submission."""

    def submit(self, payload: dict[str, Any]) -> str:
        """Record the payload; returns ``recorded`` or ``updated``."""


class _SubmitGate:
    """Shared state between the submit tool and the finish rail."""

    def __init__(self) -> None:
        self.submitted = False

    def mark(self) -> None:
        self.submitted = True


class RewardSubmitRail(AgentRail):
    """Force-finish the run once a valid reward submission is recorded."""

    priority = 900

    def __init__(self, gate: _SubmitGate) -> None:
        super().__init__()
        self._gate = gate

    async def after_tool_call(self, ctx: Any) -> None:
        if self._gate.submitted:
            ctx.request_force_finish(
                {
                    "reason": "reward submitted",
                    "status": "submitted",
                }
            )


def _payload_prop() -> dict[str, Any]:
    return {
        "type": "object",
        "description": (
            f"归因 Reward 结果对象（schema {SCHEMA_VERSION}）：{{overall, categories?, dimensions}}；"
            "overall={{comment: str, total_score: 0–100 数字}}；categories 每项 "
            "{name: str, comment?: str, score?: 数字}；dimensions 每项 {category: str, requirement: str, "
            "comment: str, score: 0–100 数字, related_signal_ids?: list}。/"
            f" Reward result object (schema {SCHEMA_VERSION}): {{overall, categories?, dimensions}}; "
            "overall={comment: str, total_score: number 0-100}; each category is {name: str, comment?: str, "
            "score?: number}; each dimension is {category: str, requirement: str, comment: str, "
            "score: number 0-100, related_signal_ids?: list}."
        ),
        "properties": {
            "schema_version": {"type": "string"},
            "overall": {"type": "object"},
            "categories": {"type": "array", "items": {"type": "object"}},
            "dimensions": {
                "type": "array",
                "items": {"type": "object"},
            },
        },
        "required": ["schema_version", "overall", "dimensions"],
    }


class SubmitRewardTool(Tool):
    """Validate and record one reward submission; valid payloads finish the run."""

    tool_name = "submit_reward"
    tool_id = "SubmitRewardTool"
    description = (
        "提交任务执行 Reward 最终结果（schema attribution.reward.v1）。overall 与 dimensions 必填，"
        "categories 可选；校验失败会返回错误原因，请修正后整条重新提交（以最后一次提交为准）。/ "
        "Submit the final task-execution reward result (schema attribution.reward.v1). overall and "
        "dimensions are required; categories is optional; on validation failure an error reason is "
        "returned — fix it and resubmit the whole payload (the last submission wins)."
    )
    input_schema = {"type": "object", "properties": {"payload": _payload_prop()}, "required": ["payload"]}

    def __init__(self, sink: RewardSink | None, gate: _SubmitGate) -> None:
        self._sink = sink
        self._gate = gate
        super().__init__(
            ToolCard(
                id="attribution-reward.submit_reward",
                name=self.tool_name,
                description=self.description,
                input_params=self.input_schema,
                stateless=False,
                idempotent=True,
            )
        )

    async def stream(self, inputs: dict[str, Any], **kwargs: Any) -> Any:  # type: ignore[override]
        del inputs, kwargs
        if False:  # pragma: no cover
            yield ToolOutput(success=False)

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:  # type: ignore[override]
        del kwargs
        payload = inputs.get("payload")
        errors = validate_reward_payload(payload)
        if errors:
            detail = "; ".join(errors)
            return ToolOutput(
                success=True,
                data={
                    "content": (
                        f"格式校验失败：{detail}。请修正后调用 submit_reward 整条重新提交（以最后一次提交为准）。/ "
                        f"Format validation failed: {detail}. Fix the issues and call submit_reward again "
                        "with the whole payload (the last submission wins)."
                    )
                },
            )
        status = "recorded"
        if self._sink is not None:
            status = self._sink.submit(dict(payload))
        self._gate.mark()
        return ToolOutput(success=True, data={"content": f'{{"status": "{status}"}}'})


def build_reward_agent(
    *,
    model: "Model",
    card: "AgentCard",
    system_prompt: str,
    workspace: str,
    language: str = "zh",
    context_budget_tokens: int = 600_000,
    max_iterations: int = 32,
    reward_sink: RewardSink | None = None,
    status_sink: StatusSink | None = None,
    model_name: str = "",
    status_counter: list[int] | None = None,
) -> tuple["DeepAgent", ContextBudgetRail, _SubmitGate]:
    """Build the Reward deep agent with default tools, workspace and submit tool.

    Returns ``(agent, rail, gate)``; the host reads ``gate.submitted`` after
    ``invoke`` to know whether a valid submission was recorded.  When
    ``status_sink`` is given, request-level LLM events (stage ``reward``) are
    reported through a :class:`LlmStatusRail`.
    """
    from openjiuwen.harness.factory import create_deep_agent
    from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail

    rail = ContextBudgetRail(context_budget_tokens, reason_prefix="reward")
    gate = _SubmitGate()
    submit_rail = RewardSubmitRail(gate)
    rails: list[AgentRail] = [SysOperationRail(with_code_tool=True), submit_rail, rail]
    if status_sink is not None:
        rails.append(
            LlmStatusRail(
                status_sink,
                stage="reward",
                agent_id=_AGENT_CARD_ID,
                model=model_name,
                counter=status_counter if status_counter is not None else [0],
            )
        )
    agent = create_deep_agent(
        model,
        card=card,
        system_prompt=system_prompt,
        tools=[SubmitRewardTool(reward_sink, gate)],
        rails=rails,
        workspace=workspace,
        restrict_to_work_dir=True,
        max_iterations=max_iterations,
        enable_security_rail=False,
        enable_model_anomaly_detection_rail=False,
    )
    return agent, rail, gate


class RewardBuilder(Protocol):
    """Abstraction over the Reward stage so the implementation can change."""

    async def build(
        self,
        *,
        task_description: str,
        message_artifacts: list[tuple[str, str]],
        file_artifacts: list[str],
        user_attribution: str,
        signals: list[Any],
    ) -> Any:
        """Build the reward; returns a result the caller converts to signals."""


__all__ = [
    "RewardBuilder",
    "RewardSink",
    "RewardSubmitRail",
    "SubmitRewardTool",
    "build_reward_agent",
    "load_reward_prompt",
]
