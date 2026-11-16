# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A team leader driven by a third-party harness instead of the local DeepAgent."""

from typing import Any

import pytest
from pydantic import ValidationError

from openjiuwen.agent_teams.external.cli_agent import spawn as cli_spawn
from openjiuwen.agent_teams.external.member_runtime import ExternalHarnessMemberRuntime, checkpoint_to_dict
from openjiuwen.agent_teams.schema.blueprint import LeaderSpec, StorageSpec, TeamAgentSpec, TransportSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec
from openjiuwen.harness_protocol import HarnessCapability, HarnessCard, HarnessCheckpoint
from tests.unit_tests.agent_teams.external.test_member_runtime import _FakeHarness, _FakeTeamSession

_LEADER_PROMPT = "Answer in one sentence."


class _FakeCodexHarness(_FakeHarness):
    """Stands in for the Codex SDK harness: same card name, no subprocess."""

    def __init__(self, config: Any) -> None:
        super().__init__()
        self.config = config
        self._card = HarnessCard(
            name="codex",
            implementation_version="1.0",
            capabilities=frozenset({HarnessCapability.GRACEFUL_ABORT, HarnessCapability.NATIVE_TOOLS}),
        )


class _TeamSession(_FakeTeamSession):
    def get_session_id(self) -> str:
        return "session-1"


@pytest.fixture(autouse=True)
def _fake_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_spawn, "CodexHarness", _FakeCodexHarness)


def _spec(team_name: str, **overrides: Any) -> TeamAgentSpec:
    options: dict[str, Any] = {
        "team_name": team_name,
        "agents": {"leader": DeepAgentSpec(system_prompt=_LEADER_PROMPT)},
        "leader": LeaderSpec(cli_agent="codex"),
        "external_cli_agents": [ExternalCliAgentSpec(cli_agent="codex")],
        "storage": StorageSpec(type="memory"),
        "transport": TransportSpec(type="inprocess"),
        "spawn_mode": "inprocess",
        "language": "en",
    }
    options.update(overrides)
    return TeamAgentSpec(**options)


async def _started_leader(spec: TeamAgentSpec, session: _TeamSession) -> tuple[Any, ExternalHarnessMemberRuntime]:
    agent = spec.build()
    await agent.team_backend.db.initialize()
    runtime = agent.resources.harness
    await runtime.start(team_session=session)
    return agent, runtime


def _swarmflow_host(runtime: ExternalHarnessMemberRuntime) -> Any:
    """The async-tool host behind the leader's bound ``swarmflow`` tool."""
    gateway = runtime.harness.start_contexts[-1].tools
    return gateway._tools["swarmflow"]._parent_agent


async def _tool_names(runtime: ExternalHarnessMemberRuntime) -> set[str]:
    context = runtime.harness.start_contexts[-1]
    return {definition.name for definition in await context.tools.definitions()}


