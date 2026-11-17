# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-level swarmflow live handles hosted on ``TeamInfra``.

Every member of a team must observe the SAME governor / budget ledger /
model resolver / base specs / expert table: per-member construction would
split the L1/L3 concurrency counters and multiply the token ceiling by N.
The leader constructs the container once in ``setup_agent`` (gate: LEADER
role + ``enable_swarmflow``) and hosts it on its ``TeamInfra``; in-process
teammates adopt the same instance by reference via
``TeamAgent.share_swarmflow_with`` before ``configure``. A member
with no injection keeps the seven handles empty (the tool factory's
resolver gate then drops ``swarmflow``), and the team's shared base build
context is never written — the handles ride per-member extras only.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.agent import agent_configurator as configurator_module
from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.rails.team_context import TeamHandleKey
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.build_context import BuildContext
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec, RailSpec
from openjiuwen.agent_teams.schema.team import TeamMemberSpec, TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.spawn.inprocess_spawn import inprocess_spawn
from openjiuwen.agent_teams.workflow.swarmflow_infra import SwarmflowInfra
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

# Keys whose extras values are the SAME object across members; the expert
# table is shallow-copied per member by ``inject_team_handles`` (defensive
# ``dict()``), so it is asserted by content instead.
_INSTANCE_SHARED_KEYS = (
    TeamHandleKey.SWARMFLOW_MODEL_RESOLVER,
    TeamHandleKey.SWARMFLOW_WORKER_BASE_SPEC,
    TeamHandleKey.SWARMFLOW_HUMAN_BASE_SPEC,
    TeamHandleKey.SWARMFLOW_CONCURRENCY_GOVERNOR,
    TeamHandleKey.SWARMFLOW_BUDGET,
    TeamHandleKey.SWARMFLOW_EXTERNAL_CLI_AGENTS,
)


