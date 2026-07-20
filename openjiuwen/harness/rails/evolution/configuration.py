# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Configure / unconfigure API for skill evolution rails."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.harness.rails.evolution.review.runtime import EvolutionReviewRuntime
from openjiuwen.harness.rails.evolution.skill_evolution_rail import SkillEvolutionRail
from openjiuwen.harness.rails.evolution.team_skill_evolution_rail import TeamSkillEvolutionRail
from openjiuwen.harness.rails.evolution.evolution_interrupt_rail import EvolutionInterruptRail


@dataclass(frozen=True)
class _ApprovalBinding:
    """Dependencies required to bind active evolution approval interrupts."""

    runtime: EvolutionReviewRuntime
    submission_service: object
    auto_save: bool
    language: str


def configure_skill_evolution(
    agent,
    *,
    skills_dir: Union[str, list[str]],
    llm: Model,
    model: str,
    team: bool = False,
    review_runtime: EvolutionReviewRuntime | None = None,
    auto_save: bool = False,
    language: str = "cn",
    **rail_kwargs,
):
    """Configure skill evolution rails on an agent.

    Idempotent: calling with the same configuration is a no-op.
    Calling with a different configuration on an already-configured agent
    raises an error. Cross-mode (regular/team) switching also raises.

    Args:
        agent: The agent to configure.
        skills_dir: Directory or list of directories containing skill definitions.
        llm: LLM client for experience generation.
        model: Model name for experience generation.
        team: When True, configure TeamSkillEvolutionRail (for team/swarm skills).
            When False (default), configure regular SkillEvolutionRail.
        review_runtime: Optional shared review runtime. Created fresh if omitted.
        auto_save: Whether to auto-save generated experiences.
        language: Language for experience generation ("cn" or "en").
        **rail_kwargs: Additional keyword arguments forwarded to the evolution rail.

    Returns:
        The agent, for chaining.
    """
    existing_regular = _find_existing_evolution_rail(agent, team=False)
    existing_team = _find_existing_evolution_rail(agent, team=True)
    _ensure_no_cross_mode_conflict(team, existing_regular, existing_team)

    existing_interrupt = _find_existing_interrupt_rail(agent)

    target_rail = existing_team if team else existing_regular
    runtime = review_runtime or _review_runtime_from_rail(target_rail) or EvolutionReviewRuntime()
    evolution_rail, evolution_created = _get_or_create_evolution_rail(
        target_rail,
        team=team,
        skills_dir=skills_dir,
        llm=llm,
        model=model,
        runtime=runtime,
        auto_save=auto_save,
        language=language,
        rail_kwargs=rail_kwargs,
    )

    binding = _make_approval_binding(evolution_rail, auto_save=auto_save, language=language)
    interrupt_rail, interrupt_created = _get_or_create_or_bind_interrupt_rail(
        agent,
        existing_interrupt,
        binding=binding,
    )
    _add_new_rail_stack(
        agent,
        interrupt_rail=interrupt_rail if interrupt_created else None,
        evolution_rail=evolution_rail if evolution_created else None,
    )

    return agent


async def configure_skill_evolution_runtime(
    agent,
    *,
    skills_dir: Union[str, list[str]],
    llm: Model,
    model: str,
    team: bool = False,
    review_runtime: EvolutionReviewRuntime | None = None,
    auto_save: bool = False,
    language: str = "cn",
    **rail_kwargs,
):
    """Configure skill evolution rails and register newly added rails immediately.

    This preserves ``configure_skill_evolution`` semantics for creation, reuse,
    and mismatch validation, then registers only rails added by this call.
    """
    before_pending = _pending_rail_identities(agent)
    configure_skill_evolution(
        agent,
        skills_dir=skills_dir,
        llm=llm,
        model=model,
        team=team,
        review_runtime=review_runtime,
        auto_save=auto_save,
        language=language,
        **rail_kwargs,
    )

    new_rails = [rail for rail in _pending_rails(agent) if id(rail) not in before_pending]
    for rail in sorted(new_rails, key=_runtime_registration_order):
        await agent.register_rail(rail)
        _remove_pending_rail(agent, rail)
    return agent


def _pending_rails(agent) -> list:
    """Return pending rails when the agent exposes DeepAgent-style storage."""
    return list(getattr(agent, "_pending_rails", []) or [])


