# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team build-context handles + accessors for team rail providers.

Team rails (``TeamToolRail`` / ``TeamPolicyRail`` / ...) need live runtime
handles — ``team_backend`` / ``workspace_manager`` / ``model_allocator`` / ... —
that the ``AgentConfigurator`` constructs at setup time, not platform-supplied
spec data. Those handles ride on the per-member ``BuildContext.extras`` under
the ``TeamHandleKey`` namespace; team rail provider factories read them directly
(they are runtime plumbing, not serializable construction params — mirroring how
swarm passes ``trajectory_registry`` / ``_parent_model`` through extras).

``inject_team_handles`` is the single writer (the configurator); the ``get_*``
accessors are the readers (the provider factories). Rails are never cached:
every native rebuild mints a fresh rail through its provider factory. State that
must outlive a rebuild lives in a reused object injected here and passed into the
fresh rail's constructor — e.g. ``reliability_components`` carries the detector
sliding windows and the leader's bound anomaly sink across cycles.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Optional,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.messager import Messager
    from openjiuwen.agent_teams.models.allocator import ModelAllocator
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
    from openjiuwen.agent_teams.reliability.factory import ReliabilityComponents
    from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
    from openjiuwen.agent_teams.tools.team import TeamBackend
    from openjiuwen.harness.tools.worktree import WorktreeManager


class TeamHandleKey:
    """extras keys under which team live handles ride on the build context."""

    TEAM_BACKEND = "team.team_backend"
    WORKSPACE_MANAGER = "team.workspace_manager"
    MODEL_ALLOCATOR = "team.model_allocator"
    MESSAGER = "team.messager"
    ON_TEAMMATE_CREATED = "team.on_teammate_created"
    SWARMFLOW_MODEL_RESOLVER = "team.swarmflow_model_resolver"
    SWARMFLOW_WORKER_BASE_SPEC = "team.swarmflow_worker_base_spec"
    SWARMFLOW_HUMAN_BASE_SPEC = "team.swarmflow_human_base_spec"
    SWARMFLOW_CONCURRENCY_GOVERNOR = "team.swarmflow_concurrency_governor"
    SWARMFLOW_BUDGET = "team.swarmflow_budget"
    RELIABILITY_COMPONENTS = "team.reliability_components"
    PERMISSIONS_OVERRIDE = "team.permissions_override"
    WORKTREE_MANAGER = "team.worktree_manager"


def inject_team_handles(
    extras: dict[str, Any],
    *,
    team_backend: Optional["TeamBackend"] = None,
    workspace_manager: Optional["TeamWorkspaceManager"] = None,
    model_allocator: Optional["ModelAllocator"] = None,
    messager: Optional["Messager"] = None,
    on_teammate_created: Optional[Callable[[str], Awaitable[None]]] = None,
    swarmflow_model_resolver: Optional[Callable[[str], Any]] = None,
    swarmflow_worker_base_spec: Optional["DeepAgentSpec"] = None,
    swarmflow_human_base_spec: Optional["DeepAgentSpec"] = None,
    swarmflow_concurrency_governor: Any = None,
    swarmflow_budget: Any = None,
    reliability_components: Optional["ReliabilityComponents"] = None,
    permissions_override: Optional[dict[str, str]] = None,
    worktree_manager: Optional["WorktreeManager"] = None,
) -> None:
    """Write the team live handles into ``extras`` (configurator-side).

    The caller must hand a per-member extras dict (decoupled from any shared
    base context) so members never cross-contaminate handles or caches.

    Args:
        extras: The per-member ``BuildContext.extras`` dict to populate.
        team_backend: The member's team backend.
        workspace_manager: The team workspace manager, if any.
        model_allocator: The team model allocator, if any.
        messager: The member's messager, if any.
        on_teammate_created: The leader's spawn-on-created callback, if any.
        swarmflow_model_resolver: The leader's swarmflow worker-model resolver, if
            any (non-None only for a leader with ``enable_swarmflow``).
        swarmflow_worker_base_spec: The base ``DeepAgentSpec`` swarmflow workers
            derive from (the team's teammate spec, or the leader spec when no
            teammate is configured). Non-None only for a swarmflow leader; carries
            teammate capabilities (model / tools / skills / workspace) WITHOUT the
            team rails, so each worker is a teammate-equivalent without team tools.
        swarmflow_budget: The leader's shared ``BudgetLedger``, capping the tokens
            all its swarmflow runs may burn. Non-None only for a swarmflow leader;
            unbounded (``total=None``) unless ``swarmflow_budget`` is configured.
        reliability_components: The member's reused reliability core (detectors /
            remediator / local reporter), if reliability is enabled. Built once
            and wrapped by a fresh rail each cycle so its state outlives rebuilds.
        permissions_override: Per-member permission narrowing from
            ``spawn_teammate.permissions``.  ``None`` when no override was
            specified at spawn time.
        worktree_manager: The owner-scoped worktree manager, if worktree
            isolation is enabled for this team.
    """
    extras[TeamHandleKey.TEAM_BACKEND] = team_backend
    extras[TeamHandleKey.WORKSPACE_MANAGER] = workspace_manager
    extras[TeamHandleKey.MODEL_ALLOCATOR] = model_allocator
    extras[TeamHandleKey.MESSAGER] = messager
    extras[TeamHandleKey.ON_TEAMMATE_CREATED] = on_teammate_created
    extras[TeamHandleKey.SWARMFLOW_MODEL_RESOLVER] = swarmflow_model_resolver
    extras[TeamHandleKey.SWARMFLOW_WORKER_BASE_SPEC] = swarmflow_worker_base_spec
    extras[TeamHandleKey.SWARMFLOW_HUMAN_BASE_SPEC] = swarmflow_human_base_spec
    extras[TeamHandleKey.SWARMFLOW_CONCURRENCY_GOVERNOR] = swarmflow_concurrency_governor
    extras[TeamHandleKey.SWARMFLOW_BUDGET] = swarmflow_budget
    extras[TeamHandleKey.RELIABILITY_COMPONENTS] = reliability_components
    extras[TeamHandleKey.PERMISSIONS_OVERRIDE] = permissions_override
    extras[TeamHandleKey.WORKTREE_MANAGER] = worktree_manager