def _swarmflow_spec(**overrides) -> TeamAgentSpec:
    """A minimal team spec with swarmflow on and a base build context attached."""
    agents = overrides.pop("agents", {"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()})
    overrides.setdefault("enable_swarmflow", True)
    return TeamAgentSpec(
        team_name="wf_team",
        agents=agents,
        build_context=BuildContext(),
        **overrides,
    )


def _member_ctx(role: TeamRole, member_name: str) -> TeamRuntimeContext:
    return TeamRuntimeContext(
        role=role,
        member_name=member_name,
        team_spec=TeamSpec(team_name="wf_team", display_name="wf_team", leader_member_name="leader"),
    )


def _run_setup(monkeypatch, spec, ctx, configurator=None):
    """Run ``setup_agent`` with the harness build stubbed out.

    Returns the configurator and the kwargs captured from the harness
    build — the per-member build context (whose extras carry the handles)
    is what the assertions read.
    """
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        # ``add_rail`` backs the budget-rail registration that runs after the
        # build when ``swarmflow_budget`` is non-None; recorded so the
        # rail-mount tests can assert on the wired instances.
        rails: list[Any] = []
        captured["rails"] = rails
        return SimpleNamespace(workspace=None, sys_operation=None, model=None, add_rail=rails.append)

    monkeypatch.setattr(configurator_module.TeamHarness, "build", fake_build)
    configurator = configurator or AgentConfigurator(card=AgentCard(id="team", name="team", description="team"))
    configurator.setup_agent(spec, ctx)
    return configurator, captured


def _extras(captured: dict) -> dict:
    return captured["build_context"].extras


def test_leader_constructs_handles_once_and_hosts_them_on_infra(monkeypatch):
    """The leader's setup builds the container, hosts it on its TeamInfra and
    injects the container's fields; a later setup on the same configurator
    re-uses the hosted instance instead of constructing a second set."""
    spec = _swarmflow_spec()

    configurator, first = _run_setup(monkeypatch, spec, _member_ctx(TeamRole.LEADER, "leader"))

    handles = configurator.swarmflow
    assert handles is not None
    first_extras = _extras(first)
    assert first_extras[TeamHandleKey.SWARMFLOW_MODEL_RESOLVER] is handles.model_resolver
    assert first_extras[TeamHandleKey.SWARMFLOW_WORKER_BASE_SPEC] is handles.worker_base_spec
    assert first_extras[TeamHandleKey.SWARMFLOW_HUMAN_BASE_SPEC] is handles.human_base_spec
    assert first_extras[TeamHandleKey.SWARMFLOW_CONCURRENCY_GOVERNOR] is handles.concurrency_governor
    assert first_extras[TeamHandleKey.SWARMFLOW_BUDGET] is handles.budget
    assert first_extras[TeamHandleKey.SWARMFLOW_EXTERNAL_CLI_AGENTS] is handles.external_cli_agents
    assert first_extras[TeamHandleKey.SWARMFLOW_EXPERT_SPECS] == handles.expert_specs

    _, second = _run_setup(monkeypatch, spec, _member_ctx(TeamRole.LEADER, "leader"), configurator=configurator)
    second_extras = _extras(second)
    assert second_extras[TeamHandleKey.SWARMFLOW_CONCURRENCY_GOVERNOR] is handles.concurrency_governor
    assert second_extras[TeamHandleKey.SWARMFLOW_BUDGET] is handles.budget


def test_teammate_uses_the_leader_injected_instance(monkeypatch):
    """The leader→share→teammate chain: the teammate's setup reads the
    injected container and wires the SAME objects into its own extras."""
    spec = _swarmflow_spec()

    leader_configurator, leader = _run_setup(monkeypatch, spec, _member_ctx(TeamRole.LEADER, "leader"))
    handles = leader_configurator.swarmflow

    # What inprocess_spawn does between construction and configure: the
    # leader's container is attached to the teammate's configurator.
    teammate_configurator = AgentConfigurator(card=AgentCard(id="t", name="t", description="t"))
    teammate_configurator.swarmflow = handles
    _, teammate = _run_setup(
        monkeypatch, spec, _member_ctx(TeamRole.TEAMMATE, "worker-1"), configurator=teammate_configurator
    )

    leader_extras = _extras(leader)
    teammate_extras = _extras(teammate)
    for key in _INSTANCE_SHARED_KEYS:
        assert teammate_extras[key] is leader_extras[key]
    assert teammate_extras[TeamHandleKey.SWARMFLOW_EXPERT_SPECS] == leader_extras[TeamHandleKey.SWARMFLOW_EXPERT_SPECS]
    # The teammate never constructed a container of its own.
    assert teammate_configurator.swarmflow is handles


def test_teammate_without_injection_keeps_handles_empty(monkeypatch):
    """A member that received no share (subprocess spawn, external CLI) keeps
    all seven handles empty — the tool factory's resolver gate then drops
    ``swarmflow`` from its tool set."""
    spec = _swarmflow_spec()

    configurator, captured = _run_setup(monkeypatch, spec, _member_ctx(TeamRole.TEAMMATE, "worker-1"))

    extras = _extras(captured)
    assert extras[TeamHandleKey.SWARMFLOW_MODEL_RESOLVER] is None
    assert extras[TeamHandleKey.SWARMFLOW_WORKER_BASE_SPEC] is None
    assert extras[TeamHandleKey.SWARMFLOW_HUMAN_BASE_SPEC] is None
    assert extras[TeamHandleKey.SWARMFLOW_CONCURRENCY_GOVERNOR] is None
    assert extras[TeamHandleKey.SWARMFLOW_BUDGET] is None
    assert extras[TeamHandleKey.SWARMFLOW_EXTERNAL_CLI_AGENTS] == ()
    assert extras[TeamHandleKey.SWARMFLOW_EXPERT_SPECS] == {}
    assert configurator.swarmflow is None


def test_swarmflow_off_leaves_handles_empty(monkeypatch):
    """Without ``enable_swarmflow`` nothing is constructed or hosted."""
    spec = _swarmflow_spec(enable_swarmflow=False)

    configurator, captured = _run_setup(monkeypatch, spec, _member_ctx(TeamRole.LEADER, "leader"))

    extras = _extras(captured)
    assert extras[TeamHandleKey.SWARMFLOW_MODEL_RESOLVER] is None
    assert extras[TeamHandleKey.SWARMFLOW_CONCURRENCY_GOVERNOR] is None
    assert extras[TeamHandleKey.SWARMFLOW_BUDGET] is None
    assert configurator.swarmflow is None


def test_shared_base_build_context_is_never_written(monkeypatch):
    """The handles ride per-member extras only; the team's base build context
    (the object every member ``derive()``s from) keeps its extras untouched."""
    spec = _swarmflow_spec()
    assert spec.build_context.extras == {}

    leader_configurator, _ = _run_setup(monkeypatch, spec, _member_ctx(TeamRole.LEADER, "leader"))
    teammate_configurator = AgentConfigurator(card=AgentCard(id="t", name="t", description="t"))
    teammate_configurator.swarmflow = leader_configurator.swarmflow
    _run_setup(monkeypatch, spec, _member_ctx(TeamRole.TEAMMATE, "worker-1"), configurator=teammate_configurator)

    assert spec.build_context.extras == {}


def test_a_rebuilt_build_context_gets_a_fresh_container(monkeypatch):
    """A cold recovery mints a brand-new base context and configurator; the
    mechanism does not ride the context, so the rebuilt leader simply
    constructs a fresh container instead of sharing the previous one."""
    first_configurator, _ = _run_setup(monkeypatch, _swarmflow_spec(), _member_ctx(TeamRole.LEADER, "leader"))
    second_configurator, _ = _run_setup(monkeypatch, _swarmflow_spec(), _member_ctx(TeamRole.LEADER, "leader"))

    assert first_configurator.swarmflow is not None
    assert second_configurator.swarmflow is not None
    assert first_configurator.swarmflow is not second_configurator.swarmflow
    assert first_configurator.swarmflow.budget is not second_configurator.swarmflow.budget


def test_leader_setup_without_a_build_context_still_works(monkeypatch):
    """No platform build context (library-embedded teams): the member context
    is synthesized and the handles flow exactly the same."""
    spec = TeamAgentSpec(
        team_name="wf_team",
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()},
        enable_swarmflow=True,
    )

    configurator, captured = _run_setup(monkeypatch, spec, _member_ctx(TeamRole.LEADER, "leader"))

    assert configurator.swarmflow is not None
    assert _extras(captured)[TeamHandleKey.SWARMFLOW_BUDGET] is configurator.swarmflow.budget


def test_team_agent_share_and_attach_swarmflow():
    """The TeamAgent-level share mirrors the workspace manager share: a
    reference hand-off, and a no-op when the leader hosts nothing."""
    leader = TeamAgent(AgentCard(id="l", name="l", description="l"))
    teammate = TeamAgent(AgentCard(id="t", name="t", description="t"))

    # No handles on the leader (swarmflow off / not yet set up): the share
    # is a no-op, the teammate stays empty.
    leader.share_swarmflow_with(teammate)
    assert teammate._configurator.swarmflow is None

    handles = SwarmflowInfra(
        model_resolver=None,
        worker_base_spec=None,
        human_base_spec=None,
        concurrency_governor=None,
        budget=None,
        external_cli_agents=(),
        expert_specs={},
    )
    leader.attach_swarmflow(handles)
    assert leader._configurator.swarmflow is handles

    leader.share_swarmflow_with(teammate)
    assert teammate._configurator.swarmflow is handles


@pytest.mark.asyncio
async def test_inprocess_spawn_shares_handles_before_configure(monkeypatch):
    """The spawn path injects the leader's handles between the workspace
    share and ``configure`` — after configure the teammate's setup has
    already run handle-less and its tool set dropped ``swarmflow``."""
    calls: list[str] = []
    leader = SimpleNamespace(
        spec=_swarmflow_spec(),
        share_workspace_cache_with=lambda _other: calls.append("share_workspace"),
        share_swarmflow_with=lambda _other: calls.append("share_swarmflow"),
        share_checkpoints_with=lambda _other: calls.append("share_checkpoints"),
        background_task_controller=None,
    )

    def fake_configure(self, spec, ctx, **kwargs):
        calls.append("configure")

    async def fake_run_agent_team(agent, inputs, **kwargs):
        return None

    monkeypatch.setattr(TeamAgent, "configure", fake_configure)
    monkeypatch.setattr(Runner, "run_agent_team", fake_run_agent_team)

    handle = await inprocess_spawn(leader, _member_ctx(TeamRole.TEAMMATE, "worker-1"))

    assert calls == ["share_workspace", "share_swarmflow", "configure", "share_checkpoints"]
    await handle._task


# ---------------------------------------------------------------------------
# Observability rail pair: the static pair the leader's setup evaluates is
# baked into every spec a run builds execution bodies from. Infra-side
# assertion (the providers themselves are no-ops unless tracing is on, which
# the observability ST covers).
# ---------------------------------------------------------------------------


def test_infra_specs_carry_the_observability_rail_pair(monkeypatch):
    """The leader's real setup hands the evaluated rail pair to the builder,
    and the pair lands on the worker base, the human avatar base and every
    expert entry — so each execution body opens an agent span without any
    per-member wiring."""
    spec = _swarmflow_spec(
        agents={
            "leader": DeepAgentSpec(),
            "teammate": DeepAgentSpec(tools=[]),
            "human_agent": DeepAgentSpec(tools=[]),
            "clip": DeepAgentSpec(
                tools=[], agent_template_spec={"agent_card": {"id": "clip", "name": "clip"}}
            ),
        },
        predefined_members=[TeamMemberSpec(member_name="clip", display_name="clip", prompt="P")],
    )

    configurator, _ = _run_setup(monkeypatch, spec, _member_ctx(TeamRole.LEADER, "leader"))
    infra = configurator.swarmflow

    expected = [RailSpec(type="core.team.observability"), RailSpec(type="core.observability")]
    assert infra.worker_base_spec.rails[-2:] == expected
    assert infra.human_base_spec.rails[-2:] == expected
    assert infra.expert_specs["clip"].spec.rails[-2:] == expected


def test_human_base_falls_back_to_teammate_and_keeps_the_rail_pair(monkeypatch):
    """No dedicated human_agent spec: the avatar base derives from the raw
    teammate instead, still carrying the rail pair."""
    spec = _swarmflow_spec(agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec(tools=[])})

    configurator, _ = _run_setup(monkeypatch, spec, _member_ctx(TeamRole.LEADER, "leader"))
    infra = configurator.swarmflow

    assert infra.human_base_spec is not None
    expected = [RailSpec(type="core.team.observability"), RailSpec(type="core.observability")]
    assert infra.human_base_spec.rails[-2:] == expected


