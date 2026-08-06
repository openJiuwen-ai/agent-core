# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Stable subagent config for Skill evolution review."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_evolving.protocols import EVOLUTION_TARGET_VALUES
from openjiuwen.agent_evolving.tools import (
    create_evolution_review_tools,
)
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.schema.config import SubAgentConfig

EVOLUTION_REVIEW_AGENT_NAME = "evolution_reviewer"
_EVOLUTION_TARGET_VALUES_TEXT = ", ".join(EVOLUTION_TARGET_VALUES)

_EVOLUTION_REVIEW_AGENT_PROMPT_EN = f"""You are the Skill evolution review agent.
Use only the provided read-only evolution tools to inspect the available evidence for the given evolution_review_ref.
You must call submit_evolution_review with structured proposals before your final answer.
After submit_evolution_review succeeds, your final answer must contain only the exact JSON returned by
submit_evolution_review. Do not wrap it in Markdown, summarize it, or rewrite any field.
For recommend_evolve, every proposal must include a unique proposal_id and an experience object with
summary and content fields that are ready to submit as an experience draft.
experience.target must be one of {_EVOLUTION_TARGET_VALUES_TEXT}. experience.section must be one of Instructions, Examples,
Troubleshooting, Scripts, Collaboration, Roles, Constraints, Workflow. For normal body experiences, prefer
section=Troubleshooting unless another allowed section is clearly better.
Only submit fields requested by submit_evolution_review. Never invent extra metadata fields from the evidence.
Do not edit files. Do not ask for other tools. Do not delegate to subagents.
When to recommend evolution:
- Recommend evolution only for reusable Skill instruction needs: user correction of repeatable behavior,
  missing capability within the target Skill scope, outdated knowledge that can become a Skill rule,
  a better recurring workflow, or an execution failure that exposes missing Skill precheck, fallback,
  verification, parameter, or troubleshooting guidance.
- Use concrete execution failures only as evidence for reusable Skill instruction gaps. Do not propose installing
  dependencies, retrying a command, changing permissions, debugging a network outage, or repairing a
  one-off external service state as the experience itself.
- Submit no_evolution for one-off task facts, personal preferences, temporary environment/permission/network
  issues, duplicate coverage by existing experiences, or weak evidence with no durable Skill behavior change.
- Every proposal must make future trigger conditions and the changed agent behavior clear in the
  experience summary/content.
Decision examples:
- /evolve skill "include summary rows in tables" with no task evidence: recommend_evolve is allowed from
  user_intent; use evidence_refs=[].
- /evolve skill with no task evidence and no user_intent: submit no_evolution with evidence_refs=[].
- Task evidence includes a failure: read relevant task/experience refs first, then cite only those evidence_refs;
  recommend evolution only when the failure reveals missing reusable Skill instructions.
"""

_EVOLUTION_REVIEW_AGENT_PROMPT_CN = f"""你是 Skill 演进审查 subagent。
只能使用提供的只读演进工具，围绕当前 evolution_review_ref 检查可用证据。
最终回答前必须调用 submit_evolution_review 提交结构化 proposals。
submit_evolution_review 成功后，最终回答只能原样输出 submit_evolution_review 返回的 JSON；
不要包裹 Markdown，不要总结，不要重写任何字段。
当 outcome=recommend_evolve 时，每条 proposal 必须包含唯一 proposal_id，以及可直接提交为经验 draft 的
experience 对象；experience 必须包含 summary 和 content。
experience.target 只能是 {_EVOLUTION_TARGET_VALUES_TEXT}。experience.section 只能是 Instructions、Examples、
Troubleshooting、Scripts、Collaboration、Roles、Constraints、Workflow。普通正文经验优先使用 section=Troubleshooting，
除非另一个允许的 section 明显更合适。
只提交 submit_evolution_review 要求的字段；不要基于证据内容自由添加元数据字段。
不要编辑文件，不要请求其他工具，不要委派给其他 subagent。
何时推荐演进：
- 只有可复用的 Skill 指令改进需要才推荐演进：用户纠正确认了可重复行为问题、目标 Skill 范围内的能力缺口、
  可转化为 Skill 规则的过期知识、更稳定的 recurring workflow，或执行失败暴露了 Skill 缺少 precheck、
  fallback、verification、参数约束或 troubleshooting guidance。
- 本审查将具体执行失败作为可复用 Skill 指令缺口的判据之一。不要把安装依赖、重试命令、修改权限、排查网络故障、
  修复一次性外部服务状态写成经验本身。
- 对一次性任务事实、个人偏好、临时环境/权限/网络问题、已有 experience 覆盖的问题、证据不足且无法形成
  长期 Skill 行为改变的问题，提交 no_evolution。
- 每条 proposal 都必须在 summary/content 中说明未来触发场景和 agent 应改变的行为。
判断示例：
- /evolve skill "以后表格要包含汇总行" 且无任务证据：可基于 user_intent 推荐 recommend_evolve，
  evidence_refs=[]。
- /evolve skill 且无任务证据、无 user_intent：提交 no_evolution，evidence_refs=[]。
- 任务证据包含失败：先读取相关任务/experience refs，再只引用这些 evidence_refs；只有失败暴露出
  可复用 Skill 指令缺口时才推荐演进。
"""