def _get(context: Any, key: str) -> Any:
    """Return ``context.extras[key]`` defensively (None when absent)."""
    extras = getattr(context, "extras", None) if context is not None else None
    return extras.get(key) if extras else None


def get_team_backend(context: Any) -> Optional["TeamBackend"]:
    """Return the team backend handle from the build context, or None."""
    return _get(context, TeamHandleKey.TEAM_BACKEND)


def get_workspace_manager(context: Any) -> Optional["TeamWorkspaceManager"]:
    """Return the team workspace manager handle, or None."""
    return _get(context, TeamHandleKey.WORKSPACE_MANAGER)


def get_model_allocator(context: Any) -> Optional["ModelAllocator"]:
    """Return the model allocator handle, or None."""
    return _get(context, TeamHandleKey.MODEL_ALLOCATOR)


def get_messager(context: Any) -> Optional["Messager"]:
    """Return the messager handle, or None."""
    return _get(context, TeamHandleKey.MESSAGER)


def get_on_teammate_created(context: Any) -> Optional[Callable[[str], Awaitable[None]]]:
    """Return the on-teammate-created callback, or None."""
    return _get(context, TeamHandleKey.ON_TEAMMATE_CREATED)


def get_swarmflow_model_resolver(context: Any) -> Optional[Callable[[str], Any]]:
    """Return the swarmflow worker-model resolver, or None."""
    return _get(context, TeamHandleKey.SWARMFLOW_MODEL_RESOLVER)


def get_swarmflow_worker_base_spec(context: Any) -> Optional["DeepAgentSpec"]:
    """Return the swarmflow worker base spec (teammate/leader spec), or None."""
    return _get(context, TeamHandleKey.SWARMFLOW_WORKER_BASE_SPEC)


def get_swarmflow_human_base_spec(context: Any) -> Optional["DeepAgentSpec"]:
    """Return the swarmflow human-session avatar base spec (human_agent spec), or None."""
    return _get(context, TeamHandleKey.SWARMFLOW_HUMAN_BASE_SPEC)


def get_swarmflow_concurrency_governor(context: Any) -> Any:
    """Return the swarmflow concurrency governor, or None."""
    return _get(context, TeamHandleKey.SWARMFLOW_CONCURRENCY_GOVERNOR)


def get_swarmflow_budget(context: Any) -> Any:
    """Return the leader's shared swarmflow ``BudgetLedger``, or None."""
    return _get(context, TeamHandleKey.SWARMFLOW_BUDGET)


def get_reliability_components(context: Any) -> Optional["ReliabilityComponents"]:
    """Return the reused reliability components handle, or None.

    Present only for members with reliability enabled. The provider factory
    wraps these reused (stateful) components in a fresh rail each cycle.
    """
    return _get(context, TeamHandleKey.RELIABILITY_COMPONENTS)


def get_permissions_override(context: Any) -> Optional[dict[str, str]]:
    """Return the per-member permissions override, or None.

    Set by ``AgentConfigurator.setup_agent`` from
    ``TeamRuntimeContext.permissions_override``.  ``None`` when no override
    was specified at spawn time.
    """
    return _get(context, TeamHandleKey.PERMISSIONS_OVERRIDE)


def get_worktree_manager(context: Any) -> Optional["WorktreeManager"]:
    """Return the owner-scoped worktree manager, or None."""
    return _get(context, TeamHandleKey.WORKTREE_MANAGER)


__all__ = [
    "TeamHandleKey",
    "inject_team_handles",
    "get_team_backend",
    "get_workspace_manager",
    "get_model_allocator",
    "get_messager",
    "get_on_teammate_created",
    "get_permissions_override",
    "get_swarmflow_model_resolver",
    "get_swarmflow_worker_base_spec",
    "get_swarmflow_human_base_spec",
    "get_swarmflow_concurrency_governor",
    "get_swarmflow_budget",
    "get_reliability_components",
    "get_worktree_manager",
]