def _set_pending_rails(agent, rails: list) -> None:
    """Replace pending rails when the agent exposes DeepAgent-style storage."""
    setattr(agent, "_pending_rails", rails)


def _pending_rail_identities(agent) -> set[int]:
    """Snapshot pending rail object identities."""
    return {id(rail) for rail in _pending_rails(agent)}


def _runtime_registration_order(rail) -> tuple[int, str]:
    """Order rails so runtime registration satisfies evolution dependencies."""
    if isinstance(rail, EvolutionInterruptRail):
        return (0, rail.__class__.__name__)
    if isinstance(rail, (SkillEvolutionRail, TeamSkillEvolutionRail)):
        return (1, rail.__class__.__name__)
    return (2, rail.__class__.__name__)


def _remove_pending_rail(agent, rail) -> None:
    """Drop a successfully registered runtime rail from the pending queue."""
    pending = getattr(agent, "_pending_rails", None)
    if pending is None:
        return
    _set_pending_rails(agent, [queued for queued in pending if queued is not rail])


def _find_existing_evolution_rail(agent, team: bool):
    """Find an existing evolution rail of the given mode, excluding subclass matches."""
    target_type = TeamSkillEvolutionRail if team else SkillEvolutionRail
    rails = agent.find_rails_by_type((target_type,))
    for rail in rails:
        if isinstance(rail, target_type) and rail.__class__ is target_type:
            return rail
    return None


def _review_runtime_from_rail(
    rail: SkillEvolutionRail | TeamSkillEvolutionRail | None,
) -> EvolutionReviewRuntime | None:
    """Return a rail review runtime through its public API when available."""
    if rail is None:
        return None
    return rail.review_runtime


def _find_existing_interrupt_rail(agent):
    """Find an existing EvolutionInterruptRail, if any."""
    rails = agent.find_rails_by_type((EvolutionInterruptRail,))
    for rail in rails:
        if isinstance(rail, EvolutionInterruptRail):
            return rail
    return None


def _ensure_no_cross_mode_conflict(team: bool, existing_regular, existing_team) -> None:
    """Raise if the requested mode conflicts with an already-configured mode."""
    if not team and existing_team is not None:
        raise RuntimeError(
            "Cannot configure regular SkillEvolutionRail: "
            "TeamSkillEvolutionRail is already configured. "
            "Use unconfigure_skill_evolution(team=True) first."
        )
    if team and existing_regular is not None:
        raise RuntimeError(
            "Cannot configure TeamSkillEvolutionRail: "
            "SkillEvolutionRail is already configured. "
            "Use unconfigure_skill_evolution(team=False) first."
        )


def _get_or_create_evolution_rail(
    existing,
    *,
    team: bool,
    skills_dir,
    llm,
    model,
    runtime,
    auto_save,
    language,
    rail_kwargs,
):
    """Return existing rail if config matches, create new rail otherwise."""
    if existing is not None:
        _validate_evolution_rail_config(
            existing,
            auto_save=auto_save,
            language=language,
            rail_kwargs=rail_kwargs,
        )
        return existing, False

    if team:
        rail = TeamSkillEvolutionRail(
            skills_dir,
            llm=llm,
            model=model,
            review_runtime=runtime,
            auto_save=auto_save,
            language=language,
            **rail_kwargs,
        )
    else:
        rail = SkillEvolutionRail(
            skills_dir,
            llm=llm,
            model=model,
            review_runtime=runtime,
            auto_save=auto_save,
            language=language,
            **rail_kwargs,
        )
    return rail, True


def _add_new_rail_stack(
    agent,
    *,
    interrupt_rail: EvolutionInterruptRail | None,
    evolution_rail: SkillEvolutionRail | TeamSkillEvolutionRail | None,
) -> None:
    """Add newly created rails in runtime dependency order."""
    for rail in (interrupt_rail, evolution_rail):
        if rail is not None:
            agent.add_rail(rail)


def _make_approval_binding(
    evolution_rail: SkillEvolutionRail | TeamSkillEvolutionRail,
    *,
    auto_save: bool,
    language: str,
) -> _ApprovalBinding:
    """Build the approval interrupt binding from an evolution rail's public API."""
    return _ApprovalBinding(
        runtime=evolution_rail.review_runtime,
        submission_service=evolution_rail.approval_submission_service,
        auto_save=auto_save,
        language=language,
    )