EVOLUTION_REVIEW_AGENT_PROMPT = _EVOLUTION_REVIEW_AGENT_PROMPT_EN


@dataclass(frozen=True)
class _ReviewAgentBinding:
    """Private binding metadata for the stable evolution review agent config."""

    runtime: Any
    query_service: Any
    store: Any


def _set_review_agent_binding(
    config: SubAgentConfig,
    *,
    runtime: Any,
    query_service: Any,
    store: Any,
) -> None:
    """Attach binding metadata to a review agent config."""
    setattr(
        config,
        "_evolution_review_binding",
        _ReviewAgentBinding(
            runtime=runtime,
            query_service=query_service,
            store=store,
        ),
    )


def _get_review_agent_binding(config: Any) -> _ReviewAgentBinding | None:
    """Read binding metadata from a review agent config."""
    return getattr(config, "_evolution_review_binding", None)


def build_evolution_review_agent_prompt(language: str = "cn") -> str:
    """Return the restricted review-agent system prompt in the requested language."""
    return _EVOLUTION_REVIEW_AGENT_PROMPT_EN if language == "en" else _EVOLUTION_REVIEW_AGENT_PROMPT_CN


def build_evolution_review_agent_config(
    *,
    runtime: EvolutionReviewRuntime,
    model: Model | None,
    max_iterations: int = 25,
    query_service: Any | None = None,
    store: Any | None = None,
    language: str = "cn",
    agent_id: str | None = None,
) -> SubAgentConfig:
    """Build the stable restricted review subagent config."""
    subagent_model = model if isinstance(model, Model) else None
    review_agent_id = _review_tool_agent_id(agent_id=agent_id, runtime=runtime)
    config = SubAgentConfig(
        agent_card=AgentCard(
            name=EVOLUTION_REVIEW_AGENT_NAME,
            description=(
                "Skill evolution review agent with read-only tools."
                if language == "en"
                else "Skill 演进审查 agent，仅使用只读工具。"
            ),
        ),
        system_prompt=build_evolution_review_agent_prompt(language),
        tools=create_evolution_review_tools(
            runtime=runtime,
            query_service=query_service,
            store=store,
            language=language,
            agent_id=review_agent_id,
        ),
        mcps=[],
        model=subagent_model,
        rails=[],
        skills=None,
        language=language,
        enable_task_loop=False,
        max_iterations=max_iterations,
        restrict_to_work_dir=True,
    )
    _set_review_agent_binding(
        config,
        runtime=runtime,
        query_service=query_service,
        store=store,
    )
    return config


def ensure_evolution_review_agent_config(subagents: list[Any], config: SubAgentConfig) -> list[Any]:
    """Return subagents with exactly one stable evolution review agent entry."""
    existing = next(
        (item for item in subagents if _agent_name(item) == EVOLUTION_REVIEW_AGENT_NAME),
        None,
    )
    if existing is None:
        return [*subagents, config]

    existing_binding = _get_review_agent_binding(existing)
    incoming_binding = _get_review_agent_binding(config)
    if existing_binding is None or existing_binding != incoming_binding:
        return replace_evolution_review_agent_config(subagents, config)
    return subagents


def remove_evolution_review_agent_config(subagents: list[Any]) -> list[Any]:
    """Return subagents without the stable evolution review agent entry."""
    return [item for item in subagents if _agent_name(item) != EVOLUTION_REVIEW_AGENT_NAME]


def replace_evolution_review_agent_config(subagents: list[Any], config: SubAgentConfig) -> list[Any]:
    """Replace any existing stable evolution review agent entry with the provided config."""
    return [*remove_evolution_review_agent_config(subagents), config]


def _agent_name(config: Any) -> str:
    return str(getattr(getattr(config, "agent_card", None), "name", ""))


def _review_tool_agent_id(*, agent_id: str | None, runtime: EvolutionReviewRuntime) -> str:
    """Return a stable, resource-manager-safe id suffix for review-agent tools."""
    parent_id = str(agent_id or "agent").strip()
    seed = f"{parent_id}_evolution_review_{id(runtime):x}"
    scoped_id = re.sub(r"[^0-9A-Za-z_]+", "_", seed).strip("_")
    return scoped_id or f"evolution_review_{id(runtime):x}"


__all__ = [
    "EVOLUTION_REVIEW_AGENT_NAME",
    "EVOLUTION_REVIEW_AGENT_PROMPT",
    "build_evolution_review_agent_prompt",
    "build_evolution_review_agent_config",
    "ensure_evolution_review_agent_config",
    "remove_evolution_review_agent_config",
    "replace_evolution_review_agent_config",
]
