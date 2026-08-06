"""Canonical active-evolution review helpers.

The review package is intentionally lazy because its subagent helpers import
the tool registry, which in turn imports harness rails.
"""

from importlib import import_module

_EXPORTS = {
    "EvolutionProposalSelection": ("runtime", "EvolutionProposalSelection"),
    "EvolutionReviewRuntime": ("runtime", "EvolutionReviewRuntime"),
    "EvolutionReviewProposal": ("result_schema", "EvolutionReviewProposal"),
    "EvolutionReviewResult": ("result_schema", "EvolutionReviewResult"),
    "EVOLUTION_REVIEW_AGENT_NAME": ("subagent", "EVOLUTION_REVIEW_AGENT_NAME"),
    "build_evolution_review_agent_config": ("subagent", "build_evolution_review_agent_config"),
    "ensure_evolution_review_agent_config": ("subagent", "ensure_evolution_review_agent_config"),
    "normalize_review_proposals": ("result_schema", "normalize_review_proposals"),
    "normalize_review_result": ("result_schema", "normalize_review_result"),
    "remove_evolution_review_agent_config": ("subagent", "remove_evolution_review_agent_config"),
}


def __getattr__(name: str):
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute_name)
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