# ---------------------------------------------------------------------------
# Budget rail: a member whose handles are wired bills its own model calls
# into the shared session ledger ("whoever holds the ledger bills it").
# ---------------------------------------------------------------------------


def _budget_rails(captured: dict) -> list[Any]:
    from openjiuwen.agent_teams.workflow.backends.budget_rail import SwarmflowBudgetRail

    return [r for r in captured["rails"] if isinstance(r, SwarmflowBudgetRail)]


class _FakeCtx:
    """Minimal AgentCallbackContext stand-in (see workflow test_budget)."""

    def __init__(self, inputs: Any) -> None:
        self.inputs = inputs
        self.finish: dict | None = None

    def request_force_finish(self, result: dict) -> None:
        self.finish = result


def _model_call_ctx(total: int) -> Any:
    from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata
    from openjiuwen.core.single_agent.rail.base import ModelCallInputs

    return _FakeCtx(
        inputs=ModelCallInputs(
            response=AssistantMessage(
                content="hi",
                usage_metadata=UsageMetadata(input_tokens=0, output_tokens=0, total_tokens=total),
            )
        )
    )


def test_teammate_with_shared_handles_mounts_the_budget_rail(monkeypatch):
    """The injected teammate carries the rail on the SAME session ledger as
    the leader, and its own model calls bill that ledger."""
    spec = _swarmflow_spec(swarmflow_budget=12_000)

    leader_configurator, _leader = _run_setup(monkeypatch, spec, _member_ctx(TeamRole.LEADER, "leader"))
    handles = leader_configurator.swarmflow

    teammate_configurator = AgentConfigurator(card=AgentCard(id="t", name="t", description="t"))
    teammate_configurator.swarmflow = handles
    _, teammate = _run_setup(
        monkeypatch, spec, _member_ctx(TeamRole.TEAMMATE, "worker-1"), configurator=teammate_configurator
    )

    rails = _budget_rails(teammate)
    assert len(rails) == 1
    rail = rails[0]
    assert rail._budget is handles.budget  # ONE session pool for the whole team

    asyncio.run(rail.after_model_call(_model_call_ctx(120)))
    assert handles.budget.spent == 120  # the teammate's own loop billed it


