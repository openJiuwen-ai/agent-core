# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evolution rail implementations and their historical harness exports."""

from importlib import import_module

from openjiuwen.harness.rails.evolution.approval_events import (
    attach_evolution_meta,
    build_evolution_progress_event,
    build_skill_approval_event,
)
from openjiuwen.harness.rails.evolution.approval_runtime import EvolutionApprovalRuntime
from openjiuwen.harness.rails.evolution.commands import (
    build_evolve_review_command_prompt,
    build_rebuild_command_prompt,
    build_simplify_command_prompt,
)
from openjiuwen.harness.rails.evolution.configuration import (
    configure_skill_evolution,
    configure_skill_evolution_runtime,
    unconfigure_skill_evolution,
)
from openjiuwen.harness.rails.evolution.context_evolution_rail import (
    ContextEvolutionRail,
    SummarizeTrajectoriesInput,
)
from openjiuwen.harness.rails.evolution.contracts import (
    EvolutionHostEventMeta,
    EvolutionRequestResult,
    EvolutionSnapshot,
    SimplifyRequestResult,
)
from openjiuwen.harness.rails.evolution.evolution_interrupt_rail import EvolutionInterruptRail
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, EvolutionTriggerPoint
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.rails.evolution.review.subagent import (
    EVOLUTION_REVIEW_AGENT_NAME,
    build_evolution_review_agent_config,
    ensure_evolution_review_agent_config,
    remove_evolution_review_agent_config,
)
from openjiuwen.harness.rails.evolution.skill_evolution_rail import SkillEvolutionRail
from openjiuwen.harness.rails.evolution.skill_evolution_sharing import SkillEvolutionSharingMixin
from openjiuwen.harness.rails.evolution.team_context_evolution_rail import (
    MergedMemoryItem,
    MergedRetrieveResult,
    TeamContextEvolutionRail,
    TeamInsightBuffer,
    TeamInsightEntry,
)
from openjiuwen.harness.rails.evolution.team_skill_evolution_rail import TeamSkillEvolutionRail
from openjiuwen.harness.rails.evolution.trajectory_rail import TrajectoryRail
from openjiuwen.harness.rails.skills.skill_create_rail import SkillCreateRail
from openjiuwen.harness.rails.skills.team_skill_create_rail import TeamSkillCreateRail

__all__ = [
    "EVOLUTION_REVIEW_AGENT_NAME",
    "EvolutionApprovalRuntime",
    "EvolutionHostEventMeta",
    "EvolutionInterruptRail",
    "EvolutionRail",
    "EvolutionRequestResult",
    "EvolutionReviewRuntime",
    "EvolutionSnapshot",
    "EvolutionTriggerPoint",
    "SimplifyRequestResult",
    "SkillCreateRail",
    "SkillEvolutionRail",
    "SkillEvolutionSharingMixin",
    "CapabilityIdentity",
    "SymphonyGraphEvolutionInput",
    "SymphonyGraphEvolutionRail",
    "TeamSymphonyGraphEvolutionRail",
    "project_symphony_execution_fragments",
    "TeamSkillCreateRail",
    "TeamSkillEvolutionRail",
    "TrajectoryRail",
    "attach_evolution_meta",
    "build_evolution_progress_event",
    "build_evolve_review_command_prompt",
    "ContextEvolutionRail",
    "MergedMemoryItem",
    "MergedRetrieveResult",
    "SummarizeTrajectoriesInput",
    "TeamContextEvolutionRail",
    "TeamInsightBuffer",
    "TeamInsightEntry",
    "build_rebuild_command_prompt",
    "build_simplify_command_prompt",
    "build_skill_approval_event",
    "build_evolution_review_agent_config",
    "configure_skill_evolution",
    "configure_skill_evolution_runtime",
    "ensure_evolution_review_agent_config",
    "remove_evolution_review_agent_config",
    "unconfigure_skill_evolution",
]


_SYMPHONY_EXPORTS = {
    "CapabilityIdentity": "openjiuwen.harness.rails.evolution.symphony_execution_graph",
    "project_symphony_execution_fragments": "openjiuwen.harness.rails.evolution.symphony_execution_fragments",
    "SymphonyGraphEvolutionInput": "openjiuwen.harness.rails.evolution.symphony_graph_evolution_rail",
    "SymphonyGraphEvolutionRail": "openjiuwen.harness.rails.evolution.symphony_graph_evolution_rail",
    "TeamSymphonyGraphEvolutionRail": "openjiuwen.harness.rails.evolution.symphony_graph_evolution_rail",
}


def __getattr__(name: str):
    """Load Symphony capture only when the optional OTel stack is installed."""
    module_name = _SYMPHONY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