def _validate_evolution_rail_config(existing, *, auto_save, language, rail_kwargs) -> None:
    """Validate that an existing rail's config matches the requested config.

    Only checks storable attributes. skills_dir, llm, and model are forwarded to
    internal services and not stored on the rail.
    """
    mismatches: list[str] = []
    if getattr(existing, "auto_save", None) != auto_save:
        mismatches.append(f"auto_save: {getattr(existing, 'auto_save', None)!r} != {auto_save!r}")
    if getattr(existing, "_language", None) != language:
        mismatches.append(f"language: {getattr(existing, '_language', None)!r} != {language!r}")

    requested_signal_trigger = rail_kwargs.get("signal_trigger")
    requested_auto_scan = rail_kwargs.get("auto_scan")
    expected_signal_trigger = bool(
        requested_signal_trigger if requested_signal_trigger is not None else requested_auto_scan or False
    )
    if getattr(existing, "signal_trigger", None) != expected_signal_trigger:
        mismatches.append(
            f"signal_trigger: {getattr(existing, 'signal_trigger', None)!r} != {expected_signal_trigger!r}"
        )

    review_old_name = "completion_followup_enabled" if isinstance(existing, TeamSkillEvolutionRail) else "fuzzy_review"
    requested_review_trigger = rail_kwargs.get("review_trigger")
    requested_legacy_review_trigger = rail_kwargs.get(review_old_name)
    expected_review_trigger = bool(
        requested_review_trigger if requested_review_trigger is not None else requested_legacy_review_trigger or False
    )
    if getattr(existing, "review_trigger", None) != expected_review_trigger:
        mismatches.append(
            f"review_trigger: {getattr(existing, 'review_trigger', None)!r} != {expected_review_trigger!r}"
        )
    if mismatches:
        raise RuntimeError(
            f"Evolution rail config mismatch. Existing configuration differs: {'; '.join(mismatches)}. "
            "Use unconfigure_skill_evolution() first to remove the existing rail."
        )


def _get_or_create_or_bind_interrupt_rail(
    agent,
    existing,
    *,
    binding: _ApprovalBinding,
):
    """Return existing interrupt rail if consistent, create/bind otherwise."""
    if existing is None:
        rail = EvolutionInterruptRail(
            review_runtime=binding.runtime,
            submission_service=binding.submission_service,
            auto_save=binding.auto_save,
            language=binding.language,
        )
        return rail, True

    existing_runtime = getattr(existing, "_review_runtime", None)
    existing_submission = getattr(existing, "_submission_service", None)

    if existing_runtime is binding.runtime and existing_submission is binding.submission_service:
        return existing, False

    if existing_runtime is None:
        existing.configure(
            review_runtime=binding.runtime,
            submission_service=binding.submission_service,
            auto_save=binding.auto_save,
            language=binding.language,
        )
        return existing, False

    raise RuntimeError(
        "EvolutionInterruptRail binding mismatch. "
        "The existing interrupt rail is bound to a different runtime or submission service. "
        "Use unconfigure_skill_evolution() first to remove the existing rail."
    )


def unconfigure_skill_evolution(agent, *, team: bool | None = None) -> int:
    """Remove skill evolution rails from an agent.

    Args:
        agent: The agent to unconfigure.
        team: When False, remove SkillEvolutionRail and EvolutionInterruptRail.
            When True, remove TeamSkillEvolutionRail and EvolutionInterruptRail.
            When None, remove all evolution rails and EvolutionInterruptRail.

    Returns:
        Number of rails removed.
    """
    if team is None:
        types_to_remove: tuple[type, ...] = (
            SkillEvolutionRail,
            TeamSkillEvolutionRail,
            EvolutionInterruptRail,
        )
    elif team:
        types_to_remove = (TeamSkillEvolutionRail, EvolutionInterruptRail)
    else:
        types_to_remove = (SkillEvolutionRail, EvolutionInterruptRail)

    return agent.strip_rails_by_type(types_to_remove)


__all__ = [
    "configure_skill_evolution",
    "configure_skill_evolution_runtime",
    "unconfigure_skill_evolution",
]
