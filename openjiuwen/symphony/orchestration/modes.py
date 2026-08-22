"""Registry of orchestration planning modes.

The supported mode names used to be spelled out as a literal set in two
places: ``OrchestrationConfig.__post_init__`` (validation) and
``OrchestrationService.plan`` (dispatch). The two copies had to be kept in
sync by hand, and an integrator could not add a planning mode without
patching the library.

This module keeps the set in one place and lets callers register extra
modes. The built-in modes stay exactly as they were: with nothing
registered, ``available_modes()`` is ``("fast", "beam")`` and dispatch is
unchanged.
"""

from __future__ import annotations

from typing import Any, Callable

BUILTIN_MODES: tuple[str, ...] = ("fast", "beam")
"""Modes implemented by this package. They cannot be overridden."""

PlannerFactory = Callable[..., Any]
"""Builds a planner for a registered mode.

Called with keyword arguments only, so new context can be added later
without breaking existing factories. A factory currently receives:

``artifacts``
    Loaded :class:`~openjiuwen.symphony.orchestration.contracts.CapabilityGraph`
    artifacts, already filtered by ``disabled_capability_ids``.
``model``
    The planning model.
``model_response_observer``
    Usage observer, may be ``None``.
``config``
    The active :class:`~openjiuwen.symphony.orchestration.config.OrchestrationConfig`.
``candidate_skill_ids``
    Skill ids selected by the caller, may be ``None``.
``progress_callback``
    Planner-level progress callback.
``language``
    Normalized orchestration language.
``dynamic_overlay``
    Session-derived edge overlay, ``{}`` when disabled.

The returned object must expose ``async def plan(query: str)`` returning the
same payload shape the built-in planners return.
"""

_EXTRA_MODES: dict[str, PlannerFactory] = {}


def register_mode(name: str, factory: PlannerFactory) -> None:
    """Register an additional planning mode.

    Raises ``ValueError`` for an empty name or an attempt to shadow a
    built-in mode, so a typo cannot silently replace ``fast`` or ``beam``.
    """

    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("Orchestration mode name must be a non-empty string.")
    if normalized in BUILTIN_MODES:
        raise ValueError(f"Cannot override built-in orchestration mode: {normalized}")
    if not callable(factory):
        raise TypeError("Orchestration mode factory must be callable.")
    _EXTRA_MODES[normalized] = factory


def unregister_mode(name: str) -> None:
    """Remove a previously registered mode. Unknown names are ignored."""

    _EXTRA_MODES.pop(str(name or "").strip(), None)


def registered_modes() -> tuple[str, ...]:
    """Return only the externally registered mode names, sorted."""

    return tuple(sorted(_EXTRA_MODES))


def available_modes() -> tuple[str, ...]:
    """Return every accepted mode name: built-ins first, then registered."""

    return (*BUILTIN_MODES, *registered_modes())


def is_supported(name: str | None) -> bool:
    """Return whether ``name`` is an accepted orchestration mode."""

    return str(name or "").strip() in set(available_modes())


def planner_factory(name: str | None) -> PlannerFactory | None:
    """Return the factory for a registered mode, or ``None`` for built-ins."""

    return _EXTRA_MODES.get(str(name or "").strip())


def unsupported_mode_error(name: str | None) -> ValueError:
    """Build the shared error for an unknown mode, listing what is accepted."""

    return ValueError(f"Unsupported orchestration mode: {name}. Available modes: {', '.join(available_modes())}.")