@pytest.mark.level0
def test_leader_with_cli_agent_is_built_on_the_external_runtime() -> None:
    agent = _spec("ext_leader_build").build()

    runtime = agent.resources.harness
    assert isinstance(runtime, ExternalHarnessMemberRuntime)
    assert runtime.provider_name == "codex"
    assert agent.resources.memory_manager is None
    # No team-join environment: the leader's tools never leave this process.
    assert "OPENJIUWEN_TEAM_JOIN" not in runtime.harness.config.env
    # Nobody is above the leader: a failed turn has to reach the user's stream.
    assert runtime._adapter._emit_turn_failure is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_started_leader_holds_the_leader_tool_set_and_bootstrap_prompt() -> None:
    session = _TeamSession()
    _agent, runtime = await _started_leader(_spec("ext_leader_tools"), session)
    try:
        context = runtime.harness.start_contexts[-1]
        names = await _tool_names(runtime)

        assert context.host_session_id == "session-1"
        assert context.agent_id == "ext_leader_tools_team_leader"
        assert "build_team" in context.system_prompt
        assert _LEADER_PROMPT in context.system_prompt
        assert {"build_team", "spawn_teammate", "create_task", "send_message", "view_task"} <= names
        local_only = {"async_tasks_list", "async_task_output", "async_task_cancel", "checkpoint", "swarmflow"}
        assert not names & local_only
        assert session.created == [("ext_leader_tools_team_leader", False)]
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_each_cycle_rebinds_the_tools_under_the_current_session() -> None:
    session = _TeamSession()
    _agent, runtime = await _started_leader(_spec("ext_leader_cycles"), session)
    first = runtime.harness.start_contexts[-1].tools
    await runtime.stop()

    await runtime.start(team_session=session)
    try:
        second = runtime.harness.start_contexts[-1].tools
        assert second is not first
        assert await _tool_names(runtime) >= {"build_team", "create_task"}
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_predefined_team_leader_gets_no_spawn_tool() -> None:
    from openjiuwen.agent_teams.schema.team import TeamMemberSpec

    spec = _spec(
        "ext_leader_predefined",
        predefined_members=[TeamMemberSpec(member_name="dev", display_name="Dev")],
        agents={
            "leader": DeepAgentSpec(system_prompt=_LEADER_PROMPT),
            "teammate": DeepAgentSpec(),
        },
        team_mode="predefined",
    )
    _agent, runtime = await _started_leader(spec, _TeamSession())
    try:
        names = await _tool_names(runtime)
        assert "spawn_teammate" not in names
        assert "create_task" in names
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_framework_built_team_leader_is_told_the_policy_and_has_no_build_team() -> None:
    spec = _spec("ext_leader_framework", dispatch_mode="scheduled", bootstrap_mode="framework")
    agent, runtime = await _started_leader(spec, _TeamSession())
    try:
        assert "build_team" not in await _tool_names(runtime)
        policy_text = runtime._team_context_tracker._policy_text
        assert policy_text is not None
        assert policy_text.startswith("<team-policy")
        assert agent.team_backend is not None
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_swarmflow_team_leader_holds_the_swarmflow_and_async_task_tools() -> None:
    spec = _spec("ext_leader_swarmflow", enable_swarmflow=True)
    agent = spec.build()
    await agent.team_backend.db.initialize()
    runtime = agent.resources.harness
    controller = object()
    agent.set_background_task_controller(controller)
    session = _TeamSession()

    await runtime.start(team_session=session)
    names = await _tool_names(runtime)
    first_host = _swarmflow_host(runtime)
    await runtime.stop()
    await runtime.start(team_session=session)
    try:
        assert {"swarmflow", "async_tasks_list", "async_task_output", "async_task_cancel"} <= names
        assert "checkpoint" not in names
        assert agent.infra.swarmflow is not None
        assert agent.background_task_controller is controller
        # One host across run cycles: a background run outlives the cycle that launched it.
        assert _swarmflow_host(runtime) is first_host
        assert first_host.background_task_controller is controller
        # No platform build context on this spec: the host still carries the
        # allowlist a script's agent(agent_type=...) is checked against.
        from openjiuwen.agent_teams.rails.team_context import TeamHandleKey

        allowlist = first_host.build_context.extras[TeamHandleKey.SWARMFLOW_EXTERNAL_CLI_AGENTS]
        assert [entry.cli_agent for entry in allowlist] == ["codex"]
        assert first_host.build_context.member_name == "team_leader"
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_dispose_stops_the_leaders_background_runs_first() -> None:
    stopped: list[tuple[str, bool]] = []

    class _Controller:
        async def stop_by_owner(self, member_name: str, *, include_paused: bool = False) -> bool:
            stopped.append((member_name, include_paused))
            return True

    agent = _spec("ext_leader_dispose", enable_swarmflow=True).build()
    await agent.team_backend.db.initialize()
    runtime = agent.resources.harness
    agent.set_background_task_controller(_Controller())
    await runtime.start(team_session=_TeamSession())

    await runtime.dispose()

    assert stopped == [("team_leader", True)]
    assert runtime.harness.stop_calls == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_restored_history_without_a_provider_checkpoint_is_not_trusted() -> None:
    agent = _spec("ext_leader_lost").build()
    backend = agent.team_backend
    await backend.db.initialize()
    backend.mark_history_restored()
    runtime = agent.resources.harness

    await runtime.start(team_session=_TeamSession())
    try:
        assert backend.history_restored() is False
    finally:
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.level1
async def test_restored_history_with_a_provider_checkpoint_is_kept() -> None:
    agent = _spec("ext_leader_resumed").build()
    backend = agent.team_backend
    await backend.db.initialize()
    backend.mark_history_restored()
    runtime = agent.resources.harness
    session = _TeamSession()
    checkpoint = HarnessCheckpoint(
        provider="codex",
        schema_version="1",
        agent_id="ext_leader_resumed_team_leader",
        host_session_id="session-1",
        checkpoint_id="cp-1",
        sequence=1,
        data={"thread_id": "t-1"},
        provider_session_id="t-1",
        revision=None,
    )
    session.member_session.state["external_runtime"] = {
        "backend": "codex",
        "checkpoint": checkpoint_to_dict(checkpoint),
    }

    await runtime.start(team_session=session)
    try:
        assert backend.history_restored() is True
        assert runtime.harness.start_contexts[-1].checkpoint is not None
    finally:
        await runtime.stop()


@pytest.mark.level1
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"external_cli_agents": []}, "not declared in external_cli_agents"),
        (
            {
                "leader": LeaderSpec(cli_agent="gemini"),
                "external_cli_agents": [ExternalCliAgentSpec(cli_agent="gemini")],
            },
            "cannot drive a leader",
        ),
        ({"enable_fork": True}, "enable_fork"),
    ],
)
def test_spec_rejects_a_leader_harness_the_team_cannot_host(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _spec("ext_leader_invalid", **overrides)
