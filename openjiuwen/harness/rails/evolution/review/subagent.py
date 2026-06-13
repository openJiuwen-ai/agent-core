# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Stable subagent config for Skill evolution review."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_evolving.protocols import EVOLUTION_TARGET_VALUES
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.agent_evolving.tools import (
    create_evolution_review_tools,
)

EVOLUTION_REVIEW_AGENT_NAME = "evolution_reviewer"
_EVOLUTION_TARGET_VALUES_TEXT = ", ".join(EVOLUTION_TARGET_VALUES)

_EVOLUTION_REVIEW_AGENT_PROMPT_EN = f"""You are the restricted Skill evolution review agent.
Use only the provided read-only evolution tools to inspect bounded evidence for the given evolution_review_ref.
You must call submit_evolution_review with structured proposals before your final answer.
After submit_evolution_review succeeds, your final answer must contain only the exact JSON returned by
submit_evolution_review. Do not wrap it in Markdown, summarize it, or rewrite any field.
For recommend_evolve, every proposal must include a unique proposal_id and an experience object with
summary and content fields that are ready to submit as an experience draft.
experience.target must be one of {_EVOLUTION_TARGET_VALUES_TEXT}. experience.section must be one of Instructions, Examples,
Troubleshooting, Scripts, Collaboration, Roles, Constraints, Workflow. For normal body experiences, prefer
section=Troubleshooting unless another allowed section is clearly better.
Do not edit files. Do not ask for other tools. Do not delegate to subagents.
"""

_EVOLUTION_REVIEW_AGENT_PROMPT_CN = f"""你是受限的 Skill 演进审查 subagent。
只能使用提供的只读演进工具，围绕当前 evolution_review_ref 检查有界证据。
最终回答前必须调用 submit_evolution_review 提交结构化 proposals。
submit_evolution_review 成功后，最终回答只能原样输出 submit_evolution_review 返回的 JSON；
不要包裹 Markdown，不要总结，不要重写任何字段。
当 outcome=recommend_evolve 时，每条 proposal 必须包含唯一 proposal_id，以及可直接提交为经验 draft 的
experience 对象；experience 必须包含 summary 和 content。
experience.target 只能是 {_EVOLUTION_TARGET_VALUES_TEXT}。experience.section 只能是 Instructions、Examples、
Troubleshooting、Scripts、Collaboration、Roles、Constraints、Workflow。普通正文经验优先使用 section=Troubleshooting，
除非另一个允许的 section 明显更合适。
不要编辑文件，不要请求其他工具，不要委派给其他 subagent。
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
                "Restricted Skill evolution review agent with scope-bound read-only tools."
                if language == "en"
                else "受限 Skill 演进审查 agent，仅使用 scope-bound 只读工具。"
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
        max_iterations=8,
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
    if existing_binding is None:
        raise RuntimeError(
            "Existing evolution_reviewer lacks binding metadata. "
            "Use build_evolution_review_agent_config() to create the config."
        )
    incoming_binding = _get_review_agent_binding(config)
    if existing_binding != incoming_binding:
        raise RuntimeError(
            "evolution_reviewer runtime/query_service/store binding mismatch. "
            "Use a consistent EvolutionReviewRuntime, query service, and store when reconfiguring."
        )
    return subagents


def remove_evolution_review_agent_config(subagents: list[Any]) -> list[Any]:
    """Return subagents without the stable evolution review agent entry."""
    return [item for item in subagents if _agent_name(item) != EVOLUTION_REVIEW_AGENT_NAME]


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
]
