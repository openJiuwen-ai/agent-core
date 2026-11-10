# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-level shared swarmflow infrastructure.

The shared swarmflow objects (worker-model resolver, worker and human base
specs, concurrency governor, budget ledger, external-CLI allowlist, expert
table) are team-level singletons: every member must draw down the same
budget pool under the same L1/L3 concurrency authority. The leader's
configurator builds the container once via :func:`build_swarmflow_infra`
and hosts it on its ``TeamInfra``; in-process teammates adopt the built
instance by reference through ``TeamAgent.share_swarmflow_with`` before
their configure — the same sharing pattern as the team workspace manager.
A member with no injected instance keeps every field empty, and the tool
factory's resolver gate then drops ``swarmflow`` from its tool set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from openjiuwen.agent_teams.schema.team import TeamRuntimeContext


class SwarmflowInfra:
    """Team-level shared swarmflow infrastructure.

    Hosted on the leader's ``TeamInfra`` and shared with in-process
    teammates by reference: one governor / budget ledger / model resolver
    per team, never per member.
    """

    __slots__ = (
        "model_resolver",
        "worker_base_spec",
        "human_base_spec",
        "concurrency_governor",
        "budget",
        "external_cli_agents",
        "expert_specs",
    )

    def __init__(
        self,
        *,
        model_resolver: Any,
        worker_base_spec: Any,
        human_base_spec: Any,
        concurrency_governor: Any,
        budget: Any,
        external_cli_agents: tuple[Any, ...],
        expert_specs: dict[str, Any],
    ) -> None:
        self.model_resolver = model_resolver
        self.worker_base_spec = worker_base_spec
        self.human_base_spec = human_base_spec
        self.concurrency_governor = concurrency_governor
        self.budget = budget
        self.external_cli_agents = external_cli_agents
        self.expert_specs = expert_specs


def build_swarmflow_infra(
    spec: "TeamAgentSpec",
    ctx: "TeamRuntimeContext",
    observability_rail_specs: list[Any],
) -> SwarmflowInfra:
    """Derive the team-level swarmflow infrastructure from the team spec.

    Pure function of the team spec plus the static observability rail pair,
    so constructing it once for the whole team is lossless. Called only from
    the leader's ``setup_agent`` — the single construction point; everyone
    else receives the built container by reference. The derivation helpers
    stay in ``agent_configurator`` where they have always lived; the imports
    are function-level to keep the workflow→agent import direction lazy,
    as elsewhere in the package.
    """
    from openjiuwen.agent_teams.agent.agent_configurator import (
        _SwarmflowModelResolver,
        _derive_swarmflow_external_cli_agents,
        _derive_swarmflow_worker_base_spec,
    )
    from openjiuwen.agent_teams.workflow.backends.expert_specs import derive_swarmflow_expert_specs
    from openjiuwen.agent_teams.workflow.concurrency import ConcurrencyGovernor, validate_swarmflow_concurrency
    from openjiuwen.agent_teams.workflow.engine.budget import BudgetLedger

    # Callable class (same call contract as the historical closure) so
    # the inject chain stays unchanged, plus pool_names for the
    # engine's fail-fast model-hint check. The resolver-level
    # ValueError raise from release/v0.1.19-2 is kept verbatim: it
    # backs the non-engine callers (CLI MCP tool set, tool gateway)
    # and the shrunk-pool window behind the engine's pre-flight.
    model_resolver = _SwarmflowModelResolver(ctx.team_spec)

    worker_base_spec = _derive_swarmflow_worker_base_spec(spec, observability_rail_specs)
    # Human-session avatars derive from the human_agent spec; fall back to
    # the raw worker base so human_session still works when no dedicated
    # human_agent spec is configured (it just lacks human-tuned desc).
    # Like the worker base, it mounts the observability rail pair so
    # the avatar's records carry an agent span in the trajectory.
    human_base = spec.agents.get("human_agent") or spec.agents.get("teammate") or spec.agents.get("leader")
    human_base_spec = (
        human_base.model_copy(
            update={"rails": list(human_base.rails or []) + observability_rail_specs},
        )
        if human_base is not None
        else None
    )

    l2_cap = validate_swarmflow_concurrency(spec.swarmflow_concurrency)
    concurrency_governor = ConcurrencyGovernor(
        spec.swarmflow_concurrency,
        agents_per_run_cap=l2_cap,
    )

    # One ledger per team, shared by every run any member launches (like the
    # governor's L3): concurrent runs draw down one pool. ``total=None``
    # keeps it unbounded while still giving scripts a live ``spent()``.
    budget = BudgetLedger(total=spec.swarmflow_budget)
    external_cli_agents = _derive_swarmflow_external_cli_agents(spec)

    # Experts are AgentGroup members already in the blueprint; a script
    # picks one by member name. Derived once here, next to the worker
    # base spec, so both ride the same rails and progressive_tool switch.
    expert_specs = derive_swarmflow_expert_specs(spec, observability_rail_specs)

    return SwarmflowInfra(
        model_resolver=model_resolver,
        worker_base_spec=worker_base_spec,
        human_base_spec=human_base_spec,
        concurrency_governor=concurrency_governor,
        budget=budget,
        external_cli_agents=external_cli_agents,
        expert_specs=expert_specs,
    )


__all__ = ["SwarmflowInfra", "build_swarmflow_infra"]
