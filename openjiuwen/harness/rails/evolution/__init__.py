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
    configure_ttse_evolution,
    configure_ttse_evolution_runtime,
    unconfigure_skill_evolution,
    unconfigure_ttse_evolution,
)
from openjiuwen.harness.rails.evolution.context_evolution_rail import (
    ContextEvolutionRail,
    SummarizeTrajectoriesInput,
)
from openjiuwen.harness.rails.evolution.context_evolve_rail import ContextEvolveRail
from openjiuwen.harness.rails.evolution.contracts import (
    EvolutionHostEventMeta,
    EvolutionRequestResult,
    SimplifyRequestResult,
)
from openjiuwen.harness.rails.evolution.evolution_interrupt_rail import EvolutionInterruptRail
from openjiuwen.harness.rails.evolution.evolution_rail import (
    EvolutionRail,
    EvolutionTriggerPoint,
    PreparedEvolutionInput,
)
from openjiuwen.harness.rails.evolution.member_skill_evolution_rail import (
    MemberSkillEvolutionRail,
)
from openjiuwen.harness.rails.evolution.metis_context_evolve_rail import MetisContextEvolveRail
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.rails.evolution.review.subagent import (
    EVOLUTION_REVIEW_AGENT_NAME,
    build_evolution_review_agent_config,
    ensure_evolution_review_agent_config,
    remove_evolution_review_agent_config,
)
from openjiuwen.harness.rails.evolution.skill_evolution_rail import SkillEvolutionRail
from openjiuwen.harness.rails.evolution.skill_evolution_sharing import SkillEvolutionSharingMixin
from openjiuwen.harness.rails.evolution.symphony_edge_evaluator import (
    SymphonyEdgeEndpointSummary,
    SymphonyEdgeEvaluationSummary,
    evaluate_symphony_edge_candidates,
)
from openjiuwen.harness.rails.evolution.symphony_edge_evidence import (
    SymphonyEdgeCandidate,
    SymphonyEdgeDecision,
    build_model_edge_decisions,
    build_symphony_edge_candidates,
)
from openjiuwen.harness.rails.evolution.symphony_execution_fragments import (
    SymphonyExecutionFragment,
    project_symphony_execution_fragments,
)
from openjiuwen.harness.rails.evolution.symphony_execution_graph import (
    CapabilityIdentity,
    CapabilitySnapshotProvider,
    build_symphony_execution_graph,
)
from openjiuwen.harness.rails.evolution.symphony_graph_evolution_rail import (
    GraphSnapshotProvider,
    SymphonyEvolutionSubmitCallback,
    SymphonyGraphEvolutionInput,
    SymphonyGraphEvolutionRail,
    TeamSymphonyGraphEvolutionRail,
)
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
    "EvolutionTriggerPoint",
    "PreparedEvolutionInput",
    "SimplifyRequestResult",
    "SkillCreateRail",
    "SkillEvolutionRail",
    "MemberSkillEvolutionRail",
    "SkillEvolutionSharingMixin",
    "CapabilityIdentity",
    "CapabilitySnapshotProvider",
    "GraphSnapshotProvider",
    "SymphonyEdgeCandidate",
    "SymphonyEdgeDecision",
    "SymphonyEdgeEndpointSummary",
    "SymphonyEdgeEvaluationSummary",
    "SymphonyExecutionFragment",
    "SymphonyEvolutionSubmitCallback",
    "SymphonyGraphEvolutionInput",
    "SymphonyGraphEvolutionRail",
    "TeamSymphonyGraphEvolutionRail",
    "TeamSkillCreateRail",
    "TeamSkillEvolutionRail",
    "TrajectoryRail",
    "attach_evolution_meta",
    "build_evolution_progress_event",
    "build_evolve_review_command_prompt",
    "ContextEvolutionRail",
    "ContextEvolveRail",
    "MergedMemoryItem",
    "MergedRetrieveResult",
    "SummarizeTrajectoriesInput",
    "MetisContextEvolveRail",
    "TeamContextEvolutionRail",
    "TeamInsightBuffer",
    "TeamInsightEntry",
    "build_rebuild_command_prompt",
    "build_simplify_command_prompt",
    "build_skill_approval_event",
    "build_model_edge_decisions",
    "build_symphony_edge_candidates",
    "build_symphony_execution_graph",
    "evaluate_symphony_edge_candidates",
    "project_symphony_execution_fragments",
    "build_evolution_review_agent_config",
    "configure_skill_evolution",
    "configure_skill_evolution_runtime",
    "ensure_evolution_review_agent_config",
    "remove_evolution_review_agent_config",
    "unconfigure_skill_evolution",
    "configure_ttse_evolution",
    "configure_ttse_evolution_runtime",
    "unconfigure_ttse_evolution",
    "TTSEConfig",
    "TTSERail",
    "TTSERecordStore",
    "SuccessDetector",
    "SuccessOutcome",
    "TrajectoryErrorSuccessDetector",
    "SignalBasedSuccessDetector",
]

_TTSE_LAZY_ATTRS = {
    "TTSERail": ("openjiuwen.harness.rails.evolution.ttse_rail", "TTSERail"),
    "TTSEConfig": ("openjiuwen.agent_evolving.ttse", "TTSEConfig"),
    "TTSERecordStore": ("openjiuwen.agent_evolving.ttse", "TTSERecordStore"),
    "SuccessDetector": ("openjiuwen.agent_evolving.ttse", "SuccessDetector"),
    "SuccessOutcome": ("openjiuwen.agent_evolving.ttse", "SuccessOutcome"),
    "TrajectoryErrorSuccessDetector": ("openjiuwen.agent_evolving.ttse", "TrajectoryErrorSuccessDetector"),
    "SignalBasedSuccessDetector": ("openjiuwen.agent_evolving.ttse", "SignalBasedSuccessDetector"),
}


def __getattr__(name: str):
    """Load TTSE types only when an explicit TTSE export is requested."""
    target = _TTSE_LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted({*globals().keys(), *_TTSE_LAZY_ATTRS})