def test_teammate_without_injection_mounts_no_budget_rail(monkeypatch):
    """A member that received no share keeps its behaviour unchanged: no
    rail, nothing billed, no token accounting it never opted into."""
    spec = _swarmflow_spec()

    _configurator, captured = _run_setup(monkeypatch, spec, _member_ctx(TeamRole.TEAMMATE, "worker-1"))

    assert _budget_rails(captured) == []


def test_leader_budget_rail_is_unchanged(monkeypatch):
    """Leader regression: the rail still mounts, still on the hosted ledger."""
    spec = _swarmflow_spec()

    configurator, captured = _run_setup(monkeypatch, spec, _member_ctx(TeamRole.LEADER, "leader"))

    rails = _budget_rails(captured)
    assert len(rails) == 1
    assert rails[0]._budget is configurator.swarmflow.budget


def test_teammate_and_worker_rails_converge_on_one_session_ledger(monkeypatch):
    """Accounting integrity: the teammate's own rail and a run's worker rail
    both draw down the SAME session pool, while the per-run ledger tracks
    only the run's own spend (the same tokens, two independent counters)."""
    from openjiuwen.agent_teams.workflow.backends.budget_rail import SwarmflowBudgetRail
    from openjiuwen.agent_teams.workflow.engine.budget import BudgetLedger

    spec = _swarmflow_spec()
    leader_configurator, _ = _run_setup(monkeypatch, spec, _member_ctx(TeamRole.LEADER, "leader"))
    handles = leader_configurator.swarmflow

    teammate_configurator = AgentConfigurator(card=AgentCard(id="t", name="t", description="t"))
    teammate_configurator.swarmflow = handles
    _, teammate = _run_setup(
        monkeypatch, spec, _member_ctx(TeamRole.TEAMMATE, "worker-1"), configurator=teammate_configurator
    )
    teammate_rail = _budget_rails(teammate)[0]

    per_run = BudgetLedger(total=1_000)
    worker_rail = SwarmflowBudgetRail(handles.budget, workflow_budget=per_run)

    asyncio.run(teammate_rail.after_model_call(_model_call_ctx(120)))
    asyncio.run(worker_rail.after_model_call(_model_call_ctx(60)))

    assert handles.budget.spent == 180  # one session pool across both
    assert per_run.spent == 60  # the run's own tally saw only its worker
