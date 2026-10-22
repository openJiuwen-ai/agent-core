# coding: utf-8
"""Tests for TeamAgent configuration and spawn payloads."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.interaction.payload import HumanAgentMessage
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.prompts import TeamSectionName
from openjiuwen.agent_teams.rails import TeamPlanModeRail, TeamPolicyRail
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    TeamAgentSpec,
    TransportSpec,
)
from openjiuwen.agent_teams.schema.team import (
    TeamMemberSpec,
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.core.runner.spawn import SpawnAgentKind
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _dummy_agents(**overrides) -> dict[str, DeepAgentSpec]:
    """Build a minimal agents dict for unit tests (no real LLM)."""
    defaults = dict(overrides)
    return {"leader": DeepAgentSpec(**defaults)}


class _FakeCoordination:
    """Minimal coordination double for TeamAgent invoke branch tests."""

    def __init__(self, agent: TeamAgent) -> None:
        self._agent = agent
        self.started = False
        self.finalized = False
        self.enqueued_inputs: list[Any] = []
        self.mailbox_enqueued = False
        self.task_poll_enqueued = False

    async def start(self, session=None) -> None:
        self.started = True

    async def enqueue_user_input(self, inputs: Any) -> None:
        self.enqueued_inputs.append(inputs)
        await self._agent._stream_controller.stream_queue.put(None)

    async def enqueue_initial_mailbox_poll(self) -> None:
        self.mailbox_enqueued = True

    async def enqueue_initial_task_poll(self) -> None:
        self.task_poll_enqueued = True

    async def finalize_round(self) -> None:
        self.finalized = True


@pytest.mark.level0
def test_team_agent_leader_policy() -> None:
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
    ).build()

    assert leader.role == TeamRole.LEADER
    # TeamLeader policy is injected by TeamPolicyRail as a PromptSection
    # before each model call, not stored in deep_config.system_prompt
    # (which stays None).
    policy_rail = next(
        r for r in leader.harness.inner_agent._pending_rails if isinstance(r, TeamPolicyRail)
    )
    role_section = next(
        s for s in policy_rail._static_sections if s.name == TeamSectionName.ROLE
    )
    assert "TeamLeader" in role_section.render("cn")


@pytest.mark.level0
def test_team_plan_leader_mounts_team_plan_mode_rail() -> None:
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
        enable_team_plan=True,
    ).build()

    assert leader.harness.find_rails(TeamPlanModeRail)


@pytest.mark.level0
def test_team_normal_leader_does_not_mount_team_plan_mode_rail() -> None:
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
        enable_team_plan=False,
    ).build()

    assert not leader.harness.find_rails(TeamPlanModeRail)


@pytest.mark.level0
def test_spawn_payload_contains_member_identity() -> None:
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
        transport=TransportSpec(type="pyzmq", params={
            "team_id": "delivery-team",
            "node_id": "leader",
            "direct_addr": "tcp://127.0.0.1:19001",
            "pubsub_publish_addr": "tcp://127.0.0.1:19100",
            "pubsub_subscribe_addr": "tcp://127.0.0.1:19101",
        }),
    ).build()
    ctx = leader.build_member_context(TeamMemberSpec(
        member_name="fe-1",
        display_name="Frontend Expert",
        role_type=TeamRole.TEAMMATE,
        desc="追求交互质量的前端工程师",
    ))

    payload = leader.build_spawn_payload(
        ctx,
        initial_message="Review the design system impact.",
    )

    assert payload["coordination"]["role"] == "teammate"
    assert payload["coordination"]["desc"] == "追求交互质量的前端工程师"
    assert payload["coordination"]["transport"]["node_id"] == "fe-1"
    assert payload["query"] == "Review the design system impact."


@pytest.mark.level0
def test_external_transport_is_separate_from_team_transport(monkeypatch) -> None:
    captured_contexts: list[TeamRuntimeContext] = []

    def capture_context(self, _spec, context):
        captured_contexts.append(context)
        return self

    monkeypatch.setattr(TeamAgent, "configure", capture_context)

    TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
        transport=TransportSpec(
            type="pyzmq",
            params={
                "team_id": "delivery-team",
                "node_id": "leader",
                "direct_addr": "tcp://127.0.0.1:19001",
                "pubsub_publish_addr": "tcp://127.0.0.1:19100",
                "pubsub_subscribe_addr": "tcp://127.0.0.1:19101",
            },
        ),
        external_transport=TransportSpec(
            type="hybrid",
            params={"external_publish_url": "ws://gateway:19000/ws"},
        ),
    ).build()

    context = captured_contexts[0]
    assert context.messager_config is not None
    assert context.messager_config.backend == "pyzmq"
    external_config = context.team_spec.external_messager_config
    assert external_config is not None
    assert external_config.backend == "hybrid"
    assert external_config.external_publish_url == "ws://gateway:19000/ws"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_spawn_config_contains_serializable_team_agent_payload() -> None:
    leader = TeamAgentSpec(
        agents=_dummy_agents(workspace=None),
        team_name="delivery",
        transport=TransportSpec(type="pyzmq", params={
            "team_id": "delivery-team",
            "node_id": "leader",
            "direct_addr": "tcp://127.0.0.1:19001",
            "pubsub_publish_addr": "tcp://127.0.0.1:19100",
            "pubsub_subscribe_addr": "tcp://127.0.0.1:19101",
        }),
    ).build()
    ctx = leader.build_member_context(TeamMemberSpec(
        member_name="be-1",
        display_name="Backend Expert",
        role_type=TeamRole.TEAMMATE,
        desc="严谨的后端架构师",
    ))

    spawn_config = leader.build_spawn_config(ctx)

    assert spawn_config.agent_kind == SpawnAgentKind.TEAM_AGENT
    assert spawn_config.runner_config is not None
    assert "spec" in spawn_config.payload
    assert "context" in spawn_config.payload
    assert spawn_config.payload["context"]["role"] == "teammate"
    assert spawn_config.payload["context"]["messager_config"]["node_id"] == "be-1"

    json.dumps(spawn_config.model_dump(mode="json"))

    teammate = await TeamAgent.from_spawn_payload(spawn_config.payload)

    assert teammate.role == TeamRole.TEAMMATE
    assert teammate.card.name == "be-1"
    assert teammate.runtime_context is not None
    assert teammate.runtime_context.messager_config is not None
    assert teammate.runtime_context.messager_config.node_id == "be-1"


@pytest.mark.level1
def test_runtime_context_roundtrips_with_pydantic_serialization() -> None:
    context = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_name="leader-1",
        desc="pm",
        team_spec=TeamSpec(team_name="demo", display_name="demo"),
    )

    restored = TeamRuntimeContext.model_validate(context.model_dump(mode="json"))

    assert restored.role == TeamRole.LEADER
    assert restored.member_name == "leader-1"
    assert restored.desc == "pm"


@pytest.mark.level0
def test_setup_agent_builds_leader_member_handle() -> None:
    """Leader gets a TeamMember handle eagerly during configure().

    Regression guard: the leader's status / execution transitions are
    silent no-ops unless ``_state.team_member`` is populated. The handle
    must be built for the leader just like for teammates, not deferred
    to a callback path that never fires for a BUSY-registered leader.
    """
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
    ).build()

    assert leader.role == TeamRole.LEADER
    handle = leader._state.team_member
    assert handle is not None
    assert handle.member_name == leader.member_name


@pytest.mark.asyncio
@pytest.mark.level0
async def test_leader_initial_direct_human_agent_route_skips_leader_user_input() -> None:
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
        enable_hitt=True,
        predefined_members=[
            TeamMemberSpec(
                member_name="human-counter-1",
                display_name="Human Counter",
                role_type=TeamRole.HUMAN_AGENT,
                desc="human counter",
            ),
            TeamMemberSpec(
                member_name="ai-counter-1",
                display_name="AI Counter",
                role_type=TeamRole.TEAMMATE,
                desc="ai counter",
            ),
        ],
    ).build()
    coordination = _FakeCoordination(leader)
    leader._coordination = coordination

    dispatched: list[list[HumanAgentMessage]] = []

    async def dispatch(payloads: list[HumanAgentMessage]) -> None:
        dispatched.append(payloads)
        await leader._stream_controller.stream_queue.put(None)

    leader._dispatch_initial_leader_route = AsyncMock(side_effect=dispatch)

    await leader.invoke({"query": "$human-counter-1 @ai-counter-1 我报2"})

    leader._dispatch_initial_leader_route.assert_awaited_once()
    assert dispatched == [
        [
            HumanAgentMessage(
                body="我报2",
                sender="human-counter-1",
                target="ai-counter-1",
            ),
        ],
    ]
    assert coordination.enqueued_inputs == []
    assert coordination.mailbox_enqueued is False
    assert coordination.finalized is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_leader_initial_human_agent_avatar_route_skips_leader_user_input() -> None:
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
        enable_hitt=True,
        predefined_members=[
            TeamMemberSpec(
                member_name="human-counter-1",
                display_name="Human Counter",
                role_type=TeamRole.HUMAN_AGENT,
                desc="human counter",
            ),
        ],
    ).build()
    coordination = _FakeCoordination(leader)
    leader._coordination = coordination

    dispatched: list[list[HumanAgentMessage]] = []

    async def dispatch(payloads: list[HumanAgentMessage]) -> None:
        dispatched.append(payloads)
        await leader._stream_controller.stream_queue.put(None)

    leader._dispatch_initial_leader_route = AsyncMock(side_effect=dispatch)

    await leader.invoke({"query": "$human-counter-1 我报2"})

    leader._dispatch_initial_leader_route.assert_awaited_once()
    assert dispatched == [[HumanAgentMessage(body="我报2", sender="human-counter-1")]]
    assert coordination.enqueued_inputs == []
    assert coordination.mailbox_enqueued is False
    assert coordination.finalized is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_leader_initial_direct_human_agent_stream_route_skips_leader_user_input() -> None:
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
        enable_hitt=True,
        predefined_members=[
            TeamMemberSpec(
                member_name="human-counter-1",
                display_name="Human Counter",
                role_type=TeamRole.HUMAN_AGENT,
                desc="human counter",
            ),
            TeamMemberSpec(
                member_name="ai-counter-1",
                display_name="AI Counter",
                role_type=TeamRole.TEAMMATE,
                desc="ai counter",
            ),
        ],
    ).build()
    coordination = _FakeCoordination(leader)
    leader._coordination = coordination

    dispatched: list[list[HumanAgentMessage]] = []

    async def dispatch(payloads: list[HumanAgentMessage]) -> None:
        dispatched.append(payloads)
        await leader._stream_controller.stream_queue.put(None)

    leader._dispatch_initial_leader_route = AsyncMock(side_effect=dispatch)

    chunks = [chunk async for chunk in leader.stream({"query": "$human-counter-1 @ai-counter-1 我报2"})]

    assert chunks == []
    leader._dispatch_initial_leader_route.assert_awaited_once()
    assert dispatched == [
        [
            HumanAgentMessage(
                body="我报2",
                sender="human-counter-1",
                target="ai-counter-1",
            ),
        ],
    ]
    assert coordination.enqueued_inputs == []
    assert coordination.mailbox_enqueued is False
    assert coordination.finalized is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_leader_initial_human_agent_avatar_stream_route_skips_leader_user_input() -> None:
    leader = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
        enable_hitt=True,
        predefined_members=[
            TeamMemberSpec(
                member_name="human-counter-1",
                display_name="Human Counter",
                role_type=TeamRole.HUMAN_AGENT,
                desc="human counter",
            ),
        ],
    ).build()
    coordination = _FakeCoordination(leader)
    leader._coordination = coordination

    dispatched: list[list[HumanAgentMessage]] = []

    async def dispatch(payloads: list[HumanAgentMessage]) -> None:
        dispatched.append(payloads)
        await leader._stream_controller.stream_queue.put(None)

    leader._dispatch_initial_leader_route = AsyncMock(side_effect=dispatch)

    chunks = [chunk async for chunk in leader.stream({"query": "$human-counter-1 我报2"})]

    assert chunks == []
    leader._dispatch_initial_leader_route.assert_awaited_once()
    assert dispatched == [[HumanAgentMessage(body="我报2", sender="human-counter-1")]]
    assert coordination.enqueued_inputs == []
    assert coordination.mailbox_enqueued is False
    assert coordination.finalized is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_teammate_initial_dollar_input_stays_regular_user_input() -> None:
    leader = TeamAgentSpec(
        agents=_dummy_agents(workspace=None),
        team_name="delivery",
    ).build()
    ctx = leader.build_member_context(
        TeamMemberSpec(
            member_name="ai-counter-1",
            display_name="AI Counter",
            role_type=TeamRole.TEAMMATE,
            desc="ai counter",
        )
    )
    spawn_config = leader.build_spawn_config(ctx)
    teammate = await TeamAgent.from_spawn_payload(spawn_config.payload)
    coordination = _FakeCoordination(teammate)
    teammate._coordination = coordination
    teammate._dispatch_initial_leader_route = AsyncMock()

    inputs = {"query": "$human-counter-1 @ai-counter-1 我报2"}
    await teammate.invoke(inputs)

    teammate._dispatch_initial_leader_route.assert_not_awaited()
    assert coordination.enqueued_inputs == [inputs]
    assert coordination.mailbox_enqueued is True
    assert coordination.finalized is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_inprocess_teammate_initial_dollar_input_stays_regular_user_input() -> None:
    team_spec = TeamSpec(
        team_name="delivery",
        display_name="delivery",
        leader_member_name="leader",
    )
    spec = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
    )
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name="ai-counter-1",
        desc="ai counter",
        team_spec=team_spec,
    )
    teammate = TeamAgent(AgentCard(id="ai-counter-1", name="ai-counter-1", description="test")).configure(spec, ctx)
    coordination = _FakeCoordination(teammate)
    teammate._coordination = coordination
    teammate._dispatch_initial_leader_route = AsyncMock()

    inputs = {"query": "$human-counter-1 @ai-counter-1 我报2"}
    await teammate.invoke(inputs)

    teammate._dispatch_initial_leader_route.assert_not_awaited()
    assert coordination.enqueued_inputs == [inputs]
    assert coordination.mailbox_enqueued is True
    assert coordination.finalized is True


@pytest.mark.asyncio
@pytest.mark.level0
async def test_inprocess_teammate_initial_dollar_stream_stays_regular_user_input() -> None:
    team_spec = TeamSpec(
        team_name="delivery",
        display_name="delivery",
        leader_member_name="leader",
    )
    spec = TeamAgentSpec(
        agents=_dummy_agents(),
        team_name="delivery",
    )
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name="ai-counter-1",
        desc="ai counter",
        team_spec=team_spec,
    )
    teammate = TeamAgent(AgentCard(id="ai-counter-1", name="ai-counter-1", description="test")).configure(spec, ctx)
    coordination = _FakeCoordination(teammate)
    teammate._coordination = coordination
    teammate._dispatch_initial_leader_route = AsyncMock()

    inputs = {"query": "$human-counter-1 @ai-counter-1 我报2"}
    chunks = [chunk async for chunk in teammate.stream(inputs)]

    assert chunks == []
    teammate._dispatch_initial_leader_route.assert_not_awaited()
    assert coordination.enqueued_inputs == [inputs]
    assert coordination.mailbox_enqueued is True
    assert coordination.finalized is True


@pytest.mark.asyncio
@pytest.mark.level1
async def test_setup_agent_builds_teammate_member_handle() -> None:
    """Teammate still gets its TeamMember handle from configure() (no regression)."""
    leader = TeamAgentSpec(
        agents=_dummy_agents(workspace=None),
        team_name="delivery",
        transport=TransportSpec(
            type="pyzmq",
            params={
                "team_id": "delivery-team",
                "node_id": "leader",
                "direct_addr": "tcp://127.0.0.1:19002",
                "pubsub_publish_addr": "tcp://127.0.0.1:19102",
                "pubsub_subscribe_addr": "tcp://127.0.0.1:19103",
            },
        ),
    ).build()
    ctx = leader.build_member_context(
        TeamMemberSpec(
            member_name="be-1",
            display_name="Backend Expert",
            role_type=TeamRole.TEAMMATE,
            desc="严谨的后端架构师",
        )
    )
    spawn_config = leader.build_spawn_config(ctx)

    teammate = await TeamAgent.from_spawn_payload(spawn_config.payload)

    assert teammate.role == TeamRole.TEAMMATE
    handle = teammate._state.team_member
    assert handle is not None
    assert handle.member_name == "be-1"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_failure_wiring_end_to_end() -> None:
    """贯通回归：回调接线 → state latch → StreamController 收尾补帧。

    目标防的回归：configurator 没把 on_team_build_failed 传进 TeamBackend、
    接到了另一个 state 实例、字段名写错——这类断裂会让"建团失败可见化"
    整体失效，而 latch 两端各自的单测（工具回调触发 / 手工置位 latch 后
    补帧）仍然全绿。本用例全程真实对象，不手工置位 latch。
    """
    import asyncio

    from openjiuwen.agent_teams.agent.stream_controller import _detect_task_failed

    team_spec = TeamSpec(
        team_name="delivery",
        display_name="delivery",
        leader_member_name="leader",
    )
    spec = TeamAgentSpec(agents=_dummy_agents(), team_name="delivery")
    ctx = TeamRuntimeContext(
        role=TeamRole.LEADER,
        member_name="leader",
        desc="leader",
        team_spec=team_spec,
    )
    leader = TeamAgent(AgentCard(id="leader", name="leader", description="t")).configure(spec, ctx)

    backend = leader._configurator.team_backend
    # 接线身份：TeamBackend 持有的回调确实来自 TeamAgent（而非丢失/接错）
    assert backend._on_team_build_failed is not None

    # 回调触发（不经工具层，直接驱动 TeamBackend 的通知口）→ latch 置位
    await backend.notify_build_failed("RuntimeError: Failed to create team delivery")
    assert leader._state.team_build_error == "RuntimeError: Failed to create team delivery"

    # controller 读的是同一个 state 实例（接错实例此处即暴露）
    assert leader._stream_controller._state is leader._state

    # 收尾对账补帧：IDLE 时从 stream_queue 拿到 task_failed 帧且 latch 清空
    # （未初始化 DB 的环境下先把 team_member 句柄摘掉——_on_idle_settled 的
    # SHUTDOWN_REQUESTED 检查会读库，与本用例的接线断言无关）
    leader._state.team_member = None
    leader._stream_controller.stream_queue = asyncio.Queue()
    await leader._stream_controller._on_idle_settled()
    chunk = leader._stream_controller.stream_queue.get_nowait()
    assert _detect_task_failed(chunk) is not None
    code, text = _detect_task_failed(chunk)
    assert code == 132001
    assert "Failed to create team delivery" in text
    assert leader._state.team_build_error is None
